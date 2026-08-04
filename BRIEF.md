# VANET Project — Spec & Decisions

**Purpose of this file:** Single source of truth for the project's goal,
locked decisions, architecture, and phase plan. Update this file when a
decision changes; treat it as the contract.

**Working rule:** Work strictly phase by phase. Pause after each phase for the
user to say "go" before starting the next. No batch-dumping multi-phase code.

---

## 1. Goal

Build a working VANET prototype that:

1. Pulls real-time traffic data for a Visakhapatnam corridor via Google Maps APIs.
2. Picks 10 vehicles moving on roads in that corridor.
3. Simulates V2V (OBU↔OBU) and V2I (OBU↔RSU) communication, with RSUs every 1 km.
4. Detects traffic jams and emits alerts of the form:
   `"Traffic jam at <XYZ area>, take U-turn at <ABC area>"`
5. Sends those alerts from stuck vehicles and RSUs to upstream vehicles, which
   then take a U-turn before reaching the jam.
6. Visually distinguishes vehicles that are stuck, moving, or rerouting.

Deliverable context: this is an **academic presentation project**. Optimize for
clarity, clean comments, and a watchable demo over cleverness.

---

## 2. Locked decisions (do not relitigate without asking)

| # | Decision | Value |
|---|----------|-------|
| Corridor | NH-16 Gajuwaka → Sheelanagar → NAD Junction (~8 km, congestion-prone, multiple realistic U-turn cuts) |
| Host vs target | Files authored on Windows at `C:\Users\NEHAGUNDE\Desktop\vanet_claude\`. Mounted into Kali at `/home/kali/vanet_claude/` via VMware shared folder (`open-vm-tools` + `vmhgfs-fuse`, single-share custom mount). SUMO + NS-3 run inside Kali. Claude Code runs from Windows; user pastes Kali run output back into chat when needed. |
| SUMO version | Eclipse SUMO 1.25.0 (already installed) |
| NS-3 version | ns-3-dev (already installed) — Python bindings partially deprecated, so use C++ for the WAVE app |
| SUMO ↔ NS-3 coupling | Pure TraCI bridge. Python supervisor talks to SUMO via the `traci` library; NS-3 runs as a separate process and consumes mobility snapshots written by the supervisor. No Veins, no native NS-3 TraCI client. |
| Demo mode | SUMO-GUI on (color-coded vehicles). Headless flag also supported. |
| Maps refresh | 30–60 s polling. Default `MAPS_REFRESH_SEC=45`. A `--mock` mode replays a recorded JSON for offline demos. |
| Language split | C++ for the NS-3 scenario + WAVE/802.11p app. Python for everything else (data node, TraCI supervisor, jam detector, output processing). |
| Sim duration | 600 s simulated time (10 min). Configurable via launcher flag. |
| Vehicle cohort | Fixed 10 vehicles for the whole sim. No rolling spawn. Configurable via launcher flag. |
| API key handling | `.env` at project root, gitignored. `.env.example` committed with placeholders. User pastes real key into `.env` by hand inside the VM — never into Claude Code chat. |

---

## 3. Jam detection rule (fixed spec)

A vehicle is "stuck" if **either**:

- Its average speed over the last N seconds is < 5 km/h for > 30 s, **or**
- Google Maps reports the segment as `duration_in_traffic / duration > 1.5`.

A **JAM** is declared on a segment when **≥ 3 of the 10 vehicles** meet the
stuck condition on that same segment. Only then do RSUs broadcast the
U-turn alert to upstream vehicles.

Alert payload format (V2V from stuck vehicles, V2I rebroadcast by RSUs):

```
JAM_DETECTED  { vehicle_id, segment_id, lat, lon, severity, timestamp }
JAM_ALERT     { jam_segment_id, jam_location_name, uturn_location_name,
                uturn_lat, uturn_lon, ttl, timestamp }
```

Human-readable rendering: `"Traffic jam at <XYZ area>, take U-turn at <ABC area>"`.

---

## 4. Architecture — two decoupled nodes

### Node 1 — Real-Time Data Node (Python)
- Reads `GOOGLE_MAPS_API_KEY` from `.env`.
- Calls Directions API + Roads API + Distance Matrix API for the corridor.
- Generates the SUMO network from real OSM data (osmWebWizard or netconvert).
- Generates 10 vehicles' routes (`.rou.xml`) seeded from current traffic.
- Writes a refreshing `traffic_state.json` with per-segment congestion.

### Node 2 — Simulation Node (SUMO + NS-3)
- SUMO runs the microsimulation using files from Node 1.
- NS-3 runs the network simulation; mobility is fed from SUMO via the TraCI
  bridge.
- 10 OBU nodes (mobile, 802.11p/WAVE) + N RSUs (static, 802.11p/WAVE) every 1 km.
- WAVE app handles `JAM_DETECTED` and `JAM_ALERT`.
- On `JAM_ALERT` receipt, the OBU calls TraCI to reroute / U-turn at the
  recommended junction.

---

## 5. Project directory layout (agreed)

```
vanet_claude/
├── BRIEF.md                      # this file
├── README.md                     # written in Phase 5
├── .env                          # gitignored — user creates in Kali
├── .env.example                  # committed
├── .gitignore
├── requirements.txt
├── corridor/
│   ├── corridor.geojson
│   ├── corridor.osm
│   ├── corridor.net.xml
│   └── rsu_positions.csv         # lat,lon,id every 1 km
├── data_node/                    # Node 1 — Python
│   ├── fetch_traffic.py
│   ├── generate_routes.py
│   ├── traffic_state.json
│   └── mock/                     # recorded responses for --mock mode
├── sim/                          # Node 2 — SUMO + NS-3
│   ├── sumo/
│   │   ├── vanet.sumocfg
│   │   └── routes.rou.xml
│   ├── ns3/
│   │   ├── vanet-scenario.cc
│   │   ├── jam-alert-app.cc
│   │   └── jam-alert-app.h
│   └── bridge/
│       ├── traci_supervisor.py   # SUMO ↔ NS-3 mobility + reroute bridge
│       └── jam_detector.py
├── output/
│   ├── results.csv               # per-vehicle final status
│   ├── alerts.log
│   └── screenshots/
├── scripts/
│   ├── install_kali.sh           # apt + pip one-shot
│   └── run_demo.sh               # end-to-end launcher with flags
└── docs/
    ├── architecture.md
    └── corridor_choice.md
```

---

## 6. Phases (work strictly in this order, pause between each)

- **Phase 0 — Setup & verification.** Confirm SUMO 1.25.0, ns-3-dev, Python 3,
  required Python libs. Generate `install_kali.sh` for anything missing.
  Create the directory tree, `.env.example`, `.gitignore`, `requirements.txt`.
  No simulation code.
- **Phase 1 — Map data.** Download OSM for the NH-16 corridor bbox, run
  `netconvert` to produce `corridor.net.xml`, place RSUs every 1 km along the
  main road, write `rsu_positions.csv`, write `docs/corridor_choice.md`.
- **Phase 2 — Real-Time Data Node.** `fetch_traffic.py`, `generate_routes.py`,
  produce `routes.rou.xml` and refreshing `traffic_state.json`. Implement
  `--mock` mode using recorded fixtures.
- **Phase 3 — SUMO + NS-3 coupling.** `vanet.sumocfg`, `vanet-scenario.cc`,
  `jam-alert-app.{cc,h}` with 802.11p/WAVE, `traci_supervisor.py` bridge.
  Mobility flowing one way, reroute commands flowing back.
- **Phase 4 — Alert logic & U-turn rerouting.** Implement the jam detection
  rule from §3. On `JAM_ALERT`, OBU reroutes via TraCI at the recommended
  junction.
- **Phase 5 — Output & visualization.** SUMO-GUI color coding (red=stuck,
  green=moving, yellow=alerted/rerouting), `results.csv` per vehicle,
  `alerts.log`, end-to-end `README.md` with run instructions.

---

## 7. Hard rules

- **Phase gate.** End every phase with "Phase N complete — ready for Phase N+1?"
  and stop. Do not start the next phase until the user says "go".
- **No API key in chat.** Never ask the user to paste the Google Maps key
  into the conversation. The code reads it from `.env`.
- **Relative paths only** in code.
- **Comment for clarity** — this is a presentation project. Explain the *why*
  of non-obvious logic (jam threshold, TraCI bridging, WAVE app structure).
  Skip narration of obvious code.
- **Minimal dependencies**, prefer apt-installable on Kali.
- **Kali is the runtime.** All scripts target Linux (forward slashes, bash).

---

## 8. Where to start

On "go", begin **Phase 0** only:

1. Verify tool versions in the VM (`sumo --version`, `ns3 --version`,
   `python3 --version`).
2. List Python libs needed (`googlemaps`, `python-dotenv`, `traci`, `sumolib`,
   `requests`, etc.) and produce `requirements.txt`.
3. Generate `scripts/install_kali.sh` covering anything missing.
4. Create the directory tree from §5, plus `.env.example`, `.gitignore`,
   empty placeholder files where useful.
5. Stop. Ask the user to confirm Phase 0 before starting Phase 1.
