#!/usr/bin/env python3
"""
traci_supervisor.py — Phase 3: SUMO ↔ NS-3 mobility bridge.

Launches SUMO via TraCI, steps through the 600-second simulation, and
produces the files NS-3 needs to run the WAVE network simulation:

  sim/bridge/mobility.ns2     — OBU waypoints in ns2 format (NS-3 reads this)
  sim/bridge/rsu_static.json  — RSU SUMO-XY positions (NS-3 static nodes)
  sim/bridge/speed_log.json   — per-vehicle speed + edge each second
                                (Phase 4 jam detector reads this)

Workflow:
  1. Run this script  →  SUMO simulation runs, outputs are written.
  2. Run NS-3 scenario →  reads mobility.ns2 + rsu_static.json, simulates
                          802.11p, writes output/alerts.log.

Usage:
  python3 sim/bridge/traci_supervisor.py            # uses routes.rou.xml (live)
  python3 sim/bridge/traci_supervisor.py --mock     # regenerates routes with mock jam
  python3 sim/bridge/traci_supervisor.py --gui      # shows SUMO-GUI while running
"""

import argparse
import csv
import json
import math
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).resolve().parent.parent.parent
SUMOCFG         = PROJECT_ROOT / "sim" / "sumo" / "vanet.sumocfg"
RSU_CSV         = PROJECT_ROOT / "corridor" / "rsu_positions.csv"
TRAFFIC_STATE   = PROJECT_ROOT / "corridor" / "traffic_state.json"
MOBILITY_NS2    = PROJECT_ROOT / "sim" / "bridge" / "mobility.ns2"
RSU_STATIC_JSON = PROJECT_ROOT / "sim" / "bridge" / "rsu_static.json"
SPEED_LOG_JSON  = PROJECT_ROOT / "sim" / "bridge" / "speed_log.json"

SIM_DURATION_S = 600   # must match vanet.sumocfg <end> value
STEP_S         = 1.0   # simulation step length (seconds)


# ── Jam zone resolver ─────────────────────────────────────────────────────────

def resolve_jam_zone() -> tuple[float, float, float, float] | None:
    """
    Read corridor/traffic_state.json and return the SUMO bounding box
    (y_min, y_max, x_min, x_max) of the worst-congested segment
    (always picks the segment with highest ratio from live data).
    Returns None if traffic_state.json is missing or has no segments.
    """
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

    # Only inject if there is a real jam — "free" traffic must not be forced slow
    level = jam_seg.get("congestion_level", "free")
    if level not in ("slow", "heavy"):
        print(f"  ✓ No real jam in live data — best segment "
              f"{jam_seg['from_rsu']} → {jam_seg['to_rsu']} "
              f"is '{level}' (ratio={jam_seg.get('congestion_ratio', 0):.2f}). "
              f"No jam injection needed.")
        return None

    # Look up RSU SUMO coordinates for the segment endpoints
    if not RSU_CSV.is_file():
        return None
    rsu_map: dict[str, dict] = {}
    with RSU_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rsu_map[row["id"]] = row

    from_rsu = rsu_map.get(jam_seg["from_rsu"])
    to_rsu   = rsu_map.get(jam_seg["to_rsu"])
    if not from_rsu or not to_rsu:
        return None

    y1 = float(from_rsu["y_m"]);  y2 = float(to_rsu["y_m"])
    x1 = float(from_rsu["x_m"]);  x2 = float(to_rsu["x_m"])
    margin = 200.0  # extend zone slightly beyond RSU positions

    print(f"  Live jam segment: {jam_seg['from_rsu']} → {jam_seg['to_rsu']}  "
          f"(ratio={jam_seg['congestion_ratio']:.2f}, "
          f"level={jam_seg['congestion_level']})")
    return (min(y1, y2) - margin, max(y1, y2) + margin,
            min(x1, x2) - margin, max(x1, x2) + margin)


# ── RSU loader ────────────────────────────────────────────────────────────────

def load_rsus() -> list[dict]:
    """Load RSU positions from CSV and return as list of dicts."""
    if not RSU_CSV.is_file():
        print(f"ERROR: {RSU_CSV} not found. Run place_rsus.py first.",
              file=sys.stderr)
        sys.exit(1)
    with RSU_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── ns2 mobility writer ───────────────────────────────────────────────────────

def write_ns2_mobility(fcd: dict[str, list], out_path: Path,
                       node_id_map: dict[str, int]) -> None:
    """
    Write an ns2-format mobility trace that NS-3's Ns2MobilityHelper reads.

    Format per vehicle per second:
        $node_(<id>) set X_ <x>          # initial position
        $node_(<id>) set Y_ <y>
        $node_(<id>) set Z_ 0.0
        $ns_ at <t> "$node_(<id>) setdest <x> <y> <speed>"

    Node IDs: OBU vehicles are nodes 0-9; RSUs are nodes 10-16.
    Speed passed to setdest = distance moved since last step (m/s).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ns2 mobility trace generated by traci_supervisor.py",
        "# OBU nodes: 0–9  |  RSU nodes: 10–16",
        "",
    ]

    for veh_id, records in sorted(fcd.items(), key=lambda kv: node_id_map[kv[0]]):
        nid = node_id_map[veh_id]
        if not records:
            continue

        # Initial position (first recorded step)
        t0, x0, y0, spd0, _ = records[0]
        lines += [
            f"$node_({nid}) set X_ {x0:.2f}",
            f"$node_({nid}) set Y_ {y0:.2f}",
            f"$node_({nid}) set Z_ 0.00",
        ]

        # Movement waypoints
        prev_x, prev_y = x0, y0
        for t, x, y, spd, _ in records:
            dist = math.hypot(x - prev_x, y - prev_y)
            # Speed = how fast to travel to the new position (m/s)
            travel_speed = dist / STEP_S if dist > 0.01 else 0.0
            lines.append(
                f'$ns_ at {t:.1f} "$node_({nid}) setdest {x:.2f} {y:.2f} '
                f'{travel_speed:.2f}"'
            )
            prev_x, prev_y = x, y
        lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── RSU static position writer ────────────────────────────────────────────────

def write_rsu_static(rsus: list[dict], net, out_path: Path) -> None:
    """
    Write RSU positions as NS-3 XY coordinates (SUMO internal metres).
    NS-3 assigns these as ConstantPositionMobilityModel for nodes 10–16.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = []
    for i, rsu in enumerate(rsus):
        x = float(rsu["x_m"])
        y = float(rsu["y_m"])
        result.append({
            "ns3_node_id": 10 + i,   # OBUs are 0-9, RSUs start at 10
            "rsu_id":      rsu["id"],
            "area":        rsu["area"],
            "x_m":         round(x, 2),
            "y_m":         round(y, 2),
            "lat":         float(rsu["lat"]),
            "lon":         float(rsu["lon"]),
        })
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Speed log writer ──────────────────────────────────────────────────────────

def write_speed_log(fcd: dict[str, list], out_path: Path,
                    node_id_map: dict[str, int]) -> None:
    """
    Write per-vehicle speed + road-segment each second.
    Phase 4 jam_detector.py reads this to apply the
    '< 5 km/h for > 30 s on same segment' detection rule.

    Format: { "600": { "veh_00": {"speed_kmh": 4.2, "edge": "...", "x": ..., "y": ...} } }
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    log: dict[str, dict] = {}
    for veh_id, records in fcd.items():
        for t, x, y, spd_ms, edge in records:
            t_str = str(int(t))
            if t_str not in log:
                log[t_str] = {}
            log[t_str][veh_id] = {
                "speed_kmh": round(spd_ms * 3.6, 2),
                "edge":      edge,
                "x":         round(x, 2),
                "y":         round(y, 2),
            }
    out_path.write_text(
        json.dumps(log, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--gui",  action="store_true",
                    help="Show SUMO-GUI window while running (slower)")
    ap.add_argument("--mock", action="store_true",
                    help="Regenerate routes.rou.xml with mock jam before running")
    ap.add_argument("--inject-jam", action="store_true",
                    help="Force speed cap on BHPV–Nathayyapalem edges (auto-set with --mock)")
    ap.add_argument("--port", type=int, default=8813,
                    help="TraCI port (default: 8813)")
    args = ap.parse_args()

    # ── Optional: regenerate routes with mock jam ─────────────────────────────
    if args.mock:
        print("Regenerating routes with mock jam scenario ...")
        ret = subprocess.run(
            [sys.executable,
             str(PROJECT_ROOT / "data_node" / "generate_routes.py"),
             "--mock"],
            cwd=str(PROJECT_ROOT),
        )
        if ret.returncode != 0:
            print("ERROR: generate_routes.py failed.", file=sys.stderr)
            return 1

    # ── Load TraCI ────────────────────────────────────────────────────────────
    try:
        import traci
    except ImportError:
        print("ERROR: traci not installed.\n"
              "  pip install traci   OR  add SUMO_HOME/tools to PYTHONPATH",
              file=sys.stderr)
        return 1

    # ── Load RSU positions ────────────────────────────────────────────────────
    rsus = load_rsus()
    print(f"Loaded {len(rsus)} RSUs from {RSU_CSV.name}")

    # ── Launch SUMO via TraCI ─────────────────────────────────────────────────
    sumo_bin = "sumo-gui" if args.gui else "sumo"
    sumo_cmd = [
        sumo_bin,
        "-c", str(SUMOCFG),
        "--step-length", str(STEP_S),
        "--no-step-log",
        "--collision.action", "warn",
    ]
    print(f"\nLaunching SUMO: {' '.join(sumo_cmd)}")
    traci.start(sumo_cmd, port=args.port)

    # ── Jam injection setup ───────────────────────────────────────────────────
    # Resolve jam zone FIRST — live mode only injects if real jam exists
    zone = resolve_jam_zone() if not args.mock else None
    inject_jam = args.mock or (args.inject_jam and zone is not None)

    if zone:
        JAM_Y_MIN, JAM_Y_MAX, JAM_X_MIN, JAM_X_MAX = zone
    else:
        # Fallback: BHPV–Nathayyapalem segment (RSU02→RSU03)
        JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0
        JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0

    JAM_SPEED_MS = 1.2    # ~4.3 km/h — below 5 km/h threshold
    JAM_START_S  = 60.0   # inject after vehicles have spread out
    JAM_END_S    = 350.0  # clear after 290 s
    jam_edges: set[str] = set()

    if inject_jam:
        print(f"  Jam injection enabled: y=[{JAM_Y_MIN:.0f},{JAM_Y_MAX:.0f}] "
              f"capped to {JAM_SPEED_MS} m/s from t={JAM_START_S}s")

    # ── Simulation loop ───────────────────────────────────────────────────────
    # fcd: vehicle_id → list of (time, x, y, speed_m/s, edge_id)
    fcd:          dict[str, list] = {}
    node_id_map:  dict[str, int]  = {}   # veh_id → ns3 node index (0-9)
    next_node_id: int = 0

    print(f"Running SUMO for {SIM_DURATION_S} s  (step = {STEP_S} s) ...")
    t_start = time.time()
    step    = 0

    try:
        while traci.simulation.getTime() < SIM_DURATION_S:
            traci.simulationStep()
            t = traci.simulation.getTime()
            step += 1

            for veh_id in traci.vehicle.getIDList():
                # Assign a stable NS-3 node index the first time we see this vehicle
                if veh_id not in node_id_map:
                    if next_node_id >= 10:
                        continue
                    node_id_map[veh_id] = next_node_id
                    next_node_id += 1
                    fcd[veh_id] = []

                x, y  = traci.vehicle.getPosition(veh_id)
                speed = traci.vehicle.getSpeed(veh_id)    # m/s
                edge  = traci.vehicle.getRoadID(veh_id)

                # Discover jam-zone edges dynamically as vehicles pass through
                if (inject_jam and not edge.startswith(":")
                        and JAM_Y_MIN <= y <= JAM_Y_MAX
                        and JAM_X_MIN <= x <= JAM_X_MAX):
                    jam_edges.add(edge)

                fcd[veh_id].append((t, x, y, speed, edge))

            # Apply / remove speed cap on jam segment edges
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
                            traci.edge.setMaxSpeed(je, 13.89)  # restore ~50 km/h
                        except Exception:
                            pass

            # Progress print every 60 steps
            if step % 60 == 0:
                n_active = len(traci.vehicle.getIDList())
                elapsed  = time.time() - t_start
                jam_info = f"  jam_edges={len(jam_edges)}" if inject_jam else ""
                print(f"  t={t:>5.0f}s  active_vehicles={n_active:>2}  "
                      f"wall_time={elapsed:.1f}s{jam_info}")

    except Exception as exc:
        print(f"ERROR during simulation: {exc}", file=sys.stderr)
        traci.close()
        return 1

    traci.close()
    wall = time.time() - t_start
    print(f"\nSUMO finished in {wall:.1f}s wall time.")
    print(f"Tracked {len(fcd)} vehicles:  {sorted(node_id_map.keys())}")

    # ── Write outputs ─────────────────────────────────────────────────────────
    print("\nWriting output files ...")

    # NS-3 needs the net object only for coordinate conversion (already in CSV)
    write_ns2_mobility(fcd, MOBILITY_NS2, node_id_map)
    print(f"  Mobility trace → {MOBILITY_NS2}")

    write_rsu_static(rsus, None, RSU_STATIC_JSON)
    print(f"  RSU positions  → {RSU_STATIC_JSON}")

    write_speed_log(fcd, SPEED_LOG_JSON, node_id_map)
    print(f"  Speed log      → {SPEED_LOG_JSON}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("  Phase 3 bridge outputs ready.")
    print("  Next: compile and run the NS-3 scenario.")
    print()
    print("  cd <ns3-dev-dir>")
    print("  cp -r /home/kali/vanet_claude/sim/ns3/*.cc .")
    print("  cp -r /home/kali/vanet_claude/sim/ns3/*.h  .")
    print("  ./ns3 run vanet-scenario")
    print("=" * 65)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
