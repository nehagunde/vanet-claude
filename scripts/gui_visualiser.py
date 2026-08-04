#!/usr/bin/env python3
"""
gui_visualiser.py — Phase 5: Animated VANET GUI.

Reads speed_log.json + rsu_static.json + jam/reroute data and produces
output/vanet_gui.html — a self-contained animated HTML5 Canvas page that
shows all 10 OBU vehicles moving along NH-16, RSU radio coverage, live
V2V/V2I communication links, jam detection alert, and rerouting events.

Usage:
    python3 scripts/gui_visualiser.py
    Then open:  output/vanet_gui.html  in any browser
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT     = Path(__file__).resolve().parent.parent
SPEED_LOG_JSON   = PROJECT_ROOT / "sim"    / "bridge" / "speed_log.json"
RSU_STATIC_JSON  = PROJECT_ROOT / "sim"    / "bridge" / "rsu_static.json"
JAM_REPORT_JSON  = PROJECT_ROOT / "output" / "jam_report.json"
REROUTE_LOG_JSON = PROJECT_ROOT / "output" / "reroute_log.json"
RSU_CSV          = PROJECT_ROOT / "corridor" / "rsu_positions.csv"
GUI_HTML_OUT     = PROJECT_ROOT / "output"  / "vanet_gui.html"


def load_json(p, default):
    if not Path(p).is_file():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_rsus_csv():
    import csv
    p = RSU_CSV
    if not p.is_file():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_compact_frames(speed_log: dict) -> dict:
    """
    Convert speed_log to compact frame dict:
    { t: { veh_id: [x, y, speed_kmh] } }
    Only keep integer-second steps 0..600.
    """
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


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "live"], default="mock",
                    help="mock = offline demo  |  live = Google Maps data")
    args = ap.parse_args()
    mode = args.mode

    gui_out = PROJECT_ROOT / "output" / f"vanet_gui_{mode}.html"

    print(f"Building GUI visualiser  [{mode.upper()} MODE] ...")

    speed_log   = load_json(SPEED_LOG_JSON,  {})
    rsu_static  = load_json(RSU_STATIC_JSON, [])
    jam_report  = load_json(JAM_REPORT_JSON, [])
    reroute_log = load_json(REROUTE_LOG_JSON,{"reroute_events": []})
    rsus_csv    = load_rsus_csv()

    if not speed_log:
        print("ERROR: speed_log.json not found. Run traci_supervisor.py first.")
        return 1

    frames = build_compact_frames(speed_log)

    # Build reroute set: {(t, veh_id)}
    reroute_set = {}
    for ev in reroute_log.get("reroute_events", []):
        reroute_set[f"{ev['t_s']}_{ev['veh_id']}"] = True

    # RSU info for JS
    rsu_info = []
    for r in rsus_csv:
        rsu_info.append({
            "id":   r["id"],
            "area": r["area"],
            "x":    float(r["x_m"]),
            "y":    float(r["y_m"]),
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

    frames_json    = json.dumps(frames,        separators=(",", ":"))
    rsu_info_json  = json.dumps(rsu_info,      separators=(",", ":"))
    jam_json       = json.dumps(jam_events_js, separators=(",", ":"))
    reroute_json   = json.dumps(reroute_set,   separators=(",", ":"))

    # Build dynamic alert banner text from actual jam data
    rsu_area_map = {r["id"]: r["area"] for r in rsus_csv}
    rsu_ids_ord  = [r["id"] for r in rsus_csv]
    if jam_report:
        nr = jam_report[0].get("nearest_rsu", "")
        nr_area = rsu_area_map.get(nr, nr)
        if nr in rsu_ids_ord:
            idx2     = rsu_ids_ord.index(nr)
            next_id  = rsu_ids_ord[idx2 + 1] if idx2 + 1 < len(rsu_ids_ord) else nr
            prev_id  = rsu_ids_ord[idx2 - 1] if idx2 > 0 else rsu_ids_ord[0]
        else:
            next_id  = nr
            prev_id  = nr
        nr2_area  = rsu_area_map.get(next_id, next_id)
        prev_area = rsu_area_map.get(prev_id, prev_id)
        jam_banner_seg     = f"{nr_area} to {nr2_area}"
        jam_banner_jam_loc = f"{nr_area} Junction"
        jam_banner_uturn   = f"{prev_area}"   # one RSU before jam — valid diversion point
    else:
        jam_banner_seg     = "congested segment"
        jam_banner_jam_loc = "ahead"
        jam_banner_uturn   = "nearest junction"

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
.main-area {{
  display: flex; flex: 1; overflow: hidden;
}}
#canvas-wrap {{
  flex: 1; position: relative; background: #0d1117;
  display: flex; flex-direction: column;
}}
canvas {{
  width: 100%; height: 100%;
}}
#alert-banner {{
  display: none; position: absolute; bottom: 10px; left: 10px;
  background: #1a0808; border: 2px solid #ff4444;
  color: #ff6b6b; padding: 14px 18px; border-radius: 10px;
  font-size: 0.92rem; text-align: center;
  animation: pulse 1.2s infinite alternate; z-index: 10;
  pointer-events: none; min-width: 420px; max-width: 500px;
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
.banner-box.sender {{
  background: #3d0e0e; border: 1px solid #ff5555;
}}
.banner-box.receiver {{
  background: #2e1a00; border: 1px solid #ff9944;
}}
.banner-box-title {{
  font-size: 0.72rem; font-weight: bold; letter-spacing: 1px;
  text-transform: uppercase; margin-bottom: 4px;
}}
.banner-box.sender .banner-box-title  {{ color: #ff7777; }}
.banner-box.receiver .banner-box-title {{ color: #ffaa55; }}
.banner-vehs {{
  font-size: 0.88rem; font-weight: bold;
}}
.banner-box.sender  .banner-vehs {{ color: #ffaaaa; }}
.banner-box.receiver .banner-vehs {{ color: #ffd090; }}
.banner-uturn {{
  font-size: 0.82rem; color: #ffcccc;
  border-top: 1px solid #ff444433; padding-top: 7px;
}}
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
.sidebar h3 {{
  font-size: 0.78rem; color: #8b949e; text-transform: uppercase;
  letter-spacing: 1px; border-bottom: 1px solid #21262d;
  padding-bottom: 6px; margin-bottom: 4px;
}}
.veh-list {{ display: flex; flex-direction: column; gap: 4px; }}
.veh-row {{
  display: flex; align-items: center; gap: 8px;
  font-size: 0.78rem; padding: 4px 8px; border-radius: 4px;
  background: #0d1117; border: 1px solid #21262d;
}}
.veh-dot {{
  width: 10px; height: 10px; border-radius: 50%;
  flex-shrink: 0;
}}
.veh-name {{ width: 50px; color: #e6edf3; font-family: monospace; }}
.veh-spd  {{ flex: 1; text-align: right; font-family: monospace; }}
.veh-bar  {{
  width: 100%; height: 3px; background: #21262d;
  border-radius: 2px; margin-top: 2px;
}}
.veh-bar-fill {{
  height: 100%; border-radius: 2px; transition: width 0.3s, background 0.3s;
}}
.legend {{ font-size: 0.75rem; }}
.leg-row {{
  display: flex; align-items: center; gap: 8px;
  padding: 3px 0; color: #8b949e;
}}
.leg-dot {{
  width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
}}
.leg-line {{
  width: 24px; height: 2px; flex-shrink: 0;
}}
.stats-grid {{
  display: grid; grid-template-columns: 1fr 1fr; gap: 6px;
}}
.stat-box {{
  background: #0d1117; border: 1px solid #21262d;
  border-radius: 6px; padding: 8px; text-align: center;
}}
.stat-val {{ font-size: 1.3rem; font-weight: bold; color: #00b4d8; }}
.stat-lbl {{ font-size: 0.65rem; color: #8b949e; }}
.stat-box.danger .stat-val {{ color: #ff4444; }}
.stat-box.ok     .stat-val {{ color: #3fb950; }}
.controls {{
  background: #161b22; border-top: 1px solid #30363d;
  padding: 10px 24px; display: flex; align-items: center; gap: 16px;
}}
.btn {{
  background: #21262d; border: 1px solid #30363d; color: #e6edf3;
  padding: 6px 16px; border-radius: 6px; cursor: pointer; font-size: 0.85rem;
}}
.btn:hover {{ background: #2d333b; border-color: #00b4d8; }}
.btn.active {{ background: #00b4d8; color: #0d1117; border-color: #00b4d8; }}
#speed-slider {{ flex: 1; accent-color: #00b4d8; }}
#progress-bar-wrap {{
  flex: 2; height: 6px; background: #21262d; border-radius: 3px;
  cursor: pointer; position: relative;
}}
#progress-bar {{
  height: 100%; background: #00b4d8; border-radius: 3px;
  width: 0%; transition: width 0.1s;
}}
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
    <p>10 OBU vehicles · 7 RSU nodes · IEEE 802.11p WAVE · 300 m radio range</p>
  </div>
  <div id="sim-time">T = 0 s</div>
</header>

<div class="main-area">

  <div id="canvas-wrap">
    <canvas id="cvs"></canvas>
    <div id="alert-banner">⚠️ JAM DETECTED — {jam_banner_seg}<br/>
      <small>Speed &lt; 5 km/h · U-turn recommended at {jam_banner_uturn}</small>
    </div>
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
          <div class="stat-lbl">Slow (&lt;5 km/h)</div>
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
      <div class="leg-row">
        <div class="leg-dot" style="background:#3fb950"></div> Fast vehicle (&gt;30 km/h)
      </div>
      <div class="leg-row">
        <div class="leg-dot" style="background:#ffbe0b"></div> Slow vehicle (5–30 km/h)
      </div>
      <div class="leg-row">
        <div class="leg-dot" style="background:#ff4444"></div> Jam vehicle (&lt;5 km/h)
      </div>
      <div class="leg-row">
        <div class="leg-dot" style="background:#00b4d8; border-radius:3px; width:16px; height:16px"></div> RSU (fixed node)
      </div>
      <div class="leg-row">
        <div class="leg-line" style="background:#00b4d888"></div> V2I comm link
      </div>
      <div class="leg-row">
        <div class="leg-line" style="background:#ffbe0b88"></div> V2V comm link
      </div>
      <div class="leg-row">
        <div class="leg-line" style="background:#ff444488; border-top: 2px dashed #ff4444"></div> JAM_ALERT link
      </div>
      <div class="leg-row">
        <div class="leg-line" style="background:#a050dc88; border-top: 2px dashed #a050dc"></div> RSU-to-RSU relay
      </div>
      <div class="leg-row">
        <div class="leg-dot" style="background:none; border:2px dashed #00b4d844; width:16px; height:16px; border-radius:50%"></div> 300m radio range
      </div>
      <div class="leg-row" style="margin-top:4px; font-size:0.7rem; color:#ff9999">
        📡 SENDER = vehicle in jam
      </div>
      <div class="leg-row" style="font-size:0.7rem; color:#ffcc88">
        📻 RECEIVER = approaching vehicle
      </div>
    </div>

    <div>
      <h3>Simulation Events</h3>
      <div id="event-log" style="font-size:0.72rem; color:#8b949e; max-height:180px; overflow-y:auto; font-family:monospace;">
        <div>Waiting for events...</div>
      </div>
    </div>
  </div>
</div>

<div class="controls">
  <button class="btn active" id="btn-play" onclick="togglePlay()">⏸ Pause</button>
  <button class="btn" onclick="resetSim()">↺ Reset</button>
  <span style="font-size:0.8rem; color:#8b949e; white-space:nowrap">Speed:</span>
  <input type="range" id="speed-slider" min="1" max="20" value="5"
         oninput="simSpeed=+this.value; document.getElementById('speed-lbl').textContent=this.value+'×'"/>
  <span id="speed-lbl" style="font-size:0.8rem; color:#00b4d8; min-width:28px">5×</span>
  <div id="progress-bar-wrap" onclick="seekTo(event)">
    <div id="progress-bar"></div>
  </div>
  <span style="font-size:0.8rem; color:#8b949e">600 s</span>
</div>

<script>
// ── Embedded simulation data ──────────────────────────────────────────────────
const FRAMES     = {frames_json};
const RSU_INFO   = {rsu_info_json};
const JAM_EVENTS = {jam_json};
const REROUTES   = {reroute_json};

const SIM_MAX    = 600;
const RADIO_M    = 300;  // 300m radio range

// ── Canvas setup ──────────────────────────────────────────────────────────────
const cvs = document.getElementById("cvs");
const ctx = cvs.getContext("2d");

function resize() {{
  cvs.width  = cvs.offsetWidth;
  cvs.height = cvs.offsetHeight;
}}
window.addEventListener("resize", () => {{ resize(); draw(); }});
resize();

// ── Coordinate mapping ────────────────────────────────────────────────────────
// SUMO Y (1847–7097) → canvas X (left→right, south→north)
// SUMO X (3245–5308) → canvas Y (small lateral offset, centred)
const SUMO_Y_MIN = 1847, SUMO_Y_MAX = 7100;
const SUMO_X_MIN = 3200, SUMO_X_MAX = 5400;

function toCanvas(sx, sy) {{
  const PAD = 80;
  const cx = PAD + (sy - SUMO_Y_MIN) / (SUMO_Y_MAX - SUMO_Y_MIN) * (cvs.width - PAD*2);
  // Lateral offset: slight curve
  const midX   = (SUMO_X_MIN + SUMO_X_MAX) / 2;
  const cy = cvs.height / 2 + (sx - midX) / (SUMO_X_MAX - SUMO_X_MIN) * (cvs.height * 0.55);
  return [cx, cy];
}}

// Scale 300m to canvas pixels
function metresToPx(m) {{
  return m / (SUMO_Y_MAX - SUMO_Y_MIN) * (cvs.width - 160);
}}

// ── Sim state ─────────────────────────────────────────────────────────────────
let simT      = 0;       // current simulation second
let playing   = true;
let simSpeed  = 5;       // simulation seconds per real second
let lastRaf   = null;
let accumMs   = 0;
let rerouted  = new Set();
let loggedEvents = [];

const BANNER_HOLD_MS = 30000;  // keep banners visible for 30 real seconds
let bannerShownAt  = -Infinity;
let rflashShownAt  = -Infinity;

const VEH_COLORS = [
  "#00b4d8","#90e0ef","#48cae4","#0096c7","#ade8f4",
  "#caf0f8","#023e8a","#0077b6","#03045e","#e0fbfc"
];

function vehColor(veh_id, speed) {{
  if (speed < 5)  return "#ff4444";
  if (speed < 30) return "#ffbe0b";
  return "#3fb950";
}}

// ── Pre-compute RSU canvas positions ─────────────────────────────────────────
function getRsuCanvas() {{
  return RSU_INFO.map(r => ({{ ...r, ...(() => {{
    const [cx, cy] = toCanvas(r.x, r.y);
    return {{ cx, cy }};
  }})() }}));
}}

// ── Draw one frame ────────────────────────────────────────────────────────────
function draw() {{
  const W = cvs.width, H = cvs.height;
  ctx.clearRect(0, 0, W, H);

  // Background gradient
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, "#0d1117");
  bg.addColorStop(1, "#161b22");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);

  const t    = Math.floor(simT);
  const step = FRAMES[t] || {{}};
  const rsus = getRsuCanvas();

  // ── Road background ───────────────────────────────────────────────────────
  ctx.beginPath();
  const road_pts = RSU_INFO.map(r => toCanvas(r.x, r.y));
  ctx.moveTo(road_pts[0][0], road_pts[0][1]);
  for (let i = 1; i < road_pts.length; i++) {{
    ctx.lineTo(road_pts[i][0], road_pts[i][1]);
  }}
  ctx.strokeStyle = "#30363d";
  ctx.lineWidth   = 24;
  ctx.lineCap     = "round";
  ctx.lineJoin    = "round";
  ctx.stroke();

  // Road surface
  ctx.beginPath();
  ctx.moveTo(road_pts[0][0], road_pts[0][1]);
  for (let i = 1; i < road_pts.length; i++) {{
    ctx.lineTo(road_pts[i][0], road_pts[i][1]);
  }}
  ctx.strokeStyle = "#444c56";
  ctx.lineWidth   = 16;
  ctx.stroke();

  // Centre dashes
  ctx.setLineDash([20, 14]);
  ctx.beginPath();
  ctx.moveTo(road_pts[0][0], road_pts[0][1]);
  for (let i = 1; i < road_pts.length; i++) {{
    ctx.lineTo(road_pts[i][0], road_pts[i][1]);
  }}
  ctx.strokeStyle = "#ffbe0b44";
  ctx.lineWidth   = 2;
  ctx.stroke();
  ctx.setLineDash([]);

  // JAM_EVENTS used only for road highlight timing
  const activeJam = JAM_EVENTS.find(j => t >= j.start && t <= j.end) || null;
  const isJam = activeJam !== null;  // road highlight still uses JAM_EVENTS

  // Jam segment highlight
  if (isJam && RSU_INFO.length >= 4) {{
    const [x1,y1] = toCanvas(RSU_INFO[2].x, RSU_INFO[2].y);
    const [x2,y2] = toCanvas(RSU_INFO[3].x, RSU_INFO[3].y);
    const grad = ctx.createLinearGradient(x1, y1, x2, y2);
    grad.addColorStop(0, "#ff444444");
    grad.addColorStop(0.5, "#ff444488");
    grad.addColorStop(1, "#ff444444");
    ctx.beginPath();
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    ctx.strokeStyle = grad;
    ctx.lineWidth   = 20;
    ctx.stroke();
  }}

  // ── RSU radio range circles ────────────────────────────────────────────────
  const rangePx = metresToPx(RADIO_M);
  rsus.forEach(r => {{
    ctx.beginPath();
    ctx.arc(r.cx, r.cy, rangePx, 0, Math.PI * 2);
    ctx.strokeStyle = "#00b4d822";
    ctx.lineWidth   = 1;
    ctx.setLineDash([6, 5]);
    ctx.stroke();
    ctx.setLineDash([]);
  }});

  // ── Collect vehicle positions ─────────────────────────────────────────────
  const vehPos = {{}};
  Object.entries(step).forEach(([vid, [sx, sy, spd]]) => {{
    const [cx, cy] = toCanvas(sx, sy);
    vehPos[vid]    = {{ cx, cy, sx, sy, spd }};
  }});

  // ── Communication links ───────────────────────────────────────────────────
  let linkCount = 0;
  const vehIds  = Object.keys(vehPos);

  // Dynamic jam detection: fires as soon as 2+ vehicles are < 5 km/h
  const slowVehs     = vehIds.filter(vid => vehPos[vid].spd < 5);
  const dynJamActive = slowVehs.length >= 2;
  const isJamActive  = isJam || dynJamActive;  // road highlight OR real-time detection

  // jamVehSet = slow vehicles right now (real-time, not from jam_report)
  const jamVehSet = new Set(isJamActive ? slowVehs : []);

  // jamRsuNode: use JAM_EVENTS RSU if available, else find RSU nearest to slow vehicle cluster
  let jamRsuNode = activeJam ? rsus.find(r => r.id === activeJam.rsu) : null;
  if (!jamRsuNode && dynJamActive && slowVehs.length > 0) {{
    const scx = slowVehs.reduce((s, v) => s + vehPos[v].sx, 0) / slowVehs.length;
    const scy = slowVehs.reduce((s, v) => s + vehPos[v].sy, 0) / slowVehs.length;
    jamRsuNode = rsus.reduce((best, r) =>
      Math.hypot(r.x - scx, r.y - scy) < Math.hypot(best.x - scx, best.y - scy) ? r : best
    );
  }}

  // Multi-hop relay: find RSU one step BEHIND jam RSU in corridor
  // Vehicles near that RSU are ~1 km before jam — enough room to take alternate route
  let alertRsuNode = null;
  if (jamRsuNode) {{
    const jamIdx = rsus.findIndex(r => r.id === jamRsuNode.id);
    alertRsuNode  = jamIdx > 0 ? rsus[jamIdx - 1] : jamRsuNode;
  }}

  // Approaching = vehicles near alertRsuNode (one RSU before jam), speed >= 5 km/h
  const approachingVehs = (isJamActive && alertRsuNode)
    ? vehIds.filter(vid => {{
        if (jamVehSet.has(vid)) return false;
        const v = vehPos[vid];
        const d = Math.hypot(v.sx - alertRsuNode.x, v.sy - alertRsuNode.y);
        return d < RADIO_M && v.spd >= 5;
      }})
    : [];

  // V2I links (normal beacons — cyan)
  rsus.forEach(r => {{
    vehIds.forEach(vid => {{
      const v = vehPos[vid];
      const d = Math.hypot(v.sx - r.x, v.sy - r.y);
      if (d < RADIO_M) {{
        const alpha = 0.15 + 0.35 * (1 - d / RADIO_M);
        ctx.beginPath();
        ctx.moveTo(r.cx, r.cy);
        ctx.lineTo(v.cx, v.cy);
        ctx.strokeStyle = `rgba(0,180,216,${{alpha}})`;
        ctx.lineWidth   = 1.2;
        ctx.stroke();
        linkCount++;
      }}
    }});
  }});

  // V2V links (normal beacons — amber)
  for (let i = 0; i < vehIds.length; i++) {{
    for (let j = i + 1; j < vehIds.length; j++) {{
      const a = vehPos[vehIds[i]], b = vehPos[vehIds[j]];
      const d = Math.hypot(a.sx - b.sx, a.sy - b.sy);
      if (d < RADIO_M) {{
        const alpha = 0.12 + 0.25 * (1 - d / RADIO_M);
        ctx.beginPath();
        ctx.moveTo(a.cx, a.cy);
        ctx.lineTo(b.cx, b.cy);
        ctx.strokeStyle = `rgba(255,190,11,${{alpha}})`;
        ctx.lineWidth   = 1;
        ctx.stroke();
        linkCount++;
      }}
    }}
  }}

  // ── Full relay chain: jam vehicles → jamRsuNode → alertRsuNode → approaching ─
  if (isJamActive && jamRsuNode && alertRsuNode && approachingVehs.length > 0) {{

    // Step 1: Jam vehicles → jamRsuNode (V2I notification, orange solid)
    jamVehSet.forEach(jvid => {{
      if (!vehPos[jvid]) return;
      const jv = vehPos[jvid];
      ctx.beginPath();
      ctx.moveTo(jv.cx, jv.cy);
      ctx.lineTo(jamRsuNode.cx, jamRsuNode.cy);
      ctx.strokeStyle = "rgba(255,140,0,0.5)";
      ctx.lineWidth   = 1.5;
      ctx.setLineDash([4, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
    }});

    // Step 2: jamRsuNode → alertRsuNode (RSU-to-RSU relay, purple dashed)
    ctx.beginPath();
    ctx.moveTo(jamRsuNode.cx, jamRsuNode.cy);
    ctx.lineTo(alertRsuNode.cx, alertRsuNode.cy);
    ctx.strokeStyle = "rgba(160,80,220,0.85)";
    ctx.lineWidth   = 2.5;
    ctx.setLineDash([10, 6]);
    ctx.stroke();
    ctx.setLineDash([]);
    // Arrowhead at alertRsuNode
    const relayAng = Math.atan2(alertRsuNode.cy - jamRsuNode.cy, alertRsuNode.cx - jamRsuNode.cx);
    ctx.beginPath();
    ctx.moveTo(alertRsuNode.cx, alertRsuNode.cy);
    ctx.lineTo(alertRsuNode.cx - 12*Math.cos(relayAng-0.4), alertRsuNode.cy - 12*Math.sin(relayAng-0.4));
    ctx.lineTo(alertRsuNode.cx - 12*Math.cos(relayAng+0.4), alertRsuNode.cy - 12*Math.sin(relayAng+0.4));
    ctx.closePath();
    ctx.fillStyle = "rgba(160,80,220,0.9)";
    ctx.fill();
    // RSU RELAY label
    const rmx = (jamRsuNode.cx + alertRsuNode.cx) / 2;
    const rmy = (jamRsuNode.cy + alertRsuNode.cy) / 2 - 10;
    ctx.fillStyle = "rgba(20,0,30,0.82)";
    ctx.beginPath();
    ctx.roundRect(rmx - 40, rmy - 14, 80, 16, 4);
    ctx.fill();
    ctx.fillStyle = "#cc88ff";
    ctx.font      = "bold 9px 'Segoe UI'";
    ctx.textAlign = "center";
    ctx.fillText("📡 RSU RELAY", rmx, rmy - 2);

    // Step 3: alertRsuNode → approaching vehicles (red dashed V2I alert)
    approachingVehs.forEach(avid => {{
      const av = vehPos[avid];
      ctx.beginPath();
      ctx.moveTo(alertRsuNode.cx, alertRsuNode.cy);
      ctx.lineTo(av.cx, av.cy);
      ctx.strokeStyle = "rgba(255,68,68,0.75)";
      ctx.lineWidth   = 2;
      ctx.setLineDash([8, 5]);
      ctx.stroke();
      ctx.setLineDash([]);
      // Arrowhead
      const ang = Math.atan2(av.cy - alertRsuNode.cy, av.cx - alertRsuNode.cx);
      ctx.beginPath();
      ctx.moveTo(av.cx, av.cy);
      ctx.lineTo(av.cx - 10*Math.cos(ang-0.4), av.cy - 10*Math.sin(ang-0.4));
      ctx.lineTo(av.cx - 10*Math.cos(ang+0.4), av.cy - 10*Math.sin(ang+0.4));
      ctx.closePath();
      ctx.fillStyle = "rgba(255,68,68,0.9)";
      ctx.fill();
      // JAM_ALERT label
      const mx = (alertRsuNode.cx + av.cx) / 2;
      const my = (alertRsuNode.cy + av.cy) / 2;
      ctx.fillStyle = "rgba(30,0,0,0.78)";
      ctx.beginPath();
      ctx.roundRect(mx - 38, my - 18, 76, 16, 4);
      ctx.fill();
      ctx.fillStyle = "#ff6666";
      ctx.font      = "bold 10px 'Segoe UI'";
      ctx.textAlign = "center";
      ctx.fillText("⚠ JAM_ALERT", mx, my - 6);
    }});
  }}

  // ── Draw RSU nodes ────────────────────────────────────────────────────────
  rsus.forEach(r => {{
    // Outer glow
    const grad = ctx.createRadialGradient(r.cx, r.cy, 0, r.cx, r.cy, 18);
    grad.addColorStop(0, "#00b4d866");
    grad.addColorStop(1, "#00b4d800");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(r.cx, r.cy, 18, 0, Math.PI * 2);
    ctx.fill();
    // Square icon
    ctx.fillStyle   = "#00b4d8";
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth   = 1.5;
    ctx.beginPath();
    const s = 9;
    ctx.rect(r.cx - s, r.cy - s, s * 2, s * 2);
    ctx.fill(); ctx.stroke();
    // Antenna
    ctx.beginPath();
    ctx.moveTo(r.cx, r.cy - s);
    ctx.lineTo(r.cx, r.cy - s - 8);
    ctx.strokeStyle = "#00b4d8";
    ctx.lineWidth = 2;
    ctx.stroke();
    // Label
    ctx.fillStyle   = "#e6edf3";
    ctx.font        = "bold 10px 'Segoe UI'";
    ctx.textAlign   = "center";
    ctx.fillText(r.id, r.cx, r.cy + s + 14);
    ctx.fillStyle   = "#8b949e";
    ctx.font        = "9px 'Segoe UI'";
    ctx.fillText(r.area, r.cx, r.cy + s + 25);
  }});

  // ── Draw vehicles ─────────────────────────────────────────────────────────
  let slowCount = 0;
  vehIds.forEach((vid, idx) => {{
    const v   = vehPos[vid];
    const col = vehColor(vid, v.spd);
    if (v.spd < 5) slowCount++;
    const key = `${{t}}_${{vid}}`;
    if (REROUTES[key]) rerouted.add(vid);
    const isRerouted = rerouted.has(vid);

    // Glow
    const grad = ctx.createRadialGradient(v.cx, v.cy, 0, v.cx, v.cy, 16);
    grad.addColorStop(0, col + "66");
    grad.addColorStop(1, col + "00");
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(v.cx, v.cy, 16, 0, Math.PI * 2);
    ctx.fill();

    // Vehicle circle
    ctx.beginPath();
    ctx.arc(v.cx, v.cy, 7, 0, Math.PI * 2);
    ctx.fillStyle   = col;
    ctx.strokeStyle = isRerouted ? "#3fb950" : "#ffffff";
    ctx.lineWidth   = isRerouted ? 2.5 : 1.5;
    ctx.fill(); ctx.stroke();

    // Reroute arrow
    if (isRerouted) {{
      ctx.fillStyle = "#3fb950";
      ctx.font = "bold 12px monospace";
      ctx.textAlign = "center";
      ctx.fillText("↗", v.cx + 9, v.cy - 7);
    }}

    // Vehicle label
    ctx.fillStyle = "#e6edf3";
    ctx.font      = "bold 9px 'Segoe UI'";
    ctx.textAlign = "center";
    ctx.fillText(vid.replace("veh_", "V"), v.cx, v.cy - 11);

    // Speed label
    ctx.fillStyle = col;
    ctx.font      = "9px monospace";
    ctx.fillText(v.spd.toFixed(0) + "k", v.cx, v.cy + 19);

    // Role badge (SENDER / RECEIVER) when jam is active
    if (isJamActive) {{
      if (jamVehSet.has(vid)) {{
        ctx.fillStyle = "rgba(180,20,20,0.85)";
        ctx.beginPath();
        ctx.roundRect(v.cx - 22, v.cy - 30, 44, 13, 3);
        ctx.fill();
        ctx.fillStyle = "#ffcccc";
        ctx.font = "bold 8px 'Segoe UI'";
        ctx.textAlign = "center";
        ctx.fillText("SENDER", v.cx, v.cy - 20);
      }} else if (approachingVehs.includes(vid)) {{
        ctx.fillStyle = "rgba(180,90,0,0.85)";
        ctx.beginPath();
        ctx.roundRect(v.cx - 26, v.cy - 30, 52, 13, 3);
        ctx.fill();
        ctx.fillStyle = "#ffe0b0";
        ctx.font = "bold 8px 'Segoe UI'";
        ctx.textAlign = "center";
        ctx.fillText("RECEIVER", v.cx, v.cy - 20);
      }}
    }}
  }});

  // ── Direction labels ──────────────────────────────────────────────────────
  ctx.fillStyle = "#484f58";
  ctx.font      = "bold 11px 'Segoe UI'";
  ctx.textAlign = "left";
  ctx.fillText("◀ Gajuwaka (South)", 8, H / 2 + 5);
  ctx.textAlign = "right";
  ctx.fillText("NAD Junction (North) ▶", W - 8, H / 2 + 5);

  // ── Update sidebar ────────────────────────────────────────────────────────
  document.getElementById("sv-active").textContent  = vehIds.length;
  document.getElementById("sv-links").textContent   = linkCount;
  document.getElementById("sv-slow").textContent    = slowCount;
  document.getElementById("sv-rerouted").textContent= rerouted.size;
  document.getElementById("sim-time").textContent   = `T = ${{t}} s`;
  document.getElementById("progress-bar").style.width = (t / SIM_MAX * 100) + "%";

  // Vehicle list
  const listEl = document.getElementById("veh-list");
  if (vehIds.length > 0) {{
    listEl.innerHTML = vehIds.map((vid, i) => {{
      const v   = vehPos[vid];
      const col = vehColor(vid, v.spd);
      const pct = Math.min(100, v.spd / 60 * 100);
      return `<div class="veh-row">
        <div class="veh-dot" style="background:${{col}}"></div>
        <span class="veh-name">${{vid.replace("veh_","V")}}</span>
        <div style="flex:1">
          <div class="veh-bar"><div class="veh-bar-fill"
            style="width:${{pct}}%;background:${{col}}"></div></div>
        </div>
        <span class="veh-spd" style="color:${{col}}">${{v.spd.toFixed(1)}} km/h</span>
      </div>`;
    }}).join("");
  }} else {{
    listEl.innerHTML = '<div style="color:#484f58;font-size:0.78rem;padding:8px">No active vehicles</div>';
  }}

  // Alert banner — show jam vehicles, hold for 30 real seconds
  const banner = document.getElementById("alert-banner");
  const rflash = document.getElementById("reroute-flash");
  const nowMs  = performance.now();

  if (isJamActive && approachingVehs.length > 0) {{
    const jamNames      = [...jamVehSet].map(v => v.replace("veh_","V")).join(", ");
    const approachNames = approachingVehs.map(v => v.replace("veh_","V")).join(", ");
    const rsuLabel      = jamRsuNode ? jamRsuNode.id : "rsu_02";
    const alertRsuLabel = alertRsuNode ? alertRsuNode.id : rsuLabel;
    banner.innerHTML =
      `<div class="banner-title">⚠️ JAM DETECTED — {jam_banner_seg}</div>` +
      `<div class="banner-grid">` +
        `<div class="banner-box sender">` +
          `<div class="banner-box-title">📡 Senders (In Jam)</div>` +
          `<div class="banner-vehs">${{jamNames || "—"}} &amp; ${{rsuLabel}}</div>` +
          `<div style="font-size:0.75rem;color:#cc88ff;margin-top:4px">📡 Relayed via ${{alertRsuLabel}}</div>` +
        `</div>` +
        `<div class="banner-box receiver">` +
          `<div class="banner-box-title">📻 Receivers (Approaching)</div>` +
          `<div class="banner-vehs">${{approachNames}}</div>` +
          `<div style="font-size:0.75rem;color:#ffaa55;margin-top:4px">Alert from ${{alertRsuLabel}}</div>` +
        `</div>` +
      `</div>` +
      `<div class="banner-uturn">↩ &nbsp;<b>${{approachNames}}</b> — Take alternate route at <b>{jam_banner_uturn}</b>, jam detected at <b>{jam_banner_jam_loc}</b></div>`;
    bannerShownAt = nowMs;
    banner.style.display = "block";
  }} else if (nowMs - bannerShownAt < BANNER_HOLD_MS) {{
    banner.style.display = "block";
  }} else {{
    banner.style.display = "none";
  }}

  // Reroute flash — only show jam vehicles (slow < 5 km/h) that got rerouted
  const reroutedNowIds = vehIds.filter(v => REROUTES[`${{t}}_${{v}}`]);
  // Rerouted = approaching vehicles that received the alert and turned around
  const reroutedJamIds = reroutedNowIds.filter(v =>
    approachingVehs.includes(v) || (!jamVehSet.has(v) && vehPos[v] && vehPos[v].spd >= 5)
  );
  if (reroutedJamIds.length > 0) {{
    const names = reroutedJamIds.map(v => v.replace("veh_","V")).join(", ");
    rflash.innerHTML = `✅ &nbsp;<b style="color:#7ee787">${{names}}</b> &nbsp;received JAM_ALERT → Rerouted successfully — Jam avoided`;
    rflashShownAt = nowMs;
    rflash.style.display = "block";
  }} else if (nowMs - rflashShownAt < BANNER_HOLD_MS) {{
    rflash.style.display = "block";  // hold visible
  }} else {{
    rflash.style.display = "none";
  }}

  // Event log
  JAM_EVENTS.forEach(j => {{
    const key = `jam_${{j.start}}`;
    if (t >= j.start && !window._logged?.[key]) {{
      window._logged = window._logged || {{}};
      window._logged[key] = true;
      addEvent(`⚠ t=${{j.start}}s JAM on ${{j.rsu}} avg ${{j.speed}} km/h`, "#ff4444");
    }}
  }});
  if (reroutedJamIds.length > 0) {{
    const rkey = `reroute_${{t}}`;
    if (!window._logged?.[rkey]) {{
      window._logged = window._logged || {{}};
      window._logged[rkey] = true;
      addEvent(`↗ t=${{t}}s vehicles rerouted`, "#3fb950");
    }}
  }}
}}

function addEvent(msg, color) {{
  const el = document.getElementById("event-log");
  const div = document.createElement("div");
  div.style.color = color;
  div.textContent = msg;
  if (el.children[0]?.textContent === "Waiting for events...") el.innerHTML = "";
  el.insertBefore(div, el.firstChild);
  while (el.children.length > 20) el.removeChild(el.lastChild);
}}

// ── Animation loop ────────────────────────────────────────────────────────────
function loop(ts) {{
  if (lastRaf === null) lastRaf = ts;
  const dtMs = ts - lastRaf;
  lastRaf    = ts;
  if (playing) {{
    accumMs += dtMs * simSpeed;
    while (accumMs >= 1000) {{
      simT    = Math.min(simT + 1, SIM_MAX);
      accumMs -= 1000;
    }}
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
  simT = 0; accumMs = 0; lastRaf = null;
  rerouted.clear();
  window._logged = {{}};
  bannerShownAt = -Infinity; rflashShownAt = -Infinity;
  document.getElementById("alert-banner").style.display = "none";
  document.getElementById("reroute-flash").style.display = "none";
  document.getElementById("event-log").innerHTML = "<div>Waiting for events...</div>";
  playing = true;
  document.getElementById("btn-play").textContent = "⏸ Pause";
  document.getElementById("btn-play").classList.add("active");
}}

function seekTo(e) {{
  const rect = e.currentTarget.getBoundingClientRect();
  const frac = (e.clientX - rect.left) / rect.width;
  simT = Math.round(frac * SIM_MAX);
  accumMs = 0;
  rerouted.clear();
  window._logged = {{}};
  bannerShownAt = -Infinity; rflashShownAt = -Infinity;
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
