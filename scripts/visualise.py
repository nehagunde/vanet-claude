#!/usr/bin/env python3
"""
visualise.py — Phase 5: Generate charts and HTML summary dashboard.

Reads all output files from Phases 3 & 4 and produces:

  output/screenshots/speed_chart.png  — vehicle speeds over time
  output/vanet_summary.html           — self-contained HTML dashboard

Usage:
    python3 scripts/visualise.py
"""

import base64
import csv
import json
import sys
from io import BytesIO
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT    = Path(__file__).resolve().parent.parent
SPEED_LOG_JSON  = PROJECT_ROOT / "sim"    / "bridge" / "speed_log.json"
RSU_STATIC_JSON = PROJECT_ROOT / "sim"    / "bridge" / "rsu_static.json"
JAM_REPORT_JSON = PROJECT_ROOT / "output" / "jam_report.json"
REROUTE_LOG_JSON= PROJECT_ROOT / "output" / "reroute_log.json"
ALERTS_LOG      = PROJECT_ROOT / "output" / "alerts.log"
RSU_CSV         = PROJECT_ROOT / "corridor" / "rsu_positions.csv"

OUT_DIR         = PROJECT_ROOT / "output" / "screenshots"
SPEED_CHART_PNG = OUT_DIR / "speed_chart.png"
HTML_OUT        = PROJECT_ROOT / "output" / "vanet_summary.html"


# ── Data loaders ──────────────────────────────────────────────────────────────

def load_json(path: Path, default):
    if not path.is_file():
        print(f"  WARNING: {path.name} not found, using default.")
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_rsus() -> list[dict]:
    if not RSU_CSV.is_file():
        return []
    with RSU_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_alerts_log() -> list[str]:
    if not ALERTS_LOG.is_file():
        return []
    with ALERTS_LOG.open(encoding="utf-8") as f:
        return [ln.rstrip() for ln in f if ln.strip() and not ln.startswith("#")]


# ── Speed chart ───────────────────────────────────────────────────────────────

def make_speed_chart(speed_log: dict, jam_report: list) -> bytes:
    """
    Returns PNG bytes of a matplotlib chart:
    - One line per vehicle (speed km/h vs simulation time)
    - Red shaded band for jam window(s)
    - Horizontal dashed line at 5 km/h threshold
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        print("  WARNING: matplotlib not installed — skipping speed chart.")
        print("           pip3 install matplotlib")
        return b""

    # Build per-vehicle time-series
    veh_data: dict[str, dict[int, float]] = {}
    for t_str, step in speed_log.items():
        t = int(t_str)
        for veh_id, info in step.items():
            if veh_id not in veh_data:
                veh_data[veh_id] = {}
            veh_data[veh_id][t] = info["speed_kmh"]

    fig, ax = plt.subplots(figsize=(14, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    colors = ["#00b4d8", "#90e0ef", "#0077b6", "#48cae4",
              "#ade8f4", "#caf0f8", "#03045e", "#023e8a", "#0096c7"]

    for idx, (veh_id, tdata) in enumerate(sorted(veh_data.items())):
        times  = sorted(tdata.keys())
        speeds = [tdata[t] for t in times]
        ax.plot(times, speeds, linewidth=1.2,
                color=colors[idx % len(colors)],
                label=veh_id, alpha=0.85)

    # Jam window shading
    for jam in jam_report:
        ax.axvspan(jam["start_s"], jam["end_s"],
                   alpha=0.25, color="#ff4444",
                   label=f"Jam: {jam['edge'][:12]}…")
        ax.annotate(
            f"  JAM DETECTED\n  t={jam['start_s']}s",
            xy=(jam["start_s"], 5),
            xytext=(jam["start_s"] + 5, 18),
            color="#ff6b6b", fontsize=8,
            arrowprops=dict(arrowstyle="->", color="#ff6b6b"),
        )

    # Threshold line
    ax.axhline(y=5.0, color="#ffbe0b", linewidth=1.5,
               linestyle="--", label="Jam threshold (5 km/h)")

    ax.set_xlabel("Simulation Time (s)", color="white", fontsize=11)
    ax.set_ylabel("Speed (km/h)", color="white", fontsize=11)
    ax.set_title("VANET — Vehicle Speeds on NH-16 Corridor\n"
                 "Gajuwaka → NAD Junction, Visakhapatnam",
                 color="white", fontsize=13, fontweight="bold")
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#444")
    ax.legend(loc="upper right", fontsize=7,
              facecolor="#0d1117", labelcolor="white",
              framealpha=0.8)
    ax.set_xlim(0, 600)
    ax.set_ylim(0, 80)

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format="png", dpi=150,
                facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


# ── HTML dashboard ────────────────────────────────────────────────────────────

def make_html(rsus: list[dict], jam_report: list, reroute_log: dict,
              alerts_lines: list[str], chart_b64: str,
              speed_log: dict, mode: str = "mock") -> str:

    # ── Stats ─────────────────────────────────────────────────────────────────
    n_vehicles = 0
    if speed_log:
        first_step = next(iter(speed_log.values()))
        n_vehicles = len(first_step)

    n_jams     = len(jam_report)
    n_rerouted = reroute_log.get("summary", {}).get("total_vehicles_rerouted", 0)
    n_alerts   = sum(1 for ln in alerts_lines if "JAM_ALERT" in ln or "REROUTE" in ln)

    jam_edge    = jam_report[0]["edge"][:20]    if jam_report else "—"
    jam_time    = jam_report[0]["start_s"]       if jam_report else "—"
    jam_speed   = jam_report[0]["avg_speed_kmh"] if jam_report else "—"
    jam_dur     = jam_report[0]["duration_s"]    if jam_report else "—"
    jam_rsu     = jam_report[0].get("nearest_rsu","—") if jam_report else "—"
    # Build human-readable segment label from RSU area names
    jam_alert_msg = (jam_report[0].get("alert_message", "")
                     if jam_report else "")
    rsu_area_map  = {r["id"]: r["area"] for r in rsus}
    jam_rsu_area  = rsu_area_map.get(jam_rsu, jam_rsu)
    # Adjacent RSU (next in corridor order)
    rsu_ids = [r["id"] for r in rsus]
    if jam_rsu in rsu_ids:
        _idx = rsu_ids.index(jam_rsu)
        _next_id = rsu_ids[_idx + 1] if _idx + 1 < len(rsu_ids) else jam_rsu
    else:
        _next_id = jam_rsu
    jam_rsu2_area = rsu_area_map.get(_next_id, _next_id)
    jam_seg_label = (f"{jam_rsu_area}–{jam_rsu2_area}"
                     if jam_report else "—")

    # ── RSU table rows ────────────────────────────────────────────────────────
    rsu_rows = ""
    for r in rsus:
        rsu_rows += f"""
        <tr>
          <td>{r['id']}</td>
          <td>{r['area']}</td>
          <td>{r['infra_type']}</td>
          <td>{float(r['lat']):.6f}</td>
          <td>{float(r['lon']):.6f}</td>
          <td>{float(r['x_m']):.1f}</td>
          <td>{float(r['y_m']):.1f}</td>
        </tr>"""

    # ── Jam event rows ────────────────────────────────────────────────────────
    jam_rows = ""
    for jam in jam_report:
        veh_list = ", ".join(jam["vehicles"])
        jam_rows += f"""
        <tr>
          <td>{jam['start_s']} s</td>
          <td>{jam['end_s']} s</td>
          <td>{jam['duration_s']} s</td>
          <td><code>{jam['edge']}</code></td>
          <td>{jam.get('nearest_rsu','—')}</td>
          <td>{jam['avg_speed_kmh']} km/h</td>
          <td>{veh_list}</td>
        </tr>"""
    if not jam_rows:
        jam_rows = "<tr><td colspan='7'>No jams detected</td></tr>"

    # ── Reroute event rows ────────────────────────────────────────────────────
    reroute_rows = ""
    for ev in reroute_log.get("reroute_events", [])[:20]:
        reroute_rows += f"""
        <tr>
          <td>{ev['t_s']} s</td>
          <td>{ev['veh_id']}</td>
          <td><code>{ev['jam_edge']}</code></td>
          <td>{ev['action']}</td>
        </tr>"""
    if not reroute_rows:
        reroute_rows = "<tr><td colspan='4'>No rerouting events</td></tr>"

    # ── Alerts log excerpt ────────────────────────────────────────────────────
    alert_html = ""
    shown = [ln for ln in alerts_lines
             if "JAM_ALERT" in ln or "JAM_DETECTED" in ln or "REROUTE" in ln]
    for ln in shown[:30]:
        css = "alert-jam" if "JAM_ALERT" in ln else \
              "alert-det" if "JAM_DETECTED" in ln else "alert-reroute"
        alert_html += f'<div class="alert-line {css}">{ln}</div>\n'
    if not alert_html:
        alert_html = '<div class="alert-line">No alert events in log</div>'

    # ── SVG corridor map ──────────────────────────────────────────────────────
    # Simple horizontal strip showing RSU positions + jam segment
    def rsu_svg_x(rsu_dict):
        # Map y_m (3846 to 7097) → SVG x (60 to 940)
        y = float(rsu_dict["y_m"])
        return round(60 + (y - 1846) / (7097 - 1846) * 880, 1)

    rsu_svg = ""
    for r in rsus:
        sx = rsu_svg_x(r)
        color = "#ff4444" if r["id"] in (jam_rsu,) else "#00b4d8"
        rsu_svg += f'''
      <circle cx="{sx}" cy="60" r="10" fill="{color}" stroke="white" stroke-width="2"/>
      <text x="{sx}" y="85" text-anchor="middle"
            fill="white" font-size="10">{r['id']}</text>
      <text x="{sx}" y="97" text-anchor="middle"
            fill="#aaa" font-size="9">{r['area']}</text>'''

    # Derive jam segment RSU IDs from actual jam_report data
    jam_from_rsu = ""
    jam_to_rsu   = ""
    if jam_report:
        # nearest_rsu is e.g. "rsu_02"; find adjacent RSU for the to-end
        nr = jam_report[0].get("nearest_rsu", "")
        rsu_ids = [r["id"] for r in rsus]
        if nr in rsu_ids:
            idx = rsu_ids.index(nr)
            jam_from_rsu = nr
            jam_to_rsu   = rsu_ids[idx + 1] if idx + 1 < len(rsu_ids) else nr

    jam_svg = ""
    if jam_report and rsus:
        rsu_dict = {r["id"]: r for r in rsus}
        fr = rsu_dict.get(jam_from_rsu)
        to = rsu_dict.get(jam_to_rsu)
        if fr and to:
            x1 = rsu_svg_x(fr)
            x2 = rsu_svg_x(to)
            jam_svg = f'''
      <rect x="{x1}" y="50" width="{x2-x1}" height="20"
            fill="#ff4444" opacity="0.35" rx="4"/>
      <text x="{(x1+x2)/2}" y="44" text-anchor="middle"
            fill="#ff6b6b" font-size="10" font-weight="bold">⚠ JAM</text>'''

    chart_img = (f'<img src="data:image/png;base64,{chart_b64}" '
                 f'style="width:100%;border-radius:8px;" alt="Speed chart"/>'
                 if chart_b64 else
                 '<p style="color:#aaa">Speed chart not available '
                 '(install matplotlib)</p>')

    # ── Full HTML ─────────────────────────────────────────────────────────────
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>VANET Traffic Jam Detection — Visakhapatnam NH-16</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', sans-serif;
    background: #0d1117; color: #e6edf3; min-height: 100vh;
  }}
  header {{
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
    padding: 30px 40px;
    border-bottom: 2px solid #00b4d8;
  }}
  header h1 {{ font-size: 1.8rem; color: #00b4d8; }}
  header p  {{ color: #8b949e; margin-top: 6px; font-size: 0.95rem; }}
  .container {{ max-width: 1300px; margin: 0 auto; padding: 30px 20px; }}
  .kpi-grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px; margin-bottom: 30px;
  }}
  .kpi {{
    background: #161b22; border: 1px solid #30363d;
    border-radius: 10px; padding: 20px; text-align: center;
  }}
  .kpi .val {{ font-size: 2.2rem; font-weight: bold; color: #00b4d8; }}
  .kpi .lbl {{ font-size: 0.8rem; color: #8b949e; margin-top: 4px; }}
  .kpi.danger .val {{ color: #ff4444; }}
  .kpi.warn   .val {{ color: #ffbe0b; }}
  .kpi.ok     .val {{ color: #3fb950; }}
  .card {{
    background: #161b22; border: 1px solid #30363d;
    border-radius: 10px; padding: 24px; margin-bottom: 24px;
  }}
  .card h2 {{
    font-size: 1.1rem; color: #00b4d8;
    border-bottom: 1px solid #21262d; padding-bottom: 10px; margin-bottom: 16px;
  }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ background: #21262d; color: #8b949e;
        padding: 8px 12px; text-align: left; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
  tr:hover td {{ background: #1c2128; }}
  code {{ background: #21262d; padding: 2px 6px; border-radius: 4px;
          font-size: 0.8rem; color: #79c0ff; }}
  .alert-box {{
    background: #0d1117; border: 1px solid #21262d;
    border-radius: 6px; padding: 12px; max-height: 300px;
    overflow-y: auto; font-family: monospace; font-size: 0.78rem;
  }}
  .alert-line   {{ padding: 2px 0; color: #8b949e; }}
  .alert-jam    {{ color: #ff4444; font-weight: bold; }}
  .alert-det    {{ color: #ffbe0b; }}
  .alert-reroute{{ color: #3fb950; }}
  .map-box {{
    background: #0d1117; border: 1px solid #21262d;
    border-radius: 8px; padding: 10px; overflow-x: auto;
  }}
  svg text {{ font-family: 'Segoe UI', sans-serif; }}
  .badge {{
    display: inline-block; padding: 3px 10px; border-radius: 20px;
    font-size: 0.75rem; font-weight: bold; margin-left: 8px;
  }}
  .badge-jam    {{ background: #3d1a1a; color: #ff4444; border: 1px solid #ff4444; }}
  .badge-ok     {{ background: #1a3d1a; color: #3fb950; border: 1px solid #3fb950; }}
  footer {{
    text-align: center; color: #484f58; font-size: 0.8rem;
    padding: 20px; border-top: 1px solid #21262d; margin-top: 20px;
  }}
</style>
</head>
<body>
<header>
  <h1>🚦 VANET Traffic Jam Detection & Alert System
    <span style="font-size:0.65rem;padding:3px 10px;border-radius:20px;margin-left:12px;
      background:{'#1a3d1a' if mode=='live' else '#3d2a00'};
      color:{'#3fb950' if mode=='live' else '#ffbe0b'};
      border:1px solid {'#3fb950' if mode=='live' else '#ffbe0b'}">
      {'🟢 LIVE — Google Maps Data' if mode=='live' else '🟡 MOCK — Simulated Jam'}
    </span>
  </h1>
  <p>NH-16 Corridor · Gajuwaka → NAD Junction, Visakhapatnam, Andhra Pradesh</p>
  <p style="margin-top:8px; font-size:0.85rem; color:#00b4d8;">
    SUMO (Traffic Simulation) + NS-3 (802.11p WAVE) + Python Jam Detector
    {'&nbsp;·&nbsp;<b style=color:#3fb950>Data source: Real Google Maps API</b>' if mode=='live'
     else '&nbsp;·&nbsp;<b style=color:#ffbe0b>Data source: Mock fixture (offline demo)</b>'}
  </p>
</header>

<div class="container">

  <!-- KPI Cards -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="val">{len(rsus)}</div>
      <div class="lbl">RSU Nodes (Fixed)</div>
    </div>
    <div class="kpi">
      <div class="val">{n_vehicles}</div>
      <div class="lbl">OBU Vehicles (Mobile)</div>
    </div>
    <div class="kpi danger">
      <div class="val">{n_jams}</div>
      <div class="lbl">Jam Events Detected</div>
    </div>
    <div class="kpi ok">
      <div class="val">{n_rerouted}</div>
      <div class="lbl">Vehicles Rerouted</div>
    </div>
  </div>

  <!-- Corridor Map -->
  <div class="card">
    <h2>📍 NH-16 Corridor — RSU Deployment Map</h2>
    <div class="map-box">
      <svg width="1000" height="110" xmlns="http://www.w3.org/2000/svg">
        <!-- Road line -->
        <line x1="50" y1="60" x2="960" y2="60"
              stroke="#30363d" stroke-width="8" stroke-linecap="round"/>
        <line x1="50" y1="60" x2="960" y2="60"
              stroke="#444c56" stroke-width="4" stroke-dasharray="20,10"/>
        <!-- Jam highlight -->
        {jam_svg}
        <!-- RSU markers -->
        {rsu_svg}
        <!-- Direction labels -->
        <text x="30" y="63" fill="#484f58" font-size="11">▶</text>
        <text x="10" y="75" fill="#484f58" font-size="9">South</text>
        <text x="945" y="75" fill="#484f58" font-size="9">North</text>
      </svg>
    </div>
    <p style="color:#8b949e; font-size:0.8rem; margin-top:8px;">
      ● Blue = RSU &nbsp;|&nbsp;
      <span style="color:#ff4444">● Red</span> = Nearest RSU to detected jam &nbsp;|&nbsp;
      <span style="color:#ff4444">■ Red band</span> = Jam segment ({jam_seg_label})
    </p>
  </div>

  <!-- Speed Chart -->
  <div class="card">
    <h2>📈 Vehicle Speed Timeline (600 s Simulation)</h2>
    {chart_img}
    <p style="color:#8b949e; font-size:0.8rem; margin-top:8px;">
      Yellow dashed line = 5 km/h jam threshold &nbsp;|&nbsp;
      Red band = detected jam window
    </p>
  </div>

  <!-- Jam Detection Results -->
  <div class="card">
    <h2>⚠️ Jam Detection Results
      <span class="badge {'badge-jam' if n_jams > 0 else 'badge-ok'}">
        {'JAM DETECTED' if n_jams > 0 else 'NO JAM'}
      </span>
    </h2>
    {'<div style="background:#3d1a1a;border:1px solid #ff4444;border-radius:8px;padding:16px;margin-bottom:16px;">'
     f'<b style="color:#ff4444">⚠ Jam confirmed at t={jam_time}s</b><br/>'
     f'Edge: <code>{jam_edge}</code> &nbsp;|&nbsp; Near: {jam_rsu} &nbsp;|&nbsp; '
     f'Avg speed: {jam_speed} km/h &nbsp;|&nbsp; Duration: {jam_dur}s<br/>'
     f'<b style="color:#ffbe0b">Alert:</b> {jam_alert_msg or f"Traffic jam on {jam_seg_label} segment — take U-turn at nearest junction"}'
     '</div>' if jam_report else ''}
    <table>
      <thead>
        <tr>
          <th>Start (s)</th><th>End (s)</th><th>Duration</th>
          <th>Edge</th><th>Nearest RSU</th>
          <th>Avg Speed</th><th>Vehicles</th>
        </tr>
      </thead>
      <tbody>{jam_rows}</tbody>
    </table>
  </div>

  <!-- Rerouting Events -->
  <div class="card">
    <h2>🔀 U-Turn Rerouting Events
      <span class="badge badge-ok">{n_rerouted} vehicles diverted</span>
    </h2>
    <table>
      <thead>
        <tr>
          <th>Time (s)</th><th>Vehicle</th>
          <th>Avoided Edge</th><th>Action</th>
        </tr>
      </thead>
      <tbody>{reroute_rows}</tbody>
    </table>
  </div>

  <!-- RSU Deployment Table -->
  <div class="card">
    <h2>📡 RSU Deployment Table (7 Units on NH-16)</h2>
    <table>
      <thead>
        <tr>
          <th>RSU ID</th><th>Area</th><th>Infra Type</th>
          <th>Latitude</th><th>Longitude</th>
          <th>SUMO X (m)</th><th>SUMO Y (m)</th>
        </tr>
      </thead>
      <tbody>{rsu_rows}</tbody>
    </table>
  </div>

  <!-- Alerts Log -->
  <div class="card">
    <h2>📋 Alerts Log Excerpt (JAM / REROUTE events)</h2>
    <div class="alert-box">
      {alert_html}
    </div>
    <p style="color:#8b949e; font-size:0.8rem; margin-top:8px;">
      <span style="color:#ff4444">■</span> JAM_ALERT &nbsp;|&nbsp;
      <span style="color:#ffbe0b">■</span> JAM_DETECTED &nbsp;|&nbsp;
      <span style="color:#3fb950">■</span> REROUTE
    </p>
  </div>

  <!-- System Architecture -->
  <div class="card">
    <h2>🏗️ System Architecture</h2>
    <table>
      <thead>
        <tr><th>Phase</th><th>Component</th><th>Role</th><th>Output</th></tr>
      </thead>
      <tbody>
        <tr><td>1</td><td>place_rsus.py</td><td>RSU placement on NH-16</td><td>rsu_positions.csv, corridor.net.xml</td></tr>
        <tr><td>2</td><td>generate_routes.py</td><td>Vehicle routes from traffic data</td><td>routes.rou.xml</td></tr>
        <tr><td>3a</td><td>traci_supervisor.py</td><td>SUMO↔NS-3 bridge via TraCI</td><td>mobility.ns2, speed_log.json</td></tr>
        <tr><td>3b</td><td>vanet-scenario.cc</td><td>802.11p WAVE radio simulation</td><td>alerts.log (beacons)</td></tr>
        <tr><td>4a</td><td>jam_detector.py</td><td>Offline jam detection</td><td>jam_report.json</td></tr>
        <tr><td>4b</td><td>rerouter.py</td><td>Live TraCI U-turn rerouting</td><td>reroute_log.json</td></tr>
        <tr><td>5</td><td>visualise.py</td><td>Charts + HTML dashboard</td><td>vanet_summary.html ← you are here</td></tr>
      </tbody>
    </table>
  </div>

</div>
<footer>
  VANET Prototype · SUMO 1.25 + NS-3 (802.11p) + Python · NH-16 Visakhapatnam
</footer>
</body>
</html>
"""


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["mock", "live"], default="mock",
                    help="mock = offline demo  |  live = Google Maps data")
    args = ap.parse_args()
    mode = args.mode

    suffix    = f"_{mode}"
    chart_png = OUT_DIR / f"speed_chart{suffix}.png"
    html_out  = PROJECT_ROOT / "output" / f"vanet_summary{suffix}.html"

    print(f"Phase 5 — Visualisation  [{mode.upper()} MODE]")
    print("=" * 55)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading output files ...")
    speed_log    = load_json(SPEED_LOG_JSON,  {})
    jam_report   = load_json(JAM_REPORT_JSON, [])
    reroute_log  = load_json(REROUTE_LOG_JSON,{"reroute_events":[],"summary":{}})
    alerts_lines = load_alerts_log()
    rsus         = load_rsus()

    print(f"  speed_log   : {len(speed_log)} time steps")
    print(f"  jam_report  : {len(jam_report)} jam event(s)")
    print(f"  reroute_log : {len(reroute_log.get('reroute_events',[]))} reroute event(s)")
    print(f"  alerts_log  : {len(alerts_lines)} lines")
    print(f"  RSUs        : {len(rsus)}")

    print("\nGenerating speed chart ...")
    chart_bytes = make_speed_chart(speed_log, jam_report)
    if chart_bytes:
        chart_png.write_bytes(chart_bytes)
        print(f"  Speed chart → {chart_png}")
    chart_b64 = base64.b64encode(chart_bytes).decode() if chart_bytes else ""

    print("Generating HTML dashboard ...")
    html = make_html(rsus, jam_report, reroute_log,
                     alerts_lines, chart_b64, speed_log, mode)
    html_out.write_text(html, encoding="utf-8")
    print(f"  Dashboard   → {html_out}")

    print()
    print("=" * 55)
    print(f"  Phase 5 [{mode.upper()}] complete.")
    print(f"  Open: output/vanet_summary_{mode}.html")
    print("=" * 55)
    return 0


if __name__ == "__main__":
    sys.exit(main())
