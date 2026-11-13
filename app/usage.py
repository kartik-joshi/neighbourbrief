"""Privacy-respecting usage counts: how often bulletins are opened, by language and format.

Only totals per day are kept (data/usage/<YYYY-MM>.json: {"2026-11-03": {"bulletin:ur": 4, ...}}):
no cookies, no IP addresses, no visitor ids, no referrers. Counted only on a public deployment, for
visitors (not signed-in staff), and not for obvious bots, so the numbers mean residents reading.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from collections import Counter

from app.config import DATA_DIR

USAGE_DIR = DATA_DIR / "usage"
_BOT = re.compile(r"bot|crawl|spider|slurp|preview|monitor|curl|wget|python|headless", re.I)
_lock = threading.Lock()
KINDS = [  # (pattern, kind); the language code is the last path part for bulletins
    (re.compile(r"^/bulletin/[\w-]+/easy$"), "easy_read"),
    (re.compile(r"^/bulletin/[\w-]+/all$"), "handout"),
    (re.compile(r"^/bulletin/[\w-]+/([\w-]+)$"), "bulletin"),
    (re.compile(r"^/neighbourhoods/\d{3}$"), "profile"),
    (re.compile(r"^/embed/\d{1,3}$"), "embed"),
    (re.compile(r"^/check$"), "check"),
    (re.compile(r"^/ask$"), "ask"),
]


def kind_of(path: str) -> str | None:
    for rx, kind in KINDS:
        m = rx.match(path)
        if m:
            return f"{kind}:{m.group(1)}" if kind == "bulletin" else kind
    return None


def record(path: str, user_agent: str = "") -> None:
    key = kind_of(path)
    if not key or _BOT.search(user_agent or ""):
        return
    today = dt.date.today()
    f = USAGE_DIR / f"{today:%Y-%m}.json"
    with _lock:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        day = data.setdefault(today.isoformat(), {})
        day[key] = day.get(key, 0) + 1
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
        tmp.replace(f)


def last_days(days: int = 30) -> dict:
    """Totals for the last `days` days: {"total", "bulletins_by_language": [(code, n)], "by_kind": [(kind, n)]}."""
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    c = Counter()
    if USAGE_DIR.exists():
        for f in sorted(USAGE_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for day, counts in data.items():
                if day >= since:
                    c.update(counts)
    langs = Counter({k.split(":", 1)[1]: v for k, v in c.items() if k.startswith("bulletin:")})
    kinds = Counter()
    for k, v in c.items():
        kinds[k.split(":", 1)[0]] += v
    return {"days": days, "total": sum(c.values()), "bulletins_by_language": langs.most_common(),
            "by_kind": kinds.most_common()}
