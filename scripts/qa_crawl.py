"""QA crawl: every internal link reachable from the main pages, in staff mode and in public mode.

    .venv/bin/python scripts/qa_crawl.py

Flags: HTTP errors (except deliberate 401/404s in public mode), links that 404, slow pages, and text that
should never reach a user ("None", "nan", "Traceback", unrendered "{{", em dashes, "undefined").
GET only; nothing is changed.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from visual_check import start_server  # noqa: E402

SEEDS = ["/", "/new", "/history", "/neighbourhoods", "/divisions", "/check", "/ask", "/how-we-count", "/how-it-works",
         "/help", "/status", "/data-health", "/impact", "/translations", "/public", "/privacy-ai", "/accessibility", "/feed.xml"]
BAD_TEXT = [(re.compile(r">\s*None\s*<|\bNone\b(?=[^<]{0,20}</(td|p|span|li|strong))"), "None shown"),
            (re.compile(r"\bnan\b"), "nan shown"), (re.compile(r"Traceback|Internal Server Error"), "server error text"),
            (re.compile(r"\{\{|\{%"), "unrendered template"), (re.compile("—"), "em dash"),
            (re.compile(r">\s*undefined\s*<"), "undefined shown")]
SKIP = re.compile(r"/(delete|restore|backup\.zip|import)|numbers\.csv|numbers\.json|meeting\.ics|\.geojson|tile\.openstreetmap|print=1")


def crawl(base: str, headers: dict, label: str, limit: int = 700) -> list[str]:
    problems, seen, queue, slow = [], set(), list(SEEDS), []
    client = httpx.Client(base_url=base, headers=headers, timeout=60, follow_redirects=False)
    while queue and len(seen) < limit:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        t = time.monotonic()
        try:
            r = client.get(path)
        except httpx.HTTPError as exc:
            problems.append(f"[{label}] {path}: request failed ({exc})")
            continue
        dt = time.monotonic() - t
        if dt > 3:
            slow.append(f"{path} {dt:.1f}s")
        expected_closed = label == "public" and r.status_code in (401, 404)
        if r.status_code >= 400 and not expected_closed:
            problems.append(f"[{label}] {path}: HTTP {r.status_code}")
            continue
        if r.status_code in (301, 302, 303, 307):
            loc = r.headers.get("location", "")
            if loc.startswith("/"):
                queue.append(loc)
            continue
        if "html" not in r.headers.get("content-type", ""):
            continue
        text = r.text
        for rx, what in BAD_TEXT:
            m = rx.search(re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", "", text))
            if m:
                problems.append(f"[{label}] {path}: {what} near {text[max(0, m.start() - 60):m.end() + 40]!r}")
        for href in re.findall(r'href="([^"#]+)', text):
            full = urljoin(base + path, href)
            u = urlparse(full)
            if u.netloc != urlparse(base).netloc or SKIP.search(full):
                continue
            p = u.path + (f"?{u.query}" if u.query else "")
            if p not in seen and not (p.startswith("/publish/") and p.count("/") > 2 and "lang=" in p and len(seen) > 400):
                queue.append(p)
    print(f"[{label}] {len(seen)} pages crawled; slow (>3 s): {', '.join(slow) or 'none'}")
    return problems


def main() -> int:
    problems = []
    proc = start_server(8044)
    try:
        problems += crawl("http://127.0.0.1:8044", {}, "staff")
    finally:
        proc.terminate()
    os.environ.update(PUBLIC_DEPLOY="true", STAFF_PASSWORD="", ALLOWED_HOSTS=".trycloudflare.com")
    proc = start_server(8045)
    try:
        problems += crawl("http://127.0.0.1:8045", {"host": "qa.trycloudflare.com"}, "public")
    finally:
        proc.terminate()
    for p in problems:
        print("PROBLEM", p)
    print("No problems found." if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
