#!/usr/bin/env python3
"""
fetch_traffic.py — Phase 2: poll Google Maps for live traffic on each
RSU-to-RSU segment and persist the result as traffic_state.json.

Modes
-----
  Mock (no API key needed — perfect for demos and offline testing):
    python3 scripts/fetch_traffic.py --mock

  Live (requires GOOGLE_MAPS_API_KEY in .env):
    python3 scripts/fetch_traffic.py

  Custom paths:
    python3 scripts/fetch_traffic.py --mock \\
        --rsu corridor/rsu_positions.csv \\
        --out corridor/traffic_state.json

  Continuous polling loop (re-runs every MAPS_REFRESH_SEC seconds from .env):
    python3 scripts/fetch_traffic.py --loop

Output — corridor/traffic_state.json:
    {
      "fetched_at": "2026-05-02T14:30:00+05:30",
      "source":     "mock" | "google_maps",
      "segments": [
        {
          "seg_id":             "seg_00_01",
          "from_rsu":           "rsu_00",
          "to_rsu":             "rsu_01",
          "from_place":         "Kurmannapalem, Visweswara Nagar [km 0.0]",
          "to_place":           "Kommadi, Gajuwaka [km 1.0]",
          "origin_lat":         17.685200,
          "origin_lon":         83.186200,
          "dest_lat":           17.693667,
          "dest_lon":           83.192733,
          "distance_m":         1002.4,
          "duration_free_s":    72.2,
          "duration_traffic_s": 108.3,
          "speed_kmh":          33.3,
          "congestion_ratio":   1.50,
          "congestion_level":   "moderate"
        },
        ...
      ]
    }

Congestion levels (ratio = duration_in_traffic / duration_free):
    free     ratio < 1.10   green   ← normal flow
    light    1.10 – 1.30    yellow  ← minor slow-down
    moderate 1.30 – 1.60    orange  ← noticeable delay
    heavy    1.60 – 2.00    red     ← significant delay
    jam      ratio ≥ 2.00   dark-red← near standstill (VANET alert threshold)
"""

import argparse
import csv
import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Timezone — Visakhapatnam = IST (UTC + 5:30) ───────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))

# ── Congestion thresholds ─────────────────────────────────────────────────────
# ratio = duration_in_traffic / duration_free_flow
# Levels chosen to match Google Maps colour bands for Indian highways.
_LEVELS = [
    (1.10, "free"),
    (1.30, "light"),
    (1.60, "moderate"),
    (2.00, "heavy"),
    (float("inf"), "jam"),
]

# ANSI colour codes for the terminal table (Kali/Linux terminal).
_COLOURS = {
    "free":     "\033[92m",   # bright green
    "light":    "\033[93m",   # bright yellow
    "moderate": "\033[33m",   # dark yellow / orange
    "heavy":    "\033[91m",   # bright red
    "jam":      "\033[31m",   # dark red
}
_RESET = "\033[0m"
_BOLD  = "\033[1m"

# Assumed free-flow speed on NH-16 (urban arterial, 50 km/h limit).
FREE_FLOW_KMH = 50.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def congestion_level(ratio: float) -> str:
    for threshold, label in _LEVELS:
        if ratio < threshold:
            return label
    return "jam"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_rsus(csv_path: Path) -> list[dict]:
    """Read rsu_positions.csv and return a list of RSU dicts."""
    rsus = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            rsus.append({
                "id":    row["id"],
                "lat":   float(row["lat"]),
                "lon":   float(row["lon"]),
                "place": row["nearest_place"],
            })
    if len(rsus) < 2:
        print("ERROR: need at least 2 RSUs in CSV to form a segment.", file=sys.stderr)
        sys.exit(1)
    return rsus


def _build_segment(i, a, b, dist_m, dur_free, dur_traffic) -> dict:
    """Assemble a segment dict from raw values."""
    ratio     = dur_traffic / dur_free if dur_free > 0 else 1.0
    speed_kmh = (dist_m / dur_traffic) * 3.6 if dur_traffic > 0 else 0.0
    return {
        "seg_id":              f"seg_{i:02d}_{i+1:02d}",
        "from_rsu":            a["id"],
        "to_rsu":              b["id"],
        "from_place":          a["place"],
        "to_place":            b["place"],
        "origin_lat":          round(a["lat"], 6),
        "origin_lon":          round(a["lon"], 6),
        "dest_lat":            round(b["lat"], 6),
        "dest_lon":            round(b["lon"], 6),
        "distance_m":          round(dist_m, 1),
        "duration_free_s":     round(dur_free, 1),
        "duration_traffic_s":  round(dur_traffic, 1),
        "speed_kmh":           round(speed_kmh, 1),
        "congestion_ratio":    round(ratio, 3),
        "congestion_level":    congestion_level(ratio),
    }


# ── Mock traffic generator ────────────────────────────────────────────────────

# Synthetic congestion pattern for 6-segment corridor (index = segment number).
# Scenario: smooth at south end, severe jam at segments 2–3, clearing northward.
_MOCK_RATIOS = [1.05, 1.20, 1.55, 2.15, 1.75, 1.30]

def mock_segments(rsus: list[dict]) -> list[dict]:
    """
    Generate synthetic traffic data — no API key required.

    The pattern simulates a realistic peak-hour scenario on NH-16:
      - Segments 0–1 (Gajuwaka side):   free / light flow
      - Segments 2–3 (mid-corridor):    moderate → jam   ← VANET alert zone
      - Segments 4–5 (NAD side):        heavy → light    ← clearing
    """
    free_ms = FREE_FLOW_KMH / 3.6
    segments = []
    for i in range(len(rsus) - 1):
        a, b     = rsus[i], rsus[i + 1]
        dist_m   = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
        dur_free = dist_m / free_ms
        ratio    = _MOCK_RATIOS[i] if i < len(_MOCK_RATIOS) else 1.05
        dur_traffic = dur_free * ratio
        segments.append(_build_segment(i, a, b, dist_m, dur_free, dur_traffic))
    return segments


# ── Live Google Maps fetch ────────────────────────────────────────────────────

def live_segments(rsus: list[dict], api_key: str) -> list[dict]:
    """
    Query Google Maps Directions API for each RSU-to-RSU segment.

    Uses departure_time=now + traffic_model=best_guess so the response
    includes duration_in_traffic (real-time congestion estimate).

    Falls back to free-flow estimate if the API call fails for a segment.
    """
    try:
        import googlemaps
    except ImportError:
        print("ERROR: googlemaps not installed.\n"
              "  pip install googlemaps  (or re-run scripts/install_kali.sh)",
              file=sys.stderr)
        sys.exit(1)

    gmaps      = googlemaps.Client(key=api_key)
    now_epoch  = int(time.time())
    free_ms    = FREE_FLOW_KMH / 3.6
    segments   = []

    for i in range(len(rsus) - 1):
        a, b   = rsus[i], rsus[i + 1]
        origin = f"{a['lat']},{a['lon']}"
        dest   = f"{b['lat']},{b['lon']}"

        print(f"  [{i+1}/{len(rsus)-1}] {a['id']} → {b['id']} ...", end=" ", flush=True)

        try:
            result = gmaps.directions(
                origin, dest,
                mode="driving",
                departure_time=now_epoch,
                traffic_model="best_guess",
            )
        except Exception as exc:
            print(f"WARN ({exc}) — using free-flow fallback")
            result = []

        if not result:
            dist_m      = haversine_m(a["lat"], a["lon"], b["lat"], b["lon"])
            dur_free    = dist_m / free_ms
            dur_traffic = dur_free
        else:
            leg         = result[0]["legs"][0]
            dist_m      = leg["distance"]["value"]
            dur_free    = leg["duration"]["value"]
            # duration_in_traffic is only present when departure_time was set.
            dur_traffic = leg.get("duration_in_traffic", leg["duration"])["value"]
            print("ok")

        segments.append(
            _build_segment(i, a, b, dist_m, dur_free, dur_traffic)
        )

        # Stay well under Google's 10 req/s rate limit.
        if i < len(rsus) - 2:
            time.sleep(0.2)

    return segments


# ── Console summary ───────────────────────────────────────────────────────────

_BAR_WIDTH = 20   # characters for the congestion bar

def _congestion_bar(ratio: float) -> str:
    """ASCII bar: filled = congestion above free-flow, max at ratio 2.5."""
    filled = min(int(round((ratio - 1.0) / 1.5 * _BAR_WIDTH)), _BAR_WIDTH)
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def print_summary(segments: list[dict], source: str, fetched_at: str) -> None:
    W = 110
    print()
    print("=" * W)
    print(f"  TRAFFIC STATE — NH-16 Gajuwaka → NAD, Visakhapatnam")
    print(f"  Source: {_BOLD}{source}{_RESET}   |   Fetched: {fetched_at}")
    print("=" * W)
    print(f"  {'Segment':<10}  {'From':<28}  {'To':<28}  "
          f"{'km/h':>6}  {'Ratio':>6}  {'Level':<9}  Bar")
    print("  " + "─" * (W - 2))

    for s in segments:
        lvl    = s["congestion_level"]
        colour = _COLOURS.get(lvl, "")
        bar    = _congestion_bar(s["congestion_ratio"])
        # Truncate place names so the table fits in 110 chars.
        frm = s["from_place"][:26] if len(s["from_place"]) > 26 else s["from_place"]
        to  = s["to_place"][:26]   if len(s["to_place"]) > 26   else s["to_place"]
        print(
            f"  {s['seg_id']:<10}  {frm:<28}  {to:<28}  "
            f"{s['speed_kmh']:>6.1f}  {s['congestion_ratio']:>6.3f}  "
            f"{colour}{lvl:<9}{_RESET}  {colour}{bar}{_RESET}"
        )

    print("  " + "─" * (W - 2))

    # Highlight any jam segments (the VANET alert threshold).
    jam_segs = [s["seg_id"] for s in segments if s["congestion_level"] == "jam"]
    if jam_segs:
        print(f"\n  {_COLOURS['jam']}{_BOLD}⚠  JAM detected on: "
              f"{', '.join(jam_segs)}{_RESET}  ← VANET U-turn alert will fire here")
    else:
        print(f"\n  {_COLOURS['free']}✓  No jams detected.{_RESET}")
    print("=" * W)
    print()


# ── JSON writer ───────────────────────────────────────────────────────────────

def write_json(segments: list[dict], source: str, out_path: Path) -> str:
    fetched_at = datetime.now(IST).isoformat(timespec="seconds")
    payload = {
        "fetched_at": fetched_at,
        "source":     source,
        "segments":   segments,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    return fetched_at


# ── Main ──────────────────────────────────────────────────────────────────────

def fetch_once(args) -> None:
    """Run one fetch cycle (mock or live) and save the result."""
    rsu_path = Path(args.rsu)
    out_path = Path(args.out)

    if not rsu_path.is_file():
        print(f"ERROR: {rsu_path} not found. Run scripts/build_corridor.sh first.",
              file=sys.stderr)
        sys.exit(1)

    rsus = load_rsus(rsu_path)

    if args.mock:
        source   = "mock"
        segments = mock_segments(rsus)
    else:
        api_key = os.getenv("GOOGLE_MAPS_API_KEY", "")
        if not api_key or api_key == "your_google_maps_api_key_here":
            print("ERROR: GOOGLE_MAPS_API_KEY not set in .env\n"
                  "  → Use --mock for a demo without a real key.",
                  file=sys.stderr)
            sys.exit(1)
        source   = "google_maps"
        print(f"Querying Google Maps for {len(rsus)-1} segments ...")
        segments = live_segments(rsus, api_key)

    fetched_at = write_json(segments, source, out_path)
    print_summary(segments, source, fetched_at)
    print(f"  JSON  → {out_path}")
    print()


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--rsu", default="corridor/rsu_positions.csv",
        help="RSU positions CSV (default: corridor/rsu_positions.csv)",
    )
    ap.add_argument(
        "--out", default="corridor/traffic_state.json",
        help="Output JSON path (default: corridor/traffic_state.json)",
    )
    ap.add_argument(
        "--mock", action="store_true",
        help="Use synthetic traffic data instead of Google Maps API",
    )
    ap.add_argument(
        "--loop", action="store_true",
        help="Repeat indefinitely, sleeping MAPS_REFRESH_SEC between polls",
    )
    ap.add_argument(
        "--interval", type=float, default=None,
        help="Override MAPS_REFRESH_SEC from .env (seconds between polls)",
    )
    args = ap.parse_args()

    if not args.loop:
        fetch_once(args)
        return 0

    # ── Continuous loop ──────────────────────────────────────────────────────
    refresh = args.interval or float(os.getenv("MAPS_REFRESH_SEC", "45"))
    print(f"Starting polling loop (interval = {refresh:.0f}s).  Ctrl-C to stop.\n")
    try:
        while True:
            fetch_once(args)
            print(f"  Sleeping {refresh:.0f}s …")
            time.sleep(refresh)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
