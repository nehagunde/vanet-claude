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

## Phase 2 — Real Jam Detection in NS-3  ✅

### What changed

| File | Change | Why |
|---|---|---|
| `sim/ns3/jam-alert-app.h` | Added `#include <set>`; replaced `m_slowCount` with `m_slowSeconds` (OBU), `m_seenSenders` set + `m_firstSlowAt` + `m_jamFired` (RSU) | OBU needs per-vehicle consecutive slow counter; RSU needs distinct sender set |
| `sim/ns3/jam-alert-app.cc` | `SendBeacon()`: reads real speed via `GetVelocity()`, increments `m_slowSeconds`, sends `JAM_DETECTED` when `> 30 s`; `HandleRead()`: RSU inserts `sender_id` into `m_seenSenders`, fires QUORUM_REACHED once when ≥ 3 distinct vehicles, resets window after 30 s | Phase 2 requirements: real speed, distinct vehicle count, one alert per jam |
| `scripts/run_mock.sh` | `--logFile` → `output/v2/alerts.log` | New outputs go to output/v2/ per convention |
| `scripts/run_live.sh` | Same | Same |

### How to test (Kali Linux)

```bash
# 1. Copy updated NS-3 files
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.h   /home/kali/ns-3-dev/scratch/vanet/
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.cc  /home/kali/ns-3-dev/scratch/vanet/
cp /home/kali/vanet_claude/sim/ns3/vanet-scenario.cc /home/kali/ns-3-dev/scratch/vanet/

# 2. Rebuild NS-3
cd /home/kali/ns-3-dev
./ns3 build

# 3. Run mock pipeline
cd /home/kali/vanet_claude
bash scripts/run_mock.sh
```

### Lines in output/v2/alerts.log that prove it works

**OBU sending JAM_DETECTED** (appears after vehicle is slow > 30 s):
```
[T=92.0] NODE=3 SENT=JAM_DETECTED SPEED=1.20 X=... Y=... SLOW_S=31
```

**RSU logging each JAM_DETECTED received**:
```
[T=93.0] RSU=12 JAM_DETECTED_FROM=3 SPEED=1.20 DISTINCT_COUNT=1
[T=93.0] RSU=12 JAM_DETECTED_FROM=5 SPEED=0.80 DISTINCT_COUNT=2
[T=93.0] RSU=12 JAM_DETECTED_FROM=7 SPEED=1.10 DISTINCT_COUNT=3
```

**RSU firing exactly once when quorum reached**:
```
[T=93.0] RSU=12 QUORUM_REACHED vehicles=[3,5,7]
[T=93.0] NODE=12 SENT=JAM_ALERT MSG="Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
```

If `QUORUM_REACHED` appears exactly once (not repeated every beacon), Phase 2 is working correctly.

---

## Phase 3 — RSU-to-RSU Wired Backhaul Relay  ✅

### What changed

| File | Change |
|---|---|
| `sim/ns3/jam-alert-app.h` | Added `SetBackhaulPeer()`; `m_hasBkPeer`, `m_bkPeerAddr`, `m_bkPort`, `m_bkTxSocket`, `m_bkRxSocket`; `HandleBackhaulRead()`, `SendBackhaulAlert()`; `m_cntSent[3]`, `m_cntRecv[3]` |
| `sim/ns3/jam-alert-app.cc` | `StartApplication()`: RSUs create `bkRxSocket` (always) and `bkTxSocket` (if peer configured). `HandleRead()`: quorum now calls `SendBackhaulAlert()` instead of 802.11p direct. `HandleBackhaulRead()`: receives relay, rebroadcasts over 802.11p. `StopApplication()`: logs STATS line for delivery ratio. OBU JAM_ALERT reception logged with `JAM_ALERT_RECV`. |
| `sim/ns3/vanet-scenario.cc` | Added `#include "ns3/point-to-point-module.h"`; PointToPoint links between adjacent RSU pairs (100 Mbps, 2 ms); subnet 10.2.<i>.0/30 per pair; `SetBackhaulPeer()` called on each RSU with its approach-side peer IP; default logFile → output/v2/alerts.log |

### How to rebuild and run

```bash
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.h   /home/kali/ns-3-dev/scratch/vanet/
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.cc  /home/kali/ns-3-dev/scratch/vanet/
cp /home/kali/vanet_claude/sim/ns3/vanet-scenario.cc /home/kali/ns-3-dev/scratch/vanet/
cd /home/kali/ns-3-dev && ./ns3 build
cd /home/kali/vanet_claude && bash scripts/run_mock.sh
```

### Log lines that prove the relay reached veh_00 and veh_01

**1. Quorum at BHPV RSU (node 12) — relay sent over wired backhaul:**
```
[T=93.0] RSU=12 QUORUM_REACHED vehicles=[3,5,7]
[T=93.0] RSU=12 RELAY_SENT PEER=10.2.1.1 MSG="Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
```

**2. New Gajuwaka RSU (node 11) receives relay and rebroadcasts over 802.11p:**
```
[T=93.0] RSU=11 RELAY_RECV FROM_RSU=12 MSG="Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
[T=93.0] NODE=11 SENT=JAM_ALERT MSG="Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
```

**3. veh_00 (node 0) and veh_01 (node 1) receive the 802.11p warning from RSU 11:**
```
[T=93.x] OBU=0 JAM_ALERT_RECV FROM_RSU=11 MSG="Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
[T=93.x] OBU=1 JAM_ALERT_RECV FROM_RSU=11 MSG="Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
```

**4. Delivery ratio — STATS line at end of simulation for each node:**
```
[T=600.0] NODE=0 STATS SENT_BEACON=600 SENT_JAM_DETECTED=0 SENT_JAM_ALERT=0 RECV_BEACON=... RECV_JAM_DETECTED=0 RECV_JAM_ALERT=1
```
`RECV_JAM_ALERT=1` on veh_00 / veh_01 confirms the warning was delivered.

To compute delivery ratio:
```bash
grep "JAM_ALERT_RECV" output/v2/alerts.log | wc -l   # OBUs that got warned
grep "SENT=JAM_ALERT" output/v2/alerts.log | wc -l    # total JAM_ALERTs sent
```
