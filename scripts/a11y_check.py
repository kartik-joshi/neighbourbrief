"""Accessibility audit with axe-core (WCAG 2.1 A/AA rules) on every page, desktop + mobile.

    .venv/bin/python scripts/a11y_check.py      → test_artifacts/a11y_report.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from visual_check import open_page, start_server  # noqa: E402

AXE = (ROOT / "scripts" / "axe.min.js").read_text()


def main() -> int:
    from playwright.sync_api import sync_playwright

    from app import store
    proc = start_server(8015)
    base = "http://127.0.0.1:8015"
    recs = [r for r in store.list_all() if r.get("demo")][:1] or store.list_all()[:1]
    pages = ["/", "/new", "/new?hood=Thorncliffe%20Park", "/how-we-count", "/check", "/check#filled", "/neighbourhoods", "/neighbourhoods/055", "/neighbourhoods/080", "/divisions", "/divisions/53", "/wards", "/wards/15", "/data-health", "/impact", "/public", "/accessibility", "/privacy-ai", "/help", "/ask", "/ask#filled", "/how-it-works", "/embed/055", "/translations", "/translations/memory", "/translations/memory/fr", "/history", "/status", "/review/no-such-bulletin"]
    # An unpublished draft too (template writer, deleted afterwards): the second-check card, share
    # buttons before publishing and a stale sign-off only appear there.
    import httpx
    tmp = httpx.post(base + "/draft", data={"neighbourhood": "Milliken", "priority": "Package thefts from porches.",
                                            "action_taken": "Door-knocking.", "writer_mode": "template", "author": "A11y Check"},
                     follow_redirects=False, timeout=60).headers["location"].rsplit("/", 1)[-1]
    httpx.post(f"{base}/publish/{tmp}/review-signoff", data={"name": "Second Person", "confirm": "1"})
    store.update(tmp, lambda r: r["english"].update(closing=r["english"].get("closing", "") + " Thanks."))
    pages += [f"/review/{tmp}", f"/publish/{tmp}", f"/publish/{tmp}?error=same"]
    for r in recs:
        pages += [f"/translations/{r['id']}/{c}" for c in r.get("languages", [])[:1]]
    for r in recs:
        pages += [f"/review/{r['id']}", f"/publish/{r['id']}"] + \
                 [f"/publish/{r['id']}?lang={c}" for c in r.get("languages", [])[:1]] + [f"/bulletin/{r['id']}/all"]
    report, total, extra = {}, 0, {}
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for w in (1280, 390):
                pg = b.new_page(viewport={"width": w, "height": 900})
                for path in pages:
                    open_page(pg, base, path)
                    pg.add_script_tag(content=AXE)
                    res = pg.evaluate("""async () => (await axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa','best-practice']})).violations
                        .map(v => ({id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.slice(0, 4).map(n => n.target.join(' ') + ' :: ' + (n.failureSummary || '').split('\\n').slice(1, 2).join(''))}))""")
                    report[f"{w} {path}"] = res
                    total += len(res)
                pg.close()
            # Keyboard: skip link, then arrow keys through the language tabs.
            pg = b.new_page(viewport={"width": 1280, "height": 900})
            if recs:
                pg.goto(f"{base}/publish/{recs[0]['id']}", wait_until="networkidle")
                pg.keyboard.press("Tab")
                first = pg.evaluate("document.activeElement.textContent.trim()")
                pg.focus("[role=tab][aria-selected=true]")
                pg.keyboard.press("ArrowRight")
                after_right = pg.evaluate("[document.activeElement.dataset.tab, document.querySelector('[data-panel]:not(.hidden)').dataset.panel]")
                pg.keyboard.press("End")
                after_end = pg.evaluate("document.activeElement.dataset.tab")
                pg.keyboard.press("ArrowRight")
                wrapped = pg.evaluate("document.activeElement.dataset.tab")
                tabs = pg.evaluate("[...document.querySelectorAll('[role=tab]')].map(t => [t.dataset.tab, t.tabIndex])")
                kb = {"first_tab_stop": first, "after_right": after_right, "after_end": after_end,
                      "wrapped": wrapped, "tabindex": tabs}
                extra["keyboard"] = kb
                codes = [t[0] for t in tabs]
                ok = (first == "Skip to main content" and after_right == [codes[1], codes[1]]
                      and after_end == codes[-1] and wrapped == codes[0]
                      and sum(1 for t in tabs if t[1] == 0) == 1)
                print("Keyboard:", "OK" if ok else "FAILED", kb)
                if not ok:
                    total += 1
                    extra["keyboard_failed"] = True
            pg.close()
            b.close()
    finally:
        proc.terminate()
        store.delete(tmp)
        (store.BULLETIN_DIR / ".trash" / f"{tmp}.json").unlink(missing_ok=True)
    (ROOT / "test_artifacts").mkdir(exist_ok=True)
    (ROOT / "test_artifacts" / "a11y_report.json").write_text(json.dumps({"pages": report, **extra}, indent=1))
    seen = {}
    for page, vs in report.items():
        for v in vs:
            seen.setdefault((v["id"], v["impact"], v["help"]), []).append((page, v["nodes"]))
    for (vid, impact, help_), where in sorted(seen.items(), key=lambda x: x[0][1] or ""):
        print(f"[{impact}] {vid}: {help_} — {len(where)} page(s)")
        for page, nodes in where[:2]:
            print(f"    {page}: {nodes[:2]}")
    print(f"{len(seen)} distinct violation types, {total} page-level violations")
    return 1 if seen or extra.get("keyboard_failed") else 0


if __name__ == "__main__":
    sys.exit(main())
