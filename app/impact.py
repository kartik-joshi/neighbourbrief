"""Impact, measured from this installation's own records (no estimates): what was published, in
which languages, how many numbers were checked, how long it took and who it can reach."""
from __future__ import annotations

import datetime as dt
from statistics import median

from app import store
from app.languages import census_languages, lang_info
from app.verify import checks


def compute() -> dict:
    recs = store.list_all()
    published = [r for r in recs if r.get("status") == "published"]
    census = census_languages()
    langs, numbers_checked, minutes, draft_s, tr_s, reach = set(), 0, [], [], [], []
    for r in published:
        v = checks.verify_record(r)
        for code, res in v.items():
            numbers_checked += len((res.get("numbers") or {}).get("numbers") or [])
            if code != "en":
                langs.add(code)
        try:
            minutes.append((dt.datetime.fromisoformat(r["published_at"]) - dt.datetime.fromisoformat(r["created_at"])).total_seconds() / 60)
        except (KeyError, TypeError, ValueError):
            pass
        t = r.get("timings") or {}
        if t.get("draft_seconds"):
            draft_s.append(t["draft_seconds"])
        tr_s += [s for s in (t.get("translate_seconds") or {}).values() if s]
        hood = census.get(r["facts"]["neighbourhood"]["id"]) or {}
        codes = set(r.get("languages") or [])
        speakers = sum(l["speakers"] for l in hood.get("languages", []) if l.get("code") in codes)
        reach.append({"name": r["facts"]["neighbourhood"]["name"], "month": r["facts"]["period"]["month_label"],
                      "population": hood.get("population"), "speakers": speakers,
                      "languages": [lang_info(c)["name"] for c in r.get("languages") or []]})
    return {
        "bulletins": len(recs), "published": len(published),
        "all_demo": bool(published) and all(r.get("demo") for r in published),
        "languages": sorted(lang_info(c)["name"] for c in langs),
        "pages": sum(1 + len(r.get("languages") or []) for r in published),
        "numbers_checked": numbers_checked,
        "median_minutes": round(median(minutes)) if minutes else None,
        "median_draft_seconds": round(median(draft_s)) if draft_s else None,
        "median_translate_seconds": round(median(tr_s)) if tr_s else None,
        "reach": reach,
        "speakers": sum(x["speakers"] for x in reach),
        "neighbourhoods_with_suggestions": sum(1 for h in census.values() if any(l["code"] and l["share"] >= 3 for l in h["languages"])),
        "neighbourhoods": len(census) or 158,
    }
