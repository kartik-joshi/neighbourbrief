"""Ward briefs for city councillors: the neighbourhoods in each of Toronto's 25 wards, each with its own
counts and a link to its plain-language bulletin.

Wards and neighbourhoods don't line up (config/neighbourhood_wards.json, built by
scripts/build_ward_map.py, says how much of each neighbourhood is in each ward). Counts are never split
by area or added up into a ward total, because that would invent numbers: a neighbourhood partly in a
ward is listed with its whole count and the share of its area in the ward. Alphabetical, never ranked.
"""
from __future__ import annotations

import json
from functools import lru_cache

from app.config import CONFIG_DIR
from app.data import facts as F
from app.data import tps

MAP_FILE = CONFIG_DIR / "neighbourhood_wards.json"
LISTED_MIN_SHARE = 10   # % of a neighbourhood's area in the ward to list it there
MOSTLY = 50


@lru_cache(maxsize=1)
def _map() -> dict:
    return json.loads(MAP_FILE.read_text(encoding="utf-8"))


def wards() -> list[dict]:
    m = _map()
    out = []
    for num, name in m["wards"].items():
        hoods = [(hid, s) for hid, ws in m["hoods"].items() for w, s in ws if str(w) == num and s >= LISTED_MIN_SHARE]
        out.append({"number": int(num), "name": name, "label": f"Ward {int(num)}: {name}",
                    "mostly": sum(1 for _, s in hoods if s >= MOSTLY), "partly": sum(1 for _, s in hoods if s < MOSTLY)})
    return sorted(out, key=lambda w: w["number"])


def build_ward(number: int, month: str | None = None) -> dict:
    ward = next((w for w in wards() if w["number"] == number), None)
    if not ward:
        raise ValueError("Unknown ward")
    m = _map()
    names = {h["id"]: h["name"] for h in F.list_neighbourhoods()}
    rows, failed, period = [], [], None
    for hid, ws in m["hoods"].items():
        share = next((s for w, s in ws if w == number), 0)
        if share < LISTED_MIN_SHARE:
            continue
        try:
            f = F.build_facts(hid, month=month)
        except F.MonthNotAvailable:
            raise  # the same for every neighbourhood: say so instead of an empty brief
        except (tps.TPSUnavailable, ValueError) as exc:
            failed.append((names.get(hid, hid), str(exc)))
            continue
        period = period or f["period"]
        rows.append({"id": hid, "name": f["neighbourhood"]["name"], "share": share, "mostly": share >= MOSTLY,
                     "month": f["totals"]["month"], "ytd": f["totals"]["ytd"],
                     "other_wards": [(w, m["wards"][str(w)]) for w, s in ws if w != number],
                     "auto_id": f"auto-{hid}-{f['period']['month_start'][:7]}",
                     "division": f["neighbourhood"]["division"]})
    if not rows:
        raise tps.TPSUnavailable(failed[0][1] if failed else "No data for this ward.")
    warnings = []
    if failed:
        warnings.append(f"Not included ({len(failed)} neighbourhoods): {', '.join(n for n, _ in failed)}. Reason: {failed[0][1]}")
    rows.sort(key=lambda r: (not r["mostly"], r["name"].lower()))
    return {"ward": ward, "period": period, "rows": rows, "warnings": warnings,
            "divisions": sorted({r["division"] for r in rows}), "source": m["source"], "built": m["built"]}
