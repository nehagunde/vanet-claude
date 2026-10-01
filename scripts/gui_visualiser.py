#!/usr/bin/env python3
"""
gui_visualiser.py — Animated VANET GUI.

Reads speed_log.json + rsu_static.json + v2/alerts.log + v2/reroute_log.json
and produces output/vanet_gui_{mode}.html.

All SENDER badges, relay arrows, RECEIVER badges, and alert banners are driven
exclusively by timestamped events parsed from output/v2/alerts.log.
No events are inferred from vehicle speeds or heuristic proximity logic.
"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT       = Path(__file__).resolve().parent.parent
SPEED_LOG_JSON     = PROJECT_ROOT / "sim"      / "bridge" / "speed_log.json"
RSU_STATIC_JSON    = PROJECT_ROOT / "sim"      / "bridge" / "rsu_static.json"
JAM_REPORT_JSON    = PROJECT_ROOT / "output"   / "jam_report.json"
REROUTE_LOG_V2     = PROJECT_ROOT / "output"   / "v2" / "reroute_log.json"
REROUTE_LOG_LEGACY = PROJECT_ROOT / "output"   / "reroute_log.json"
ALERTS_LOG_V2      = PROJECT_ROOT / "output"   / "v2" / "alerts.log"
ALERTS_LOG_LEGACY  = PROJECT_ROOT / "output"   / "alerts.log"
TRAFFIC_STATE_JSON = PROJECT_ROOT / "corridor" / "traffic_state.json"

# Congestion level → road colour (live mode)
CONGESTION_COLOURS = {
    "free":     "#3fb950",   # green
    "light":    "#7ee787",   # light green
    "moderate": "#ffbe0b",   # amber
    "heavy":    "#ff8c00",   # orange
    "slow":     "#ff8c00",
    "jam":      "#ff4444",   # red
}


def load_json(p, default):
    if not Path(p).is_file():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def build_compact_frames(speed_log: dict) -> dict:
    frames = {}
    for t_str, step in speed_log.items():
        t = int(t_str)
        frames[t] = {}
        for veh_id, info in step.items():
            frames[t][veh_id] = [
                round(info["x"],   1),
                round(info["y"],   1),
                round(info["speed_kmh"], 1),
            ]
    return frames


def parse_alerts_log_gui(log_path: Path) -> dict:
    """
    Parse output/v2/alerts.log into GUI event data.

    Returns:
      jam_detected  : {t(int): [node_id, ...]}  — OBUs that sent JAM_DETECTED
      quorum_reached: {t, rsu, vehicles:[int]}   — first QUORUM_REACHED
      relay_sent    : {t, from_rsu, peer, msg}   — first RELAY_SENT at quorum RSU
      relay_recv    : {t, rsu, from_rsu}         — first RELAY_RECV
      ja_sent       : {t, rsu}                   — first SENT=JAM_ALERT (from relay RSU)
      ja_recv       : {t(int): [obu_id, ...]}    — OBUs that received JAM_ALERT
    """
    events = {
        "jam_detected":   {},
        "quorum_reached": None,
        "relay_sent":     None,
        "relay_recv":     None,
        "ja_sent":        None,
        "ja_recv":        {},
    }
    if not log_path.exists():
        return events

    pat_jd = re.compile(r'\[T=([0-9.]+)\]\s+NODE=(\d+)\s+SENT=JAM_DETECTED')
    pat_qr = re.compile(r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+QUORUM_REACHED\s+vehicles=\[([^\]]*)\]')
    pat_rs = re.compile(r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+RELAY_SENT\s+PEER=([\d.]+)\s+MSG="([^"]*)"')
    pat_rr = re.compile(r'\[T=([0-9.]+)\]\s+RSU=(\d+)\s+RELAY_RECV\s+FROM_RSU=(\d+)')
    pat_ja = re.compile(r'\[T=([0-9.]+)\]\s+NODE=(\d+)\s+SENT=JAM_ALERT')
    pat_jr = re.compile(r'\[T=([0-9.]+)\]\s+OBU=(\d+)\s+JAM_ALERT_RECV')

    with log_path.open(encoding="utf-8") as f:
        for line in f:
            m = pat_jd.search(line)
            if m:
                t = int(float(m.group(1)))
                events["jam_detected"].setdefault(t, []).append(int(m.group(2)))
                continue
            m = pat_qr.search(line)
            if m and events["quorum_reached"] is None:
                t = int(float(m.group(1)))
                vehs = [int(v.strip()) for v in m.group(3).split(",")
                        if v.strip().lstrip("-").isdigit()]
                events["quorum_reached"] = {"t": t, "rsu": int(m.group(2)), "vehicles": vehs}
                continue
            m = pat_rs.search(line)
            if m and events["relay_sent"] is None:
                t = int(float(m.group(1)))
                events["relay_sent"] = {
                    "t": t, "from_rsu": int(m.group(2)),
                    "peer": m.group(3), "msg": m.group(4),
                }
                continue
            m = pat_rr.search(line)
            if m and events["relay_recv"] is None:
                t = int(float(m.group(1)))
                events["relay_recv"] = {
                    "t": t, "rsu": int(m.group(2)), "from_rsu": int(m.group(3)),
                }
                continue
            m = pat_ja.search(line)
            if m and events["ja_sent"] is None:
                t = int(float(m.group(1)))
                events["ja_sent"] = {"t": t, "rsu": int(m.group(2))}
                continue
            m = pat_jr.search(line)
            if m:
                t = int(float(m.group(1)))
                events["ja_recv"].setdefault(t, []).append(int(m.group(2)))

    return events


def build_segment_info(traffic_state: list, rsu_static: list) -> list:
    """
    Build per-segment congestion info for live road colouring.
    Maps each traffic_state entry to the canvas indices of its from/to RSUs.
    Returns list of {from_id, to_id, level, colour, speed_kmh, ratio, label}.
    """
    rsu_id_to_idx = {r["rsu_id"]: i for i, r in enumerate(rsu_static)}
    segments = []
    for seg in traffic_state:
        fi = rsu_id_to_idx.get(seg.get("from_rsu", ""))
        ti = rsu_id_to_idx.get(seg.get("to_rsu",   ""))
        if fi is None or ti is None:
            continue
        level = seg.get("congestion_level", "free")
        segments.append({
            "fi":       fi,
            "ti":       ti,
            "level":    level,
            "colour":   CONGESTION_COLOURS.get(level, "#00b4d8"),
            "speed":    round(seg.get("speed_kmh", 50.0), 1),
            "ratio":    round(seg.get("congestion_ratio", 1.0), 2),
            "dist_km":  round(seg.get("distance_m", 0) / 1000, 2),
            "label":    f"{seg.get('from_rsu','?')} → {seg.get('to_rsu','?')}",
        })
    return segments


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "live"], default="mock")
    args = ap.parse_args()
    mode = args.mode

    gui_out = PROJECT_ROOT / "output" / f"vanet_gui_{mode}.html"
    print(f"Building GUI visualiser [{mode.upper()} MODE] ...")

    speed_log   = load_json(SPEED_LOG_JSON,  {})
    rsu_static  = load_json(RSU_STATIC_JSON, [])
    jam_report  = load_json(JAM_REPORT_JSON, [])

    # Live mode: load real Google Maps congestion data
    traffic_state = []
    if mode == "live":
        traffic_state = load_json(TRAFFIC_STATE_JSON, [])
        if traffic_state:
            print(f"  Loaded live traffic: {len(traffic_state)} segments from traffic_state.json")
            for seg in traffic_state:
                lvl = seg.get("congestion_level", "?")
                spd = seg.get("speed_kmh", 0)
                print(f"    {seg.get('from_rsu','?')} → {seg.get('to_rsu','?')}: {lvl} ({spd:.1f} km/h)")
        else:
            print("  [WARN] traffic_state.json not found — live road colours unavailable")
            print("         Run: python3 scripts/fetch_traffic.py --live  first")
    segment_info  = build_segment_info(traffic_state, rsu_static) if traffic_state else []

    # Use v2 reroute log if available, fall back to legacy
    reroute_log = load_json(REROUTE_LOG_V2, None)
    if reroute_log is None or not reroute_log.get("reroute_events"):
        reroute_log = load_json(REROUTE_LOG_LEGACY, {"reroute_events": []})

    # Use v2 alerts log if available, fall back to legacy
    alerts_log_path = ALERTS_LOG_V2 if ALERTS_LOG_V2.exists() else ALERTS_LOG_LEGACY
    ns3_events = parse_alerts_log_gui(alerts_log_path)
    print(f"  Loaded NS-3 events from: {alerts_log_path.name}")
    if ns3_events["quorum_reached"]:
        q = ns3_events["quorum_reached"]
        print(f"    QUORUM_REACHED at T={q['t']}s  RSU={q['rsu']}  vehicles={q['vehicles']}")
    if ns3_events["relay_sent"]:
        r = ns3_events["relay_sent"]
        print(f"    RELAY_SENT at T={r['t']}s  RSU={r['from_rsu']}")
    if ns3_events["ja_sent"]:
        ja = ns3_events["ja_sent"]
        print(f"    JAM_ALERT broadcast at T={ja['t']}s  RSU={ja['rsu']}")
    recv_count = sum(len(v) for v in ns3_events["ja_recv"].values())
    print(f"    JAM_ALERT_RECV: {recv_count} OBU reception(s)")

    if not speed_log:
        print("ERROR: speed_log.json not found. Run traci_supervisor.py first.")
        return 1

    frames = build_compact_frames(speed_log)

    # Reroute set keyed by "t_veh"
    reroute_set = {}
    for ev in reroute_log.get("reroute_events", []):
        reroute_set[f"{ev['t_s']}_{ev['veh_id']}"] = True

    # RSU info — use rsu_static so we have ns3_node_id for event-driven relay drawing
    rsu_info = []
    for r in rsu_static:
        rsu_info.append({
            "ns3_id": r["ns3_node_id"],
            "id":     r["rsu_id"],
            "area":   r["area"],
            "x":      r["x_m"],
            "y":      r["y_m"],
        })

    jam_events_js = []
    for jam in jam_report:
        jam_events_js.append({
            "start": jam["start_s"],
            "end":   jam["end_s"],
            "edge":  jam["edge"],
            "rsu":   jam.get("nearest_rsu", "?"),
            "speed": jam["avg_speed_kmh"],
            "vehs":  jam["vehicles"],
        })

    # Pre-compute first_sender_t: node_id -> first T they sent JAM_DETECTED
    first_sender_t = {}
    for t_int, nodes in ns3_events["jam_detected"].items():
        for n in nodes:
            if n not in first_sender_t or t_int < first_sender_t[n]:
                first_sender_t[n] = t_int

    # Build NS3_EVENTS payload for JS
    ns3_payload = {
        "first_sender_t": {str(k): v for k, v in first_sender_t.items()},
        "quorum":         ns3_events["quorum_reached"],
        "relay_sent":     ns3_events["relay_sent"],
        "relay_recv":     ns3_events["relay_recv"],
        "ja_sent":        ns3_events["ja_sent"],
        "ja_recv":        {str(k): v for k, v in ns3_events["ja_recv"].items()},
    }
    # Alert message from log, or fallback
    alert_msg_text = (ns3_events["relay_sent"] or {}).get(
        "msg", "Take alternate route — jam detected ahead")

    frames_json      = json.dumps(frames,        separators=(",", ":"))
    rsu_info_json    = json.dumps(rsu_info,      separators=(",", ":"))
    jam_json         = json.dumps(jam_events_js, separators=(",", ":"))
    reroute_json     = json.dumps(reroute_set,   separators=(",", ":"))
    ns3_events_json  = json.dumps(ns3_payload,   separators=(",", ":"))
    segment_info_json = json.dumps(segment_info, separators=(",", ":"))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<title>VANET GUI — NH-16 Visakhapatnam</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: #0d1117; color: #e6edf3;
  font-family: 'Segoe UI', sans-serif;
  display: flex; flex-direction: column; height: 100vh;
}}
header {{
  background: linear-gradient(90deg,#1a1a2e,#16213e);
  padding: 10px 24px; border-bottom: 2px solid #00b4d8;
  display: flex; align-items: center; gap: 20px;
}}
header h1 {{ font-size: 1.1rem; color: #00b4d8; }}
header p  {{ font-size: 0.78rem; color: #8b949e; }}
#sim-time {{
  margin-left: auto; font-size: 1.6rem; font-weight: bold;
  color: #00b4d8; font-family: monospace; min-width: 90px; text-align: right;
}}
.main-area {{ display: flex; flex: 1; overflow: hidden; }}
#canvas-wrap {{
  flex: 1; position: relative; background: #0d1117;
  display: flex; flex-direction: column;
}}
canvas {{ width: 100%; height: 100%; }}
#alert-banner {{
  display: none; position: absolute; bottom: 10px; left: 10px;
  background: #1a0808; border: 2px solid #ff4444;
  color: #ff6b6b; padding: 14px 18px; border-radius: 10px;
  font-size: 0.92rem; text-align: center;
  animation: pulse 1.2s infinite alternate; z-index: 10;
  pointer-events: none; min-width: 420px; max-width: 520px;
}}
.banner-title {{
  font-size: 1.05rem; font-weight: bold; color: #ff4444;
  margin-bottom: 10px; letter-spacing: 0.5px;
}}
.banner-grid {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
  margin-bottom: 10px;
}}
.banner-box {{
  padding: 8px 12px; border-radius: 7px; text-align: left;
}}
.banner-box.sender   {{ background: #3d0e0e; border: 1px solid #ff5555; }}
.banner-box.receiver {{ background: #2e1a00; border: 1px solid #ff9944; }}
.banner-box-title {{ font-size: 0.72rem; font-weight: bold; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 4px; }}
.banner-box.sender   .banner-box-title {{ color: #ff7777; }}
.banner-box.receiver .banner-box-title {{ color: #ffaa55; }}
.banner-vehs {{ font-size: 0.88rem; font-weight: bold; }}
.banner-box.sender   .banner-vehs {{ color: #ffaaaa; }}
.banner-box.receiver .banner-vehs {{ color: #ffd090; }}
.banner-uturn {{ font-size: 0.82rem; color: #ffcccc; border-top: 1px solid #ff444433; padding-top: 7px; }}
@keyframes pulse {{
  from {{ box-shadow: 0 0 10px #ff4444; }}
  to   {{ box-shadow: 0 0 28px #ff4444, 0 0 50px #ff000055; }}
}}
#reroute-flash {{
  display: none; position: absolute; bottom: 10px; right: 10px;
  background: #0a1f0a; border: 2px solid #3fb950;
  color: #3fb950; padding: 10px 18px; border-radius: 8px;
  font-weight: bold; font-size: 0.92rem; z-index: 10;
  pointer-events: none; max-width: 420px; text-align: center;
  box-shadow: 0 0 16px #3fb95055;
}}
.sidebar {{
  width: 280px; background: #161b22;
  border-left: 1px solid #30363d;
  display: flex; flex-direction: column; overflow-y: auto;
  padding: 16px; gap: 12px;
}}
.sidebar h3 {{ font-size: 0.78rem; color: #8b949e; text-transform: uppercase;
  letter-spacing: 1px; border-bottom: 1px solid #21262d;
  padding-bottom: 6px; margin-bottom: 4px; }}
.veh-list {{ display: flex; flex-direction: column; gap: 4px; }}
.veh-row {{ display: flex; align-items: center; gap: 8px;
  font-size: 0.78rem; padding: 4px 8px; border-radius: 4px;
  background: #0d1117; border: 1px solid #21262d; }}
.veh-dot {{ width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }}
.veh-name {{ width: 50px; color: #e6edf3; font-family: monospace; }}
.veh-spd  {{ flex: 1; text-align: right; font-family: monospace; }}
.veh-bar  {{ width: 100%; height: 3px; background: #21262d; border-radius: 2px; margin-top: 2px; }}
.veh-bar-fill {{ height: 100%; border-radius: 2px; transition: width 0.3s, background 0.3s; }}
.legend {{ font-size: 0.75rem; }}
.leg-row {{ display: flex; align-items: center; gap: 8px; padding: 3px 0; color: #8b949e; }}
.leg-dot  {{ width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0; }}
.leg-line {{ width: 24px; height: 2px; flex-shrink: 0; }}
.stats-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }}
.stat-box {{ background: #0d1117; border: 1px solid #21262d;
  border-radius: 6px; padding: 8px; text-align: center; }}
.stat-val {{ font-size: 1.3rem; font-weight: bold; color: #00b4d8; }}
.stat-lbl {{ font-size: 0.65rem; color: #8b949e; }}
.stat-box.danger .stat-val {{ color: #ff4444; }}
.stat-box.ok     .stat-val {{ color: #3fb950; }}
.controls {{ background: #161b22; border-top: 1px solid #30363d;
  padding: 10px 24px; display: flex; align-items: center; gap: 16px; }}
.btn {{ background: #21262d; border: 1px solid #30363d; color: #e6edf3;
  padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }}
.btn:hover {{ background: #2d333b; border-color: #00b4d8; }}
.btn.active {{ background: #00b4d8; color: #0d1117; border-color: #00b4d8; }}
#speed-slider {{ flex: 1; accent-color: #00b4d8; }}
#progress-bar-wrap {{ flex: 2; height: 6px; background: #21262d; border-radius: 3px;
  cursor: pointer; position: relative; }}
#progress-bar {{ height: 100%; background: #00b4d8; border-radius: 3px; width: 0%; transition: width 0.1s; }}
</style>
</head>
<body>

<header>
  <div>
    <h1>🚦 VANET Traffic Jam Detection — NH-16 Visakhapatnam
      <span style="font-size:0.6rem;padding:2px 10px;border-radius:20px;margin-left:10px;
        background:{'#1a3d1a' if mode=='live' else '#3d2a00'};
        color:{'#3fb950' if mode=='live' else '#ffbe0b'};
        border:1px solid {'#3fb950' if mode=='live' else '#ffbe0b'}">
        {'🟢 LIVE — Real Google Maps Data' if mode=='live' else '🟡 MOCK — Simulated Jam Scenario'}
      </span>
    </h1>
    <p>10 OBU vehicles · 7 RSU nodes · IEEE 802.11p WAVE · 300 m radio range · Events from NS-3 alerts.log</p>
  </div>
  <div id="sim-time">T = 0 s</div>
</header>

<div class="main-area">
  <div id="canvas-wrap">
    <canvas id="cvs"></canvas>
    <div id="alert-banner"></div>
    <div id="reroute-flash">🔀 Vehicles Rerouted — Alternate path assigned</div>
  </div>

  <div class="sidebar">
    <div>
      <h3>Live Stats</h3>
      <div class="stats-grid">
        <div class="stat-box" id="stat-active">
          <div class="stat-val" id="sv-active">0</div>
          <div class="stat-lbl">Active OBUs</div>
        </div>
        <div class="stat-box" id="stat-links">
          <div class="stat-val" id="sv-links">0</div>
          <div class="stat-lbl">Radio Links</div>
        </div>
        <div class="stat-box danger" id="stat-slow">
          <div class="stat-val" id="sv-slow">0</div>
          <div class="stat-lbl">In Jam</div>
        </div>
        <div class="stat-box ok" id="stat-rerouted">
          <div class="stat-val" id="sv-rerouted">0</div>
          <div class="stat-lbl">Rerouted</div>
        </div>
      </div>
    </div>

    <div>
      <h3>Vehicle Speeds</h3>
      <div class="veh-list" id="veh-list"></div>
    </div>

    <div class="legend">
      <h3>Legend</h3>
      <div class="leg-row"><div class="leg-dot" style="background:#3fb950"></div> Fast (&gt;30 km/h)</div>
      <div class="leg-row"><div class="leg-dot" style="background:#ffbe0b"></div> Slow (5–30 km/h)</div>
      <div class="leg-row"><div class="leg-dot" style="background:#ff4444"></div> Jam (&lt;5 km/h)</div>
      <div class="leg-row"><div class="leg-dot" style="background:#00b4d8;border-radius:3px;width:16px;height:16px"></div> RSU fixed node</div>
      <div class="leg-row"><div class="leg-line" style="background:#00b4d888"></div> V2I beacon link</div>
      <div class="leg-row"><div class="leg-line" style="background:#ffbe0b88"></div> V2V beacon link</div>
      <div class="leg-row"><div class="leg-line" style="background:#ff8c0088;border-top:2px dashed #ff8c00"></div> JAM_DETECTED (V2I)</div>
      <div class="leg-row"><div class="leg-line" style="background:#a050dc88;border-top:2px dashed #a050dc"></div> RSU backhaul relay</div>
      <div class="leg-row"><div class="leg-dot" style="background:#ff444400;border:2px dashed #ff444488;width:16px;height:16px;border-radius:50%"></div> JAM_ALERT broadcast</div>
      <div class="leg-row" style="margin-top:4px;font-size:0.7rem;color:#ff9999">📡 SENDER = JAM_DETECTED vehicle (NS-3 log)</div>
      <div class="leg-row" style="font-size:0.7rem;color:#ffcc88">📻 RECEIVER = JAM_ALERT_RECV vehicle (NS-3 log)</div>
    </div>

    <div>
      <h3>NS-3 Event Log</h3>
      <div id="event-log" style="font-size:0.72rem;color:#8b949e;max-height:200px;overflow-y:auto;font-family:monospace;">
        <div>Waiting for events...</div>
      </div>
    </div>
  </div>
</div>

<div class="controls">
  <button class="btn active" id="btn-play" onclick="togglePlay()">⏸ Pause</button>
  <button class="btn" onclick="resetSim()">↺ Reset</button>
  <span style="font-size:0.8rem;color:#8b949e;white-space:nowrap">Speed:</span>
  <input type="range" id="speed-slider" min="1" max="20" value="5"
         oninput="simSpeed=+this.value;document.getElementById('speed-lbl').textContent=this.value+'×'"/>
  <span id="speed-lbl" style="font-size:0.8rem;color:#00b4d8;min-width:28px">5×</span>
  <div id="progress-bar-wrap" onclick="seekTo(event)">
    <div id="progress-bar"></div>
  </div>
  <span style="font-size:0.8rem;color:#8b949e">600 s</span>
</div>

<script>
// ── Embedded data (generated by gui_visualiser.py) ────────────────────────────
const FRAMES        = {frames_json};
const RSU_INFO      = {rsu_info_json};
const JAM_EVENTS    = {jam_json};
const REROUTES      = {reroute_json};
const SEGMENT_INFO  = {segment_info_json};   // live mode: Google Maps congestion per segment
const SIM_MODE      = "{mode}";

// NS-3 events from output/v2/alerts.log — NOT inferred from vehicle speeds
const NS3_EVENTS  = {ns3_events_json};

const SIM_MAX  = 600;
const RADIO_M  = 300;
const JAM_END_T = 350;   // jam clears at T=350s (traci_supervisor JAM_END_S)

// ── Canvas setup ──────────────────────────────────────────────────────────────
const cvs = document.getElementById("cvs");
const ctx = cvs.getContext("2d");
function resize() {{ cvs.width = cvs.offsetWidth; cvs.height = cvs.offsetHeight; }}
window.addEventListener("resize", () => {{ resize(); draw(); }});
resize();

// ── Coordinate mapping ────────────────────────────────────────────────────────
const SUMO_Y_MIN = 1847, SUMO_Y_MAX = 7100;
const SUMO_X_MIN = 3200, SUMO_X_MAX = 5400;
function toCanvas(sx, sy) {{
  const PAD = 80;
  const cx = PAD + (sy - SUMO_Y_MIN) / (SUMO_Y_MAX - SUMO_Y_MIN) * (cvs.width  - PAD*2);
  const midX = (SUMO_X_MIN + SUMO_X_MAX) / 2;
  const cy = cvs.height / 2 + (sx - midX) / (SUMO_X_MAX - SUMO_X_MIN) * (cvs.height * 0.55);
  return [cx, cy];
}}
function metresToPx(m) {{
  return m / (SUMO_Y_MAX - SUMO_Y_MIN) * (cvs.width - 160);
}}

// ── Sim state ─────────────────────────────────────────────────────────────────
let simT = 0, playing = true, simSpeed = 5, lastRaf = null, accumMs = 0;
let rerouted = new Set();
const BANNER_HOLD_S = 30; // simulation seconds to hold banner visible

// ── RSU lookup helpers ────────────────────────────────────────────────────────
let _rsuCanvas = null;
function getRsuCanvas() {{
  if (!_rsuCanvas) {{
    _rsuCanvas = RSU_INFO.map(r => {{
      const [cx, cy] = toCanvas(r.x, r.y);
      return {{ ...r, cx, cy }};
    }});
  }}
  // Recompute cx/cy when canvas resizes (they depend on canvas dimensions)
  return RSU_INFO.map(r => {{
    const [cx, cy] = toCanvas(r.x, r.y);
    return {{ ...r, cx, cy }};
  }});
}}
function rsuByNs3Id(ns3_id) {{
  return getRsuCanvas().find(r => r.ns3_id === ns3_id) || null;
}}

// ── NS-3 event helpers ───────────────────────────────────────────────────────
// OBU node_id == index in veh_00..veh_09 (node 0 = veh_00, etc.)
function nodeId(veh_id) {{
  return parseInt(veh_id.replace("veh_", ""), 10);
}}

// Is this vehicle a JAM_DETECTED sender at time t?
// True from its first JAM_DETECTED until JAM_END_T (jam still active).
function isSender(veh_id, t) {{
  const ft = NS3_EVENTS.first_sender_t[String(nodeId(veh_id))];
  return ft !== undefined && ft <= t && t <= JAM_END_T;
}}

// Is the RSU-to-RSU relay arrow active? (RELAY_SENT event + hold window)
function relayArrowActive(t) {{
  const r = NS3_EVENTS.relay_sent;
  return r !== null && t >= r.t && t <= r.t + BANNER_HOLD_S;
}}

// Is RSU broadcasting JAM_ALERT?
function jaBroadcastActive(t) {{
  const ja = NS3_EVENTS.ja_sent;
  return ja !== null && t >= ja.t && t <= ja.t + BANNER_HOLD_S;
}}

// Did this OBU receive JAM_ALERT at time t (or within last 10s)?
function isReceiver(veh_id, t) {{
  const n = nodeId(veh_id);
  for (const [tStr, nodes] of Object.entries(NS3_EVENTS.ja_recv)) {{
    const et = parseInt(tStr, 10);
    if (et <= t && et >= t - 10 && nodes.includes(n)) return true;
  }}
  return false;
}}

// Should the alert banner be shown?
function bannerDue(t) {{
  const q = NS3_EVENTS.quorum;
  return q !== null && t >= q.t;
}}

// ── Draw one frame ────────────────────────────────────────────────────────────
function draw() {{
  const W = cvs.width, H = cvs.height;
  ctx.clearRect(0, 0, W, H);

  // Background
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#0d1117");
  bg.addColorStop(1, "#161b22");
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

  const t    = Math.floor(simT);
  const step = FRAMES[t] || {{}};
  const rsus = getRsuCanvas();

  // ── Road ─────────────────────────────────────────────────────────────────
  const road_pts = RSU_INFO.map(r => toCanvas(r.x, r.y));
  function drawRoad(color, width) {{
    ctx.beginPath();
    ctx.moveTo(road_pts[0][0], road_pts[0][1]);
    for (let i=1;i<road_pts.length;i++) ctx.lineTo(road_pts[i][0], road_pts[i][1]);
    ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.stroke();
  }}
  drawRoad("#30363d", 24);
  drawRoad("#444c56", 16);
  ctx.setLineDash([20,14]);
  drawRoad("#ffbe0b44", 2);
  ctx.setLineDash([]);

  // Jam segment highlight (timing from JAM_EVENTS injection, not NS-3)
  const activeJam = JAM_EVENTS.find(j => t >= j.start && t <= j.end) || null;
  if (activeJam && rsus.length >= 4) {{
    const [x1,y1] = toCanvas(rsus[2].x, rsus[2].y);
    const [x2,y2] = toCanvas(rsus[3].x, rsus[3].y);
    const grad = ctx.createLinearGradient(x1,y1,x2,y2);
    grad.addColorStop(0,"#ff444433"); grad.addColorStop(0.5,"#ff444488"); grad.addColorStop(1,"#ff444433");
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2);
    ctx.strokeStyle = grad; ctx.lineWidth = 20; ctx.stroke();
  }}

  // ── RSU radio range circles ────────────────────────────────────────────────
  const rangePx = metresToPx(RADIO_M);
  rsus.forEach(r => {{
    ctx.beginPath(); ctx.arc(r.cx, r.cy, rangePx, 0, Math.PI*2);
    ctx.strokeStyle = "#00b4d822"; ctx.lineWidth = 1; ctx.setLineDash([6,5]); ctx.stroke(); ctx.setLineDash([]);
  }});

  // ── Collect vehicle positions ─────────────────────────────────────────────
  const vehPos = {{}};
  Object.entries(step).forEach(([vid,[sx,sy,spd]]) => {{
    const [cx,cy] = toCanvas(sx,sy);
    vehPos[vid] = {{ cx, cy, sx, sy, spd }};
  }});
  const vehIds = Object.keys(vehPos);

  // ── V2I beacon links (distance-based — radio physics) ────────────────────
  let linkCount = 0;
  rsus.forEach(r => {{
    vehIds.forEach(vid => {{
      const v = vehPos[vid];
      const d = Math.hypot(v.sx - r.x, v.sy - r.y);
      if (d < RADIO_M) {{
        const alpha = 0.15 + 0.35 * (1 - d/RADIO_M);
        ctx.beginPath(); ctx.moveTo(r.cx,r.cy); ctx.lineTo(v.cx,v.cy);
        ctx.strokeStyle = `rgba(0,180,216,${{alpha}})`; ctx.lineWidth = 1.2; ctx.stroke();
        linkCount++;
      }}
    }});
  }});

  // ── V2V beacon links (distance-based) ────────────────────────────────────
  for (let i=0;i<vehIds.length;i++) {{
    for (let j=i+1;j<vehIds.length;j++) {{
      const a = vehPos[vehIds[i]], b = vehPos[vehIds[j]];
      const d = Math.hypot(a.sx-b.sx, a.sy-b.sy);
      if (d < RADIO_M) {{
        const alpha = 0.12 + 0.25 * (1-d/RADIO_M);
        ctx.beginPath(); ctx.moveTo(a.cx,a.cy); ctx.lineTo(b.cx,b.cy);
        ctx.strokeStyle = `rgba(255,190,11,${{alpha}})`; ctx.lineWidth = 1; ctx.stroke();
        linkCount++;
      }}
    }}
  }}

  // ── JAM_DETECTED links: sender vehicles → jam RSU (from NS-3 log) ────────
  // Shown from first JAM_DETECTED until JAM_END_T
  const jamRsuNode   = NS3_EVENTS.relay_sent  ? rsuByNs3Id(NS3_EVENTS.relay_sent.from_rsu) : null;
  const alertRsuNode = NS3_EVENTS.relay_recv  ? rsuByNs3Id(NS3_EVENTS.relay_recv.rsu)       : null;

  if (jamRsuNode) {{
    vehIds.forEach(vid => {{
      if (!isSender(vid, t)) return;
      const v = vehPos[vid];
      ctx.beginPath(); ctx.moveTo(v.cx, v.cy); ctx.lineTo(jamRsuNode.cx, jamRsuNode.cy);
      ctx.strokeStyle = "rgba(255,140,0,0.55)"; ctx.lineWidth = 1.5;
      ctx.setLineDash([4,5]); ctx.stroke(); ctx.setLineDash([]);
    }});
  }}

  // ── RSU-to-RSU backhaul relay arrow (NS-3 RELAY_SENT event) ─────────────
  if (relayArrowActive(t) && jamRsuNode && alertRsuNode) {{
    ctx.beginPath();
    ctx.moveTo(jamRsuNode.cx, jamRsuNode.cy);
    ctx.lineTo(alertRsuNode.cx, alertRsuNode.cy);
    ctx.strokeStyle = "rgba(160,80,220,0.9)"; ctx.lineWidth = 2.5;
    ctx.setLineDash([10,6]); ctx.stroke(); ctx.setLineDash([]);
    // Arrowhead
    const ang = Math.atan2(alertRsuNode.cy - jamRsuNode.cy, alertRsuNode.cx - jamRsuNode.cx);
    ctx.beginPath();
    ctx.moveTo(alertRsuNode.cx, alertRsuNode.cy);
    ctx.lineTo(alertRsuNode.cx - 12*Math.cos(ang-0.4), alertRsuNode.cy - 12*Math.sin(ang-0.4));
    ctx.lineTo(alertRsuNode.cx - 12*Math.cos(ang+0.4), alertRsuNode.cy - 12*Math.sin(ang+0.4));
    ctx.closePath(); ctx.fillStyle = "rgba(160,80,220,0.9)"; ctx.fill();
    // Label
    const rmx = (jamRsuNode.cx + alertRsuNode.cx)/2, rmy = (jamRsuNode.cy + alertRsuNode.cy)/2 - 10;
    ctx.fillStyle = "rgba(20,0,30,0.82)"; ctx.beginPath();
    ctx.roundRect(rmx-52, rmy-14, 104, 16, 4); ctx.fill();
    ctx.fillStyle = "#cc88ff"; ctx.font = "bold 9px 'Segoe UI'"; ctx.textAlign = "center";
    ctx.fillText("📡 RSU BACKHAUL RELAY", rmx, rmy-2);
  }}

  // ── JAM_ALERT broadcast ring at alertRSU (from NS-3 SENT=JAM_ALERT) ──────
  // Draws a pulsing orange ring to show RSU is broadcasting.
  // Specific arrows to vehicles are omitted when no JAM_ALERT_RECV events in log.
  if (jaBroadcastActive(t) && alertRsuNode) {{
    const pulse = 0.4 + 0.6 * Math.abs(Math.sin((t % 10) / 10 * Math.PI));
    ctx.beginPath(); ctx.arc(alertRsuNode.cx, alertRsuNode.cy, rangePx, 0, Math.PI*2);
    ctx.strokeStyle = `rgba(255,80,80,${{pulse * 0.45}})`; ctx.lineWidth = 3;
    ctx.setLineDash([12,8]); ctx.stroke(); ctx.setLineDash([]);

    // Label JAM_ALERT_BROADCAST
    ctx.fillStyle = "rgba(30,0,0,0.82)"; ctx.beginPath();
    ctx.roundRect(alertRsuNode.cx-54, alertRsuNode.cy + rangePx + 4, 108, 16, 4); ctx.fill();
    ctx.fillStyle = "#ff8888"; ctx.font = "bold 9px 'Segoe UI'"; ctx.textAlign = "center";
    ctx.fillText("⚠ JAM_ALERT broadcast", alertRsuNode.cx, alertRsuNode.cy + rangePx + 16);

    // Draw arrows to any OBU in range (if log shows JAM_ALERT_RECV at this t)
    const recvNow = NS3_EVENTS.ja_recv[String(t)] || [];
    recvNow.forEach(obu_id => {{
      const vid = "veh_" + String(obu_id).padStart(2, "0");
      if (!vehPos[vid]) return;
      const av = vehPos[vid];
      ctx.beginPath(); ctx.moveTo(alertRsuNode.cx, alertRsuNode.cy); ctx.lineTo(av.cx, av.cy);
      ctx.strokeStyle = "rgba(255,68,68,0.85)"; ctx.lineWidth = 2;
      ctx.setLineDash([8,5]); ctx.stroke(); ctx.setLineDash([]);
      const ang = Math.atan2(av.cy - alertRsuNode.cy, av.cx - alertRsuNode.cx);
      ctx.beginPath(); ctx.moveTo(av.cx, av.cy);
      ctx.lineTo(av.cx-10*Math.cos(ang-0.4), av.cy-10*Math.sin(ang-0.4));
      ctx.lineTo(av.cx-10*Math.cos(ang+0.4), av.cy-10*Math.sin(ang+0.4));
      ctx.closePath(); ctx.fillStyle = "rgba(255,68,68,0.9)"; ctx.fill();
    }});
  }}

  // ── Draw RSU nodes ────────────────────────────────────────────────────────
  rsus.forEach(r => {{
    const isAlertRsu = alertRsuNode && r.ns3_id === alertRsuNode.ns3_id;
    const isJamRsu   = jamRsuNode   && r.ns3_id === jamRsuNode.ns3_id;
    const rsuGlow = isJamRsu && isSender("veh_02", t) ? "#ff4444"
                  : isAlertRsu && jaBroadcastActive(t) ? "#ff8c00"
                  : "#00b4d8";
    const grad = ctx.createRadialGradient(r.cx, r.cy, 0, r.cx, r.cy, 18);
    grad.addColorStop(0, rsuGlow + "66"); grad.addColorStop(1, rsuGlow + "00");
    ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(r.cx, r.cy, 18, 0, Math.PI*2); ctx.fill();
    ctx.fillStyle = rsuGlow; ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 1.5;
    ctx.beginPath(); const s=9; ctx.rect(r.cx-s, r.cy-s, s*2, s*2); ctx.fill(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(r.cx, r.cy-s); ctx.lineTo(r.cx, r.cy-s-8);
    ctx.strokeStyle = rsuGlow; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = "#e6edf3"; ctx.font = "bold 10px 'Segoe UI'"; ctx.textAlign = "center";
    ctx.fillText(r.id, r.cx, r.cy + s + 14);
    ctx.fillStyle = "#8b949e"; ctx.font = "9px 'Segoe UI'";
    ctx.fillText(r.area, r.cx, r.cy + s + 25);
  }});

  // ── Draw vehicles ─────────────────────────────────────────────────────────
  let slowCount = 0;
  vehIds.forEach(vid => {{
    const v = vehPos[vid];
    const col = v.spd < 5 ? "#ff4444" : v.spd < 30 ? "#ffbe0b" : "#3fb950";
    if (v.spd < 5) slowCount++;
    const key = `${{t}}_${{vid}}`;
    if (REROUTES[key]) rerouted.add(vid);
    const isRerouted = rerouted.has(vid);
    const sender   = isSender(vid, t);
    const receiver = isReceiver(vid, t);

    // Glow
    const grad = ctx.createRadialGradient(v.cx, v.cy, 0, v.cx, v.cy, 16);
    grad.addColorStop(0, col+"66"); grad.addColorStop(1, col+"00");
    ctx.fillStyle = grad; ctx.beginPath(); ctx.arc(v.cx, v.cy, 16, 0, Math.PI*2); ctx.fill();

    // Vehicle circle
    ctx.beginPath(); ctx.arc(v.cx, v.cy, 7, 0, Math.PI*2);
    ctx.fillStyle = col;
    ctx.strokeStyle = isRerouted ? "#3fb950" : "#ffffff";
    ctx.lineWidth = isRerouted ? 2.5 : 1.5;
    ctx.fill(); ctx.stroke();

    if (isRerouted) {{
      ctx.fillStyle = "#3fb950"; ctx.font = "bold 12px monospace"; ctx.textAlign = "center";
      ctx.fillText("↗", v.cx+9, v.cy-7);
    }}

    ctx.fillStyle = "#e6edf3"; ctx.font = "bold 9px 'Segoe UI'"; ctx.textAlign = "center";
    ctx.fillText(vid.replace("veh_","V"), v.cx, v.cy-11);
    ctx.fillStyle = col; ctx.font = "9px monospace";
    ctx.fillText(v.spd.toFixed(0)+"k", v.cx, v.cy+19);

    // SENDER / RECEIVER badges — ONLY from NS-3 log events
    if (sender) {{
      ctx.fillStyle = "rgba(180,20,20,0.88)"; ctx.beginPath();
      ctx.roundRect(v.cx-22, v.cy-30, 44, 13, 3); ctx.fill();
      ctx.fillStyle = "#ffcccc"; ctx.font = "bold 8px 'Segoe UI'"; ctx.textAlign = "center";
      ctx.fillText("SENDER", v.cx, v.cy-20);
    }} else if (receiver) {{
      ctx.fillStyle = "rgba(180,90,0,0.88)"; ctx.beginPath();
      ctx.roundRect(v.cx-28, v.cy-30, 56, 13, 3); ctx.fill();
      ctx.fillStyle = "#ffe0b0"; ctx.font = "bold 8px 'Segoe UI'"; ctx.textAlign = "center";
      ctx.fillText("RECEIVER", v.cx, v.cy-20);
    }}
  }});

  // ── Direction labels ──────────────────────────────────────────────────────
  ctx.fillStyle = "#484f58"; ctx.font = "bold 11px 'Segoe UI'";
  ctx.textAlign = "left";  ctx.fillText("◀ Gajuwaka (South)", 8, H/2+5);
  ctx.textAlign = "right"; ctx.fillText("NAD Junction (North) ▶", W-8, H/2+5);

  // ── Sidebar stats ─────────────────────────────────────────────────────────
  document.getElementById("sv-active").textContent   = vehIds.length;
  document.getElementById("sv-links").textContent    = linkCount;
  document.getElementById("sv-slow").textContent     = slowCount;
  document.getElementById("sv-rerouted").textContent = rerouted.size;
  document.getElementById("sim-time").textContent    = `T = ${{t}} s`;
  document.getElementById("progress-bar").style.width = (t/SIM_MAX*100)+"%";

  // Vehicle list
  const listEl = document.getElementById("veh-list");
  if (vehIds.length > 0) {{
    listEl.innerHTML = vehIds.map(vid => {{
      const v = vehPos[vid];
      const col = v.spd < 5 ? "#ff4444" : v.spd < 30 ? "#ffbe0b" : "#3fb950";
      const pct = Math.min(100, v.spd/60*100);
      return `<div class="veh-row">
        <div class="veh-dot" style="background:${{col}}"></div>
        <span class="veh-name">${{vid.replace("veh_","V")}}</span>
        <div style="flex:1"><div class="veh-bar"><div class="veh-bar-fill"
          style="width:${{pct}}%;background:${{col}}"></div></div></div>
        <span class="veh-spd" style="color:${{col}}">${{v.spd.toFixed(1)}} km/h</span>
      </div>`;
    }}).join("");
  }} else {{
    listEl.innerHTML = '<div style="color:#484f58;font-size:0.78rem;padding:8px">No active vehicles</div>';
  }}

  // ── Alert banner (event-driven from NS-3 QUORUM_REACHED) ─────────────────
  const banner = document.getElementById("alert-banner");
  const rflash = document.getElementById("reroute-flash");

  if (bannerDue(t)) {{
    const q = NS3_EVENTS.quorum || {{}};
    const rs = NS3_EVENTS.relay_sent || {{}};
    const alertMsg = rs.msg || "Take alternate route — jam detected ahead";
    // Sender names from quorum vehicles (node_id → V##)
    const senderNames = (q.vehicles || [])
      .map(n => "V" + String(n).padStart(2, "0")).join(", ");
    // Receiver names from ja_recv log
    const allRecv = Object.values(NS3_EVENTS.ja_recv).flat();
    const recvText = allRecv.length > 0
      ? allRecv.map(n => "V"+String(n).padStart(2,"0")).join(", ")
      : "None (OBUs beyond RSU range at T=" + (rs.t || "—") + "s)";
    const relayRsu = alertRsuNode ? alertRsuNode.id : "rsu_01";
    const jamRsuLabel = jamRsuNode ? jamRsuNode.id : "rsu_02";
    banner.innerHTML =
      `<div class="banner-title">⚠️ QUORUM_REACHED — Jam confirmed at ${{jamRsuLabel}}</div>` +
      `<div class="banner-grid">` +
        `<div class="banner-box sender">` +
          `<div class="banner-box-title">📡 Senders (JAM_DETECTED)</div>` +
          `<div class="banner-vehs">${{senderNames || "—"}}</div>` +
          `<div style="font-size:0.75rem;color:#cc88ff;margin-top:4px">→ Relayed via ${{relayRsu}}</div>` +
        `</div>` +
        `<div class="banner-box receiver">` +
          `<div class="banner-box-title">📻 Receivers (JAM_ALERT_RECV)</div>` +
          `<div class="banner-vehs" style="font-size:0.8rem">${{recvText}}</div>` +
        `</div>` +
      `</div>` +
      `<div class="banner-uturn">↩ &nbsp;${{alertMsg}}</div>`;
    banner.style.display = "block";
  }} else {{
    banner.style.display = "none";
  }}

  // Reroute flash
  const reroutedNowIds = vehIds.filter(v => REROUTES[`${{t}}_${{v}}`]);
  if (reroutedNowIds.length > 0) {{
    const names = reroutedNowIds.map(v => v.replace("veh_","V")).join(", ");
    rflash.innerHTML = `✅ &nbsp;<b style="color:#7ee787">${{names}}</b> &nbsp;received JAM_ALERT → Rerouted successfully`;
    rflash.style.display = "block";
  }} else if (!rerouted.size) {{
    rflash.style.display = "none";
  }}

  // ── NS-3 milestone events → sidebar log ───────────────────────────────────
  if (NS3_EVENTS.quorum && t >= NS3_EVENTS.quorum.t) {{
    const key = `quorum_${{NS3_EVENTS.quorum.t}}`;
    if (!window._logged?.[key]) {{
      window._logged = window._logged || {{}};
      window._logged[key] = true;
      addEvent(`⚠ T=${{NS3_EVENTS.quorum.t}}s QUORUM_REACHED RSU=${{NS3_EVENTS.quorum.rsu}} vehicles=${{JSON.stringify(NS3_EVENTS.quorum.vehicles)}}`, "#ff6b6b");
    }}
  }}
  if (NS3_EVENTS.relay_sent && t >= NS3_EVENTS.relay_sent.t) {{
    const key = `relay_${{NS3_EVENTS.relay_sent.t}}`;
    if (!window._logged?.[key]) {{
      window._logged = window._logged || {{}};
      window._logged[key] = true;
      addEvent(`📡 T=${{NS3_EVENTS.relay_sent.t}}s RELAY_SENT RSU=${{NS3_EVENTS.relay_sent.from_rsu}}→RSU=${{NS3_EVENTS.relay_recv?.rsu||"?"}}`, "#cc88ff");
    }}
  }}
  if (NS3_EVENTS.ja_sent && t >= NS3_EVENTS.ja_sent.t) {{
    const key = `ja_${{NS3_EVENTS.ja_sent.t}}`;
    if (!window._logged?.[key]) {{
      window._logged = window._logged || {{}};
      window._logged[key] = true;
      addEvent(`📢 T=${{NS3_EVENTS.ja_sent.t}}s JAM_ALERT broadcast RSU=${{NS3_EVENTS.ja_sent.rsu}}`, "#ff8c00");
    }}
  }}
  for (const [tStr, obus] of Object.entries(NS3_EVENTS.ja_recv)) {{
    const et = parseInt(tStr, 10);
    if (t >= et) {{
      const key = `jarecv_${{tStr}}`;
      if (!window._logged?.[key]) {{
        window._logged = window._logged || {{}};
        window._logged[key] = true;
        addEvent(`✅ T=${{tStr}}s JAM_ALERT_RECV OBU=${{obus.join(",")}}`, "#3fb950");
      }}
    }}
  }}
  JAM_EVENTS.forEach(j => {{
    const key = `jam_${{j.start}}`;
    if (t >= j.start && !window._logged?.[key]) {{
      window._logged = window._logged || {{}};
      window._logged[key] = true;
      addEvent(`🚦 T=${{j.start}}s Jam injected on ${{j.rsu}} (avg ${{j.speed}} km/h)`, "#ffbe0b");
    }}
  }});
  vehIds.filter(v => REROUTES[`${{t}}_${{v}}`]).forEach(v => {{
    const key = `rr_${{t}}_${{v}}`;
    if (!window._logged?.[key]) {{
      window._logged = window._logged || {{}};
      window._logged[key] = true;
      addEvent(`↗ T=${{t}}s ${{v}} rerouted via alternate path`, "#3fb950");
    }}
  }});
}}

function addEvent(msg, color) {{
  const el = document.getElementById("event-log");
  const div = document.createElement("div");
  div.style.color = color; div.textContent = msg;
  if (el.children[0]?.textContent === "Waiting for events...") el.innerHTML = "";
  el.insertBefore(div, el.firstChild);
  while (el.children.length > 25) el.removeChild(el.lastChild);
}}

// ── Animation loop ────────────────────────────────────────────────────────────
function loop(ts) {{
  if (lastRaf === null) lastRaf = ts;
  const dtMs = ts - lastRaf; lastRaf = ts;
  if (playing) {{
    accumMs += dtMs * simSpeed;
    while (accumMs >= 1000) {{ simT = Math.min(simT+1, SIM_MAX); accumMs -= 1000; }}
    if (simT >= SIM_MAX) {{ playing = false; document.getElementById("btn-play").textContent = "▶ Play"; }}
  }}
  draw();
  requestAnimationFrame(loop);
}}

function togglePlay() {{
  playing = !playing;
  document.getElementById("btn-play").textContent = playing ? "⏸ Pause" : "▶ Play";
  document.getElementById("btn-play").classList.toggle("active", playing);
}}

function resetSim() {{
  simT = 0; accumMs = 0; lastRaf = null; rerouted.clear(); window._logged = {{}};
  document.getElementById("alert-banner").style.display = "none";
  document.getElementById("reroute-flash").style.display = "none";
  document.getElementById("event-log").innerHTML = "<div>Waiting for events...</div>";
  playing = true;
  document.getElementById("btn-play").textContent = "⏸ Pause";
  document.getElementById("btn-play").classList.add("active");
}}

function seekTo(e) {{
  const rect = e.currentTarget.getBoundingClientRect();
  simT = Math.round((e.clientX - rect.left) / rect.width * SIM_MAX);
  accumMs = 0; rerouted.clear(); window._logged = {{}};
}}

requestAnimationFrame(loop);
</script>
</body>
</html>"""

    gui_out.parent.mkdir(parents=True, exist_ok=True)
    gui_out.write_text(html, encoding="utf-8")
    print(f"  GUI → {gui_out}")
    print()
    print("=" * 55)
    print(f"  Open: output/vanet_gui_{mode}.html")
    print("=" * 55)
    return 0


if __name__ == "__main__":
    sys.exit(main())
