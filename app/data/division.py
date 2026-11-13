"""Division summary for Community Police Liaison Committees: a police division's neighbourhoods side
by side, with totals that are sums of the neighbourhood counts (same rules as the bulletin).

Neighbourhoods are listed alphabetically, never ranked. Sums of neighbourhood counts can differ
slightly from TPS's own division totals (division and neighbourhood boundaries don't line up exactly).
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.data import facts as F
from app.data import tps


def divisions() -> list[dict]:
    by = defaultdict(list)
    for h in F.list_neighbourhoods():
        label = F.division_label(h.get("division", ""))
        m = re.search(r"\d+", label)
        if m:
            by[int(m.group(0))].append(h)
    return [{"number": n, "label": f"{n} Division", "hoods": sorted(hs, key=lambda h: h["name"].lower())}
            for n, hs in sorted(by.items())]


def build_division(number: int, month: str | None = None) -> dict:
    div = next((d for d in divisions() if d["number"] == number), None)
    if not div:
        raise ValueError("Unknown division")
    rows, failed, per = [], [], []
    for h in div["hoods"]:
        try:
            f = F.build_facts(h["id"], month=month)
        except (tps.TPSUnavailable, F.MonthNotAvailable) as exc:
            failed.append((h["name"], str(exc)))
            continue
        per.append(f)
        nco = f.get("nco") or {}
        rows.append({"id": h["id"], "name": h["name"], "month": f["totals"]["month"], "ytd": f["totals"]["ytd"],
                     "has_nco": bool(nco.get("has_nco"))})
    warnings = []
    if failed:  # one line, not one per neighbourhood (offline mode can miss most of them)
        warnings.append(f"Not included ({len(failed)} of {len(div['hoods'])} neighbourhoods): "
                        f"{', '.join(n for n, _ in failed)}. Reason: {failed[0][1]}")
    if not per:
        raise tps.TPSUnavailable(warnings[0] if warnings else "No data for this division.")
    cats = []
    for c in per[0]["categories"]:
        lines = [next(x for x in f["categories"] if x["key"] == c["key"]) for f in per]
        prev = None if any(x["ytd_prev"] is None for x in lines) else sum(x["ytd_prev"] for x in lines)
        ytd = sum(x["ytd"] for x in lines)
        pct = F.pct_change(ytd, prev) if prev is not None and not c.get("counts_only") else None
        cats.append({"key": c["key"], "label": c["label"], "month": sum(x["month"] for x in lines), "ytd": ytd,
                     "ytd_prev": prev, "pct_change": pct})
    from app.languages import census_languages
    census = census_languages()
    speakers, population = defaultdict(int), 0
    for r in rows:
        c = census.get(r["id"]) or {}
        population += c.get("population") or 0
        for l in c.get("languages", []):
            speakers[l["language"]] += l["speakers"]
    langs = [{"language": k, "speakers": v, "share": round(100 * v / population, 1) if population else 0}
             for k, v in sorted(speakers.items(), key=lambda kv: -kv[1])[:6]]
    return {"division": div, "period": per[0]["period"], "categories": cats, "rows": rows, "warnings": warnings,
            "languages": langs, "population": population,
            "totals": {"month": sum(r["month"] for r in rows), "ytd": sum(r["ytd"] for r in rows)},
            "nco": next((f["nco"]["division"] for f in per if (f.get("nco") or {}).get("division")), None)}
