#!/usr/bin/env python3
"""
rerouter.py — Phase 4: Live SUMO rerouting demo via TraCI.

Re-runs the SUMO simulation with the mock jam scenario.  At each second
it checks whether the jam threshold is met on any road edge and, if so,
tells every vehicle whose planned route passes through that edge to find
an alternate route (TraCI rerouteTraveltime).

This is the VANET U-turn alert in action:
  RSU detects jam → broadcasts JAM_ALERT → approaching OBUs reroute

Outputs:
    output/reroute_log.json  — timestamped list of every rerouting decision

Usage:
    python3 data_node/rerouter.py            # uses existing routes.rou.xml
    python3 data_node/rerouter.py --gui      # show SUMO-GUI while running
"""

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).resolve().parent.parent
SUMOCFG         = PROJECT_ROOT / "sim" / "sumo" / "vanet.sumocfg"
TRAFFIC_STATE   = PROJECT_ROOT / "corridor" / "traffic_state.json"
RSU_CSV         = PROJECT_ROOT / "corridor" / "rsu_positions.csv"
REROUTE_LOG_JSON= PROJECT_ROOT / "output" / "reroute_log.json"
ALERTS_LOG      = PROJECT_ROOT / "output" / "alerts.log"

# ── Thresholds (same as jam_detector.py) ──────────────────────────────────────
JAM_SPEED_KMH    = 5.0
JAM_MIN_VEHICLES = 3
JAM_MIN_SECONDS  = 30


def resolve_jam_zone() -> tuple:
    """Return (y_min, y_max, x_min, x_max) of jammed segment from live data."""
    if not TRAFFIC_STATE.is_file():
        return None
    try:
        with TRAFFIC_STATE.open(encoding="utf-8") as f:
            ts = json.load(f)
    except Exception:
        return None
    segs = ts.get("segments", [])
    if not segs:
        return None
    jam_seg = max(segs, key=lambda s: s.get("congestion_ratio", 0))

    # Only return a zone if there is a real jam
    level = jam_seg.get("congestion_level", "free")
    if level not in ("slow", "heavy"):
        print(f"  ✓ No real jam in live data — best segment "
              f"{jam_seg['from_rsu']} → {jam_seg['to_rsu']} "
              f"is '{level}' (ratio={jam_seg.get('congestion_ratio', 0):.2f}). "
              f"No jam injection needed.")
        return None
    rsu_map = {}
    if RSU_CSV.is_file():
        with RSU_CSV.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rsu_map[row["id"]] = row
    fr = rsu_map.get(jam_seg["from_rsu"])
    to = rsu_map.get(jam_seg["to_rsu"])
    if not fr or not to:
        return None
    y1, y2 = float(fr["y_m"]), float(to["y_m"])
    x1, x2 = float(fr["x_m"]), float(to["x_m"])
    m = 200.0
    print(f"  Live jam segment: {jam_seg['from_rsu']} → {jam_seg['to_rsu']} "
          f"(ratio={jam_seg['congestion_ratio']:.2f})")
    return (min(y1,y2)-m, max(y1,y2)+m, min(x1,x2)-m, max(x1,x2)+m)

SIM_DURATION_S = 600
STEP_S         = 1.0


# ── Jam tracker ───────────────────────────────────────────────────────────────

class JamTracker:
    """
    Tracks slow-vehicle streaks per road edge.
    Call update(t, vehicles_info) each step.
    Returns set of currently jammed edge IDs.
    """

    def __init__(self):
        # slow_streak[edge][veh_id] = consecutive slow seconds
        self.slow_streak: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # confirmed_jam[edge] = seconds it has been jammed (post-threshold)
        self.confirmed_jam: dict[str, int] = {}
        # jam events log
        self.jam_events: list[dict] = []
        self._alerted: set[str] = set()   # edges already alerted this jam

    def update(self, t: float,
               edge_map: dict[str, list[tuple[str, float]]]) -> set[str]:
        """
        edge_map: {edge_id: [(veh_id, speed_kmh), ...]}
        Returns set of jammed edge IDs (threshold met THIS step).
        """
        jammed: set[str] = set()

        for edge, vehs in edge_map.items():
            slow_this_step = []
            for veh_id, spd in vehs:
                if spd < JAM_SPEED_KMH:
                    self.slow_streak[edge][veh_id] += 1
                    slow_this_step.append((veh_id, self.slow_streak[edge][veh_id]))
                else:
                    self.slow_streak[edge][veh_id] = 0

            qualifying = [(v, s) for v, s in slow_this_step if s >= JAM_MIN_SECONDS]
            if len(qualifying) >= JAM_MIN_VEHICLES:
                jammed.add(edge)
                if edge not in self._alerted:
                    self._alerted.add(edge)
                    self.jam_events.append({
                        "detected_at_s": int(t),
                        "edge":          edge,
                        "vehicles":      [v for v, _ in qualifying],
                        "alert":         f"JAM on {edge} — U-turn recommended",
                    })
            else:
                # Jam cleared
                if edge in self._alerted:
                    self._alerted.discard(edge)

        return jammed


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--gui",  action="store_true",
                    help="Show SUMO-GUI window while running")
    ap.add_argument("--mock", action="store_true",
                    help="Mock mode: always inject jam at BHPV–Nathayyapalem zone")
    ap.add_argument("--port", type=int, default=8814,
                    help="TraCI port (default: 8814, different from supervisor)")
    args = ap.parse_args()

    try:
        import traci
    except ImportError:
        print("ERROR: traci not installed.", file=sys.stderr)
        return 1

    sumo_bin = "sumo-gui" if args.gui else "sumo"
    sumo_cmd = [
        sumo_bin,
        "-c", str(SUMOCFG),
        "--step-length", str(STEP_S),
        "--no-step-log",
        "--collision.action", "warn",
    ]

    print(f"Launching SUMO for rerouting demo: {' '.join(sumo_cmd)}")
    traci.start(sumo_cmd, port=args.port)

    # ── Jam injection setup ───────────────────────────────────────────────────
    if args.mock:
        # Mock: always inject at hardcoded BHPV–Nathayyapalem zone
        JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0
        JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0
        inject_jam = True
        print("  Mock jam injection: BHPV–Nathayyapalem zone (hardcoded)")
    else:
        # Live: only inject if real jam exists in traffic_state.json
        zone = resolve_jam_zone()
        inject_jam = zone is not None
        if zone:
            JAM_Y_MIN, JAM_Y_MAX, JAM_X_MIN, JAM_X_MAX = zone
        else:
            JAM_Y_MIN = JAM_Y_MAX = JAM_X_MIN = JAM_X_MAX = 0.0  # unused

    JAM_SPEED_MS = 1.2
    JAM_START_S  = 60.0
    JAM_END_S    = 350.0
    jam_edges: set[str] = set()

    if inject_jam:
        print(f"  Jam injection: y=[{JAM_Y_MIN:.0f},{JAM_Y_MAX:.0f}] "
              f"capped to {JAM_SPEED_MS} m/s from t={JAM_START_S}s")
    else:
        print("  ✓ Traffic is free — running without jam injection. Vehicles move at full speed.")

    tracker    = JamTracker()
    rerouted:  dict[str, float] = {}   # veh_id → time of last reroute
    reroute_events: list[dict]  = []

    print(f"Running with jam detection + live rerouting ...")
    t_wall = time.time()

    try:
        while traci.simulation.getTime() < SIM_DURATION_S:
            traci.simulationStep()
            t = traci.simulation.getTime()

            vehicles = traci.vehicle.getIDList()

            # Discover jam-zone edges dynamically (only if injecting)
            if inject_jam:
                for veh_id in vehicles:
                    x, y = traci.vehicle.getPosition(veh_id)
                    edge = traci.vehicle.getRoadID(veh_id)
                    if (not edge.startswith(":")
                            and JAM_Y_MIN <= y <= JAM_Y_MAX
                            and JAM_X_MIN <= x <= JAM_X_MAX):
                        jam_edges.add(edge)

            # Apply / remove speed cap (only if injecting)
            if inject_jam and jam_edges:
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

            # Build edge_map for this step
            edge_map: dict[str, list] = defaultdict(list)
            veh_edges: dict[str, str] = {}
            for veh_id in vehicles:
                spd  = traci.vehicle.getSpeed(veh_id) * 3.6   # → km/h
                edge = traci.vehicle.getRoadID(veh_id)
                if edge.startswith(":"):
                    continue
                edge_map[edge].append((veh_id, spd))
                veh_edges[veh_id] = edge

            # Detect jams
            jammed_edges = tracker.update(t, edge_map)

            if jammed_edges:
                # Find vehicles approaching or on a jammed edge and reroute them
                for veh_id in vehicles:
                    # Don't reroute the same vehicle more than once per 60 s
                    if t - rerouted.get(veh_id, -999) < 60:
                        continue

                    # Get vehicle's upcoming route edges
                    try:
                        route_edges = traci.vehicle.getRoute(veh_id)
                    except Exception:
                        continue

                    current_idx = traci.vehicle.getRouteIndex(veh_id)
                    upcoming    = set(route_edges[current_idx:])

                    if upcoming & jammed_edges:
                        # Vehicle's path crosses a jammed edge → reroute
                        try:
                            # Raise travel-time cost on jammed edges so Dijkstra avoids them
                            for je in jammed_edges:
                                traci.edge.adaptTraveltime(je, 9999.0)
                            traci.vehicle.rerouteTraveltime(veh_id)
                            rerouted[veh_id] = t

                            new_route = traci.vehicle.getRoute(veh_id)
                            event = {
                                "t_s":      int(t),
                                "veh_id":   veh_id,
                                "jam_edge": sorted(upcoming & jammed_edges),
                                "new_route_len": len(new_route),
                                "action":   "REROUTED — avoid jam, take alternate path",
                            }
                            reroute_events.append(event)
                            print(f"  t={t:>5.0f}s  REROUTED {veh_id}  "
                                  f"jam_edge={sorted(upcoming & jammed_edges)}")
                        except Exception as exc:
                            print(f"  t={t:>5.0f}s  reroute failed for {veh_id}: {exc}")

            # Print jam alerts as they fire
            for je in tracker.jam_events:
                if je.get("printed"):
                    continue
                print(f"\n  *** JAM DETECTED at t={je['detected_at_s']}s ***")
                print(f"      Edge     : {je['edge']}")
                print(f"      Vehicles : {', '.join(je['vehicles'])}")
                print(f"      Alert    : {je['alert']}\n")
                je["printed"] = True

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        traci.close()
        return 1

    traci.close()
    wall = time.time() - t_wall
    print(f"\nSUMO rerouting demo finished in {wall:.1f}s wall time.")

    # ── Write outputs ─────────────────────────────────────────────────────────
    REROUTE_LOG_JSON.parent.mkdir(parents=True, exist_ok=True)

    output = {
        "jam_events":    [
            {k: v for k, v in je.items() if k != "printed"}
            for je in tracker.jam_events
        ],
        "reroute_events": reroute_events,
        "summary": {
            "total_jams_detected": len(tracker.jam_events),
            "total_vehicles_rerouted": len(rerouted),
        },
    }
    REROUTE_LOG_JSON.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nWriting outputs ...")
    print(f"  Reroute log → {REROUTE_LOG_JSON}")

    # Append summary to alerts.log
    ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ALERTS_LOG.open("a", encoding="utf-8") as f:
        f.write("\n# ── Phase 4 Rerouter Results " + "─" * 38 + "\n")
        for ev in reroute_events:
            f.write(
                f"[T={ev['t_s']:.1f}] NODE=RSU TYPE=REROUTE "
                f"VEH={ev['veh_id']} "
                f"JAM_EDGE={ev['jam_edge']} "
                f"ACTION=\"{ev['action']}\"\n"
            )
    print(f"  Alerts log  → {ALERTS_LOG}  (appended)")

    print()
    print("=" * 60)
    print(f"  Jams detected    : {len(tracker.jam_events)}")
    print(f"  Vehicles rerouted: {len(rerouted)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
