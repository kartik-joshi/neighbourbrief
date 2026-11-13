"""Per-language verification of a bulletin record (numbers + script) and highlighted rendering."""
from __future__ import annotations

from markupsafe import Markup, escape

from app.verify import binding as B
from app.verify import numbers as N
from app.verify import script as S


def verify_language(rec: dict, code: str) -> dict:
    allowed = rec["facts"]["allowed_numbers"]
    en = rec["english"]
    if code == "en":
        num = N.check_english(en, allowed)
        scr = {"ok": True, "issues": [], "scripts": [], "expected": ["Latin"]}
        bind = B.english_binding(en, rec["facts"])
    else:
        tr = (rec.get("translations", {}).get(code) or {}).get("doc")
        if not tr:
            return {"ok": False, "status": "missing", "summary": "Not translated yet",
                    "numbers": {}, "script": {}}
        num = N.check_translation(en, tr, allowed)
        scr = S.check_doc(tr, code, en)
        bind = B.translation_alignment(en, tr) + B.percent_binding(en, tr)
    from app.verify import meaning
    mean = meaning.check((rec.get("translations", {}).get(code) or {}), en) if code != "en" else {"checked": False, "ok": True, "issues": []}
    qual = {"blocking": [], "flags": []}
    if code != "en":
        from app.verify import quality
        entry = rec.get("translations", {}).get(code) or {}
        back = entry.get("back") or {}
        fresh = back.get("doc") if back.get("for") == meaning.doc_hash(tr) else None   # only a back-translation of this text
        qual = quality.check(code, en, tr, fresh, list(rec["facts"].get("display") or []))
    ok = num["ok"] and scr["ok"] and not bind and mean["ok"] and not qual["blocking"]
    parts = []
    if not num["ok"]:
        parts.append("Number mismatch: " + N.describe(num))
    if bind:
        parts.append("Numbers in the wrong place: " + " ".join(bind))
    if not scr["ok"]:
        parts.append(S.describe(scr))
    if not mean["ok"]:
        parts.append("Meaning changed: " + " ".join(i["text"] for i in mean["issues"]))
    if qual["blocking"]:
        parts.append("Meaning changed: " + " ".join(i["text"] for i in qual["blocking"]))
    return {
        "ok": ok,
        "status": "verified" if ok else "mismatch",
        "summary": "All numbers match TPS data" if ok else "; ".join(parts),
        "numbers": num,
        "script": scr,
        "binding": bind,
        "meaning": mean,
        "quality": qual,   # "flags" don't block: they ask a person to look (automatic bulletins leave the language out)
    }


def verify_record(rec: dict) -> dict:
    codes = ["en"] + [c for c in rec.get("languages", []) if c != "en"]
    rec["verification"] = {c: verify_language(rec, c) for c in codes}
    return rec["verification"]


def publish_blocked(rec: dict) -> list[str]:
    """Languages whose check fails (mismatch/mixed script/missing)."""
    v = rec.get("verification") or verify_record(rec)
    return [c for c, r in v.items() if not r["ok"]]


def content_blockers(rec: dict) -> list[dict]:
    """Safety findings (tone, group statements, personal info, missing reminder) that block
    publishing until fixed or confirmed. Length / reading level stay advisory."""
    from app.verify import content

    return [f for f in content.check(rec["english"], rec.get("facts"))["findings"] if f["level"] == "warn"]


def source_tooltip(facts: dict) -> str:
    p = facts["period"]
    return (f"Matches TPS data: {facts['source']['name']}, {p['month_label']} and "
            f"{p['ytd_label']} (vs {p['ytd_prev_label']}), by report date")


def render(text: str, facts: dict, lang: str = "en", source_text: str = "",
           highlight: bool = True) -> Markup:
    """Escape `text`; when highlighting, wrap verified numbers, unverified numbers and
    foreign-script runs in spans."""
    if not text:
        return Markup("")
    if not highlight:
        return Markup(escape(text))
    allowed = {N.canonical_number(a) for a in facts["allowed_numbers"]}
    tip = source_tooltip(facts)
    spans: list[tuple[int, int, str]] = []
    norm = N.normalize_digits(text)
    for m in N._NUM_RE.finditer(norm):
        c = N._canonical(m.group(0))
        cls = "num-ok" if c is not None and c in allowed else "num-bad"
        spans.append((m.start(), m.end(), cls))
    if lang != "en":
        for it in S.find_foreign(text, lang, source_text):
            spans.append((it["start"], it["end"], "mixed-script:" + it["script"]))
    spans.sort()
    out, pos = [], 0
    for s, e, cls in spans:
        if s < pos:
            continue  # overlapping (shouldn't happen: digits are script-neutral)
        out.append(str(escape(text[pos:s])))
        chunk = escape(text[s:e])
        if cls == "num-ok":
            out.append(f'<span class="num-ok" title="{escape(tip)}">{chunk}</span>')
        elif cls == "num-bad":
            out.append(f'<span class="num-bad" title="Not found in TPS data for this bulletin">{chunk}</span>')
        else:
            script = cls.split(":", 1)[1]
            out.append(f'<mark class="mixed-script" title="⚠️ Mixed script: {escape(script)} '
                       f'characters in this language">{chunk}</mark>')
        pos = e
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))
