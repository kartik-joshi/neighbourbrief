"""The public site's in-browser "Check a text" (app/static/factcheck.js) must give the same verdicts
as the app's checker (app/verify/factcheck.py) on the same neighbourhood data.

    .venv/bin/python scripts/js_parity_check.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from visual_check import start_server  # noqa: E402

SAMPLES = [
    "{name} safety update.\nAssaults were down 7% this year, with 95 reports so far.\nBreak-ins rose to 38. There were 14 robberies.",
    "Stolen vehicles are up 22% compared with last year. Call 416-808-5300 or visit 48 Thorncliffe Park Drive on October 13 at 7 p.m.",
    "Thefts over $5,000: 8 so far. Car break-ins fell to 36 in 2026.",
    "Traffic collisions are up 3% this year: 303 so far, 8 involving a pedestrian. Crashes fell 3%. There were 25 collisions.",
    "1,234 reports and 12.50 percent of something. Shootings: 2. Homicides: 0 this year.",
]


def main() -> int:
    from playwright.sync_api import sync_playwright

    from app.data import facts as F, profile as P
    from app.verify import factcheck as FC
    hoods = ["Thorncliffe Park", "Milliken", "Moss Park"]
    proc = start_server(8039)
    bad = 0
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_context(extra_http_headers={"x-nb-public": "1"}).new_page()
            for name in hoods:
                f = F.build_facts(name)
                hid = f["neighbourhood"]["id"]
                road = P.road_safety(hid, dt.date.fromisoformat(f["period"]["month_start"]))
                pg.goto(f"http://127.0.0.1:8039/neighbourhoods/{hid}", wait_until="networkidle")
                ex = FC.example_text(f, road)
                for text in [s.format(name=name) for s in SAMPLES] + [ex]:
                    py = [(i["text"], i["verdict"], i["detail"]) for i in FC.check_text(text, f, road)["items"]]
                    js = [tuple(x) for x in pg.evaluate("t => { const d = JSON.parse(document.getElementById('jsfc-data').textContent);"
                                                          " return NBFC.check(t, d).items.map(i => [i.text, i.verdict, i.detail]); }", text)]
                    if py != js:
                        bad += 1
                        print(f"MISMATCH {name}: {text[:60]!r}")
                        for a, c in zip(py, js):
                            if a != c:
                                print("   py:", a, "\n   js:", c)
                        if len(py) != len(js):
                            print("   lengths", len(py), len(js))
            b.close()
    finally:
        proc.terminate()
    print("JS and Python agree on every sample." if not bad else f"{bad} mismatching sample(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
