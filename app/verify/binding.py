"""Number *binding* checks: not just "are these numbers allowed?" but "is each number in the right
place?": the right category line, the same line in every translation, still a percentage.

The basic check (numbers.py) treats a bulletin as one bag of numbers, so a number moved to the
wrong category, two numbers swapped between lines, or "3%" losing its percent sign would pass.
"""
from __future__ import annotations

import re
from collections import Counter

from app.verify import numbers as N

# How "percent" is written after (or before) a number, per language.
_PCT_AFTER = r"(?:\s?[%٪％]|\s*(?:percent|per cent|فیصد|درصد|سلن\w*|فیصده|بالمئة|بالمائة|في المئة|في المائة|"
_PCT_AFTER += r"процент\w*|per cento|por ciento|por cento|porsiyento|porsyento|சதவீத\w*|퍼센트|프로))"
_PCT_BEFORE = r"(?:[%٪％]\s?|百分之\s*|نسبة\s*)"


def _norm(text: str) -> str:
    return N.normalize_digits(text or "")


def _shown_lines(facts: dict) -> list[dict]:
    by_key = {l["key"]: l for l in facts["categories"]}
    return [by_key[k] for k in facts["display"] if k in by_key]


def line_numbers(line: dict, facts: dict) -> set[str]:
    """Numbers that may appear in one category line: its own counts, plus years."""
    nums = {str(line["month"]), str(line["ytd"])}
    if line.get("ytd_prev") is not None:
        nums.add(str(line["ytd_prev"]))
    if line.get("pct_change") is not None:
        nums.add(str(abs(line["pct_change"])))
    nums |= {str(facts["period"]["year"]), str(facts["period"]["prev_year"])}
    nums |= set(N.extract_numbers(line["label"]))  # "5,000" in "Thefts over $5,000"
    return nums


def english_binding(doc: dict, facts: dict) -> list[str]:
    """Each English category line may only use its own category's numbers."""
    shown = _shown_lines(facts)
    bullets = doc.get("bullets") or []
    if len(bullets) != len(shown):
        return [f"The bulletin has {len(bullets)} category lines but {len(shown)} categories; "
                "each category should have exactly one line."]
    issues = []
    for line, text in zip(shown, bullets):
        stray = [n for n in N.extract_numbers(text) if n not in line_numbers(line, facts)]
        if stray:
            own = ", ".join(sorted(line_numbers(line, facts) - {str(facts['period']['year']), str(facts['period']['prev_year'])},
                                   key=lambda s: (len(s), s)))
            issues.append(f"The {line['label']} line has {', '.join(stray)}, which isn't one of its "
                          f"numbers ({own}).")
    return issues


def _fields(doc: dict) -> list[tuple[str, str]]:
    out = [("headline", doc.get("headline", "")), ("summary", doc.get("summary", ""))]
    out += [(f"line {i + 1}", b) for i, b in enumerate(doc.get("bullets") or [])]
    out += [("officers' section", doc.get("officer_section", "")), ("closing", doc.get("closing", ""))]
    return out


def _merged_hint(extra: list[str], english: list[str]) -> str:
    for x in extra:
        for i in range(len(english)):
            for j in range(len(english)):
                if i != j and english[i] + english[j] == x:
                    return f" (looks like {english[i]} and {english[j]} were merged into {x})"
    return ""


def translation_alignment(en: dict, tr: dict) -> list[str]:
    """Each translated field/line must carry the same numbers as the same English field/line."""
    issues = []
    en_f, tr_f = _fields(en), _fields(tr)
    if len(en.get("bullets") or []) != len(tr.get("bullets") or []):
        issues.append(f"The translation has {len(tr.get('bullets') or [])} category lines; the English "
                      f"has {len(en.get('bullets') or [])}.")
    tr_map = dict(tr_f)
    for name, en_text in en_f:
        if name not in tr_map:
            continue
        a, b = N.extract_numbers(en_text), N.extract_numbers(tr_map[name])
        missing = list((Counter(a) - Counter(b)).elements())
        extra = list((Counter(b) - Counter(a)).elements())
        if missing or extra:
            parts = []
            if missing:
                parts.append("missing " + ", ".join(missing))
            if extra:
                parts.append("has " + ", ".join(extra) + " instead")
            issues.append(f"In the {name}, the numbers don't match the English ({'; '.join(parts)})"
                          + _merged_hint(extra, a) + ".")
    return issues


def percentages(text: str) -> list[str]:
    """Canonical numbers written as a percentage in `text` (either side of the number)."""
    t = _norm(text)
    out = []
    for m in N._NUM_RE.finditer(t):
        after = re.match(_PCT_AFTER, t[m.end():m.end() + 16], re.I)
        before = re.search(_PCT_BEFORE + r"$", t[max(0, m.start() - 8):m.start()])
        if after or before:
            c = N._canonical(m.group(0))
            if c is not None:
                out.append(c)
    return out


def percent_binding(en: dict, tr: dict) -> list[str]:
    """A number that is a percentage in English must still be a percentage in the translation."""
    issues = []
    tr_map = dict(_fields(tr))
    for name, en_text in _fields(en):
        need = Counter(percentages(en_text))
        if not need or name not in tr_map:
            continue
        have = Counter(percentages(tr_map[name]))
        lost = list((need - have).elements())
        if lost:
            issues.append(f"In the {name}, {', '.join(n + '%' for n in lost)} is no longer shown as a "
                          "percentage.")
    return issues
