# Phase 3: How SUMO and NS-3 are bridged

The core problem is that SUMO and NS-3 are two completely separate simulators written in different languages (Python/C vs C++). They can't talk to each other in real time in our setup, so Phase 3 uses a **file-based sequential bridge**.

---

## The two-step pipeline

```
Step 1: SUMO runs           Step 2: NS-3 runs
─────────────────           ─────────────────
traci_supervisor.py         vanet-scenario.cc
      │                           │
      │  controls SUMO            │  reads files
      ▼                           ▼
  SUMO engine            mobility.ns2
  (600 seconds)          rsu_static.json
      │                  speed_log.json
      │ writes
      ▼
  3 bridge files
```

SUMO must **finish completely** before NS-3 starts. NS-3 replays what SUMO recorded.

---

## Step 1 — traci_supervisor.py controls SUMO

**TraCI** (Traffic Control Interface) is SUMO's Python API. It lets Python reach inside a running SUMO simulation and read/write anything.

```
Python script
    │
    │  traci.start(sumo_cmd)        ← launches SUMO as a subprocess
    │
    │  while time < 600:
    │      traci.simulationStep()   ← advances SUMO by 1 second
    │      for each vehicle:
    │          getPosition()        ← reads X, Y in SUMO metres
    │          getSpeed()           ← reads speed in m/s
    │          getRoadID()          ← reads which road edge it's on
    │
    │  traci.close()                ← SUMO shuts down
```

Each second, for each of the 10 OBU vehicles, the script records a tuple:
```
(time=45.0, x=3450.2, y=4210.7, speed=8.3, edge="gneE_12")
```

By the end, `fcd` (floating car data) is a dictionary of **600 snapshots per vehicle**.

---

## Step 2 — Three bridge files are written

### File 1: `mobility.ns2`
This is the **most important file**. NS-3's `Ns2MobilityHelper` reads this exact format.

```
$node_(0) set X_ 3320.5     ← initial position of vehicle 0
$node_(0) set Y_ 1847.2
$node_(0) set Z_ 0.00

$ns_ at 1.0 "$node_(0) setdest 3325.1 1851.6 4.80"   ← at t=1s, move here at 4.8 m/s
$ns_ at 2.0 "$node_(0) setdest 3330.4 1856.1 5.12"
$ns_ at 3.0 "$node_(0) setdest 3336.8 1861.9 6.74"
...600 lines per vehicle...
```

NS-3 reads this file and **replays every vehicle's exact SUMO path** — same road, same speed, same timing. This is how SUMO's traffic microsimulation becomes NS-3's node mobility.

### File 2: `rsu_static.json`
RSUs don't move. This file gives NS-3 their fixed positions:

```json
[
  { "ns3_node_id": 10, "rsu_id": "rsu_00", "x_m": 3320.99, "y_m": 1846.97 },
  { "ns3_node_id": 11, "rsu_id": "rsu_01", "x_m": 3483.10, "y_m": 2810.81 },
  ...7 RSUs...
]
```

NS-3 places these nodes with `ConstantPositionMobilityModel` — they never move.

### File 3: `speed_log.json`
This is for Phase 4, not NS-3:

```json
{
  "145": { "veh_00": {"speed_kmh": 4.1, "edge": "gneE_05", "x": 3484.9, "y": 3781.4} },
  "146": { "veh_00": {"speed_kmh": 3.8, "edge": "gneE_05", ...} },
  ...
}
```

One entry per second per vehicle — the jam detector will scan this for `< 5 km/h for > 30 s`.

---

## Step 3 — NS-3 replays the simulation

```
vanet-scenario.cc:
  1. Creates 10 OBU nodes + 7 RSU nodes  (17 total)
  2. Attaches 802.11p radio to all nodes  (WifiHelper, 5.9 GHz, 300m range)
  3. Assigns IP addresses                 (10.1.x.x subnet)
  4. Loads mobility.ns2 → OBUs move      (Ns2MobilityHelper)
  5. Loads rsu_static.json → RSUs fixed  (ConstantPositionMobilityModel)
  6. Installs JamAlertApp on all nodes
  7. Runs 600-second simulation
```

During the simulation, `JamAlertApp` fires every 1 second on each OBU:
- "Am I within 300m radio range of an RSU or another OBU?"
- If yes → NS-3's Friis propagation model delivers the UDP packet
- RSU receives it → logs `RECV=BEACON FROM=3` to `alerts.log`

---

## What each simulator actually contributes

| SUMO | NS-3 |
|---|---|
| Realistic traffic microsimulation | Realistic 802.11p radio physics |
| Vehicle follows road geometry | Friis propagation + 300m range |
| Speed obeys acceleration/deceleration | Packet delivery probability |
| Jam forms naturally from congestion | RSU hears which vehicles are nearby |
| Writes `speed_log.json` | Writes `alerts.log` |

---

## Why sequential and not real-time?

Real-time coupling (SUMO sends one step → NS-3 simulates → SUMO advances) is technically possible but complex. For a demonstration project, sequential coupling is:
- **Simpler** — no IPC, no synchronization bugs
- **Identical output** — SUMO's physics don't depend on NS-3 radio
- **Reproducible** — re-run NS-3 on the same trace without re-running SUMO

The guide sees the same physical reality simulated twice — once for traffic, once for radio — then Phase 4 combines the results.

---

## Files produced by Phase 3

| File | Location | Used by |
|---|---|---|
| `mobility.ns2` | `sim/bridge/` | NS-3 Ns2MobilityHelper |
| `rsu_static.json` | `sim/bridge/` | NS-3 ConstantPositionMobilityModel |
| `speed_log.json` | `sim/bridge/` | Phase 4 jam_detector.py |
| `alerts.log` | `output/` | Phase 5 visualisation |

## Scripts in Phase 3

| Script | Language | Role |
|---|---|---|
| `sim/bridge/traci_supervisor.py` | Python | Runs SUMO, collects FCD, writes bridge files |
| `sim/ns3/vanet-scenario.cc` | C++ | NS-3 main: radio setup, mobility, app install |
| `sim/ns3/jam-alert-app.h` | C++ | WAVE app header (MsgType, VanetMsg, JamAlertApp) |
| `sim/ns3/jam-alert-app.cc` | C++ | WAVE app impl: beacon broadcast, RSU aggregation |
