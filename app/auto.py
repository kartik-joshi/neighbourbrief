"""Automatic bulletins: a checked monthly bulletin for every one of the 158 neighbourhoods, written
from the open data alone (the no-AI template writer, no officer note), in English and the
neighbourhood's top home languages.

Officers' bulletins stay the main product; an automatic one fills the gap where nobody has written
one yet, so every resident who looks up their neighbourhood gets a plain-language page. Same numbers,
same checks, same publish gate: a language is shown only if every check passes. Nothing is stored
in data/bulletins/; translations are cached in data/cache/auto/ (one small file per neighbourhood and
month) so each is made once.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading

from app.ai import translate, writer
from app.config import CACHE_DIR
from app.data import facts as F
from app.languages import suggested_languages
from app.verify import checks

log = logging.getLogger(__name__)

AUTO_DIR = CACHE_DIR / "auto"
_ID_RE = re.compile(r"^auto-(\d{3})-(\d{4}-\d{2})$")
_lock = threading.RLock()
CLOSING = "Questions? Contact your local police division, listed below. In an emergency, call 911."
CLOSING_NCO = "Questions? Talk to your Neighbourhood Community Officers, listed below. In an emergency, call 911."


def is_auto(bid: str) -> bool:
    return bool(_ID_RE.match(bid or ""))


def auto_id(hood_id: str, month_start: str) -> str:
    return f"auto-{hood_id}-{month_start[:7]}"


def _english(facts: dict) -> dict:
    doc = writer.template_draft(facts, {})
    doc["closing"] = CLOSING_NCO if (facts.get("nco") or {}).get("has_nco") else CLOSING
    doc["followup"] = ""
    return doc


def _hash(doc: dict) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def _cache_path(bid: str):
    return AUTO_DIR / f"{bid}.json"


def _load_cache(bid: str, english_hash: str) -> dict:
    try:
        data = json.loads(_cache_path(bid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("translations", {}) if data.get("english") == english_hash else {}


def _save_cache(bid: str, english_hash: str, translations: dict) -> None:
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _cache_path(bid).with_suffix(".tmp")
    tmp.write_text(json.dumps({"english": english_hash, "translations": translations}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(_cache_path(bid))


def can_translate_now() -> bool:
    """Translate on request only with Azure (about a second); the local model takes a minute or
    more per language, too slow for a page view. Offline, only cached translations are shown."""
    return translate.engine_for()[0] == "azure"


def languages_for(facts: dict) -> list[str]:
    """The neighbourhood's top home languages (2021 Census), then French, Canada's other official language."""
    n = facts["neighbourhood"]
    codes = [l["code"] for l in suggested_languages(n["name"], n["id"]) if l["code"] in translate.LANGUAGES]
    return codes + ["fr"]


def build(hood: str, month: str | None = None, translate_missing: bool | None = None) -> dict:
    """A bulletin record (same shape as a saved one, status "automatic") for a neighbourhood id or
    name and month (default: the latest complete month). Raises like F.build_facts."""
    facts = F.build_facts(hood, month=month)
    facts["allowed_numbers"] = F.allowed_numbers(facts, {})
    english = _english(facts)
    bid = auto_id(facts["neighbourhood"]["id"], facts["period"]["month_start"])
    wanted = languages_for(facts)
    h = _hash(english)
    with _lock:
        translations = _load_cache(bid, h)
        from app import tm
        for c in wanted:  # memory first: no call, and lines a translator approved replace machine ones
            full = tm.apply(english, c, facts["neighbourhood"]["name"])
            if full:
                doc, mem = full
                entry = {"doc": doc, "engine": "memory", "engine_label": "Translation memory",
                         "memory": {k: v for k, v in mem.items() if k != "hits"}}
                from app.verify import meaning
                composed = tm.back_doc(english, mem["hits"])
                entry["back"] = {"doc": composed, "for": meaning.doc_hash(doc)} if composed else None
                if mem["all_reviewed"]:
                    entry.update(human_reviewed=True, reviewed_by="translation memory")
                translations[c] = entry
        todo = [c for c in wanted if c not in translations]  # failed ones are remembered, not retried
        if todo and (can_translate_now() if translate_missing is None else translate_missing):
            for code in todo:
                try:
                    # Azure only: a page view or site build can't wait a minute per language for the local model
                    doc, info = translate.translate_doc(english, code, names=[facts["neighbourhood"]["name"]],
                                                        llm_fallback=not can_translate_now())
                    from app.verify import meaning
                    back = translate.back_for(doc, code, info)
                    translations[code] = {"doc": doc, **{k: v for k, v in info.items() if k != "memory_back"},
                                          "back": {"doc": back, "for": meaning.doc_hash(doc)} if back else None}
                except Exception as exc:  # a failed language is left out, never shown half-done
                    log.warning("Automatic bulletin %s: %s translation failed: %s", bid, code, exc)
                    if isinstance(exc, translate.TranslationFailed):  # a network blip is retried next time
                        translations[code] = {"doc": None, "error": str(exc)[:200]}
    rec = {
        "id": bid, "auto": True, "status": "automatic", "facts": facts, "officer": {}, "english": english,
        "languages": [c for c in wanted if (translations.get(c) or {}).get("doc") and not translations[c].get("failed_checks")],
        "translations": translations, "services_shown": [s["id"] for s in facts.get("services", [])[:F.SERVICES_SHOWN]],
        "signoff": "", "manual_override": False, "draft_source": "template",
    }
    checks.verify_record(rec)
    # the publish gate: a language that fails any check is left out (English failing means no bulletin).
    # Stricter than an officer's bulletin: no person reviews an automatic one, so a translation the
    # guard rails flag for a human look (app/verify/quality.py) is left out too.
    failed = [c for c in rec["languages"] if not rec["verification"][c]["ok"]
              or any(f.get("strong", True) for f in (rec["verification"][c].get("quality") or {}).get("flags") or [])]
    for c in failed:
        v = rec["verification"][c]
        why = v.get("summary") if not v["ok"] else "flagged for a human look: " + "; ".join(f["text"] for f in v["quality"]["flags"] if f.get("strong", True))
        log.warning("Automatic bulletin %s: %s left out (%s)", bid, c, why)
        translations[c] = {**translations[c], "failed_checks": True}
    if (todo and translations) or failed:
        with _lock:
            _save_cache(bid, h, translations)
    rec["languages"] = [c for c in rec["languages"] if c not in failed]
    rec["untried"] = [c for c in wanted if c not in translations]
    checks.verify_record(rec)
    for c in rec["languages"]:
        tm.learn(rec, c)  # standard lines that passed every check become machine entries
    return rec


def from_id(bid: str, translate_missing: bool | None = None) -> dict:
    m = _ID_RE.match(bid or "")
    if not m:
        raise KeyError(bid)
    rec = build(m.group(1), month=m.group(2), translate_missing=translate_missing)
    if rec["id"] != bid:
        raise KeyError(bid)
    return rec


def showable(rec: dict) -> bool:
    return bool(rec["verification"]["en"]["ok"]) and not checks.content_blockers(rec)
