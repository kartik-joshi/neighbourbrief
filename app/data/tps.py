"""TPS ArcGIS REST access: schema inspection, paginated queries, 12-hour cache, offline snapshot."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import CACHE_DIR, SNAPSHOT_DIR, get_settings

log = logging.getLogger(__name__)

BASE = "https://services.arcgis.com/S9th0jAJ7bqgIRjw/ArcGIS/rest/services"
YTD_LAYER = "YTD_CRIME_WM"
MCI_LAYER = "Major_Crime_Indicators_Open_Data"
NCO_LAYER = "NCO_Neighbourhoods_WM_A"
ASSETS_LAYER = "Community_Assets_TO"

CACHE_TTL_SECONDS = 12 * 3600
PAGE_SIZE = 2000
TIMEOUT = httpx.Timeout(30.0, connect=10.0)

YTD_FIELDS = ["EVENT_UNIQUE_ID", "REPORT_DATE_AGOL", "OCC_DATE_AGOL", "CRIME_TYPE", "DIVISION",
              "HOOD_158", "NEIGHBOURHOOD_158"]


class TPSUnavailable(RuntimeError):
    """Raised when TPS open data cannot be reached (or is missing from the offline snapshot)."""


def _safe_key(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def cached(key: str, fetch, *, ttl: int = CACHE_TTL_SECONDS) -> Any:
    """Return data for `key` from snapshot (offline), fresh cache, or `fetch()` (online).

    Online: a fresh cache hit is returned; otherwise fetch and store. If the fetch fails and a stale
    cache or snapshot copy exists, that copy is used rather than failing the request.
    """
    name = _safe_key(key) + ".json"
    cache_path = CACHE_DIR / name
    snap_path = SNAPSHOT_DIR / name

    if get_settings().demo_offline:
        for p in (snap_path, cache_path):
            if p.exists():
                return _read_json(p)
        raise TPSUnavailable(
            "Offline demo mode is on and this neighbourhood is not in the demo snapshot. "
            "Choose Thorncliffe Park, Milliken or Westminster-Branson, or turn DEMO_OFFLINE off."
        )

    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < ttl:
        return _read_json(cache_path)
    try:
        data = fetch()
    except (httpx.HTTPError, TPSUnavailable, ValueError) as exc:
        for p in (cache_path, snap_path):
            if p.exists():
                log.warning("TPS fetch failed (%s); using saved copy %s", exc, p.name)
                return _read_json(p)
        if isinstance(exc, TPSUnavailable):
            raise
        raise TPSUnavailable(
            "Couldn't reach Toronto Police open data right now. Check the internet connection "
            "and try again, or run in offline demo mode (DEMO_OFFLINE=true)."
        ) from exc
    _write_json(cache_path, data)
    return data


def _get(url: str, params: dict[str, Any]) -> dict:
    if get_settings().demo_offline:  # hard guard: never touch the network offline
        raise TPSUnavailable("Network disabled in offline demo mode.")
    with httpx.Client(timeout=TIMEOUT) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise TPSUnavailable(f"TPS open data returned an error: {err.get('message') or err}")
    return data


def layer_url(layer: str) -> str:
    return f"{BASE}/{layer}/FeatureServer/0"


def _without_drawing_info(meta: dict) -> dict:
    """The map styling isn't used, and TPS's has picture symbols stored as signed (long-expired) S3 links
    whose temporary AWS key IDs trip secret scanners, so it is never saved."""
    return {k: v for k, v in meta.items() if k != "drawingInfo"} if isinstance(meta, dict) else meta


def schema(layer: str) -> dict:
    """Layer metadata (fields, editingInfo). Always inspected before querying."""
    return _without_drawing_info(cached(f"schema_{layer}", lambda: _without_drawing_info(_get(layer_url(layer), {"f": "pjson"}))))


def field_names(layer: str) -> set[str]:
    return {f["name"] for f in schema(layer).get("fields", [])}


def pick_field(layer: str, candidates: list[str]) -> str:
    names = field_names(layer)
    for c in candidates:
        if c in names:
            return c
    raise TPSUnavailable(f"TPS layer {layer} no longer has any of the fields {candidates}.")


def query_all(layer: str, where: str, out_fields: list[str]) -> list[dict]:
    """Paginated query returning a list of attribute dicts."""
    available = field_names(layer)
    fields = [f for f in out_fields if f in available]
    oid = pick_field(layer, ["OBJECTID", "OBJECTID_1", "FID"])
    rows: list[dict] = []
    offset = 0
    while True:
        data = _get(
            f"{layer_url(layer)}/query",
            {
                "where": where,
                "outFields": ",".join(fields),
                "returnGeometry": "false",
                "orderByFields": oid,
                "resultOffset": offset,
                "resultRecordCount": PAGE_SIZE,
                "f": "json",
            },
        )
        feats = data.get("features", [])
        rows.extend(f.get("attributes", {}) for f in feats)
        if not feats or not (data.get("exceededTransferLimit") or len(feats) >= PAGE_SIZE):
            break
        offset += len(feats)
    return rows


def _hood_where(field: str, hood_id: str) -> str:
    hood_id = re.sub(r"\D", "", hood_id).zfill(3)
    return f"{field}='{hood_id}'"


REFRESHING = ("Toronto Police open data is being refreshed right now (the {layer} layer is {state}). "
              "Try again in a few minutes.")
DROP_TOLERANCE = 0.8  # a layer with fewer than 80% of the records it had before is still loading


def layer_total(layer: str) -> int:
    data = _get(f"{layer_url(layer)}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"})
    return int(data.get("count") or 0)


def guard_complete(layer: str, period: str) -> None:
    """Refuse data fetched while TPS is reloading a layer (seen live: YTD_CRIME_WM was empty for a
    while after its daily refresh). Empty, or well below the most records seen this `period`
    (calendar year for the current-year layer), means "not ready": raise, so `cached` keeps the
    last good copy instead of saving zeros."""
    total = layer_total(layer)
    if total == 0:
        raise TPSUnavailable(REFRESHING.format(layer=layer, state="empty"))
    best = baseline(layer, period)
    if best and total < DROP_TOLERANCE * best:
        raise TPSUnavailable(REFRESHING.format(layer=layer, state=f"partly loaded: {total:,} of about {best:,} records"))
    note_total(layer, total, period)


def _total_path(layer: str):
    return CACHE_DIR / f"layer_total_{_safe_key(layer)}.json"


def baseline(layer: str, period: str) -> int:
    """Most records seen in `layer` this period (by any check: fetches, Status, Data health)."""
    p = _total_path(layer)
    seen = _read_json(p) if p.exists() else {}
    return seen.get("max", 0) if seen.get("period") == period else 0


def note_total(layer: str, total: int, period: str) -> None:
    """Remember a complete-looking total as the baseline (a total well below it is never recorded)."""
    if not total:
        return
    best = baseline(layer, period)
    if best and total < DROP_TOLERANCE * best:
        return
    _write_json(_total_path(layer), {"period": period, "max": max(best, total), "last": total, "checked": time.time()})


def period_for(layer: str) -> str:
    import datetime as _dt
    return str(_dt.date.today().year) if layer == YTD_LAYER else "all"


def _complete(layer: str, period: str, rows: list[dict]) -> list[dict]:
    guard_complete(layer, period)
    return rows


def _at_least(rows: list, minimum: int, layer: str) -> list:
    """City-wide lists (all NCO rows, all neighbourhoods) can't legitimately be nearly empty."""
    if len(rows) < minimum:
        raise TPSUnavailable(REFRESHING.format(layer=layer, state=f"incomplete: {len(rows)} rows"))
    return rows


def fetch_ytd(hood_id: str) -> list[dict]:
    """Current-year offences for one neighbourhood (YTD layer, updated daily)."""
    return cached(
        f"ytd_{hood_id}",
        lambda: _complete(YTD_LAYER, period_for(YTD_LAYER),
                          query_all(YTD_LAYER, _hood_where("HOOD_158", hood_id), YTD_FIELDS)),
    )


def mci_fields() -> dict[str, str]:
    return {
        "category": pick_field(MCI_LAYER, ["MCI_CATEGORY", "CSI_CATEGORY"]),
        "report_date": pick_field(MCI_LAYER, ["REPORT_DATE", "REPORT_DATE_AGOL"]),
        "report_year": pick_field(MCI_LAYER, ["REPORT_YEAR"]),
        "hood": pick_field(MCI_LAYER, ["HOOD_158"]),
    }


def fetch_mci_year(hood_id: str, year: int) -> list[dict]:
    """Historic MCI rows for one neighbourhood and report year (for same-period comparison)."""
    f = mci_fields()
    where = f"{_hood_where(f['hood'], hood_id)} AND {f['report_year']}='{year}'"
    return cached(
        f"mci_{hood_id}_{year}",
        lambda: _complete(MCI_LAYER, "all", query_all(MCI_LAYER, where, ["EVENT_UNIQUE_ID", f["report_date"], f["category"]])),
        ttl=7 * 24 * 3600,  # historic years change rarely
    )


# Last year's counts for two categories the Major Crime Indicators dataset doesn't cover; both are
# published by report date, like the rest. (Shootings and homicides are published by occurrence date
# only, so they stay counts without a comparison.)
PREV_YEAR_LAYERS = {"Theft from Motor Vehicle": "Theft_From_Motor_Vehicle_Open_Data",
                    "Bike Theft": "Bicycle_Thefts_Open_Data"}


def fetch_category_year(key: str, hood_id: str, year: int) -> list[dict]:
    """Rows (EVENT_UNIQUE_ID, REPORT_DATE, CATEGORY) for one of PREV_YEAR_LAYERS, one neighbourhood and report year."""
    layer = PREV_YEAR_LAYERS[key]
    where = f"{_hood_where('HOOD_158', hood_id)} AND REPORT_YEAR={int(year)}"
    rows = cached(f"{layer}_{hood_id}_{year}",
                  lambda: _complete(layer, "all", query_all(layer, where, ["EVENT_UNIQUE_ID", "REPORT_DATE"])),
                  ttl=7 * 24 * 3600)
    return [{**r, "CATEGORY": key} for r in rows]


NCO_FIELDS = ["Division", "FirstName", "LastName", "Rank", "Badge", "Neighbourh", "Neighbour2",
              "Neighbour3", "WorkPhoneN", "EmailAddre", "NCO", "DIV", "UNIT_NAME", "ADDRESS", "PHONE"]


def fetch_nco_all() -> list[dict]:
    """All NCO rows (one per officer per neighbourhood; ~470 rows). Also our neighbourhood list."""
    return cached("nco_all", lambda: _at_least(query_all(NCO_LAYER, "1=1", NCO_FIELDS), 100, NCO_LAYER))


def fetch_neighbourhood_list_from_ytd() -> list[dict]:
    def fetch():
        data = _get(
            f"{layer_url(YTD_LAYER)}/query",
            {"where": "1=1", "outFields": "HOOD_158,NEIGHBOURHOOD_158,DIVISION",
             "returnDistinctValues": "true", "returnGeometry": "false", "f": "json"},
        )
        return _at_least([f["attributes"] for f in data.get("features", [])], 100, YTD_LAYER)
    return cached("hoods_ytd", fetch)


ASSET_FIELDS = ["OBJECTID", "ResourceAgencyNum", "PublicName", "Category", "PhysicalAddress1",
                "Phone1Number", "PhoneNumberBusinessLine", "PhoneNumberHotline", "WebsiteAddress",
                "AgencyStatus", "HoodID", "Latitude", "Longitude"]


def fetch_assets(hood_id: str) -> list[dict]:
    """Community services TPS lists for one neighbourhood (Community_Assets_TO)."""
    return cached(
        f"assets_{hood_id}",
        lambda: _complete(ASSETS_LAYER, "all", query_all(ASSETS_LAYER, _hood_where(pick_field(ASSETS_LAYER, ["HoodID"]), hood_id),
                                                         ASSET_FIELDS)),
        ttl=7 * 24 * 3600,
    )


def data_freshness_ms() -> int | None:
    info = schema(YTD_LAYER).get("editingInfo") or {}
    return info.get("dataLastEditDate") or info.get("lastEditDate")
