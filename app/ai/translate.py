"""Translation: Azure Translator (if a key is set) or the LLM, with number protection and a
script check (retry once, then flag ⚠️ and suggest Azure)."""
from __future__ import annotations

import html
import json
import logging
import re
import time

import httpx

from app.ai import llm
from app.ai.writer import FIELDS, extract_json, normalize_doc
from app.config import get_settings
from app.data.tps import cached
from app.languages import LANGUAGES, lang_info
from app.verify import numbers as N
from app.verify import script as S

log = logging.getLogger(__name__)

TOKEN_RE = re.compile(r"[⟦\[]{1,2}\s*N\s*(\d+)\s*[⟧\]]{1,2}")
NAME_RE = re.compile(r"[⟦\[]{1,2}\s*P\s*(\d+)\s*[⟧\]]{1,2}")


class TranslationFailed(RuntimeError):
    pass


# ---------------------------------------------------------------- engine choice

def engine_for() -> tuple[str, str]:
    """Return (engine, note). engine is 'azure' or 'llm'."""
    s = get_settings()
    has_key = bool(s.azure_translator_key)
    if s.translator == "llm":
        return "llm", ""
    if s.translator == "azure" and not has_key:
        return "llm", "TRANSLATOR=azure but AZURE_TRANSLATOR_KEY is empty, so the AI model is used instead."
    if has_key and s.demo_offline:
        return "llm", "Offline demo mode: using the local AI model instead of Azure Translator."
    if has_key:
        return "azure", ""
    return "llm", ""


def engine_label(engine: str) -> str:
    return "Azure Translator" if engine == "azure" else f"AI model ({llm.provider_label()})"


def azure_supported_codes() -> set[str] | None:
    """Codes Azure Translator supports (public endpoint, no key). None if unknown (offline)."""
    s = get_settings()
    if s.demo_offline:
        return None

    def fetch():
        r = httpx.get(f"{s.azure_translator_endpoint}/languages",
                      params={"api-version": "3.0", "scope": "translation"}, timeout=10)
        r.raise_for_status()
        return sorted(r.json().get("translation", {}).keys())

    try:
        return {c.lower() for c in cached("azure_languages", fetch)}
    except Exception as exc:  # unreachable: don't block the app
        log.warning("Couldn't load Azure language list: %s", exc)
        return None


def language_choices() -> list[dict]:
    supported = azure_supported_codes()
    out = []
    for code, info in LANGUAGES.items():
        if code == "en":
            continue
        ok = supported is None or code.lower() in supported
        out.append({"code": code, "name": info["name"], "native": info["native"], "enabled": ok})
    return out


# ---------------------------------------------------------------- number protection

def protect(doc: dict, names: list[str] | None = None) -> tuple[dict, list[str]]:
    """Replace every number with ⟦N0⟧, ⟦N1⟧ … and each proper name in `names` with ⟦P0⟧ … ;
    return (protected_doc, originals). Names come back in their original Latin spelling."""
    originals: list[str] = []
    names = [n for n in (names or []) if n]

    def sub(text: str) -> str:
        # Numbers first (names never contain digits here), then names: so the digit inside a
        # ⟦P0⟧ token is never mistaken for a number.
        text = _sub_numbers(text)
        for i, name in enumerate(names):
            text = re.sub(re.escape(name), f"⟦P{i}⟧", text, flags=re.I)
        return text

    def _sub_numbers(text: str) -> str:
        def rep(m):
            originals.append(text[m.start():m.end()])
            return f"⟦N{len(originals) - 1}⟧"
        return N._NUM_RE.sub(rep, N.normalize_digits(text))

    out = {}
    for k in FIELDS:
        v = doc.get(k)
        if k == "followup" and not v:
            continue  # optional field: never send an empty one (a model might invent text for it)
        out[k] = [sub(x) for x in v] if isinstance(v, list) else sub(v or "")
    return out, originals


def restore(doc: dict, originals: list[str], names: list[str] | None = None) -> tuple[dict, list[int], list[int]]:
    """Put original digits (and names) back. Returns (doc, missing_token_ids, duplicated_token_ids)."""
    seen: dict[int, int] = {}
    names = [n for n in (names or []) if n]

    def sub(text: str) -> str:
        def rep(m):
            i = int(m.group(1))
            seen[i] = seen.get(i, 0) + 1
            return originals[i] if 0 <= i < len(originals) else m.group(0)
        text = NAME_RE.sub(lambda m: names[int(m.group(1))] if int(m.group(1)) < len(names) else m.group(0), text)
        return TOKEN_RE.sub(rep, text)

    out = {}
    for k in FIELDS:
        if k == "followup" and k not in doc:
            continue  # optional field that wasn't sent (normalize_doc fills it in)
        v = doc.get(k)
        out[k] = [sub(x) for x in v] if isinstance(v, list) else sub(v or "")
    missing = [i for i in range(len(originals)) if i not in seen]
    dup = [i for i, n in seen.items() if n > 1]
    return out, missing, dup


# ---------------------------------------------------------------- LLM engine

LANG_GUIDANCE = {
    "ur": ("Write standard Urdu (اردو), the national language of Pakistan, in Urdu letters "
           "(for example ٹ ڈ ڑ ں ے ھ). Do NOT write Sindhi, Punjabi, Hindi or any Devanagari letters: "
           "write ہوئیں / ہوئی / ہے / ہیں, never हुईं / हुई / है / हैं."),
    "ps": "Write standard Pashto (پښتو) in Pashto letters (for example ټ ډ ړ ښ ږ ځ څ ګ ڼ). Do not write Urdu or Dari.",
    "ar": "Write Modern Standard Arabic in Arabic letters only. Do not use Persian or Urdu letters such as پ چ گ ی.",
    "fa": "Write standard Persian (Farsi) in Persian letters. Do not write Urdu or Arabic.",
    "zh-Hant": "Write Traditional Chinese characters as used in Hong Kong. No pinyin, no Simplified characters. Write percentages with the % sign (e.g. 11%), never 厘 or 成.",
    "zh-Hans": "Write Simplified Chinese characters. No pinyin. Write percentages with the % sign (e.g. 11%), never 厘 or 成.",
    "ta": "Write Tamil in Tamil script only. No romanized Tamil.",
    "ko": "Write Korean in Hangul. No romanization.",
    "ru": "Write Russian in Cyrillic letters.",
}


def _system_prompt(code: str, glossary: dict[str, str] | None = None) -> str:
    info = lang_info(code)
    scripts = " and ".join(info["scripts"])
    gl = ""
    if glossary:
        gl = ("- Use exactly these terms for these categories: "
              + "; ".join(f'"{k}" → "{v}"' for k, v in glossary.items()) + ".\n")
    return (
        f"You are a professional community translator from English to {info['llm_name']}. "
        f"Translate the values of the JSON object, writing in {scripts} script. "
        f"{LANG_GUIDANCE.get(code, '')}\n"
        "Rules:\n"
        "- Return ONLY the translated JSON object with exactly the same keys and the same number of "
        "items in \"bullets\". No explanations, no notes, no romanization or transliteration, no "
        "markdown.\n"
        "- Copy every placeholder like ⟦N0⟧, ⟦N1⟧ exactly once, unchanged. They stand for numbers. "
        "Do not add any other numbers or digits, and never write a placeholder's number yourself.\n"
        "- Copy every name placeholder like ⟦P0⟧ unchanged; it stands for a place name.\n"
        "- Translate month and weekday names. Keep names of places, streets and organizations in "
        "their original Latin spelling.\n"
        + gl +
        "- Do not use em dashes (—); use your language's normal punctuation.\n"
        "- Plain, calm, simple language for residents. Keep the meaning exactly; add nothing."
    )


def _llm_once(protected: dict, code: str, feedback: str = "", glossary: dict | None = None) -> dict:
    user = json.dumps(protected, ensure_ascii=False, indent=1)
    if feedback:
        user += "\n\n" + feedback
    raw = llm.complete(_system_prompt(code, glossary), user, json=True, temperature=0.1, max_tokens=3000)
    return normalize_doc(extract_json(raw), code)


_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december"]
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def repair_date_words(doc: dict, code: str) -> tuple[dict, list[str]]:
    """Replace English month/weekday names left untranslated with the CLDR (Babel) names for
    the language. Used only after the retry, when these are the only script problems left."""
    from babel.dates import get_day_names, get_month_names

    loc = lang_info(code)["babel"]
    months = get_month_names("wide", "format", locale=loc)
    days = get_day_names("wide", "format", locale=loc)
    table = {m: months[i + 1] for i, m in enumerate(_MONTHS)}
    table.update({d: days[i] for i, d in enumerate(_DAYS)})
    fixed: list[str] = []

    def sub(text: str) -> str:
        def rep(m):
            fixed.append(m.group(0))
            return table[m.group(0).lower()]
        return re.sub(r"\b(" + "|".join(table) + r")\b", rep, text, flags=re.I)

    out = {k: ([sub(x) for x in v] if isinstance(v, list) else sub(v)) for k, v in doc.items()}
    return out, fixed


def translate_llm(doc: dict, code: str, names: list[str] | None = None) -> tuple[dict, dict]:
    protected, originals = protect(doc, names)
    en_numbers = N.numbers_in_doc(doc)
    from app import glossary as G
    en_text = " ".join(N.bulletin_text_fields(doc))
    terms = G.terms_for(code, en_text)
    info = {"engine": "llm", "attempts": 0, "retried_for_script": False, "problems": []}
    feedback = ""
    best = None
    for attempt in range(2):  # first try + one retry
        info["attempts"] = attempt + 1
        try:
            out = _llm_once(protected, code, feedback, terms)
        except (ValueError, json.JSONDecodeError) as exc:
            info["problems"].append(f"bad JSON: {exc}")
            feedback = "Your previous answer was not valid JSON. Return ONLY the JSON object."
            continue
        restored, missing, dup = restore(out, originals, names)
        scr = S.check_doc(restored, code, doc)
        problems = []
        if missing or dup:
            problems.append("placeholders missing or repeated: " +
                            ", ".join(f"⟦N{i}⟧" for i in sorted(set(missing + dup))))
        from app.verify import binding as B
        problems_bind = B.translation_alignment(doc, restored) + B.percent_binding(doc, restored)
        lost, added = N.compare_multisets(en_numbers, N.numbers_in_doc(restored))
        if (lost or added) and not (missing or dup):
            problems.append("numbers changed (write numbers only as the ⟦N⟧ placeholders, never as "
                            "digits): " + ", ".join(lost + added))
        if problems_bind and not (lost or added or missing or dup):
            problems.append("numbers moved or changed meaning: " + " ".join(problems_bind)
                            + " Keep each number in its own line and write percentages with %.")
        if len(restored["bullets"]) != len(doc.get("bullets", [])):
            problems.append(f"expected {len(doc.get('bullets', []))} bullets, got {len(restored['bullets'])}")
        if not scr["ok"]:
            problems.append(S.describe(scr))
            info["retried_for_script"] = info["retried_for_script"] or attempt == 0
        best = restored
        if not problems:
            info["problems"] = []
            return restored, info
        info["problems"] = problems
        bad_words = ", ".join(sorted({i["text"] for i in scr["issues"]})) if not scr["ok"] else ""
        feedback = ("Your previous answer had problems: " + "; ".join(problems) + ". "
                    + (f"These words are in the wrong script: {bad_words}. " if bad_words else "")
                    + f"Translate again using only {' and '.join(lang_info(code)['scripts'])} script, "
                      "keeping every placeholder exactly once. Return ONLY the JSON.")
    if best is None:
        raise TranslationFailed("The AI model didn't return a usable translation.")
    # Last resort for two narrow, well-understood slips (after the retry):
    #  - English month/weekday names left untranslated → CLDR calendar names;
    #  - common Hindi function words in Devanagari inside Urdu → their Urdu spelling.
    # Only when *every* remaining script problem is one of these; anything else still blocks.
    scr = S.check_doc(best, code, doc)
    if not scr["ok"] and all(_repairable(i, code) for i in scr["issues"]):
        repaired, notes = best, []
        if any(i["script"] == "Latin" for i in scr["issues"]):
            repaired, fixed = repair_date_words(repaired, code)
            info["date_words_filled"] = sorted(set(fixed))
            notes.append("Month/day names the model left in English were filled in from the Unicode "
                         "CLDR calendar data: " + ", ".join(sorted(set(fixed))) + ".")
        if any(i["script"] == "Devanagari" for i in scr["issues"]):
            repaired, fixed = repair_devanagari_urdu(repaired)
            info["devanagari_fixed"] = sorted(set(fixed))
            notes.append("Common Hindi words the model wrote in Devanagari were replaced with their "
                         "Urdu spelling: " + ", ".join(f"{w} → {HINDI_TO_URDU[w]}" for w in sorted(set(fixed))) + ".")
        if S.check_doc(repaired, code, doc)["ok"]:
            info["note"] = " ".join(notes)
            info["problems"] = []
            return repaired, info
    return best, info


# Hindi (Devanagari) function words gemma3 slips into Urdu, with their standard Urdu spelling.
# Deliberately short: only grammatical words whose Urdu form is unambiguous.
HINDI_TO_URDU = {
    "हुईं": "ہوئیں", "हुई": "ہوئی", "हुए": "ہوئے", "हुआ": "ہوا", "है": "ہے", "हैं": "ہیں",
    "था": "تھا", "थी": "تھی", "थे": "تھے", "और": "اور", "में": "میں", "की": "کی", "के": "کے",
    "का": "کا", "को": "کو", "से": "سے", "पर": "پر", "गया": "گیا", "गई": "گئی", "गए": "گئے",
    "रहा": "رہا", "रही": "رہی", "रहे": "رہے", "भी": "بھی", "नहीं": "نہیں", "या": "یا",
    "एक": "ایک", "यह": "یہ", "वह": "وہ", "लिए": "لیے", "किया": "کیا", "करें": "کریں",
}


def _repairable(issue: dict, code: str) -> bool:
    if issue["script"] == "Latin":
        return issue["text"].lower() in (_MONTHS + _DAYS)
    if issue["script"] == "Devanagari":
        return code == "ur" and issue["text"] in HINDI_TO_URDU
    return False


def repair_devanagari_urdu(doc: dict) -> tuple[dict, list[str]]:
    fixed: list[str] = []
    pattern = re.compile("|".join(sorted(map(re.escape, HINDI_TO_URDU), key=len, reverse=True)))

    def sub(text: str) -> str:
        def rep(m):
            fixed.append(m.group(0))
            return HINDI_TO_URDU[m.group(0)]
        # whole Devanagari words only (letters/marks; the danda "।" is punctuation, not a letter)
        text = re.sub(r"[\u0900-\u0963\u0966-\u097F]+",
                      lambda m: rep(m) if pattern.fullmatch(m.group(0)) else m.group(0), text)
        return text.replace("\u0964", "\u06d4") if fixed else text  # । → ۔ once we've repaired

    out = {k: ([sub(x) for x in v] if isinstance(v, list) else sub(v)) for k, v in doc.items()}
    return out, fixed


# ---------------------------------------------------------------- Azure engine

_TIME_OR_NUM = re.compile(rf"(?<!\d)\d{{1,2}}:\d{{2}}(?!\d)|{N._NUM_RE.pattern}")


def _azure_html(text: str) -> str:
    """HTML-escape and wrap numbers (with a directly following % sign) in notranslate spans. A clock time
    ("6:30") is one span: protected separately, Azure split it around "p.m." ("6시: 오후30 시")."""
    norm = N.normalize_digits(text)
    out, pos = [], 0
    for m in _TIME_OR_NUM.finditer(norm):
        end = m.end() + 1 if norm[m.end():m.end() + 1] in ("%", "٪") else m.end()
        if m.start() < pos:
            continue
        out.append(html.escape(text[pos:m.start()], quote=False))
        out.append(f'<span class="notranslate">{html.escape(text[m.start():end])}</span>')
        pos = end
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def _azure_text(text: str, code: str, names: list[str] | None = None) -> str:
    """_azure_html, with a bulletin line's category label sent already translated (glossary) and protected,
    and the neighbourhood's name kept as written (as the AI model path does; street signs and maps use it)."""
    from app import glossary as G
    fixed = G.label_prefix(code, text)
    if fixed:
        term, rest = fixed
        out = f'<span class="notranslate">{html.escape(term)}</span>' + _azure_html(rest)
    else:
        out = _azure_html(text)
    for name in names or []:
        if name and not re.search(r"\d", name):
            out = out.replace(html.escape(name, quote=False), f'<span class="notranslate">{html.escape(name, quote=False)}</span>')
    return out


_SPAN_RE = re.compile(r'<span class="notranslate">(.*?)</span>', re.S)
# Arabic-script letters (not digits): Azure drops the space next to a protected number in these.
_ARABIC_LETTER = "[\u0621-\u064A\u066E-\u06D3\u06FA-\u06FF]"


# Scripts that put a space between a number and a word; Azure sometimes drops it next to a protected
# number ("powyżej5,000dolarów"). Not Chinese, Korean or Tamil, which attach words to numbers by design.
_SPACED_SCRIPTS = {"Latin", "Cyrillic", "Greek", "Bengali", "Gujarati", "Gurmukhi"}
_SPAN_OPEN = '<span class="notranslate">'


def _space_around_spans(s: str) -> str:
    s = re.sub(r"([^\W\d_])" + re.escape(_SPAN_OPEN), r"\1 " + _SPAN_OPEN, s)       # word<span>5  → word <span>5
    return re.sub(r"</span>([^\W\d_])", r"</span> \1", s)                           # 5</span>word → 5</span> word


def _from_azure_html(s: str, code: str = "") -> str:
    # Two protected numbers that Azure placed side by side must not merge: "92" + "3%" → "923%", and a
    # plain space before three digits reads as a thousands separator ("312 321" → 312321), so use ", ".
    s = re.sub(r"</span>\s*<span class=\"notranslate\">(?=\d{3}(?!\d))", "</span>, <span class=\"notranslate\">", s)
    s = re.sub(r"</span>\s*<span class=\"notranslate\">", "</span> <span class=\"notranslate\">", s)
    if set(lang_info(code)["scripts"]) & _SPACED_SCRIPTS:
        s = _space_around_spans(s)
    s = _SPAN_RE.sub(lambda m: m.group(1), s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).strip()
    if lang_info(code)["scripts"] == ["Arabic"]:
        s = re.sub(rf"([0-9%٪])(?={_ARABIC_LETTER})", r"\1 ", s)   # "911کال" → "911 کال"
        s = re.sub(rf"({_ARABIC_LETTER})(?=[0-9])", r"\1 ", s)      # "کال911" → "کال 911"
    return s


AZURE_TO_OURS = {"pt": "pt-pt", "pt-PT": "pt-pt", "tl": "fil", "zh-Hans": "zh-Hans", "zh-Hant": "zh-Hant", "prs": "fa"}


def detect_language(text: str) -> str | None:
    """NeighbourBrief language code of `text` ('en' for English), by Azure's detector. None when Azure
    isn't set up, offline, unsure, or the language isn't one NeighbourBrief supports."""
    s = get_settings()
    if not s.azure_translator_key or s.demo_offline or not text.strip():
        return None
    headers = {"Ocp-Apim-Subscription-Key": s.azure_translator_key, "Content-Type": "application/json"}
    if s.azure_translator_region:
        headers["Ocp-Apim-Subscription-Region"] = s.azure_translator_region
    try:
        r = httpx.post(f"{s.azure_translator_endpoint}/detect", params={"api-version": "3.0"}, headers=headers,
                       json=[{"Text": text[:500]}], timeout=15)
        r.raise_for_status()
        d = r.json()[0]
    except (httpx.HTTPError, ValueError, IndexError, KeyError) as exc:
        log.info("Language detection skipped: %s", exc)
        return None
    if d.get("score", 0) < 0.5:
        return None
    code = AZURE_TO_OURS.get(d.get("language"), d.get("language"))
    return code if code in LANGUAGES or code == "en" else None


def translate_plain(text: str, to: str, source: str | None = None) -> str | None:
    """One short text through Azure (numbers protected the same way as bulletins). None if unavailable."""
    s = get_settings()
    if not s.azure_translator_key or s.demo_offline or not text.strip():
        return None
    headers = {"Ocp-Apim-Subscription-Key": s.azure_translator_key, "Content-Type": "application/json"}
    if s.azure_translator_region:
        headers["Ocp-Apim-Subscription-Region"] = s.azure_translator_region
    params = {"api-version": "3.0", "to": to, "textType": "html"}
    if source:
        params["from"] = source
    try:
        r = httpx.post(f"{s.azure_translator_endpoint}/translate", params=params, headers=headers,
                       json=[{"Text": _azure_html(text)}], timeout=30)
        r.raise_for_status()
        return _from_azure_html(r.json()[0]["translations"][0]["text"], to)
    except (httpx.HTTPError, ValueError, IndexError, KeyError) as exc:
        log.info("Translation of a short text skipped: %s", exc)
        return None


def back_for(doc: dict, code: str, info: dict) -> dict | None:
    """The back-translation for the meaning checks: memory lines keep their own (no call); otherwise Azure."""
    if info.get("engine") == "memory":
        return info.get("memory_back")
    return back_translate(doc, code)


def back_translate(doc: dict, code: str) -> dict | None:
    """The translation, translated back to English by Azure (for the meaning check and for officers
    who don't read the language). None when Azure isn't set up, offline mode is on, or it fails."""
    s = get_settings()
    if not s.azure_translator_key or s.demo_offline or not doc:
        return None
    flat, texts = [], []
    for k in FIELDS:
        v = doc.get(k)
        for i, x in (enumerate(v) if isinstance(v, list) else [(None, v or "")]):
            flat.append((k, i))
            texts.append(x)
    headers = {"Ocp-Apim-Subscription-Key": s.azure_translator_key, "Content-Type": "application/json"}
    if s.azure_translator_region:
        headers["Ocp-Apim-Subscription-Region"] = s.azure_translator_region
    try:
        r = httpx.post(f"{s.azure_translator_endpoint}/translate", params={"api-version": "3.0", "from": code, "to": "en"},
                       headers=headers, json=[{"Text": t} for t in texts], timeout=30)
        r.raise_for_status()
        results = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.info("Back-translation from %s skipped: %s", code, exc)
        return None
    out: dict = {k: ([] if k == "bullets" else "") for k in FIELDS}
    for (k, i), res in zip(flat, results):
        t = res["translations"][0]["text"]
        if i is None:
            out[k] = t
        else:
            out[k].append(t)
    return out


def translate_azure(doc: dict, code: str, names: list[str] | None = None) -> tuple[dict, dict]:
    s = get_settings()
    flat: list[tuple[str, int | None]] = []
    texts: list[str] = []
    for k in FIELDS:
        v = doc.get(k)
        if isinstance(v, list):
            for i, x in enumerate(v):
                flat.append((k, i))
                texts.append(x)
        else:
            flat.append((k, None))
            texts.append(v or "")
    headers = {"Ocp-Apim-Subscription-Key": s.azure_translator_key, "Content-Type": "application/json"}
    if s.azure_translator_region:
        headers["Ocp-Apim-Subscription-Region"] = s.azure_translator_region
    for attempt in range(3):  # the free tier rate-limits bursts: wait as long as Azure asks
        r = httpx.post(
            f"{s.azure_translator_endpoint}/translate",
            params={"api-version": "3.0", "from": "en", "to": code, "textType": "html"},
            headers=headers,
            json=[{"Text": _azure_text(t, code, names)} for t in texts],
            timeout=30,
        )
        if r.status_code != 429 or attempt == 2:
            break
        try:
            wait = min(float(r.headers.get("Retry-After", "")), 20.0)
        except ValueError:
            wait = 5.0 * (attempt + 1)
        log.info("Azure Translator rate-limited (429); retrying in %.0f s", wait)
        time.sleep(wait)
    r.raise_for_status()
    results = r.json()
    out: dict = {k: ([] if k == "bullets" else "") for k in FIELDS}
    for (k, i), res in zip(flat, results):
        t = _from_azure_html(res["translations"][0]["text"], code)
        if i is None:
            out[k] = t
        else:
            out[k].append(t)
    return normalize_doc(out, code), {"engine": "azure", "attempts": 1, "problems": []}


# ---------------------------------------------------------------- entry point

_ZH_MONTH_YEAR = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])\s?月\s?(\d{4})\s?日")
_KO_MONTH_YEAR = re.compile(r"(?<!\d)(1[0-2]|0?[1-9])\s?월\s?(\d{4})\s?일")


def repair_month_year(doc: dict, code: str) -> tuple[dict, int]:
    """Azure sometimes writes "September 2026" as "9月2026日" ("the 2026th day of September") in
    Chinese, or "9월 2026일" in Korean. Put it in the normal order ("2026年9月", "2026년 9월");
    the numbers don't change, so every check still applies."""
    if not (code.startswith("zh") or code == "ko"):
        return doc, 0
    pat, fmt = (_KO_MONTH_YEAR, r"\2년 \1월") if code == "ko" else (_ZH_MONTH_YEAR, r"\2年\1月")
    count = 0

    def fix(t):
        nonlocal count
        new, n = pat.subn(fmt, t)
        count += n
        return new
    out = {k: ([fix(x) for x in v] if isinstance(v, list) else fix(v) if isinstance(v, str) else v) for k, v in doc.items()}
    return out, count


def translate_doc(doc: dict, code: str, names: list[str] | None = None, llm_fallback: bool = True) -> tuple[dict, dict]:
    """Translate one bulletin doc. Returns (translated_doc, info) or raises TranslationFailed.
    llm_fallback=False: if Azure fails, raise instead of using the (much slower) local model.
    Lines the translation memory has (app/tm.py) come from it; when it has every line, no engine is used."""
    from app import tm
    name = (names or [""])[0]
    full = tm.apply(doc, code, name)
    if full:
        out, mem = full
        out, _ = repair_month_year(out, code)
        info = {"engine": "memory", "engine_label": "Translation memory", "seconds": 0.0, "note": "",
                "memory": {k: v for k, v in mem.items() if k != "hits"}, "memory_back": tm.back_doc(doc, mem["hits"])}
        log.info("Translated to %s from memory (%d lines, %d reviewed)", code, mem["lines"], mem["reviewed"])
        return out, info
    engine, note = engine_for()
    t0 = time.monotonic()
    info: dict = {"note": note}
    if engine == "azure":
        try:
            out, i = translate_azure(doc, code, names)
            info.update(i)
        except Exception as exc:
            if not llm_fallback:
                raise
            log.warning("Azure Translator failed for %s: %s", code, exc)
            info["note"] = f"Azure Translator failed ({type(exc).__name__}); used the AI model instead."
            engine = "llm"
    if engine == "llm":
        try:
            out, i = translate_llm(doc, code, names)
            note = " ".join(x for x in (info.get("note"), i.pop("note", "")) if x)
            info.update(i)
            info["note"] = note
        except llm.LLMUnavailable as exc:
            raise TranslationFailed(
                "The AI model isn't available (is Ollama running?) and no Azure Translator key is "
                f"set, so this language couldn't be translated. ({exc})") from exc
    out, fixed = repair_month_year(out, code)
    if fixed:
        info["note"] = " ".join(x for x in (info.get("note"), "Fixed the order of a month and year.") if x)
    info["engine"] = engine
    info["engine_label"] = engine_label(engine)
    info["seconds"] = round(time.monotonic() - t0, 1)
    part = tm.apply(doc, code, name, translated=out)  # same line, same words, every month; reviewed lines win
    if part and part[1]["from_memory"]:
        out = part[0]
        info["memory"] = {k: v for k, v in part[1].items() if k != "hits"}
    scr = S.check_doc(out, code, doc)
    info["script_ok"] = scr["ok"]
    if engine == "llm":
        from app import glossary as G
        used, expected = G.usage(code, " ".join(N.bulletin_text_fields(doc)), " ".join(N.bulletin_text_fields(out)))
        info["glossary"] = {"used": used, "expected": expected}
    if not scr["ok"] and engine == "llm":
        info["suggestion"] = ("This translation still mixes scripts after a retry. Consider "
                              "switching to Azure Translator (set AZURE_TRANSLATOR_KEY in .env).")
    log.info("Translated to %s with %s in %.1fs", code, engine, info["seconds"])
    return out, info
