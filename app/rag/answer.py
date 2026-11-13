"""Answer a question from retrieved documents, with citations and guardrails.

Engines, best available first: Cohere chat with documents (native citations); the configured local
model (citations as [n]); or a sources-only answer that quotes the top documents (always works).
Guardrails: ranking questions are declined (NeighbourBrief never ranks neighbourhoods); every number
in an answer must appear in the retrieved documents or the question, otherwise the answer is held
back and only the sources are shown."""
from __future__ import annotations

import re
import time

from app.ai import cohere, llm
from app.rag import corpus, retrieve
from app.verify import numbers as N

SYSTEM = (
    "You answer questions about Toronto neighbourhoods using ONLY the documents provided. They come from "
    "Toronto Police Service and City of Toronto open data, as published by NeighbourBrief TO. Rules: "
    "1) Use only facts and numbers that appear in the documents; never calculate new numbers. "
    "2) If the documents don't answer the question, say you don't have that information. "
    "3) Plain language, at most 4 short sentences, and say which month or period a number is for. 'Last month' "
    "means the latest complete month in the documents; prefer the latest data over older bulletins. "
    "4) Answer in the same language as the question, and write every number with digits (6, not six). "
    "5) Never rank or compare neighbourhoods as safer or more dangerous. "
    "6) A report to police is not a charge; don't describe people or groups.")

RANKING = re.compile(
    r"\b(most|least|more|less)\s+(dangerous|unsafe|violent|safe|criminal|crime[- ]ridden)\b|\b(safest|worst|best|dangerous|unsafe|bad)\s+"
    r"(neighbou?rhoods?|areas?|places?|parts?)\b|\brank(ed|ing|s)?\b|\bwhich\s+neighbou?rhoods?\s+(has|have|is|are)\s+the\s+(most|highest|least|lowest)\b"
    r"|\bavoid\b.*\b(neighbou?rhood|area)\b"
    r"|\b(safer|riskier|more dangerous|less dangerous|less safe|more violent|less violent|more crime|less crime|worse)\b.{0,40}\bthan\s+(?!(last|the last|previous|the previous|before|a year|usual|normal|in\s+\d{4}|\d{4})\b)"  # than last year: fine
    r"|\bcompare\b.{0,60}\b(safety|crime|dangerous|safe)\b", re.I)

DECLINE = ("NeighbourBrief doesn't rank or compare neighbourhoods as safer or more dangerous: numbers depend on population, "
           "visitors and how often people report, and rankings stigmatise the people who live there. You can look at any "
           "neighbourhood on its own profile, with its numbers over time.")


def _number_guard(answer: str, docs: list, question: str) -> list[str]:
    """Numbers in the answer that appear in none of the documents. Numbers in the question don't count:
    a model that repeats a wrong number from the question ("is it true there were 950?") must be caught."""
    allowed = set()
    for d, _ in docs:
        allowed.update(N.extract_numbers(d.title + " " + d.text))
    clean = re.sub(r"\[\d+\]", " ", answer)                      # citation markers
    clean = re.sub(r"(?m)^\s*(?:[-*•]\s*)?\d{1,2}[.)]\s+", " ", clean)  # list numbering ("1. ", "2) ")
    return [n for n in N.extract_numbers(clean) if n not in allowed]


def _local(question: str, docs: list, english: str | None = None) -> dict:
    listing = "\n".join(f"[{i + 1}] {d.title}: {d.text}" for i, (d, _) in enumerate(docs))
    gloss = f" (in English: {english})" if english else ""
    user = (f"Documents:\n{listing}\n\nQuestion: {question}{gloss}\n\n"
            "Answer following the rules. After each sentence, put the number of the document it comes from in square "
            "brackets, like [1].")
    text = llm.complete(SYSTEM, user, temperature=0.1, max_tokens=500).strip()
    cites = []
    for m in re.finditer(r"\[(\d+)\]", text):
        i = int(m.group(1)) - 1
        if 0 <= i < len(docs):
            cites.append({"start": m.start(), "end": m.end(), "text": m.group(0), "doc_ids": [docs[i][0].id]})
    return {"text": text, "citations": cites}


GENERIC = {"report", "reported", "reports", "month", "last", "year", "neighbourhood", "neighborhood", "toronto", "police",
           "data", "many", "much", "there", "number", "tell", "me", "about", "show", "give", "please", "now", "current", "recent",
           "past", "previous", "happened", "have", "has", "been"}
# Topic words: one of these matching is enough to quote a sentence (other words need two matches).
TOPIC = {"assault", "break", "vehicle", "stolen", "robbery", "robberi", "theft", "bike", "shooting", "homicide", "collision",
         "pedestrian", "cyclist", "injur", "fatal", "language", "officer", "service", "percentage", "percent", "charg", "rank"}
NO_ANSWER = "I don't have information about that in NeighbourBrief's data."


def _extractive(question: str, docs: list, hood_names: list[str] | None = None) -> dict:
    """No model: the one or two sentences from the top documents that best match the question
    (rare words count more; the neighbourhood's own name and generic words don't count)."""
    import math
    from app.rag.retrieve import _stem, expand, tokens
    skip = {_stem(t) for n in (hood_names or []) for t in tokens(n)} | {_stem(t) for t in GENERIC}
    raw = tokens(question)
    q = ({_stem(t) for t in raw} | expand(set(raw))) - skip
    sents = []
    for rank, (d, _) in enumerate(docs[:4]):
        for sent in re.split(r"(?<=[.!?])\s+", d.text):
            if len(sent) > 12:
                sents.append((rank, sent.strip(), d, {_stem(t) for t in tokens(sent)}))
    weight = {"dataset": 0.5}  # descriptions of a dataset rarely answer a question about a neighbourhood
    df = {}
    for *_, toks in sents:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    cands = []
    for rank, sent, d, toks in sents:
        hit = q & toks
        topical = any(h.startswith(t) for h in hit for t in TOPIC)
        if len(hit) >= (1 if topical or len(q) <= 2 else 2):  # one ordinary shared word is too weak to quote
            score = sum(math.log(1 + len(sents) / df[t]) for t in hit) * weight.get(d.kind, 1.0)
            cands.append((score - 0.2 * rank, rank, sent, d))
    cands.sort(key=lambda x: (-x[0], x[1]))
    if not cands:
        return {"text": NO_ANSWER, "citations": []}
    best = cands[0][0]
    picked = [c for c in cands[:2] if c[0] >= 0.6 * best]
    text, cites = "From the open data: ", []
    for _, _, sent, d in picked:
        text += sent + " "
        marker = f"[{[x[0].id for x in docs].index(d.id) + 1}]"
        cites.append({"start": len(text), "end": len(text) + len(marker), "text": marker, "doc_ids": [d.id]})
        text += marker + " "
    return {"text": text.strip(), "citations": cites}


ENGLISH = re.compile(r"\b(the|there|were|was|reports?|reported|assaults?|from the open data|so far)\b", re.I)


def _translate_answer(text: str, lang: str, out: dict) -> dict | None:
    """An English answer to a question in another language: the same answer in that language through
    Azure (numbers protected), shown only if every number matches the original exactly."""
    from app.ai import translate
    from app.languages import lang_info
    clean = re.sub(r"\s*\[\d+\]", "", text).strip()
    if not ENGLISH.search(clean):
        return None  # already in the question's language
    t = translate.translate_plain(clean, lang, "en")
    if not t:
        return None
    if sorted(N.extract_numbers(t)) != sorted(N.extract_numbers(clean)):
        out["warnings"].append("The answer couldn't be translated with exactly the same numbers, so only the English is shown.")
        return None
    info = lang_info(lang)
    return {"code": lang, "name": info["name"], "native": info["native"], "rtl": info["rtl"], "text": t}


def engine_available(engine: str) -> bool:
    if engine == "cohere":
        return cohere.configured()
    if engine == "local":
        return llm.status().get("ok", False)
    return engine == "extractive"


def ask(question: str, hood_id: str | None = None, engine: str = "auto", k: int = 6) -> dict:
    t0 = time.monotonic()
    question = " ".join((question or "").split())[:500]
    out = {"question": question, "answer": "", "citations": [], "sources": [], "engine": "", "retrieval": "",
           "neighbourhoods": [], "warnings": [], "guard": {"ok": True, "unsupported": []}, "declined": False}
    if not question:
        return out
    if RANKING.search(question):
        out.update(answer=DECLINE, engine="rule", declined=True)
        return out
    # Other languages: an English version of the question for keyword search, sentence picking and the
    # ranking rule (a ranking question in Urdu must be declined too). Azure; skipped offline.
    from app.ai import translate
    lang = translate.detect_language(question)
    english = translate.translate_plain(question, "en", lang) if lang and lang != "en" else None
    out["language"], out["question_en"] = lang, english
    if english and RANKING.search(english):
        out.update(answer=DECLINE, engine="rule", declined=True)
        return out
    search_q = english or question
    docs, hoods, warnings = corpus.candidates(f"{search_q} {question}" if english else question, hood_id)
    out["neighbourhoods"], out["warnings"] = [h["name"] for h in hoods], warnings
    ranked, method = retrieve.rank(search_q, docs, k)
    out["retrieval"] = method
    if engine == "auto":
        engine = next(e for e in ("cohere", "local", "extractive") if engine_available(e))
    result = None
    try:
        if engine == "cohere":
            # Only the original question: Command A's own multilingual ability is what we measure per language
            # (an English hint makes models answer in English; English answers are translated afterwards anyway).
            result = cohere.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}],
                                 documents=[{"id": d.id, "data": {"title": d.title, "text": d.text}} for d, _ in ranked],
                                 temperature=0.1, max_tokens=500)
        elif engine == "local":
            result = _local(question, ranked, english)
    except (cohere.CohereUnavailable, llm.LLMUnavailable) as exc:
        out["warnings"].append(f"{engine} unavailable ({exc}); showing the sources instead.")
        engine, result = "extractive", None
    if result is None:
        engine, result = "extractive", _extractive(search_q, ranked, [h["name"] for h in hoods])
    out["engine"] = engine
    unsupported = _number_guard(result["text"], ranked, question)
    by_id = {d.id: d for d, _ in ranked}
    cited = [i for c in result["citations"] for i in c["doc_ids"]]
    order = list(dict.fromkeys(cited + [d.id for d, _ in ranked]))
    out["sources"] = [{"id": i, "title": by_id[i].title, "url": by_id[i].url, "text": by_id[i].text, "cited": i in cited}
                      for i in order if i in by_id]
    out["citations"] = result["citations"]
    if unsupported:
        out["guard"] = {"ok": False, "unsupported": unsupported}
        out["held_back"] = result["text"]
        out["answer"] = ("I couldn't match every number in the answer to the data, so it isn't shown. "
                         "The sources below have the numbers.")
        out["citations"] = []
    else:
        out["answer"] = result["text"]
        if lang and lang != "en":
            out["translated"] = _translate_answer(result["text"], lang, out)
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out


def render(answer: dict):
    """Answer text as HTML with citation markers linking to the sources list."""
    from markupsafe import Markup, escape
    text, cites = answer["answer"], sorted(answer.get("citations") or [], key=lambda c: c["start"] or 0)
    idx = {s["id"]: n + 1 for n, s in enumerate(answer["sources"])}
    out, pos = [], 0
    for c in cites:
        s, e = c.get("start"), c.get("end")
        if s is None or e is None or s < pos or e > len(text):
            continue
        marks = "".join(f'<sup><a href="#src-{idx[i]}" class="underline">{idx[i]}</a></sup>' for i in c["doc_ids"] if i in idx)
        seg = text[s:e]
        if re.fullmatch(r"\[\d+\]", seg):  # local/extractive markers: replace by links
            out.append(str(escape(text[pos:s])) + marks)
        else:  # Cohere spans: underline the cited words, then the links
            out.append(str(escape(text[pos:s])) + f'<span class="cite">{escape(seg)}</span>' + marks)
        pos = e
    out.append(str(escape(text[pos:])))
    return Markup("".join(out))
