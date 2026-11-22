"""Cohere v2 API: chat with documents (grounded answers with citations), embed and rerank.

Only plain httpx, like the other providers. Nothing is sent when COHERE_API_KEY is empty or offline
mode is on: callers check `configured()` and fall back (local model, lexical search).
API shapes checked against docs.cohere.com (v2 /chat, /embed, /rerank) on Oct 23, 2026.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime

import httpx

from app.config import CACHE_DIR, get_settings

log = logging.getLogger(__name__)
BASE = "https://api.cohere.com/v2"
EMBED_BATCH = 96  # the API's limit per call


class CohereUnavailable(RuntimeError):
    """Not configured, offline, or the API call failed. Callers fall back."""


# Monthly budget: the key may be shared with other projects (a trial key has 1,000 calls a month in all),
# so this app stops at COHERE_MONTHLY_CALLS and falls back to the local model or sources-only answers.
USAGE_FILE = CACHE_DIR / "cohere_usage.json"
_usage_lock = threading.Lock()


def usage() -> dict:
    """{"month": "YYYY-MM", "calls": n} for the current month (every request sent, retries included)."""
    month = datetime.now().strftime("%Y-%m")
    try:
        data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if data.get("month") == month else {"month": month, "calls": 0}


def _spend_one_call() -> None:
    limit = get_settings().cohere_monthly_calls
    with _usage_lock:
        u = usage()
        if u["calls"] >= limit:
            raise CohereUnavailable(f"This month's Cohere budget is used ({u['calls']}/{limit} calls; COHERE_MONTHLY_CALLS in .env).")
        u["calls"] += 1
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = USAGE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(u), encoding="utf-8")
        tmp.replace(USAGE_FILE)


def configured() -> bool:
    s = get_settings()
    return bool(s.cohere_api_key) and not s.demo_offline


def label() -> str:
    s = get_settings()
    return f"Cohere ({s.cohere_chat_model})"


def _post(path: str, body: dict, timeout: float = 60.0) -> dict:
    s = get_settings()
    if not s.cohere_api_key:
        raise CohereUnavailable("COHERE_API_KEY is not set in .env.")
    if s.demo_offline:
        raise CohereUnavailable("Offline mode: Cohere is not used.")
    headers = {"Authorization": f"Bearer {s.cohere_api_key}", "Content-Type": "application/json",
               "Accept": "application/json", "X-Client-Name": "neighbourbrief-to"}
    for attempt in range(3):
        _spend_one_call()
        try:
            r = httpx.post(f"{BASE}/{path}", json=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise CohereUnavailable(f"Cohere is unreachable ({type(exc).__name__}).") from exc
        if r.status_code == 429 and attempt < 2:  # rate limited (trial keys): wait as asked, then retry
            try:
                wait = min(float(r.headers.get("Retry-After", "")), 20.0)
            except ValueError:
                wait = 3.0 * (attempt + 1)
            log.info("Cohere rate-limited; retrying in %.0f s", wait)
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            detail = ""
            try:
                detail = (r.json() or {}).get("message", "")
            except ValueError:
                pass
            raise CohereUnavailable(f"Cohere returned {r.status_code}{': ' + detail[:200] if detail else ''}.")
        return r.json()
    raise CohereUnavailable("Cohere kept rate-limiting the request.")


def chat(messages: list[dict], documents: list[dict] | None = None, json_mode: bool = False,
         temperature: float = 0.2, max_tokens: int = 900, model: str | None = None) -> dict:
    """{"text", "citations": [{"start", "end", "text", "doc_ids"}], "id", "usage"}.

    `documents`: [{"id": str, "data": {"title": ..., "text": ...}}]; answers cite them by id."""
    body: dict = {"model": model or get_settings().cohere_chat_model, "messages": messages,
                  "temperature": temperature, "max_tokens": max_tokens}
    if documents:
        body["documents"] = documents
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    data = _post("chat", body)
    msg = data.get("message") or {}
    text = "".join(c.get("text", "") for c in msg.get("content") or [] if c.get("type") == "text")
    cites = []
    for c in msg.get("citations") or []:
        ids = [s.get("id") or (s.get("document") or {}).get("id") for s in c.get("sources") or [] if s.get("type", "document") == "document"]
        cites.append({"start": c.get("start"), "end": c.get("end"), "text": c.get("text", ""), "doc_ids": [i for i in ids if i]})
    return {"text": text, "citations": cites, "id": data.get("id"), "usage": data.get("usage") or {}}


def embed(texts: list[str], input_type: str, model: str | None = None) -> list[list[float]]:
    """Float embeddings, batched by the API limit. input_type: search_document or search_query."""
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        data = _post("embed", {"model": model or get_settings().cohere_embed_model, "texts": texts[i:i + EMBED_BATCH],
                               "input_type": input_type, "embedding_types": ["float"]})
        vecs = (data.get("embeddings") or {}).get("float") or []
        if len(vecs) != len(texts[i:i + EMBED_BATCH]):
            raise CohereUnavailable("Cohere returned the wrong number of embeddings.")
        out.extend(vecs)
    return out


def rerank(query: str, documents: list[str], top_n: int, model: str | None = None) -> list[tuple[int, float]]:
    """[(index into documents, relevance 0..1)], best first."""
    if not documents:
        return []
    data = _post("rerank", {"model": model or get_settings().cohere_rerank_model, "query": query,
                            "documents": documents, "top_n": min(top_n, len(documents))})
    return [(int(r["index"]), float(r.get("relevance_score", 0))) for r in data.get("results") or []]


def status() -> dict:
    """For the Status page: configured or not (no network call)."""
    s = get_settings()
    if not s.cohere_api_key:
        return {"ok": False, "detail": "Not set up (add COHERE_API_KEY to .env to use Cohere)."}
    if s.demo_offline:
        return {"ok": False, "detail": "Key set; not used in offline mode."}
    u = usage()
    return {"ok": u["calls"] < s.cohere_monthly_calls,
            "detail": f"Key set: chat {s.cohere_chat_model}, embed {s.cohere_embed_model}, rerank {s.cohere_rerank_model}. "
                      f"This month: {u['calls']}/{s.cohere_monthly_calls} calls."}
