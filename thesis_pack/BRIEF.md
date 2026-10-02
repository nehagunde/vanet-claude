# VANET Project — Spec & Implementation Record

**Purpose of this file:** Single source of truth for the project's goal,
decisions, architecture, and what was actually implemented. Reflects the
final implemented state of the project as of September 2026.

---

## 1. Goal

Build a working VANET prototype that:

1. Pulls real-time traffic data for the NH-16 Gajuwaka → NAD Junction corridor
   in Visakhapatnam via Google Maps Directions API (live mode), or replays a
   recorded fixture (mock mode).
2. Places 10 OBU vehicles across 6 corridor segments, distributed by congestion ratio.
3. Simulates V2I (OBU↔RSU) and RSU-to-RSU relay communication using IEEE 802.11p WAVE.
4. Detects traffic jams using a dual-condition rule (speed + vehicle count + duration).
5. Fires a proactive early warning alert via multi-hop RSU relay so approaching
   vehicles receive the alert ~1 km before the jam — early enough to divert.
6. Alert message: `"Take alternate route at <diversion RSU area>, jam detected at <jam RSU area>"`
7. Reroutes approaching vehicles via TraCI (Dijkstra shortest-path, avoiding jammed edges).
8. Produces animated HTML GUI and results dashboard showing the full relay chain.

Deliverable context: **academic MTech dissertation project** — optimise for
clarity, clean comments, and a watchable demo.

---

## 2. Locked Decisions (Implemented)

| # | Decision | Implemented Value |
|---|---|---|
| Title | Dual-Mode VANET System for Real-Time Jam Detection and Early Rerouting Advisory Using V2I and RSU Relay | Final title |
| Corridor | NH-16 Old Gajuwaka → NAD Junction, Visakhapatnam (~6.48 km) | 7 RSUs placed |
| RSU count | 7 RSUs (rsu_00 to rsu_06) | From `rsu_positions.csv` |
| OBU count | 10 vehicles (fixed cohort) | `N_VEHICLES = 10` in `generate_routes.py` |
| Simulation duration | 600 s (10 minutes) | `SIM_DURATION_S = 600` |
| Time step | 1 s | `vanet.sumocfg`, `traci_supervisor.py` |
| SUMO version | Eclipse SUMO 1.25.0 | Kali VM |
| NS-3 version | ns-3-dev | Kali VM |
| SUMO ↔ NS-3 coupling | Sequential (decoupled). SUMO runs first via TraCI → writes `mobility.ns2` → NS-3 reads it. Not real-time bidirectional. | `traci_supervisor.py` |
| Host vs target | Files on Windows `C:\Users\NEHAGUNDE\Desktop\vanet_claude\`. SUMO + NS-3 run on Kali Linux VM at `/home/kali/vanet_claude/`. | Shared folder via VMware |
| Language split | C++ for NS-3 WAVE app. Python for everything else. | Implemented |
| Demo mode | Two modes: `--mock` (always jams BHPV) and live (Google Maps real-time). | `run_mock.sh`, `run_live.sh` |
| API key | `.env` at project root, gitignored. Read via `python-dotenv`. | `.env`, `.env.example` |
| Maps refresh | `MAPS_REFRESH_SEC=45` default. `--loop` flag for continuous polling. | `fetch_traffic.py` |
| Alert message | "Take alternate route at {diversion point}, jam detected at {jam RSU area}" | `gui_visualiser.py` |
| Rerouting algorithm | Dijkstra via TraCI `rerouteTraveltime` | `rerouter.py` |
| Base paper | Sommer, German & Dressler — "Bidirectionally Coupled Network and Road Traffic Simulation for Improved IVC Analysis", IEEE TMC, vol. 10, no. 1, 2011 | Extension: added jam detection + RSU relay + rerouting application |

---

## 3. Jam Detection Rule (Implemented)

### Dual-Layer Detection

**Layer 1 — NS-3 (online, during 802.11p simulation):**

```
BEACON_INTERVAL   = 1.0 s       — OBU broadcasts every second
JAM_SPEED         = 5.0 km/h    — OBU sends JAM_DETECTED if speed < 5 km/h
JAM_VEH_THRESHOLD = 3           — RSU fires JAM_ALERT when >= 3 vehicles report slow
JAM_TIME_WINDOW   = 30.0 s      — all 3 reports must arrive within 30 seconds
```

RSU resets counter after firing JAM_ALERT to prevent repeated spamming.

**Layer 2 — Python offline (post-simulation, `jam_detector.py`):**

```
JAM_SPEED_KMH    = 5.0    km/h
JAM_MIN_VEHICLES = 3      vehicles on the SAME road edge
JAM_MIN_SECONDS  = 30     consecutive seconds each vehicle below threshold
```

**Live mode injection condition (`traci_supervisor.py`, `rerouter.py`):**
- Jam injected only if Google Maps congestion level is `"slow"` or `"heavy"`
- `"free"`, `"light"`, `"moderate"` → no jam injection → no alert sent
- This ensures live mode output matches real-world traffic at time of running

**Mock mode injection:**
- Always injects jam at BHPV–Nathayyapalem zone (hardcoded bounding box)
- `JAM_Y_MIN=3700, JAM_Y_MAX=4800, JAM_X_MIN=3000, JAM_X_MAX=4200` (SUMO metres)
- Jam starts at `t=60 s`, ends at `t=350 s`, speed capped to `1.2 m/s (~4.3 km/h)`
- After jam end: speed restored to `13.89 m/s (~50 km/h)`

**Google Maps congestion levels (`fetch_traffic.py`):**
```
ratio = duration_in_traffic / duration_free_flow
free      ratio < 1.10
light     1.10 – 1.30
moderate  1.30 – 1.60
heavy     1.60 – 2.00
jam       ratio >= 2.00
```

---

## 4. Multi-Hop RSU Relay Architecture (Key Contribution)

The alert does not go directly from jam vehicles to approaching vehicles —
they are ~1 km apart, beyond the 300 m 802.11p radio range. Instead:

```
Hop 1: Jam vehicles (veh_02, veh_03, veh_04)
           ──V2I──►  rsu_02 (BHPV Junction, jam RSU)

Hop 2: rsu_02
           ──RSU-to-RSU──►  rsu_01 (New Gajuwaka, alert RSU)

Hop 3: rsu_01
           ──I2V──►  approaching vehicles (veh_00, veh_01)
```

**Alert fires at rsu_01 (New Gajuwaka), ~1 km before the jam at rsu_02 (BHPV).**
This gives approaching vehicles enough road distance to take the diversion.

**Alert message:**
```
"Take alternate route at New Gajuwaka, jam detected at BHPV Junction"
```

**alertRsuNode selection logic (`gui_visualiser.py`):**
```python
jamRsuIdx    = corridor.index(jamRsuNode)
alertRsuNode = corridor[jamRsuIdx - 1]   # one RSU behind jam RSU
```

---

## 5. System Architecture (Implemented)

### Pipeline Phases

```
Phase 1  : Build corridor road network from OSM → corridor.net.xml
Phase 2a : Google Maps API (live) or mock fixture → corridor/traffic_state.json
Phase 2b : Generate SUMO vehicle routes → sim/sumo/routes.rou.xml
Phase 3a : SUMO via TraCI → mobility.ns2, rsu_static.json, speed_log.json
Phase 3b : NS-3 802.11p WAVE simulation → output/alerts.log
Phase 4a : Offline jam detector → output/jam_report.json
Phase 4b : TraCI rerouter (2nd SUMO run) → output/reroute_log.json
Phase 5  : Visualiser + GUI → output/vanet_summary_*.html, vanet_gui_*.html
```

### Node Architecture

**Data Node (Python):**
- `scripts/fetch_traffic.py` — Google Maps API or mock fixture → `traffic_state.json`
- `data_node/generate_routes.py` — route generation weighted by congestion ratio
- `data_node/jam_detector.py` — offline jam detection from `speed_log.json`
- `data_node/rerouter.py` — second SUMO run with live rerouting via TraCI

**Simulation Node (SUMO + NS-3):**
- `sim/sumo/vanet.sumocfg` — SUMO configuration
- `sim/bridge/traci_supervisor.py` — TraCI bridge; produces mobility.ns2
- `sim/ns3/vanet-scenario.cc` — NS-3 main: 10 OBU + 7 RSU nodes, 802.11p
- `sim/ns3/jam-alert-app.cc/.h` — BEACON / JAM_DETECTED / JAM_ALERT application

**NS-3 Node IDs:**
```
Nodes  0–9  : OBU (mobile vehicles)
Nodes 10–16 : RSU (fixed roadside units)
```

---

## 6. Project Directory Layout (Actual)

```
vanet_claude/
├── BRIEF.md                          # this file — project spec + implementation record
├── PAPER_DETAILS.md                  # detailed technical values for paper writing
├── .env                              # gitignored — Google Maps API key
├── .env.example                      # committed placeholder
├── .gitignore
├── requirements.txt
├── corridor/
│   ├── corridor.osm                  # OpenStreetMap data for NH-16
│   ├── corridor.net.xml              # SUMO road network (from netconvert)
│   ├── rsu_positions.csv             # 7 RSUs: id, area, lat, lon, x_m, y_m
│   ├── rsu_pois.add.xml              # RSU POI markers for SUMO-GUI
│   ├── rsu_map.html                  # RSU placement visualisation
│   ├── sumo_view.xml                 # SUMO-GUI view settings
│   └── traffic_state.json            # live traffic state (refreshed each run)
├── data_node/
│   ├── generate_routes.py            # Phase 2b — vehicle route generator
│   ├── jam_detector.py               # Phase 4a — offline jam detector
│   ├── rerouter.py                   # Phase 4b — live TraCI rerouter
│   └── mock/
│       └── traffic_state.mock.json   # fixed fixture: BHPV jam, peak-hour scenario
├── sim/
│   ├── sumo/
│   │   ├── vanet.sumocfg             # SUMO config (600 s, 1 s steps)
│   │   └── routes.rou.xml            # 10 OBU vehicle trips (generated)
│   ├── ns3/
│   │   ├── vanet-scenario.cc         # NS-3 main: 802.11p topology
│   │   ├── jam-alert-app.cc          # WAVE application implementation
│   │   └── jam-alert-app.h           # WAVE application header + constants
│   └── bridge/
│       ├── traci_supervisor.py       # Phase 3a — SUMO TraCI bridge
│       ├── mobility.ns2              # OBU waypoint trace (generated)
│       ├── rsu_static.json           # 7 RSU positions for NS-3 (generated)
│       └── speed_log.json            # per-vehicle speed each second (generated)
├── scripts/
│   ├── fetch_traffic.py              # Phase 2a — Google Maps / mock fetcher
│   ├── run_mock.sh                   # full mock pipeline launcher
│   ├── run_live.sh                   # full live pipeline launcher
│   ├── gui_visualiser.py             # Phase 5 — animated HTML GUI
│   ├── visualise.py                  # Phase 5 — results dashboard
│   ├── build_corridor.sh             # Phase 1 — OSM → SUMO network
│   ├── install_kali.sh               # apt + pip one-shot installer
│   ├── place_rsus.py                 # Phase 1 — RSU placement along corridor
│   └── calc_rsu_distances.py         # RSU inter-distance calculator
├── output/
│   ├── alerts.log                    # NS-3 + detector + rerouter events (appended)
│   ├── jam_report.json               # detected jam events with timing & location
│   ├── reroute_log.json              # rerouting decisions per vehicle
│   ├── results.xml                   # SUMO per-vehicle trip info
│   ├── vanet_summary_mock.html       # mock mode results dashboard
│   ├── vanet_gui_mock.html           # mock mode animated GUI
│   ├── vanet_summary_live.html       # live mode results dashboard
│   ├── vanet_gui_live.html           # live mode animated GUI
│   └── screenshots/                  # speed chart PNGs
└── docs/
    ├── corridor_choice.md
    └── phase3_bridge_explanation.md
```

---

## 7. SUMO Parameters (Implemented)

| Parameter | Value |
|---|---|
| Duration | 600 s |
| Step length | 1 s |
| Vehicles | 10 OBUs |
| Depart spread | 120 s (staggered over first 2 minutes) |
| Free-flow speed | 50 km/h (13.89 m/s) |
| Vehicle type | passenger car, accel=2.6 m/s², decel=4.5 m/s², sigma=0.5, length=4.5 m, minGap=2.5 m |
| Jam speed cap | 1.2 m/s (~4.3 km/h) |
| Jam active | t=60 s to t=350 s (290 s duration) |
| Teleport timeout | 300 s |
| TraCI port (supervisor) | 8813 |
| TraCI port (rerouter) | 8814 |
| Reroute cost | 9999 s travel time on jammed edges |
| Reroute cooldown | 60 s per vehicle |

---

## 8. NS-3 / 802.11p Parameters (Implemented)

| Parameter | Value |
|---|---|
| Standard | IEEE 802.11p (WIFI_STANDARD_80211p) |
| Frequency | 5.9 GHz |
| Channel width | 10 MHz |
| Data rate | 6 Mb/s (OfdmRate6MbpsBW10MHz) |
| Tx power | 20 dBm |
| Effective range | ~300 m (Friis free-space model) |
| Propagation loss | FriisPropagationLossModel |
| Propagation delay | ConstantSpeedPropagationDelayModel |
| MAC | AdhocWifiMac (no association handshake) |
| Transport | UDP broadcast, 255.255.255.255, port 7777 |
| Beacon interval | 1.0 s |
| OBU nodes | 10 (NS-3 IDs 0–9), mobile (ns2 waypoint trace) |
| RSU nodes | 7 (NS-3 IDs 10–16), fixed (ConstantPositionMobilityModel) |
| RSU antenna height | 1.5 m |
| Simulation time | 600 s |

---

## 9. RSU Positions (Implemented)

| RSU ID | Area | Lat | Lon | X (m) | Y (m) | Dist to next |
|---|---|---|---|---|---|---|
| rsu_00 | Old Gajuwaka | 17.685048 | 83.203902 | 3320.99 | 1846.97 | 0.9815 km |
| rsu_01 | New Gajuwaka | 17.693737 | 83.205536 | 3483.10 | 2810.81 | 0.9749 km |
| rsu_02 | BHPV Junction | 17.702503 | 83.205661 | 3484.99 | 3781.42 | 0.9137 km |
| rsu_03 | Nathayyapalem | 17.710709 | 83.205199 | 3425.37 | 4689.20 | 0.9123 km |
| rsu_04 | Sheelanagar | 17.718771 | 83.203601 | 3245.36 | 5579.66 | 0.6003 km |
| rsu_05 | Gopalapatnam | 17.723778 | 83.205720 | 3463.72 | 6136.56 | 2.0786 km |
| rsu_06 | NAD Junction | 17.732260 | 83.223209 | 5307.99 | 7097.46 | — |

**Total corridor: ~6.48 km**

---

## 10. Message Types (Implemented)

| Type | Code | Sender | Trigger |
|---|---|---|---|
| BEACON | 0 | OBU | Every 1 s (always) |
| JAM_DETECTED | 1 | OBU | Speed < 5 km/h (NS-3 side; placeholder in current build — speed=-1) |
| JAM_ALERT | 2 | RSU | >= 3 JAM_DETECTED in 30 s window |

**Wire format (VanetMsg, 113 bytes, packed):**
```
uint8_t  msg_type       (1 byte)
uint32_t sender_id      (4 bytes)
float    speed_kmh      (4 bytes)
float    pos_x          (4 bytes)
float    pos_y          (4 bytes)
char     edge[32]       (32 bytes)
char     alert_msg[64]  (64 bytes)
```

---

## 11. Output Files (Implemented)

| File | Written by | Purpose |
|---|---|---|
| `corridor/traffic_state.json` | `fetch_traffic.py` | Per-segment congestion from Google Maps or mock |
| `sim/sumo/routes.rou.xml` | `generate_routes.py` | 10 vehicle trips |
| `sim/bridge/mobility.ns2` | `traci_supervisor.py` | OBU waypoints for NS-3 |
| `sim/bridge/rsu_static.json` | `traci_supervisor.py` | 7 RSU XY positions for NS-3 |
| `sim/bridge/speed_log.json` | `traci_supervisor.py` | Per-vehicle speed + edge each second |
| `output/alerts.log` | NS-3 + jam_detector + rerouter | All VANET events timestamped |
| `output/jam_report.json` | `jam_detector.py` | Detected jam events |
| `output/reroute_log.json` | `rerouter.py` | Rerouting decisions |
| `output/results.xml` | SUMO | Per-vehicle trip stats |
| `output/vanet_summary_*.html` | `visualise.py` | Results dashboard |
| `output/vanet_gui_*.html` | `gui_visualiser.py` | Animated GUI |

---

## 12. Run Commands

### Mock Mode (offline demo, no API key needed)
```bash
cd /home/kali/vanet_claude
bash scripts/run_mock.sh
```
Opens: `output/vanet_gui_mock.html` and `output/vanet_summary_mock.html`

### Live Mode (real-time Google Maps data)
```bash
cd /home/kali/vanet_claude
bash scripts/run_live.sh
```
Opens: `output/vanet_gui_live.html` and `output/vanet_summary_live.html`

### Individual Phase Commands
```bash
# Phase 2a — fetch traffic
python3 scripts/fetch_traffic.py --mock        # mock
python3 scripts/fetch_traffic.py               # live

# Phase 2b — generate routes
python3 data_node/generate_routes.py --mock
python3 data_node/generate_routes.py

# Phase 3a — SUMO via TraCI
python3 sim/bridge/traci_supervisor.py --mock
python3 sim/bridge/traci_supervisor.py

# Phase 3b — NS-3
cd /home/kali/ns-3-dev
./ns3 run "vanet-scenario \
  --mobilityFile=/home/kali/vanet_claude/sim/bridge/mobility.ns2 \
  --rsuFile=/home/kali/vanet_claude/sim/bridge/rsu_static.json \
  --logFile=/home/kali/vanet_claude/output/alerts.log"
cd /home/kali/vanet_claude

# Phase 4b — rerouter
python3 data_node/rerouter.py --mock
python3 data_node/rerouter.py

# Phase 4a — jam detector
python3 data_node/jam_detector.py

# Phase 5 — visualise
python3 scripts/visualise.py --mode mock
python3 scripts/gui_visualiser.py --mode mock
```

---

## 13. Known Limitations

1. SUMO–NS-3 coupling is sequential (not real-time bidirectional) — NS-3 cannot feed back into the same SUMO run.
2. NS-3 speed values in beacons are placeholder (`-1.0`) — real detection relies on Python-side `speed_log.json`.
3. Friis propagation overestimates range in urban environments with buildings and multipath.
4. Full WAVE stack (IEEE 1609.3/1609.4 multi-channel) not implemented — plain UDP used.
5. 10 vehicles only — high-density channel saturation not modelled.
6. Single direction (south to north) on one corridor only.
7. rsu_05 → rsu_06 gap is 2.08 km — beyond single-hop 300 m range; no RSU relay covers this segment.
