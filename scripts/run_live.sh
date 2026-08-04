#!/usr/bin/env bash
# =============================================================================
#  run_live.sh — Full VANET pipeline in LIVE mode (real Google Maps data)
#
#  Requires:  GOOGLE_MAPS_API_KEY set in /home/kali/vanet_claude/.env
#
#  Produces:
#    output/vanet_summary_live.html   — results dashboard
#    output/vanet_gui_live.html       — animated GUI
#
#  Usage:
#    cd /home/kali/vanet_claude
#    bash scripts/run_live.sh
# =============================================================================

set -e
cd "$(dirname "$0")/.."
echo ""
echo "============================================================"
echo "  VANET — LIVE MODE  (real Google Maps traffic on NH-16)"
echo "============================================================"
echo ""

# ── Clear previous output files (fresh run) ──────────────────────────────────
echo "[CLEAN] Clearing output files from previous run ..."
rm -f output/alerts.log output/jam_report.json output/reroute_log.json
echo "  Done."
echo ""

# ── PHASE 2a — Fetch live traffic from Google Maps API ───────────────────────
echo "[PHASE 2a] Fetching live traffic from Google Maps API ..."
echo "           Querying 6 segments on NH-16 Visakhapatnam ..."
python3 scripts/fetch_traffic.py
echo ""

# ── PHASE 2b — Generate routes from live congestion data ─────────────────────
echo "[PHASE 2b] Generating vehicle routes from live traffic data ..."
python3 data_node/generate_routes.py
echo ""

# ── PHASE 3a — SUMO simulation + bridge files ────────────────────────────────
echo "[PHASE 3a] Running SUMO via TraCI (live jam zone from Maps data) ..."
python3 sim/bridge/traci_supervisor.py --inject-jam
echo ""

# ── PHASE 3b — NS-3 802.11p radio simulation ─────────────────────────────────
echo "[PHASE 3b] Running NS-3 802.11p WAVE simulation ..."
cd /home/kali/ns-3-dev
./ns3 run "vanet-scenario \
  --mobilityFile=/home/kali/vanet_claude/sim/bridge/mobility.ns2 \
  --rsuFile=/home/kali/vanet_claude/sim/bridge/rsu_static.json \
  --logFile=/home/kali/vanet_claude/output/alerts.log" 2>&1 | tail -5
cd /home/kali/vanet_claude
echo ""

# ── PHASE 4b — Live rerouting demo ───────────────────────────────────────────
echo "[PHASE 4b] Running live rerouter (SUMO + TraCI, live jam zone) ..."
python3 data_node/rerouter.py
echo ""

# ── PHASE 4a — Offline jam detector (runs AFTER rerouter so it can merge) ────
echo "[PHASE 4a] Running offline jam detector ..."
python3 data_node/jam_detector.py
echo ""

# ── PHASE 5 — Visualisation ──────────────────────────────────────────────────
echo "[PHASE 5] Generating charts and HTML dashboard (LIVE) ..."
python3 scripts/visualise.py --mode live
echo ""

echo "[PHASE 5] Generating animated GUI (LIVE) ..."
python3 scripts/gui_visualiser.py --mode live
echo ""

echo "============================================================"
echo "  LIVE pipeline complete. Open these files in your browser:"
echo "  output/vanet_summary_live.html   — Results dashboard"
echo "  output/vanet_gui_live.html       — Animated GUI"
echo "============================================================"
echo ""
