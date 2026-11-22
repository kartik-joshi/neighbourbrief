"""Environment loading. Settings are read on every call so tests (and a changed .env) take effect."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
BULLETIN_DIR = DATA_DIR / "bulletins"
SNAPSHOT_DIR = DATA_DIR / "demo_snapshot"
CONFIG_DIR = ROOT / "config"

load_dotenv(ROOT / ".env", override=False)


def _env(name: str, default: str = "") -> str:
    val = os.environ.get(name, default) or default
    # Tolerate "value   # comment" if a dotenv version doesn't strip it.
    if " #" in val:
        val = val.split(" #", 1)[0]
    return val.strip()


def _bool(name: str, default: bool = False) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_deployment: str
    azure_openai_api_version: str
    anthropic_api_key: str
    anthropic_model: str
    translator: str
    azure_translator_key: str
    azure_translator_region: str
    azure_translator_endpoint: str
    demo_offline: bool
    llm_timeout: float
    allow_lan: bool
    public_deploy: bool = False   # public demo: resident pages for everyone, staff pages behind STAFF_PASSWORD
    staff_password: str = ""
    allowed_hosts: str = ""       # extra Host names, e.g. ".trycloudflare.com" (leading dot = any subdomain)
    cohere_api_key: str = ""
    cohere_chat_model: str = "command-a-03-2025"
    cohere_embed_model: str = "embed-multilingual-v3.0"
    cohere_rerank_model: str = "rerank-v5.0-fast"
    cohere_monthly_calls: int = 300  # this app's share of the key's monthly quota (trial keys: 1,000 calls, shared)
    allow_clients: str = ""  # extra client networks, e.g. Docker's bridge: 172.16.0.0/12
    publisher_name: str = ""     # who publishes the public site, e.g. "53 Division CPLC" (never TPS branding)
    publisher_contact: str = ""  # e.g. an email or website for questions
    require_review: bool = False
    role_author: str = "Author"
    role_reviewer: str = "Reviewer"


def get_settings() -> Settings:
    return Settings(
        llm_provider=_env("LLM_PROVIDER", "openai_compatible").lower(),
        llm_base_url=_env("LLM_BASE_URL", "http://localhost:11434/v1"),
        llm_api_key=_env("LLM_API_KEY", "ollama"),
        llm_model=_env("LLM_MODEL", "gemma3:12b"),
        azure_openai_endpoint=_env("AZURE_OPENAI_ENDPOINT"),
        azure_openai_api_key=_env("AZURE_OPENAI_API_KEY"),
        azure_openai_deployment=_env("AZURE_OPENAI_DEPLOYMENT"),
        azure_openai_api_version=_env("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        anthropic_model=_env("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        translator=_env("TRANSLATOR", "auto").lower(),
        azure_translator_key=_env("AZURE_TRANSLATOR_KEY"),
        azure_translator_region=_env("AZURE_TRANSLATOR_REGION"),
        azure_translator_endpoint=_env(
            "AZURE_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com"
        ).rstrip("/"),
        demo_offline=_bool("DEMO_OFFLINE", False),
        llm_timeout=float(_env("LLM_TIMEOUT", "240")),
        allow_lan=_bool("ALLOW_LAN", False),
        allow_clients=_env("ALLOW_CLIENTS", ""),
        public_deploy=_bool("PUBLIC_DEPLOY", False),
        staff_password=_env("STAFF_PASSWORD"),
        allowed_hosts=_env("ALLOWED_HOSTS"),
        cohere_api_key=_env("COHERE_API_KEY"),
        cohere_chat_model=_env("COHERE_CHAT_MODEL", "command-a-03-2025"),
        cohere_embed_model=_env("COHERE_EMBED_MODEL", "embed-multilingual-v3.0"),
        cohere_rerank_model=_env("COHERE_RERANK_MODEL", "rerank-v5.0-fast"),
        cohere_monthly_calls=int(_env("COHERE_MONTHLY_CALLS", "300") or 300),
        publisher_name=_env("PUBLISHER_NAME", "")[:80],
        publisher_contact=_env("PUBLISHER_CONTACT", "")[:120],
        require_review=_bool("REQUIRE_REVIEW", False),
        role_author=_env("ROLE_AUTHOR", "Author")[:30] or "Author",
        role_reviewer=_env("ROLE_REVIEWER", "Reviewer")[:30] or "Reviewer",
    )
