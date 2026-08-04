# Corridor selection — NH-16 Gajuwaka → Sheelanagar → NAD Junction

## Selected corridor

**National Highway 16 (Visakhapatnam):** an ~8 km arterial stretch from
**Gajuwaka Junction** in the south, through **Sheelanagar**, to **NAD Junction**
(Naval Armament Depot) in the north.

| Endpoint          | Approx. coordinates       | Role in corridor                              |
|-------------------|---------------------------|-----------------------------------------------|
| Gajuwaka Junction | 17.6852° N, 83.1862° E    | South end — industrial / port-bound traffic   |
| Sheelanagar       | 17.7142° N, 83.2093° E    | Mid-corridor — multiple side roads & U-turns  |
| NAD Junction      | 17.7340° N, 83.2226° E    | North end — commuter inflow toward CBD        |

Straight-line distance between endpoints ≈ **6.6 km**; along-road distance
including curvature ≈ **7.5–8 km**.

## Bounding box for OSM extraction

```
South: 17.680
North: 17.740
West:  83.180
East:  83.230
```

≈ 6.6 km × 5.3 km. The bbox is intentionally a little larger than the
corridor itself so service roads, parallel routes, and U-turn cuts are
captured — these matter for realistic SUMO rerouting in Phase 4.

## Why this corridor (against the project requirements)

| Project requirement              | How NH-16 fits                                                                                                                                           |
|----------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| Length 5–8 km                    | ~7.5–8 km along the road — accommodates 7–8 RSUs at 1 km spacing.                                                                                        |
| Multiple junctions / U-turn options | Divided arterial with several signalised junctions: Gajuwaka, BHEL, Sheelanagar, Kancharapalem, NAD. Service roads on both sides allow realistic U-turns at most junctions. |
| Known congestion-prone           | Two daily peaks: morning (S→N: workers/students into the city), evening (N→S: return + heavy goods toward port and industrial belt). Midday slowdowns from port-bound HGVs. |
| Good Google Maps traffic coverage| NH-16 is a national highway — Google's `duration_in_traffic` data is reliable and granular here.                                                          |
| Good OSM coverage                | NH-16 is well-mapped in OSM, including service roads and minor cross-streets — important for rerouting realism.                                          |

## Alternatives considered (and rejected)

- **Beach Road (RK Beach → Rushikonda)** — scenic but lighter traffic; the
  median is closed in many places, so "take a U-turn at <ABC>" alerts have
  few realistic answer points.
- **Dwaraka Nagar → Siripuram → Asilmetta** — dense city core, but only
  ~3 km, which makes 5–8 RSUs at 1 km spacing awkward.

## Generation procedure (reproducible)

The corridor data in `corridor/` is regenerated from raw OSM by:

```bash
bash scripts/build_corridor.sh
```

This produces:

| File                            | Source                     | Purpose                                         |
|---------------------------------|----------------------------|-------------------------------------------------|
| `corridor/corridor.osm`         | Overpass API (highway=*)   | Raw OSM extract for the bbox                    |
| `corridor/corridor.net.xml`     | `netconvert` on the OSM    | SUMO simulation network                         |
| `corridor/rsu_positions.csv`    | `place_rsus.py`            | RSU id, lat, lon, x, y (every 1 km along NH-16) |

## RSU placement strategy

RSUs are laid at fixed 1 km intervals along the **geographic centerline**
between the south and north endpoints (linear interpolation in lat/lon
space — accurate enough at this scale). They are *not* snapped to a specific
SUMO edge, because:

1. 802.11p radio range is ~300 m, so RSUs only need to be near the corridor
   to cover passing vehicles.
2. SUMO and NS-3 use different coordinate frames; placing RSUs by
   geographic coordinate keeps them consistent across both simulators.

`place_rsus.py` converts each lat/lon to SUMO's local Cartesian (x, y) using
the projection embedded in `corridor.net.xml`, so Phase 3 (NS-3) can
position the RSU nodes directly without redoing the conversion.
