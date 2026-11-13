"""Record a captioned backup video of the demo flow (real app, real data, real model/translator).

    .venv/bin/python scripts/record_demo_video.py        → demo_video/NeighbourBrief_demo_backup.mp4

Scenes are recorded separately and joined with ffmpeg, so the ~40 s AI drafting wait is cut out.
The bulletin created for the video is removed afterwards (History stays as it was).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from visual_check import start_server  # noqa: E402

from app import store  # noqa: E402

OUT_DIR = ROOT / "demo_video"
RAW = OUT_DIR / "raw"
SIZE = {"width": 1280, "height": 800}
PORT = 8030
BASE = f"http://127.0.0.1:{PORT}"
SITE = f"http://127.0.0.1:{PORT + 1}/"

CAPTION_JS = """(text) => {
  let el = document.getElementById('nb-caption');
  if (!el) {
    el = document.createElement('div'); el.id = 'nb-caption';
    el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);z-index:9999;' +
      'max-width:1000px;padding:14px 22px;border-radius:14px;background:rgba(12,34,66,.92);color:#fff;' +
      'font:600 22px/1.35 "Noto Sans",system-ui,sans-serif;text-align:center;box-shadow:0 8px 30px rgba(0,0,0,.3)';
    document.body.appendChild(el);
  }
  el.textContent = text; el.style.display = text ? 'block' : 'none';
}"""

CARD = """<!doctype html><meta charset=utf-8><body style="margin:0;height:100vh;display:flex;align-items:center;
justify-content:center;background:#0c2242;color:#fff;font-family:'Noto Sans',system-ui,sans-serif">
<div style="max-width:900px;text-align:center;padding:40px">{body}</div></body>"""


def caption(page, text, hold=0.0):
    page.evaluate(CAPTION_JS, text)
    if hold:
        page.wait_for_timeout(int(hold * 1000))


def scene(browser, name, fn, **ctx_kw):
    d = RAW / name
    d.mkdir(parents=True, exist_ok=True)
    ctx = browser.new_context(viewport=SIZE, record_video_dir=str(d), record_video_size=SIZE, **ctx_kw)
    page = ctx.new_page()
    try:
        result = fn(page)
    finally:
        video = page.video
        ctx.close()
    return video.path(), result


def main() -> int:
    from playwright.sync_api import sync_playwright

    shutil.rmtree(RAW, ignore_errors=True)
    OUT_DIR.mkdir(exist_ok=True)
    before = {r["id"] for r in store.list_all()}
    proc = start_server(PORT)
    import functools
    import http.server
    import threading
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_public_site import build
    build(ROOT / "public_site")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    site_srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT + 1), functools.partial(Quiet, directory=str(ROOT / "public_site")))
    threading.Thread(target=site_srv.serve_forever, daemon=True).start()
    clips = []
    created = None
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()

            def title(page):
                page.set_content(CARD.format(body=
                    "<div style='font-size:54px;font-weight:700'>NeighbourBrief TO</div>"
                    "<div style='font-size:26px;margin-top:18px;opacity:.9'>Toronto's open safety data as a one-page "
                    "monthly bulletin for each neighbourhood: plain language, residents' own languages, and every "
                    "number checked against the data.</div>"))
                page.wait_for_timeout(5000)
            clips.append(scene(b, "00_title", title)[0])

            def how(page):
                page.goto(f"{BASE}/how-it-works", wait_until="networkidle")
                caption(page, "Code counts every number. AI only writes sentences. Every number is checked before publishing.", 5)
            clips.append(scene(b, "00b_how", how)[0])

            def profile(page):
                page.goto(f"{BASE}/neighbourhoods/055", wait_until="networkidle")
                caption(page, "One neighbourhood, eight open datasets from TPS and the City of Toronto", 3.5)
                page.evaluate("document.getElementById('pf-road').scrollIntoView({block:'start'})")
                caption(page, "Road safety from TPS collision data. A month still being filled in is drawn lighter, not hidden.", 5)
                page.evaluate("document.getElementById('pf-monthly').scrollIntoView({block:'start'})")
                caption(page, "This year month by month, and every count links to the same query on the TPS portal", 3.5)
                page.evaluate("document.getElementById('pf-trend').scrollIntoView({block:'start'})")
                caption(page, "Twelve years of reports, and the languages residents speak at home (2021 Census)", 4)
                page.evaluate("document.getElementById('pf-people').scrollIntoView({block:'center'})")
                page.wait_for_timeout(2500)
            clips.append(scene(b, "01_profile", profile)[0])

            def new(page):
                page.goto(f"{BASE}/new?hood=Thorncliffe%20Park", wait_until="networkidle")
                page.wait_for_timeout(1500)
                caption(page, "Make a bulletin: officers, data date, map and suggested languages fill in", 3.5)
                page.evaluate("document.getElementById('followup-box').scrollIntoView({block:'center'})")
                caption(page, "You said, we did: follow up on last month's priority, in the officer's words")
                page.click("#followup")
                page.keyboard.type("Evening patrols in the plaza lots and 2 lock-your-car sessions with tenants", delay=14)
                page.select_option("#followup_status", "ongoing")
                page.click("#priority")
                page.keyboard.type("Car break-ins near the plaza parking lots in the evening.", delay=14)
                page.click("#action_taken")
                page.keyboard.type("More evening patrols with the tenant association.", delay=14)
                page.select_option("#tip_id", "car-lock")
                page.evaluate("document.getElementById('draft-btn').scrollIntoView({block:'center'})")
                caption(page, "Code counts every number from the open data. The AI only writes the sentences.", 2.5)
                page.click("#draft-btn", no_wait_after=True)
                page.wait_for_timeout(3000)
            clips.append(scene(b, "02_new", new)[0])

            t0 = time.time()
            while time.time() - t0 < 240:
                new_ids = [r["id"] for r in store.list_all() if r["id"] not in before]
                if new_ids and store.load(new_ids[0]).get("english"):
                    created = new_ids[0]
                    break
                time.sleep(2)
            if not created:
                raise SystemExit("draft didn't appear")

            def review(page):
                page.goto(f"{BASE}/review/{created}", wait_until="networkidle")
                caption(page, "The draft as it will print. Every underlined number matches TPS open data.", 4.5)
                page.evaluate("window.scrollTo({top:0})")
                caption(page, "What if a number is typed wrong?", 1.5)
                page.evaluate("""() => { const t = document.getElementById('bullets'); window._orig = t.value;
                    t.value = t.value.replace(/(\\d+) this year/, (m, n) => (+n + 9) + ' this year'); t.scrollIntoView({block:'center'}); }""")
                page.wait_for_timeout(1200)
                page.click("form[action$='/save'] button[type=submit]")
                page.wait_for_load_state("networkidle")
                page.evaluate("window.scrollTo({top:0})")
                caption(page, "It's flagged in red and publishing is blocked until it's fixed.", 4.5)
                page.evaluate("() => { document.getElementById('bullets').value = window._orig || document.getElementById('bullets').value; }")
                page.evaluate("""() => { const t = document.getElementById('bullets'); t.value = t.value.replace(/(\\d+) this year/, (m, n) => (+n - 9) + ' this year'); }""")
                page.click("form[action$='/save'] button[type=submit]")
                page.wait_for_load_state("networkidle")
                caption(page, "Fixed: verified again. Now translate into Urdu, Pashto and Arabic.", 2.5)
                page.evaluate("document.getElementById('translate-form').scrollIntoView({block:'center'})")
                page.click("#translate-form button[type=submit]")
                page.wait_for_url(f"**/publish/{created}**", timeout=240000)
                page.wait_for_load_state("networkidle")
                page.click("[data-tab=ur]")
                caption(page, "Every translation is checked: same numbers, right alphabet, right to left.", 3.5)
                page.click('[data-panel="ur"] details summary')
                page.wait_for_timeout(600)
                caption(page, "And read back in English: 'down 3%' can never quietly become 'up 3%'.", 4.5)
            clips.append(scene(b, "03_review_translate", review)[0])

            def publish(page):
                page.goto(f"{BASE}/publish/{created}", wait_until="networkidle")
                page.evaluate("document.getElementById('second-check').scrollIntoView({block:'center'})")
                caption(page, "Optional second check: a different person signs off, tied to this exact text.", 2)
                page.fill("#sc-name", "Sgt. Sam Reviewer")
                page.check("#second-check input[name=confirm]")
                page.click("#second-check button[type=submit]")
                page.wait_for_load_state("networkidle")
                page.evaluate("window.scrollTo({top:0})")
                page.click("#publish-btn")
                page.wait_for_load_state("networkidle")
                caption(page, "Published. Print one page per language, or share on WhatsApp and email.", 2.5)
                page.click("[data-tab=ur]")
                page.evaluate("document.querySelector('[data-card=ur]').scrollIntoView({block:'center'})")
                c = page.evaluate("""() => NB.drawCard(document.querySelector('[data-card="ur"]')).toDataURL('image/png')""")
                page.evaluate("""(src) => { const d = document.createElement('div'); d.id = 'card-preview';
                    d.style.cssText = 'position:fixed;inset:0;background:rgba(12,34,66,.85);display:flex;align-items:center;justify-content:center;z-index:9998';
                    d.innerHTML = '<img src="' + src + '" style="height:80vh;border-radius:12px;box-shadow:0 10px 40px rgba(0,0,0,.5)">'; document.body.appendChild(d); }""", c)
                caption(page, "Image to share: a ready-made card for the community's WhatsApp group, in Urdu", 4.5)
            clips.append(scene(b, "04_publish", publish, permissions=["clipboard-read", "clipboard-write"])[0])

            def check(page):
                page.goto(f"{BASE}/check?hood=Thorncliffe%20Park&example=1", wait_until="networkidle")
                caption(page, "Check my numbers: paste any newsletter or post about a neighbourhood", 2.5)
                page.click("form[action='/check'] button[type=submit]")
                page.wait_for_load_state("networkidle")
                page.evaluate("document.getElementById('ck-result').scrollIntoView({block:'start'})")
                caption(page, "Wrong category, wrong direction, or not in the data at all: caught in seconds.", 5)
            clips.append(scene(b, "05_check", check)[0])

            def health(page):
                page.goto(f"{BASE}/data-health", wait_until="networkidle")
                caption(page, "Data health: every dataset's freshness and shape, plus feedback for the publishers", 4.5)
            clips.append(scene(b, "06_health", health)[0])

            def site(page):
                page.goto(SITE + "index.html", wait_until="networkidle")
                caption(page, "One command builds a public website: plain files, no server, no login, with an embed card", 4)
                page.goto(SITE + "neighbourhoods/055.html", wait_until="networkidle")
                page.wait_for_timeout(2000)
            clips.append(scene(b, "07_site", site)[0])

            def end(page):
                page.set_content(CARD.format(body=
                    "<div style='font-size:40px;font-weight:700'>What would make it real</div>"
                    "<div style='font-size:24px;margin-top:22px;line-height:1.6;text-align:left'>"
                    "1. Confirm the current-year crime feed as a supported source<br>"
                    "2. A home for the neighbourhood language table<br>"
                    "3. Community translators to review the AI translations</div>"
                    "<div style='font-size:20px;margin-top:22px'>Open source (MIT) and ready to hand over.</div>"
                    "<div style='font-size:15px;margin-top:40px;opacity:.75'>Prototype built on Toronto Police Service "
                    "open data (Open Government Licence – Ontario). Not affiliated with or endorsed by the Toronto "
                    "Police Service.</div>"))
                page.wait_for_timeout(7000)
            clips.append(scene(b, "08_end", end)[0])
            b.close()
    finally:
        proc.terminate()
        site_srv.shutdown()
        if created:  # leave History exactly as it was
            try:
                store.delete(created)
                (store.BULLETIN_DIR / ".trash" / f"{created}.json").unlink(missing_ok=True)
            except KeyError:
                pass

    out = OUT_DIR / "NeighbourBrief_demo_backup.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for c in clips:
        cmd += ["-i", str(c)]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    print(f"Saved {out.relative_to(ROOT)} ({float(dur):.0f} s, {out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
