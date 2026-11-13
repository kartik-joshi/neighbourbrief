"""More guard rails on translations, from the back-translation (translated back to English) that the
meaning check already makes. Numbers and the alphabet are checked elsewhere; these catch translations
whose numbers are all right but whose words went wrong.

Blocking (clear harm, like an up/down flip):
- "not charged" lost its "not": a report to police "means someone was charged".
- Alarming words that the English doesn't have ("dangerous", "crime wave"...).

Flags for a human (shown on Review/Publish; an automatic bulletin has no human, so a flagged language
is left out of it):
- Category drift: a line reads back as a different crime ("theft from vehicles" → "vehicle theft").
- Lost or added content: the line reads back much shorter or longer, or shares few words with the English.
- Left in English: a translated line is the English text.
- Register: Korean in casual speech (bulletins use the polite -요/-니다 forms).

Thresholds were set on the 560+ real Azure translations in data/cache/auto/ (see docs/TEST_REPORT.md).
"""
from __future__ import annotations

import re

from app.verify.content import ALARMING, CRIME_FRAMING

_NEG = re.compile(r"\b(not|no|nobody|no one|none|never|neither|nor|without|doesn'?t|don'?t|isn'?t|wasn'?t|cannot|can'?t)\b|n't\b", re.I)
_CHARGE = re.compile(r"\bcharg|\baccus|\bindict|\bconvict|\bguilty\b|\bprosecut", re.I)
_STOP = set("a an the of in on at to for by from with and or is are was were be been this that these those it its "
            "there their they we you your our as so than then by this last year month time reports report police".split())
_CAT = [  # (key, back-translation pattern); "theft from vehicles" must be tested before "vehicle theft"
    ("Theft from Motor Vehicle", r"(theft|stealing|stolen|robber\w*|burglar\w*)s? (from|in|inside|of items in|of property from) (motor )?(vehicles?|cars?|automobiles?)|(things|items|property|belongings) stolen from (vehicles?|cars?)|car break[- ]?ins?"),
    ("Bike Theft", r"bi(ke|cycle)s?"),
    ("Auto Theft", r"(stolen|theft of|thefts? of|stealing) (motor )?(vehicles?|cars?|automobiles?)|(vehicle|car|auto|automobile) thefts?|(vehicles?|cars?) (were |was )?stolen|carjack\w*"),
    ("Break and Enter", r"break[- ]?ins?|breaking and entering|burglar\w*|housebreak\w*|break and enter"),
    ("Robbery", r"robber\w*|mugg\w*|hold-?ups?"),
    ("Assault", r"assault\w*|attacks?|violence|beat\w*"),
    ("Shooting", r"shoot\w*|gunfire|shots? fired|firearm"),
    ("Homicide", r"homicid\w*|murder\w*|killing\w*"),
    ("Theft Over", r"thefts? (over|of more than)"),
]
_KO_CASUAL = re.compile(r"(?:어|아|야|해|지|냐|니|자|래|게|네)(?:[.!?。]|$)")
_KO_POLITE = re.compile(r"(?:다|요|까|오|죠|세요|니다)(?:[.!?。]|$)")

SCORE_MARGIN = 0.06      # meaning score this far below the threshold: strong
RATIO_MARGIN = (0.8, 1.25)  # length ratio this far outside the range: strong
MIN_OVERLAP = 0.25     # share of the English line's content words found in the back-translation
LEN_RATIO = (0.45, 2.2)  # back-translation length / English length


def _words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+", (text or "").lower()):
        if w in _STOP or len(w) < 3:
            continue
        for suf in ("ings", "ing", "ies", "es", "ed", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 3:
                w = w[: -len(suf)]
                break
        out.add(w)
    return out


def _categories(text: str) -> set[str]:
    found, t = set(), (text or "").lower()
    for key, pat in _CAT:
        if re.search(pat, t):
            found.add(key)
            t = re.sub(pat, " ", t)  # "theft from cars" is not also "car theft"
    return found


def _lines(en: dict, tr: dict, back: dict):
    for k in ("headline", "summary", "officer_section", "followup", "closing"):
        if en.get(k):
            yield k, None, en.get(k, ""), tr.get(k, ""), back.get(k, "")
    for i, e in enumerate(en.get("bullets") or []):
        yield "bullets", i, e, (tr.get("bullets") or [""] * (i + 1))[i] if i < len(tr.get("bullets") or []) else "", \
            (back.get("bullets") or [])[i] if i < len(back.get("bullets") or []) else ""


def _locked_label(code: str, en_line: str, tr_line: str) -> bool:
    """The line starts with the glossary's own (hand-checked, protected) name for its category, so the
    category can't have drifted; Azure's back-translation of it is lossy ("thefts from vehicles" in
    Polish, Ukrainian or Greek reads back as "vehicle thefts")."""
    from app import glossary as G
    fixed = G.label_prefix(code, en_line)
    return bool(fixed) and (tr_line or "").strip().startswith(fixed[0])


def _where(field: str, i: int | None) -> str:
    return f"line {i + 1}" if field == "bullets" else f"the {field.replace('_', ' ')}"


def _direct(code: str, en: dict, tr: dict, add, flags) -> None:
    """Meaning score and length, English vs translation directly (app/verify/similarity.py): no
    back-translation needed, so it also checks offline translations. Skipped if the model isn't there."""
    from app.verify import similarity as SIM
    limits = SIM.thresholds().get(code)
    if not limits:
        return
    scores = SIM.line_scores(en, tr)
    for s_ in scores or []:
        lim = limits.get("bullet" if s_["field"] == "bullets" else "other")
        if not lim:
            continue
        where = _where(s_["field"], s_["index"])
        ratio = len(s_["tr"]) / max(1, len(s_["en"]))
        # beyond the calibrated range: ask a person; far beyond it: strong enough to leave the language
        # out of an automatic bulletin (about 1% of good lines are near the edge, so near isn't enough there)
        far = s_["score"] < lim["score"] - SCORE_MARGIN or ratio < lim["ratio"][0] * RATIO_MARGIN[0] or ratio > lim["ratio"][1] * RATIO_MARGIN[1]
        if s_["score"] < lim["score"]:
            add(flags, "meaning_score", s_["field"], s_["index"],
                f"{where.capitalize()} doesn't seem to say the same as the English (meaning score {s_['score']:.2f}, usually above {lim['score']:.2f}).",
                s_["en"], "", score=s_["score"], strong=far)
        elif len(s_["en"]) >= 30 and not lim["ratio"][0] * RATIO_MARGIN[0] <= ratio <= lim["ratio"][1] * RATIO_MARGIN[1]:
            # length varies more than meaning (names, word order): only well outside the usual range
            add(flags, "length_direct", s_["field"], s_["index"],
                f"{where.capitalize()} is {'much shorter' if ratio < lim['ratio'][0] else 'much longer'} than usual for {code} (something may be missing or added).",
                s_["en"], "", score=s_["score"], strong=far)


def check(code: str, en: dict, tr: dict, back: dict | None, categories: list[str] | None = None) -> dict:
    """{"blocking": [...], "flags": [...]}; each item {"kind", "field", "index", "text", "en", "back"}.
    `categories`: the category key of each bullet, in order (from the facts' display list)."""
    blocking, flags = [], []

    def add(lst, kind, field, i, text, e="", b="", **extra):
        lst.append({"kind": kind, "field": field, "index": i, "text": text, "en": e, "back": b, "strong": True, **extra})

    for field, i, e, t, b in _lines(en, tr, back or {}):
        if t and len(e) > 25 and t.strip().lower() == e.strip().lower():
            add(flags, "untranslated", field, i, f"{_where(field, i).capitalize()} is still in English.", e, t)
        if code == "ko" and t:
            sentences = [s for s in re.split(r"(?<=[.!?。])\s+", t.strip()) if re.search(r"[가-힣]", s)]
            casual = [s for s in sentences if _KO_CASUAL.search(s.strip()) and not _KO_POLITE.search(s.strip())]
            if casual:
                add(flags, "register", field, i, f"{_where(field, i).capitalize()} uses casual Korean; bulletins use the polite form.", e, casual[0])
        if not back or not b:
            continue
        # "does not mean anyone was charged" must keep its "not"
        if _CHARGE.search(e) and _NEG.search(e):
            if _CHARGE.search(b) and not _NEG.search(b):
                add(blocking, "negation", field, i, f"In {_where(field, i)}, \"not charged\" reads back without the \"not\".", e, b)
            elif not _CHARGE.search(b):
                add(flags, "lost_reminder", field, i, f"In {_where(field, i)}, the \"a report doesn't mean anyone was charged\" reminder doesn't read back.", e, b)
        added = [w for w in ALARMING + CRIME_FRAMING if re.search(rf"\b{re.escape(w)}\b", b, re.I) and not re.search(rf"\b{re.escape(w)}\b", e, re.I)]
        if added:
            add(blocking, "alarming", field, i, f"{_where(field, i).capitalize()} reads back with \"{added[0]}\", which the English doesn't say.", e, b)
        ew, bw = _words(e), _words(b)
        if len(ew) >= 4 and len(ew & bw) / len(ew) < MIN_OVERLAP:
            add(flags, "lost_content", field, i, f"{_where(field, i).capitalize()} reads back quite differently from the English.", e, b)
        elif len(e) >= 30 and not LEN_RATIO[0] <= len(b) / len(e) <= LEN_RATIO[1]:
            add(flags, "length", field, i, f"{_where(field, i).capitalize()} reads back {'much shorter' if len(b) < len(e) else 'much longer'} than the English.", e, b)
        if field == "bullets" and categories and i < len(categories) and not _locked_label(code, e, t):
            got = _categories(b.split(":")[0]) or _categories(b)
            if got and categories[i] not in got:
                add(flags, "category", field, i, f"Line {i + 1} should be about {categories[i].lower()} but reads back as {', '.join(sorted(g.lower() for g in got))}.", e, b)
    _direct(code, en, tr, add, flags)
    return {"blocking": blocking, "flags": flags}
