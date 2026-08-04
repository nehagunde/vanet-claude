#!/usr/bin/env bash
# build_corridor.sh — Phase 1: download OSM, build SUMO network, place RSUs.
#
# Run from the project root inside Kali, with the venv activated:
#   source ~/.venvs/vanet_claude/bin/activate
#   bash scripts/build_corridor.sh
#
# Idempotent: skips OSM download and netconvert if outputs already exist.
# To force a refresh, delete the relevant file in corridor/ and re-run.

set -euo pipefail

# ---------- locate project paths ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

CORRIDOR_DIR="$PROJECT_ROOT/corridor"
OSM_FILE="$CORRIDOR_DIR/corridor.osm"
NET_FILE="$CORRIDOR_DIR/corridor.net.xml"
RSU_FILE="$CORRIDOR_DIR/rsu_positions.csv"
POIS_FILE="$CORRIDOR_DIR/rsu_pois.add.xml"
mkdir -p "$CORRIDOR_DIR"

# ---------- corridor bounding box (NH-16 Gajuwaka → NAD, Visakhapatnam) ----------
# See docs/corridor_choice.md for justification.
BBOX_SOUTH=17.680
BBOX_WEST=83.180
BBOX_NORTH=17.740
BBOX_EAST=83.230

echo "==> Phase 1 — building corridor"
echo "    Bounding box: S=$BBOX_SOUTH W=$BBOX_WEST N=$BBOX_NORTH E=$BBOX_EAST"
echo

# ---------- 1. Sanity checks ----------
have() { command -v "$1" >/dev/null 2>&1; }
for tool in curl netconvert python3; do
    if ! have "$tool"; then
        echo "ERROR: '$tool' is not on PATH. Run scripts/install_kali.sh first." >&2
        exit 1
    fi
done

# ---------- 2. Download OSM via Overpass API ----------
if [ -s "$OSM_FILE" ]; then
    echo "==> $OSM_FILE already present ($(du -h "$OSM_FILE" | cut -f1)) — skipping download."
    echo "    (Delete it to force refresh.)"
else
    echo "==> Querying Overpass API for highway data within bbox"
    # Highways only (way[\"highway\"]) plus all nodes referenced (>;).
    OVERPASS_QUERY="[out:xml][timeout:180];
(
  way[\"highway\"](${BBOX_SOUTH},${BBOX_WEST},${BBOX_NORTH},${BBOX_EAST});
  >;
);
out body;"

    HTTP_CODE=$(curl -s -o "$OSM_FILE" -w "%{http_code}" \
        -X POST -d "$OVERPASS_QUERY" \
        https://overpass-api.de/api/interpreter)
    if [ "$HTTP_CODE" != "200" ] || [ ! -s "$OSM_FILE" ]; then
        echo "ERROR: Overpass returned HTTP $HTTP_CODE or empty file." >&2
        echo "       Check network connectivity, then retry." >&2
        rm -f "$OSM_FILE"
        exit 1
    fi
    echo "    Saved: $OSM_FILE  ($(du -h "$OSM_FILE" | cut -f1))"
fi
echo

# ---------- 3. Run netconvert to build the SUMO network ----------
if [ -s "$NET_FILE" ] && [ "$NET_FILE" -nt "$OSM_FILE" ]; then
    echo "==> $NET_FILE is newer than the OSM input — skipping netconvert."
    echo "    (Delete $NET_FILE to force rebuild.)"
else
    echo "==> Running netconvert"
    # --proj.utm           : project lat/lon to UTM Cartesian (so we can use x/y)
    # --geometry.remove    : merge collinear edges into a single polyline
    # --roundabouts.guess  : auto-detect roundabouts from geometry
    # --junctions.join     : merge clusters of close junctions into one
    # --tls.guess-signals  : promote crossings near junctions into traffic lights
    # --tls.discard-simple : drop trivial single-phase signals
    # --tls.join           : merge adjacent traffic lights at the same junction
    # --remove-edges.isolated : drop edges disconnected from the main graph
    netconvert \
        --osm-files "$OSM_FILE" \
        --output-file "$NET_FILE" \
        --proj.utm \
        --geometry.remove \
        --roundabouts.guess \
        --junctions.join \
        --tls.guess-signals \
        --tls.discard-simple \
        --tls.join \
        --remove-edges.isolated
    echo "    Saved: $NET_FILE  ($(du -h "$NET_FILE" | cut -f1))"
fi
echo

# ---------- 4. Place RSUs every 1 km along the corridor centerline ----------
echo "==> Placing RSUs at 1 km intervals (+ reverse geocoding + SUMO POI file)"
python3 "$SCRIPT_DIR/place_rsus.py" \
    --net  "$NET_FILE" \
    --out  "$RSU_FILE" \
    --pois "$POIS_FILE" \
    --spacing-m 1000
echo

# ---------- 5. Summary ----------
echo "==> Phase 1 complete."
echo
echo "    OSM extract: $OSM_FILE   ($(du -h "$OSM_FILE" | cut -f1))"
echo "    SUMO net:    $NET_FILE   ($(du -h "$NET_FILE" | cut -f1))"
echo "    RSU CSV:     $RSU_FILE"
echo
echo "Inspect the RSU table:"
echo "    column -t -s, $RSU_FILE | less -S"
echo
echo "View RSUs as RED DOTS on the SUMO network:"
echo "    sumo-gui -n $NET_FILE \\"
echo "             --additional-files $POIS_FILE"
