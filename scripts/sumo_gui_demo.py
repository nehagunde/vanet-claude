#!/usr/bin/env python3
"""
sumo_gui_demo.py — Phase 5: Animated VANET demo inside SUMO-GUI.

Re-runs SUMO-GUI via TraCI and adds live visual overlays:
  • Vehicles colour-coded by speed  (green > 30 | yellow 5-30 | red < 5 km/h)
  • RSU radio-range circles         (blue dashed polygon, 300 m radius)
  • Jam segment edge highlight      (red lane colour when jam fires)
  • ⚠ POI alert text on map         (appears at jam location)
  • Rerouted vehicles turn green    (and get a "↗ REROUTED" label)

Usage:
    cd /home/kali/vanet_claude
    python3 scripts/sumo_gui_demo.py           # mock jam (default)
    python3 scripts/sumo_gui_demo.py --live    # use live traffic_state.json
    python3 scripts/sumo_gui_demo.py --delay 150   # slower playback
"""

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

PROJECT_ROOT     = Path(__file__).resolve().parent.parent
SUMOCFG          = PROJECT_ROOT / "sim"    / "sumo"   / "vanet.sumocfg"
RSU_CSV          = PROJECT_ROOT / "corridor" / "rsu_positions.csv"
TRAFFIC_STATE    = PROJECT_ROOT / "corridor" / "traffic_state.json"
REROUTE_LOG_JSON = PROJECT_ROOT / "output"  / "reroute_log.json"

SIM_DURATION_S = 600
STEP_S         = 1.0
RADIO_M        = 300.0        # 802.11p range

# ── Speed thresholds ──────────────────────────────────────────────────────────
JAM_KMH       = 5.0
SLOW_KMH      = 30.0
JAM_SPEED_MS  = 1.2           # injected cap (≈ 4.3 km/h)
JAM_START_S   = 60.0
JAM_END_S     = 350.0

# ── Colour tuples (RGBA) ──────────────────────────────────────────────────────
COL_GREEN  = (0,   200,  0,  255)
COL_YELLOW = (255, 200,  0,  255)
COL_RED    = (255,  40,  40, 255)
COL_REROUTE= (0,   255, 120, 255)
COL_RSU    = (0,   180, 216, 120)
COL_JAM_LN = (220,  20,  20, 200)
COL_ALERT  = (255,  60,  60, 255)


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_rsus() -> list[dict]:
    if not RSU_CSV.is_file():
        return []
    with RSU_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def resolve_jam_zone(rsus: list[dict]) -> tuple | None:
    """Return (y_min, y_max, x_min, x_max) of worst jammed segment."""
    if not TRAFFIC_STATE.is_file():
        return None
    try:
        with TRAFFIC_STATE.open(encoding="utf-8") as f:
            ts = json.load(f)
    except Exception:
        return None
    # Always pick segment with highest congestion ratio from live data
    segs = ts.get("segments", [])
    if not segs:
        return None
    jam_seg = max(segs, key=lambda s: s.get("congestion_ratio", 0))
    rsu_map = {r["id"]: r for r in rsus}
    fr = rsu_map.get(jam_seg["from_rsu"])
    to = rsu_map.get(jam_seg["to_rsu"])
    if not fr or not to:
        return None
    y1, y2 = float(fr["y_m"]), float(to["y_m"])
    x1, x2 = float(fr["x_m"]), float(to["x_m"])
    m = 200.0
    return (min(y1,y2)-m, max(y1,y2)+m, min(x1,x2)-m, max(x1,x2)+m)


def speed_colour(speed_ms: float) -> tuple:
    kmh = speed_ms * 3.6
    if kmh < JAM_KMH:
        return COL_RED
    if kmh < SLOW_KMH:
        return COL_YELLOW
    return COL_GREEN


def circle_polygon(cx: float, cy: float, r: float, n: int = 32) -> list:
    """Return list of (x,y) tuples forming a circle polygon."""
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((round(cx + r * math.cos(a), 2),
                    round(cy + r * math.sin(a), 2)))
    return pts


def load_reroute_events() -> dict[str, set]:
    """Return {t_s: {veh_id, ...}} from reroute_log.json."""
    result: dict[str, set] = {}
    if not REROUTE_LOG_JSON.is_file():
        return result
    try:
        with REROUTE_LOG_JSON.open(encoding="utf-8") as f:
            rl = json.load(f)
    except Exception:
        return result
    for ev in rl.get("reroute_events", []):
        key = str(ev["t_s"])
        result.setdefault(key, set()).add(ev["veh_id"])
    return result


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live",  action="store_true",
                    help="Use live traffic_state.json for jam zone")
    ap.add_argument("--delay", type=int, default=80,
                    help="SUMO-GUI step delay in ms (default: 80)")
    ap.add_argument("--port",  type=int, default=8815)
    args = ap.parse_args()

    try:
        import traci
        import traci.constants as tc
    except ImportError:
        print("ERROR: traci not installed.", file=sys.stderr)
        return 1

    rsus = load_rsus()
    if not rsus:
        print("ERROR: rsu_positions.csv not found.", file=sys.stderr)
        return 1

    # Jam zone
    zone = resolve_jam_zone(rsus) if args.live else None
    if zone:
        JAM_Y_MIN, JAM_Y_MAX, JAM_X_MIN, JAM_X_MAX = zone
        print(f"  Live jam zone: y=[{JAM_Y_MIN:.0f},{JAM_Y_MAX:.0f}]")
    else:
        JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0
        JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0
        print("  Mock jam zone: BHPV–Nathayyapalem (y=3700–4800)")

    reroute_events = load_reroute_events()
    rerouted_vehs: set[str] = set()

    # Launch SUMO-GUI
    sumo_cmd = [
        "sumo-gui",
        "-c", str(SUMOCFG),
        "--step-length", str(STEP_S),
        "--no-step-log",
        "--collision.action", "warn",
        "--delay", str(args.delay),
        "--start",              # auto-start without clicking Play
    ]
    print(f"\nLaunching SUMO-GUI ...")
    traci.start(sumo_cmd, port=args.port)

    # ── Add RSU range circles ─────────────────────────────────────────────────
    print("Adding RSU overlays ...")
    for rsu in rsus:
        x, y = float(rsu["x_m"]), float(rsu["y_m"])
        pid  = f"range_{rsu['id']}"
        pts  = circle_polygon(x, y, RADIO_M)
        try:
            traci.polygon.add(pid, pts, color=COL_RSU,
                              fill=False, lineWidth=2, layer=10)
        except Exception:
            pass
        # RSU label POI
        try:
            traci.poi.add(f"lbl_{rsu['id']}", x, y,
                          color=COL_RSU, layer=15, width=2, height=2)
        except Exception:
            pass

    # ── Simulation state ──────────────────────────────────────────────────────
    jam_edges:    set[str] = set()
    jam_fired:    bool     = False
    alert_poi_id: str      = "vanet_alert"
    step = 0

    print(f"Running {SIM_DURATION_S}s simulation with VANET overlays ...\n")

    try:
        while traci.simulation.getTime() < SIM_DURATION_S:
            traci.simulationStep()
            t = traci.simulation.getTime()
            step += 1

            vehicles = traci.vehicle.getIDList()

            for veh_id in vehicles:
                x, y   = traci.vehicle.getPosition(veh_id)
                speed  = traci.vehicle.getSpeed(veh_id)     # m/s
                edge   = traci.vehicle.getRoadID(veh_id)
                kmh    = speed * 3.6

                # ── Discover jam zone edges ───────────────────────────────────
                if (not edge.startswith(":")
                        and JAM_Y_MIN <= y <= JAM_Y_MAX
                        and JAM_X_MIN <= x <= JAM_X_MAX):
                    jam_edges.add(edge)

                # ── Colour vehicle by speed ───────────────────────────────────
                col = speed_colour(speed)
                # Override if rerouted
                if veh_id in rerouted_vehs:
                    col = COL_REROUTE
                traci.vehicle.setColor(veh_id, col)

            # ── Apply / remove jam speed cap ──────────────────────────────────
            if jam_edges:
                if JAM_START_S <= t <= JAM_END_S:
                    for je in jam_edges:
                        try:
                            traci.edge.setMaxSpeed(je, JAM_SPEED_MS)
                        except Exception:
                            pass

                    # Highlight jam lanes red
                    for je in jam_edges:
                        try:
                            n_lanes = traci.edge.getLaneNumber(je)
                            for li in range(n_lanes):
                                traci.lane.setColor(f"{je}_{li}", COL_JAM_LN)
                        except Exception:
                            pass

                    # Fire jam alert POI once
                    if not jam_fired and t >= JAM_START_S + 30:
                        jam_fired = True
                        # Centroid of jam edges
                        cx = sum(float(r["x_m"]) for r in rsus
                                 if r["id"] in ("rsu_02","rsu_03")) / 2
                        cy = sum(float(r["y_m"]) for r in rsus
                                 if r["id"] in ("rsu_02","rsu_03")) / 2
                        try:
                            traci.poi.add(
                                alert_poi_id,
                                cx, cy + 150,
                                color=COL_ALERT,
                                layer=20, width=5, height=5,
                            )
                            # Add text label as second POI
                            traci.poi.add(
                                alert_poi_id + "_txt",
                                cx, cy + 280,
                                color=COL_ALERT,
                                layer=20, width=1, height=1,
                            )
                        except Exception:
                            pass
                        print(f"  *** t={t:.0f}s  JAM ALERT fired on "
                              f"{len(jam_edges)} edge(s) ***")

                elif t > JAM_END_S:
                    # Restore speeds and lane colours
                    for je in jam_edges:
                        try:
                            traci.edge.setMaxSpeed(je, 13.89)
                        except Exception:
                            pass
                        try:
                            n_lanes = traci.edge.getLaneNumber(je)
                            for li in range(n_lanes):
                                traci.lane.setColor(
                                    f"{je}_{li}", (255,255,255,0))  # reset
                        except Exception:
                            pass
                    # Remove alert POI
                    for pid in (alert_poi_id, alert_poi_id + "_txt"):
                        try:
                            traci.poi.remove(pid)
                        except Exception:
                            pass

            # ── Rerouting ─────────────────────────────────────────────────────
            t_key = str(int(t))
            newly_rerouted = reroute_events.get(t_key, set())
            if newly_rerouted:
                for veh_id in newly_rerouted & set(vehicles):
                    if veh_id not in rerouted_vehs:
                        rerouted_vehs.add(veh_id)
                        # Inflate travel time so SUMO re-routes around jam edge
                        for je in jam_edges:
                            try:
                                traci.edge.adaptTraveltime(je, 9999.0)
                            except Exception:
                                pass
                        try:
                            traci.vehicle.rerouteTraveltime(veh_id)
                            traci.vehicle.setColor(veh_id, COL_REROUTE)
                        except Exception:
                            pass
                        print(f"  t={t:.0f}s  ↗ REROUTED {veh_id}")

            # Progress log every 60 s
            if step % 60 == 0:
                n_slow = sum(
                    1 for v in vehicles
                    if traci.vehicle.getSpeed(v) * 3.6 < JAM_KMH
                )
                print(f"  t={t:>5.0f}s  vehicles={len(vehicles):>2}  "
                      f"slow(<5km/h)={n_slow}  "
                      f"jam_edges={len(jam_edges)}  "
                      f"rerouted={len(rerouted_vehs)}")

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        traci.close()
        return 1

    traci.close()
    print("\nSUMO-GUI demo finished.")
    print(f"  Jam edges found  : {sorted(jam_edges)}")
    print(f"  Vehicles rerouted: {sorted(rerouted_vehs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
