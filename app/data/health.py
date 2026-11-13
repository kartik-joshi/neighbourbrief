"""Dataset health: is each open dataset NeighbourBrief depends on fresh, complete and still shaped
the way the app expects? Findings are written so they can be sent back to the data publisher."""
from __future__ import annotations

import datetime as dt
import json

from app.data import facts as F
from app.data import tps

# (key, label, publisher, service, layer, fields the app needs, newest-record date field, expected update interval in days)
DATASETS = [
    ("ytd", "Current-year crime (YTD_CRIME_WM)", "Toronto Police Service", "YTD_CRIME_WM", 0,
     ["EVENT_UNIQUE_ID", "REPORT_DATE_AGOL", "CRIME_TYPE", "HOOD_158", "DIVISION"], "REPORT_DATE_AGOL", 7),
    ("mci", "Major Crime Indicators (historical)", "Toronto Police Service", "Major_Crime_Indicators_Open_Data", 0,
     ["EVENT_UNIQUE_ID", "REPORT_DATE", "MCI_CATEGORY|CSI_CATEGORY", "HOOD_158", "REPORT_YEAR"], "REPORT_DATE", 120),
    ("collisions", "Traffic Collisions", "Toronto Police Service", "Traffic_Collisions_Open_Data", 0,
     ["EVENT_UNIQUE_ID", "OCC_YEAR", "OCC_MONTH", "HOOD_158", "INJURY_COLLISIONS", "PEDESTRIAN", "BICYCLE", "FATALITIES"], "OCC_DATE", 45),
    ("trend", "Neighbourhood Crime Rates", "Toronto Police Service", "Neighbourhood_Crime_Rates_Open_Data", 0,
     ["HOOD_ID", "AREA_NAME"], None, 400),
    ("nco", "Neighbourhood Community Officers", "Toronto Police Service", "NCO_Neighbourhoods_WM_A", 0,
     ["Neighbourh", "DIV"], None, 180),
    ("assets", "Community Assets", "Toronto Police Service", "Community_Assets_TO", 0,
     ["PublicName", "Category", "HoodID", "AgencyStatus", "Latitude", "Longitude"], None, 400),
    ("boundaries", "Neighbourhoods (158 model)", "City of Toronto, via TPS open data", "NEIGHBOURHOOD_158", 1,
     ["HOOD_ID", "AREA_NAME"], None, None),
]


# Plain-language explainers: what one record is, how NeighbourBrief uses it, what to keep in mind.
EXPLAIN = {
    "ytd": ("One record per offence reported to police this calendar year in the major crime categories, with the "
            "neighbourhood and the date it was reported.",
            "Bulletin counts for this month and the year so far, one per report and category.",
            "A report is not a charge or a conviction. TPS reloads this layer daily by emptying it first; "
            "NeighbourBrief ignores it while it reloads."),
    "mci": ("One record per offence in five Major Crime Indicators (assault, break and enter, auto theft, robbery, "
            "theft over $5,000), going back years.",
            "Last year's number for the same period, so a change can be shown.",
            "Theft from vehicles, bike theft and shootings aren't in it, so their change shows as n/a."),
    "collisions": ("One record per traffic collision police attended or were told about, by the date it happened, with "
                   "whether anyone was injured or killed and whether a pedestrian or cyclist was involved.",
                   "Road safety on neighbourhood profiles and when checking a text's numbers.",
                   "The latest month fills in as late reports arrive; profiles mark it when it looks incomplete."),
    "trend": ("One record per neighbourhood with yearly counts (and rates per 100,000 residents) for each major category.",
              "Twelve years of reports on neighbourhood profiles (counts only).",
              "Ends with the last full calendar year."),
    "nco": ("One record per Neighbourhood Community Officer and neighbourhood, with their division and contact.",
            "Officers and contacts on bulletins and profiles; the list of neighbourhoods.",
            "Neighbourhoods without officers show their division's contact instead."),
    "assets": ("One record per community service (food, legal, health, seniors and more) with address, phone and "
               "neighbourhood.", "Local support services on bulletins and pins on maps.",
               "Some services listed for a neighbourhood are located elsewhere (for example a head office); maps leave those off."),
    "boundaries": ("The outline of each of the City's 158 neighbourhoods.",
                   "Maps and \"Use my location\".", "Places only; no data is drawn on them."),
}


def _url(service: str, layer: int) -> str:
    return f"{tps.BASE}/{service}/FeatureServer/{layer}"


def _schema(service: str, layer: int) -> dict:
    return tps.cached(f"schema_{service}" if layer == 0 else f"schema_{service}_{layer}",
                      lambda: tps._get(_url(service, layer), {"f": "pjson"}))


def _stats(service: str, layer: int, date_field: str | None) -> dict:
    def fetch():
        out = {"count": tps._get(f"{_url(service, layer)}/query", {"where": "1=1", "returnCountOnly": "true", "f": "json"}).get("count")}
        if layer == 0 and out["count"]:
            tps.note_total(service, int(out["count"]), tps.period_for(service))  # baseline for the refresh guard
        if date_field:
            st = [{"statisticType": "max", "onStatisticField": date_field, "outStatisticFieldName": "latest"}]
            feats = tps._get(f"{_url(service, layer)}/query",
                             {"where": "1=1", "outStatistics": json.dumps(st), "f": "json"}).get("features") or []
            out["latest"] = feats[0]["attributes"].get("latest") if feats else None
        return out
    return tps.cached(f"stats_{service}_{layer}", fetch)


def check_dataset(entry, today: dt.date) -> dict:
    key, label, publisher, service, layer, needed, date_field, interval = entry
    item = {"key": key, "label": label, "publisher": publisher, "url": _url(service, layer), "explain": EXPLAIN.get(key),
            "state": "ok", "problems": [], "updated": None, "age_days": None, "count": None, "latest": None}
    try:
        schema = _schema(service, layer)
    except tps.TPSUnavailable as exc:
        item.update(state="unknown", problems=[f"Couldn't read the dataset: {exc}"])
        return item
    names = {f["name"] for f in schema.get("fields", [])}
    missing = [f for f in needed if not any(alt in names for alt in f.split("|"))]
    if missing:
        item["state"] = "bad"
        item["problems"].append("Fields the app needs are missing: " + ", ".join(missing) + ".")
    ms = (schema.get("editingInfo") or {}).get("dataLastEditDate")
    if ms:
        upd = F.to_toronto(ms)
        item["updated"] = upd.strftime("%B %-d, %Y")
        item["age_days"] = (today - upd.date()).days
        if interval and item["age_days"] > interval:
            item["state"] = "warn" if item["state"] == "ok" else item["state"]
            item["problems"].append(f"Last updated {item['age_days']} days ago; usually updated at least every {interval} days.")
    try:
        st = _stats(service, layer, date_field if date_field in names else None)
        item["count"] = st.get("count")
        if item["count"] == 0:
            item["state"] = "warn" if item["state"] == "ok" else item["state"]
            item["problems"].append("The layer is empty right now; TPS is probably refreshing it. The app keeps using its last good copy.")
        if st.get("latest"):
            item["latest"] = F.to_toronto(st["latest"]).strftime("%B %-d, %Y")
    except tps.TPSUnavailable:
        pass
    return item


def findings(today: dt.date) -> list[dict]:
    """Gaps found by the app itself while joining the datasets (useful feedback for publishers)."""
    out = []
    try:
        hoods = F.list_neighbourhoods()
        with_nco = {h["id"] for h in hoods if (F.nco_info(h["id"]) or {}).get("has_nco")}
        if hoods:
            out.append({"dataset": "Neighbourhood Community Officers", "kind": "coverage",
                        "text": f"{len(with_nco & {h['id'] for h in hoods})} of {len(hoods)} neighbourhoods list Neighbourhood Community Officers; the others show the division contact."})
    except tps.TPSUnavailable:
        pass
    out.append({"dataset": "Major Crime Indicators (historical)", "kind": "scope",
                "text": "Theft from motor vehicle, bike theft and shootings are separate TPS datasets, so last year's number "
                        "for them is not in the historical MCI layer and percent change is shown as n/a."})
    out.append({"dataset": "Traffic Collisions", "kind": "lag",
                "text": "The most recent month often arrives incomplete (late reports). Profiles mark it as possibly incomplete when it is well below recent months."})
    out.append({"dataset": "Neighbourhood Crime Rates", "kind": "cadence",
                "text": "Yearly counts end with the last full calendar year; the current year is only in the current-year crime layer."})
    out.append({"dataset": "Community Assets", "kind": "quality",
                "text": "Some services listed for a neighbourhood are located far outside it (for example head offices); the map leaves those off."})
    return out


def run(today: dt.date | None = None) -> dict:
    today = today or F.toronto_today()
    items = [check_dataset(d, today) for d in DATASETS]
    counts = {s: sum(1 for i in items if i["state"] == s) for s in ("ok", "warn", "bad", "unknown")}
    return {"checked": today.strftime("%B %-d, %Y"), "items": items, "counts": counts, "findings": findings(today)}
