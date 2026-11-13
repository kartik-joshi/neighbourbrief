"""Neighbourhood profile: one page joining several open datasets for one neighbourhood.

Every block names its dataset, its date basis and when it was updated. Counts only, no rankings
and no comparison with other neighbourhoods (same fairness rules as the bulletin).
"""
from __future__ import annotations

import datetime as dt

from app.data import facts as F
from app.data import tps

COLLISIONS_LAYER = "Traffic_Collisions_Open_Data"
RATES_LAYER = "Neighbourhood_Crime_Rates_Open_Data"
COLLISION_FIELDS = ["EVENT_UNIQUE_ID", "OCC_YEAR", "OCC_MONTH", "FATALITIES", "INJURY_COLLISIONS",
                    "FTR_COLLISIONS", "PD_COLLISIONS", "PEDESTRIAN", "BICYCLE", "HOOD_158"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]
TREND = [  # (Neighbourhood Crime Rates field prefix, label shown)
    ("ASSAULT", "Assaults"), ("BREAKENTER", "Break-ins"), ("AUTOTHEFT", "Stolen vehicles"),
    ("THEFTFROMMV", "Thefts from vehicles"), ("ROBBERY", "Robberies"), ("THEFTOVER", "Thefts over $5,000"),
    ("BIKETHEFT", "Bike thefts"), ("SHOOTING", "Shootings"), ("HOMICIDE", "Homicides"),
]
SOURCES = {
    "crime": {"name": "Major Crime Indicators (current year and historical)", "publisher": "Toronto Police Service",
              "basis": "report date"},
    "collisions": {"name": "Traffic Collisions", "publisher": "Toronto Police Service", "basis": "date of the collision"},
    "trend": {"name": "Neighbourhood Crime Rates", "publisher": "Toronto Police Service", "basis": "calendar year counts"},
    "census": {"name": "Neighbourhood Profiles 2021", "publisher": "City of Toronto", "basis": "2021 Census"},
    "nco": {"name": "Neighbourhood Community Officer neighbourhoods", "publisher": "Toronto Police Service", "basis": "current list"},
    "services": {"name": "Community Assets", "publisher": "Toronto Police Service", "basis": "current list"},
    "boundaries": {"name": "Neighbourhoods (158 model)", "publisher": "City of Toronto, via TPS open data", "basis": "current boundaries"},
}


def fetch_collisions(hood_id: str, year: int) -> list[dict]:
    """Collisions in one neighbourhood for `year` and the year before (one cached file)."""
    where = f"HOOD_158 = '{hood_id}' AND OCC_YEAR IN ('{year}', '{year - 1}')"
    return tps.cached(f"collisions_{hood_id}_{year}",
                      lambda: tps._complete(COLLISIONS_LAYER, "all", tps.query_all(COLLISIONS_LAYER, where, COLLISION_FIELDS)))


def fetch_trend_all() -> list[dict]:
    def fetch():
        fields = ["HOOD_ID", "AREA_NAME"] + [c for c in tps.field_names(RATES_LAYER)
                                             if any(c.startswith(p + "_") and not c.startswith(p + "_RATE")
                                                    for p, _ in TREND) or c.startswith("POPULATION_")]
        return tps._at_least(tps.query_all(RATES_LAYER, "1=1", fields), 150, RATES_LAYER)
    return tps.cached("crime_trend_all", fetch, ttl=30 * 24 * 3600)


def _yes(v) -> bool:
    return str(v).strip().upper() in {"YES", "Y", "TRUE", "1"}


def road_safety(hood_id: str, month_start: dt.date) -> dict:
    """Collision counts for the month, the year to date and the same period last year."""
    year, m = month_start.year, month_start.month
    rows = {}
    for r in fetch_collisions(hood_id, year):  # one row per collision event
        rows.setdefault(r.get("EVENT_UNIQUE_ID") or id(r), r)

    def tally(sel):
        out = {"total": 0, "injury": 0, "pedestrian": 0, "cyclist": 0, "fatal": 0}
        for r in sel:
            out["total"] += 1
            out["injury"] += _yes(r.get("INJURY_COLLISIONS"))
            out["pedestrian"] += _yes(r.get("PEDESTRIAN"))
            out["cyclist"] += _yes(r.get("BICYCLE"))
            out["fatal"] += (r.get("FATALITIES") or 0) > 0
        return out

    def month_no(r):
        name = str(r.get("OCC_MONTH") or "")
        return MONTHS.index(name) + 1 if name in MONTHS else 0

    def yr(r):
        try:
            return int(r.get("OCC_YEAR"))
        except (TypeError, ValueError):
            return 0
    rs = list(rows.values())
    by_month = []
    for i in range(12):  # the 12 months ending with this one
        mm, yy = (m - 11 + i - 1) % 12 + 1, year + (m - 11 + i - 1) // 12
        by_month.append({"label": f"{MONTHS[mm - 1][:3]} {yy}", "count": sum(1 for r in rs if yr(r) == yy and month_no(r) == mm)})
    month = tally(r for r in rs if yr(r) == year and month_no(r) == m)
    ytd = tally(r for r in rs if yr(r) == year and 0 < month_no(r) <= m)
    prev = tally(r for r in rs if yr(r) == year - 1 and 0 < month_no(r) <= m)
    earlier = [b["count"] for b in by_month[-7:-1] if b["count"]]
    looks_partial = bool(earlier) and month["total"] < 0.6 * (sum(earlier) / len(earlier))
    return {"month": month, "ytd": ytd, "ytd_prev": prev,
            "pct_change": F.pct_change(ytd["total"], prev["total"]),
            "by_month": by_month, "looks_partial": looks_partial,
            "updated": _updated(COLLISIONS_LAYER)}


def crime_trend(hood_id: str) -> dict | None:
    """Yearly report counts per category for this neighbourhood (2014 to the latest full year)."""
    row = next((r for r in fetch_trend_all() if str(int(float(r.get("HOOD_ID") or 0))).zfill(3) == hood_id), None)
    if not row:
        return None
    years = sorted({int(k.rsplit("_", 1)[1]) for k in row if k.rsplit("_", 1)[-1].isdigit() and not k.startswith("POPULATION")})
    series = []
    for prefix, label in TREND:
        vals = [row.get(f"{prefix}_{y}") for y in years]
        if all(v is None for v in vals):
            continue
        series.append({"key": prefix, "label": label, "values": [int(v or 0) for v in vals]})
    pop_years = sorted(int(k.rsplit("_", 1)[1]) for k in row if k.startswith("POPULATION_") and row[k])
    return {"years": years, "series": series,
            "population": row.get(f"POPULATION_{pop_years[-1]}") if pop_years else None,
            "population_year": pop_years[-1] if pop_years else None,
            "updated": _updated(RATES_LAYER)}


def _updated(layer: str) -> str | None:
    try:
        ms = (tps.schema(layer).get("editingInfo") or {}).get("dataLastEditDate")
    except tps.TPSUnavailable:
        return None
    d = F.to_toronto(ms) if ms else None
    return d.strftime("%B %-d, %Y") if d else None


def sparkline(values: list[int], w: int = 120, h: int = 28) -> str:
    """SVG polyline points for a small trend line (no axes; the numbers are in the table)."""
    if not values:
        return ""
    hi = max(values) or 1
    step = w / max(len(values) - 1, 1)
    return " ".join(f"{i * step:.1f},{h - 2 - (v / hi) * (h - 4):.1f}" for i, v in enumerate(values))


def this_year_by_month(facts: dict) -> dict:
    """Reports per category for each month from January to the bulletin month (same counting
    rules as the bulletin: one per report and category, by report date in Toronto time). The
    bulletin month's numbers are the same as the bulletin's."""
    end = dt.date.fromisoformat(facts["period"]["month_end"])
    df = F.dedupe_frame(tps.fetch_ytd(facts["neighbourhood"]["id"]), "EVENT_UNIQUE_ID", "CRIME_TYPE", "REPORT_DATE_AGOL")
    months = [dt.date(end.year, m, 1) for m in range(1, end.month + 1)]
    labels = {c["key"]: c["label"] for c in facts["categories"]}
    series = []
    for key, label in labels.items():
        vals = []
        for m0 in months:
            m1 = (m0.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
            vals.append(F.count_between(df, m0, m1).get(key, 0))
        series.append({"key": key, "label": label, "values": vals, "max": max(vals) if vals else 0})
    return {"months": [m.strftime("%b") for m in months], "series": series}


def build_profile(hood_query: str, month: str | None = None) -> dict:
    facts = F.build_facts(hood_query, month=month)
    hid = facts["neighbourhood"]["id"]
    month_start = dt.date.fromisoformat(facts["period"]["month_start"])
    out = {"facts": facts, "warnings": []}
    try:
        out["road"] = road_safety(hid, month_start)
    except tps.TPSUnavailable as exc:
        out["road"] = None
        out["warnings"].append(f"Traffic collisions unavailable: {exc}")
    try:
        out["trend"] = crime_trend(hid)
        if out["trend"]:
            for s in out["trend"]["series"]:
                s["points"] = sparkline(s["values"])
    except tps.TPSUnavailable as exc:
        out["trend"] = None
        out["warnings"].append(f"Long-term counts unavailable: {exc}")
    try:
        out["monthly"] = this_year_by_month(facts)
    except tps.TPSUnavailable:
        out["monthly"] = None
    from app.languages import census_languages
    out["census"] = census_languages().get(hid)
    out["sources"] = SOURCES
    return out


def public_json(p: dict) -> dict:
    """Machine-readable profile (open data reuse): numbers, labels and sources only."""
    f = p["facts"]
    return {
        "neighbourhood": f["neighbourhood"],
        "period": {k: f["period"][k] for k in ("month_label", "month_start", "month_end", "ytd_label", "basis")},
        "reported_crime": [{k: c.get(k) for k in ("key", "label", "month", "ytd", "ytd_prev", "pct_change")} for c in f["categories"]],
        "road_safety": {k: v for k, v in (p.get("road") or {}).items()} or None,
        "yearly_counts": {k: v for k, v in (p.get("trend") or {}).items() if k != "series"} | (
            {"series": [{k: s[k] for k in ("key", "label", "values")} for s in p["trend"]["series"]]} if p.get("trend") else {}),
        "languages_2021": p.get("census"),
        "sources": p["sources"],
        "notes": ["Counts only; percent change only when both numbers are 10 or more.",
                  "No locations of incidents are included.",
                  "Not affiliated with or endorsed by the Toronto Police Service."],
    }
