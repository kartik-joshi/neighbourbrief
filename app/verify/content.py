"""Plain-language & safety checks on the English bulletin (advisory, shown on Review).

The AI prompt asks for these rules, but officers edit text and template drafts skip the model, so
the rules are checked on whatever text will actually be published:
- tone: no alarming words ("dangerous", "crime wave", …)
- no statements about groups of people (who commits or suffers crime)
- no personal information (resident phone numbers, emails, home addresses)
- remind once that a report to police isn't a charge
- length 120–180 words, reading level around Grade 6
"""
from __future__ import annotations

import re
from functools import lru_cache

from app.verify.numbers import bulletin_text_fields

ALARMING = ["dangerous", "unsafe", "crime wave", "crime-wave", "hotspot", "hot spot", "surge",
            "spike", "epidemic", "rampant", "out of control", "war zone", "crime-ridden", "no-go"]
CRIME_FRAMING = ["crimes committed", "criminals", "thugs", "perpetrators"]
GROUP_WORDS = ["immigrant", "immigrants", "refugee", "refugees", "newcomer", "newcomers",
               "black", "white", "asian", "brown", "muslim", "muslims", "hindu", "sikh", "jewish",
               "christian", "african", "arab", "arabs", "chinese", "tamil", "pakistani", "afghan",
               "somali", "russian", "filipino", "italian", "portuguese", "youths", "teenagers",
               "gang", "gangs", "homeless", "illegal"]

# Contacts TPS publishes are fine; anything else looks like a resident's details.
_OK_EMAIL = re.compile(r"@(tps\.ca|torontopolice\.on\.ca)$", re.I)
_OK_PHONE = re.compile(r"^(416[-. ]?808[-. ]?\d{4}|911)$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4})(?!\d)")
ADDRESS = re.compile(
    r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][\w'.-]*\s){1,3}"
    r"(Street|St|Avenue|Ave|Av|Road|Rd|Drive|Dr|Boulevard|Blvd|Crescent|Cres|Court|Crt|Ct|Lane|Ln|"
    r"Way|Place|Pl|Circle|Terrace|Trail|Square|Sq)\b\.?")
APARTMENT = re.compile(r"\b(unit|apt\.?|apartment|suite)\s*#?\s*\d+", re.I)

WORDS_MIN, WORDS_MAX = 120, 180
GRADE_TARGET = 6
GRADE_WARN = 9


def _syllables(word: str) -> int:
    w = word.lower().strip(".,;:!?'\"()")
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:es|ed|e)$", "", w) or w
    groups = re.findall(r"[aeiouy]+", w)
    return max(1, len(groups))


def reading_grade(text: str) -> float | None:
    """Flesch–Kincaid grade level (rough; English only)."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    sentences = max(1, len(re.findall(r"[.!?]+(?:\s|$)", text)))
    if len(words) < 20:
        return None
    syl = sum(_syllables(w) for w in words)
    return round(0.39 * len(words) / sentences + 11.8 * syl / len(words) - 15.59, 1)


def _find_words(text: str, words: list[str]) -> list[str]:
    found = []
    for w in words:
        if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text, re.I):
            found.append(w)
    return found


def personal_info(text: str, allowed_text: str = "") -> list[str]:
    """Possible personal details in `text` (things TPS doesn't publish itself)."""
    hits = []
    for m in EMAIL.finditer(text):
        if not _OK_EMAIL.search(m.group(0)):
            hits.append(m.group(0))
    for m in PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group(1))
        if not _OK_PHONE.match(m.group(1)) and not _OK_PHONE.match(digits):
            hits.append(m.group(1))
    for m in ADDRESS.finditer(text):
        hits.append(m.group(0).strip())
    for m in APARTMENT.finditer(text):
        hits.append(m.group(0))
    return list(dict.fromkeys(hits))


@lru_cache(maxsize=1)
def _place_names() -> list[str]:
    """Toronto's 158 neighbourhood names, longest first (2021 Census file, no network)."""
    import json
    from app.config import CONFIG_DIR
    try:
        hoods = json.loads((CONFIG_DIR / "neighbourhood_languages_2021.json").read_text(encoding="utf-8"))["neighbourhoods"]
    except (OSError, ValueError, KeyError):
        return []
    return sorted({h["name"] for h in hoods.values() if h.get("name")}, key=len, reverse=True)


def without_place_names(text: str, extra: list[str] | None = None) -> str:
    """Remove place names before looking for group words: "Black Creek" is a neighbourhood, not a
    statement about people (it once kept Black Creek from getting an automatic bulletin)."""
    for name in list(extra or []) + _place_names():
        text = re.sub(re.escape(name), " ", text, flags=re.I)
    return text


def check(doc: dict, facts: dict | None = None) -> dict:
    """Return {"findings": [...], "words": int, "grade": float|None, "ok": bool}.

    Each finding: {"level": "warn"|"info", "title": str, "detail": str, "matches": [str]}.
    Advisory: shown on Review, never blocks publishing (numbers + script already do).
    """
    text = " ".join(bulletin_text_fields(doc))
    findings = []
    alarming = _find_words(text, ALARMING)
    if alarming:
        findings.append({"level": "warn", "title": "Alarming words",
                         "detail": "Keep the tone calm and neutral; never call a place dangerous.",
                         "matches": alarming})
    framing = _find_words(text, CRIME_FRAMING)
    if framing:
        findings.append({"level": "warn", "title": "Say “reports to police”",
                         "detail": "Counts are reports to police, not proven crimes or offenders.",
                         "matches": framing})
    here = [(facts or {}).get("neighbourhood", {}).get("name", "")] if facts else []
    groups = _find_words(without_place_names(text, [n for n in here if n]), GROUP_WORDS)
    if groups:
        findings.append({"level": "warn", "title": "Mentions a group of people",
                         "detail": "Bulletins must not make statements about who commits or suffers "
                                   "crime. Check these words are needed (e.g. a place name is fine).",
                         "matches": groups})
    pii = personal_info(text)
    if pii:
        findings.append({"level": "warn", "title": "Possible personal information",
                         "detail": "Don't include residents' phone numbers, emails or home addresses. "
                                   "TPS contact details are fine.",
                         "matches": pii})
    if not re.search(r"\bcharged?\b|\bcharges\b", text, re.I):
        findings.append({"level": "warn", "title": "Missing the “report ≠ charge” reminder",
                         "detail": "Remind readers once that a report to police does not mean anyone "
                                   "was charged.", "matches": []})
    words = len(re.findall(r"\b[\w'’-]+\b", text))
    if words < WORDS_MIN or words > WORDS_MAX:
        findings.append({"level": "info", "title": f"{words} words",
                         "detail": f"Aim for {WORDS_MIN}–{WORDS_MAX} words so it reads in a minute "
                                   "and fits one page.", "matches": []})
    grade = reading_grade(text)
    if grade is not None and grade > GRADE_WARN:
        findings.append({"level": "info", "title": f"Reading level ≈ Grade {grade:g}",
                         "detail": f"Aim for about Grade {GRADE_TARGET}: shorter sentences, "
                                   "everyday words.", "matches": []})
    return {"findings": findings, "words": words, "grade": grade,
            "ok": not any(f["level"] == "warn" for f in findings)}


def officer_warnings(officer: dict) -> list[str]:
    """Warnings about the officer's own inputs, shown right after drafting."""
    text = " ".join(v for v in officer.values() if isinstance(v, str))
    out = []
    pii = personal_info(text)
    if pii:
        out.append("Your text may include personal information (" + ", ".join(pii) + "). "
                   "Bulletins shouldn't identify residents or homes. Please check before publishing.")
    groups = _find_words(without_place_names(text), GROUP_WORDS)
    if groups:
        out.append("Your text mentions " + ", ".join(groups) + ". Bulletins shouldn't make statements "
                   "about who commits or suffers crime.")
    return out
