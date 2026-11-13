"""Crawl the built static site through a plain file server (as any web host would serve it):
every page reachable from index.html loads, no request fails, no console errors, no horizontal
overflow on a phone.

    .venv/bin/python scripts/build_public_site.py && .venv/bin/python scripts/check_public_site.py
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "public_site"
PORT = 8031


def main() -> int:
    from playwright.sync_api import sync_playwright
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(SITE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{PORT}/"
    problems, seen, queue = [], set(), [base + "index.html"]
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            for width in (1280, 390):
                pg = b.new_page(viewport={"width": width, "height": 900})
                errors = []
                pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                pg.on("requestfailed", lambda r: errors.append(f"failed {r.url}") if "tile.openstreetmap" not in r.url else None)
                pg.on("response", lambda r: errors.append(f"{r.status} {r.url}") if r.status >= 400 and r.url.startswith(base) else None)
                todo = list(queue) if width == 1280 else sorted(u for u in seen if u.endswith(".html"))
                visited = set()
                while todo:
                    url = todo.pop(0)
                    if url in visited:
                        continue
                    visited.add(url)
                    errors.clear()
                    pg.goto(url, wait_until="networkidle")
                    if pg.evaluate("document.documentElement.scrollWidth") > width + 1:
                        problems.append(f"{width}px overflow: {url}")
                    problems += [f"{width}px {url}: {e}" for e in errors]
                    if width == 1280:
                        for href in pg.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
                            full = urljoin(url, href).split("#")[0]
                            if urlparse(full).netloc == urlparse(base).netloc and full not in seen:
                                seen.add(full)
                                if full.endswith(".html"):
                                    todo.append(full)
                                else:  # data files: just check they exist
                                    r = pg.request.get(full)
                                    if r.status != 200:
                                        problems.append(f"{r.status} {full} (linked from {url})")
                print(f"{width}px: {len(visited)} pages")
                pg.close()
            b.close()
    finally:
        srv.shutdown()
    print(f"{len(seen)} distinct links")
    for pr in problems:
        print("PROBLEM", pr)
    print("No problems found." if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
