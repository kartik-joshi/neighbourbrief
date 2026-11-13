"""Which of Toronto's 25 wards each neighbourhood lies in, and how much of it (share of its area).

    .venv/bin/python scripts/build_ward_map.py

Wards (City of Toronto open data, "City Wards", 25-ward model) and neighbourhoods (158 model) don't
line up, so each neighbourhood is sampled on a fine grid of points and each point is placed in a ward.
Written to config/neighbourhood_wards.json (committed: the app needs no network for it). Counts are
never split by area: a ward brief lists each neighbourhood's own numbers and how much of it is in the ward.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import geo  # noqa: E402

WARDS_URL = ("https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/5e7a8234-f805-43ac-820f-03d7c360b588/"
             "resource/737b29e0-8329-4260-b6af-21555ab24f28/download/City%20Wards%20Data%20-%204326.geojson")
OUT = ROOT / "config" / "neighbourhood_wards.json"
GRID = 60          # points per side of each neighbourhood's bounding box
MIN_SHARE = 5      # % of a neighbourhood's area below which a ward is ignored (boundary slivers)


def main() -> int:
    wards = httpx.get(WARDS_URL, timeout=60, follow_redirects=True).json()["features"]
    names = {int(f["properties"]["AREA_SHORT_CODE"]): f["properties"]["AREA_NAME"] for f in wards}
    if len(names) != 25:
        raise SystemExit(f"Expected 25 wards, got {len(names)}")
    hoods = geo.boundaries()["features"]
    out = {}
    for h in hoods:
        x0, y0, x1, y1 = geo.bbox(h["geometry"])
        counts, inside = Counter(), 0
        for i in range(GRID):
            for j in range(GRID):
                lon, lat = x0 + (i + .5) * (x1 - x0) / GRID, y0 + (j + .5) * (y1 - y0) / GRID
                if not geo.contains(h["geometry"], lon, lat):
                    continue
                inside += 1
                w = next((int(f["properties"]["AREA_SHORT_CODE"]) for f in wards if geo.contains(f["geometry"], lon, lat)), None)
                if w:
                    counts[w] += 1
        shares = sorted(((w, round(100 * n / inside)) for w, n in counts.items() if inside), key=lambda x: -x[1])
        out[h["properties"]["id"]] = [[w, s] for w, s in shares if s >= MIN_SHARE]
        print(f"{h['properties']['id']} {h['properties']['name'][:30]:30} {out[h['properties']['id']]}")
    missing = [hid for hid, v in out.items() if not v]
    if missing:
        raise SystemExit(f"No ward found for {missing}")
    OUT.write_text(json.dumps({"source": "City of Toronto open data: City Wards (25-ward model) and Neighbourhoods (158)",
                               "built": dt.date.today().isoformat(), "wards": {str(k): v for k, v in sorted(names.items())},
                               "hoods": out}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {OUT.relative_to(ROOT)}: {len(out)} neighbourhoods, {len(names)} wards")
    return 0


if __name__ == "__main__":
    sys.exit(main())
