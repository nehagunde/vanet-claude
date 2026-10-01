#!/usr/bin/env python3
"""
rerouter_v2.py — Phase 4: NS-3-alert-driven rerouting.

Reads output/v2/alerts.log produced by the NS-3 802.11p simulation.
Looks for the RELAY_SENT event (RSU=12 fired backhaul relay at BHPV quorum)
and uses its timestamp as the "alert received" time for approaching vehicles.

At that simulation time every vehicle whose remaining route still contains
a jam edge is rerouted via SUMO TraCI.  Vehicles already ON a jam edge are
skipped (they are already stuck).

Why RELAY_SENT rather than JAM_ALERT_RECV:
    The alert was broadcast by RSU=11 (New Gajuwaka) at T=245 s.  No OBU
    logged a JAM_ALERT_RECV because every vehicle had already passed RSU=11's
    radio range by that time.  RELAY_SENT is the earliest moment the V2I
    system actually produced a warning, so it is used as the rerouting trigger.

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

# ── Jam injection parameters (must match traci_supervisor.py) ─────────────────
JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0
JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0
JAM_SPEED_MS          = 1.2     # ~4.3 km/h
JAM_START_S           = 60.0
JAM_END_S             = 350.0
SIM_DURATION_S        = 600.0
STEP_S                = 1.0

# Inflated travel time assigned to jam edges so Dijkstra avoids them
TTIME_PENALTY         = 99999.0


# ── Alert log parser ──────────────────────────────────────────────────────────

def parse_ns3_alert(log_path: Path) -> tuple:
    """
    Scan the NS-3 alerts log for the first RELAY_SENT line.
    Returns (t_alert: float, rsu_id: int, msg: str) or (None, None, None).
    """
    pattern = re.compile(
        r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+RELAY_SENT\s+PEER=[\d.]+\s+MSG="([^"]*)"'
    )
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            m = pattern.search(line)
            if m:
                return float(m.group(1)), int(m.group(2)), m.group(3)
    return None, None, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--gui",  action="store_true", help="Show SUMO-GUI")
    ap.add_argument("--port", type=int, default=8815,
                    help="TraCI port (default 8815; avoids clash with rerouter.py)")
    args = ap.parse_args()

    # ── Parse NS-3 alert ─────────────────────────────────────────────────────
    if not ALERTS_LOG.exists():
        print(f"ERROR: {ALERTS_LOG} not found. Run NS-3 (Phase 3b) first.",
              file=sys.stderr)
        return 1

    t_alert, alert_rsu, alert_msg = parse_ns3_alert(ALERTS_LOG)
    if t_alert is None:
        print("ERROR: No RELAY_SENT event found in NS-3 alerts log.\n"
              f"       Checked: {ALERTS_LOG}", file=sys.stderr)
        return 1

    print(f"NS-3 alert: T={t_alert:.1f}s  RSU={alert_rsu}"
          f"  MSG=\"{alert_msg}\"")
    print(f"  → Rerouting trigger: T={t_alert:.0f}s (RELAY_SENT)")
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
    print(f"Launching SUMO: {' '.join(sumo_cmd)}")
    traci.start(sumo_cmd, port=args.port)

    jam_edges: set[str]         = set()   # discovered dynamically in jam zone
    reroute_events: list[dict]  = []
    rerouted: set[str]          = set()   # veh_ids already handled
    alert_applied               = False

    try:
        while traci.simulation.getTime() < SIM_DURATION_S:
            traci.simulationStep()
            t        = traci.simulation.getTime()
            vehicles = traci.vehicle.getIDList()

            # ── Discover jam edges dynamically ────────────────────────────────
            for veh_id in vehicles:
                x, y = traci.vehicle.getPosition(veh_id)
                edge  = traci.vehicle.getRoadID(veh_id)
                if (not edge.startswith(":")
                        and JAM_Y_MIN <= y <= JAM_Y_MAX
                        and JAM_X_MIN <= x <= JAM_X_MAX):
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
                print(f"[T={t:.0f}s] NS-3 RELAY alert — scanning "
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
    REROUTE_LOG.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "alert": {
            "t_alert_s":    t_alert,
            "rsu_id":       alert_rsu,
            "alert_source": f"RELAY_SENT RSU={alert_rsu} (NS-3 wired backhaul relay)",
            "alert_msg":    alert_msg,
            "note": (
                "Alert time derived from NS-3 RELAY_SENT event at BHPV RSU quorum. "
                "No OBU logged JAM_ALERT_RECV because all vehicles had passed "
                "RSU-01 (New Gajuwaka) radio range before T=245 s. "
                "RELAY_SENT is the earliest point the V2I system issued a warning."
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

    REROUTE_LOG.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Reroute log → {REROUTE_LOG}")
    print()
    print("=" * 60)
    print(f"  Vehicles rerouted      : {output['summary']['total_vehicles_rerouted']}")
    print(f"  Avoided jam            : {output['summary']['vehicles_avoided_jam']}")
    print(f"  No alternate found     : {output['summary']['vehicles_no_alternate']}")
    print(f"  Alert fired at         : T={t_alert:.0f}s (NS-3 RELAY_SENT)")
    print(f"  Jam edges discovered   : {sorted(jam_edges)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
