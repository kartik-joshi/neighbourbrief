"""Number extraction, normalization and checking: the visible trust feature.

Every number in a bulletin (English and every translation) must be one that code computed from TPS
data (or that the officer typed), and each translation must carry exactly the same numbers as the
English version.
"""
from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Iterable

from markupsafe import Markup, escape

# Map native digits to ASCII: Eastern Arabic, Persian/Urdu (extended), Devanagari, Bengali, Tamil,
# full-width. Plus Arabic decimal/thousands separators and Arabic percent sign.
_DIGIT_BLOCKS = [0x0660, 0x06F0, 0x0966, 0x09E6, 0x0BE6, 0xFF10]
_TRANS = {}
for _base in _DIGIT_BLOCKS:
    for _i in range(10):
        _TRANS[_base + _i] = str(_i)
_TRANS.update({
    0x066B: ".",   # ARABIC DECIMAL SEPARATOR
    0x066C: ",",   # ARABIC THOUSANDS SEPARATOR
    0x066A: "%",   # ARABIC PERCENT SIGN
    0xFF0C: ",",   # FULLWIDTH COMMA
    0xFF0E: ".",   # FULLWIDTH FULL STOP
    0xFF05: "%",   # FULLWIDTH PERCENT
})

_SPACE_SEP = "    "  # nbsp, narrow nbsp, thin space, space (thousands in ru/fr style)

# A number: digit run, optionally followed by groups joined by , . or a space-like thousands separator.
# A space group followed by a percent sign is a separate number ("26 136%" is 26 and 136%, not 26136).
_NUM_RE = re.compile(rf"\d+(?:(?:[.,]|[{_SPACE_SEP}](?=\d{{3}}(?!\d)(?![{_SPACE_SEP}]?[%٪])))\d+)*")


def normalize_digits(text: str) -> str:
    return text.translate(_TRANS)


def _canonical(raw: str) -> str | None:
    """Turn a raw numeric token into a canonical string: '1,234' -> '1234', '12.50' -> '12.5'."""
    s = raw
    for ch in _SPACE_SEP:
        s = s.replace(ch, "")
    parts = re.split(r"[.,]", s)
    seps = re.findall(r"[.,]", s)
    if not seps:
        num = s
    elif all(len(p) == 3 for p in parts[1:]) and len(parts[0]) <= 3:
        num = "".join(parts)  # every separator is a thousands separator (1,234 / 1.234.567)
    elif len(seps) == 1:
        num = parts[0] + "." + parts[1]  # a decimal point or decimal comma
    else:
        # e.g. 1,234.5 or 1.234,5: last separator is the decimal one.
        num = "".join(parts[:-1]) + "." + parts[-1]
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


# "8月" / "8월" is how Chinese/Japanese/Korean write the month name "August", and "tháng 8" is how
# Vietnamese does: a date word, not a count. ("8 tháng", number first, means "8 months": a count.)
_MONTH_SUFFIX = re.compile(r"\s?[月월]")
_MONTH_PREFIX = re.compile(r"(?:^|[^\w])tháng\s?$", re.IGNORECASE)


def is_month_name(norm_text: str, m: re.Match) -> bool:
    if not (m.group(0).isdigit() and 1 <= int(m.group(0)) <= 12):
        return False
    return bool(_MONTH_SUFFIX.match(norm_text, m.end())) or bool(_MONTH_PREFIX.search(norm_text[max(0, m.start() - 8):m.start()]))


def extract_numbers(text: str) -> list[str]:
    """All numbers in `text`, canonicalized, in order of appearance (CJK month names excluded)."""
    if not text:
        return []
    out = []
    norm = normalize_digits(text)
    for m in _NUM_RE.finditer(norm):
        if is_month_name(norm, m):
            continue
        c = _canonical(m.group(0))
        if c is not None:
            out.append(c)
    return out


def canonical_number(value) -> str:
    c = _canonical(str(value))
    return c if c is not None else str(value)


def bulletin_text_fields(doc: dict) -> list[str]:
    """The text fields of a bulletin draft (English or translated) that are checked."""
    fields = [doc.get("headline", ""), doc.get("summary", "")]
    fields += list(doc.get("bullets", []) or [])
    fields += [doc.get("followup", ""), doc.get("officer_section", ""), doc.get("closing", "")]
    return [f for f in fields if f]


def numbers_in_doc(doc: dict) -> list[str]:
    nums: list[str] = []
    for f in bulletin_text_fields(doc):
        nums.extend(extract_numbers(f))
    return nums


def not_allowed(numbers: Iterable[str], allowed: Iterable) -> list[str]:
    allowed_set = {canonical_number(a) for a in allowed}
    return sorted({n for n in numbers if n not in allowed_set}, key=_sort_key)


def _sort_key(n: str):
    try:
        return (0, Decimal(n))
    except InvalidOperation:
        return (1, n)


def compare_multisets(english: list[str], translated: list[str]) -> tuple[list[str], list[str]]:
    """Return (missing_in_translation, extra_in_translation) as sorted lists (with repeats)."""
    ce, ct = Counter(english), Counter(translated)
    missing = sorted((ce - ct).elements(), key=_sort_key)
    extra = sorted((ct - ce).elements(), key=_sort_key)
    return missing, extra


def check_english(doc: dict, allowed: Iterable) -> dict:
    nums = numbers_in_doc(doc)
    bad = not_allowed(nums, allowed)
    return {
        "ok": not bad,
        "numbers": nums,
        "not_allowed": bad,
        "missing": [],
        "extra": [],
    }


def check_translation(english_doc: dict, translated_doc: dict, allowed: Iterable) -> dict:
    en = numbers_in_doc(english_doc)
    tr = numbers_in_doc(translated_doc)
    bad = not_allowed(tr, allowed)
    missing, extra = compare_multisets(en, tr)
    return {
        "ok": not bad and not missing and not extra,
        "numbers": tr,
        "not_allowed": bad,
        "missing": missing,
        "extra": extra,
    }


def describe(result: dict) -> str:
    if result["ok"]:
        return "All numbers match TPS data"
    parts = []
    if result["not_allowed"]:
        parts.append("not in TPS data: " + ", ".join(result["not_allowed"]))
    if result["missing"]:
        parts.append("missing from translation: " + ", ".join(result["missing"]))
    if result["extra"]:
        parts.append("added in translation: " + ", ".join(result["extra"]))
    return "; ".join(parts)


def highlight(text: str, allowed: Iterable, tooltip: str = "Matches TPS data") -> Markup:
    """HTML-escape `text` and wrap each number: verified (subtle underline) or unverified (red)."""
    if not text:
        return Markup("")
    allowed_set = {canonical_number(a) for a in allowed}
    norm = normalize_digits(text)  # same length as text: digit translation is 1:1
    out: list[str] = []
    pos = 0
    for m in _NUM_RE.finditer(norm):
        out.append(str(escape(text[pos:m.start()])))
        original = text[m.start():m.end()]
        c = _canonical(m.group(0))
        if c is not None and c in allowed_set:
            out.append(f'<span class="num-ok" title="{escape(tooltip)}">{escape(original)}</span>')
        else:
            out.append(
                f'<span class="num-bad" title="Not found in TPS data for this bulletin">'
                f"{escape(original)}</span>"
            )
        pos = m.end()
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))
