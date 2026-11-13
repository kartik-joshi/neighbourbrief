"""Translation memory for the lines code writes ("Assaults: 6 reports in September. 95 this year, ..."),
the way apps translate their own interface text: each sentence pattern is translated once, a person
reviews it, and from then on only the numbers change. An officer's own words are never stored here.

An entry is the translation of one English pattern, with its numbers (and the neighbourhood's name) as
slots: {0}, {1}... and {name}. Entries are keyed by the language's plural category for each number
(CLDR rules via Babel), because "1 сообщение / 3 сообщения / 5 сообщений" are different sentences.
Entries come only from translations that passed every check ("machine"), and become "reviewed" when a
translator approves them on /translations/memory. Lines filled from memory are checked again like any
other translation. Stored in data/translation_memory/<language>.json (committed: reviews are valuable).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from functools import lru_cache

from app.config import DATA_DIR
from app.verify import numbers as N

TM_DIR = DATA_DIR / "translation_memory"
_lock = threading.RLock()
_SLOT = re.compile(r"\{(\d+|name)\}")
_LABELS = None


def _labels() -> list[str]:
    global _LABELS
    if _LABELS is None:
        from app.data.facts import CATEGORIES
        _LABELS = sorted((label for _, label in CATEGORIES), key=len, reverse=True)
    return _LABELS


_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_BULLET_REST = re.compile(rf"^\d+ reports? in {_MONTH}\. \d+ this year(?:, the same as by this time last year|"
                          r", (?:up|down) \d+% from \d+ by this time last year|, compared with \d+ by this time last year)?\.$")
_SUMMARY = re.compile(rf"^In {_MONTH}, there were \d+ reports? to police in (?P<name>.+)\. A report to police does not mean anyone was charged\.$")
_SUMMARY0 = re.compile(rf"^There were no reports to police in (?P<name>.+) in {_MONTH} in the categories we track\. A report to police does not mean anyone was charged\.$")
_HEADLINE = re.compile(rf"^(?P<name>.+): safety update for {_MONTH} \d{{4}}$")


@lru_cache(maxsize=1)
def _closings() -> set[str]:
    from app import auto
    return {auto.CLOSING, auto.CLOSING_NCO, "Questions? Talk to your Neighbourhood Community Officers. In an emergency, call 911."}


def _plural(code: str, n: str) -> str:
    from babel.core import Locale
    from app.languages import lang_info
    try:
        return Locale.parse(lang_info(code)["babel"]).plural_form(int(n))
    except Exception:
        return "other"


def pattern(text: str, name: str = "") -> tuple[str, list[str]] | None:
    """(English pattern with slots, the numbers in slot order) for a line code writes; None otherwise."""
    text = (text or "").strip()
    if text in _closings():  # its numbers (911) are slots too, so they match the translation's
        nums = re.findall(r"\d+", text)
        it = iter(range(len(nums)))
        return re.sub(r"\d+", lambda m: "{" + str(next(it)) + "}", text), nums
    m = _SUMMARY.match(text) or _SUMMARY0.match(text) or _HEADLINE.match(text)
    if m and m.group("name") == name:
        pat = text.replace(name, "{name}")
        if text.startswith("In "):  # the summary's count
            n = re.search(r"there were (\d+)", text).group(1)
            return pat.replace(f"there were {n} ", "there were {0} ", 1), [n]
        y = re.search(r"(\d{4})$", text)
        if y:  # the headline's year
            return pat[: -4] + "{0}", [y.group(1)]
        return pat, []
    for label in _labels():
        if text.startswith(label + ": ") and _BULLET_REST.match(text[len(label) + 2:]):
            rest, nums = text[len(label) + 2:], []

            def slot(mm):
                nums.append(mm.group(0))
                return "{" + str(len(nums) - 1) + "}"
            return f"{label}: " + re.sub(r"\d+", slot, rest), nums
    return None


def key(code: str, pat: str, nums: list[str]) -> str:
    return pat + " |" + ",".join(_plural(code, n) for n in nums)


def _path(code: str):
    if not re.fullmatch(r"[a-zA-Z-]{2,8}", code or ""):
        raise ValueError(code)
    return TM_DIR / f"{code}.json"


def load(code: str) -> dict:
    try:
        return json.loads(_path(code).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(code: str, data: dict) -> None:
    TM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(code).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(_path(code))


def fill(template: str, nums: list[str], name: str) -> str:
    return _SLOT.sub(lambda m: name if m.group(1) == "name" else nums[int(m.group(1))], template)


def _template_from(translated: str, nums: list[str], name: str, pct: list[bool] | None = None) -> str | None:
    """Turn a concrete translation back into a template. Each number is matched to its English slot by
    value and by whether it's a percentage ("down 20% from 20" is clear). If two slots still look the
    same (same value, both percentages or both not), we can't tell which is which: None (not stored;
    a later month with different numbers will teach the line). The name, if any, must appear verbatim."""
    pct = pct or [False] * len(nums)
    if "{name}" in translated or re.search(r"\{\d+\}", translated):
        return None
    slots = list(zip(nums, pct))
    if len(set(slots)) != len(slots):
        return None
    norm = N.normalize_digits(translated)
    found = [(m.start(), m.end(), (m.group(1), bool(m.group(2)))) for m in re.finditer(r"(\d+)(\s?[%٪])?", norm)]
    if sorted(f[2] for f in found) != sorted(slots):
        return None
    out, pos = [], 0
    for s_, e, v in found:
        tail = translated[s_ + len(v[0]):e]  # keep the translation's own % (and its spacing)
        out.append(translated[pos:s_] + "{" + str(slots.index(v)) + "}" + tail)
        pos = e
    out.append(translated[pos:])
    t = "".join(out)
    if name:
        if name not in t:
            return None
        t = t.replace(name, "{name}")
    return t


def _pct_slots(pat: str) -> list[bool]:
    return [bool(m.group(2)) for m in re.finditer(r"\{(\d+)\}(%)?", pat)]


def _fields(doc: dict):
    for k in ("headline", "summary", "closing"):
        yield k, None, doc.get(k) or ""
    for i, b in enumerate(doc.get("bullets") or []):
        yield "bullets", i, b


def lookup(doc: dict, code: str, name: str) -> dict:
    """{(field, index): {"text", "back", "status", "key"}} for the lines memory can translate."""
    data, out = load(code), {}
    for field, i, text in _fields(doc):
        p = pattern(text, name)
        if not p:
            continue
        e = data.get(key(code, *p))
        if e:
            out[(field, i)] = {"text": fill(e["tr"], p[1], name), "key": key(code, *p), "status": e["status"],
                               "back": fill(e["back"], p[1], name) if e.get("back") else None}
    return out


def apply(doc: dict, code: str, name: str, translated: dict | None = None) -> tuple[dict, dict] | None:
    """Fill `translated` (or a copy of the English, when every line is in memory) with memory lines.
    Returns (doc, info) or None when memory can't cover what's asked."""
    hits = lookup(doc, code, name)
    if translated is None:
        needed = [(f, i) for f, i, t in _fields(doc) if t]
        extra = [k for k in ("officer_section", "followup") if doc.get(k)]
        if extra or any(k not in hits for k in needed):
            return None
        translated = {k: ([] if k == "bullets" else "") for k in doc}
        translated["bullets"] = list(doc.get("bullets") or [])
    out = {k: (list(v) if isinstance(v, list) else v) for k, v in translated.items()}
    for (field, i), h in hits.items():
        if field == "bullets":
            out["bullets"][i] = h["text"]
        else:
            out[field] = h["text"]
    lines = sum(1 for _, _, t in _fields(doc) if t) + sum(1 for k in ("officer_section", "followup") if doc.get(k))
    reviewed = sum(1 for h in hits.values() if h["status"] == "reviewed")
    return out, {"lines": lines, "from_memory": len(hits), "reviewed": reviewed, "hits": hits,
                 "all_reviewed": lines > 0 and reviewed == lines}


def back_doc(doc_en: dict, hits: dict) -> dict | None:
    """A back-translation assembled from memory entries (each entry keeps its own), for the meaning checks."""
    back = {k: ("" if not isinstance(v, list) else ["" for _ in v]) for k, v in doc_en.items()}
    any_ = False
    for (field, i), h in hits.items():
        if h.get("back"):
            any_ = True
            if field == "bullets":
                back["bullets"][i] = h["back"]
            else:
                back[field] = h["back"]
    return back if any_ else None


def learn(rec: dict, code: str, reviewed_by: str = "") -> int:
    """Store the lines of a translation that passed every check (numbers, alphabet, meaning, no flags) as
    "machine" entries. With `reviewed_by` (a translator confirmed the whole translation), store them as
    "reviewed", replacing machine entries; a translator's earlier review is never overwritten by machine text.
    Bullet lines are stored only if they start with the glossary's locked category name."""
    from app import glossary as G
    from app.verify import meaning
    v = (rec.get("verification") or {}).get(code) or {}
    t = (rec.get("translations") or {}).get(code) or {}
    if not v.get("ok") or not t.get("doc"):
        return 0
    if not reviewed_by and ((v.get("quality") or {}).get("flags") or t.get("engine") == "memory"):
        return 0
    back = t.get("back") or {}
    back = back.get("doc") if back.get("for") == meaning.doc_hash(t["doc"]) else None
    name = rec["facts"]["neighbourhood"]["name"]
    added = 0
    with _lock:
        data = load(code)
        for field, i, text in _fields(rec["english"]):
            p = pattern(text, name)
            if not p:
                continue
            k = key(code, *p)
            old = data.get(k)
            if old and (not reviewed_by or old["status"] == "reviewed"):
                continue
            tr = (t["doc"].get("bullets") or [""] * (i + 1))[i] if field == "bullets" else t["doc"].get(field, "")
            bk = None
            if back:
                bk = (back.get("bullets") or [])[i] if field == "bullets" and i < len(back.get("bullets") or []) else back.get(field) if field != "bullets" else None
            prefix = ""
            if field == "bullets":  # the label is fixed text (it can hold a number: "Thefts over $5,000")
                locked = G.label_prefix(code, text)
                if not locked or not (tr or "").strip().startswith(locked[0]):
                    continue
                prefix, tr = locked[0], tr.strip()[len(locked[0]):]
                if bk and ":" in bk:
                    bprefix, bk = bk.split(":", 1)
                    bk = ":" + bk
                else:
                    bprefix, bk = "", None
            nm = name if "{name}" in p[0] else ""  # only lines whose English has the name must keep it
            template = _template_from(tr, p[1], nm, _pct_slots(p[0]))
            if template is None:
                continue
            bt = _template_from(bk, p[1], nm, _pct_slots(p[0])) if bk else None
            entry = {"en": p[0], "tr": prefix + template, "back": ((bprefix if field == "bullets" else "") + bt) if bt else None,
                     "status": "reviewed" if reviewed_by else "machine", "created": dt.date.today().isoformat(),
                     "reviewed_by": reviewed_by[:60] if reviewed_by else "", "example": text}
            if reviewed_by:
                entry["reviewed_at"] = dt.date.today().isoformat()
            data[k] = entry
            added += 1
        if added:
            _save(code, data)
    return added


def review(code: str, k: str, reviewer: str, new_text: str | None = None) -> dict:
    """A translator approves (and optionally corrects) an entry. The slots must stay the same."""
    with _lock:
        data = load(code)
        e = data.get(k)
        if not e:
            raise KeyError(k)
        if new_text is not None:
            new_text = " ".join(new_text.split())
            if sorted(_SLOT.findall(new_text)) != sorted(_SLOT.findall(e["tr"])):
                raise ValueError("Keep every {0}, {1}… and {name} exactly once: they're where the numbers and the name go.")
            if new_text != e["tr"]:
                e["tr"], e["back"], e["edited"] = new_text, None, True
        e.update(status="reviewed", reviewed_by=" ".join((reviewer or "").split())[:60] or "translator",
                 reviewed_at=dt.date.today().isoformat())
        _save(code, data)
        return e


def stats() -> list[dict]:
    from app.languages import LANGUAGES, lang_info
    out = []
    for code in LANGUAGES:
        if code == "en":
            continue
        d = load(code)
        out.append({"code": code, "lang": lang_info(code), "total": len(d),
                    "reviewed": sum(1 for e in d.values() if e["status"] == "reviewed")})
    return out


def number_forms(code: str, k: str) -> str:
    """Which numbers an entry is for, in plain words: "{0}: like 0, 1 · {1}: like 2, 3, 4". Shown on the
    review page, because a language may word "1 report" and "3 reports" differently (separate entries)."""
    cats = [c for c in k.rsplit(" |", 1)[-1].split(",") if c]
    if not cats or all(c == "other" for c in cats) and len({_plural(code, n) for n in ("1", "2", "5")}) == 1:
        return ""
    parts = []
    for i, c in enumerate(cats):
        ex = [str(n) for n in range(0, 200) if _plural(code, str(n)) == c][:3]
        parts.append(f"{{{i}}}: like {', '.join(ex)}")
    return " · ".join(parts)
