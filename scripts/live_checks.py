"""Live checks for the testing rounds (real TPS data, real local model).

  --part data : facts for the 3 demo neighbourhoods + 2 random others; independent re-count
                (non-negative, dedupe, Toronto-time dates, small-number rule).
  --part e2e  : FastAPI TestClient New → Draft → Review → Translate → Publish for the 3 demo
                neighbourhoods with the real model; every language must pass numbers + script.
                Logs drafting and per-language translation times.

    .venv/bin/python scripts/live_checks.py --round 1 --part all
Results: test_artifacts/live_round<N>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEMO_OFFLINE"] = "false"

from app import store  # noqa: E402
from app.data import facts as F  # noqa: E402
from app.data import tps  # noqa: E402
from app.languages import suggested_languages  # noqa: E402

OUT = ROOT / "test_artifacts"
TOR = ZoneInfo("America/Toronto")
DEMO_HOODS = ["Thorncliffe Park", "Milliken", "Westminster-Branson"]


def check_facts(name: str) -> dict:
    f = F.build_facts(name)
    hid = f["neighbourhood"]["id"]
    problems = []
    ms, me = dt.date.fromisoformat(f["period"]["month_start"]), dt.date.fromisoformat(f["period"]["month_end"])
    year = me.year
    raw = tps.fetch_ytd(hid)
    pairs = {(r["EVENT_UNIQUE_ID"], r["CRIME_TYPE"]): r["REPORT_DATE_AGOL"] for r in raw}
    dup_rows = len(raw) - len(pairs)
    # independent re-count (Toronto time, first-seen date per pair is equivalent: dates per event are equal)
    month_tor, month_utc, ytd = Counter(), Counter(), Counter()
    for (eid, cat), msv in pairs.items():
        d_tor = dt.datetime.fromtimestamp(msv / 1000, TOR).date()
        d_utc = dt.datetime.fromtimestamp(msv / 1000, dt.timezone.utc).date()
        if ms <= d_tor <= me:
            month_tor[cat] += 1
        if ms <= d_utc <= me:
            month_utc[cat] += 1
        if dt.date(year, 1, 1) <= d_tor <= me:
            ytd[cat] += 1
    for line in f["categories"]:
        k = line["key"]
        for fld in ("month", "ytd", "ytd_prev"):
            v = line[fld]
            if v is not None and (not isinstance(v, int) or v < 0):
                problems.append(f"{k}.{fld} = {v!r} is not a non-negative integer")
        if line["month"] != month_tor[k]:
            problems.append(f"{k}: month {line['month']} != independent recount {month_tor[k]}")
        if line["ytd"] != ytd[k]:
            problems.append(f"{k}: ytd {line['ytd']} != independent recount {ytd[k]}")
        if line["ytd"] < line["month"]:
            problems.append(f"{k}: ytd < month")
        pct = line["pct_change"]
        if pct is not None:
            if line["ytd"] < 10 or (line["ytd_prev"] or 0) < 10:
                problems.append(f"{k}: percent shown with a count under 10")
            if k in ("Shooting", "Homicide"):
                problems.append(f"{k}: percent shown for counts-only category")
            if not isinstance(pct, int):
                problems.append(f"{k}: percent not a whole number")
        elif line["mci"] and line["ytd"] >= 10 and (line["ytd_prev"] or 0) >= 10:
            problems.append(f"{k}: both counts ≥ 10 but no percent")
    if "Sexual Violation" in {l["key"] for l in f["categories"]}:
        problems.append("Sexual Violation not excluded")
    if not 3 <= len(f["display"]) <= 6:
        problems.append(f"display has {len(f['display'])} categories")
    return {
        "neighbourhood": f["neighbourhood"]["name"], "id": hid, "division": f["neighbourhood"]["division"],
        "raw_rows": len(raw), "duplicate_rows_removed": dup_rows,
        "month_total": f["totals"]["month"], "ytd_total": f["totals"]["ytd"],
        "utc_vs_toronto_month_difference": sum(abs(month_utc[k] - month_tor[k]) for k in set(month_utc) | set(month_tor)),
        "categories": {l["key"]: [l["month"], l["ytd"], l["ytd_prev"], l["pct_change"]] for l in f["categories"]},
        "nco": (f["nco"] or {}).get("has_nco"),
        "freshness": f["freshness"]["tps_updated_label"],
        "problems": problems,
    }


def run_data(seed: int | None) -> dict:
    hoods = [h["name"] for h in F.list_neighbourhoods()]
    rng = random.Random(seed)
    others = rng.sample([h for h in hoods if h not in DEMO_HOODS], 2)
    results = []
    for name in DEMO_HOODS + others:
        t0 = time.monotonic()
        try:
            r = check_facts(name)
        except Exception as exc:  # report, don't crash the round
            r = {"neighbourhood": name, "problems": [f"{type(exc).__name__}: {exc}"]}
        r["seconds"] = round(time.monotonic() - t0, 1)
        results.append(r)
        print(f"  {name}: {'OK' if not r['problems'] else r['problems']}  "
              f"(rows {r.get('raw_rows')}, dupes removed {r.get('duplicate_rows_removed')}, "
              f"month {r.get('month_total')}, ytd {r.get('ytd_total')})")
    return {"random_neighbourhoods": others, "results": results,
            "ok": all(not r["problems"] for r in results)}


SAMPLES = {
    "Thorncliffe Park": ("Car break-ins near the plaza parking lots in the evening.",
                         "More evening patrols and a lock-your-car reminder session with the tenant association.",
                         "Tuesday, October 13 at 7 p.m., Thorncliffe Park Public Library"),
    "Milliken": ("Vehicles stolen from driveways overnight.",
                 "More overnight patrols on residential streets and anti-theft tips shared at 3 community events.",
                 "Thursday, October 15 at 6:30 p.m., at the local library branch"),
    "Westminster-Branson": ("Break-ins to storage lockers in apartment buildings.",
                            "We met building managers about lighting and locker security, and visited 4 buildings with tenants.",
                            "Wednesday, October 21 at 7 p.m., community room at the local library"),
}


def run_e2e(round_no: int) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app

    store.BULLETIN_DIR = OUT / f"e2e_bulletins_round{round_no}"
    client = TestClient(app)
    results = []
    for name in DEMO_HOODS:
        pr, act, meet = SAMPLES[name]
        langs = [l["code"] for l in suggested_languages(name)]
        res = {"neighbourhood": name, "languages": langs, "problems": []}
        t0 = time.monotonic()
        r = client.post("/draft", data={"neighbourhood": name, "priority": pr, "action_taken": act,
                                        "meeting_note": meet, "languages": langs}, follow_redirects=False)
        if r.status_code != 303:
            res["problems"].append(f"draft HTTP {r.status_code}")
            results.append(res)
            continue
        bid = r.headers["location"].rsplit("/", 1)[-1]
        rec = store.load(bid)
        res.update(id=bid, draft_source=rec["draft_source"], draft_seconds=rec["timings"]["draft_seconds"],
                   draft_attempts=rec.get("draft_info", {}).get("attempts"))
        if client.get(f"/review/{bid}").status_code != 200:
            res["problems"].append("review page failed")
        client.post(f"/review/{bid}/translate", data={"languages": langs, "wait": "1"}, follow_redirects=False)
        rec = store.load(bid)
        res["translations"] = {}
        for c in langs:
            t = rec["translations"].get(c) or {}
            v = rec["verification"].get(c, {})
            res["translations"][c] = {
                "seconds": t.get("seconds"), "attempts": t.get("attempts"), "engine": t.get("engine"),
                "numbers_ok": v.get("numbers", {}).get("ok"), "script_ok": v.get("script", {}).get("ok"),
                "summary": v.get("summary"), "error": t.get("error"),
            }
            if not v.get("ok"):
                res["problems"].append(f"{c}: {v.get('summary')}")
        if not rec["verification"]["en"]["ok"]:
            res["problems"].append("en: " + rec["verification"]["en"]["summary"])
        client.post(f"/publish/{bid}", data={}, follow_redirects=False)
        rec = store.load(bid)
        res["published"] = rec["status"] == "published"
        if not res["published"]:
            res["problems"].append("publish blocked")
        for c in ["en"] + langs:
            if client.get(f"/bulletin/{bid}/{c}").status_code != 200:
                res["problems"].append(f"bulletin page {c} failed")
        res["total_seconds"] = round(time.monotonic() - t0, 1)
        print(f"  {name}: draft {res['draft_source']} {res['draft_seconds']} s; " +
              ", ".join(f"{c} {v['seconds']} s {'✅' if v['numbers_ok'] and v['script_ok'] else '⚠️'}"
                        for c, v in res["translations"].items()) +
              f"; published={res['published']}" + (f"; problems: {res['problems']}" if res["problems"] else ""))
        results.append(res)
    return {"results": results, "ok": all(not r["problems"] for r in results)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--part", choices=["data", "e2e", "all"], default="all")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    path = OUT / f"live_round{args.round}.json"
    report = json.loads(path.read_text()) if path.exists() else {}
    report["round"] = args.round
    if args.part in ("data", "all"):
        print("Live data checks…")
        report["data"] = run_data(args.seed)
    if args.part in ("e2e", "all"):
        print("End-to-end with the real model…")
        report["e2e"] = run_e2e(args.round)
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print("Saved", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
