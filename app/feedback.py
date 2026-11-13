"""Resident feedback: "What should your officers focus on next month?" on bulletins and profiles.

Privacy first: no names, contact details, IP addresses or cookies are stored; a comment with a phone
number, email or link is refused (residents are pointed to their officers instead). Each answer is one
line in data/feedback/<neighbourhood id>.jsonl: date, topic, optional comment (280 characters),
"was this useful?", language. Comments are never published; staff see a summary when they start the
next bulletin, so "You said, we did" can start from what residents said.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from collections import Counter

from app.config import DATA_DIR

FEEDBACK_DIR = DATA_DIR / "feedback"
MAX_COMMENT = 280
RATE_LIMIT = 5             # answers per visitor per hour (kept in memory only)
DAILY_CAP = 300            # answers per neighbourhood per day, against floods
TOPICS = [
    ("break-ins", "Break-ins"), ("stolen-vehicles", "Stolen vehicles"), ("theft-from-vehicles", "Theft from vehicles"),
    ("packages", "Package thefts"), ("bike-thefts", "Bike thefts"), ("assaults", "Assaults"),
    ("robberies", "Robberies"), ("road-safety", "Road safety and speeding"), ("other", "Something else"),
]
TOPIC_LABEL = dict(TOPICS)
_HID = re.compile(r"^\d{3}$")
_URL = re.compile(r"https?://|www\.|\.(com|ca|org|net)\b", re.I)
_lock = threading.Lock()


class Refused(ValueError):
    """The answer can't be saved; the message says why, in plain words."""


def _path(hid: str):
    if not _HID.match(hid or ""):
        raise Refused("Unknown neighbourhood.")
    return FEEDBACK_DIR / f"{hid}.jsonl"



def entries(hid: str, days: int | None = None) -> list[dict]:
    try:
        lines = _path(hid).read_text(encoding="utf-8").splitlines()
    except (OSError, Refused):
        return []
    out = []
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat() if days else ""
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("date", "") >= since:
            out.append(e)
    return out


def add(hid: str, topic: str, comment: str = "", useful: str = "", lang: str = "en", visitor: str = "") -> dict:
    """Save one answer. Raises Refused with a message for the resident."""
    from app.verify.content import personal_info

    path = _path(hid)
    topic = topic if topic in TOPIC_LABEL else ""
    comment = " ".join((comment or "").split())[:MAX_COMMENT]
    useful = useful if useful in ("yes", "no") else ""
    if not (topic or comment or useful):
        raise Refused("Please pick a topic or write a few words.")
    if comment and (personal_info(comment) or _URL.search(comment)):
        raise Refused("Please leave out phone numbers, emails, addresses and links. To report something "
                      "or be contacted, call 416-808-2222 or your officers (listed on the bulletin).")
    from app import ratelimit
    if visitor and not ratelimit.allow("feedback", visitor, RATE_LIMIT):
        raise Refused("Thanks, we already have your answers for now. Please try again later.")
    today = dt.date.today().isoformat()
    if sum(1 for e in entries(hid, days=1) if e.get("date") == today) >= DAILY_CAP:
        raise Refused("We can't take more answers for this neighbourhood today. Please try again tomorrow.")
    entry = {"date": today, "topic": topic, "comment": comment, "useful": useful, "lang": (lang or "en")[:8]}
    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def summary(hid: str, days: int = 60) -> dict:
    """For staff: what residents asked for recently (topics by count, latest comments)."""
    es = entries(hid, days)
    topics = Counter(e["topic"] for e in es if e.get("topic"))
    return {
        "count": len(es), "days": days,
        "topics": [(TOPIC_LABEL[t], n) for t, n in topics.most_common()],
        "comments": [e for e in reversed(es) if e.get("comment")][:8],
        "useful": (sum(1 for e in es if e.get("useful") == "yes"), sum(1 for e in es if e.get("useful") == "no")),
    }


def totals() -> dict:
    """Across neighbourhoods (Impact page): answers and neighbourhoods with answers."""
    n, hoods = 0, 0
    if FEEDBACK_DIR.exists():
        for p in FEEDBACK_DIR.glob("*.jsonl"):
            k = len(entries(p.stem))
            n += k
            hoods += 1 if k else 0
    return {"answers": n, "neighbourhoods": hoods}
