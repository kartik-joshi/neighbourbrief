"""Provenance: for every count in a bulletin, a query on the TPS open data portal that anyone can
open to get the same number (distinct EVENT_UNIQUE_ID per category, by report date in Toronto time).
Checked live: 27/27 counts in three neighbourhoods matched on Oct 23, 2026."""
from __future__ import annotations

import datetime as dt
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from app.data import tps

TORONTO = ZoneInfo("America/Toronto")


def _utc(d: dt.date) -> str:
    """Toronto midnight at the start of `d`, as the UTC timestamp ArcGIS compares against."""
    t = dt.datetime(d.year, d.month, d.day, tzinfo=TORONTO).astimezone(dt.timezone.utc)
    return t.strftime("%Y-%m-%d %H:%M:%S")


def _url(layer: str, where: str) -> str:
    q = {"where": where, "returnDistinctValues": "true", "outFields": "EVENT_UNIQUE_ID",
         "returnCountOnly": "true", "returnGeometry": "false", "f": "pjson"}
    return f"{tps.layer_url(layer)}/query?{urlencode(q)}"


def queries(facts: dict) -> dict[str, dict]:
    """{category key: {"month": url, "ytd": url, "prev": url or None}}."""
    p = facts["period"]
    hid = facts["neighbourhood"]["id"]
    start, end = dt.date.fromisoformat(p["month_start"]), dt.date.fromisoformat(p["month_end"])
    after = end + dt.timedelta(days=1)
    jan1, prev_jan1 = dt.date(end.year, 1, 1), dt.date(end.year - 1, 1, 1)
    try:
        prev_after = after.replace(year=end.year - 1)
    except ValueError:  # Feb 29
        prev_after = dt.date(end.year - 1, 3, 1)
    try:
        mf = tps.mci_fields()
    except tps.TPSUnavailable:
        mf = None
    out = {}
    for c in facts["categories"]:
        key = c["key"].replace("'", "''")
        base = f"HOOD_158='{hid}' AND CRIME_TYPE='{key}'"
        row = {
            "month": _url(tps.YTD_LAYER, f"{base} AND REPORT_DATE_AGOL >= TIMESTAMP '{_utc(start)}' "
                                         f"AND REPORT_DATE_AGOL < TIMESTAMP '{_utc(after)}'"),
            "ytd": _url(tps.YTD_LAYER, f"{base} AND REPORT_DATE_AGOL >= TIMESTAMP '{_utc(jan1)}' "
                                       f"AND REPORT_DATE_AGOL < TIMESTAMP '{_utc(after)}'"),
            "prev": None,
        }
        if c["key"] in tps.PREV_YEAR_LAYERS and c.get("ytd_prev") is not None:  # its own dataset, no category field
            row["prev"] = _url(tps.PREV_YEAR_LAYERS[c["key"]], f"HOOD_158='{hid}' "
                                                                f"AND REPORT_DATE >= TIMESTAMP '{_utc(prev_jan1)}' "
                                                                f"AND REPORT_DATE < TIMESTAMP '{_utc(prev_after)}'")
        elif mf and c.get("ytd_prev") is not None:
            row["prev"] = _url(tps.MCI_LAYER, f"{mf['hood']}='{hid}' AND {mf['category']}='{key}' "
                                              f"AND {mf['report_date']} >= TIMESTAMP '{_utc(prev_jan1)}' "
                                              f"AND {mf['report_date']} < TIMESTAMP '{_utc(prev_after)}'")
        out[c["key"]] = row
    return out
