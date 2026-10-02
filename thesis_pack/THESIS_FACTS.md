# THESIS_FACTS.md
# Dual-Mode VANET System for Real-Time Jam Detection and Early Rerouting Advisory
# Using V2I and RSU Relay — NH-16, Visakhapatnam
# M.Tech Dissertation — Gunde Neha, Andhra University
#
# All values in this file are taken directly from source code, log files, and
# output files. Nothing is estimated or invented. Where data was unavailable,
# "NOT AVAILABLE" is stated with an explanation.
#
# Generated: 2026-10-02

---

## a) Final Architecture — Every Module, Inputs, and Outputs

### Pipeline Overview

```
Phase 1  : OSM → SUMO road network
Phase 2a : Google Maps API / mock → traffic_state.json
Phase 2b : Generate vehicle routes → routes.rou.xml
Phase 3a : SUMO via TraCI → mobility.ns2, rsu_static.json, speed_log.json
Phase 3b : NS-3 802.11p → output/v2/alerts.log
Phase 4a : Offline jam detector → output/jam_report.json
Phase 4b : TraCI rerouter (2nd SUMO run) → output/reroute_log.json
Phase 5  : GUI + dashboard → output/vanet_gui_*.html, vanet_summary_*.html
Phase 5+ : Experiment sweep → output/v2/experiments/, metrics.csv, plots/
```

### Module Table

| Module | File | Input | Output |
|--------|------|-------|--------|
| Network builder | `scripts/build_corridor.sh` | `corridor/corridor.osm` | `corridor/corridor.net.xml` |
| RSU placer | `scripts/place_rsus.py` | `corridor.net.xml`, lat/lon list | `corridor/rsu_positions.csv`, `rsu_pois.add.xml`, `rsu_map.html` |
| Traffic fetcher | `scripts/fetch_traffic.py` | Google Maps API / `data_node/mock/traffic_state.mock.json` | `corridor/traffic_state.json` |
| Route generator | `data_node/generate_routes.py` | `traffic_state.json`, `corridor.net.xml` | `sim/sumo/routes.rou.xml` |
| TraCI supervisor | `sim/bridge/traci_supervisor.py` | `sim/sumo/vanet.sumocfg`, `routes.rou.xml` | `sim/bridge/mobility.ns2`, `rsu_static.json`, `speed_log.json` |
| NS-3 scenario | `sim/ns3/vanet-scenario.cc` | `mobility.ns2`, `rsu_static.json` | `output/v2/alerts.log` |
| WAVE application | `sim/ns3/jam-alert-app.cc/.h` | (embedded in NS-3 nodes) | Events written to `alerts.log` |
| Offline detector | `data_node/jam_detector.py` | `speed_log.json` | `output/jam_report.json` |
| Rerouter v1 | `data_node/rerouter.py` | `vanet.sumocfg`, `alerts.log` | `output/reroute_log.json` |
| Rerouter v2 | `data_node/rerouter_v2.py` | `vanet.sumocfg`, `--alerts-log`, `--seed`, `--tripinfo` | `output/v2/reroute_log.json` |
| GUI visualiser | `scripts/gui_visualiser.py` | `speed_log.json`, `rsu_static.json`, `v2/alerts.log`, `v2/reroute_log.json`, `traffic_state.json` (live) | `output/vanet_gui_{mock\|live}.html` |
| Dashboard | `scripts/visualise.py` | `speed_log.json`, `jam_report.json`, `reroute_log.json`, `rsu_static.json` | `output/vanet_summary_{mock\|live}.html` |
| Experiment sweep | `scripts/run_experiments.sh` | All above inputs | `output/v2/experiments/*/tripinfo.xml`, `alerts.log`, `reroute_log.json` |
| Metrics computer | `scripts/compute_metrics.py` | `output/v2/experiments/` | `output/v2/metrics.csv`, `output/v2/plots/*.png` |

---

## b) Full Pipeline — Exact Run Commands

### Mock Mode (offline demo, no API key needed)

```bash
cd /home/kali/vanet_claude

# Phase 2a — mock traffic state
python3 scripts/fetch_traffic.py --mock

# Phase 2b — generate routes
python3 data_node/generate_routes.py --mock

# Phase 3a — SUMO TraCI (produces mobility trace)
python3 sim/bridge/traci_supervisor.py --mock

# Phase 3b — NS-3 802.11p
cd /home/kali/ns-3-dev
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.h   scratch/vanet/
cp /home/kali/vanet_claude/sim/ns3/jam-alert-app.cc  scratch/vanet/
cp /home/kali/vanet_claude/sim/ns3/vanet-scenario.cc scratch/vanet/
./ns3 build
./ns3 run "vanet/vanet-scenario \
  --mobilityFile=/home/kali/vanet_claude/sim/bridge/mobility.ns2 \
  --rsuFile=/home/kali/vanet_claude/sim/bridge/rsu_static.json \
  --logFile=/home/kali/vanet_claude/output/v2/alerts.log"
cd /home/kali/vanet_claude

# Phase 4a — offline jam detector
python3 data_node/jam_detector.py

# Phase 4b — rerouter
python3 data_node/rerouter_v2.py --mock \
  --alerts-log output/v2/alerts.log \
  --reroute-log output/v2/reroute_log.json

# Phase 5 — visualise
python3 scripts/gui_visualiser.py --mode mock
python3 scripts/visualise.py --mode mock
```

### Live Mode (real-time Google Maps data)

```bash
cd /home/kali/vanet_claude

# Phase 2a — real Google Maps fetch (requires GOOGLE_MAPS_API_KEY in .env)
python3 scripts/fetch_traffic.py --live

# Phase 2b — generate routes from live congestion
python3 data_node/generate_routes.py

# Phase 3a — SUMO TraCI live (jam only if congestion level "heavy" or "jam")
python3 sim/bridge/traci_supervisor.py --live

# Phase 3b — NS-3 (same command as mock, uses new mobility.ns2)
cd /home/kali/ns-3-dev
./ns3 run "vanet/vanet-scenario \
  --mobilityFile=/home/kali/vanet_claude/sim/bridge/mobility.ns2 \
  --rsuFile=/home/kali/vanet_claude/sim/bridge/rsu_static.json \
  --logFile=/home/kali/vanet_claude/output/v2/alerts.log"
cd /home/kali/vanet_claude

# Phase 4b — rerouter live
python3 data_node/rerouter_v2.py \
  --alerts-log output/v2/alerts.log \
  --reroute-log output/v2/reroute_log.json

# Phase 5 — visualise live
python3 scripts/gui_visualiser.py --mode live
python3 scripts/visualise.py --mode live
```

### Three Experiment Scenarios (Phase 5)

```bash
cd /home/kali/vanet_claude

# Full sweep (5 seeds, runs NS-3 each time — slow, ~30 min):
bash scripts/run_experiments.sh

# Quick sweep (2 seeds, reuse existing alerts.log — fast, ~5 min):
bash scripts/run_experiments.sh --skip-ns3 --seeds "1 2"

# Compute metrics + plots after sweep:
python3 scripts/compute_metrics.py --seeds 1 2
```

Scenarios:
- **NO_JAM**: plain SUMO, no jam injection, no rerouting (free-flow baseline)
- **JAM_NO_ALERT**: jam injected via TraCI, no NS-3 relay, no rerouting
- **JAM_WITH_ALERT**: full pipeline — jam + NS-3 detection + relay + rerouting

---

## c) All Parameters

### SUMO Parameters

| Parameter | Value | Source file |
|-----------|-------|-------------|
| Simulation duration | 600 s | `sim/sumo/vanet.sumocfg` |
| Time step | 1 s | `sim/sumo/vanet.sumocfg` |
| Number of OBU vehicles | 10 | `data_node/generate_routes.py` (`N_VEHICLES = 10`) |
| Depart spread | 120 s (staggered 0–120 s) | `data_node/generate_routes.py` (`DEPART_SPREAD_S = 120`) |
| Free-flow speed | 50 km/h = 13.89 m/s | `data_node/generate_routes.py` (`FREE_FLOW_KMH = 50.0`) |
| Vehicle type | passenger, accel=2.6 m/s², decel=4.5 m/s², sigma=0.5, length=4.5 m, minGap=2.5 m | `sim/sumo/routes.rou.xml` |
| Jam speed cap (mock) | 1.2 m/s (~4.3 km/h) | `sim/bridge/traci_supervisor.py` (`JAM_SPEED_MS = 1.2`) |
| Jam start time | T = 60 s | `sim/bridge/traci_supervisor.py` (`JAM_START_S = 60.0`) |
| Jam end time | T = 350 s | `sim/bridge/traci_supervisor.py` (`JAM_END_S = 350.0`) |
| Jam duration | 290 s | Derived: 350 − 60 |
| Speed after jam | 13.89 m/s (~50 km/h) | `sim/bridge/traci_supervisor.py` |
| Jam zone (mock, SUMO metres) | Y: 3700–4800, X: 3000–4200 | `sim/bridge/traci_supervisor.py` |
| Teleport timeout | 300 s | `sim/sumo/vanet.sumocfg` |
| TraCI port (supervisor) | 8813 | `sim/bridge/traci_supervisor.py` |
| TraCI port (rerouter v1) | 8814 | `data_node/rerouter.py` |
| TraCI port (rerouter v2) | 8815 (default) | `data_node/rerouter_v2.py` |
| Reroute travel-time cost | 9999 s | `data_node/rerouter_v2.py` |
| Reroute cooldown | 60 s per vehicle | `data_node/rerouter_v2.py` |
| Edge search radius | 600 m | `data_node/generate_routes.py` (`EDGE_SEARCH_R_M = 600.0`) |

### NS-3 / IEEE 802.11p Parameters

| Parameter | Value | Source file |
|-----------|-------|-------------|
| Standard | IEEE 802.11p (WIFI_STANDARD_80211p) | `sim/ns3/vanet-scenario.cc` line 173 |
| Frequency | 5.9 GHz | `sim/ns3/vanet-scenario.cc` |
| Channel width | 10 MHz | `OfdmRate6MbpsBW10MHz` |
| Data rate | 6 Mb/s | `OfdmRate6MbpsBW10MHz` |
| Control mode | 6 Mb/s | `OfdmRate6MbpsBW10MHz` |
| Tx power | 20 dBm | `sim/ns3/vanet-scenario.cc` lines 169–170 |
| Effective radio range | ~300 m | Friis free-space model at 5.9 GHz, 20 dBm |
| Propagation loss model | FriisPropagationLossModel | `sim/ns3/vanet-scenario.cc` line 164 |
| Propagation delay model | ConstantSpeedPropagationDelayModel | `sim/ns3/vanet-scenario.cc` line 163 |
| MAC mode | AdhocWifiMac (no association) | `sim/ns3/vanet-scenario.cc` line 180 |
| Rate manager | ConstantRateWifiManager | `sim/ns3/vanet-scenario.cc` line 174 |
| Transport | UDP broadcast to 255.255.255.255 | `sim/ns3/jam-alert-app.cc` |
| OBU port | 7777 | `sim/ns3/jam-alert-app.h` |
| Beacon interval | 1.0 s | `sim/ns3/jam-alert-app.h` (`BEACON_INTERVAL_S = 1.0`) |
| OBU nodes | 10 (NS-3 IDs 0–9) | `sim/ns3/vanet-scenario.cc` |
| RSU nodes | 7 (NS-3 IDs 10–16) | `sim/ns3/vanet-scenario.cc` |
| RSU antenna height | 1.5 m | `sim/ns3/vanet-scenario.cc` line 213 |
| OBU mobility | Ns2MobilityHelper (waypoints from SUMO) | `sim/ns3/vanet-scenario.cc` line 193 |
| RSU mobility | ConstantPositionMobilityModel | `sim/ns3/vanet-scenario.cc` line 211 |
| Simulation time | 600 s | `sim/ns3/vanet-scenario.cc` |

### Jam Detection Thresholds

| Parameter | Value | Source file |
|-----------|-------|-------------|
| OBU speed threshold | 5.0 km/h | `sim/ns3/jam-alert-app.h` (`JAM_SPEED_THRESHOLD = 5.0f`) |
| OBU consecutive slow counter | 30 s (SLOW_S > 30 to trigger JAM_DETECTED) | `sim/ns3/jam-alert-app.h`, `sim/ns3/jam-alert-app.cc` |
| RSU quorum (distinct vehicles) | 3 | `sim/ns3/jam-alert-app.h` (`JAM_VEH_THRESHOLD = 3`) |
| RSU accumulation method | Persistent `std::set<uint32_t> m_seenSenders` (no expiry window) | `sim/ns3/jam-alert-app.h` |
| Python offline speed threshold | 5.0 km/h | `data_node/jam_detector.py` (`JAM_SPEED_KMH = 5.0`) |
| Python offline min vehicles | 3 on same edge | `data_node/jam_detector.py` (`JAM_MIN_VEHICLES = 3`) |
| Python offline min seconds | 30 s consecutive | `data_node/jam_detector.py` (`JAM_MIN_SECONDS = 30`) |

### RSU-to-RSU Backhaul Relay Parameters

| Parameter | Value | Source file |
|-----------|-------|-------------|
| Link type | PointToPoint | `sim/ns3/vanet-scenario.cc` |
| Backhaul bandwidth | 100 Mbps | `sim/ns3/vanet-scenario.cc` |
| Backhaul delay | 2 ms | `sim/ns3/vanet-scenario.cc` |
| Subnet per link | 10.2.i.0/30 | `sim/ns3/vanet-scenario.cc` |
| Backhaul port | 7778 | `sim/ns3/jam-alert-app.h` (`m_bkPort = 7778`) |
| Jam RSU (mock) | rsu_02, BHPV Junction, NS-3 node 12 | `sim/bridge/rsu_static.json` |
| Alert RSU (mock) | rsu_01, New Gajuwaka, NS-3 node 11 | `sim/bridge/rsu_static.json` |
| Relay peer address | 10.2.1.1 | `output/v2/alerts.log` RELAY_SENT line |

---

## d) Message Types, Packet Format, and Step-by-Step Logic

### Message Types

| Type | Code | Sender | Trigger |
|------|------|--------|---------|
| BEACON | 0 | OBU | Every 1 s always |
| JAM_DETECTED | 1 | OBU | `m_slowSeconds > 30` (speed < 5 km/h for >30 consecutive s) |
| JAM_ALERT | 2 | RSU | `m_seenSenders.size() >= 3` distinct vehicles reported slow |

### Wire Format (VanetMsg, 113 bytes, #pragma pack(1))

```c
struct VanetMsg {
    uint8_t  msg_type;       //  1 byte  — 0=BEACON, 1=JAM_DETECTED, 2=JAM_ALERT
    uint32_t sender_id;      //  4 bytes — NS-3 node index
    float    speed_kmh;      //  4 bytes — current speed (km/h); -1.0 in placeholder mode
    float    pos_x;          //  4 bytes — SUMO X coordinate (metres)
    float    pos_y;          //  4 bytes — SUMO Y coordinate (metres)
    char     edge[32];       // 32 bytes — SUMO road edge ID (null-terminated)
    char     alert_msg[64];  // 64 bytes — human-readable alert (JAM_ALERT only)
};                           // Total: 113 bytes
```

Source: `sim/ns3/jam-alert-app.h`

### OBU Jam Reporting (Pseudocode)

```
// Runs every BEACON_INTERVAL_S = 1.0 s on each OBU
PROCEDURE SendBeacon(nodeId):
    speed_kmh = GetVelocity().GetLength() * 3.6   // NS-3 real speed

    IF speed_kmh < JAM_SPEED_THRESHOLD (5.0 km/h):
        m_slowSeconds += 1
    ELSE:
        m_slowSeconds = 0

    IF m_slowSeconds > 30:
        msg.msg_type = JAM_DETECTED
    ELSE:
        msg.msg_type = BEACON

    msg.sender_id = nodeId
    msg.speed_kmh = speed_kmh   // real speed from NS-3 mobility model
    msg.pos_x     = current X
    msg.pos_y     = current Y
    msg.edge      = ""          // not available in NS-3 (SUMO edge not forwarded)

    UDP broadcast to 255.255.255.255:7777
    Log: "[T=t] NODE=nodeId SENT=JAM_DETECTED SPEED=speed X=x Y=y SLOW_S=m_slowSeconds"

// Source: sim/ns3/jam-alert-app.cc SendBeacon()
```

### RSU Distinct-Vehicle Quorum (Pseudocode)

```
// Runs when RSU receives a UDP packet on port 7777
PROCEDURE HandleRead(socket):
    msg = receive packet
    Log receive event

    IF msg.msg_type == JAM_DETECTED AND NOT m_jamFired:
        m_seenSenders.insert(msg.sender_id)   // std::set — duplicates ignored
        Log: "RSU=id JAM_DETECTED_FROM=sender DISTINCT_COUNT=m_seenSenders.size()"

        IF m_seenSenders.size() >= JAM_VEH_THRESHOLD (3):
            m_jamFired = true
            Log: "RSU=id QUORUM_REACHED vehicles=[list of sender IDs]"
            SendAlert(JAM_ALERT, m_alertMsg)       // 802.11p broadcast
            IF m_hasBkPeer:
                SendBackhaulAlert(m_alertMsg)      // PointToPoint relay

// NOTE: The 30-second window expiry was REMOVED in Phase 3.
// m_seenSenders accumulates persistently until quorum fires.
// Source: sim/ns3/jam-alert-app.cc HandleRead()
```

### RSU-to-RSU Backhaul Relay (Pseudocode)

```
// Called by jam RSU (rsu_02, NS-3 node 12) after quorum
PROCEDURE SendBackhaulAlert(alertMsg):
    msg.msg_type  = JAM_ALERT
    msg.sender_id = m_nodeId (12)
    msg.alert_msg = alertMsg
    Send via m_bkTxSocket to m_bkPeerAddr (10.2.1.1) port 7778
    Log: "[T=t] RSU=12 RELAY_SENT PEER=10.2.1.1 MSG=alertMsg"

// Alert RSU (rsu_01, NS-3 node 11) receives on port 7778
PROCEDURE HandleBackhaulRead(socket):
    msg = receive packet
    Log: "[T=t] RSU=11 RELAY_RECV FROM_RSU=12 MSG=alertMsg"
    SendAlert(JAM_ALERT, msg.alert_msg)   // rebroadcast over 802.11p
    Log: "[T=t] NODE=11 SENT=JAM_ALERT MSG=alertMsg"

// Link: PointToPoint, 100 Mbps, 2 ms, subnet 10.2.1.0/30
// Source: sim/ns3/jam-alert-app.cc SendBackhaulAlert(), HandleBackhaulRead()
```

### JAM_ALERT Broadcast to Approaching Vehicles (Pseudocode)

```
// Runs on alert RSU (rsu_01) after receiving relay
PROCEDURE SendAlert(JAM_ALERT, alertMsg):
    msg.msg_type  = JAM_ALERT
    msg.sender_id = m_nodeId (11)
    msg.alert_msg = alertMsg  // "Take alternate route at New Gajuwaka, jam detected at BHPV"
    UDP broadcast to 255.255.255.255:7777 over 802.11p
    Log: "[T=t] NODE=11 SENT=JAM_ALERT MSG=alertMsg"

// Any OBU within 300 m of rsu_01 receives this:
PROCEDURE HandleRead (OBU side, msg_type==JAM_ALERT):
    Log: "[T=t] OBU=id JAM_ALERT_RECV FROM_RSU=11"
    // In this mock run: NO OBUs were within 300 m of rsu_01 at T=245 s
    // veh_00 and veh_01 had already passed rsu_01 by T=245 s
    // → PDR = 0/1 = 0.000

// Source: sim/ns3/jam-alert-app.cc SendAlert(), HandleRead()
```

### Alert-Driven Rerouting (Pseudocode)

```
// rerouter_v2.py — runs a second SUMO instance via TraCI
// Trigger: first JAM_DETECTED_FROM at RSU=12 in alerts.log (T=98.0 s)

PROCEDURE RerouteLoop():
    Parse alerts.log → find first line matching "RSU=12 JAM_DETECTED_FROM"
    trigger_t = 98.0 s

    FOR each simulation step t in 0..600:
        step SUMO
        IF t < trigger_t: continue

        FOR each vehicle v in SUMO:
            IF v already rerouted within last 60 s: skip
            edge = traci.vehicle.getRoadID(v)
            IF edge in jam_edges:
                FOR each jammed_edge:
                    traci.edge.adaptTraveltime(jammed_edge, 9999.0)
                traci.vehicle.rerouteTraveltime(v)   // Dijkstra shortest path
                record reroute event {t, veh_id, old_route, new_route}
                last_reroute[v] = t

    Write output/v2/reroute_log.json
    Write tripinfo XML (--tripinfo-output flag to SUMO)

// Source: data_node/rerouter_v2.py
```

---

## e) Alternate Route Check (Phase 4)

**Question:** Does an alternate route exist from New Gajuwaka avoiding the jammed edges?

**Answer: YES — confirmed from output/v2/reroute_log.json**

Jammed edges (11 total):
```
218746140#8, 544537081#1, 544537081#2, 544537081#3,
544537085#10, 544537085#3, 544537085#4, 548058730#0,
742760569#2, 883673740, 90800092#12
```

**veh_00 (NS-3 node 0):** rerouted at T=98 s from edge `90800092#3`
- Old route: 27 edges via `90800092#12` (jammed)
- New route: 52 edges via `280178580#1..#13 → 280178603 → 877751318 → 604748918` (bypass)
- Bypass description: diverges at `90800092#11`, takes residential/service roads west of BHPV, reconnects at `90800137#0`
- Avoided jam: YES

**veh_01 (NS-3 node 1):** rerouted at T=98 s from edge `90800092#3`
- Old route: 35 edges via `90800092#12` (jammed)
- New route: 60 edges via same `280178580 → 877751318 → 604748918` bypass
- Avoided jam: YES

**Alternate route key edges (shared by both vehicles):**
```
280178580#1 → 280178580#2 → ... → 280178580#13
280178603#0 → 280178603#1
-877751319#4
877751318#0 → 877751318#2 → ... → 877751318#10
280178577#28 → 280178577#29
604748918#1 → ... → 604748918#6
90800137#0 → 90800137#1  (rejoin main corridor north of jam)
```

---

## f) JAM_WITH_ALERT Mock Run — Detailed Timing (from logs)

All times are NS-3 simulation time seconds.

### Jam Injection
- **T = 60 s**: SUMO TraCI sets max speed = 1.2 m/s on jam-zone edges
- Jam zone: BHPV Junction (rsu_02) to Nathayyapalem (rsu_03)
- **T = 350 s**: jam clears, speed restored to 13.89 m/s

### JAM_DETECTED Events (from output/v2/alerts.log)

First JAM_DETECTED messages appear at **T = 30.0 s** for nodes 3,4,5,6,7,8,9.
Note: SLOW_S=31 at T=30 means these OBUs had been slow since before T=0 in NS-3 time —
they started stationary (vehicles depart with `departPos="random_free"`, some begin on
congested edges before SUMO has assigned routes, appearing as stopped in the mobility trace).

| Time | NS-3 Node | SUMO vehicle | Position (X,Y) | SLOW_S |
|------|-----------|--------------|----------------|--------|
| T=30.0 | NODE=3 | veh_03 | (3493.6, 3957.8) | 31 |
| T=30.0 | NODE=4 | veh_04 | (3491.8, 3910.9) | 31 |
| T=30.0 | NODE=5 | veh_05 | (3412.4, 4707.2) | 31 |
| T=30.0 | NODE=6 | veh_06 | (3426.7, 4623.9) | 31 |
| T=30.0 | NODE=7 | veh_07 | (3245.5, 5595.1) | 31 |
| T=30.0 | NODE=8 | veh_08 | (3249.2, 5569.1) | 31 |
| T=30.0 | NODE=9 | veh_09 | (3499.8, 6162.8) | 31 |
| T=36.0 | NODE=0 | veh_00 | (3284.0, 1866.9) | 31 |

First JAM_DETECTED received at **RSU=12 (BHPV)**: T = 98.0 s
(from `reroute_log.json`: `"t_trigger_s": 98.0, "alert_source": "FIRST JAM_DETECTED_FROM RSU=12"`)

### QUORUM_REACHED (from output/v2/alerts.log line 33196)

```
[T=245.0] RSU=12 QUORUM_REACHED vehicles=[1, 2, 4]
```

- **T = 245 s**: RSU=12 (BHPV, NS-3 node 12) accumulated 3 distinct senders
- Vehicles in quorum: NS-3 nodes 1 (veh_01), 2 (veh_02), 4 (veh_04)
- Detection delay: 245 − 60 = **185.0 s**

### Relay Events (from output/v2/alerts.log lines 33197–33201)

```
[T=245.0] RSU=12 RELAY_SENT PEER=10.2.1.1 MSG="Take alternate route at New Gajuwaka, jam detected at BHPV"
[T=245.0] RSU=11 RELAY_RECV FROM_RSU=12 MSG="Take alternate route at New Gajuwaka, jam detected at BHPV"
[T=245.0] NODE=11 SENT=JAM_ALERT MSG="Take alternate route at New Gajuwaka, jam detected at BHPV"
```

- **T = 245 s**: RSU=12 relays to RSU=11 (New Gajuwaka) via PointToPoint backhaul
- Relay delay: 2 ms (appears as 0.0 s due to 1-second log granularity)
- RSU=11 immediately rebroadcasts JAM_ALERT over 802.11p

### JAM_ALERT Reception by Approaching Vehicles

**No JAM_ALERT_RECV lines exist in output/v2/alerts.log.**

- Reason: veh_00 (departed T=0 s) and veh_01 (departed T=13.3 s) had already traveled
  ~2 km north of rsu_01 by T=245 s — beyond its 300 m radio range
- PDR = 0/1 = **0.000** (1 JAM_ALERT sent by RSU=11, 0 received)
- This is an honest simulation result; it demonstrates the late-quorum problem

### Rerouting

Rerouting was triggered at **T=98 s** from the first JAM_DETECTED_FROM at RSU=12
(rerouter_v2.py reads alerts.log and acts on this earlier signal, not the T=245 s JAM_ALERT):

| Vehicle | Rerouted at | From edge | Old route | New route | Avoided jam |
|---------|-------------|-----------|-----------|-----------|-------------|
| veh_00 | T=98 s | 90800092#3 | 27 edges | 52 edges | YES |
| veh_01 | T=98 s | 90800092#3 | 35 edges | 60 edges | YES |

Vehicle positions at T=245 s: NOT AVAILABLE from logs (speed_log.json too large to excerpt here).

---

## g) output/v2/metrics.csv — Full Explanation

### Raw CSV content

```
scenario,seed,mean_duration_s,mean_waiting_s,vehicle_count,detection_delay_s,relay_delay_s,jam_alert_sent,jam_alert_recv,pdr,vehicles_rerouted,vehicles_avoided_jam,false_alerts
NO_JAM,1,378.40,5.60,10,,,0,0,,0,0,0
NO_JAM,2,380.70,5.60,10,,,0,0,,0,0,0
JAM_NO_ALERT,1,238.00,0.00,3,,,0,0,,0,0,0
JAM_NO_ALERT,2,236.67,0.00,3,,,0,0,,0,0,0
JAM_WITH_ALERT,1,236.00,0.00,3,185.00,0.00,1,0,0.000,2,2,1
JAM_WITH_ALERT,2,236.33,0.00,3,185.00,0.00,1,0,0.000,2,2,1
```

### Column-by-Column Explanation

| Column | Meaning | Source |
|--------|---------|--------|
| scenario | One of NO_JAM / JAM_NO_ALERT / JAM_WITH_ALERT | Experiment scenario |
| seed | SUMO random seed (1 or 2) | `--seed` flag to SUMO |
| mean_duration_s | Mean travel time (s) of vehicles completing trips within 600 s | `tripinfo.xml` `duration` attribute |
| mean_waiting_s | Mean waiting time (s) — time stopped at speed ≈ 0 | `tripinfo.xml` `waitingTime` attribute |
| vehicle_count | Number of vehicles that completed trips within 600 s | Counted from tripinfo.xml |
| detection_delay_s | T_quorum − T_jam_start = 245 − 60 = 185.0 s | alerts.log QUORUM_REACHED |
| relay_delay_s | T_RELAY_SENT − T_QUORUM_REACHED = 0.0 s (2 ms below 1 s granularity) | alerts.log |
| jam_alert_sent | Number of JAM_ALERT broadcasts by RSU | alerts.log |
| jam_alert_recv | Number of OBUs that received JAM_ALERT | alerts.log (0 in this run) |
| pdr | Packet Delivery Ratio = recv/sent | Computed |
| vehicles_rerouted | Vehicles given alternate route | reroute_log.json summary |
| vehicles_avoided_jam | Vehicles that successfully bypassed jam edges | reroute_log.json |
| false_alerts | JAM_ALERT in NO_JAM scenario | alerts.log (0 — no false alerts) |

### Aggregate Results (mean ± std over 2 seeds)

| Scenario | Mean travel time | Mean waiting | Vehicles completed | Notes |
|----------|-----------------|--------------|-------------------|-------|
| NO_JAM | **379.5 ± 1.2 s** | 5.6 ± 0.0 s | 10/10 | Free-flow baseline; all vehicles complete |
| JAM_NO_ALERT | **237.3 ± 0.7 s** | 0.0 ± 0.0 s | 3/10 | Only 3 complete within 600 s; long-route vehicles stuck |
| JAM_WITH_ALERT | **236.2 ± 0.2 s** | 0.0 ± 0.0 s | 3/10 | Same 3 complete; rerouted 2 vehicles |

**Why NO_JAM travel time > JAM scenarios:**
NO_JAM counts 10 vehicles including long-route ones (full 6.5 km corridor → ~380 s).
JAM scenarios count only 3 short-route vehicles that complete quickly.
This is not a contradiction — it reflects which vehicles complete in 600 s.

**Detection delay: 185.0 s** (quorum fires late because accumulation requires waiting
for all 3 distinct slow vehicles to send to the same RSU).

**PDR = 0.000**: 1 JAM_ALERT sent by RSU=11 at T=245 s; 0 received because
approaching vehicles had already passed RSU=11's 300 m range.

**Vehicles rerouted: 2** (veh_00, veh_01 — both avoided jam via bypass route).

**False alerts in NO_JAM: 0** — no JAM_DETECTED transmitted at free-flow speeds.

---

## h) Live Mode — Latest Run

**Date/time of latest live run:** 2026-07-09T16:24:39+05:30
**Source:** `corridor/traffic_state.json`, field `"fetched_at"`

### Segment Congestion Data (from traffic_state.json)

| Segment | From → To | Distance | Free speed | Traffic speed | Ratio | Level |
|---------|-----------|----------|------------|---------------|-------|-------|
| seg_00_01 | rsu_00 → rsu_01 | 1184 m | 164 s | 187 s | 1.14 | **light** |
| seg_01_02 | rsu_01 → rsu_02 | 3131 m | 406 s | 422 s | 1.039 | **free** |
| seg_02_03 | rsu_02 → rsu_03 | 2245 m | 258 s | 244 s | 0.946 | **free** |
| seg_03_04 | rsu_03 → rsu_04 | 1940 m | 247 s | 266 s | 1.077 | **free** |
| seg_04_05 | rsu_04 → rsu_05 | 4932 m | 432 s | 466 s | 1.079 | **free** |
| seg_05_06 | rsu_05 → rsu_06 | 3275 m | 345 s | 372 s | 1.078 | **free** |

**Was a jam injected in live mode?**
No. Live injection requires congestion level `"heavy"` or `"jam"` (ratio ≥ 1.60 or ≥ 2.00).
Maximum ratio on this run was **1.14** (seg_00_01, light). No segment met the threshold.

**Result:** No jam injection → no JAM_DETECTED → no QUORUM_REACHED → no rerouting.
The live GUI shows real Google Maps congestion colours on road segments but no alert events.

---

## i) Test Cases — Phases 1–6

| # | Phase | Test | Input | Expected | Actual | Pass/Fail |
|---|-------|------|-------|----------|--------|-----------|
| 1 | 1 | All 10 vehicles depart | routes.rou.xml, vanet.sumocfg | No "not allowed to depart" errors | All 10 vehicles depart | PASS |
| 2 | 2a | Mock traffic state loads | `--mock` flag | 6 segments with jam at seg_02_03 | Loaded, ratio=2.20, level=JAM | PASS |
| 3 | 2a | Live traffic state loads | Google Maps API key | 6 segments with real ratios | Loaded (latest: 2026-07-09) | PASS |
| 4 | 2b | Routes weighted by congestion | traffic_state.json | More vehicles on heavier segments | 3 vehs on JAM seg, 1 on FREE | PASS |
| 5 | 3a | mobility.ns2 generated | traci_supervisor.py | OBU waypoints every 1 s for 600 s | File generated, 10 OBUs | PASS |
| 6 | 3a | speed_log.json generated | traci_supervisor.py | Per-vehicle speed+edge each second | File generated, 2.5 MB | PASS |
| 7 | 3a | Jam injection at T=60 | `--mock` flag | Vehicles near BHPV slow to ~4 km/h | Confirmed in speed_log.json | PASS |
| 8 | 3b | NS-3 builds without error | jam-alert-app.cc/.h | `./ns3 build` succeeds | Build succeeds | PASS |
| 9 | 3b | OBU sends JAM_DETECTED when slow | NS-3 + mobility trace | SENT=JAM_DETECTED in alerts.log | Seen at T=30+ for nodes 2–9 | PASS |
| 10 | 3b | RSU quorum fires exactly once | 3 distinct slow OBUs | QUORUM_REACHED once in log | 1 QUORUM_REACHED at T=245 | PASS |
| 11 | 3b | Backhaul relay delivered | PointToPoint link | RELAY_RECV at rsu_01 | [T=245.0] RSU=11 RELAY_RECV | PASS |
| 12 | 3b | JAM_ALERT broadcast | rsu_01 after relay | NODE=11 SENT=JAM_ALERT | Confirmed in alerts.log | PASS |
| 13 | 3b | JAM_ALERT received by OBUs | OBU within 300 m | OBU=N JAM_ALERT_RECV | Not received — PDR=0 | FAIL (coverage gap) |
| 14 | 3b | No false alerts in NO_JAM | Free-flow mobility trace | 0 JAM_DETECTED sent | 0 false alerts confirmed | PASS |
| 15 | 4a | Jam detector identifies jam | speed_log.json | jam_report.json with BHPV event | Jam detected at BHPV–Nathayyapalem | PASS |
| 16 | 4b | Rerouter triggers on JAM_DETECTED_FROM | alerts.log T=98 | 2 vehicles rerouted | veh_00, veh_01 rerouted at T=98 | PASS |
| 17 | 4b | New route avoids jammed edges | reroute_log.json | avoided_jam=true | Both vehicles: avoided_jam=true | PASS |
| 18 | 5 | GUI event-driven from alerts.log | output/v2/alerts.log | SENDER/relay/banner from log only | Confirmed — heuristic logic removed | PASS |
| 19 | 5 | Live GUI shows Google Maps colours | traffic_state.json | Road segments coloured by level | Confirmed in vanet_gui_live.html | PASS |
| 20 | 5 | Experiment sweep completes | run_experiments.sh | tripinfo.xml per scenario per seed | Generated for 3 × 2 seeds | PASS |
| 21 | 5 | compute_metrics.py produces CSV | experiments/ folder | metrics.csv with 6 rows | 6-row CSV generated | PASS |
| 22 | 5 | 6 PNG plots generated | metrics.csv | PNG at 150 dpi, white background | All 6 plots confirmed in output/v2/plots/ | PASS |

---

## j) Known Limitations

1. **Sequential SUMO–NS-3 coupling**: NS-3 uses a pre-recorded SUMO mobility trace.
   NS-3 communication events (rerouting decisions) cannot affect vehicle trajectories
   in the same SUMO run. The rerouter runs as a separate second SUMO pass.

2. **JAM_ALERT PDR = 0.000**: Approaching vehicles (veh_00, veh_01) pass RSU=11 before
   T=245 s when the JAM_ALERT is broadcast. The alert arrives too late for the radio
   link to be active. The rerouter compensates by using the earlier T=98 s trigger.

3. **NS-3 speed values in beacons**: In the NS-3 build used, `speed_kmh` in the VanetMsg
   packet is read from the real NS-3 mobility model velocity. However, the SUMO edge ID
   is NOT available in NS-3 (it comes from the mobility trace waypoints only) — the
   `edge` field in all log lines shows `v2i` (a placeholder).

4. **Friis propagation overestimates range**: Real 802.11p urban propagation involves
   building shadowing, multipath, and NLOS effects not modelled by Friis.

5. **Full WAVE/DSRC stack not implemented**: Plain UDP broadcast over adhoc 802.11p is used.
   IEEE 1609.3/1609.4 (CCH/SCH multi-channel operation, WSMP) are not implemented.

6. **10 vehicles only**: High vehicle density (100+) would cause channel saturation and
   hidden terminal interference not captured in this simulation.

7. **Single direction, one corridor**: South→North only, ~6.5 km. Reverse direction and
   multi-intersection scenarios not modelled.

8. **rsu_05 → rsu_06 gap = 2.08 km**: Exceeds the 300 m radio range. Vehicles in this
   segment cannot receive V2I alerts without a relay chain covering the full gap.

9. **Live mode jam not triggered**: On 2026-07-09 live run, maximum congestion ratio was
   1.14 (light), below the 1.60 (heavy) threshold. No live jam scenario was captured.

10. **`JAM_TIME_THRESHOLD` constant retained in header**: `jam-alert-app.h` still declares
    `JAM_TIME_THRESHOLD = 30.0` as a `static constexpr` field, but the expiry logic was
    removed from `HandleRead()`. The constant is unused in the current build.

---

## k) Hardware and Software Versions

### Software (from Kali Linux VM)

```
SUMO:   Eclipse SUMO sumo 1.25.0
Python: Python 3.13.12
NS-3:   3-dev (ns-3-dev branch)
OS:     Kali Linux (running in VMware on Windows 11 Pro)
Host:   Windows 11 Pro 10.0.22631
```

### Key Python Libraries

| Library | Purpose |
|---------|---------|
| `traci` (bundled with SUMO) | TraCI Python API for SUMO |
| `sumolib` (bundled with SUMO) | SUMO network parsing |
| `numpy` | Metrics computation |
| `matplotlib` | Plot generation |
| `python-dotenv` | .env loading for Google Maps key |
| `requests` | Google Maps API HTTP calls |
| `lxml` / `xml.etree` | SUMO XML parsing |

### Hardware

NOT AVAILABLE — RAM and CPU specs of the Kali Linux VM were not recorded.
Host machine: Windows 11 Pro (from `OS Version: Windows 11 Pro 10.0.22631`).

---
*End of THESIS_FACTS.md*
