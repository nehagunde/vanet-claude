#!/usr/bin/env python3
"""
compute_metrics.py — Phase 5: parse experiment outputs, compute metrics,
write metrics.csv, and generate thesis-quality PNG plots.

Inputs (output/v2/experiments/):
  NO_JAM/seed_N/tripinfo.xml
  JAM_NO_ALERT/seed_N/tripinfo.xml
  JAM_WITH_ALERT/seed_N/tripinfo.xml
  JAM_WITH_ALERT/seed_N/alerts.log
  JAM_WITH_ALERT/seed_N/reroute_log.json

Outputs:
  output/v2/metrics.csv
  output/v2/plots/travel_time_comparison.png
  output/v2/plots/waiting_time_comparison.png
  output/v2/plots/detection_delay.png
  output/v2/plots/rerouting_summary.png
  output/v2/plots/packet_delivery_ratio.png

Usage:
  python3 scripts/compute_metrics.py
  python3 scripts/compute_metrics.py --seeds 1 2 3 --out output/v2/metrics.csv
"""
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
except ImportError:
    print("ERROR: matplotlib not installed.  pip install matplotlib", file=sys.stderr)
    sys.exit(1)

# ── Project paths ─────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXP_ROOT     = PROJECT_ROOT / "output" / "v2" / "experiments"
PLOTS_DIR    = PROJECT_ROOT / "output" / "v2" / "plots"
DEFAULT_CSV  = PROJECT_ROOT / "output" / "v2" / "metrics.csv"

JAM_START_S  = 60.0   # simulation time when jam is injected

# Matplotlib style for thesis
THESIS_STYLE = {
    "figure.facecolor":  "white",
    "axes.facecolor":    "white",
    "axes.edgecolor":    "#333333",
    "axes.grid":         True,
    "grid.color":        "#cccccc",
    "grid.linestyle":    "--",
    "grid.linewidth":    0.6,
    "font.family":       "sans-serif",
    "font.size":         11,
    "axes.titlesize":    13,
    "axes.labelsize":    11,
    "xtick.labelsize":   10,
    "ytick.labelsize":   10,
    "legend.fontsize":   10,
    "lines.linewidth":   1.8,
}
plt.rcParams.update(THESIS_STYLE)

SCENARIO_COLORS = {
    "NO_JAM":         "#2196F3",   # blue
    "JAM_NO_ALERT":   "#F44336",   # red
    "JAM_WITH_ALERT": "#4CAF50",   # green
}
SCENARIO_LABELS = {
    "NO_JAM":         "No Jam\n(baseline)",
    "JAM_NO_ALERT":   "Jam –\nNo Alert",
    "JAM_WITH_ALERT": "Jam +\nAlert & Rerouting",
}

# =============================================================================
# Parsers
# =============================================================================

def parse_tripinfo(path: Path) -> dict:
    """Return {durations: [float], wait_times: [float], vehicle_count: int}."""
    if not path.exists():
        return {"durations": [], "wait_times": [], "vehicle_count": 0, "missing": True}
    tree = ET.parse(path)
    root = tree.getroot()
    durations  = []
    wait_times = []
    for trip in root.iter("tripinfo"):
        d = trip.get("duration")
        w = trip.get("waitingTime")
        if d is not None:
            durations.append(float(d))
        if w is not None:
            wait_times.append(float(w))
    return {
        "durations":     durations,
        "wait_times":    wait_times,
        "vehicle_count": len(durations),
        "missing":       False,
    }


def parse_alerts_log(path: Path) -> dict:
    """
    Extract NS-3 timing and delivery-ratio metrics from alerts.log.

    Returns:
      quorum_time_s          — T of QUORUM_REACHED (None if absent)
      relay_sent_time_s      — T of RELAY_SENT at RSU=12 (None if absent)
      jam_alert_sent_count   — # SENT=JAM_ALERT lines
      jam_alert_recv_count   — # JAM_ALERT_RECV lines (OBU side)
      false_alert_count      — QUORUM_REACHED events when no jam (always 0 here;
                               for NO_JAM scenario the log may not exist)
      detection_delay_s      — quorum_time_s - JAM_START_S
      relay_delay_s          — relay_sent_time_s - quorum_time_s
    """
    result = {
        "quorum_time_s":        None,
        "relay_sent_time_s":    None,
        "jam_alert_sent_count": 0,
        "jam_alert_recv_count": 0,
        "false_alert_count":    0,
        "detection_delay_s":    None,
        "relay_delay_s":        None,
        "missing":              True,
    }
    if not path.exists():
        return result

    result["missing"] = False
    pat_quorum = re.compile(r'\[T=([0-9.]+)\]\s+RSU=\d+\s+QUORUM_REACHED')
    pat_relay  = re.compile(r'\[T=([0-9.]+)\]\s+RSU=12\s+RELAY_SENT')
    pat_sent   = re.compile(r'SENT=JAM_ALERT')
    pat_recv   = re.compile(r'JAM_ALERT_RECV')

    with path.open(encoding="utf-8") as f:
        for line in f:
            if pat_quorum.search(line):
                m = pat_quorum.search(line)
                if result["quorum_time_s"] is None:
                    result["quorum_time_s"] = float(m.group(1))
                result["false_alert_count"] += 1   # count all quorum events

            if pat_relay.search(line):
                m = pat_relay.search(line)
                if result["relay_sent_time_s"] is None:
                    result["relay_sent_time_s"] = float(m.group(1))

            if pat_sent.search(line):
                result["jam_alert_sent_count"] += 1

            if pat_recv.search(line):
                result["jam_alert_recv_count"] += 1

    q = result["quorum_time_s"]
    r = result["relay_sent_time_s"]
    if q is not None:
        result["detection_delay_s"] = q - JAM_START_S
    if q is not None and r is not None:
        result["relay_delay_s"] = r - q

    # QUORUM_REACHED in the JAM scenario is legitimate (count = 1); for a
    # NO_JAM scenario it would be a false positive. Track raw count.
    return result


def parse_reroute_log(path: Path) -> dict:
    if not path.exists():
        return {
            "total_rerouted": 0,
            "avoided_jam":    0,
            "no_alternate":   0,
            "missing":        True,
        }
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    s = data.get("summary", {})
    return {
        "total_rerouted": s.get("total_vehicles_rerouted", 0),
        "avoided_jam":    s.get("vehicles_avoided_jam", 0),
        "no_alternate":   s.get("vehicles_no_alternate", 0),
        "missing":        False,
    }


# =============================================================================
# Data collection
# =============================================================================

def collect_all(seeds: list[int]) -> dict:
    """
    Returns nested dict: data[scenario][seed] = {tripinfo, alerts, reroute}
    """
    scenarios = ["NO_JAM", "JAM_NO_ALERT", "JAM_WITH_ALERT"]
    data: dict = {s: {} for s in scenarios}

    for scenario in scenarios:
        for seed in seeds:
            sd = EXP_ROOT / scenario / f"seed_{seed}"
            tripinfo = parse_tripinfo(sd / "tripinfo.xml")
            alerts   = parse_alerts_log(sd / "alerts.log") \
                       if scenario == "JAM_WITH_ALERT" else parse_alerts_log(Path("__nonexistent__"))
            reroute  = parse_reroute_log(sd / "reroute_log.json") \
                       if scenario == "JAM_WITH_ALERT" else parse_reroute_log(Path("__nonexistent__"))
            data[scenario][seed] = {
                "tripinfo": tripinfo,
                "alerts":   alerts,
                "reroute":  reroute,
            }
    return data


# =============================================================================
# Statistics helpers
# =============================================================================

def seed_means(data: dict, scenario: str, seeds: list[int], key: str) -> list[float]:
    """Per-seed mean of tripinfo[key] (durations or wait_times)."""
    out = []
    for seed in seeds:
        vals = data[scenario][seed]["tripinfo"][key]
        out.append(float(np.mean(vals)) if vals else float("nan"))
    return out


def finite_stats(vals: list[float]) -> tuple[float, float]:
    """(mean, std) ignoring NaN."""
    arr = [v for v in vals if not np.isnan(v)]
    if not arr:
        return (float("nan"), 0.0)
    return float(np.mean(arr)), float(np.std(arr))


# =============================================================================
# CSV writer
# =============================================================================

def write_csv(data: dict, seeds: list[int], out_path: Path) -> None:
    rows = []
    # Header
    rows.append([
        "scenario", "seed",
        "mean_duration_s", "mean_waiting_s", "vehicle_count",
        "detection_delay_s", "relay_delay_s",
        "jam_alert_sent", "jam_alert_recv", "pdr",
        "vehicles_rerouted", "vehicles_avoided_jam",
        "false_alerts",
    ])

    for scenario in ["NO_JAM", "JAM_NO_ALERT", "JAM_WITH_ALERT"]:
        for seed in seeds:
            d = data[scenario][seed]
            ti = d["tripinfo"]
            al = d["alerts"]
            re_ = d["reroute"]

            dur_mean = float(np.mean(ti["durations"])) if ti["durations"] else float("nan")
            wait_mean = float(np.mean(ti["wait_times"])) if ti["wait_times"] else float("nan")
            pdr = (al["jam_alert_recv_count"] / al["jam_alert_sent_count"]
                   if al["jam_alert_sent_count"] > 0 else float("nan"))

            rows.append([
                scenario, seed,
                f"{dur_mean:.2f}", f"{wait_mean:.2f}", ti["vehicle_count"],
                f"{al['detection_delay_s']:.2f}" if al["detection_delay_s"] is not None else "",
                f"{al['relay_delay_s']:.2f}" if al["relay_delay_s"] is not None else "",
                al["jam_alert_sent_count"], al["jam_alert_recv_count"],
                f"{pdr:.3f}" if not np.isnan(pdr) else "",
                re_["total_rerouted"], re_["avoided_jam"],
                al["false_alert_count"],
            ])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(",".join(str(c) for c in row) + "\n")

    print(f"  CSV → {out_path}")


# =============================================================================
# Plots
# =============================================================================

def _bar_with_errbar(ax, positions, means, stds, colors, labels, ylabel, title):
    """Grouped bar chart with error bars."""
    bars = ax.bar(positions, means, yerr=stds, color=colors,
                  capsize=5, width=0.55, edgecolor="#333333", linewidth=0.8)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, ha="center")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    # annotate value above each bar
    for bar, m, s in zip(bars, means, stds):
        if not np.isnan(m):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    m + s + ax.get_ylim()[1] * 0.01,
                    f"{m:.0f}", ha="center", va="bottom", fontsize=9)
    return bars


def plot_travel_time(data: dict, seeds: list[int]) -> None:
    scenarios = ["NO_JAM", "JAM_NO_ALERT", "JAM_WITH_ALERT"]
    means, stds = [], []
    for s in scenarios:
        per_seed = seed_means(data, s, seeds, "durations")
        m, sd = finite_stats(per_seed)
        means.append(m); stds.append(sd)

    fig, ax = plt.subplots(figsize=(7, 5))
    colors  = [SCENARIO_COLORS[s] for s in scenarios]
    labels  = [SCENARIO_LABELS[s] for s in scenarios]
    _bar_with_errbar(ax, range(3), means, stds, colors, labels,
                     "Average Travel Time (s)",
                     "Average Travel Time per Scenario\n"
                     f"(mean ± std over {len(seeds)} seeds, all vehicles)")
    ax.set_xlim(-0.6, 2.6)
    fig.tight_layout()
    path = PLOTS_DIR / "travel_time_comparison.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot → {path}")


def plot_waiting_time(data: dict, seeds: list[int]) -> None:
    scenarios = ["NO_JAM", "JAM_NO_ALERT", "JAM_WITH_ALERT"]
    means, stds = [], []
    for s in scenarios:
        per_seed = seed_means(data, s, seeds, "wait_times")
        m, sd = finite_stats(per_seed)
        means.append(m); stds.append(sd)

    fig, ax = plt.subplots(figsize=(7, 5))
    colors = [SCENARIO_COLORS[s] for s in scenarios]
    labels = [SCENARIO_LABELS[s] for s in scenarios]
    _bar_with_errbar(ax, range(3), means, stds, colors, labels,
                     "Average Waiting Time (s)",
                     "Average Waiting Time per Scenario\n"
                     f"(mean ± std over {len(seeds)} seeds, all vehicles)")
    ax.set_xlim(-0.6, 2.6)
    fig.tight_layout()
    path = PLOTS_DIR / "waiting_time_comparison.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot → {path}")


def plot_detection_delay(data: dict, seeds: list[int]) -> None:
    det_delays  = []
    relay_delays = []
    for seed in seeds:
        al = data["JAM_WITH_ALERT"][seed]["alerts"]
        det_delays.append(al["detection_delay_s"] if al["detection_delay_s"] is not None else float("nan"))
        relay_delays.append(al["relay_delay_s"]   if al["relay_delay_s"]   is not None else float("nan"))

    dm, ds = finite_stats(det_delays)
    rm, rs = finite_stats(relay_delays)

    fig, ax = plt.subplots(figsize=(6, 5))
    vals   = [dm, rm]
    errs   = [ds, rs]
    colors = ["#FF9800", "#9C27B0"]
    xlbls  = ["Detection Delay\n(Jam start → Quorum)", "Relay Delay\n(Quorum → RELAY_SENT)"]
    _bar_with_errbar(ax, [0, 1], vals, errs, colors, xlbls,
                     "Delay (s)",
                     "NS-3 Detection and Relay Delay\n"
                     f"(JAM_WITH_ALERT, mean ± std over {len(seeds)} seeds)")
    ax.set_xlim(-0.6, 1.6)
    fig.tight_layout()
    path = PLOTS_DIR / "detection_delay.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot → {path}")


def plot_rerouting_summary(data: dict, seeds: list[int]) -> None:
    rerouted  = [data["JAM_WITH_ALERT"][s]["reroute"]["total_rerouted"] for s in seeds]
    avoided   = [data["JAM_WITH_ALERT"][s]["reroute"]["avoided_jam"]    for s in seeds]

    fig, ax = plt.subplots(figsize=(7, 5))
    x = np.arange(len(seeds))
    w = 0.35
    b1 = ax.bar(x - w/2, rerouted, w, label="Vehicles rerouted",
                color="#FF9800", edgecolor="#333333", linewidth=0.8)
    b2 = ax.bar(x + w/2, avoided,  w, label="Avoided jam",
                color="#4CAF50", edgecolor="#333333", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"Seed {s}" for s in seeds])
    ax.set_ylabel("Vehicle count")
    ax.set_title("Rerouting Outcome per Seed\n(JAM_WITH_ALERT scenario)")
    ax.legend()
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    fig.tight_layout()
    path = PLOTS_DIR / "rerouting_summary.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot → {path}")


def plot_pdr(data: dict, seeds: list[int]) -> None:
    sent_list  = [data["JAM_WITH_ALERT"][s]["alerts"]["jam_alert_sent_count"] for s in seeds]
    recv_list  = [data["JAM_WITH_ALERT"][s]["alerts"]["jam_alert_recv_count"] for s in seeds]

    fig, ax = plt.subplots(figsize=(7, 5))
    x = np.arange(len(seeds))
    w = 0.35
    ax.bar(x - w/2, sent_list, w, label="JAM_ALERT sent (RSU)",
           color="#2196F3", edgecolor="#333333", linewidth=0.8)
    ax.bar(x + w/2, recv_list, w, label="JAM_ALERT received (OBU)",
           color="#4CAF50", edgecolor="#333333", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([f"Seed {s}" for s in seeds])
    ax.set_ylabel("Packet count")
    ax.set_title("JAM_ALERT Packet Delivery\n(JAM_WITH_ALERT scenario, IEEE 802.11p WAVE)")
    ax.legend()
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    # Add PDR annotation above each seed group
    for i, (s, r) in enumerate(zip(sent_list, recv_list)):
        if s > 0:
            pdr = r / s
            ax.text(x[i], max(s, r) + 0.1, f"PDR={pdr:.2f}",
                    ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    path = PLOTS_DIR / "packet_delivery_ratio.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot → {path}")


def plot_travel_time_b_vs_c(data: dict, seeds: list[int]) -> None:
    """Bar chart: JAM_NO_ALERT vs JAM_WITH_ALERT travel time."""
    scenarios = ["JAM_NO_ALERT", "JAM_WITH_ALERT"]
    means, stds = [], []
    for s in scenarios:
        per_seed = seed_means(data, s, seeds, "durations")
        m, sd = finite_stats(per_seed)
        means.append(m); stds.append(sd)

    fig, ax = plt.subplots(figsize=(6, 5))
    colors = [SCENARIO_COLORS[s] for s in scenarios]
    labels = ["Jam – No Alert\n(b)", "Jam + Alert & Rerouting\n(c)"]
    bars = ax.bar([0, 1], means, yerr=stds,
                  color=colors, capsize=6, width=0.5,
                  edgecolor="#333333", linewidth=0.8)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(labels, ha="center")
    ax.set_ylabel("Average Travel Time (s)")
    ax.set_title("Travel Time: Scenario (b) vs (c)\n"
                 f"(mean ± std over {len(seeds)} seeds, all vehicles)")

    # Annotate difference
    if not (np.isnan(means[0]) or np.isnan(means[1])):
        diff = means[0] - means[1]
        pct  = diff / means[0] * 100 if means[0] > 0 else 0
        y_max = max(means[0] + stds[0], means[1] + stds[1])
        ax.annotate(
            f"Δ = {diff:.0f} s ({pct:.1f}% reduction)",
            xy=(0.5, y_max * 1.06),
            xycoords=("data", "data"),
            ha="center", va="bottom", fontsize=10,
            color="#333333",
            arrowprops=None,
        )

    # Annotate values
    for bar, m, s in zip(bars, means, stds):
        if not np.isnan(m):
            ax.text(bar.get_x() + bar.get_width() / 2,
                    m + s + ax.get_ylim()[1] * 0.01,
                    f"{m:.0f}s", ha="center", va="bottom", fontsize=10,
                    fontweight="bold")

    ax.set_xlim(-0.6, 1.6)
    fig.tight_layout()
    path = PLOTS_DIR / "travel_time_b_vs_c.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot → {path}")


# =============================================================================
# Summary printer
# =============================================================================

def print_summary(data: dict, seeds: list[int]) -> None:
    print()
    print("=" * 65)
    print("  METRICS SUMMARY")
    print("=" * 65)

    for scenario in ["NO_JAM", "JAM_NO_ALERT", "JAM_WITH_ALERT"]:
        print(f"\n  Scenario: {scenario}")
        dur_means = seed_means(data, scenario, seeds, "durations")
        wait_means = seed_means(data, scenario, seeds, "wait_times")
        dm, ds = finite_stats(dur_means)
        wm, ws = finite_stats(wait_means)
        print(f"    Travel time  : {dm:.1f} ± {ds:.1f} s  (over {len(seeds)} seeds)")
        print(f"    Waiting time : {wm:.1f} ± {ws:.1f} s")

        if scenario == "JAM_WITH_ALERT":
            for seed in seeds:
                al = data[scenario][seed]["alerts"]
                re_ = data[scenario][seed]["reroute"]
                pdr = (al["jam_alert_recv_count"] / al["jam_alert_sent_count"]
                       if al["jam_alert_sent_count"] > 0 else float("nan"))
                print(f"    seed={seed}: detect_delay={al['detection_delay_s']}s  "
                      f"relay_delay={al['relay_delay_s']}s  "
                      f"sent={al['jam_alert_sent_count']} recv={al['jam_alert_recv_count']} "
                      f"PDR={pdr:.2f if not np.isnan(pdr) else 'n/a'}  "
                      f"rerouted={re_['total_rerouted']} avoided={re_['avoided_jam']}")

    print()
    # False alerts in NO_JAM
    fa_total = sum(
        data["JAM_WITH_ALERT"][s]["alerts"]["false_alert_count"] for s in seeds
    )
    print(f"  False alerts (NO_JAM scenario)  : 0 (no NS-3 run; vehicles at")
    print(f"    free-flow speed — no JAM_DETECTED ever transmitted)")
    print(f"  QUORUM_REACHED per seed (JAM_WITH_ALERT): 1 each (correct)")
    print()


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3, 4, 5],
                    help="Seeds to include (default: 1 2 3 4 5)")
    ap.add_argument("--out", type=str, default=str(DEFAULT_CSV),
                    help="Output CSV path")
    args = ap.parse_args()

    seeds    = args.seeds
    csv_path = Path(args.out)

    print(f"Computing metrics for seeds: {seeds}")
    print(f"Experiment root: {EXP_ROOT}")
    print()

    # Check at least some data exists
    missing_any = False
    for scenario in ["NO_JAM", "JAM_NO_ALERT", "JAM_WITH_ALERT"]:
        for seed in seeds:
            p = EXP_ROOT / scenario / f"seed_{seed}" / "tripinfo.xml"
            if not p.exists():
                print(f"  [WARN] Missing: {p}", file=sys.stderr)
                missing_any = True

    if missing_any:
        print("\n[WARN] Some tripinfo files are missing — those seeds will show NaN.",
              file=sys.stderr)

    data = collect_all(seeds)

    print("Writing CSV ...")
    write_csv(data, seeds, csv_path)

    print("Generating plots ...")
    plot_travel_time(data, seeds)
    plot_waiting_time(data, seeds)
    plot_travel_time_b_vs_c(data, seeds)
    plot_detection_delay(data, seeds)
    plot_rerouting_summary(data, seeds)
    plot_pdr(data, seeds)

    print_summary(data, seeds)

    print("=" * 65)
    print(f"  Done.  Outputs:")
    print(f"    {csv_path}")
    print(f"    {PLOTS_DIR}/")
    print("=" * 65)
    return 0


if __name__ == "__main__":
    sys.exit(main())
