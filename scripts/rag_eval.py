"""Evaluate "Ask about a neighbourhood" per engine on questions generated from the live facts, so the
right answer is known.

    .venv/bin/python scripts/rag_eval.py                       # every available engine
    .venv/bin/python scripts/rag_eval.py --engines cohere,local --hoods 055,130,035,001

Scores per engine: answer correct (the expected number or words appear), cited the right document,
number guard passed (no number outside the sources), declined when it should, median seconds.
Writes test_artifacts/rag_eval.json and the results table in docs/COHERE.md (between the markers).
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.data import facts as F  # noqa: E402
from app.rag import answer as A  # noqa: E402
from app.verify import numbers as N  # noqa: E402

MARK_START, MARK_END = "<!-- rag-eval:start -->", "<!-- rag-eval:end -->"
LANG_START, LANG_END = "<!-- rag-lang:start -->", "<!-- rag-lang:end -->"
QCACHE = ROOT / "data" / "cache" / "rag_eval_questions.json"

# What Cohere documents (checked on docs.cohere.com, Oct 24, 2026):
# - embed-multilingual-v3.0 ("supported languages" page): every NeighbourBrief language except Pashto.
# - Command A ("trained to perform well in 23 languages"): of ours, only these.
EMBED_DOCUMENTED = {"ur", "ar", "fa", "zh-Hant", "zh-Hans", "ta", "ru", "fil", "it", "pt-pt", "es", "ko", "vi", "pl",
                    "bn", "el", "gu", "uk", "pa", "so"}
COMMAND_A_DOCUMENTED = {"es", "it", "pt-pt", "ko", "zh-Hant", "zh-Hans", "ar", "ru", "pl", "vi", "uk", "el", "fa"}
ENGLISH_HINTS = re.compile(r"\b(assaults?|reported|reports|there were|so far)\b", re.I)


def questions(hood_id: str) -> list[dict]:
    from app.data import profile as P
    p = P.build_profile(hood_id)
    f = p["facts"]
    name, hid = f["neighbourhood"]["name"], f["neighbourhood"]["id"]
    cats = [c for c in f["categories"] if c["month"] > 0] or f["categories"]
    c0 = max(cats, key=lambda c: c["month"])
    pct = next((c for c in f["categories"] if c.get("pct_change")), None)
    qs = [
        {"type": "count", "q": f"How many {c0['label'].lower()} were reported in {name} last month?",
         "expect_num": str(c0["month"]), "doc": f"crime-{hid}"},
        {"type": "ytd", "q": f"How many {c0['label'].lower()} have been reported in {name} so far this year?",
         "expect_num": str(c0["ytd"]), "doc": f"crime-{hid}"},
    ]
    if pct:
        qs.append({"type": "direction", "q": f"Are {pct['label'].lower()} in {name} up or down compared with last year?",
                   "expect_word": "up" if pct["pct_change"] > 0 else "down", "doc": f"crime-{hid}"})  # the direction, not the %
    if p.get("road"):
        qs.append({"type": "collisions", "q": f"How many traffic collisions involving a pedestrian were there in {name} this year?",
                   "expect_num": str(p["road"]["ytd"]["pedestrian"]), "doc": f"road-{hid}"})
    if p.get("census") and p["census"].get("languages"):
        lang = p["census"]["languages"][0]["language"]
        qs.append({"type": "language", "q": f"What is the most common home language other than English and French in {name}?",
                   "expect_word": lang.split("/")[0].lower(), "doc": f"languages-{hid}"})
    nco = f.get("nco") or {}
    if nco.get("has_nco"):
        last = nco["officers"][0]["name"].split()[-1]
        qs.append({"type": "officers", "q": f"Who are the Neighbourhood Community Officers for {name}?", "expect_word": last.lower(), "doc": f"help-{hid}"})
    qs.append({"type": "urdu", "q": f"{name} میں پچھلے مہینے کتنے حملے رپورٹ ہوئے؟", "hood": hid,
               "expect_num": str(next(c["month"] for c in f["categories"] if c["key"] == "Assault")), "doc": f"crime-{hid}"})
    for q in qs:
        q.setdefault("hood", None)
        q["neighbourhood"] = name
    return qs


def translated_questions(hood_id: str) -> list[dict]:
    """The count question in every NeighbourBrief language, translated by Azure Translator (cached,
    so every run asks exactly the same words; the neighbourhood name is protected)."""
    import json as _json
    from app.ai import translate
    from app.languages import LANGUAGES
    f = F.build_facts(hood_id)
    name, hid = f["neighbourhood"]["name"], f["neighbourhood"]["id"]
    expect = str(next(c["month"] for c in f["categories"] if c["key"] == "Assault"))
    english = f"How many assaults were reported in {name} last month?"
    cache = _json.loads(QCACHE.read_text(encoding="utf-8")) if QCACHE.exists() else {}
    out = []
    for code in [c for c in LANGUAGES if c != "en"]:
        key = f"{code}|{english}"
        if key not in cache:
            try:
                doc, _ = translate.translate_azure({"headline": english, "summary": "", "bullets": [], "officer_section": "",
                                                    "closing": "", "followup": ""}, code)
                cache[key] = doc["headline"]
            except Exception as exc:  # no Azure: skip that language, say so
                print(f"  (no {code} question: {exc})")
                continue
        out.append({"type": "language", "lang": code, "q": cache[key], "hood": hid, "neighbourhood": name,
                    "expect_num": expect, "doc": f"crime-{hid}"})
    QCACHE.parent.mkdir(parents=True, exist_ok=True)
    QCACHE.write_text(_json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def same_script(code: str, text: str) -> bool | None:
    """Did the answer come back in the question's alphabet? None for Latin-script languages, where an
    English answer is spotted by common English words instead."""
    from app.languages import lang_info
    from app.verify import script as S
    scripts = lang_info(code)["scripts"]
    if scripts == ["Latin"]:
        return not ENGLISH_HINTS.search(text)
    letters = [S.char_script(ch) for ch in text if ch.isalpha()]
    letters = [x for x in letters if x]
    return bool(letters) and sum(1 for x in letters if x in scripts) / len(letters) > 0.5


GLOBAL = [
    {"type": "rules", "q": "Why is there no percentage change for shootings?", "expect_word": "count", "doc": "rule-small-numbers"},
    {"type": "rules", "q": "Does a report to police mean someone was charged?", "expect_word": "not", "doc": "rule-counting"},
    {"type": "decline", "q": "Which is the most dangerous neighbourhood in Toronto?", "expect_decline": True},
    {"type": "decline", "q": "Rank the neighbourhoods by crime.", "expect_decline": True},
    {"type": "out_of_scope", "q": "What will the weather be in Thorncliffe Park tomorrow?", "expect_no_numbers": True},
]


def score(q: dict, r: dict) -> dict:
    text = r.get("answer", "")
    nums = N.extract_numbers(re.sub(r"\[\d+\]", " ", text))
    if q.get("expect_decline"):
        correct = bool(r.get("declined"))
    elif q.get("expect_no_numbers"):
        correct = r["guard"]["ok"] and not r.get("declined")
    else:
        correct = r["guard"]["ok"]
        if q.get("expect_num"):
            correct = correct and q["expect_num"] in nums
        if q.get("expect_word"):
            correct = correct and q["expect_word"] in text.lower()
    cited = {i for c in r.get("citations") or [] for i in c["doc_ids"]}
    return {"correct": bool(correct), "cited_right": (q.get("doc") in cited) if q.get("doc") else None,
            "guard_ok": r["guard"]["ok"], "held_back": not r["guard"]["ok"], "seconds": r.get("seconds") or 0.0,
            "retrieval": r.get("retrieval"), "engine_used": r.get("engine")}


def run(engines: list[str], hoods: list[str], lang_hood: str | None = None) -> dict:
    qs = ([x for h in hoods for x in questions(h)] + GLOBAL if hoods else []) + (translated_questions(lang_hood) if lang_hood else [])
    results = {}
    for e in engines:
        if not A.engine_available(e):
            print(f"skip {e}: not available")
            continue
        rows = []
        for q in qs:
            t = time.monotonic()
            r = A.ask(q["q"], q.get("hood"), engine=e)
            s = score(q, r)
            s["seconds"] = round(time.monotonic() - t, 2)
            if q.get("lang"):
                tr = (r.get("translated") or {}).get("text")
                s["translated"] = bool(tr)
                s["native"] = bool(r["guard"]["ok"] and same_script(q["lang"], r.get("answer", "")))
                s["same_language"] = same_script(q["lang"], tr or r.get("answer", "")) if r["guard"]["ok"] else None
            rows.append({**q, **s, "answer": r.get("answer", "")[:300], "unsupported": r["guard"]["unsupported"]})
            print(f"[{e}] {'✓' if s['correct'] else '✗'} {q['type']:<12} {q['q'][:70]}")
        rows_main = [x for x in rows if not x.get("lang")]
        lang_rows = [x for x in rows if x.get("lang")]
        rows = rows_main or lang_rows
        answered = [x for x in rows if x["type"] not in ("decline",)]
        cite_rows = [x for x in rows if x["cited_right"] is not None]
        results[e] = {
            "questions": len(rows),
            "accuracy": round(sum(x["correct"] for x in rows) / len(rows), 3),
            "citation_accuracy": round(sum(x["cited_right"] for x in cite_rows) / len(cite_rows), 3) if cite_rows else None,
            "guard_pass": round(sum(x["guard_ok"] for x in answered) / len(answered), 3),
            "held_back": sum(x["held_back"] for x in rows),
            "declines_correct": sum(x["correct"] for x in rows if x["type"] == "decline"),
            "median_seconds": round(statistics.median(x["seconds"] for x in rows), 2),
            "by_type": {t: f"{sum(x['correct'] for x in rows if x['type'] == t)}/{sum(1 for x in rows if x['type'] == t)}"
                        for t in dict.fromkeys(x["type"] for x in rows)},
            "rows": rows,
            "languages": {x["lang"]: {"correct": x["correct"], "same_language": x.get("same_language"), "q": x["q"],
                                      "translated": x.get("translated"), "native": x.get("native"),
                                      "answer": x["answer"], "expect": x["expect_num"], "held_back": x["held_back"]} for x in lang_rows},
        }
    return {"hoods": hoods, "engines": results}


def table(res: dict) -> str:
    lines = ["| Engine | Questions | Correct | Cited the right source | Number guard passed | Held back | Median s |",
             "|---|---|---|---|---|---|---|"]
    for e, r in res["engines"].items():
        cite = f"{r['citation_accuracy']:.0%}" if r["citation_accuracy"] is not None else "n/a"
        lines.append(f"| {e} | {r['questions']} | {r['accuracy']:.0%} | {cite} | {r['guard_pass']:.0%} | {r['held_back']} | {r['median_seconds']} |")
    types = list(next(iter(res["engines"].values()))["by_type"]) if res["engines"] else []
    if types:
        lines += ["", "| Engine | " + " | ".join(types) + " |", "|---|" + "---|" * len(types)]
        for e, r in res["engines"].items():
            lines.append(f"| {e} | " + " | ".join(r["by_type"][t] for t in types) + " |")
    return "\n".join(lines)


def lang_table(res: dict) -> str:
    from app.languages import lang_info
    engines = [e for e in res["engines"] if res["engines"][e].get("languages")]
    if not engines:
        return "_No per-language run yet._"
    codes = list(next(iter(res["engines"][e]["languages"] for e in engines)))

    def cell(e, c):
        x = res["engines"][e]["languages"].get(c)
        if not x:
            return "n/a"
        mark = "✓" if x["correct"] else ("held back" if x["held_back"] else "✗")
        if x["correct"] and x["same_language"] is False:
            mark += " (answered in English)" if lang_info(c)["scripts"] == ["Latin"] else " (wrong alphabet)"
        elif x["correct"] and x.get("translated") and not x.get("native"):
            mark += " (via translation)"
        return mark
    lines = ["| Language | Cohere Embed documents it | Command A documents it | " + " | ".join(engines) + " |",
             "|---|---|---|" + "---|" * len(engines)]
    for c in codes:
        lines.append(f"| {lang_info(c)['name']} | {'yes' if c in EMBED_DOCUMENTED else '**no**'} | "
                     f"{'yes' if c in COMMAND_A_DOCUMENTED else '**no**'} | " + " | ".join(cell(e, c) for e in engines) + " |")
    totals = " · ".join(f"{e}: {sum(1 for x in res['engines'][e]['languages'].values() if x['correct'])}/{len(codes)}" for e in engines)
    details = []
    for e in engines:
        misses = [(c, x) for c, x in res["engines"][e]["languages"].items() if not x["correct"] or x["same_language"] is False]
        for c, x in misses:
            why = "held back by the number guard" if x["held_back"] else ("right number, wrong language" if x["correct"] else f"expected {x['expect']}")
            details.append(f"- **{e}, {lang_info(c)['name']}** ({why}). Q: {x['q']} · A: {x['answer'][:220].replace(chr(10), ' ')}")
    return "\n".join(lines) + f"\n\nCorrect: {totals}.\n\n**Details of every miss**\n\n" + ("\n".join(details) or "None.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="cohere,local,extractive")
    ap.add_argument("--hoods", default="055,130,035", help="'' to run only the per-language questions")
    ap.add_argument("--languages", default="055", help="neighbourhood id for the same question in every language ('' to skip)")
    a = ap.parse_args()
    hoods = [h.strip().zfill(3) for h in a.hoods.split(",") if h.strip()]
    res = run([e.strip() for e in a.engines.split(",") if e.strip()], hoods, a.languages.strip().zfill(3) if a.languages.strip() else None)
    out = ROOT / "test_artifacts" / "rag_eval.json"
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    t = table(res)
    print("\n" + t)
    doc = ROOT / "docs" / "COHERE.md"
    if doc.exists():
        s = doc.read_text(encoding="utf-8")
        when = time.strftime("%Y-%m-%d %H:%M")
        if MARK_START in s and MARK_END in s and hoods:
            names = ", ".join(F.find_neighbourhood(h)["name"] for h in hoods if F.find_neighbourhood(h))
            block = f"{MARK_START}\n_Run {when} on {names} (questions generated from the live facts)._\n\n{t}\n{MARK_END}"
            s = s[:s.index(MARK_START)] + block + s[s.index(MARK_END) + len(MARK_END):]
        if LANG_START in s and LANG_END in s and any(r.get("languages") for r in res["engines"].values()):
            lname = F.find_neighbourhood(a.languages.strip().zfill(3))["name"]
            lblock = (f"{LANG_START}\n_Run {when}: \"How many assaults were reported in {lname} last month?\" in every "
                      f"language (translated by Azure Translator)._\n\n{lang_table(res)}\n{LANG_END}")
            s = s[:s.index(LANG_START)] + lblock + s[s.index(LANG_END) + len(LANG_END):]
        doc.write_text(s, encoding="utf-8")
        print(f"Updated {doc.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
