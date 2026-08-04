#!/usr/bin/env bash
# =============================================================================
#  run_mock.sh — Full VANET pipeline in MOCK mode (offline demo, no API key)
#
#  Produces:
#    output/vanet_summary_mock.html   — results dashboard
#    output/vanet_gui_mock.html       — animated GUI
#
#  Usage:
#    cd /home/kali/vanet_claude
#    bash scripts/run_mock.sh
# =============================================================================

set -e
cd "$(dirname "$0")/.."
echo ""
echo "============================================================"
echo "  VANET — MOCK MODE  (simulated jam, no Google API needed)"
echo "============================================================"
echo ""

# ── Clear previous output files (fresh run) ──────────────────────────────────
echo "[CLEAN] Clearing output files from previous run ..."
rm -f output/alerts.log output/jam_report.json output/reroute_log.json
echo "  Done."
echo ""

# ── PHASE 2 — Generate routes from mock traffic fixture ──────────────────────
echo "[PHASE 2] Generating vehicle routes from mock jam scenario ..."
python3 data_node/generate_routes.py --mock
echo ""

# ── PHASE 3a — SUMO simulation + bridge files ────────────────────────────────
echo "[PHASE 3a] Running SUMO via TraCI (mock jam injected) ..."
python3 sim/bridge/traci_supervisor.py --mock
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
echo "[PHASE 4b] Running live rerouter (SUMO + TraCI) ..."
python3 data_node/rerouter.py --mock
echo ""

# ── PHASE 4a — Offline jam detection (runs AFTER rerouter so it can merge) ───
echo "[PHASE 4a] Running offline jam detector ..."
python3 data_node/jam_detector.py
echo ""

# ── PHASE 5 — Visualisation ──────────────────────────────────────────────────
echo "[PHASE 5] Generating charts and HTML dashboard (MOCK) ..."
python3 scripts/visualise.py --mode mock
echo ""

echo "[PHASE 5] Generating animated GUI (MOCK) ..."
python3 scripts/gui_visualiser.py --mode mock
echo ""

echo "============================================================"
echo "  MOCK pipeline complete. Open these files in your browser:"
echo "  output/vanet_summary_mock.html   — Results dashboard"
echo "  output/vanet_gui_mock.html       — Animated GUI"
echo "============================================================"
echo ""
