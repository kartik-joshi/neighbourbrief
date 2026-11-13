"""Meaning score per line, without translating back: the English line and its translation are compared
directly with a multilingual sentence-embedding model (bge-m3 by BAAI, MIT licence, through the local
Ollama; 100+ languages incl. every NeighbourBrief language). Close meaning means close vectors. This is a
light form of what translation teams call quality estimation; Google uses LaBSE the same way to filter
translated text.

Runs on this computer, so it also checks translations made offline by the local model (which have no
back-translation). If the model isn't installed or Ollama isn't running, it's skipped and the
back-translation checks still apply. Thresholds are set per language from real translations
(config/similarity_thresholds.json, written by scripts/calibrate_similarity.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading

import httpx

from app.config import CONFIG_DIR, get_settings

log = logging.getLogger(__name__)
MODEL = "bge-m3"
THRESHOLDS_FILE = CONFIG_DIR / "similarity_thresholds.json"
_cache: dict[str, list[float]] = {}
_lock = threading.Lock()
_down_until = 0.0


def _base() -> str:
    url = get_settings().llm_base_url.rstrip("/")
    return url[:-3] if url.endswith("/v1") else url


def embed(texts: list[str]) -> list[list[float]] | None:
    """Vectors for `texts`, or None when the model isn't available (never raises)."""
    import time
    global _down_until
    if not texts:
        return []
    if time.time() < _down_until:
        return None
    keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
    todo = [t for t, k in zip(texts, keys) if k not in _cache]
    if todo:
        try:
            r = httpx.post(f"{_base()}/api/embed", json={"model": MODEL, "input": todo}, timeout=60)
            r.raise_for_status()
            vecs = r.json()["embeddings"]
        except Exception as exc:  # not installed / not running: skip for a minute, don't slow every page
            log.info("Meaning score unavailable (%s): %s", MODEL, exc)
            _down_until = time.time() + 60
            return None
        with _lock:
            for t, v in zip(todo, vecs):
                _cache[hashlib.sha1(t.encode()).hexdigest()] = v
            if len(_cache) > 50000:
                _cache.clear()
    return [_cache[k] for k in keys]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b) or 1.0)


def thresholds() -> dict:
    try:
        return json.loads(THRESHOLDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def line_scores(en: dict, tr: dict) -> list[dict] | None:
    """[{"field", "index", "score", "en", "tr"}] for each line with text in both; None if unavailable."""
    pairs = []
    for k in ("headline", "summary", "followup", "officer_section", "closing"):
        if en.get(k) and tr.get(k):
            pairs.append((k, None, en[k], tr[k]))
    for i, (e, t) in enumerate(zip(en.get("bullets") or [], tr.get("bullets") or [])):
        if e and t:
            pairs.append(("bullets", i, e, t))
    if not pairs:
        return []
    vecs = embed([p[2] for p in pairs] + [p[3] for p in pairs])
    if vecs is None:
        return None
    n = len(pairs)
    return [{"field": f, "index": i, "score": round(cosine(vecs[j], vecs[n + j]), 4), "en": e, "tr": t}
            for j, (f, i, e, t) in enumerate(pairs)]
