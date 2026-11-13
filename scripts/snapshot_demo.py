"""Save TPS data for the 3 demo neighbourhoods into data/demo_snapshot/ and pre-generate one
complete bulletin for each (English + its languages) into data/bulletins/.

    .venv/bin/python scripts/snapshot_demo.py            # snapshot + bulletins (≈10–15 min on gemma3:12b)
    .venv/bin/python scripts/snapshot_demo.py --data-only
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEMO_OFFLINE"] = "false"  # snapshotting needs the network

from app import store  # noqa: E402
from app.config import SNAPSHOT_DIR  # noqa: E402
from app.languages import suggested_languages  # noqa: E402

def _meeting_day(weekday: int, weeks_ahead: int = 2) -> str:
    """A date that is always in the future at demo time: that weekday, about two weeks from today."""
    import datetime as dt
    d = dt.date.today() + dt.timedelta(weeks=weeks_ahead)
    return (d + dt.timedelta(days=(weekday - d.weekday()) % 7)).isoformat()


# Each demo carries last month's priority forward ("You said, we did") and a meeting about two weeks out.
DEMO = [
    {
        "neighbourhood": "Thorncliffe Park",
        "priority": "Package thefts from apartment lobbies in the afternoon.",
        "action_taken": "Building visits with property managers about lobby doors and parcel rooms.",
        "prev_priority": "Car break-ins near the plaza parking lots in the evening.",
        "followup": "Evening patrols continued and the tenant association shared lock-your-car tips.",
        "followup_status": "ongoing",
        "meeting_date": _meeting_day(1), "meeting_time": "19:00", "meeting_place": "Thorncliffe Park Public Library",
        "tip_id": "packages",
    },
    {
        "neighbourhood": "Milliken",
        "priority": "Vehicles stolen from driveways overnight.",
        "action_taken": "More overnight patrols on residential streets and anti-theft tips shared at 3 community events.",
        "prev_priority": "Vehicles stolen from driveways overnight.",
        "followup": "Overnight patrols started on the streets residents named at the last meeting.",
        "followup_status": "ongoing",
        "meeting_date": _meeting_day(3), "meeting_time": "18:30", "meeting_place": "Milliken branch, Toronto Public Library",
        "tip_id": "car-theft",
    },
    {
        "neighbourhood": "Westminster-Branson",
        "priority": "Bike thefts from apartment bike rooms.",
        "action_taken": "We met building managers about bike room locks and cameras.",
        "prev_priority": "Break-ins to storage lockers in apartment buildings.",
        "followup": "Managers of 4 buildings improved locker lighting and locks.",
        "followup_status": "done",
        "meeting_date": _meeting_day(2), "meeting_time": "19:00", "meeting_place": "Community room, Centennial branch library",
        "tip_id": "bike",
    },
]


def snapshot_data() -> list[str]:
    from app.snapshot import save_for_offline
    saved = save_for_offline([d["neighbourhood"] for d in DEMO])
    return [h["name"] for h in saved]


def pregenerate() -> list[str]:
    from fastapi.testclient import TestClient

    from app.main import app

    # Replace previous demo bulletins.
    for rec in store.list_all():
        if rec.get("demo"):
            (store.BULLETIN_DIR / f"{rec['id']}.json").unlink(missing_ok=True)
    client = TestClient(app)
    ids = []
    for d in DEMO:
        langs = [l["code"] for l in suggested_languages(d["neighbourhood"])]
        t0 = time.monotonic()
        r = client.post("/draft", data={**d, "languages": langs}, follow_redirects=False)
        if r.status_code != 303:
            print(f"  ! draft failed for {d['neighbourhood']}: {r.status_code}")
            continue
        bid = r.headers["location"].rsplit("/", 1)[-1]
        rec = store.load(bid)
        print(f"  {d['neighbourhood']}: {rec['draft_source']} draft in {rec['timings']['draft_seconds']} s")
        client.post(f"/review/{bid}/translate", data={"languages": langs, "wait": "1"}, follow_redirects=False)
        rec = store.load(bid)
        for c in langs:
            t = rec["translations"].get(c) or {}
            v = rec["verification"].get(c, {})
            print(f"    {c}: {t.get('seconds')} s, {'✅' if v.get('ok') else '⚠️ ' + v.get('summary', '')}")
        client.post(f"/publish/{bid}", data={}, follow_redirects=False)
        rec = store.update(bid, lambda r: r.update(demo=True))
        # stable name: demo-<neighbourhood>-<month>, so the Docker image picks demo bulletins by pattern
        stable = "demo-" + bid.rsplit("-", 1)[0]
        rec["id"] = stable
        store.save(rec)
        (store.BULLETIN_DIR / f"{bid}.json").unlink(missing_ok=True)
        bid = stable
        print(f"    status: {rec['status']} ({round(time.monotonic() - t0)} s total) → /publish/{bid}")
        ids.append(bid)
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-only", action="store_true")
    args = ap.parse_args()
    print("Saving TPS data snapshot…")
    files = snapshot_data()
    print(f"  saved {', '.join(files)} to {SNAPSHOT_DIR.relative_to(ROOT)}")
    if not args.data_only:
        print("Pre-generating demo bulletins (AI draft + translations)…")
        ids = pregenerate()
        print("Done:", ", ".join(ids))


if __name__ == "__main__":
    main()
