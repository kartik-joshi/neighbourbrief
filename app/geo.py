"""Neighbourhood boundaries (City of Toronto 158 model, as published on the TPS open data site).

Used for the locator map, the offline outline and "Use my location". Only boundaries and the
community services' own addresses are ever mapped: never incident locations, heatmaps or "hot" areas.
"""
from __future__ import annotations

import math

from app.data import tps

BOUNDARY_SERVICE = f"{tps.BASE}/NEIGHBOURHOOD_158/FeatureServer/1"
BOUNDARY_KEY = "boundaries_158"


def boundaries() -> dict:
    """GeoJSON FeatureCollection of all 158 neighbourhoods (WGS84, simplified to about 20 m)."""
    def fetch():
        data = tps._get(f"{BOUNDARY_SERVICE}/query", {
            "where": "1=1", "outFields": "HOOD_ID,AREA_NAME", "returnGeometry": "true", "outSR": 4326,
            "maxAllowableOffset": 0.0002, "geometryPrecision": 5, "f": "geojson"})
        feats = [{"type": "Feature",
                  "properties": {"id": str(f["properties"]["HOOD_ID"]).zfill(3), "name": f["properties"]["AREA_NAME"]},
                  "geometry": f["geometry"]}
                 for f in data.get("features", []) if f.get("geometry")]
        if len(feats) < 100:
            raise tps.TPSUnavailable("The neighbourhood boundaries layer returned too few shapes.")
        return {"type": "FeatureCollection", "features": feats}
    return tps.cached(BOUNDARY_KEY, fetch, ttl=30 * 24 * 3600)


def feature(hood_id: str) -> dict | None:
    hid = str(hood_id).zfill(3)
    return next((f for f in boundaries()["features"] if f["properties"]["id"] == hid), None)


def _polygons(geom: dict) -> list[list[list[list[float]]]]:
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def _in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(geom: dict, lon: float, lat: float) -> bool:
    return any(_in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:])
               for poly in _polygons(geom))


def locate(lon: float, lat: float) -> dict | None:
    """The neighbourhood containing a point (used by tests; the browser does this itself)."""
    return next((f["properties"] for f in boundaries()["features"] if contains(f["geometry"], lon, lat)), None)


def outline(hood_id: str, size: int = 240, points: list[tuple[float, float]] | None = None) -> dict | None:
    """A plain SVG path of one neighbourhood (for offline mode and printing): {"viewBox", "d", "points"}.

    `points` are (lon, lat) pairs placed with the same projection (service pins)."""
    f = feature(hood_id)
    if not f:
        return None
    polys = _polygons(f["geometry"])
    pts = [p for poly in polys for ring in poly for p in ring]
    lat0 = sum(p[1] for p in pts) / len(pts)
    k = math.cos(math.radians(lat0))
    xs, ys = [p[0] * k for p in pts], [-p[1] for p in pts]
    minx, miny = min(xs), min(ys)
    span = max(max(xs) - minx, max(ys) - miny) or 1
    scale = (size - 16) / span

    def xy(p):
        return f"{(p[0] * k - minx) * scale + 8:.1f},{(-p[1] - miny) * scale + 8:.1f}"
    d = " ".join("M" + " L".join(xy(p) for p in ring) + " Z" for poly in polys for ring in poly)
    w = (max(xs) - minx) * scale + 16
    h = (max(ys) - miny) * scale + 16
    pins = [[round((lon * k - minx) * scale + 8, 1), round((-lat - miny) * scale + 8, 1)] for lon, lat in points or []]
    return {"viewBox": f"0 0 {w:.0f} {h:.0f}", "d": d, "points": pins}


def bbox(geom: dict) -> list[float]:
    pts = [p for poly in _polygons(geom) for ring in poly for p in ring]
    return [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)]
