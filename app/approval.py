"""Two-person check without accounts: a second person signs off by name and role, and every
bulletin keeps a short history of who drafted, changed, reviewed and published it.

A sign-off is tied to the exact text it approved (a hash of the English and every translation);
any later change makes it stale, so what's published is what was reviewed. With REQUIRE_REVIEW=true
publishing waits for a current sign-off. Names are typed, not authenticated: this records
responsibility, it isn't access control (that comes with staff sign-in if the tool is hosted).
"""
from __future__ import annotations

import hashlib
import json

from app.config import get_settings
from app.store import now_iso

MAX_EVENTS = 200


def roles() -> dict:
    s = get_settings()
    return {"author": s.role_author, "reviewer": s.role_reviewer}


def content_hash(rec: dict) -> str:
    docs = {"en": rec.get("english") or {}}
    for code, t in sorted((rec.get("translations") or {}).items()):
        if (t or {}).get("doc"):
            docs[code] = t["doc"]
    norm = {c: {k: v for k, v in d.items() if v not in ("", [], None)} for c, d in docs.items()}
    return hashlib.sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def log(rec: dict, action: str, who: str = "", role: str = "", detail: str = "") -> None:
    events = rec.setdefault("audit", [])
    events.append({"at": now_iso(), "action": action, "who": (who or "").strip()[:60], "role": role, "detail": detail[:200]})
    del events[:-MAX_EVENTS]


def state(rec: dict) -> dict:
    review = rec.get("review")
    current = bool(review) and review.get("hash") == content_hash(rec)
    return {"required": get_settings().require_review, "review": review, "current": current,
            "stale": bool(review) and not current, "roles": roles(), "author": rec.get("author", "")}


def sign_off(rec: dict, name: str) -> str | None:
    """Record a sign-off; returns an error code ("name", "same") instead when it can't be accepted."""
    name = " ".join((name or "").split())
    if len(name) < 2:
        return "name"
    author = " ".join((rec.get("author") or "").split())
    if author and author.lower() == name.lower():
        return "same"
    rec["review"] = {"name": name[:60], "role": roles()["reviewer"], "at": now_iso(), "hash": content_hash(rec)}
    log(rec, "reviewed", name, roles()["reviewer"], "Signed off the numbers, wording and translations")
    return None
