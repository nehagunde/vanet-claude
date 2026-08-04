#!/usr/bin/env python3
"""
place_rsus.py — Phase 1 (v3): place RSUs at real infrastructure (petrol
pumps, bus stands, cafes, restaurants) at 7 key areas along NH-16,
Visakhapatnam.

Guide specification:
  Every RSU must be within 100 m of the NH-16 highway centreline.
  Preferred placement: real roadside infrastructure (petrol pump, bus
  stand, cafe, restaurant) that satisfies the 100 m constraint.
  Fallback: highway anchor — centroid snapped directly to the nearest
  point on the road (distance = 0 m).

Strategy per area:
  1. Fetch NH-16 road geometry from Overpass (done ONCE before the loop).
  2. Query Overpass API for petrol pumps / bus stands / cafes / restaurants
     within 600 m of the area centroid.
  3. Keep only those POIs whose distance to the road polyline is ≤ 100 m.
  4. From the qualifying POIs pick the one nearest to the area centroid.
  5. If no POI qualifies, snap the centroid directly to the highway
     (infra_type = "Highway Anchor", distance = 0 m).

Outputs:
  corridor/rsu_positions.csv   — id, place, infra type, lat, lon, x, y
  corridor/rsu_pois.add.xml    — SUMO additional-file (red circles)
  corridor/sumo_view.xml       — SUMO GUI view settings
  corridor/rsu_map.html        — interactive Leaflet map on real OSM tiles
"""

import argparse
import csv
import json
import math
import re
import sys
import time
import xml.sax.saxutils as saxutils
from pathlib import Path

import requests

# ── 7 key area centroids — coordinates taken DIRECTLY from the NH-16 road ─────
# Previous coordinates pointed at local service roads running parallel to NH-16.
# These corrected coordinates are extracted from the Overpass road polyline that
# represents the actual NH-16 main carriageway at each area's latitude.
# Rule: every centroid must sit ON the orange road line visible in rsu_map.html.
KEY_AREAS = [
    {"id": "rsu_00", "area": "Old Gajuwaka",  "fallback_infra": "Bus Stand",   "lat": 17.6851, "lon": 83.2032},
    {"id": "rsu_01", "area": "New Gajuwaka",  "fallback_infra": "Petrol Pump", "lat": 17.6934, "lon": 83.2053},
    {"id": "rsu_02", "area": "BHPV",          "fallback_infra": "Bus Stand",   "lat": 17.7025, "lon": 83.2057},
    {"id": "rsu_03", "area": "Nathayyapalem", "fallback_infra": "Bus Stand",   "lat": 17.7107, "lon": 83.2050},
    {"id": "rsu_04", "area": "Sheelanagar",   "fallback_infra": "Bus Stand",   "lat": 17.7189, "lon": 83.2038},
    {"id": "rsu_05", "area": "Gopalapatnam",  "fallback_infra": "Petrol Pump", "lat": 17.7255, "lon": 83.2085},
    {"id": "rsu_06", "area": "NAD Junction",  "fallback_infra": "Bus Stand",   "lat": 17.7320, "lon": 83.2227},
]

# ── NH-16 road waypoints — dense trace of the ACTUAL road (35 points) ─────────
# Coordinates matched to the Overpass road polyline main strand.
# Used for: (a) POI distance filtering, (b) HTML fallback road display.
# Spacing ≈ 200 m so closest-point-on-segment errors stay well under 50 m.
NH16_WAYPOINTS = [
    (17.6851, 83.2032),  # rsu_00 Old Gajuwaka
    (17.6865, 83.2037),
    (17.6880, 83.2042),
    (17.6895, 83.2047),
    (17.6910, 83.2050),
    (17.6920, 83.2051),
    (17.6934, 83.2053),  # rsu_01 New Gajuwaka
    (17.6950, 83.2054),
    (17.6965, 83.2055),
    (17.6978, 83.2056),
    (17.6990, 83.2057),
    (17.7008, 83.2057),
    (17.7025, 83.2057),  # rsu_02 BHPV
    (17.7042, 83.2057),
    (17.7058, 83.2056),
    (17.7073, 83.2054),
    (17.7090, 83.2052),
    (17.7107, 83.2050),  # rsu_03 Nathayyapalem
    (17.7125, 83.2049),
    (17.7143, 83.2046),
    (17.7163, 83.2043),
    (17.7176, 83.2040),
    (17.7189, 83.2038),  # rsu_04 Sheelanagar
    (17.7205, 83.2045),
    (17.7220, 83.2058),
    (17.7235, 83.2069),
    (17.7248, 83.2078),
    (17.7255, 83.2085),  # rsu_05 Gopalapatnam
    (17.7270, 83.2110),
    (17.7285, 83.2135),
    (17.7300, 83.2163),
    (17.7310, 83.2190),
    (17.7320, 83.2227),  # rsu_06 NAD Junction
    (17.7330, 83.2255),
    (17.7340, 83.2265),
]

# POI types in priority order (fuel preferred — most visible landmark)
POI_PRIORITY = [
    ("amenity", "fuel",        "Petrol Pump"),
    ("highway", "bus_stop",    "Bus Stand"),
    ("amenity", "cafe",        "Cafe"),
    ("amenity", "restaurant",  "Restaurant"),
    ("shop",    "convenience", "Convenience Store"),
]

OVERPASS_URL      = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL     = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {
    "User-Agent": "vanet-project/1.0 (academic-research)",
    "Accept-Language": "en",
}
NOMINATIM_DELAY_S  = 1.1
CORRIDOR_BBOX      = (17.680, 83.180, 17.740, 83.230)   # (S, W, N, E)
POI_SEARCH_R_DEG   = 0.006   # ≈ 600 m search box half-width

# ── Guide requirement: every RSU must be within this distance of NH-16 ────────
MAX_HIGHWAY_OFFSET_M = 100   # metres


# ── Geometry helpers ──────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _closest_pt_on_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    seg_sq = dx * dx + dy * dy
    if seg_sq == 0:
        return ax, ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_sq))
    return ax + t * dx, ay + t * dy


def snap_to_road(net, x, y, search_r=300.0):
    """Snap (x,y) to the nearest point on any road edge within search_r m."""
    neighbours = net.getNeighboringEdges(x, y, search_r)
    if not neighbours:
        return x, y
    best_x, best_y, best_d = x, y, float("inf")
    for edge, _ in neighbours:
        shape = edge.getShape()
        for i in range(len(shape) - 1):
            cx, cy = _closest_pt_on_segment(
                x, y,
                shape[i][0], shape[i][1],
                shape[i + 1][0], shape[i + 1][1],
            )
            d = math.hypot(cx - x, cy - y)
            if d < best_d:
                best_d, best_x, best_y = d, cx, cy
    return best_x, best_y


def _closest_pt_on_segment_ll(p_lat, p_lon, a_lat, a_lon, b_lat, b_lon):
    """
    Closest point on segment a→b to point p, all in lat/lon degrees.
    Uses simple planar projection (accurate enough for < 2 km segments).
    Returns (lat, lon) of the closest point.
    """
    dx = b_lon - a_lon
    dy = b_lat - a_lat
    len_sq = dx * dx + dy * dy
    if len_sq == 0:
        return a_lat, a_lon
    t = max(0.0, min(1.0,
        ((p_lon - a_lon) * dx + (p_lat - a_lat) * dy) / len_sq))
    return a_lat + t * dy, a_lon + t * dx


def dist_to_road_m(lat, lon, road_pts) -> float:
    """
    Minimum haversine distance (metres) from (lat, lon) to the road
    polyline defined by road_pts = [(lat, lon), ...].

    Used to enforce the MAX_HIGHWAY_OFFSET_M = 100 m guide constraint.
    Returns inf when road_pts is empty (triggers highway-anchor fallback).
    """
    if not road_pts:
        return float("inf")
    best = float("inf")
    for i in range(len(road_pts) - 1):
        c_lat, c_lon = _closest_pt_on_segment_ll(
            lat, lon,
            road_pts[i][0],     road_pts[i][1],
            road_pts[i + 1][0], road_pts[i + 1][1],
        )
        d = haversine_m(lat, lon, c_lat, c_lon)
        if d < best:
            best = d
    return best


def snap_to_road_pts(lat, lon, road_pts) -> tuple[float, float]:
    """
    Snap (lat, lon) to the nearest point on the road_pts polyline.

    Used for the highway-anchor fallback so the RSU lands EXACTLY on
    the orange NH-16 line drawn in the HTML map (distance shown = 0 m).
    Returns (snapped_lat, snapped_lon).
    """
    if not road_pts:
        return lat, lon
    best_lat, best_lon = lat, lon
    best_d = float("inf")
    for i in range(len(road_pts) - 1):
        c_lat, c_lon = _closest_pt_on_segment_ll(
            lat, lon,
            road_pts[i][0],     road_pts[i][1],
            road_pts[i + 1][0], road_pts[i + 1][1],
        )
        d = haversine_m(lat, lon, c_lat, c_lon)
        if d < best_d:
            best_d    = d
            best_lat  = c_lat
            best_lon  = c_lon
    return best_lat, best_lon


# ── Overpass POI finder ───────────────────────────────────────────────────────

def fetch_poi_near(lat, lon, label="area") -> dict | None:
    """
    Search for roadside infrastructure within MAX_HIGHWAY_OFFSET_M (100 m)
    of (lat, lon), which is a point ON the NH-16 highway.

    Because the centroid is ON the road, any POI within 100 m of the
    centroid is automatically within 100 m of the highway — no separate
    road-distance check needed.

    Strategy:
      1. Fetch all petrol pumps / bus stands / cafes / restaurants in a
         200 m bounding box around the centroid.
      2. Keep only those whose haversine distance to the centroid is ≤ 100 m.
      3. From qualifying POIs pick the nearest to the centroid.

    Returns dict {lat, lon, infra, poi_name} or None if none qualify.
    """
    # 0.0010° ≈ 111 m lat / 106 m lon — gives a ~200 m × 200 m search box
    r = 0.0010
    s, n_box = lat - r, lat + r
    w, e     = lon - r, lon + r

    node_clauses = "\n".join(
        f'  node["{k}"="{v}"]({s:.5f},{w:.5f},{n_box:.5f},{e:.5f});'
        for k, v, _ in POI_PRIORITY
    )
    query = f"[out:json][timeout:25];\n(\n{node_clauses}\n);\nout body;"

    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers={
                "User-Agent": "vanet-project/1.0 (academic-research)",
                "Accept":     "*/*",          # fixes 406 Not Acceptable
            },
            timeout=30,
        )
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception as exc:
        print(f"    [WARN] Overpass error for {label}: {exc}", file=sys.stderr)
        return None

    if not elements:
        return None

    # Keep only POIs within MAX_HIGHWAY_OFFSET_M of the centroid (which is on road)
    qualifying = [
        el for el in elements
        if haversine_m(lat, lon, el["lat"], el["lon"]) <= MAX_HIGHWAY_OFFSET_M
    ]

    if not qualifying:
        return None   # caller will use highway-anchor fallback

    # Pick nearest to centroid
    best = min(qualifying,
               key=lambda el: haversine_m(lat, lon, el["lat"], el["lon"]))
    tags = best.get("tags", {})

    infra = "Infrastructure"
    for k, v, lbl in POI_PRIORITY:
        if tags.get(k) == v:
            infra = lbl
            break

    name = (tags.get("name")
            or tags.get("operator")
            or tags.get("brand")
            or infra)

    return {
        "lat":      best["lat"],
        "lon":      best["lon"],
        "infra":    infra,
        "poi_name": name,
    }


# ── Overpass road-geometry fetcher ────────────────────────────────────────────

def fetch_road_geometry() -> list[tuple[float, float]]:
    """
    Fetch the NH-16 road geometry from Overpass API.

    Strategy: query all primary/trunk ways in the corridor bbox, then
    pick the LONGEST single way by node count.  That longest way is
    almost certainly NH-16 itself (the urban arterial through Gajuwaka →
    NAD Junction).  Using a single connected way avoids the zigzag
    artefacts that appear when points from multiple parallel roads are
    merged and sorted by latitude.

    Falls back to NH16_WAYPOINTS on any error or empty result.
    """
    s, n = CORRIDOR_BBOX[0], CORRIDOR_BBOX[2]
    w, e = 83.180, 83.228      # full east extent to include NAD Junction

    query = (
        f"[out:json][timeout:60];"
        f"way[\"highway\"~\"primary|trunk\"]({s},{w},{n},{e});"
        f"out geom;"
    )
    try:
        resp = requests.get(
            OVERPASS_URL,
            params={"data": query},
            headers={"User-Agent": "vanet-project/1.0 (academic-research)"},
            timeout=65,
        )
        resp.raise_for_status()
        ways = resp.json().get("elements", [])
    except Exception as exc:
        print(f"  [WARN] Overpass road fetch failed: {exc}", file=sys.stderr)
        return []

    if not ways:
        return []

    # Pick the single longest way (by node count) — that is NH-16.
    # Using ONE connected way gives a clean polyline with no zigzags.
    longest = max(ways, key=lambda w: len(w.get("geometry", [])))
    pts = [(g["lat"], g["lon"]) for g in longest.get("geometry", [])]

    if not pts:
        return []

    # Ensure the list runs south → north (Gajuwaka end first).
    if pts[0][0] > pts[-1][0]:
        pts.reverse()

    return pts


# ── Nominatim reverse geocoder ────────────────────────────────────────────────

def reverse_geocode(lat, lon) -> str:
    _SKIP = {"India", "Andhra Pradesh", "Telangana",
             "Visakhapatnam District", "Visakhapatnam", "Vishakhapatnam"}
    try:
        resp = requests.get(
            NOMINATIM_URL,
            params={"lat": lat, "lon": lon, "format": "json", "zoom": 17},
            headers=NOMINATIM_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        addr = data.get("address", {})

        parts = []
        for key in ("road", "suburb", "neighbourhood", "quarter",
                    "village", "town", "residential", "industrial",
                    "city_district", "county"):
            val = addr.get(key, "").strip()
            if val and val not in parts and val not in _SKIP:
                parts.append(val)
            if len(parts) == 2:
                break
        if parts:
            return ", ".join(parts)

        display = data.get("display_name", "")
        if display:
            segs = [s.strip() for s in display.split(",")]
            useful = [s for s in segs if s and not s.isdigit() and s not in _SKIP]
            if useful:
                return ", ".join(useful[:2])

    except Exception:
        pass
    return f"{lat:.4f}°N, {lon:.4f}°E"


# ── SUMO additional-file writer ───────────────────────────────────────────────

def write_pois_xml(rows: list[dict], out_path: Path, view_path: Path,
                   positions: list[dict] | None = None) -> None:
    """
    Write the SUMO additional-file with, for each RSU:
      1. A filled red circle polygon  — 300 m radio-range indicator.
      2. A red POI dot                — RSU position marker with label.
      3. A thin offset line poly      — RSU position → nearest SUMO road edge
                                        (mirrors the dotted line in the HTML map).
      4. A distance-label POI         — "XX m" at the midpoint of the offset line.

    Elements 3 & 4 let the guide see the road-offset distance directly in
    SUMO-GUI, exactly as shown in the HTML map.
    """
    def esc(s):
        s = str(s).replace("—", "-").replace("–", "-")
        return saxutils.escape(s, {'"': "&quot;"})

    def make_id(prefix, rsu_id):
        raw = f"{prefix}_{rsu_id}"
        raw = re.sub(r"[^A-Za-z0-9_\-\.]", "_", raw)
        return re.sub(r"_+", "_", raw).strip("_")

    # Build a lookup: rsu_id → snap info from positions list
    snap_info = {}
    if positions:
        for p in positions:
            snap_info[p["id"]] = p

    # 100 m marker circle — visible at overview zoom without being overwhelming
    R_LAT = 0.0009    # 100 m in latitude degrees  (100 / 111 320)
    R_LON = R_LAT / math.cos(math.radians(17.71))
    N_PTS = 36

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<additional>']

    for r in rows:
        rsu_id  = r["id"]
        label   = esc(f"{r['id'].upper()} — {r['area']} | {r['nearest_place']}")
        lat     = float(r["lat"])
        lon     = float(r["lon"])

        # ── 1. Small red circle outline (100 m radius, no fill) ──────────────
        # Visible at the corridor overview zoom (scale bar ≈ 1000 m).
        circle_pts = []
        for i in range(N_PTS):
            angle = 2 * math.pi * i / N_PTS
            circle_pts.append(
                f"{lon + R_LON * math.cos(angle):.6f},"
                f"{lat + R_LAT * math.sin(angle):.6f}"
            )
        circle_pts.append(circle_pts[0])
        lines.append(
            f'    <poly id="{make_id("range", rsu_id)}"'
            f' shape="{" ".join(circle_pts)}" geo="true"'
            f' color="255,0,0" fill="0" lineWidth="3" layer="200"'
            f' name=""/>'
        )

        # ── 2. RSU POI dot — solid red, medium size ───────────────────────────
        # minSize="20" in sumo_view.xml guarantees visibility at any zoom.
        lines.append(
            f'    <poi id="{esc(rsu_id)}"'
            f' lon="{r["lon"]}" lat="{r["lat"]}" geo="true"'
            f' color="255,0,0,255" type="RSU" layer="202"'
            f' name="{label}"/>'
        )

        # ── 3 & 4. Offset line + distance label (SUMO XY coordinates) ────────
        snap = snap_info.get(rsu_id)
        if snap:
            rx, ry   = snap["x_m"],      snap["y_m"]       # RSU SUMO XY
            sx, sy   = snap["snap_x_m"], snap["snap_y_m"]  # nearest road XY
            dist_m   = snap["road_dist_sumo"]              # metres to road

            # Only draw the offset line when there is a measurable gap.
            # (highway anchors have dist ≈ 0 m and no line is needed)
            if dist_m > 1:
                # Thin dark line: RSU position → nearest road edge point
                lines.append(
                    f'    <poly id="{make_id("offset", rsu_id)}"'
                    f' shape="{rx:.2f},{ry:.2f} {sx:.2f},{sy:.2f}"'
                    f' color="50,50,50,200" fill="0" lineWidth="3" layer="201"'
                    f' name=""/>'
                )

                # Distance label POI at the midpoint of the offset line
                mid_x = (rx + sx) / 2.0
                mid_y = (ry + sy) / 2.0
                lines.append(
                    f'    <poi id="{make_id("dist", rsu_id)}"'
                    f' x="{mid_x:.2f}" y="{mid_y:.2f}"'
                    f' color="255,255,200,230" type="dist_label" layer="203"'
                    f' name="{dist_m} m"/>'
                )
            else:
                # Highway anchor: show "0 m" label right at the RSU dot
                lines.append(
                    f'    <poi id="{make_id("dist", rsu_id)}"'
                    f' x="{rx:.2f}" y="{ry:.2f}"'
                    f' color="100,255,100,230" type="dist_label" layer="203"'
                    f' name="0 m"/>'
                )

    lines.append('</additional>')
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── sumo_view.xml — POI dot size, label style, polygon outline ───────────
    view_lines = [
        '<viewsettings>',
        '    <scheme name="real world">',
        '        <!-- Range-circle polygons: show outline only, no label -->',
        '        <polyName show="0"/>',
        '        <polySize minSize="0" exaggeration="1"/>',
        '        <!-- RSU POI dots: solid red, always visible at any zoom -->',
        '        <poiName  show="1" size="50" color="255,0,0"/>',
        '        <poiSize  minSize="20" exaggeration="1"/>',
        '    </scheme>',
        '</viewsettings>',
    ]
    view_path.write_text("\n".join(view_lines) + "\n", encoding="utf-8")


# ── Interactive HTML map ──────────────────────────────────────────────────────

def write_html_map(rows: list[dict], road_pts: list[tuple],
                   out_path: Path) -> None:
    """
    Leaflet.js map with:
      • Real OSM base tiles — actual NH-16 road is visible.
      • Always-on RSU labels showing id, area, and infrastructure type.
      • 300 m radio-range circle per RSU.
      • The actual NH-16 road geometry overlaid in blue (from Overpass).
    """
    mid_lat = sum(float(r["lat"]) for r in rows) / len(rows)
    mid_lon = sum(float(r["lon"]) for r in rows) / len(rows)

    # Build JS for road polyline (actual NH-16 geometry or hardcoded trace)
    if road_pts:
        pts_js = ",\n      ".join(f"[{lat:.6f},{lon:.6f}]"
                                  for lat, lon in road_pts)
        route_js = (
            f"L.polyline([\n      {pts_js}\n    ], "
            "{color:'#e65c00',weight:7,opacity:0.75})"
            ".addTo(map)"
            ".bindTooltip('NH-16 — Gajuwaka to NAD Junction',"
            "{sticky:true,className:'route-tip'})"
        )
    else:
        # Hardcoded NH-16 waypoints traced from Google Maps
        pts_js = ",\n      ".join(f"[{lat:.6f},{lon:.6f}]"
                                  for lat, lon in NH16_WAYPOINTS)
        route_js = (
            f"L.polyline([\n      {pts_js}\n    ], "
            "{color:'#e65c00',weight:7,opacity:0.75})"
            ".addTo(map)"
            ".bindTooltip('NH-16 — Gajuwaka to NAD Junction',"
            "{sticky:true,className:'route-tip'})"
        )

    # Build JS marker calls
    marker_lines = []
    for r in rows:
        short_place = r["nearest_place"].split(",")[0].strip()
        infra  = r.get("infra_type", "RSU")
        marker_lines.append(
            f'  addRSU({r["lat"]},{r["lon"]},'
            f'"{r["id"].upper()}","{r["area"]}","{short_place}","{infra}");'
        )
    markers_js = "\n".join(marker_lines)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>NH-16 RSU Placement — Visakhapatnam</title>
  <link rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <style>
    body {{ margin:0; font-family:Arial,sans-serif; }}
    #map {{ height:100vh; width:100%; }}
    .rsu-label {{
      background:rgba(160,0,0,0.88); border:none; border-radius:4px;
      color:#fff; font-weight:bold; font-size:11px;
      padding:3px 8px; white-space:nowrap;
    }}
    .rsu-label::before {{ display:none; }}
    .route-tip {{
      background:#e65c00; color:#fff; font-weight:bold;
      border:none; border-radius:4px; padding:3px 8px;
    }}
    #legend {{
      position:absolute; top:10px; left:60px; z-index:1000;
      background:#fff; padding:10px 14px; border-radius:6px;
      box-shadow:0 2px 8px rgba(0,0,0,.3); font-size:13px;
      max-width:360px; line-height:1.6;
    }}
    #legend h3 {{ margin:0 0 6px; font-size:14px; }}
  </style>
</head>
<body>
<div id="legend">
  <h3>NH-16 RSU Placement — Visakhapatnam</h3>
  <b>7 RSUs</b> at real infrastructure along Gajuwaka → NAD Junction.<br>
  <span style="color:red">&#9679;</span> Road Side Unit &nbsp;
  <span style="color:#888">&#9900;</span> 300 m radio range<br>
  <span style="color:#e65c00;font-weight:bold">&#9135;&#9135;</span> NH-16 highway (Gajuwaka → NAD Junction)
</div>
<div id="map"></div>
<script>
  var map = L.map('map').setView([{mid_lat:.6f},{mid_lon:.6f}], 15);
  // CartoDB Voyager tiles — work from file:// without referer restrictions
  L.tileLayer('https://{{s}}.basemaps.cartocdn.com/rastertiles/voyager/{{z}}/{{x}}/{{y}}{{r}}.png', {{
    maxZoom:19,
    subdomains:'abcd',
    attribution:'&copy; <a href="https://openstreetmap.org">OpenStreetMap</a> contributors &copy; <a href="https://carto.com">CARTO</a>'
  }}).addTo(map);

  // NH-16 actual road geometry
  var roadLine = {route_js}
  var roadLatLngs = roadLine.getLatLngs();

  // ── Helpers: find closest point on the road polyline to a given RSU ─────────
  function closestPtOnSegment(p, a, b) {{
    var dx = b.lng - a.lng, dy = b.lat - a.lat;
    var lenSq = dx*dx + dy*dy;
    if (lenSq === 0) return L.latLng(a.lat, a.lng);
    var t = Math.max(0, Math.min(1,
      ((p.lng-a.lng)*dx + (p.lat-a.lat)*dy) / lenSq));
    return L.latLng(a.lat + t*dy, a.lng + t*dx);
  }}

  function closestPtOnRoad(lat, lon) {{
    var p = L.latLng(lat, lon);
    var bestDist = Infinity, bestPt = roadLatLngs[0];
    for (var i = 0; i < roadLatLngs.length - 1; i++) {{
      var c = closestPtOnSegment(p, roadLatLngs[i], roadLatLngs[i+1]);
      var d = map.distance(p, c);
      if (d < bestDist) {{ bestDist = d; bestPt = c; }}
    }}
    return {{ pt: bestPt, dist: Math.round(bestDist) }};
  }}

  function addRSU(lat, lon, id, area, place, infra) {{
    // 300 m radio-range circle
    L.circle([lat,lon], {{
      radius:300, color:'#cc0000', weight:1,
      fillColor:'#ff4444', fillOpacity:0.07, dashArray:'4,4'
    }}).addTo(map);

    // ── Offset line: RSU → nearest point on NH-16 ──────────────────────────
    var result  = closestPtOnRoad(lat, lon);
    var nearPt  = result.pt;
    var distM   = result.dist;

    // Dashed black line showing the gap
    L.polyline([[lat, lon], [nearPt.lat, nearPt.lng]], {{
      color:'#333333', weight:2, dashArray:'5,5', opacity:0.85
    }}).addTo(map);

    // Distance label at midpoint of the offset line
    var midLat = (lat + nearPt.lat) / 2;
    var midLon = (lon  + nearPt.lng) / 2;
    L.marker([midLat, midLon], {{
      icon: L.divIcon({{
        html: '<div style="background:#fff;border:1.5px solid #333;'
            + 'border-radius:4px;padding:1px 5px;font-size:11px;'
            + 'font-weight:bold;white-space:nowrap;box-shadow:0 1px 4px rgba(0,0,0,.3);">'
            + distM + ' m</div>',
        iconAnchor: [22, 10],
        className: ''
      }})
    }}).addTo(map);

    // Red RSU dot
    var dot = L.circleMarker([lat,lon], {{
      radius:10, color:'#8b0000', weight:2,
      fillColor:'#ff0000', fillOpacity:1
    }}).addTo(map);

    dot.bindTooltip(
      '<b>' + id + '</b><br>' + area + '<br><i>' + infra + '</i>',
      {{permanent:true, direction:'right', offset:[12,0], className:'rsu-label'}}
    ).openTooltip();

    dot.bindPopup(
      '<b>' + id + '</b><br>' +
      '<b>Area:</b> ' + area + '<br>' +
      '<b>Infrastructure:</b> ' + infra + '<br>' +
      '<b>Place:</b> ' + place + '<br>' +
      '<b>Lat:</b> ' + lat.toFixed(6) + '<br>' +
      '<b>Lon:</b> ' + lon.toFixed(6) + '<br>' +
      '<b>Distance to NH-16:</b> ' + distM + ' m<br>' +
      '<small>802.11p radio range: 300 m</small>'
    );
  }}

{markers_js}
</script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--net",  required=True, help="SUMO .net.xml file")
    ap.add_argument("--out",  required=True, help="Output CSV path")
    ap.add_argument("--pois", default="corridor/rsu_pois.add.xml",
                    help="Output SUMO POI additional-file")
    ap.add_argument("--no-geocode", action="store_true",
                    help="Skip Nominatim reverse geocoding")
    ap.add_argument("--no-poi-search", action="store_true",
                    help="Skip Overpass POI search (use key-area centroids)")
    args = ap.parse_args()

    # ── Load SUMO net ────────────────────────────────────────────────────────
    try:
        import sumolib
    except ImportError:
        print("ERROR: sumolib not installed. Activate venv.", file=sys.stderr)
        return 1

    net_path = Path(args.net)
    if not net_path.is_file():
        print(f"ERROR: {net_path} not found. Run build_corridor.sh first.",
              file=sys.stderr)
        return 1

    net = sumolib.net.readNet(str(net_path))

    # ── Step 1: fetch NH-16 road geometry for HTML display ───────────────────
    # The corrected KEY_AREAS centroids now sit ON this road, so the HTML
    # dotted-line distances will be ≤ 100 m for every RSU.
    print("\n  Fetching NH-16 road geometry from Overpass (for HTML display) ...")
    road_pts = fetch_road_geometry()
    if road_pts:
        print(f"  Road geometry: {len(road_pts)} points from Overpass — OK")
    else:
        road_pts = NH16_WAYPOINTS
        print(f"  Road geometry: Overpass failed — using "
              f"{len(NH16_WAYPOINTS)}-point hardcoded NH-16 trace")

    # ── Step 2: find POI positions for each key area ──────────────────────────
    n_areas = len(KEY_AREAS)
    print(f"\nLocating infrastructure POIs within {MAX_HIGHWAY_OFFSET_M} m "
          f"of NH-16 for {n_areas} key areas ...")
    positions = []

    for i, area in enumerate(KEY_AREAS):
        sys.stdout.write(f"  [{i+1}/{n_areas}] {area['area']:20s} → ")
        sys.stdout.flush()

        poi = None
        if not args.no_poi_search:
            # Centroid is ON the road → any POI within 100 m qualifies
            poi = fetch_poi_near(
                area["lat"], area["lon"],
                label=area["area"],
            )
            time.sleep(0.5)   # be polite to Overpass API

        if poi:
            lat, lon = poi["lat"], poi["lon"]
            infra     = poi["infra"]
            poi_name  = poi["poi_name"]
            d_centroid = haversine_m(area["lat"], area["lon"], lat, lon)
            print(f"found {infra} — {poi_name}  ({d_centroid:.0f} m from road)")
        else:
            # ── Highway-anchor fallback ──────────────────────────────────────
            # No qualifying POI within 100 m of the highway found.
            # Snap the area centroid directly to the nearest point on the
            # road_pts polyline (same geometry as the orange line in the HTML).
            # This guarantees the dotted line in the HTML shows 0 m.
            lat, lon  = area["lat"], area["lon"]
            infra     = "Highway Anchor"
            poi_name  = f"{area['area']} (Highway Anchor)"
            lat, lon  = snap_to_road_pts(lat, lon, road_pts)
            print(f"no POI within {MAX_HIGHWAY_OFFSET_M} m → Highway Anchor "
                  f"(snapped to NH-16, ~0 m)")

        # Convert lat/lon → SUMO internal XY (plain projection only).
        # We do NOT snap_to_road here; the Overpass geometry already placed
        # the RSU on or within 100 m of NH-16.
        x, y = net.convertLonLat2XY(lon, lat)

        # Find the nearest point on any SUMO road edge — used to draw the
        # offset line (RSU → road) and distance label in SUMO-GUI, mirroring
        # the dotted line shown in the HTML map.
        snap_x, snap_y   = snap_to_road(net, x, y, search_r=500.0)
        road_dist_sumo_m = round(math.hypot(snap_x - x, snap_y - y))

        positions.append({
            "id":             area["id"],
            "area":           area["area"],
            "infra":          infra,
            "poi_name":       poi_name,
            "lat":            lat,
            "lon":            lon,
            "x_m":            x,
            "y_m":            y,
            "snap_x_m":       snap_x,       # nearest SUMO road edge X
            "snap_y_m":       snap_y,       # nearest SUMO road edge Y
            "road_dist_sumo": road_dist_sumo_m,  # metres to road in SUMO net
        })

    # ── Step 3: reverse geocoding ────────────────────────────────────────────
    print(f"\nFetching place names from Nominatim ...")
    for i, p in enumerate(positions):
        if args.no_geocode:
            p["nearest_place"] = f"{p['area']} ({p['infra']})"
        else:
            p["nearest_place"] = reverse_geocode(p["lat"], p["lon"])
            sys.stdout.write(f"  [{i+1}/{n_areas}] {p['id']}: {p['nearest_place']}\n")
            sys.stdout.flush()
            if i < n_areas - 1:
                time.sleep(NOMINATIM_DELAY_S)

    # ── Step 4: inter-RSU distances ──────────────────────────────────────────
    for i, p in enumerate(positions):
        p["from_prev"] = (
            haversine_m(positions[i-1]["lat"], positions[i-1]["lon"],
                        p["lat"], p["lon"]) / 1000.0
            if i > 0 else None
        )
        p["to_next"] = (
            haversine_m(p["lat"], p["lon"],
                        positions[i+1]["lat"], positions[i+1]["lon"]) / 1000.0
            if i < n_areas - 1 else None
        )

    # ── Step 5: console summary ──────────────────────────────────────────────
    W = 100
    print()
    print("=" * W)
    print("  RSU PLACEMENT — NH-16 Gajuwaka → NAD, Visakhapatnam")
    print("  (Positioned at real infrastructure: petrol pumps, bus stands, cafes)")
    print("=" * W)
    print(f"  {'ID':<8}  {'Area':<16}  {'Infrastructure':<18}  "
          f"{'Place':<28}  {'Lat':>10}  {'Lon':>10}  {'Dist':>8}")
    print("  " + "─" * (W - 2))
    for p in positions:
        dist = (f"{p['from_prev']:.2f} km" if p["from_prev"] is not None
                else "  (start)")
        print(f"  {p['id']:<8}  {p['area']:<16}  {p['infra']:<18}  "
              f"{p['nearest_place'][:28]:<28}  "
              f"{p['lat']:>10.6f}  {p['lon']:>10.6f}  {dist:>8}")
    print("  " + "─" * (W - 2))
    print("=" * W)
    print()

    # ── Step 6: write CSV ────────────────────────────────────────────────────
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["id", "area", "infra_type", "nearest_place",
                  "lat", "lon", "x_m", "y_m",
                  "dist_from_prev_km", "dist_to_next_km"]
    rows = []
    for p in positions:
        rows.append({
            "id":               p["id"],
            "area":             p["area"],
            "infra_type":       p["infra"],
            "nearest_place":    p["nearest_place"],
            "lat":              f"{p['lat']:.6f}",
            "lon":              f"{p['lon']:.6f}",
            "x_m":              f"{p['x_m']:.2f}",
            "y_m":              f"{p['y_m']:.2f}",
            "dist_from_prev_km": (f"{p['from_prev']:.4f}"
                                  if p["from_prev"] is not None else ""),
            "dist_to_next_km":   (f"{p['to_next']:.4f}"
                                  if p["to_next"] is not None else ""),
        })
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  CSV   → {out_path}")

    # ── Step 7: write SUMO POI file ──────────────────────────────────────────
    pois_path = Path(args.pois)
    view_path = pois_path.parent / "sumo_view.xml"
    pois_path.parent.mkdir(parents=True, exist_ok=True)
    write_pois_xml(rows, pois_path, view_path, positions)
    print(f"  POIs  → {pois_path}")
    print(f"  View  → {view_path}")

    # ── Step 8: write HTML map (road_pts already fetched in Step 1) ─────────
    html_path = pois_path.parent / "rsu_map.html"
    write_html_map(rows, road_pts, html_path)
    print(f"  Map   → {html_path}")

    print()
    print("  ── View RSUs on real map (recommended for demo):")
    print(f"    firefox {html_path} &")
    print()
    print("  ── View in SUMO-GUI:")
    print(f"    sumo-gui -n {net_path} \\")
    print(f"             --additional-files {pois_path} \\")
    print(f"             --gui-settings-file {view_path}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
