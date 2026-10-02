#!/usr/bin/env python3
"""
rerouter_v2.py — Phase 4: NS-3-alert-driven per-vehicle rerouting (Fix 5).

Reads output/v2/alerts.log produced by the NS-3 802.11p simulation.
Uses each OBU's individual JAM_ALERT_RECV time (from alerts.log) as its
personal rerouting trigger — each vehicle is rerouted at the exact simulation
time it received the JAM_ALERT from an RSU over 802.11p.

Vehicles that never received a JAM_ALERT are not rerouted.
Vehicles already on a jam edge when their alert arrives are skipped.

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
NODE_MAP     = PROJECT_ROOT / "sim" / "bridge" / "node_map.json"

# ── Jam injection parameters (must match traci_supervisor.py) ─────────────────
JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0
JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0
JAM_SPEED_MS          = 1.2     # ~4.3 km/h
JAM_SPEED_KMH         = JAM_SPEED_MS * 3.6   # 4.32 km/h — threshold for "slow"
JAM_START_S           = 60.0
JAM_END_S             = 350.0
SIM_DURATION_S        = 1200.0  # extended so all vehicles finish (Fix 6)
STEP_S                = 1.0

# Inflated travel time assigned to jam edges so Dijkstra avoids them
TTIME_PENALTY         = 99999.0


# ── Alert log parsers ─────────────────────────────────────────────────────────

def parse_recv_times(log_path: Path) -> dict:
    """
    Fix 5: Parse OBU JAM_ALERT_RECV lines from alerts.log.
    Returns {ns3_node_id: (recv_time, from_rsu)} for the FIRST receipt per OBU.

    Format matched:
        [T=t] OBU=N JAM_ALERT_RECV FROM_RSU=M MSG="..."
    """
    pat = re.compile(
        r'\[T=([0-9.]+)\]\s+OBU=(\d+)\s+JAM_ALERT_RECV\s+FROM_RSU=(\d+)'
    )
    result: dict = {}
    with log_path.open(encoding="utf-8") as f:
        for line in f:
            m = pat.search(line)
            if m:
                t      = float(m.group(1))
                obu_id = int(m.group(2))
                rsu_id = int(m.group(3))
                if obu_id not in result:   # first receipt only
                    result[obu_id] = (t, rsu_id)
    return result


def parse_ns3_alert(log_path: Path) -> tuple:
    """
    Scan the NS-3 alerts log for:
      1. First QUORUM_REACHED at any RSU → quorum time
      2. First RELAY_SENT at RSU=12      → human-readable alert message

    Returns (t_quorum: float, rsu_id: int, alert_msg: str, t_relay: float)
    or (None, None, fallback_msg, None) if no quorum found.
    Used for the summary / reroute_log metadata only (Fix 5 reroutes per-vehicle).
    """
    pat_quorum = re.compile(
        r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+QUORUM_REACHED'
    )
    pat_relay = re.compile(
        r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+RELAY_SENT\s+PEER=[\d.]+\s+MSG="([^"]*)"'
    )
    t_quorum  = None
    rsu_id    = None
    t_relay   = None
    alert_msg = "Take alternate route: jam detected ahead"

    with log_path.open(encoding="utf-8") as f:
        for line in f:
            if t_quorum is None:
                m = pat_quorum.search(line)
                if m:
                    t_quorum = float(m.group(1))
                    rsu_id   = int(m.group(2))
            if t_relay is None:
                m = pat_relay.search(line)
                if m and int(m.group(2)) == 12:
                    t_relay   = float(m.group(1))
                    alert_msg = m.group(3)
            if t_quorum is not None and t_relay is not None:
                break

    return t_quorum, rsu_id, alert_msg, t_relay


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

    # ── Parse NS-3 per-OBU alert receipt times (Fix 5) ───────────────────────
    if not alerts_log_path.exists():
        print(f"ERROR: {alerts_log_path} not found. Run NS-3 first.",
              file=sys.stderr)
        return 1

    obu_recv: dict = parse_recv_times(alerts_log_path)
    t_quorum, alert_rsu, alert_msg, t_relay = parse_ns3_alert(alerts_log_path)

    if not obu_recv:
        print("WARNING: No JAM_ALERT_RECV events found in NS-3 alerts log.\n"
              f"  Checked: {alerts_log_path}\n"
              "  No vehicles will be rerouted — check the NS-3 run.")
    else:
        for nid, (t_recv, from_rsu) in sorted(obu_recv.items()):
            print(f"  OBU node {nid}: JAM_ALERT_RECV at T={t_recv:.1f}s"
                  f"  FROM_RSU={from_rsu}")

    if t_quorum is not None:
        print(f"\nNS-3 quorum:          T={t_quorum:.1f}s  RSU={alert_rsu}")
    if t_relay is not None:
        print(f"NS-3 relay alert:     T={t_relay:.1f}s  MSG=\"{alert_msg}\"")
    print()

    # ── Load NS-3 node → SUMO vehicle mapping ────────────────────────────────
    node_map_path = NODE_MAP
    if node_map_path.exists():
        with node_map_path.open(encoding="utf-8") as f:
            raw_map = json.load(f)
        # raw_map is {"ns3_id_str": "veh_id"}
        node_to_veh: dict = {int(k): v for k, v in raw_map.items()}
    else:
        # Fallback: assume veh_0N → node N
        print(f"  [WARN] {node_map_path} not found — using default veh_0N mapping")
        node_to_veh = {i: f"veh_{i:02d}" for i in range(10)}

    # Build veh_id → alert receipt time map
    veh_recv_times: dict = {}
    for nid, (t_recv, from_rsu) in obu_recv.items():
        veh_id = node_to_veh.get(nid)
        if veh_id:
            veh_recv_times[veh_id] = {"t_recv": t_recv, "from_rsu": from_rsu}
    print(f"Vehicles with JAM_ALERT: {sorted(veh_recv_times.keys())}")
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
    reroute_events: list[dict] = []
    rerouted: set[str]         = set()   # veh_ids already rerouted
    jam_edges_inflated         = False   # travel-time penalty applied once

    try:
        while traci.simulation.getTime() < SIM_DURATION_S:
            traci.simulationStep()
            t        = traci.simulation.getTime()
            vehicles = traci.vehicle.getIDList()

            # ── Discover additional jam edges dynamically (speed-gated) ──────
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

            # ── Fix 5: per-vehicle rerouting at its own JAM_ALERT_RECV time ──
            # Inflate travel-time penalty once on first rerouting trigger.
            if veh_recv_times and jam_edges and not jam_edges_inflated:
                earliest = min(v["t_recv"] for v in veh_recv_times.values())
                if t >= earliest:
                    jam_edges_inflated = True
                    for je in jam_edges:
                        try:
                            traci.edge.adaptTraveltime(je, TTIME_PENALTY)
                        except Exception:
                            pass
                    print(f"[T={t:.0f}s] Jam edge travel-time penalty applied: "
                          f"{sorted(jam_edges)}")

            for veh_id, recv_info in veh_recv_times.items():
                if veh_id in rerouted:
                    continue
                if t < recv_info["t_recv"]:
                    continue
                if veh_id not in vehicles:
                    # vehicle not yet in sim or already arrived
                    continue

                current_edge = traci.vehicle.getRoadID(veh_id)
                if current_edge.startswith(":"):
                    current_edge = ""

                if current_edge in jam_edges:
                    rerouted.add(veh_id)   # mark so we don't retry
                    print(f"  [T={t:.0f}s] {veh_id}: SKIP — already on jam edge "
                          f"({current_edge})")
                    reroute_events.append({
                        "t_alert_s":    recv_info["t_recv"],
                        "t_reroute_s":  int(t),
                        "from_rsu":     recv_info["from_rsu"],
                        "veh_id":       veh_id,
                        "current_edge": current_edge,
                        "old_route":    [],
                        "new_route":    [],
                        "avoided_jam":  False,
                        "skipped":      "already_on_jam_edge",
                    })
                    continue

                try:
                    route     = list(traci.vehicle.getRoute(veh_id))
                    route_idx = traci.vehicle.getRouteIndex(veh_id)
                    upcoming  = set(route[route_idx:])
                except Exception as exc:
                    print(f"  [T={t:.0f}s] {veh_id}: SKIP — cannot read route ({exc})")
                    rerouted.add(veh_id)
                    continue

                if not (upcoming & jam_edges):
                    rerouted.add(veh_id)
                    print(f"  [T={t:.0f}s] {veh_id}: SKIP — no jam edge in "
                          f"remaining route (already past jam zone)")
                    continue

                old_route = route[:]
                try:
                    traci.vehicle.rerouteTraveltime(veh_id)
                    new_route   = list(traci.vehicle.getRoute(veh_id))
                    avoided_jam = not bool(set(new_route) & jam_edges)
                    rerouted.add(veh_id)

                    event = {
                        "t_alert_s":     recv_info["t_recv"],
                        "t_reroute_s":   int(t),
                        "from_rsu":      recv_info["from_rsu"],
                        "veh_id":        veh_id,
                        "current_edge":  current_edge,
                        "old_route":     old_route,
                        "new_route":     new_route,
                        "avoided_jam":   avoided_jam,
                        "old_route_len": len(old_route),
                        "new_route_len": len(new_route),
                    }
                    reroute_events.append(event)

                    status = "AVOIDED JAM" if avoided_jam else "NO ALTERNATE FOUND"
                    print(f"  [T={t:.0f}s] {veh_id}: REROUTED"
                          f"  alert_t={recv_info['t_recv']:.1f}s"
                          f"  old={len(old_route)} edges"
                          f"  new={len(new_route)} edges  [{status}]")
                except Exception as exc:
                    print(f"  [T={t:.0f}s] {veh_id}: reroute failed — {exc}")

    except Exception as exc:
        print(f"ERROR during simulation: {exc}", file=sys.stderr)
        traci.close()
        return 1

    traci.close()
    print(f"Simulation complete. Rerouted {len(rerouted)} vehicle(s).")
    print()

    # ── Write output/v2/reroute_log.json ─────────────────────────────────────
    reroute_log_path.parent.mkdir(parents=True, exist_ok=True)
    actual_rerouted = [e for e in reroute_events
                       if e.get("new_route") and e["new_route"] != e.get("old_route")]
    output = {
        "alert": {
            "t_quorum_s":   t_quorum,
            "t_relay_s":    t_relay,
            "rsu_id":       alert_rsu,
            "alert_msg":    alert_msg,
            "per_vehicle_recv": {
                vid: {"t_recv": info["t_recv"], "from_rsu": info["from_rsu"]}
                for vid, info in veh_recv_times.items()
            },
        },
        "jam_edges":      sorted(jam_edges),
        "reroute_events": reroute_events,
        "summary": {
            "total_vehicles_rerouted":   len(rerouted),
            "vehicles_avoided_jam":      sum(1 for e in reroute_events
                                             if e.get("avoided_jam")),
            "vehicles_no_alternate":     sum(1 for e in reroute_events
                                             if not e.get("avoided_jam")
                                             and not e.get("skipped")),
        },
    }

    reroute_log_path.parent.mkdir(parents=True, exist_ok=True)
    reroute_log_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Reroute log → {reroute_log_path}")
    print()
    print("=" * 60)
    print(f"  Vehicles with alert    : {sorted(veh_recv_times.keys())}")
    print(f"  Vehicles rerouted      : {output['summary']['total_vehicles_rerouted']}")
    print(f"  Avoided jam            : {output['summary']['vehicles_avoided_jam']}")
    print(f"  No alternate found     : {output['summary']['vehicles_no_alternate']}")
    print(f"  Jam edges discovered   : {sorted(jam_edges)}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
