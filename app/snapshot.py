"""Offline snapshot: save TPS data for chosen neighbourhoods into data/demo_snapshot/.

`snapshot_meta.json` records, per neighbourhood, the Toronto date its data was saved; in offline
mode that date is "today" for that neighbourhood, so its last complete month matches its data.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil

from app.config import CACHE_DIR, SNAPSHOT_DIR, get_settings
from app.data import tps

META = "snapshot_meta.json"
SHARED_KEYS = [f"schema_{tps.YTD_LAYER}", f"schema_{tps.MCI_LAYER}", f"schema_{tps.NCO_LAYER}",
               f"schema_{tps.ASSETS_LAYER}",
               "nco_all", "hoods_ytd", "azure_languages", "boundaries_158", "crime_trend_all",
               "schema_Traffic_Collisions_Open_Data", "schema_Neighbourhood_Crime_Rates_Open_Data",
               "schema_NEIGHBOURHOOD_158_1"] + [  # Data health page (app/data/health.py DATASETS)
    f"stats_{s}_{layer}" for s, layer in [
        ("YTD_CRIME_WM", 0), ("Major_Crime_Indicators_Open_Data", 0), ("Traffic_Collisions_Open_Data", 0),
        ("Neighbourhood_Crime_Rates_Open_Data", 0), ("NCO_Neighbourhoods_WM_A", 0), ("Community_Assets_TO", 0),
        ("NEIGHBOURHOOD_158", 1)]]


def load_meta() -> dict:
    p = SNAPSHOT_DIR / META
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def saved_neighbourhoods() -> dict[str, dict]:
    """{hood_id: {"name", "date"}} for neighbourhoods available offline."""
    meta = load_meta()
    out = dict(meta.get("saved", {}))
    if not out and meta.get("neighbourhoods"):  # older meta format: names + one date
        from app.data import facts as F
        for name in meta["neighbourhoods"]:
            try:
                hood = F.find_neighbourhood(name)
            except tps.TPSUnavailable:
                hood = None
            if hood:
                out[hood["id"]] = {"name": hood["name"], "date": meta.get("snapshot_date")}
    return out


def date_for(hood_id: str) -> dt.date | None:
    info = saved_neighbourhoods().get(hood_id)
    if info and info.get("date"):
        return dt.date.fromisoformat(info["date"])
    meta = load_meta()
    return dt.date.fromisoformat(meta["snapshot_date"]) if meta.get("snapshot_date") else None


def _copy(key: str) -> str | None:
    src = CACHE_DIR / f"{tps._safe_key(key)}.json"
    if src.exists():
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, SNAPSHOT_DIR / src.name)
        return src.name
    return None


def save_for_offline(names: list[str]) -> list[dict]:
    """Fetch fresh TPS data for these neighbourhoods and add them to the offline snapshot.

    Raises tps.TPSUnavailable (network) or ValueError (unknown name / offline mode)."""
    from app.data import facts as F
    from app.store import now_iso

    if get_settings().demo_offline:
        raise ValueError("Turn offline mode off (DEMO_OFFLINE=false) to download data.")
    today = F.toronto_today()
    _, month_end = F.last_full_month(today)
    hoods = []
    for name in names:
        hood = F.find_neighbourhood(name)
        if not hood:
            raise ValueError(f"Unknown neighbourhood: {name}")
        hoods.append(hood)
    keys = list(SHARED_KEYS)
    for h in hoods:
        keys += [f"ytd_{h['id']}", f"mci_{h['id']}_{month_end.year - 1}", f"assets_{h['id']}",
                 f"collisions_{h['id']}_{month_end.year}"]
        keys += [f"{layer}_{h['id']}_{month_end.year - 1}" for layer in tps.PREV_YEAR_LAYERS.values()]
    for k in keys:  # force fresh downloads
        (CACHE_DIR / f"{tps._safe_key(k)}.json").unlink(missing_ok=True)
    tps.schema(tps.YTD_LAYER), tps.schema(tps.MCI_LAYER), tps.schema(tps.NCO_LAYER)
    tps.fetch_nco_all()
    tps.fetch_neighbourhood_list_from_ytd()
    try:
        from app.ai.translate import azure_supported_codes
        azure_supported_codes()
    except Exception:
        pass
    for h in hoods:
        tps.fetch_ytd(h["id"])
        tps.fetch_mci_year(h["id"], month_end.year - 1)
        for key in tps.PREV_YEAR_LAYERS:
            try:
                tps.fetch_category_year(key, h["id"], month_end.year - 1)
            except tps.TPSUnavailable:
                pass  # optional comparison
        try:
            tps.fetch_assets(h["id"])
        except tps.TPSUnavailable:
            pass  # services are optional
        try:  # neighbourhood profile datasets (optional)
            from app import geo
            from app.data import profile
            profile.fetch_collisions(h["id"], month_end.year)
            profile.fetch_trend_all()
            geo.boundaries()
        except tps.TPSUnavailable:
            pass
    files = [f for f in (_copy(k) for k in keys) if f]

    meta = load_meta()
    saved = saved_neighbourhoods()
    for h in hoods:
        saved[h["id"]] = {"name": h["name"], "date": today.isoformat()}
    meta.update({
        "snapshot_date": today.isoformat(),
        "saved_at": now_iso(),
        "saved": saved,
        "neighbourhoods": [v["name"] for v in saved.values()],
        "files": sorted(set(meta.get("files", [])) | set(files)),
    })
    (SNAPSHOT_DIR / META).write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    return [{"id": h["id"], "name": h["name"], "date": today.isoformat()} for h in hoods]
