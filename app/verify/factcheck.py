"""'Check my numbers': fact-check any text (newsletter, release, post) against the open-data facts
for a neighbourhood and month.

For each number in the text: does it match a fact, and is it used for the right category and in
the right direction? Dates, times, phone numbers, addresses and years are recognised and skipped.
"""
from __future__ import annotations

import re

from app.verify import numbers as N

# Words that tie a sentence to a category (English).
CATEGORY_WORDS = {
    "Assault": r"assault",
    "Break and Enter": r"break[- ]?ins?|break and enters?|burglar\w*",
    "Auto Theft": r"stolen (?:vehicles?|cars?)|auto thefts?|car thefts?|vehicle thefts?|cars? stolen",
    "Theft from Motor Vehicle": r"thefts? from (?:motor )?vehicles?|thefts? from cars?|car break[- ]?ins?",
    "Robbery": r"robber\w*",
    "Theft Over": r"thefts? over",
    "Bike Theft": r"bike thefts?|bicycle thefts?|stolen bikes?",
    "Shooting": r"shootings?",
    "Homicide": r"homicides?|murders?",
    "Collisions": r"collisions?|crash\w*|(?:traffic|car|road) accidents?|road safety|pedestrians?|cyclists?",
}
UP = r"\b(up|increase[sd]?|increasing|rise|rose|risen|higher|more|jump\w*|climb\w*)\b"
DOWN = r"\b(down|decrease[sd]?|decreasing|drop\w*|fell|fallen|fall|lower|fewer|less|declin\w*)\b"

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
SKIP_PATTERNS = [
    ("dollar amount", re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?")),
    ("phone", re.compile(r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")),
    ("time", re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:a\.?m\.?|p\.?m\.?)", re.I)),
    ("date", re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}", re.I)),
    ("address", re.compile(r"\b\d{1,5}[A-Za-z]?\s+(?:[A-Z][\w'.-]*\s){1,3}(?:Street|St|Avenue|Ave|Av|Road|Rd|Drive|Dr|"
                           r"Boulevard|Blvd|Crescent|Cres|Court|Ct|Lane|Way|Place|Pl|Circle|Trail|Square)\b\.?")),
    ("year", re.compile(r"\b(?:19|20)\d{2}\b")),
]


def road_index(road: dict | None, facts: dict) -> list[dict]:
    """Traffic collision numbers (TPS Traffic Collisions) for the same neighbourhood and month."""
    if not road:
        return []
    p = facts["period"]
    names = {"total": "collisions", "injury": "collisions with injuries", "pedestrian": "collisions involving a pedestrian",
             "cyclist": "collisions involving a cyclist", "fatal": "fatal collisions"}
    out = []
    for k, label in names.items():
        out.append({"value": str(road["month"][k]), "key": "Collisions", "kind": "month", "text": f"Traffic {label}: {p['month_label']}"})
        out.append({"value": str(road["ytd"][k]), "key": "Collisions", "kind": "ytd", "text": f"Traffic {label}: year to date {p['year']}"})
        out.append({"value": str(road["ytd_prev"][k]), "key": "Collisions", "kind": "prev", "text": f"Traffic {label}: same period {p['prev_year']}"})
    if road.get("pct_change") is not None:
        pc = road["pct_change"]
        out.append({"value": str(abs(pc)), "key": "Collisions", "kind": "pct", "sign": (pc > 0) - (pc < 0),
                    "text": f"Traffic collisions: {'up' if pc > 0 else 'down' if pc < 0 else 'no change'} {abs(pc)}% year to date vs {p['prev_year']}"})
    return out


def fact_index(facts: dict, road: dict | None = None) -> list[dict]:
    """Every number the facts support, with a plain description."""
    p = facts["period"]
    out = road_index(road, facts)
    for l in facts["categories"]:
        out.append({"value": str(l["month"]), "key": l["key"], "kind": "month",
                    "text": f"{l['label']}: reports in {p['month_label']}"})
        out.append({"value": str(l["ytd"]), "key": l["key"], "kind": "ytd",
                    "text": f"{l['label']}: year to date {p['year']}"})
        if l.get("ytd_prev") is not None:
            out.append({"value": str(l["ytd_prev"]), "key": l["key"], "kind": "prev",
                        "text": f"{l['label']}: same period {p['prev_year']}"})
        if l.get("pct_change") is not None:
            out.append({"value": str(abs(l["pct_change"])), "key": l["key"], "kind": "pct",
                        "sign": (l["pct_change"] > 0) - (l["pct_change"] < 0),
                        "text": f"{l['label']}: {'up' if l['pct_change'] > 0 else 'down' if l['pct_change'] < 0 else 'no change'} "
                                f"{abs(l['pct_change'])}% year to date vs {p['prev_year']}"})
    out.append({"value": str(facts["totals"]["month"]), "key": None, "kind": "total",
                "text": f"All categories: reports in {p['month_label']}"})
    out.append({"value": str(facts["totals"]["ytd"]), "key": None, "kind": "total",
                "text": f"All categories: year to date {p['year']}"})
    return out


def _sentences(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in re.finditer(r"[.!?](?=\s+[A-Z0-9\"“(]|\s*$)|\n+", text):
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _categories_in(sentence: str) -> set[str]:
    s = sentence.lower()
    found = {k for k, pat in CATEGORY_WORDS.items() if re.search(pat, s)}
    if "Theft from Motor Vehicle" in found and re.search(r"car break[- ]?ins?", s):
        found.discard("Break and Enter")
    return found


def check_text(text: str, facts: dict, road: dict | None = None) -> dict:
    """Return {"items": [...], "counts": {...}} for every number in `text` (crime facts, plus traffic
    collisions when `road` is given)."""
    index = fact_index(facts, road)
    norm = N.normalize_digits(text or "")
    skipped_spans = []
    for kind, pat in SKIP_PATTERNS:
        for m in pat.finditer(norm):
            skipped_spans.append((m.start(), m.end(), kind))
    sentences = _sentences(norm)
    items = []
    for m in N._NUM_RE.finditer(norm):
        value = N._canonical(m.group(0))
        if value is None:
            continue
        span = next(((s, e, k) for s, e, k in skipped_spans if s <= m.start() and m.end() <= e), None)
        skip = span[2] if span else None
        if span and items and items[-1].get("_span") == span:
            continue  # one item per phone number / date / address, not one per digit group
        s0, s1 = next(((a, b) for a, b in sentences if a <= m.start() < b), (0, len(norm)))
        sentence = norm[s0:s1]
        is_pct = bool(re.match(r"\s?(%|percent|per cent)", norm[m.end():m.end() + 9], re.I))
        item = {"start": m.start(), "end": m.end(), "text": text[m.start():m.end()], "value": value,
                "sentence": text[s0:s1].strip()}
        if skip and not is_pct:
            item.update(verdict="skipped", _span=span, start=span[0], end=span[1], text=text[span[0]:span[1]],
                        detail=f"Looks like {'an' if skip[0] in 'aeiou' else 'a'} {skip}, not a statistic.")
            items.append(item)
            continue
        cats = _categories_in(sentence)
        matches = [f for f in index if f["value"] == value and (f["kind"] == "pct") == is_pct]
        if not matches:
            close = [f for f in index if f["value"] == value]
            hint = ""
            if close and is_pct:
                hint = " It matches a count, but it's written as a percentage."
            elif close:
                hint = " It matches a percentage, but it's written as a count."
            item.update(verdict="not_found",
                        detail=f"Not in the TPS data for {facts['neighbourhood']['name']}, {facts['period']['month_label']}." + hint)
            items.append(item)
            continue
        if cats:
            in_cat = [f for f in matches if f["key"] in cats]
            if not in_cat:
                item.update(verdict="wrong_category",
                            detail=f"{value} is the number for {matches[0]['text']}, but this sentence is about "
                                   f"{', '.join(_label(facts, c) for c in sorted(cats))}.",
                            facts=[f["text"] for f in matches])
                items.append(item)
                continue
            matches = in_cat
        pct_match = next((f for f in matches if f["kind"] == "pct"), None)
        if pct_match:
            says_up, says_down = bool(re.search(UP, sentence, re.I)), bool(re.search(DOWN, sentence, re.I))
            if (says_up and not says_down and pct_match["sign"] < 0) or (says_down and not says_up and pct_match["sign"] > 0):
                item.update(verdict="direction",
                            detail=f"The data says {pct_match['text']}, but this sentence says the opposite direction.",
                            facts=[pct_match["text"]])
                items.append(item)
                continue
        item.update(verdict="match", detail=" or ".join(f["text"] for f in matches[:3]),
                    facts=[f["text"] for f in matches])
        items.append(item)
    for it in items:
        it.pop("_span", None)
    counts = {k: sum(1 for i in items if i["verdict"] == k)
              for k in ("match", "wrong_category", "direction", "not_found", "skipped")}
    return {"items": items, "counts": counts,
            "ok": counts["wrong_category"] + counts["direction"] + counts["not_found"] == 0}


def example_text(facts: dict, road: dict | None = None) -> str:
    """A sample paragraph built from these facts with one mistake of each kind (for demos and
    training): a correct line, a number from another category, a wrong direction and an invented number."""
    p = facts["period"]
    month = p["month_label"].split(" ")[0]
    usable = [c for c in facts["categories"] if c["key"] != "Theft Over"]
    with_pct = [c for c in usable if c.get("pct_change")]
    lines = [f"{facts['neighbourhood']['name']} safety update, {p['month_label']}."]
    used = set()
    if with_pct:
        c = with_pct[0]
        lines.append(f"{c['label']} were {'down' if c['pct_change'] < 0 else 'up'} {abs(c['pct_change'])}% this year, "
                     f"with {c['ytd']} reports so far.")
        used.add(c["key"])
        other = next((o for o in usable if o["key"] not in used and o["ytd"] != c["ytd"]
                      and str(c["ytd"]) not in {str(o["month"]), str(o["ytd"]), str(o.get("ytd_prev"))}), None)
        if other:
            lines.append(f"{other['label']} rose to {c['ytd']} this year.")
            used.add(other["key"])
        flip = next((o for o in with_pct if o["key"] not in used), c)
        lines.append(f"{flip['label']} are {'up' if flip['pct_change'] < 0 else 'down'} {abs(flip['pct_change'])}% "
                     f"compared with last year.")
        used.add(flip["key"])
    taken = {f["value"] for f in fact_index(facts, road)}  # the invented number must be in no dataset
    invented = next(str(n) for n in range(37, 400) if str(n) not in taken)
    last = next((o for o in usable if o["key"] not in used), usable[0])
    lines.append(f"There were {invented} {last['label'].lower()} in {month}.")
    lines.append("Join us on Tuesday at 7 p.m. at 100 Queen Street West, or call 416-808-5300.")
    return "\n".join(lines)


def _label(facts: dict, key: str) -> str:
    if key == "Collisions":
        return "traffic collisions"
    return next((l["label"] for l in facts["categories"] if l["key"] == key), key)


def render(text: str, result: dict):
    """Text as HTML with each checked number marked by verdict."""
    from markupsafe import Markup, escape
    cls = {"match": "fc-ok", "wrong_category": "fc-warn", "direction": "fc-warn", "not_found": "fc-bad", "skipped": "fc-skip"}
    out, pos = [], 0
    for i, it in enumerate(result["items"]):
        out.append(str(escape(text[pos:it["start"]])))
        out.append(f'<mark class="{cls[it["verdict"]]}" title="{escape(it["detail"])}" id="fc-{i}">'
                   f'{escape(text[it["start"]:it["end"]])}</mark>')
        pos = it["end"]
    out.append(str(escape(text[pos:])))
    return Markup("".join(out).replace("\n", "<br>"))
