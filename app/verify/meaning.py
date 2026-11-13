"""Meaning check by back-translation: each translation is translated back to English and every line
must still go the same direction ("down 7%" can't come back as "up 7%").

Only clear flips count (up vs down). Back-translation is rough, especially for legal terms (Urdu
"نقب زنی", housebreaking, comes back as "Digging"), so category names are shown to the officer but
never block anything.
"""
from __future__ import annotations

import hashlib
import json
import re

UP = re.compile(r"\b(up|increase[sd]?|increasing|rise[sn]?|rising|rose|higher|more than|jump\w*|climb\w*|grew|grown|growth)\b", re.I)
DOWN = re.compile(r"\b(down|decrease[sd]?|decreasing|drop\w*|fell|fallen|falls?|lower|fewer|less than|declin\w*|reduc\w*)\b", re.I)


def direction(text: str) -> str | None:
    u, d = bool(UP.search(text or "")), bool(DOWN.search(text or ""))
    if u and not d:
        return "up"
    if d and not u:
        return "down"
    return None  # no direction, or both (can't tell)


def doc_hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _pairs(en: dict, back: dict):
    for k in ("headline", "summary"):
        yield k, None, en.get(k, ""), back.get(k, "")
    for i, (e, b) in enumerate(zip(en.get("bullets") or [], back.get("bullets") or [])):
        yield "bullets", i, e, b


def compare(en: dict, back: dict) -> list[dict]:
    """Lines whose direction flipped between the English and the back-translation."""
    issues = []
    for field, i, e, b in _pairs(en, back):
        de, db = direction(e), direction(b)
        if de and db and de != db:
            where = f"line {i + 1}" if field == "bullets" else f"the {field}"
            issues.append({"field": field, "index": i, "en": e, "back": b, "en_dir": de, "back_dir": db,
                           "text": f"In {where}, the English says {de} but the translation reads back as {db}."})
    return issues


def check(entry: dict | None, en: dict) -> dict:
    """Meaning result for one translation entry: {"checked", "ok", "issues", "stale"}."""
    tr = (entry or {}).get("doc")
    back = (entry or {}).get("back") or {}
    if not tr or not back.get("doc"):
        return {"checked": False, "ok": True, "issues": [], "stale": False}
    if back.get("for") != doc_hash(tr):
        return {"checked": False, "ok": True, "issues": [], "stale": True}
    issues = compare(en, back["doc"])
    return {"checked": True, "ok": not issues, "issues": issues, "stale": False}
