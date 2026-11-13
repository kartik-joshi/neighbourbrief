"""Provider-agnostic LLM client: one `complete(system, user, json=False) -> str`.

Providers (chosen by LLM_PROVIDER in .env):
  openai_compatible  any OpenAI-compatible endpoint (default: local Ollama at :11434/v1) : tested
  azure_openai       Azure OpenAI deployment                                              : untested
  anthropic          Anthropic Messages API                                              : untested
  cohere             Cohere v2 chat (COHERE_API_KEY, COHERE_CHAT_MODEL)                  : unit-tested
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """The model can't be reached / isn't configured. Callers fall back (template writer, etc.)."""


def _is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def provider_label() -> str:
    s = get_settings()
    if s.llm_provider == "azure_openai":
        return f"Azure OpenAI ({s.azure_openai_deployment or 'no deployment set'})"
    if s.llm_provider == "anthropic":
        return f"Anthropic ({s.anthropic_model})"
    if s.llm_provider == "cohere":
        return f"Cohere ({s.cohere_chat_model})"
    where = "local" if _is_local(s.llm_base_url) else urlparse(s.llm_base_url).hostname
    return f"{s.llm_model} ({where})"


def complete(system: str, user: str, json: bool = False, temperature: float = 0.2,
             max_tokens: int = 2000) -> str:
    s = get_settings()
    prov = s.llm_provider
    if s.demo_offline and not (prov == "openai_compatible" and _is_local(s.llm_base_url)):
        raise LLMUnavailable("Offline demo mode: only a local model can be used.")
    t0 = time.monotonic()
    try:
        if prov == "openai_compatible":
            out = _openai_compatible(s, system, user, json, temperature, max_tokens)
        elif prov == "azure_openai":
            out = _azure_openai(s, system, user, json, temperature, max_tokens)
        elif prov == "anthropic":
            out = _anthropic(s, system, user, json, temperature, max_tokens)
        elif prov == "cohere":
            from app.ai import cohere
            try:
                out = cohere.chat([{"role": "system", "content": system}, {"role": "user", "content": user}],
                                  json_mode=json, temperature=temperature, max_tokens=max_tokens)["text"]
            except cohere.CohereUnavailable as exc:
                raise LLMUnavailable(str(exc)) from exc
        else:
            raise LLMUnavailable(f"Unknown LLM_PROVIDER {prov!r}")
    except LLMUnavailable:
        raise
    except Exception as exc:  # network, auth, timeouts, SDK errors -> caller falls back
        name = type(exc).__name__
        raise LLMUnavailable(f"{provider_label()} is unavailable ({name}: {str(exc)[:200]})") from exc
    log.info("LLM %s answered in %.1fs", provider_label(), time.monotonic() - t0)
    return out or ""


def _openai_compatible(s, system, user, json, temperature, max_tokens) -> str:
    from openai import OpenAI

    client = OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key or "none",
                    timeout=s.llm_timeout, max_retries=0)
    kwargs = {}
    if json:
        kwargs["response_format"] = {"type": "json_object"}
    resp = client.chat.completions.create(
        model=s.llm_model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    return resp.choices[0].message.content


def _azure_openai(s, system, user, json, temperature, max_tokens) -> str:
    from openai import AzureOpenAI

    if not (s.azure_openai_endpoint and s.azure_openai_api_key and s.azure_openai_deployment):
        raise LLMUnavailable("Azure OpenAI is selected but AZURE_OPENAI_ENDPOINT / _API_KEY / "
                             "_DEPLOYMENT are not all set in .env.")
    client = AzureOpenAI(azure_endpoint=s.azure_openai_endpoint, api_key=s.azure_openai_api_key,
                         api_version=s.azure_openai_api_version, timeout=s.llm_timeout, max_retries=1)
    kwargs = {"response_format": {"type": "json_object"}} if json else {}
    resp = client.chat.completions.create(
        model=s.azure_openai_deployment,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
        **kwargs,
    )
    return resp.choices[0].message.content


def _anthropic(s, system, user, json, temperature, max_tokens) -> str:
    if not s.anthropic_api_key:
        raise LLMUnavailable("Anthropic is selected but ANTHROPIC_API_KEY is not set in .env.")
    if json:
        system += "\n\nRespond with a single JSON object only. No prose before or after it."
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": s.anthropic_model, "max_tokens": max_tokens, "temperature": temperature,
              "system": system, "messages": [{"role": "user", "content": user}]},
        timeout=s.llm_timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


_status_cache: dict = {"t": 0.0, "value": None}


def status(max_age: float = 20.0) -> dict:
    """Cheap availability check for the UI. {"ok": bool, "label": str, "detail": str}."""
    now = time.monotonic()
    if _status_cache["value"] is not None and now - _status_cache["t"] < max_age:
        return _status_cache["value"]
    s = get_settings()
    value = {"ok": False, "label": provider_label(), "detail": ""}
    try:
        if s.llm_provider == "openai_compatible":
            if s.demo_offline and not _is_local(s.llm_base_url):
                raise LLMUnavailable("offline mode")
            r = httpx.get(s.llm_base_url.rstrip("/") + "/models",
                          headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=3)
            r.raise_for_status()
            ids = {m.get("id") for m in r.json().get("data", [])}
            if s.llm_model not in ids:
                raise LLMUnavailable(f"model {s.llm_model} not found (have: {', '.join(sorted(i for i in ids if i))})")
            value["ok"] = True
        elif s.llm_provider == "azure_openai":
            value["ok"] = bool(s.azure_openai_endpoint and s.azure_openai_api_key and s.azure_openai_deployment)
            value["detail"] = "" if value["ok"] else "Azure OpenAI settings missing in .env"
        elif s.llm_provider == "anthropic":
            value["ok"] = bool(s.anthropic_api_key)
            value["detail"] = "" if value["ok"] else "ANTHROPIC_API_KEY missing in .env"
        elif s.llm_provider == "cohere":
            value["ok"] = bool(s.cohere_api_key) and not s.demo_offline
            value["detail"] = "" if value["ok"] else ("offline mode" if s.cohere_api_key else "COHERE_API_KEY missing in .env")
    except Exception as exc:
        value["detail"] = str(exc)[:200]
    _status_cache.update(t=now, value=value)
    return value
