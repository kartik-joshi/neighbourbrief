"""Deterministic facts: code computes every number that may appear in a bulletin."""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from zoneinfo import ZoneInfo

import pandas as pd

from app.data import tps
from app.languages import normalize_name, suggested_languages
from app.verify.numbers import canonical_number, extract_numbers

TORONTO = ZoneInfo("America/Toronto")

# Display order and plain-language labels. Keys are TPS CRIME_TYPE values.
CATEGORIES = [
    ("Assault", "Assaults"),
    ("Break and Enter", "Break-ins"),
    ("Auto Theft", "Stolen vehicles"),
    ("Theft from Motor Vehicle", "Theft from vehicles"),
    ("Robbery", "Robberies"),
    ("Theft Over", "Thefts over $5,000"),
    ("Bike Theft", "Bike thefts"),
    ("Shooting", "Shootings"),
    ("Homicide", "Homicides"),
]
MCI_CATEGORIES = {"Assault", "Auto Theft", "Break and Enter", "Robbery", "Theft Over"}
COUNTS_ONLY = {"Shooting", "Homicide"}  # never show a percent change
EXCLUDED = {"Sexual Violation"}  # sensitive; no comparable history (see docs/DECISIONS.md)
SMALL_NUMBER_MIN = 10
MAX_DISPLAY = 6
MIN_DISPLAY = 3

SOURCE = {
    "name": "Toronto Police Service Public Safety Data Portal",
    "licence": "Open Government Licence – Ontario",
    "layers": [tps.YTD_LAYER, tps.MCI_LAYER, tps.NCO_LAYER, tps.ASSETS_LAYER],
    "attribution": (
        "Prototype built on Toronto Police Service open data (Open Government Licence – Ontario). "
        "Not affiliated with or endorsed by the Toronto Police Service."
    ),
}


# ---------------------------------------------------------------- time helpers

def to_toronto(epoch_ms) -> dt.datetime | None:
    """Convert ArcGIS epoch milliseconds (UTC) to an aware America/Toronto datetime."""
    if epoch_ms is None or (isinstance(epoch_ms, float) and pd.isna(epoch_ms)):
        return None
    return dt.datetime.fromtimestamp(int(epoch_ms) / 1000, tz=dt.timezone.utc).astimezone(TORONTO)


def last_full_month(today: dt.date) -> tuple[dt.date, dt.date]:
    first_this = today.replace(day=1)
    end = first_this - dt.timedelta(days=1)
    return end.replace(day=1), end


def toronto_today() -> dt.date:
    return dt.datetime.now(TORONTO).date()


def effective_today(hood_id: str | None = None) -> dt.date:
    """Today in Toronto; in offline demo mode, the day that neighbourhood's data was saved (so the
    "last full month" always matches the saved data)."""
    from app.config import get_settings

    if get_settings().demo_offline:
        from app import snapshot
        d = snapshot.date_for(hood_id) if hood_id else None
        if d is None:
            meta = snapshot.load_meta()
            d = dt.date.fromisoformat(meta["snapshot_date"]) if meta.get("snapshot_date") else None
        if d is not None:
            return d
    return toronto_today()


# ---------------------------------------------------------------- neighbourhoods

def _hood_id(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(3) if digits else ""


def clean_hood_name(name: str) -> str:
    return re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", name or "").strip()


def list_neighbourhoods() -> list[dict]:
    """All 158 neighbourhoods with division, from the NCO layer (fallback: distinct YTD values)."""
    hoods: dict[str, dict] = {}
    try:
        for r in tps.fetch_nco_all():
            hid = _hood_id(r.get("Neighbourh"))
            if not hid or hid in hoods:
                continue
            name = r.get("Neighbour2") or clean_hood_name(r.get("Neighbour3", ""))
            hoods[hid] = {"id": hid, "name": name.strip(), "division": (r.get("DIV") or "").strip()}
    except tps.TPSUnavailable:
        pass
    if not hoods:
        for r in tps.fetch_neighbourhood_list_from_ytd():
            hid = _hood_id(r.get("HOOD_158"))
            if not hid or hid in hoods or not r.get("NEIGHBOURHOOD_158"):
                continue
            if r["NEIGHBOURHOOD_158"].upper().startswith("NSA"):
                continue
            hoods[hid] = {"id": hid, "name": clean_hood_name(r["NEIGHBOURHOOD_158"]),
                          "division": r.get("DIVISION") or ""}
    return sorted(hoods.values(), key=lambda h: h["name"].lower())


def find_neighbourhood(query: str) -> dict | None:
    q = (query or "").strip()
    hoods = list_neighbourhoods()
    if re.fullmatch(r"\d{1,3}", q):
        hid = q.zfill(3)
        return next((h for h in hoods if h["id"] == hid), None)
    nq = normalize_name(q)
    return next((h for h in hoods if normalize_name(h["name"]) == nq), None)


def division_label(code: str) -> str:
    m = re.search(r"\d+", code or "")
    return f"{int(m.group(0))} Division" if m else (code or "")


def nco_info(hood_id: str) -> dict | None:
    """Officers (NCO=Yes) or the division contact, as TPS publishes them in "Find My NCO"."""
    try:
        rows = [r for r in tps.fetch_nco_all() if _hood_id(r.get("Neighbourh")) == hood_id]
    except tps.TPSUnavailable:
        return None
    if not rows:
        return None
    first = rows[0]
    officers = []
    for r in rows:
        if str(r.get("NCO", "")).strip().lower() != "yes":
            continue
        last = (r.get("LastName") or "").strip()
        last = last.title() if last.isupper() else last
        name = " ".join(x for x in [(r.get("Rank") or "").strip(),
                                    (r.get("FirstName") or "").strip(), last] if x)
        officers.append({
            "name": name,
            "badge": (r.get("Badge") or "").strip(),
            "phone": (r.get("WorkPhoneN") or "").strip(),
            "email": (r.get("EmailAddre") or "").strip(),
        })
    return {
        "has_nco": bool(officers),
        "officers": officers,
        "division": {
            "name": (first.get("UNIT_NAME") or division_label(first.get("DIV", ""))).strip(),
            "phone": (first.get("PHONE") or "").strip(),
            "address": ", ".join(x for x in [(first.get("ADDRESS") or "").strip(),
                                             (first.get("CITY") or "").strip()] if x),
            "email": (first.get("EmailAddre") or "").strip() if not officers else "",
        },
    }


# ---------------------------------------------------------------- counting

def dedupe_frame(rows: list[dict], id_field: str, cat_field: str, date_field: str) -> pd.DataFrame:
    """Rows are offence/victim level: keep one row per (event id, category)."""
    df = pd.DataFrame(rows, columns=[id_field, cat_field, date_field]) if rows else pd.DataFrame(
        columns=[id_field, cat_field, date_field])
    if df.empty:
        return pd.DataFrame(columns=["event", "category", "report_date"])
    df = df.rename(columns={id_field: "event", cat_field: "category", date_field: "report_ms"})
    df = df.dropna(subset=["event", "category", "report_ms"])
    df["category"] = df["category"].astype(str).str.strip()
    df = df.sort_values("report_ms").drop_duplicates(subset=["event", "category"], keep="first")
    df["report_date"] = df["report_ms"].map(lambda ms: to_toronto(ms).date())
    return df[["event", "category", "report_date"]]


def count_between(df: pd.DataFrame, start: dt.date, end: dt.date) -> Counter:
    if df.empty:
        return Counter()
    mask = (df["report_date"] >= start) & (df["report_date"] <= end)
    return Counter(df.loc[mask, "category"].tolist())


def pct_change(cur: int, prev: int) -> int | None:
    """Percent change, only when both counts are >= 10 (small-number rule). Whole numbers."""
    if cur < SMALL_NUMBER_MIN or prev < SMALL_NUMBER_MIN:
        return None
    return int(round((cur - prev) / prev * 100))


def same_day_last_year(d: dt.date) -> dt.date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - 1, day=28)


def category_lines(ytd_df: pd.DataFrame, mci_prev_df: pd.DataFrame, month_start: dt.date,
                   month_end: dt.date, prev_keys: set[str] | None = None) -> list[dict]:
    """prev_keys: categories with last year's data (default: the Major Crime Indicators five)."""
    prev_keys = MCI_CATEGORIES if prev_keys is None else prev_keys
    year_start = month_start.replace(month=1, day=1)
    month_counts = count_between(ytd_df, month_start, month_end)
    ytd_counts = count_between(ytd_df, year_start, month_end)
    prev_counts = count_between(mci_prev_df, same_day_last_year(year_start),
                                same_day_last_year(month_end))
    lines = []
    for key, label in CATEGORIES:
        if key in EXCLUDED:
            continue
        month = int(month_counts.get(key, 0))
        ytd = int(ytd_counts.get(key, 0))
        is_mci = key in MCI_CATEGORIES
        has_prev = key in prev_keys
        prev = int(prev_counts.get(key, 0)) if has_prev else None
        pct = pct_change(ytd, prev) if (has_prev and key not in COUNTS_ONLY) else None
        direction = None
        if prev is not None:
            direction = "up" if ytd > prev else "down" if ytd < prev else "same"
        lines.append({
            "key": key,
            "label": label,
            "month": month,
            "ytd": ytd,
            "ytd_prev": prev,
            "pct_change": pct,
            "direction": direction,
            "counts_only": key in COUNTS_ONLY,
            "mci": is_mci,
        })
    return lines


def pick_display(lines: list[dict]) -> list[str]:
    """3–6 category keys for the bulletin: always shootings/homicides if any, then most reports."""
    must = [l for l in lines if l["counts_only"] and (l["month"] or l["ytd"])]
    rest = [l for l in lines if l not in must and (l["month"] or l["ytd"])]
    rest.sort(key=lambda l: (-l["month"], -l["ytd"]))
    chosen = must + rest[: max(0, MAX_DISPLAY - len(must))]
    if len(chosen) < MIN_DISPLAY:
        for l in lines:  # quiet month: fill with main categories showing 0
            if l not in chosen and l["mci"]:
                chosen.append(l)
            if len(chosen) >= MIN_DISPLAY:
                break
    order = [k for k, _ in CATEGORIES]
    return sorted((l["key"] for l in chosen), key=order.index)


# ---------------------------------------------------------------- local services

# Most useful first for a safety bulletin; "General/Other" (mostly child care centres) is skipped.
SERVICE_PRIORITY = ["Legal Services (Abuse,Safety)", "Emergency-Crisis", "Newcomers-Ethnocultural",
                    "Food", "Seniors", "Health", "Homelessness & Housing", "Employment-Education",
                    "Child Care,Family & Youth", "Indigenous peoples", "Disability", "Government"]
SERVICES_SHOWN = 4
SERVICES_CANDIDATES = 10


def _service_name(public: str) -> str:
    parts = [p.strip() for p in (public or "").split(" - ") if p.strip()]
    if not parts:
        return ""
    name = parts[0] if len(parts) == 1 or parts[-1] == parts[0] else f"{parts[0]}: {parts[-1]}"
    return name if len(name) <= 70 else name[:67].rstrip() + "…"


def _service_phone(r: dict) -> str:
    for k in ("Phone1Number", "PhoneNumberBusinessLine", "PhoneNumberHotline"):
        v = (r.get(k) or "").split(";")[0].strip()
        if re.search(r"\d{3}\D{0,2}\d{3}\D?\d{4}", v):
            return re.split(r"\s*(?:ext|x)\s*\d", v, maxsplit=1, flags=re.I)[0].strip()
    return ""


def _service_point(r: dict) -> dict:
    """The service's own map position, when TPS lists one inside the Toronto area."""
    try:
        lat, lon = float(r.get("Latitude")), float(r.get("Longitude"))
    except (TypeError, ValueError):
        return {}
    return {"lat": round(lat, 5), "lon": round(lon, 5)} if 43.5 < lat < 43.9 and -79.7 < lon < -79.1 else {}


def local_services(hood_id: str) -> list[dict]:
    """Up to 10 candidate services, one per category in priority order, then more."""
    try:
        rows = tps.fetch_assets(hood_id)
    except tps.TPSUnavailable:
        return []
    seen_orgs, by_cat = set(), {}
    for r in rows:
        if (r.get("AgencyStatus") or "Active") != "Active":
            continue
        cat = (r.get("Category") or "").strip()
        if cat not in SERVICE_PRIORITY:
            continue
        org = (r.get("PublicName") or "").split(" - ")[0].strip().lower()
        if not org or org in seen_orgs:
            continue
        seen_orgs.add(org)
        by_cat.setdefault(cat, []).append({
            "id": str(r.get("ResourceAgencyNum") or r.get("OBJECTID")),
            "name": _service_name(r.get("PublicName", "")),
            "category": cat,
            "address": (r.get("PhysicalAddress1") or "").strip(),
            "phone": _service_phone(r),
            **_service_point(r),
        })
    out, rnd = [], 0
    while len(out) < SERVICES_CANDIDATES and any(len(v) > rnd for v in by_cat.values()):
        for cat in SERVICE_PRIORITY:  # round-robin: one per category first
            if len(by_cat.get(cat, [])) > rnd and len(out) < SERVICES_CANDIDATES:
                out.append(by_cat[cat][rnd])
        rnd += 1
    return out


# ---------------------------------------------------------------- allowed numbers

def base_allowed_numbers(facts: dict) -> set[str]:
    nums: set[str] = set()
    p = facts["period"]
    for k in ("year", "prev_year", "month_last_day", "month_number", "days_in_month"):
        nums.add(str(p[k]))
    nums.add("1")  # "August 1", "January 1"
    for line in facts["categories"]:
        for k in ("month", "ytd", "ytd_prev", "pct_change"):
            if line.get(k) is not None:
                nums.add(str(abs(line[k])))
        nums.update(extract_numbers(line["label"]))  # e.g. 5,000 in "Thefts over $5,000"
    t = facts["totals"]
    nums.update(str(v) for v in t.values())
    nums.update(extract_numbers(facts["neighbourhood"]["division"]))
    nums.add("911")
    return nums


def allowed_numbers(facts: dict, officer: dict | None = None) -> list[str]:
    nums = base_allowed_numbers(facts)
    for v in (officer or {}).values():
        if isinstance(v, str):
            nums.update(extract_numbers(v))
    return sorted({canonical_number(n) for n in nums}, key=lambda s: (len(s), s))


# ---------------------------------------------------------------- build

class MonthNotAvailable(ValueError):
    """The requested month can't be built from the open data (shown to the officer as-is)."""


def available_months(today: dt.date | None = None) -> list[dt.date]:
    """Complete months that can be drafted, newest first: January → the last full month of the
    last full month's year (the current-year TPS feed only covers one calendar year)."""
    today = today or effective_today()
    last, _ = last_full_month(today)
    return [dt.date(last.year, m, 1) for m in range(last.month, 0, -1)]


def parse_month(value: str | None, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """'2026-07' → (2026-07-01, 2026-07-31). Empty → the last full month."""
    today = today or effective_today()
    if not value:
        return last_full_month(today)
    try:
        start = dt.datetime.strptime(value.strip()[:7], "%Y-%m").date()
    except ValueError as exc:
        raise MonthNotAvailable(f"“{value}” isn't a month. Pick one from the list.") from exc
    if start not in available_months(today):
        raise MonthNotAvailable(
            f"{start.strftime('%B %Y')} can't be drafted: pick a complete month from "
            f"{available_months(today)[-1].strftime('%B')} to "
            f"{available_months(today)[0].strftime('%B %Y')}.")
    nxt = (start.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return start, nxt - dt.timedelta(days=1)


def build_facts(hood_query: str, today: dt.date | None = None, month: str | None = None) -> dict:
    hood = find_neighbourhood(hood_query)
    if hood is None:
        raise ValueError(f"Unknown neighbourhood: {hood_query!r}")
    hid = hood["id"]
    today = today or effective_today(hid)
    month_start, month_end = parse_month(month, today)
    year = month_end.year
    prev_year = year - 1

    ytd_rows = tps.fetch_ytd(hid)
    ytd_df = dedupe_frame(ytd_rows, "EVENT_UNIQUE_ID", "CRIME_TYPE", "REPORT_DATE_AGOL")
    if not ytd_df.empty:
        data_years = set(ytd_df["report_date"].map(lambda d: d.year))
        if year not in data_years and max(data_years) > year:
            # January: the current-year feed restarted, so last December isn't in it any more.
            raise MonthNotAvailable(
                f"The TPS current-year feed has moved on to {max(data_years)}, so "
                f"{month_start.strftime('%B %Y')} is no longer in it (the historical dataset is "
                "published quarterly). Try again once TPS publishes it, or pick a "
                f"{max(data_years)} month.")
    ytd_df = ytd_df[ytd_df["report_date"].map(lambda d: d.year == year)] if not ytd_df.empty else ytd_df

    mf = tps.mci_fields()
    mci_rows = tps.fetch_mci_year(hid, prev_year)
    mci_df = dedupe_frame(mci_rows, "EVENT_UNIQUE_ID", mf["category"], mf["report_date"])
    prev_keys = set(MCI_CATEGORIES)
    for key in tps.PREV_YEAR_LAYERS:  # optional: without them, these two just have no comparison
        try:
            rows = tps.fetch_category_year(key, hid, prev_year)
        except tps.TPSUnavailable:
            continue
        mci_df = pd.concat([mci_df, dedupe_frame(rows, "EVENT_UNIQUE_ID", "CATEGORY", "REPORT_DATE")], ignore_index=True)
        prev_keys.add(key)

    lines = category_lines(ytd_df, mci_df, month_start, month_end, prev_keys)
    display = pick_display(lines)

    division_code = hood.get("division") or ""
    if not division_code and ytd_rows:
        division_code = Counter(r.get("DIVISION") for r in ytd_rows).most_common(1)[0][0] or ""

    fresh_ms = tps.data_freshness_ms()
    latest_report = max((d for d in ytd_df["report_date"]), default=None) if not ytd_df.empty else None
    fresh = to_toronto(fresh_ms) if fresh_ms else None
    langs = suggested_languages(hood["name"], hid)

    facts = {
        "neighbourhood": {"id": hid, "name": hood["name"],
                          "division_code": division_code,
                          "division": division_label(division_code)},
        "period": {
            "month_label": month_start.strftime("%B %Y"),
            "month_start": month_start.isoformat(),
            "month_end": month_end.isoformat(),
            "month_number": month_start.month,
            "month_last_day": month_end.day,
            "days_in_month": month_end.day,
            "year": year,
            "prev_year": prev_year,
            "ytd_label": f"January 1 – {month_end.strftime('%B')} {month_end.day}, {year}",
            "ytd_prev_label": f"January 1 – {month_end.strftime('%B')} {month_end.day}, {prev_year}",
            "basis": "report date (when the report was made to police), Toronto time",
        },
        "categories": lines,
        "display": display,
        "totals": {
            "month": sum(l["month"] for l in lines),
            "ytd": sum(l["ytd"] for l in lines),
        },
        "excluded": sorted(EXCLUDED),
        "nco": nco_info(hid),
        "services": local_services(hid),
        "languages": langs,
        "freshness": {
            "tps_updated": fresh.isoformat() if fresh else None,
            "tps_updated_label": fresh.strftime("%B %-d, %Y, %-I:%M %p").replace("AM", "a.m.").replace("PM", "p.m.") if fresh else "unknown",
            "latest_report_date": latest_report.isoformat() if latest_report else None,
            "fetched_at": dt.datetime.now(TORONTO).isoformat(timespec="seconds"),
        },
        "source": SOURCE,
    }
    facts["data_warnings"] = data_warnings(month_end, latest_report, fresh.date() if fresh else None, today)
    facts["allowed_numbers"] = allowed_numbers(facts)
    return facts


def data_warnings(month_end: dt.date, latest_report: dt.date | None, fresh: dt.date | None,
                  today: dt.date) -> list[str]:
    """Signs the TPS data might not cover the whole bulletin month (shown on Review)."""
    out = []
    month = month_end.strftime("%B")
    if fresh is not None and (today - fresh).days > 3:
        out.append(f"TPS data was last updated {fresh.strftime('%B %-d')} "
                   f"({(today - fresh).days} days ago). Numbers may be behind.")
    if fresh is not None and fresh <= month_end:
        out.append(f"TPS data was last updated before the end of {month}, so {month} may be "
                   "incomplete. Consider waiting a few days.")
    elif latest_report is not None and (month_end - latest_report).days > 3:
        out.append(f"The latest report in the data for this neighbourhood is from "
                   f"{latest_report.strftime('%B %-d')}. If that seems early, the data for "
                   f"{month} may be incomplete.")
    return out


if __name__ == "__main__":  # python -m app.data.facts "Thorncliffe Park"
    import json
    import sys

    print(json.dumps(build_facts(" ".join(sys.argv[1:]) or "Thorncliffe Park"), indent=2,
                     ensure_ascii=False))
