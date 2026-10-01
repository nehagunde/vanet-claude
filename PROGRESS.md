# PROGRESS — NS-3 Integration Improvement

## Convention
- Existing outputs stay in `output/` untouched.
- New comparison-run outputs (Phase 3+) go into `output/v2/`.
- Each phase ends with run commands and expected output listed here.

---

## Phase 1 — Small Bug Fixes  ✅

### What changed

| File | Change | Why |
|---|---|---|
| `data_node/generate_routes.py` | Added `e.allows("passenger")` to `find_nearest_edge` filter | Edge `-1221667957` (near rsu_05) does not allow passenger cars → veh_09 failed to depart |
| `sim/bridge/traci_supervisor.py` | `("slow","heavy")` → `("heavy","jam")` in live injection condition | "slow" is not a valid congestion level; "jam" (ratio≥2.00) was excluded |
| `data_node/rerouter.py` | Same injection condition fix | Same bug mirrored in rerouter |
| `sim/ns3/jam-alert-app.h` | Extended `Setup()` with 5th param `alertMsg`; added `m_alertMsg` field | Allow per-RSU alert text from scenario |
| `sim/ns3/jam-alert-app.cc` | Store `alertMsg` in `Setup()`; use `m_alertMsg` in `SendAlert()` | RSU now broadcasts the correct "Take alternate route at X, jam detected at Y" text |
| `sim/ns3/vanet-scenario.cc` | Added `area` field to `RsuEntry`; load it from JSON; compute and pass alert message per RSU | Each RSU now knows its own area name and its predecessor's area name |
| `data_node/jam_detector.py` | Dynamic alert message: "Take alternate route at {alert_area}, jam detected at {jam_area}" | Unified alert text; was "Traffic jam on edge X — take U-turn" |
| `scripts/visualise.py` | Count all unique vehicle IDs (not just first timestep); rename "U-Turn Rerouting Events" → "Rerouting Events" | Was showing "1 OBU Vehicles" because only veh_00 is active at t=0 |

### How to test (Kali Linux)

```bash
cd /home/kali/vanet_claude
bash scripts/run_mock.sh
```

Expected output to confirm:
1. All 10 vehicles depart — no "not allowed to depart" errors in SUMO output
2. NS-3 `output/alerts.log` RSU alert lines contain `"Take alternate route at New Gajuwaka, jam detected at BHPV Junction"`
3. `output/jam_report.json` alert_message = `"Take alternate route at New Gajuwaka, jam detected at BHPV Junction"`
4. Dashboard `output/vanet_summary_mock.html` shows **10** OBU Vehicles (not 1)
5. Dashboard heading reads **"Rerouting Events"** (not "U-Turn Rerouting Events")

### NS-3 rebuild required
After copying files to scratch/, run:
```bash
cd /home/kali/ns-3-dev
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.h  scratch/
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.cc scratch/
cp /home/kali/vanet_claude/sim/ns3/vanet-scenario.cc scratch/
./ns3 build
```

---

## Phase 2 — (pending)
