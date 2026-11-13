"""Pre-flight checks: is everything ready for a meeting or demo? Shown at /status."""
from __future__ import annotations

import datetime as dt
import json
import time

import httpx

from app import store
from app.ai import llm, translate
from app.config import CACHE_DIR, SNAPSHOT_DIR, get_settings
from app.data import facts as F
from app.data import tps
from app.verify import checks

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


def _item(name: str, state: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "state": state, "detail": detail, "fix": fix}


def check_tps(offline: bool) -> dict:
    if offline:
        return _item("TPS open data", INFO, "Offline demo mode: using the saved snapshot, no internet.")
    t0 = time.monotonic()
    try:
        r = httpx.get(tps.layer_url(tps.YTD_LAYER), params={"f": "json"}, timeout=6)
        r.raise_for_status()
        if r.json().get("error"):
            raise ValueError(r.json()["error"])
        ms = round((time.monotonic() - t0) * 1000)
        try:
            total = tps.layer_total(tps.YTD_LAYER)
        except Exception:
            total = None
        if total:
            best = tps.baseline(tps.YTD_LAYER, tps.period_for(tps.YTD_LAYER))
            if best and total < tps.DROP_TOLERANCE * best:
                return _item("TPS open data", WARN, f"Reachable ({ms} ms), but TPS is still reloading this year's crime "
                             f"data ({total:,} of about {best:,} records).",
                             "Neighbourhoods saved earlier keep working from their last good copy. Usually back within minutes.")
            tps.note_total(tps.YTD_LAYER, total, tps.period_for(tps.YTD_LAYER))
        if total == 0:
            return _item("TPS open data", WARN, f"Reachable ({ms} ms), but TPS is refreshing this year's crime data "
                         "right now (the layer is empty).",
                         "Neighbourhoods saved earlier keep working from their last good copy; new ones will "
                         "say \"try again in a few minutes\". Usually back within minutes.")
        return _item("TPS open data", OK, f"Reachable ({ms} ms)" + (f", {total:,} records this year." if total else "."))
    except Exception as exc:
        return _item("TPS open data", FAIL, f"Not reachable ({type(exc).__name__}).",
                     "Check the internet connection, or set DEMO_OFFLINE=true in .env and restart.")


def check_freshness(today: dt.date) -> dict:
    try:
        ms = tps.data_freshness_ms()
    except tps.TPSUnavailable as exc:
        return _item("Data freshness", FAIL, str(exc))
    if not ms:
        return _item("Data freshness", WARN, "TPS didn't report when the data was last updated.")
    fresh = F.to_toronto(ms)
    age = (today - fresh.date()).days
    last_start, _ = F.last_full_month(today)
    detail = (f"TPS data updated {fresh.strftime('%B %-d, %Y')}"
              f"{'' if age <= 0 else f' ({age} day' + ('s' if age != 1 else '') + ' ago)'}. "
              f"Latest complete month: {last_start.strftime('%B %Y')}.")
    if age > 3:
        return _item("Data freshness", WARN, detail, "Numbers may be behind; mention the date when presenting.")
    return _item("Data freshness", OK, detail)


def check_writer() -> dict:
    st = llm.status(max_age=0)
    if st["ok"]:
        return _item("AI writer", OK, f"{st['label']} is ready.")
    return _item("AI writer", WARN, f"{st['label']} isn't available ({st['detail'] or 'no response'}). "
                 "Drafts will use the template writer.",
                 "Start Ollama (`ollama serve`) and check `ollama list` shows the model.")


def check_translator(offline: bool) -> dict:
    engine, note = translate.engine_for()
    s = get_settings()
    label = translate.engine_label(engine)
    if engine == "azure":
        return _item("Translation", OK, f"{label} (key set, region {s.azure_translator_region or 'not set'}).")
    detail = f"{label}." + (f" {note}" if note else "")
    if not llm.status()["ok"]:
        return _item("Translation", FAIL, detail + " The AI model is also unavailable, so nothing can be translated.",
                     "Start Ollama, or add AZURE_TRANSLATOR_KEY to .env.")
    return _item("Translation", OK if offline or not s.azure_translator_key else INFO,
                 detail + " About 35–60 s per language on the local model.",
                 "" if s.azure_translator_key else "Optional: add AZURE_TRANSLATOR_KEY to .env for faster, stronger translation.")


def check_snapshot(today: dt.date, offline: bool) -> dict:
    meta_path = SNAPSHOT_DIR / "snapshot_meta.json"
    if not meta_path.exists():
        return _item("Offline snapshot", FAIL if offline else WARN, "No offline snapshot saved.",
                     "Use \"Save for offline\" below for the neighbourhoods you'll show.")
    meta = json.loads(meta_path.read_text())
    date = dt.date.fromisoformat(meta["snapshot_date"])
    age = (today - date).days
    hoods = meta.get("neighbourhoods", [])
    detail = (f"Saved {date.strftime('%B %-d, %Y')} for {len(hoods)} neighbourhood"
              f"{'s' if len(hoods) != 1 else ''}: {', '.join(hoods)}.")
    if age > 2 and not offline:
        return _item("Offline snapshot", WARN, detail + f" It's {age} days old.",
                     "Refresh before the meeting: \"Save for offline\" below (keeps the demo bulletins as they are).")
    return _item("Offline snapshot", OK, detail)


def check_bulletins() -> dict:
    recs = store.list_all()
    if not recs:
        return _item("Saved bulletins", INFO, "No bulletins yet.")
    published = [r for r in recs if r.get("status") == "published"]
    verified = [r for r in published if not checks.publish_blocked(r)]
    demo = [r for r in recs if r.get("demo")]
    detail = (f"{len(recs)} saved, {len(published)} published ({len(verified)} fully verified)"
              + (f", {len(demo)} pre-generated demo bulletin{'s' if len(demo) != 1 else ''}" if demo else "")
              + ".")
    if published and len(verified) < len(published):
        return _item("Saved bulletins", WARN, detail, "Some published bulletins were published with a manual review.")
    return _item("Saved bulletins", OK, detail)


def check_cache() -> dict:
    files = list(CACHE_DIR.glob("*.json"))
    size = sum(f.stat().st_size for f in files)
    return _item("Data cache", INFO, f"{len(files)} cached responses, {size / 1_000_000:.1f} MB (kept 12 h).")


def check_cohere() -> dict:
    """Optional: Cohere for Ask (embed, rerank, grounded chat with citations). No network call here."""
    from app.ai import cohere
    st = cohere.status()
    return _item("Cohere (Ask)", OK if st["ok"] else INFO, st["detail"],
                 "" if st["ok"] else "Optional. Ask still works with the local model or sources only.")


def run_all() -> dict:
    s = get_settings()
    today = F.effective_today()
    items = [
        _item("Mode", INFO, "Offline demo mode (no internet used)." if s.demo_offline
              else "Online: live TPS open data.", "" if s.demo_offline else
              "If the Wi-Fi is bad: set DEMO_OFFLINE=true in .env and restart."),
        check_tps(s.demo_offline),
        check_freshness(today),
        check_writer(),
        check_translator(s.demo_offline),
        check_cohere(),
        check_snapshot(today, s.demo_offline),
        check_bulletins(),
        check_cache(),
        _item("Access", WARN if s.allow_lan else OK,
              "Other devices on the network can open the app (ALLOW_LAN=true)." if s.allow_lan
              else "This computer only. Other devices on the network are refused.",
              "Set ALLOW_LAN=false in .env unless you really need it." if s.allow_lan else ""),
    ]
    worst = FAIL if any(i["state"] == FAIL for i in items) else WARN if any(i["state"] == WARN for i in items) else OK
    return {"overall": worst, "items": items, "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
