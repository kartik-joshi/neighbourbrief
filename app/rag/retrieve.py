"""Ranking documents for a question: Cohere embeddings + rerank when configured, otherwise BM25.

Embeddings of documents are cached on disk by text hash and model, so only new text is sent."""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from collections import Counter

from app.ai import cohere
from app.config import CACHE_DIR, get_settings
from app.rag.corpus import Doc

EMBED_CACHE = CACHE_DIR / "rag_embeddings.json"
_STOP = set("the a an of in on at to for and or is are was were be been how many much what which who when where why did does do this that "
            "there their it its by with from so far as than about".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.lower().replace("’", "'")) if t not in _STOP]


# Words people (and machine translation) use for the same thing as the documents' words.
SYNONYMS = {"attack": "assault", "attacks": "assault", "assault": "assault", "burglary": "break", "burglaries": "break",
            "burglar": "break", "housebreaking": "break", "car": "vehicle", "cars": "vehicle", "auto": "vehicle",
            "crash": "collision", "crashes": "collision", "accident": "collision", "accidents": "collision",
            "murder": "homicide", "murders": "homicide", "killings": "homicide", "gunfire": "shooting",
            "officer": "officer", "cops": "officer", "police": "police", "language": "language", "languages": "language"}


def expand(words: set[str]) -> set[str]:
    """Query words plus the documents' word for the same thing."""
    return {_stem(w) for w in words} | {_stem(SYNONYMS[w]) for w in words if w in SYNONYMS}


def _stem(t: str) -> str:
    """Crude English suffix stripping: involved/involving/involves → involv, assaults → assault."""
    for suf in ("ing", "ed", "es", "s"):
        if len(t) > len(suf) + 3 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def bm25(query: str, docs: list[Doc], k1: float = 1.4, b: float = 0.7) -> list[tuple[int, float]]:
    raw = tokens(query)
    q = sorted(expand(set(raw)) | {_stem(t) for t in raw})
    toks = [[_stem(t) for t in tokens(d.title + " " + d.text)] for d in docs]
    if not docs or not q:
        return [(i, 0.0) for i in range(len(docs))]
    avg = sum(len(t) for t in toks) / len(toks)
    df = Counter(w for t in toks for w in set(t))
    out = []
    for i, t in enumerate(toks):
        tf = Counter(t)
        score = 0.0
        for w in q:
            if w in tf:
                idf = math.log(1 + (len(docs) - df[w] + 0.5) / (df[w] + 0.5))
                score += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * len(t) / avg))
        out.append((i, score))
    return sorted(out, key=lambda x: -x[1])


EMBED_CACHE_MAX = 4000
_cache_lock = threading.Lock()


def _load_cache() -> dict:
    try:
        return json.loads(EMBED_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def doc_embeddings(docs: list[Doc]) -> list[list[float]]:
    model = get_settings().cohere_embed_model
    keys = [hashlib.sha1(f"{model}|{d.title}|{d.text}".encode()).hexdigest() for d in docs]
    with _cache_lock:
        cache = _load_cache()
        missing = [i for i, k in enumerate(keys) if k not in cache]
        if missing:
            vecs = cohere.embed([f"{docs[i].title}. {docs[i].text}" for i in missing], "search_document", model=model)
            for i, v in zip(missing, vecs):
                cache[keys[i]] = v
            # the counts change as the data updates, so old texts' vectors pile up: keep the newest ones
            cache = dict(list(cache.items())[-EMBED_CACHE_MAX:])
            EMBED_CACHE.parent.mkdir(parents=True, exist_ok=True)
            tmp = EMBED_CACHE.with_suffix(".tmp")
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            tmp.replace(EMBED_CACHE)
        return [cache[k] for k in keys]


def _cos(a, b) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def rank(query: str, docs: list[Doc], k: int = 6) -> tuple[list[tuple[Doc, float]], str]:
    """Top k documents and the method used ("cohere" or "bm25")."""
    if not docs:
        return [], "none"
    if cohere.configured():
        try:
            pool = list(range(len(docs)))
            if len(docs) > 24:  # embed to shortlist, then rerank the shortlist
                qv = cohere.embed([query], "search_query", model=get_settings().cohere_embed_model)[0]
                dv = doc_embeddings(docs)
                pool = sorted(pool, key=lambda i: -_cos(qv, dv[i]))[:24]
            ranked = cohere.rerank(query, [f"{docs[i].title}. {docs[i].text}" for i in pool], top_n=k)
            return [(docs[pool[i]], s) for i, s in ranked], "cohere"
        except cohere.CohereUnavailable:
            pass  # fall back to lexical ranking
    # Lexical ranking can't tell "how many last month" (the summary) from "month by month" (the table):
    # a small prior for the summary documents, and older bulletins count half.
    prior = {"crime": 1.25, "road": 1.15, "monthly": 0.9, "trend": 0.9}
    scored = [(i, s * prior.get(docs[i].kind, 1.0) * (0.5 if docs[i].meta.get("older") else 1.0)) for i, s in bm25(query, docs)]
    if not any(s > 0 for _, s in scored):
        # Nothing matched by keyword (a question written fully in Urdu, Chinese, ... can't match English
        # documents): use the chosen neighbourhood's summaries, most useful first.
        order = {"crime": 0, "road": 1, "monthly": 2, "languages": 3, "help": 4, "trend": 5, "bulletin": 6}
        hood = sorted((i for i, d in enumerate(docs) if d.hood_id), key=lambda i: order.get(docs[i].kind, 9))
        if hood:
            return [(docs[i], 0.0) for i in hood[:k]], "bm25 (no keyword match: neighbourhood summaries)"
    scored.sort(key=lambda x: -x[1])
    return [(docs[i], s) for i, s in scored[:k]], "bm25"
