#!/usr/bin/env python3
"""
rerouter_v2.py — Phase 4: NS-3-alert-driven rerouting.

Reads output/v2/alerts.log produced by the NS-3 802.11p simulation.
Uses the FIRST JAM_DETECTED_FROM event at RSU=12 (BHPV) as the rerouting
trigger.  This is the earliest NS-3-observable signal that the BHPV jam
exists — approximately T=91 s — which is before veh_00 (approaching from
Old Gajuwaka) enters the jam zone at T≈134 s.

At that simulation time every vehicle whose remaining route still contains
a jam edge is rerouted via SUMO TraCI.  Vehicles already ON a jam edge are
skipped (they are already stuck).

Why FIRST_JAM_DETECTED rather than RELAY_SENT (T=245):
    At T=245 all approaching vehicles (veh_00, veh_01) have already passed
    through BHPV — detection latency exceeded approach time.  The first
    JAM_DETECTED_FROM at RSU=12 (T≈91 s) is the earliest actionable NS-3
    signal and catches veh_00 approximately 43 s before it enters the jam.
    The human-readable alert message is taken from the later RELAY_SENT line.

Outputs:
    output/v2/reroute_log.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUMOCFG      = PROJECT_ROOT / "sim" / "sumo" / "vanet.sumocfg"
ALERTS_LOG   = PROJECT_ROOT / "output" / "v2" / "alerts.log"
REROUTE_LOG  = PROJECT_ROOT / "output" / "v2" / "reroute_log.json"
SPEEDLOG     = PROJECT_ROOT / "sim" / "bridge" / "speed_log.json"

# ── Jam injection parameters (must match traci_supervisor.py) ─────────────────
JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0
JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0
JAM_SPEED_MS          = 1.2     # ~4.3 km/h
JAM_SPEED_KMH         = JAM_SPEED_MS * 3.6   # 4.32 km/h — threshold for "slow"
JAM_START_S           = 60.0
JAM_END_S             = 350.0
SIM_DURATION_S        = 600.0
STEP_S                = 1.0

# Inflated travel time assigned to jam edges so Dijkstra avoids them
TTIME_PENALTY         = 99999.0


# ── Alert log parser ──────────────────────────────────────────────────────────

def parse_ns3_alert(log_path: Path) -> tuple:
    """
    Scan the NS-3 alerts log for two events:
      1. First JAM_DETECTED_FROM at RSU=12 → trigger time (earliest signal)
      2. First RELAY_SENT at RSU=12       → human-readable alert message

    Returns (t_trigger: float, rsu_id: int, alert_msg: str, t_relay: float)
    or (None, None, None, None) if no JAM_DETECTED_FROM found.
    """
    pat_jam = re.compile(
        r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+JAM_DETECTED_FROM=\d+'
    )
    pat_relay = re.compile(
        r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+RELAY_SENT\s+PEER=[\d.]+\s+MSG="([^"]*)"'
    )
    t_trigger = None
    rsu_id    = None
    t_relay   = None
    alert_msg = "Take alternate route: jam detected ahead"   # fallback

    with log_path.open(encoding="utf-8") as f:
        for line in f:
            if t_trigger is None:
                m = pat_jam.search(line)
                if m and int(m.group(2)) == 12:   # RSU=12 is BHPV
                    t_trigger = float(m.group(1))
                    rsu_id    = int(m.group(2))
            if t_relay is None:
                m = pat_relay.search(line)
                if m and int(m.group(2)) == 12:
                    t_relay   = float(m.group(1))
                    alert_msg = m.group(3)
            if t_trigger is not None and t_relay is not None:
                break

    return t_trigger, rsu_id, alert_msg, t_relay


def preseed_jam_edges(speedlog_path: Path) -> set:
    """
    Load the edges where vehicles were actually slow (<JAM_SPEED_KMH) inside
    the jam zone from the previous SUMO run's speed log.

    This gives the complete set of NH-16 jam segments upfront, so the rerouter
    does not have to wait for vehicles to enter those edges during THIS run.
    Bypass roads (residential / service) are excluded because no vehicle was
    slow on them in the previous run.
    """
    if not speedlog_path.exists():
        print(f"  [preseed] speed log not found: {speedlog_path} — skipping")
        return set()
    with speedlog_path.open(encoding="utf-8") as f:
        speed_log = json.load(f)
    edges: set = set()
    for vehicles in speed_log.values():
        for info in vehicles.values():
            edge  = info.get("edge", "")
            speed = info.get("speed_kmh", 100.0)
            y     = info.get("y", 0.0)
            x     = info.get("x", 0.0)
            if (not edge.startswith(":")
                    and speed < JAM_SPEED_KMH
                    and JAM_Y_MIN <= y <= JAM_Y_MAX
                    and JAM_X_MIN <= x <= JAM_X_MAX):
                edges.add(edge)
    return edges


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--gui",  action="store_true", help="Show SUMO-GUI")
    ap.add_argument("--port", type=int, default=8815,
                    help="TraCI port (default 8815; avoids clash with rerouter.py)")
    ap.add_argument("--seed", type=int, default=0,
                    help="SUMO random seed (default: 0)")
    ap.add_argument("--tripinfo", type=str, default="",
                    help="Path for SUMO tripinfo XML output (optional)")
    ap.add_argument("--alerts-log", type=str, default="",
                    help="Override path to NS-3 alerts log (default: output/v2/alerts.log)")
    ap.add_argument("--reroute-log", type=str, default="",
                    help="Override path for reroute log JSON output")
    args = ap.parse_args()

    alerts_log_path = Path(args.alerts_log) if args.alerts_log else ALERTS_LOG
    reroute_log_path = Path(args.reroute_log) if args.reroute_log else REROUTE_LOG

    # ── Parse NS-3 alert ─────────────────────────────────────────────────────
    if not alerts_log_path.exists():
        print(f"ERROR: {ALERTS_LOG} not found. Run NS-3 (Phase 3b) first.",
              file=sys.stderr)
        return 1

    t_alert, alert_rsu, alert_msg, t_relay = parse_ns3_alert(alerts_log_path)
    if t_alert is None:
        print("ERROR: No JAM_DETECTED_FROM event found for RSU=12 in NS-3 alerts log.\n"
              f"       Checked: {alerts_log_path}", file=sys.stderr)
        return 1

    print(f"NS-3 first detection: T={t_alert:.1f}s  RSU={alert_rsu}"
          f"  (JAM_DETECTED_FROM)")
    if t_relay is not None:
        print(f"NS-3 relay alert:     T={t_relay:.1f}s  MSG=\"{alert_msg}\"")
    print(f"  → Rerouting trigger: T={t_alert:.0f}s (first JAM_DETECTED_FROM at RSU=12)")
    print()

    # ── Pre-seed jam edges from previous SUMO speed log ──────────────────────
    jam_edges: set[str] = preseed_jam_edges(SPEEDLOG)
    print(f"Pre-seeded {len(jam_edges)} jam edge(s) from speed log:")
    if jam_edges:
        print(f"  {sorted(jam_edges)}")
    print()

    # ── Start SUMO ────────────────────────────────────────────────────────────
    try:
        import traci
    except ImportError:
        print("ERROR: traci not installed (pip install traci).", file=sys.stderr)
        return 1

    sumo_bin = "sumo-gui" if args.gui else "sumo"
    sumo_cmd = [
        sumo_bin,
        "-c", str(SUMOCFG),
        "--step-length", str(STEP_S),
        "--no-step-log",
        "--collision.action", "warn",
    ]
    if args.seed:
        sumo_cmd += ["--seed", str(args.seed)]
    if args.tripinfo:
        sumo_cmd += ["--tripinfo-output", args.tripinfo]
    print(f"Launching SUMO: {' '.join(sumo_cmd)}")
    traci.start(sumo_cmd, port=args.port)

    # jam_edges pre-seeded above from speedlog; more edges added dynamically
    reroute_events: list[dict]  = []
    rerouted: set[str]          = set()   # veh_ids already handled
    alert_applied               = False

    try:
        while traci.simulation.getTime() < SIM_DURATION_S:
            traci.simulationStep()
            t        = traci.simulation.getTime()
            vehicles = traci.vehicle.getIDList()

            # ── Discover additional jam edges dynamically (speed-gated) ──────
            # Only add an edge when the vehicle is actually slow on it —
            # this prevents bypass/residential roads from entering the set.
            for veh_id in vehicles:
                x, y   = traci.vehicle.getPosition(veh_id)
                edge   = traci.vehicle.getRoadID(veh_id)
                spd_ms = traci.vehicle.getSpeed(veh_id)
                if (not edge.startswith(":")
                        and JAM_Y_MIN <= y <= JAM_Y_MAX
                        and JAM_X_MIN <= x <= JAM_X_MAX
                        and spd_ms * 3.6 < JAM_SPEED_KMH):
                    jam_edges.add(edge)

            # ── Apply / remove speed cap ──────────────────────────────────────
            if jam_edges:
                if JAM_START_S <= t <= JAM_END_S:
                    for je in jam_edges:
                        try:
                            traci.edge.setMaxSpeed(je, JAM_SPEED_MS)
                        except Exception:
                            pass
                elif t > JAM_END_S:
                    for je in jam_edges:
                        try:
                            traci.edge.setMaxSpeed(je, 13.89)
                        except Exception:
                            pass

            # ── Alert-time rerouting (fires exactly once) ─────────────────────
            if not alert_applied and t >= t_alert and jam_edges:
                alert_applied = True
                print(f"[T={t:.0f}s] NS-3 first-detection alert — scanning "
                      f"{len(vehicles)} vehicle(s), {len(jam_edges)} jam edge(s)")
                print(f"  Jam edges: {sorted(jam_edges)}")
                print()

                # Inflate travel time on jam edges once, before all reroutes
                for je in jam_edges:
                    try:
                        traci.edge.adaptTraveltime(je, TTIME_PENALTY)
                    except Exception:
                        pass

                for veh_id in sorted(vehicles):
                    current_edge = traci.vehicle.getRoadID(veh_id)
                    if current_edge.startswith(":"):
                        current_edge = ""   # in junction — treat as unknown

                    # Skip vehicles already inside the jam
                    if current_edge in jam_edges:
                        print(f"  {veh_id}: SKIP — already on jam edge "
                              f"({current_edge})")
                        continue

                    try:
                        route     = list(traci.vehicle.getRoute(veh_id))
                        route_idx = traci.vehicle.getRouteIndex(veh_id)
                        upcoming  = set(route[route_idx:])
                    except Exception as exc:
                        print(f"  {veh_id}: SKIP — cannot read route ({exc})")
                        continue

                    if not (upcoming & jam_edges):
                        print(f"  {veh_id}: SKIP — no jam edge in remaining "
                              f"route (already past jam zone)")
                        continue

                    # Vehicle is approaching — reroute it
                    old_route = route[:]
                    try:
                        traci.vehicle.rerouteTraveltime(veh_id)
                        new_route   = list(traci.vehicle.getRoute(veh_id))
                        avoided_jam = not bool(set(new_route) & jam_edges)
                        rerouted.add(veh_id)

                        event = {
                            "t_s":          int(t),
                            "veh_id":       veh_id,
                            "current_edge": current_edge,
                            "old_route":    old_route,
                            "new_route":    new_route,
                            "avoided_jam":  avoided_jam,
                            "old_route_len": len(old_route),
                            "new_route_len": len(new_route),
                        }
                        reroute_events.append(event)

                        status = "AVOIDED JAM" if avoided_jam else "NO ALTERNATE FOUND"
                        print(f"  {veh_id}: REROUTED  "
                              f"old={len(old_route)} edges  "
                              f"new={len(new_route)} edges  [{status}]")
                    except Exception as exc:
                        print(f"  {veh_id}: reroute failed — {exc}")

                print()

    except Exception as exc:
        print(f"ERROR during simulation: {exc}", file=sys.stderr)
        traci.close()
        return 1

    traci.close()
    print(f"Simulation complete. Rerouted {len(rerouted)} vehicle(s).")
    print()

    # ── Write output/v2/reroute_log.json ─────────────────────────────────────
    reroute_log_path.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "alert": {
            "t_trigger_s":  t_alert,
            "t_relay_s":    t_relay,
            "rsu_id":       alert_rsu,
            "alert_source": f"FIRST JAM_DETECTED_FROM RSU={alert_rsu} (NS-3 802.11p)",
            "alert_msg":    alert_msg,
            "note": (
                "Trigger time = first JAM_DETECTED_FROM at RSU=12 (BHPV). "
                "This is the earliest NS-3 signal that the jam exists, occurring "
                "~43 s before veh_00 enters the jam zone. "
                "RELAY_SENT (full quorum + backhaul relay) fires later at "
                f"T={t_relay:.0f}s but by then all approaching vehicles have passed BHPV."
            ),
        },
        "jam_edges":     sorted(jam_edges),
        "reroute_events": reroute_events,
        "summary": {
            "total_vehicles_rerouted":   len(rerouted),
            "vehicles_avoided_jam":      sum(1 for e in reroute_events
                                             if e["avoided_jam"]),
            "vehicles_no_alternate":     sum(1 for e in reroute_events
                                             if not e["avoided_jam"]),
        },
    }

    reroute_log_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Reroute log → {reroute_log_path}")
    print()
    print("=" * 60)
    print(f"  Vehicles rerouted      : {output['summary']['total_vehicles_rerouted']}")
    print(f"  Avoided jam            : {output['summary']['vehicles_avoided_jam']}")
    print(f"  No alternate found     : {output['summary']['vehicles_no_alternate']}")
    print(f"  Alert fired at         : T={t_alert:.0f}s (first JAM_DETECTED_FROM RSU=12)")
    print(f"  Jam edges discovered   : {sorted(jam_edges)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
