"""Visual check with Playwright + Chromium.

Starts the app on a spare port, loads every page at 1280 px and 390 px, saves screenshots to
test_artifacts/, saves a PDF of every bulletin language with page.pdf(), and reports horizontal
overflow, RTL direction and PDF page counts in test_artifacts/visual_report.json.

    .venv/bin/python scripts/visual_check.py [--base http://127.0.0.1:8010]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "test_artifacts"
WIDTHS = {"desktop": (1280, 900), "mobile": (390, 844)}
PAGE_PX = 945  # Letter height minus 2 × 14 mm margins, in CSS px


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [str(ROOT / ".venv/bin/uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT, env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=2).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    proc.kill()
    raise SystemExit("server didn't start")


def pdf_pages(data: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?![s\w])", data))


def open_page(pg, base, path):
    """Load a page; "/check#filled" submits a sample text so the results view is checked too."""
    if path == "/check#filled":
        pg.goto(base + "/check", wait_until="networkidle", timeout=60000)
        pg.fill("#ck-hood", "Thorncliffe Park")
        pg.fill("#ck-text", "Assaults were down 7% this year, with 95 reports so far. Break-ins rose to 38. "
                            "There were 14 robberies. Call 416-808-5300 or visit 48 Thorncliffe Park Drive on October 13.")
        pg.click("form[action='/check'] button[type=submit]")
        pg.wait_for_load_state("networkidle")
    elif path == "/ask#filled":  # an answer (sources-only engine: no model or network needed)
        pg.goto(base + "/ask", wait_until="networkidle", timeout=60000)
        pg.fill("#ask-q", "How many assaults were reported in Thorncliffe Park last month?")
        if pg.query_selector("#ask-engine"):  # not shown to public visitors
            pg.select_option("#ask-engine", "extractive")
        pg.click("form[action='/ask'] button[type=submit]")
        pg.wait_for_load_state("networkidle")
    else:
        pg.goto(base + path, wait_until="networkidle", timeout=60000)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--port", type=int, default=8012)
    args = ap.parse_args()
    from playwright.sync_api import sync_playwright

    from app import store

    OUT.mkdir(exist_ok=True)
    proc = None
    base = args.base
    if not base:
        proc = start_server(args.port)
        base = f"http://127.0.0.1:{args.port}"
    recs = store.list_all()
    demo = [r for r in recs if r.get("demo")] or recs[:3]
    pages = [("home", "/"), ("new", "/new"), ("new_map", "/new?hood=Thorncliffe%20Park"), ("how", "/how-we-count"), ("check", "/check"), ("hoods", "/neighbourhoods"), ("data_health", "/data-health"), ("public_home", "/public"), ("impact", "/impact"), ("accessibility", "/accessibility"), ("privacy_ai", "/privacy-ai"), ("translations", "/translations"), ("memory", "/translations/memory"), ("memory_fr", "/translations/memory/fr"), ("help", "/help"), ("ask", "/ask"), ("ask_answer", "/ask#filled"), ("how_it_works", "/how-it-works"), ("embed", "/embed/055"), ("profile", "/neighbourhoods/055"), ("divisions", "/divisions"), ("division", "/divisions/53"), ("wards", "/wards"), ("ward", "/wards/15"), ("check_result", "/check#filled"), ("history", "/history"), ("status", "/status"), ("error404", "/review/no-such-bulletin")]
    for r in demo:
        slug = r["id"].rsplit("-", 1)[0]
        pages.append((f"review_{slug}", f"/review/{r['id']}"))
        for c in ["en"] + r.get("languages", []):
            pages.append((f"publish_{slug}_{c}", f"/publish/{r['id']}?lang={c}"))
    # automatic bulletins (Latin-script and right-to-left/Indic languages); skipped if the data is offline
    from app import auto
    for hid in ("080", "002"):
        try:
            rec = auto.build(hid)
        except Exception:
            continue
        pages.append((f"auto_profile_{hid}", f"/neighbourhoods/{hid}"))
        pages += [(f"auto_{hid}_{c}", f"/bulletin/{rec['id']}/{c}") for c in ["en"] + rec["languages"]]
        demo.append(rec)
    report = {"base": base, "pages": [], "pdfs": [], "problems": []}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for wname, (w, h) in WIDTHS.items():
                ctx = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
                page = ctx.new_page()
                for name, path in pages:
                    open_page(page, base, path)
                    page.wait_for_timeout(300)
                    info = page.evaluate("""() => {
                        const b = document.querySelector('[data-panel]:not(.hidden) .bulletin') || document.querySelector('.bulletin');
                        return {
                          scrollWidth: document.documentElement.scrollWidth,
                          innerWidth: window.innerWidth,
                          dir: b ? b.getAttribute('dir') : null,
                          lang: b ? b.getAttribute('lang') : null,
                          fontFamily: b ? getComputedStyle(b).fontFamily : null,
                          fontsOk: document.fonts ? document.fonts.status : 'n/a',
                        };
                    }""")
                    shot = OUT / f"{wname}_{name}.png"
                    page.screenshot(path=str(shot), full_page=True)
                    entry = {"page": path, "width": w, "screenshot": shot.name, **info}
                    if info["scrollWidth"] > info["innerWidth"] + 1:
                        report["problems"].append(f"horizontal overflow on {path} at {w}px "
                                                  f"({info['scrollWidth']} > {info['innerWidth']})")
                    if info["lang"] in ("ur", "ar", "ps", "fa") and info["dir"] != "rtl":
                        report["problems"].append(f"{path}: {info['lang']} bulletin is not RTL")
                    report["pages"].append(entry)
                ctx.close()
            # PDFs: standalone bulletin pages, print media, Letter.
            ctx = browser.new_context()
            page = ctx.new_page()
            for r in demo:
                slug = r["id"].rsplit("-", 1)[0]
                for c in ["en"] + r.get("languages", []):
                    page.goto(f"{base}/bulletin/{r['id']}/{c}", wait_until="networkidle", timeout=60000)
                    page.emulate_media(media="print")
                    page.wait_for_timeout(200)
                    zoom = page.evaluate("document.querySelector('.bulletin').style.zoom")  # print safety net
                    out = OUT / f"bulletin_{slug}_{c}.pdf"
                    data = page.pdf(path=str(out), format="Letter", print_background=True,
                                    prefer_css_page_size=True)
                    n = pdf_pages(data)
                    report["pdfs"].append({"file": out.name, "pages": n})
                    if n != 1:
                        report["problems"].append(f"{out.name} has {n} pages (expected 1)")
                    # Headroom: flag pages within 10 px of the Letter limit before they spill over.
                    page.set_viewport_size({"width": 710, "height": 945})
                    h = page.evaluate("document.querySelector('.bulletin').getBoundingClientRect().height")
                    page.set_viewport_size({"width": 1280, "height": 720})
                    report["pdfs"][-1]["print_height_px"] = round(h)
                    if zoom:  # the print safety net scaled it to fit: fine if the text stays readable
                        report["pdfs"][-1]["scaled"] = round(float(zoom), 3)
                        if float(zoom) < 0.9:
                            report["problems"].append(f"{out.name} had to be scaled to {float(zoom):.0%} to fit one page (minimum 90%)")
                    elif h > PAGE_PX - 10:
                        report["problems"].append(f"{out.name} is {round(h)} px tall — within 10 px of one page ({PAGE_PX})")
                    page.emulate_media(media="screen")
                    page.screenshot(path=str(OUT / f"sheet_{slug}_{c}.png"), full_page=True)
                for c in ["en"] + r.get("languages", [])[:1]:  # large print: allowed 2 pages, not more
                    page.goto(f"{base}/bulletin/{r['id']}/{c}?large=1", wait_until="networkidle", timeout=60000)
                    page.emulate_media(media="print")
                    out = OUT / f"bulletin_{slug}_{c}_large.pdf"
                    n = pdf_pages(page.pdf(path=str(out), format="Letter", print_background=True,
                                           prefer_css_page_size=True))
                    report["pdfs"].append({"file": out.name, "pages": n})
                    if n > 2:
                        report["problems"].append(f"{out.name} has {n} pages (large print should be ≤ 2)")
                    # A blank first page once slipped under "≤ 2 pages": the title must be on page 1.
                    first = page.evaluate("document.querySelector('.b-title').getBoundingClientRect().top")
                    if first > 200:
                        report["problems"].append(f"{out.name}: title isn't at the top of page 1 (blank first page?)")
                    page.emulate_media(media="screen")
                page.goto(f"{base}/bulletin/{r['id']}/all", wait_until="networkidle", timeout=60000)
                page.emulate_media(media="print")
                out = OUT / f"bulletin_{slug}_all.pdf"
                n = pdf_pages(page.pdf(path=str(out), format="Letter", print_background=True,
                                       prefer_css_page_size=True))
                want = 1 + sum(1 for c in r.get("languages", []) if (r["translations"].get(c) or {}).get("doc"))
                report["pdfs"].append({"file": out.name, "pages": n})
                if n != want:
                    report["problems"].append(f"{out.name} has {n} pages (expected {want})")
                page.emulate_media(media="screen")
            ctx.close()
            browser.close()
    finally:
        if proc:
            proc.terminate()
    (OUT / "visual_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"{len(report['pages'])} page screenshots, {len(report['pdfs'])} PDFs → {OUT.relative_to(ROOT)}")
    for pdf in report["pdfs"]:
        extra = f", {pdf['print_height_px']}/{PAGE_PX} px" if "print_height_px" in pdf else ""
        extra += f", scaled to {pdf['scaled']:.0%}" if "scaled" in pdf else ""
        print(f"  {pdf['file']}: {pdf['pages']} page(s){extra}")
    print("Problems:" if report["problems"] else "No automatic problems found.")
    for prob in report["problems"]:
        print("  -", prob)
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
