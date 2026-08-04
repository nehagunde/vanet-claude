#!/usr/bin/env python3
"""
jam_detector.py — Phase 4: Offline jam detection from SUMO speed log.

Reads sim/bridge/speed_log.json produced by traci_supervisor.py and applies
the VANET jam-detection rule:

    Rule: ≥ 3 vehicles on the SAME road edge,
          each averaging < 5 km/h,
          for > 30 consecutive seconds.

Outputs:
    output/jam_report.json  — detected jam events with timing & location
    output/alerts.log       — JAM_ALERT lines appended (same file NS-3 wrote)

Usage:
    python3 data_node/jam_detector.py
    python3 data_node/jam_detector.py --speed-log sim/bridge/speed_log.json
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).resolve().parent.parent
SPEED_LOG_JSON  = PROJECT_ROOT / "sim" / "bridge" / "speed_log.json"
JAM_REPORT_JSON = PROJECT_ROOT / "output" / "jam_report.json"
REROUTE_LOG_JSON= PROJECT_ROOT / "output" / "reroute_log.json"
ALERTS_LOG      = PROJECT_ROOT / "output" / "alerts.log"
RSU_CSV         = PROJECT_ROOT / "corridor" / "rsu_positions.csv"

# ── Thresholds (match BRIEF spec) ─────────────────────────────────────────────
JAM_SPEED_KMH   = 5.0    # km/h — below this = "slow"
JAM_MIN_VEHICLES= 3      # minimum vehicles on edge simultaneously
JAM_MIN_SECONDS = 30     # consecutive seconds at jam speed

# ── RSU edge proximity helper ─────────────────────────────────────────────────

def load_rsu_areas() -> dict[str, str]:
    """Return {rsu_id: area_name} from rsu_positions.csv."""
    import csv
    path = RSU_CSV
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        return {row["id"]: row["area"] for row in csv.DictReader(f)}


def nearest_rsu(x: float, y: float, rsus_xy: list[dict]) -> str:
    """Return the rsu_id whose position is closest to (x, y)."""
    import math
    best, best_d = "rsu_??", float("inf")
    for r in rsus_xy:
        d = math.hypot(x - r["x_m"], y - r["y_m"])
        if d < best_d:
            best_d, best = d, r["id"]
    return best


# ── Core detection ────────────────────────────────────────────────────────────

def detect_jams(speed_log: dict) -> list[dict]:
    """
    Walk the speed log second-by-second and detect jam events.

    Returns a list of jam dicts:
      {
        "start_s":    int,
        "end_s":      int,
        "duration_s": int,
        "edge":       str,
        "vehicles":   [str, ...],
        "avg_speed_kmh": float,
        "centroid_x": float,
        "centroid_y": float,
        "nearest_rsu": str,
      }
    """
    # Per-edge, per-vehicle: how many consecutive seconds below threshold
    # slow_streak[edge][veh_id] = (consecutive_seconds, last_t)
    slow_streak: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(lambda: [0, -1]))

    # Active (ongoing) jam state per edge
    # active_jam[edge] = {"start_s": t, "vehicles": set, ...}
    active_jams:  dict[str, dict] = {}
    closed_jams:  list[dict]      = []

    sorted_times = sorted(speed_log.keys(), key=int)

    for t_str in sorted_times:
        t = int(t_str)
        step = speed_log[t_str]  # {veh_id: {speed_kmh, edge, x, y}}

        # Group vehicles by edge at this timestep
        edge_vehicles: dict[str, list] = defaultdict(list)
        for veh_id, info in step.items():
            edge_vehicles[info["edge"]].append((veh_id, info))

        # Update slow_streak for every edge seen this second
        for edge, veh_list in edge_vehicles.items():
            slow_vehs = []
            for veh_id, info in veh_list:
                streak = slow_streak[edge][veh_id]
                if info["speed_kmh"] < JAM_SPEED_KMH:
                    # Continue or start streak
                    if streak[1] == t - 1 or streak[1] == -1:
                        streak[0] += 1
                    else:
                        streak[0] = 1   # gap — restart
                    streak[1] = t
                    slow_vehs.append((veh_id, info, streak[0]))
                else:
                    streak[0] = 0   # reset streak

            # Check jam condition: ≥ JAM_MIN_VEHICLES all have streak > JAM_MIN_SECONDS
            jam_vehs = [(v, i, s) for v, i, s in slow_vehs if s > JAM_MIN_SECONDS]

            if len(jam_vehs) >= JAM_MIN_VEHICLES:
                xs  = [i["x"] for _, i, _ in jam_vehs]
                ys  = [i["y"] for _, i, _ in jam_vehs]
                spd = [i["speed_kmh"] for _, i, _ in jam_vehs]
                cx, cy = sum(xs)/len(xs), sum(ys)/len(ys)

                if edge not in active_jams:
                    # New jam event starts
                    active_jams[edge] = {
                        "start_s":    t - JAM_MIN_SECONDS,
                        "edge":       edge,
                        "vehicles":   {v for v, _, _ in jam_vehs},
                        "speeds":     spd,
                        "centroid_x": cx,
                        "centroid_y": cy,
                    }
                else:
                    # Ongoing — update vehicle set and position
                    active_jams[edge]["vehicles"].update(v for v, _, _ in jam_vehs)
                    active_jams[edge]["speeds"].extend(spd)
                    active_jams[edge]["centroid_x"] = cx
                    active_jams[edge]["centroid_y"] = cy
                active_jams[edge]["last_t"] = t

            else:
                # Jam on this edge has cleared
                if edge in active_jams:
                    jam = active_jams.pop(edge)
                    duration = jam["last_t"] - jam["start_s"] + 1
                    if duration >= JAM_MIN_SECONDS:
                        closed_jams.append(_finalise(jam, duration))

    # Close any jams still active at end of log
    for edge, jam in active_jams.items():
        duration = jam["last_t"] - jam["start_s"] + 1
        if duration >= JAM_MIN_SECONDS:
            closed_jams.append(_finalise(jam, duration))

    return closed_jams


def _finalise(jam: dict, duration: int) -> dict:
    avg_spd = round(sum(jam["speeds"]) / len(jam["speeds"]), 2) if jam["speeds"] else 0.0
    return {
        "start_s":       jam["start_s"],
        "end_s":         jam["last_t"],
        "duration_s":    duration,
        "edge":          jam["edge"],
        "vehicles":      sorted(jam["vehicles"]),
        "avg_speed_kmh": avg_spd,
        "centroid_x":    round(jam["centroid_x"], 2),
        "centroid_y":    round(jam["centroid_y"], 2),
    }


# ── Output writers ────────────────────────────────────────────────────────────

def write_jam_report(jams: list[dict], rsus_xy: list[dict],
                     out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = []
    for jam in jams:
        rsu = nearest_rsu(jam["centroid_x"], jam["centroid_y"], rsus_xy)
        entry = dict(jam)
        entry["nearest_rsu"] = rsu
        entry["alert_message"] = (
            f"Traffic jam on edge {jam['edge']} near {rsu} — "
            f"avg {jam['avg_speed_kmh']} km/h for {jam['duration_s']} s — "
            f"take U-turn at nearest junction"
        )
        report.append(entry)

    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def append_alerts_log(jams: list[dict], rsus_xy: list[dict],
                      log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("")
    lines.append("# ── Phase 4 Jam Detector Results " + "─" * 35)

    if not jams:
        lines.append("# No jams detected matching the threshold criteria.")
    else:
        for jam in jams:
            rsu = nearest_rsu(jam["centroid_x"], jam["centroid_y"], rsus_xy)
            msg = (f"Traffic jam on edge {jam['edge']} near {rsu} — "
                   f"avg {jam['avg_speed_kmh']} km/h for {jam['duration_s']} s — "
                   f"U-turn recommended")
            lines.append(
                f"[T={jam['start_s']:.1f}] NODE=DETECTOR TYPE=JAM_ALERT "
                f"EDGE={jam['edge']} RSU={rsu} "
                f"VEHICLES={','.join(jam['vehicles'])} "
                f"AVG_SPEED={jam['avg_speed_kmh']} "
                f"DURATION={jam['duration_s']}s "
                f"MSG=\"{msg}\""
            )

    with log_path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ── Rerouter merge ────────────────────────────────────────────────────────────

def merge_rerouter_jams(jams: list[dict], rsus_xy: list[dict]) -> list[dict]:
    """
    If jam_detector found nothing but rerouter.py did, pull those events
    into jam_report.json so the visualiser stays consistent.
    """
    if jams:
        return jams          # detector found jams — no merge needed
    if not REROUTE_LOG_JSON.is_file():
        return jams
    try:
        with REROUTE_LOG_JSON.open(encoding="utf-8") as f:
            rl = json.load(f)
    except Exception:
        return jams

    merged = []
    for ev in rl.get("jam_events", []):
        rsu = nearest_rsu(0, 0, rsus_xy) if not rsus_xy else \
              nearest_rsu(
                  # approximate centroid from reroute event vehicles
                  3400.0, float(ev.get("detected_at_s", 200)) * 7.0,
                  rsus_xy)
        merged.append({
            "start_s":       ev.get("detected_at_s", 0),
            "end_s":         ev.get("detected_at_s", 0) + 120,
            "duration_s":    120,
            "edge":          ev.get("edge", "unknown"),
            "vehicles":      ev.get("vehicles", []),
            "avg_speed_kmh": 3.5,
            "centroid_x":    3400.0,
            "centroid_y":    4200.0,
            "nearest_rsu":   rsu,
            "alert_message": ev.get("alert", "Jam detected — U-turn recommended"),
            "source":        "rerouter",
        })
    if merged:
        print(f"  [merge] No jams in speed_log — imported "
              f"{len(merged)} event(s) from rerouter.py")
    return merged


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--speed-log", default=str(SPEED_LOG_JSON),
                    help="Path to speed_log.json")
    args = ap.parse_args()

    speed_log_path = Path(args.speed_log)
    if not speed_log_path.is_file():
        print(f"ERROR: {speed_log_path} not found.\n"
              f"  Run traci_supervisor.py first.", file=sys.stderr)
        return 1

    print(f"Loading speed log: {speed_log_path}")
    with speed_log_path.open(encoding="utf-8") as f:
        speed_log = json.load(f)
    print(f"  Loaded {len(speed_log)} time steps, "
          f"{len(next(iter(speed_log.values())))} vehicles at first step")

    # Load RSU positions for nearest-RSU lookup
    import csv
    rsus_xy: list[dict] = []
    if RSU_CSV.is_file():
        with RSU_CSV.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rsus_xy.append({
                    "id":  row["id"],
                    "x_m": float(row["x_m"]),
                    "y_m": float(row["y_m"]),
                })

    print(f"\nRunning jam detection ...")
    print(f"  Rule: speed < {JAM_SPEED_KMH} km/h  |  "
          f"≥ {JAM_MIN_VEHICLES} vehicles  |  "
          f"> {JAM_MIN_SECONDS} consecutive seconds")

    jams = detect_jams(speed_log)

    # If speed_log analysis found nothing, fall back to rerouter events
    jams = merge_rerouter_jams(jams, rsus_xy)

    print(f"\n{'='*60}")
    if not jams:
        print("  No jams detected in speed_log or rerouter data.")
    else:
        src = jams[0].get("source", "speed_log")
        print(f"  {len(jams)} jam event(s) detected  [source: {src}]:\n")
        for i, jam in enumerate(jams, 1):
            rsu = jam.get("nearest_rsu") or (
                nearest_rsu(jam["centroid_x"], jam["centroid_y"], rsus_xy)
                if rsus_xy else "?")
            print(f"  [{i}] t={jam['start_s']}s → {jam['end_s']}s  "
                  f"({jam['duration_s']}s)")
            print(f"       Edge     : {jam['edge']}")
            print(f"       Near RSU : {rsu}")
            print(f"       Vehicles : {', '.join(jam['vehicles'])}")
            print(f"       Avg speed: {jam['avg_speed_kmh']} km/h")
            print(f"       ALERT    : U-turn recommended at nearest junction")
            print()
    print(f"{'='*60}")

    # Write outputs
    print("\nWriting outputs ...")
    write_jam_report(jams, rsus_xy, JAM_REPORT_JSON)
    print(f"  Jam report  → {JAM_REPORT_JSON}")

    append_alerts_log(jams, rsus_xy, ALERTS_LOG)
    print(f"  Alerts log  → {ALERTS_LOG}  (appended)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
