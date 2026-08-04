#!/usr/bin/env python3
"""
generate_routes.py — Phase 2: generate SUMO vehicle routes from traffic state.

Reads:
  corridor/traffic_state.json  — per-segment congestion (Google Maps or mock)
  corridor/rsu_positions.csv   — RSU lat/lon positions along NH-16
  corridor/corridor.net.xml    — SUMO road network

Writes:
  sim/sumo/routes.rou.xml      — 10 OBU vehicles as <trip> elements
  sim/sumo/vanet.sumocfg       — SUMO config tying net + routes together

Vehicle distribution logic:
  Vehicles are distributed across the 6 corridor segments weighted by
  congestion ratio.  Higher ratio = more vehicles queued on that segment.
  Minimum 1 vehicle per segment; adjusted to exactly N_VEHICLES total.

  Example (mock jam on seg_02_03, ratio=2.20):
    seg_00_01 → 1 vehicle   (free,     ratio 1.05)
    seg_01_02 → 1 vehicle   (light,    ratio 1.25)
    seg_02_03 → 3 vehicles  (JAM,      ratio 2.20)  ← alert fires here
    seg_03_04 → 2 vehicles  (heavy,    ratio 1.85)
    seg_04_05 → 2 vehicles  (moderate, ratio 1.45)
    seg_05_06 → 1 vehicle   (light,    ratio 1.15)

Usage:
  python3 data_node/generate_routes.py            # live traffic_state.json
  python3 data_node/generate_routes.py --mock     # mock jam fixture
  python3 data_node/generate_routes.py --vehicles 10 --duration 600
"""

import argparse
import csv
import json
import math
import sys
from pathlib import Path

# ── Paths (relative to project root) ─────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).resolve().parent.parent
TRAFFIC_JSON    = PROJECT_ROOT / "corridor" / "traffic_state.json"
MOCK_JSON       = PROJECT_ROOT / "data_node" / "mock" / "traffic_state.mock.json"
RSU_CSV         = PROJECT_ROOT / "corridor" / "rsu_positions.csv"
NET_XML         = PROJECT_ROOT / "corridor" / "corridor.net.xml"
ROUTES_OUT      = PROJECT_ROOT / "sim" / "sumo" / "routes.rou.xml"
SUMOCFG_OUT     = PROJECT_ROOT / "sim" / "sumo" / "vanet.sumocfg"

# ── Simulation constants (locked in BRIEF §2) ─────────────────────────────────
N_VEHICLES      = 10     # fixed OBU cohort
SIM_DURATION_S  = 600    # 10 minutes simulated time
DEPART_SPREAD_S = 120    # vehicles staggered over first 2 minutes
FREE_FLOW_KMH   = 50.0   # NH-16 urban speed limit
EDGE_SEARCH_R_M = 600.0  # radius to search for nearest SUMO edge

# ANSI colours for terminal summary table
_C = {
    "free":     "\033[92m",   # bright green
    "light":    "\033[93m",   # bright yellow
    "moderate": "\033[33m",   # dark yellow
    "heavy":    "\033[91m",   # bright red
    "jam":      "\033[31m",   # dark red
}
_R = "\033[0m"
_B = "\033[1m"


# ── Geometry ──────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres."""
    R  = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_traffic(mock: bool) -> dict:
    """Load traffic_state.json (live or mock)."""
    path = MOCK_JSON if mock else TRAFFIC_JSON
    if not path.is_file():
        print(f"ERROR: {path} not found.", file=sys.stderr)
        if mock:
            print("  → Run this script once without --mock to create the file,\n"
                  "    or check data_node/mock/traffic_state.mock.json exists.",
                  file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def load_rsus() -> list[dict]:
    """Load RSU positions from CSV."""
    if not RSU_CSV.is_file():
        print(f"ERROR: {RSU_CSV} not found. Run place_rsus.py first.",
              file=sys.stderr)
        sys.exit(1)
    with RSU_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ── SUMO edge finder ──────────────────────────────────────────────────────────

def find_nearest_edge(net, lat: float, lon: float,
                      search_r: float = EDGE_SEARCH_R_M) -> str | None:
    """
    Return the ID of the nearest driveable SUMO edge to (lat, lon).

    Driveability filter: excludes internal junction edges (id starts with ':')
    and edges with no lanes (pedestrian areas, etc.).
    Tries expanding the search radius twice before giving up.
    """
    x, y = net.convertLonLat2XY(lon, lat)

    for radius in (search_r, search_r * 2, search_r * 4):
        neighbours = net.getNeighboringEdges(x, y, radius)
        if not neighbours:
            continue
        # Keep only driveable edges (not internal junctions, not footpaths)
        driveable = [
            (e, d) for e, d in neighbours
            if not e.getID().startswith(":")
            and e.getLaneNumber() > 0
            and e.getSpeed() > 1.0   # > 1 m/s → excludes pedestrian edges
        ]
        if driveable:
            return min(driveable, key=lambda pair: pair[1])[0].getID()

    return None   # nothing found even after expanding


# ── Vehicle distribution ──────────────────────────────────────────────────────

def distribute_vehicles(segments: list[dict], n: int) -> list[int]:
    """
    Allocate n vehicles across segments proportionally to congestion ratio.

    Congestion ratio > 1 means traffic slower than free-flow.
    Cap at 2.5 to prevent extreme skew when one segment is severely jammed.
    Every segment gets at least 1 vehicle so every RSU sees traffic.
    """
    # Weight each segment by how congested it is
    weights = [min(seg["congestion_ratio"], 2.5) for seg in segments]
    total   = sum(weights)

    # Proportional allocation, floor to minimum 1
    counts = [max(1, round(n * w / total)) for w in weights]

    # Adjust total to exactly n by adding/removing from most/least congested
    diff = sum(counts) - n
    if diff > 0:
        # Remove from least-congested segments that have more than 1
        sorted_idx = sorted(range(len(counts)), key=lambda i: weights[i])
        for i in sorted_idx:
            if diff == 0:
                break
            if counts[i] > 1:
                counts[i] -= 1
                diff -= 1
    elif diff < 0:
        # Add to most-congested segments
        sorted_idx = sorted(range(len(counts)), key=lambda i: -weights[i])
        for i in sorted_idx:
            if diff == 0:
                break
            counts[i] += 1
            diff += 1

    return counts


# ── Writers ───────────────────────────────────────────────────────────────────

def write_routes(segments: list[dict], rsus: list[dict],
                 edge_ids: list[str | None], counts: list[int],
                 out_path: Path, duration: int) -> None:
    """
    Write sim/sumo/routes.rou.xml.

    Uses SUMO <trip> elements (not <route>): SUMO auto-routes each vehicle
    from its start edge to the NAD Junction end edge using Dijkstra.
    This is simpler than manually listing every edge in the path.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    max_speed_ms = round(FREE_FLOW_KMH / 3.6, 2)   # 50 km/h → 13.89 m/s

    # Destination = edge nearest to rsu_06 (NAD Junction end)
    end_edge = edge_ids[-1]
    if end_edge is None:
        print("ERROR: could not find SUMO edge near NAD Junction (rsu_06).",
              file=sys.stderr)
        sys.exit(1)

    total_vehs      = sum(counts)
    # Stagger departure times evenly over DEPART_SPREAD_S seconds
    depart_interval = DEPART_SPREAD_S / max(total_vehs - 1, 1)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<routes xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
        ' xsi:noNamespaceSchemaLocation='
        '"http://sumo.dlr.de/xsd/routes_file.xsd">',
        '',
        '    <!--',
        '        OBU Vehicle Type — Phase 2',
        '        All 10 vehicles are identical "passenger" cars equipped',
        '        with 802.11p OBU hardware (modelled in NS-3, Phase 3).',
        '        Color starts green (moving); TraCI changes it to red/yellow',
        '        in Phase 4 when jam is detected.',
        '    -->',
        f'    <vType id="obu"',
        f'           accel="2.6"',           # m/s²  comfortable urban acceleration
        f'           decel="4.5"',           # m/s²  comfortable braking
        f'           sigma="0.5"',           # driver imperfection (0=perfect, 1=chaotic)
        f'           length="4.5"',          # metres  standard sedan
        f'           minGap="2.5"',          # metres  safe following gap
        f'           maxSpeed="{max_speed_ms}"',   # 50 km/h in m/s
        f'           color="0,200,0"',       # green = moving normally
        f'           guiShape="passenger"/>',
        '',
    ]

    veh_id = 0

    for seg_idx, (seg, count) in enumerate(zip(segments, counts)):
        from_edge = edge_ids[seg_idx]

        if from_edge is None:
            print(f"  [WARN] No SUMO edge near rsu_{seg_idx:02d} "
                  f"— skipping {count} vehicle(s)", file=sys.stderr)
            veh_id += count
            continue

        # Skip if start == end (last RSU)
        if from_edge == end_edge and seg_idx < len(segments) - 1:
            from_edge = edge_ids[seg_idx]

        lines.append(
            f'    <!-- {seg["seg_id"]} | {seg["congestion_level"].upper()}'
            f' | ratio={seg["congestion_ratio"]:.3f}'
            f' | speed={seg["speed_kmh"]:.1f} km/h'
            f' | {count} vehicle(s) -->'
        )

        for _ in range(count):
            depart_t = round(veh_id * depart_interval, 1)
            lines.append(
                f'    <trip id="veh_{veh_id:02d}"'
                f' type="obu"'
                f' from="{from_edge}"'
                f' to="{end_edge}"'
                f' depart="{depart_t}"'
                f' departPos="random_free"'    # random position on start edge
                f' departSpeed="max"/>'        # enter at max allowed speed
            )
            veh_id += 1

        lines.append('')

    lines.append('</routes>')
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_sumocfg(out_path: Path, duration: int) -> None:
    """
    Write sim/sumo/vanet.sumocfg.

    Paths are relative to the sumocfg file location (sim/sumo/) so the
    project works regardless of where on disk it is mounted.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<configuration>',
        '',
        '    <input>',
        '        <!-- Network built from OSM in Phase 1 -->',
        '        <net-file value="../../corridor/corridor.net.xml"/>',
        '        <!-- Vehicle routes generated by generate_routes.py -->',
        '        <route-files value="routes.rou.xml"/>',
        '        <!-- RSU POI markers from Phase 1 -->',
        '        <additional-files value="../../corridor/rsu_pois.add.xml"/>',
        '    </input>',
        '',
        '    <time>',
        f'        <begin value="0"/>',
        f'        <end value="{duration}"/>',
        '        <step-length value="1"/>      <!-- 1 s steps for TraCI sync -->',
        '    </time>',
        '',
        '    <processing>',
        '        <!-- Route errors: warn but continue (avoids crash on edge mismatches) -->',
        '        <ignore-route-errors value="true"/>',
        '        <collision.action value="warn"/>',
        '        <!-- Teleport stuck vehicles after 300 s (prevents deadlock) -->',
        '        <time-to-teleport value="300"/>',
        '    </processing>',
        '',
        '    <output>',
        '        <!-- Per-vehicle trip stats written at simulation end (Phase 5) -->',
        '        <tripinfo-output value="../../output/results.csv"/>',
        '    </output>',
        '',
        '    <gui_only>',
        '        <gui-settings-file value="../../corridor/sumo_view.xml"/>',
        '        <tracker-interval value="0.5"/>',
        '    </gui_only>',
        '',
        '</configuration>',
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Console summary ───────────────────────────────────────────────────────────

def print_summary(segments: list[dict], counts: list[int],
                  edge_ids: list[str | None], source: str) -> None:
    W = 100
    print()
    print("=" * W)
    print(f"  {_B}VEHICLE DISTRIBUTION — NH-16 Gajuwaka → NAD, Visakhapatnam{_R}")
    print(f"  Traffic source: {_B}{source}{_R}   |   "
          f"Total vehicles: {_B}{sum(counts)}{_R}")
    print("=" * W)
    print(f"  {'Segment':<12} {'Level':<10} {'Ratio':>6}  {'km/h':>6}  "
          f"{'Vehicles':>9}  SUMO start edge")
    print("  " + "─" * (W - 2))

    for seg, count, eid in zip(segments, counts, edge_ids):
        lvl    = seg["congestion_level"]
        colour = _C.get(lvl, "")
        edge_s = eid if eid else f"\033[91mNOT FOUND\033[0m"
        bar    = "▮" * count + "▯" * (N_VEHICLES - count)
        print(f"  {seg['seg_id']:<12}"
              f" {colour}{lvl:<10}{_R}"
              f" {seg['congestion_ratio']:>6.3f}"
              f"  {seg['speed_kmh']:>6.1f}"
              f"  {colour}{count:>3}{_R} {bar}  {edge_s}")

    print("  " + "─" * (W - 2))

    # Highlight jam segments — these are where VANET alerts will fire
    jam_segs = [seg["seg_id"] for seg in segments
                if seg["congestion_level"] == "jam"]
    if jam_segs:
        print(f"\n  {_C['jam']}{_B}⚠  JAM on: {', '.join(jam_segs)}"
              f" — VANET U-turn alert will fire here{_R}")
    else:
        print(f"\n  {_C['free']}✓  No jams in current traffic state.{_R}")
        print(f"     Use --mock to simulate a peak-hour jam scenario.")

    print("=" * W)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--mock", action="store_true",
        help="Use mock jam fixture (data_node/mock/traffic_state.mock.json)",
    )
    ap.add_argument(
        "--vehicles", type=int, default=N_VEHICLES,
        help=f"Number of OBU vehicles to spawn (default: {N_VEHICLES})",
    )
    ap.add_argument(
        "--duration", type=int, default=SIM_DURATION_S,
        help=f"Simulation duration in seconds (default: {SIM_DURATION_S})",
    )
    args = ap.parse_args()

    # ── Step 1: load inputs ──────────────────────────────────────────────────
    mode = "MOCK (jam scenario)" if args.mock else "LIVE"
    print(f"\nLoading traffic state [{mode}] ...")
    traffic  = load_traffic(args.mock)
    segments = traffic["segments"]
    source   = traffic.get("source", "unknown")
    print(f"  {len(segments)} segments | fetched: {traffic['fetched_at']}")

    print("Loading RSU positions ...")
    rsus = load_rsus()
    print(f"  {len(rsus)} RSUs")

    print("Loading SUMO network (this takes a few seconds) ...")
    try:
        import sumolib
    except ImportError:
        print("ERROR: sumolib not installed.\n"
              "  pip install sumolib  OR  re-run scripts/install_kali.sh",
              file=sys.stderr)
        return 1

    if not NET_XML.is_file():
        print(f"ERROR: {NET_XML} not found. Run build_corridor.sh first.",
              file=sys.stderr)
        return 1

    net = sumolib.net.readNet(str(NET_XML))
    print(f"  Loaded: {NET_XML.name}")

    # ── Step 2: find nearest SUMO edge for each RSU position ─────────────────
    print(f"\nFinding nearest driveable SUMO edges ...")
    edge_ids: list[str | None] = []
    for rsu in rsus:
        eid = find_nearest_edge(net, float(rsu["lat"]), float(rsu["lon"]))
        status = eid if eid else "\033[91mNOT FOUND\033[0m"
        print(f"  {rsu['id']:8s}  lat={rsu['lat']}  lon={rsu['lon']}  → {status}")
        edge_ids.append(eid)

    # ── Step 3: distribute vehicles by congestion weight ──────────────────────
    print(f"\nDistributing {args.vehicles} vehicles across {len(segments)} segments ...")
    counts = distribute_vehicles(segments, args.vehicles)
    print_summary(segments, counts, edge_ids, source)

    # ── Step 4: write routes.rou.xml ─────────────────────────────────────────
    write_routes(segments, rsus, edge_ids, counts, ROUTES_OUT, args.duration)
    print(f"  Routes  → {ROUTES_OUT}")

    # ── Step 5: write vanet.sumocfg ──────────────────────────────────────────
    write_sumocfg(SUMOCFG_OUT, args.duration)
    print(f"  Config  → {SUMOCFG_OUT}")

    print()
    print("  ── Preview in SUMO-GUI:")
    print(f"     sumo-gui -c {SUMOCFG_OUT}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
