# PAPER_DETAILS.md
# Dual-Mode VANET System for Real-Time Jam Detection and Early Rerouting Advisory
# Using V2I and RSU Relay — NH-16, Visakhapatnam
#
# All values in this file are taken directly from the source code.
# No values are assumed or estimated.

---

## 1. Final System Architecture and Role of Each Node/Component

### Pipeline Phases (in execution order)

```
Phase 1  : Build corridor road network from OpenStreetMap (corridor.net.xml)
Phase 2a : Fetch traffic state — Google Maps API (live) OR mock fixture (mock)
Phase 2b : Generate SUMO vehicle routes weighted by congestion ratio
Phase 3a : SUMO simulation via TraCI → mobility.ns2, rsu_static.json, speed_log.json
Phase 3b : NS-3 802.11p WAVE simulation → alerts.log
Phase 4a : Offline jam detector → jam_report.json  (reads speed_log.json)
Phase 4b : TraCI rerouter → reroute_log.json       (live rerouting in SUMO)
Phase 5  : Visualiser + GUI → vanet_summary_*.html, vanet_gui_*.html
```

### Components and Their Roles

| Component | File | Role |
|---|---|---|
| Corridor network | `corridor/corridor.net.xml` | SUMO road network of NH-16, converted from OSM |
| RSU positions | `corridor/rsu_positions.csv` | 7 RSU lat/lon + SUMO XY coordinates |
| Traffic fetcher | `scripts/fetch_traffic.py` | Queries Google Maps API or reads mock fixture; writes `corridor/traffic_state.json` |
| Route generator | `data_node/generate_routes.py` | Creates 10 OBU vehicle trips in `sim/sumo/routes.rou.xml` weighted by congestion ratio |
| SUMO config | `sim/sumo/vanet.sumocfg` | Ties net + routes; runs 0–600 s, step 1 s |
| TraCI supervisor | `sim/bridge/traci_supervisor.py` | Runs SUMO via TraCI; collects per-vehicle FCD; writes `mobility.ns2`, `rsu_static.json`, `speed_log.json` |
| NS-3 scenario | `sim/ns3/vanet-scenario.cc` | Creates 10 OBU + 7 RSU nodes; installs 802.11p radio; runs JamAlertApp; writes `alerts.log` |
| JamAlertApp | `sim/ns3/jam-alert-app.cc/.h` | OBUs broadcast BEACONs every 1 s; RSUs aggregate and fire JAM_ALERT when threshold met |
| Jam detector | `data_node/jam_detector.py` | Offline post-processing of `speed_log.json`; applies detection rule; writes `jam_report.json` |
| Rerouter | `data_node/rerouter.py` | Re-runs SUMO via TraCI (port 8814); detects jammed edges; calls `rerouteTraveltime` (Dijkstra) on approaching vehicles |
| Visualiser | `scripts/visualise.py` | Reads output files; writes `vanet_summary_*.html` dashboard |
| GUI visualiser | `scripts/gui_visualiser.py` | Animated canvas showing RSU relay chain, SENDER/RECEIVER badges, alert banner |

### Node Numbering (NS-3)

| NS-3 Node IDs | Type | Count |
|---|---|---|
| 0 – 9 | OBU (On-Board Unit, mobile vehicles) | 10 |
| 10 – 16 | RSU (Roadside Unit, fixed) | 7 |

---

## 2. Jam Detection Algorithm — Exact Thresholds and Timing

### Two-Layer Detection

**Layer 1 — NS-3 (online, real-time during simulation):**
Defined in `sim/ns3/jam-alert-app.h`:
```
BEACON_INTERVAL_S   = 1.0 s      // OBU broadcasts every 1 second
JAM_SPEED_THRESHOLD = 5.0 km/h   // OBU reports JAM_DETECTED if speed < 5 km/h
JAM_VEH_THRESHOLD   = 3          // RSU fires JAM_ALERT when >= 3 vehicles report slow
JAM_TIME_THRESHOLD  = 30.0 s     // all 3 reports must arrive within 30-second window
```

**RSU aggregation logic (jam-alert-app.cc lines 208–232):**
- RSU counts incoming JAM_DETECTED messages
- If `(now - first_slow_at) <= 30.0 s` and `slow_count >= 3`: fire JAM_ALERT, reset counter
- If window expired (>30 s): restart counter from 1

**Layer 2 — Python offline detector (post-simulation):**
Defined in `data_node/jam_detector.py`:
```python
JAM_SPEED_KMH    = 5.0    # km/h — below this = "slow"
JAM_MIN_VEHICLES = 3      # minimum vehicles on same road edge simultaneously
JAM_MIN_SECONDS  = 30     # consecutive seconds each vehicle must be below threshold
```

**Detection rule (jam_detector.py line 119):**
```
jam confirmed when:
  count(vehicles with consecutive_slow_seconds > 30) >= 3
  all vehicles on the SAME road edge
```

**Jam injection in simulation (traci_supervisor.py):**
```python
JAM_SPEED_MS = 1.2    # m/s (~4.3 km/h) — speed cap applied to jam edges
JAM_START_S  = 60.0   # jam begins at t=60 s (after vehicles have spread)
JAM_END_S    = 350.0  # jam clears at t=350 s (duration = 290 s)
```
Speed is restored to `13.89 m/s` (~50 km/h) after `JAM_END_S`.

**Jam zone bounding box (mock mode, traci_supervisor.py + rerouter.py):**
```python
JAM_Y_MIN, JAM_Y_MAX = 3700.0, 4800.0   # SUMO Y coordinates (metres)
JAM_X_MIN, JAM_X_MAX = 3000.0, 4200.0   # SUMO X coordinates (metres)
```
Live mode: bounding box computed from RSU CSV coordinates ± 200 m margin.

**Live mode congestion levels (fetch_traffic.py):**
```
ratio = duration_in_traffic / duration_free_flow
free      ratio < 1.10
light     1.10 – 1.30
moderate  1.30 – 1.60
heavy     1.60 – 2.00
jam       ratio >= 2.00
```
Jam injection only if level is "slow" or "heavy" (traci_supervisor.py line 70).

---

## 3. Alert Point Selection Logic and Alert Message Format

### Alert RSU Selection

The system uses a **multi-hop RSU relay** design:

- **jamRsuNode** = rsu_02 (BHPV Junction, NS-3 node 12) — nearest RSU to the jam zone
- **alertRsuNode** = rsu_01 (New Gajuwaka, NS-3 node 11) — one RSU index behind jamRsuNode

Selection logic in `scripts/gui_visualiser.py`:
```python
jamRsuIdx    = corridor.index(jamRsuNode)   # index of jam RSU in corridor list
alertRsuNode = corridor[jamRsuIdx - 1]      # RSU one step behind (south/approaching side)
```

This places the alert point ~1 km before the jam zone, giving approaching vehicles
enough road distance to take the diversion.

### Alert Message Format

Set in `scripts/gui_visualiser.py`:
```
"Take alternate route at {jam_banner_uturn}, jam detected at {jam_banner_jam_loc}"
```

Example (mock mode):
```
"Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
```

Where:
- `jam_banner_jam_loc` = area name of jamRsuNode (BHPV Junction = rsu_02)
- `jam_banner_uturn`   = area name of alertRsuNode (New Gajuwaka = rsu_01)

### Offline detector alert format (jam_detector.py):
```
"Traffic jam on edge {edge} near {nearest_rsu} — avg {speed} km/h for {duration} s — take U-turn at nearest junction"
```

### NS-3 RSU alert message (jam-alert-app.cc line 226):
```
"Traffic jam detected — take U-turn at nearest junction"
```

---

## 4. V2V and V2I Message Flow — Step by Step

```
Step 1  [t = 0 s]
        OBUs depart from their respective corridor segments.
        Each OBU broadcasts BEACON every 1 second over 802.11p (UDP broadcast to 255.255.255.255, port 7777).
        BEACON payload: {msg_type=0, sender_id, speed_kmh=-1, pos_x, pos_y, edge}

Step 2  [t = 60 s]
        TraCI supervisor sets max speed = 1.2 m/s on jam-zone edges (BHPV–Nathayyapalem).
        Vehicles on those edges begin slowing below 5 km/h.

Step 3  [t ≈ 90 s]
        Slow vehicles have been below 5 km/h for > 30 consecutive seconds.
        NS-3 OBU nodes broadcast JAM_DETECTED (msg_type=1) to all within 300 m radio range.
        → rsu_02 (BHPV, NS-3 node 12) receives JAM_DETECTED from ≥ 3 vehicles.

Step 4  [RSU aggregation]
        rsu_02 counts JAM_DETECTED messages within 30-second window.
        When slow_count >= 3: rsu_02 broadcasts JAM_ALERT (msg_type=2) over 802.11p.
        JAM_ALERT payload: {msg_type=2, sender_id=12, alert_msg="Traffic jam detected..."}

Step 5  [RSU-to-RSU relay]
        rsu_01 (New Gajuwaka, NS-3 node 11) receives JAM_ALERT from rsu_02.
        rsu_01 re-broadcasts JAM_ALERT to all nodes within its 300 m range.
        → Approaching vehicles V00, V01 (near New Gajuwaka) receive the alert.

Step 6  [Vehicle rerouting]
        Python rerouter (rerouter.py) detects jammed edges via TraCI.
        Calls traci.edge.adaptTraveltime(jammed_edge, 9999.0) to inflate cost.
        Calls traci.vehicle.rerouteTraveltime(veh_id) → SUMO Dijkstra finds alternate path.
        Rerouting cooldown: same vehicle not rerouted again within 60 seconds.

Step 7  [t = 350 s]
        JAM_END_S reached. TraCI restores max speed to 13.89 m/s (~50 km/h).
        Jam condition clears. No new JAM_DETECTED messages sent.
```

**Message types (VanetMsg struct, jam-alert-app.h):**
```c
BEACON       = 0   // periodic hello from OBU
JAM_DETECTED = 1   // OBU self-reports slow speed
JAM_ALERT    = 2   // RSU rebroadcast — jam confirmed
```

**Wire format (packed struct, no padding):**
```c
uint8_t  msg_type       // 1 byte
uint32_t sender_id      // 4 bytes — NS-3 node index
float    speed_kmh      // 4 bytes
float    pos_x          // 4 bytes — SUMO X coordinate
float    pos_y          // 4 bytes — SUMO Y coordinate
char     edge[32]       // 32 bytes — SUMO road edge ID
char     alert_msg[64]  // 64 bytes — human-readable alert (JAM_ALERT only)
// Total: 113 bytes per message
```

---

## 5. SUMO Parameters

| Parameter | Value | Source |
|---|---|---|
| Simulation duration | 600 s (10 minutes) | `vanet.sumocfg`, `generate_routes.py` |
| Time step | 1 s | `vanet.sumocfg`, `traci_supervisor.py` |
| Number of OBU vehicles | 10 | `generate_routes.py` (`N_VEHICLES = 10`) |
| Vehicle depart spread | 120 s (staggered over first 2 minutes) | `generate_routes.py` (`DEPART_SPREAD_S = 120`) |
| Free-flow speed (NH-16) | 50 km/h | `generate_routes.py` (`FREE_FLOW_KMH = 50.0`) |
| Jam speed cap | 1.2 m/s (~4.3 km/h) | `traci_supervisor.py` (`JAM_SPEED_MS = 1.2`) |
| Jam start time | t = 60 s | `traci_supervisor.py` (`JAM_START_S = 60.0`) |
| Jam end time | t = 350 s | `traci_supervisor.py` (`JAM_END_S = 350.0`) |
| Jam duration | 290 s | Derived: `JAM_END_S - JAM_START_S` |
| Speed restored to | 13.89 m/s (~50 km/h) | `traci_supervisor.py` |
| Teleport timeout | 300 s | `vanet.sumocfg` (`time-to-teleport`) |
| Collision action | warn (no abort) | `vanet.sumocfg` |
| Route error handling | ignore-route-errors = true | `vanet.sumocfg` |
| Edge search radius | 600 m | `generate_routes.py` (`EDGE_SEARCH_R_M = 600.0`) |
| Network source | OpenStreetMap (NH-16 corridor) | `corridor/corridor.osm` → `corridor.net.xml` |
| TraCI port (supervisor) | 8813 | `traci_supervisor.py` |
| TraCI port (rerouter) | 8814 | `rerouter.py` |

**Vehicle distribution (mock mode, from generate_routes.py comments):**
```
seg_00_01 (Old Gajuwaka → New Gajuwaka)     → 1 vehicle   (ratio 1.05, free)
seg_01_02 (New Gajuwaka → BHPV)             → 1 vehicle   (ratio 1.25, light)
seg_02_03 (BHPV → Nathayyapalem)            → 3 vehicles  (ratio 2.20, JAM) ← alert zone
seg_03_04 (Nathayyapalem → Sheelanagar)     → 2 vehicles  (ratio 1.85, heavy)
seg_04_05 (Sheelanagar → Gopalapatnam)      → 2 vehicles  (ratio 1.45, moderate)
seg_05_06 (Gopalapatnam → NAD Junction)     → 1 vehicle   (ratio 1.15, light)
Total: 10 vehicles
```

**Rerouter simulation (rerouter.py, runs independently):**
```
SIM_DURATION_S = 600 s
STEP_S         = 1.0 s
Reroute cooldown: 60 s per vehicle
Jammed edge travel time cost: 9999.0 s (to force Dijkstra avoidance)
JAM_SPEED_KMH  = 5.0   (rerouter detection threshold)
JAM_MIN_VEHICLES = 3
JAM_MIN_SECONDS  = 30
```

---

## 6. NS-3 Parameters (802.11p Settings)

| Parameter | Value | Source |
|---|---|---|
| Standard | IEEE 802.11p (WIFI_STANDARD_80211p) | `vanet-scenario.cc` line 173 |
| Channel width | 10 MHz | `OfdmRate6MbpsBW10MHz` |
| Data rate | 6 Mb/s | `OfdmRate6MbpsBW10MHz` |
| Control mode | 6 Mb/s | `OfdmRate6MbpsBW10MHz` |
| Frequency | 5.9 GHz | `FriisPropagationLossModel`, `vanet-scenario.cc` line 165 |
| Tx power start | 20 dBm | `vanet-scenario.cc` line 169 |
| Tx power end | 20 dBm | `vanet-scenario.cc` line 170 |
| Effective range | ~300 m (free space, Friis model) | Comments in `vanet-scenario.cc` |
| Propagation loss model | FriisPropagationLossModel | `vanet-scenario.cc` line 164 |
| Propagation delay model | ConstantSpeedPropagationDelayModel | `vanet-scenario.cc` line 163 |
| MAC mode | AdhocWifiMac (no association) | `vanet-scenario.cc` line 180 |
| Rate manager | ConstantRateWifiManager | `vanet-scenario.cc` line 174 |
| IP subnet | 10.1.0.0 / 255.255.0.0 | `vanet-scenario.cc` line 189 |
| Transport | UDP broadcast to 255.255.255.255 | `jam-alert-app.cc` |
| Port | 7777 | `vanet-scenario.cc` line 127, `jam-alert-app.h` |
| Beacon interval | 1.0 s | `jam-alert-app.h` (`BEACON_INTERVAL_S = 1.0`) |
| OBU nodes | 10 (NS-3 nodes 0–9) | `vanet-scenario.cc` (`nObu = 10`) |
| RSU nodes | 7 (NS-3 nodes 10–16) | `vanet-scenario.cc` (`nRsu = rsus.size()`) |
| RSU antenna height | 1.5 m | `vanet-scenario.cc` line 213 (`Vector(x, y, 1.5)`) |
| OBU mobility model | Ns2MobilityHelper (waypoint trace from SUMO) | `vanet-scenario.cc` line 193 |
| RSU mobility model | ConstantPositionMobilityModel | `vanet-scenario.cc` line 211 |
| Simulation time | 600 s | `vanet-scenario.cc` (`simTime = 600.0`) |

**RSU positions (from `sim/bridge/rsu_static.json`):**

| NS-3 ID | RSU ID | Area | X (m) | Y (m) | Lat | Lon |
|---|---|---|---|---|---|---|
| 10 | rsu_00 | Old Gajuwaka | 3320.99 | 1846.97 | 17.685048 | 83.203902 |
| 11 | rsu_01 | New Gajuwaka | 3483.10 | 2810.81 | 17.693737 | 83.205536 |
| 12 | rsu_02 | BHPV | 3484.99 | 3781.42 | 17.702503 | 83.205661 |
| 13 | rsu_03 | Nathayyapalem | 3425.37 | 4689.20 | 17.710709 | 83.205199 |
| 14 | rsu_04 | Sheelanagar | 3245.36 | 5579.66 | 17.718771 | 83.203601 |
| 15 | rsu_05 | Gopalapatnam | 3463.72 | 6136.56 | 17.723778 | 83.205720 |
| 16 | rsu_06 | NAD Junction | 5307.99 | 7097.46 | 17.732260 | 83.223209 |

**Inter-RSU distances (from `rsu_positions.csv`):**
```
rsu_00 → rsu_01 : 0.9815 km
rsu_01 → rsu_02 : 0.9749 km
rsu_02 → rsu_03 : 0.9137 km
rsu_03 → rsu_04 : 0.9123 km
rsu_04 → rsu_05 : 0.6003 km
rsu_05 → rsu_06 : 2.0786 km
Total corridor  : ~6.48 km
```

---

## 7. Google Maps Integration and SUMO–NS-3 Coupling

### Google Maps API Usage (fetch_traffic.py)

- **API used:** Google Maps Directions API
- **Call parameters:** `mode="driving"`, `departure_time=now_epoch`, `traffic_model="best_guess"`
- **Per-segment query:** one API call per RSU-to-RSU segment (6 calls total for 7 RSUs)
- **Rate limit compliance:** `time.sleep(0.2)` between calls (stays under 10 req/s limit)
- **Fields used from response:**
  - `leg["distance"]["value"]` → `distance_m`
  - `leg["duration"]["value"]` → `duration_free_s`
  - `leg["duration_in_traffic"]["value"]` → `duration_traffic_s`
- **Congestion ratio:** `ratio = duration_traffic_s / duration_free_s`
- **Output:** `corridor/traffic_state.json`
- **Fallback:** If API call fails for a segment, free-flow values are used

**Live mode jam injection condition (traci_supervisor.py line 70):**
```python
if level not in ("slow", "heavy"):
    return None   # no jam injection
```
Only `"slow"` and `"heavy"` trigger jam injection. `"free"`, `"light"`, `"moderate"` do not.

### SUMO–NS-3 Coupling (Decoupled / Sequential)

This project uses **sequential (decoupled) coupling**, not real-time bidirectional:

```
Step 1: SUMO runs via TraCI (traci_supervisor.py)
        → Outputs: mobility.ns2, rsu_static.json, speed_log.json

Step 2: NS-3 reads mobility.ns2 (vehicle waypoints) + rsu_static.json (RSU positions)
        → Outputs: alerts.log

Step 3: Python jam_detector.py reads speed_log.json
        → Outputs: jam_report.json

Step 4: Python rerouter.py re-runs SUMO via TraCI with live rerouting
        → Outputs: reroute_log.json
```

**TraCI bidirectional coupling (rerouter.py):**
- SUMO → Python: `traci.vehicle.getPosition()`, `traci.vehicle.getSpeed()`, `traci.vehicle.getRoadID()`, `traci.vehicle.getRoute()`
- Python → SUMO: `traci.edge.setMaxSpeed()`, `traci.edge.adaptTraveltime()`, `traci.vehicle.rerouteTraveltime()`

**Mobility trace format (mobility.ns2):**
```
$node_(0) set X_ <x>
$node_(0) set Y_ <y>
$node_(0) set Z_ 0.00
$ns_ at <t> "$node_(0) setdest <x> <y> <speed>"
```
OBU nodes: 0–9; RSU nodes: 10–16 (static, not in mobility trace).

---

## 8. Output Files and Metrics

| File | Written by | Contents |
|---|---|---|
| `corridor/traffic_state.json` | `fetch_traffic.py` | Per-segment: distance_m, duration_free_s, duration_traffic_s, speed_kmh, congestion_ratio, congestion_level |
| `sim/sumo/routes.rou.xml` | `generate_routes.py` | 10 vehicle `<trip>` elements with depart times and from/to edges |
| `sim/bridge/mobility.ns2` | `traci_supervisor.py` | Per-vehicle ns2 waypoint trace: position + speed every 1 s for 600 s |
| `sim/bridge/rsu_static.json` | `traci_supervisor.py` | 7 RSU NS-3 node IDs + XY positions + lat/lon |
| `sim/bridge/speed_log.json` | `traci_supervisor.py` | Per-second per-vehicle: speed_kmh, edge, x, y — 600 entries |
| `output/alerts.log` | NS-3 (`vanet-scenario.cc`) + appended by `jam_detector.py` + `rerouter.py` | Timestamped BEACON / JAM_DETECTED / JAM_ALERT events; jam detector results; rerouter results |
| `output/jam_report.json` | `jam_detector.py` | Jam events: start_s, end_s, duration_s, edge, vehicles[], avg_speed_kmh, centroid_x/y, nearest_rsu, alert_message |
| `output/reroute_log.json` | `rerouter.py` | jam_events[], reroute_events[], summary {total_jams_detected, total_vehicles_rerouted} |
| `output/results.xml` (results.csv) | SUMO (`vanet.sumocfg`) | Per-vehicle trip info: depart, arrival, duration, routeLength, waitingTime |
| `output/vanet_summary_mock.html` | `scripts/visualise.py` | Dashboard: speed charts, jam timeline, rerouting stats |
| `output/vanet_gui_mock.html` | `scripts/gui_visualiser.py` | Animated canvas: vehicles, RSUs, V2I arrows, RSU relay chain, alert banner |
| `output/vanet_summary_live.html` | `scripts/visualise.py` | Same as mock but with live traffic data |
| `output/vanet_gui_live.html` | `scripts/gui_visualiser.py` | Same as mock GUI but with live mode data |

**alerts.log entry format:**
```
[T=<sim_s>] NODE=<id> SENT=<BEACON|JAM_DETECTED|JAM_ALERT> SPEED=<kmh> X=<x> Y=<y>
[T=<sim_s>] NODE=<id> RECV=<type> FROM=<sender_id> SPEED=<kmh> EDGE=<edge> MSG="<alert>"
[T=<t>] NODE=RSU TYPE=REROUTE VEH=<id> JAM_EDGE=[...] ACTION="REROUTED — avoid jam, take alternate path"
[T=<t>] NODE=DETECTOR TYPE=JAM_ALERT EDGE=<edge> RSU=<rsu> VEHICLES=... AVG_SPEED=... DURATION=...s MSG="..."
```

---

## 9. Assumptions and Limitations

### Assumptions

1. **Free-flow speed:** NH-16 urban speed limit assumed to be 50 km/h (`FREE_FLOW_KMH = 50.0`). Used when Google Maps does not return duration data.
2. **Radio range:** 300 m effective range assumed under the Friis free-space propagation model at 5.9 GHz, 20 dBm Tx power. Actual urban range may differ due to buildings, foliage, and multipath.
3. **RSU spacing:** RSUs are placed approximately every 1 km (0.60–2.08 km actual spacing). Each RSU covers its 300 m radio range; gaps exist between rsu_04–rsu_05 (600 m) and rsu_05–rsu_06 (2.08 km — beyond single-hop range).
4. **OBU count:** Fixed at 10 vehicles. The simulation does not model additional background traffic beyond these 10 OBUs.
5. **Vehicle speed in NS-3:** NS-3 beacons carry `speed_kmh = -1.0` (not available). Real speed detection relies entirely on the Python-side `speed_log.json` analysis, not on NS-3 packet contents.
6. **SUMO–NS-3 coupling:** Sequential (not real-time bidirectional). NS-3 uses pre-recorded SUMO waypoints; NS-3 communication events do not feed back into the SUMO simulation in the same run. Rerouting feedback is handled by a separate second SUMO run (`rerouter.py`).
7. **Google Maps congestion ratio:** Ratio computed as `duration_in_traffic / duration_free`. This reflects Google's traffic model, not direct sensor measurements.
8. **Jam injection trigger (live mode):** Only `"slow"` and `"heavy"` congestion levels trigger jam injection. The `"jam"` level from the mock fixture is not checked in the live-mode injection condition — only `"slow"` and `"heavy"` are used.
9. **Teleport:** SUMO is configured to teleport stuck vehicles after 300 s (`time-to-teleport = 300`) to prevent simulation deadlock.
10. **Corridor direction:** The simulation models southbound-to-northbound travel: Old Gajuwaka (rsu_00) → NAD Junction (rsu_06). Reverse direction traffic is not modelled.

### Limitations

1. **No real-time bidirectional coupling:** SUMO and NS-3 run sequentially. NS-3 packet events cannot affect vehicle trajectories within the same simulation run. The rerouter runs as a separate second SUMO pass.
2. **Fixed OBU count (10):** Real traffic has hundreds to thousands of vehicles. Results may not scale linearly. Packet collision and channel saturation effects at high vehicle density are not captured.
3. **Friis propagation (free-space):** Real 802.11p urban propagation involves shadowing, multipath, and NLOS conditions. Friis over-estimates range in dense urban environments.
4. **No WAVE short-message protocol (WSMP):** The implementation uses plain UDP over adhoc 802.11p, not the full WAVE/DSRC stack (IEEE 1609.3/1609.4 multi-channel operation, CCH/SCH switching).
5. **Speed in NS-3 beacons is placeholder:** The `speed_kmh = -1.0` in NS-3 beacons means the NS-3 layer does not perform speed-based JAM_DETECTED classification. All real detection is done post-hoc by `jam_detector.py` reading `speed_log.json`.
6. **Single corridor, single direction:** The system is validated only on the NH-16 Gajuwaka–NAD Junction segment (~6.5 km, 7 RSUs). Scalability to a full city network or multi-lane highway has not been evaluated.
7. **No V2V direct alerts:** Jam vehicles and approaching vehicles are ~1 km apart (beyond 300 m 802.11p range). The V2V direct alert path does not exist — all alerts travel via the RSU relay chain (V2I → RSU-to-RSU → I2V).
8. **Google Maps API dependency (live mode):** Live mode requires a valid `GOOGLE_MAPS_API_KEY` in `.env`. Rate limits, API quota, and network availability are external dependencies outside the system's control.
9. **Mock fixture is static:** `data_node/mock/traffic_state.mock.json` has a fixed timestamp (`2026-05-06T08:45:00+05:30`) and a hardcoded jam at BHPV–Nathayyapalem. It does not change between runs.
10. **No collision/safety messages:** The system models only traffic jam detection and rerouting advisory. Collision warning, emergency vehicle notification, and lane-change assist (ETSI CAM/DENM) are out of scope.
