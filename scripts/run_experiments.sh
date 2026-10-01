#!/usr/bin/env bash
# =============================================================================
#  run_experiments.sh — Phase 5: three-scenario × five-seed experiment sweep
#
#  Scenarios
#    NO_JAM        — no jam injection, no NS-3, no rerouting (free-flow baseline)
#    JAM_NO_ALERT  — jam injected, no rerouting (worst case)
#    JAM_WITH_ALERT— jam + NS-3 detection + backhaul relay + rerouting
#
#  Seeds: 1 2 3 4 5  (SUMO --seed controls departPos / car-following noise)
#
#  Outputs (all under output/v2/experiments/):
#    NO_JAM/seed_N/tripinfo.xml
#    JAM_NO_ALERT/seed_N/tripinfo.xml
#    JAM_WITH_ALERT/seed_N/tripinfo.xml
#    JAM_WITH_ALERT/seed_N/alerts.log
#    JAM_WITH_ALERT/seed_N/reroute_log.json
#
#  Usage (on Kali Linux):
#    cd /home/kali/vanet_claude
#    bash scripts/run_experiments.sh
#
#  Optional flags:
#    --skip-ns3    skip NS-3 run (reuse existing alerts.log for each seed)
#    --seeds "1 2" run only those seeds (for quick testing)
#    --gui         launch SUMO-GUI for each run (very slow, for debug only)
# =============================================================================

set -euo pipefail
cd "$(dirname "$0")/.."

PRJ="$(pwd)"
EXP_OUT="$PRJ/output/v2/experiments"
NS3_ROOT="/home/kali/ns-3-dev"
SEEDS="1 2 3 4 5"
SKIP_NS3=0
GUI_FLAG=""
TRACI_PORT_BASE=8820   # base port; each scenario uses base+offset to avoid clashes

# ── Argument parsing ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-ns3)  SKIP_NS3=1 ;;
    --seeds)     SEEDS="$2"; shift ;;
    --gui)       GUI_FLAG="--gui" ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

echo ""
echo "============================================================"
echo "  Phase 5 — Experiment sweep"
echo "  Seeds    : $SEEDS"
echo "  Skip NS-3: $SKIP_NS3"
echo "============================================================"
echo ""

mkdir -p "$EXP_OUT"

# ── Helper: wait for SUMO to release port ────────────────────────────────────
wait_port_free() {
  local port=$1
  for i in $(seq 1 20); do
    if ! ss -tlnp 2>/dev/null | grep -q ":${port} "; then
      return 0
    fi
    sleep 0.5
  done
  echo "[WARN] Port $port still in use after 10s" >&2
}

# =============================================================================
for seed in $SEEDS; do
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  SEED $seed"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # ── (a) NO_JAM — plain SUMO, no TraCI ─────────────────────────────────────
  echo ""
  echo "[NO_JAM seed=$seed] Running free-flow SUMO ..."
  NOJAM_DIR="$EXP_OUT/NO_JAM/seed_$seed"
  mkdir -p "$NOJAM_DIR"

  sumo \
    -c "$PRJ/sim/sumo/vanet.sumocfg" \
    --seed "$seed" \
    --tripinfo-output "$NOJAM_DIR/tripinfo.xml" \
    --no-step-log \
    2>&1 | grep -v "^$" | tail -4

  echo "  → $NOJAM_DIR/tripinfo.xml"

  # ── (b) JAM_NO_ALERT — jam injected, no rerouting ─────────────────────────
  echo ""
  echo "[JAM_NO_ALERT seed=$seed] Running SUMO with jam (no rerouting) ..."
  JAMNO_DIR="$EXP_OUT/JAM_NO_ALERT/seed_$seed"
  mkdir -p "$JAMNO_DIR"

  PORT_B=$((TRACI_PORT_BASE + 10))
  wait_port_free $PORT_B

  python3 "$PRJ/sim/bridge/traci_supervisor.py" \
    --mock \
    --seed "$seed" \
    --tripinfo "$JAMNO_DIR/tripinfo.xml" \
    --port $PORT_B \
    $GUI_FLAG \
    2>&1 | grep -v "^$" | tail -6

  echo "  → $JAMNO_DIR/tripinfo.xml"

  # ── (c) JAM_WITH_ALERT — full pipeline ────────────────────────────────────
  echo ""
  echo "[JAM_WITH_ALERT seed=$seed] Step 1 — SUMO + jam (mobility trace) ..."
  JALERT_DIR="$EXP_OUT/JAM_WITH_ALERT/seed_$seed"
  mkdir -p "$JALERT_DIR"

  PORT_C=$((TRACI_PORT_BASE + 20))
  wait_port_free $PORT_C

  # Step 1: SUMO run — injects jam, writes mobility.ns2 (NO tripinfo here;
  #         tripinfo comes from the rerouter's SUMO run which includes rerouting)
  python3 "$PRJ/sim/bridge/traci_supervisor.py" \
    --mock \
    --seed "$seed" \
    --port $PORT_C \
    $GUI_FLAG \
    2>&1 | grep -v "^$" | tail -6

  # Step 2: NS-3 802.11p simulation
  if [[ $SKIP_NS3 -eq 0 ]]; then
    echo "[JAM_WITH_ALERT seed=$seed] Step 2 — NS-3 802.11p simulation ..."
    cd "$NS3_ROOT"
    ./ns3 run "vanet/vanet-scenario \
      --mobilityFile=$PRJ/sim/bridge/mobility.ns2 \
      --rsuFile=$PRJ/sim/bridge/rsu_static.json \
      --logFile=$JALERT_DIR/alerts.log" \
      2>&1 | grep -v "^$" | tail -4
    cd "$PRJ"
  else
    echo "[JAM_WITH_ALERT seed=$seed] Step 2 — SKIPPING NS-3 (--skip-ns3 set)"
    # Reuse alerts log from the baseline run if it exists
    if [[ ! -f "$JALERT_DIR/alerts.log" ]]; then
      if [[ -f "$PRJ/output/v2/alerts.log" ]]; then
        cp "$PRJ/output/v2/alerts.log" "$JALERT_DIR/alerts.log"
        echo "  Copied baseline output/v2/alerts.log → $JALERT_DIR/alerts.log"
      else
        echo "  ERROR: No alerts.log found. Run without --skip-ns3 at least once." >&2
        exit 1
      fi
    fi
  fi
  echo "  → $JALERT_DIR/alerts.log"

  # Step 3: Rerouter — runs its own SUMO with same seed + jam + rerouting
  echo "[JAM_WITH_ALERT seed=$seed] Step 3 — Rerouter (SUMO + TraCI + rerouting) ..."
  PORT_D=$((TRACI_PORT_BASE + 30))
  wait_port_free $PORT_D

  python3 "$PRJ/data_node/rerouter_v2.py" \
    --seed "$seed" \
    --alerts-log "$JALERT_DIR/alerts.log" \
    --tripinfo "$JALERT_DIR/tripinfo.xml" \
    --reroute-log "$JALERT_DIR/reroute_log.json" \
    --port $PORT_D \
    $GUI_FLAG \
    2>&1 | grep -v "^$" | tail -8

  echo "  → $JALERT_DIR/tripinfo.xml"
  echo "  → $JALERT_DIR/reroute_log.json"

done

echo ""
echo "============================================================"
echo "  Experiment sweep complete."
echo "  Outputs: $EXP_OUT"
echo ""
echo "  Next step:"
echo "    python3 scripts/compute_metrics.py"
echo "============================================================"
echo ""
