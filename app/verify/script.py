"""Script check: a translation must use only its language's expected Unicode script(s),
plus digits, punctuation, symbols and spaces.

Latin words are tolerated in non-Latin translations only when the same word appears in the English
source (proper nouns and acronyms such as "Thorncliffe Park", "TPS", "WhatsApp"); any other
foreign-script run (e.g. Devanagari inside Urdu) is flagged as "mixed script".
"""
from __future__ import annotations

import re
import unicodedata

from app.languages import lang_info

_PREFIX_SCRIPT = {
    "LATIN": "Latin", "CYRILLIC": "Cyrillic", "GREEK": "Greek", "ARABIC": "Arabic",
    "ARABIC-INDIC": "Arabic", "EXTENDED": "Arabic",  # EXTENDED ARABIC-INDIC DIGIT
    "HEBREW": "Hebrew", "DEVANAGARI": "Devanagari", "BENGALI": "Bengali", "GURMUKHI": "Gurmukhi",
    "GUJARATI": "Gujarati", "TAMIL": "Tamil", "TELUGU": "Telugu", "KANNADA": "Kannada",
    "MALAYALAM": "Malayalam", "SINHALA": "Sinhala", "THAI": "Thai", "LAO": "Lao",
    "CJK": "Han", "IDEOGRAPHIC": "Han", "HANGUL": "Hangul", "HIRAGANA": "Hiragana",
    "KATAKANA": "Katakana", "BOPOMOFO": "Bopomofo", "ARMENIAN": "Armenian", "GEORGIAN": "Georgian",
    "ETHIOPIC": "Ethiopic", "SYRIAC": "Syriac", "THAANA": "Thaana", "MONGOLIAN": "Mongolian",
    "TIBETAN": "Tibetan", "KHMER": "Khmer", "MYANMAR": "Myanmar",
}


def char_script(ch: str) -> str | None:
    """Script of a letter/mark/digit; None for script-neutral characters (ASCII digits,
    punctuation, spaces, symbols, emoji, generic combining marks, format controls)."""
    cat = unicodedata.category(ch)
    if cat[0] not in "LMN":
        return None
    if cat == "Nd" and ch.isascii():
        return None
    if cat[0] == "N" and cat != "Nd":
        return None  # superscripts, fractions, circled numbers: neutral
    name = unicodedata.name(ch, "")
    if not name:
        return "Unknown"
    if name.startswith("COMBINING"):
        return None
    if name.startswith(("FULLWIDTH", "HALFWIDTH")):
        rest = name.split(" ", 1)[1] if " " in name else ""
        if rest.startswith(("LATIN", "DIGIT")):
            return "Han"  # full-width forms are part of CJK typography
        return char_script(unicodedata.normalize("NFKC", ch)[0]) if ch != unicodedata.normalize("NFKC", ch)[0] else None
    first = name.split(" ", 1)[0]
    return _PREFIX_SCRIPT.get(first, first.title())


def _runs(text: str):
    """Yield (start, end, script) runs of consecutive same-script characters (ignoring neutrals)."""
    cur_script, start = None, 0
    for i, ch in enumerate(text):
        sc = char_script(ch)
        if sc is None:
            if cur_script is not None:
                yield start, i, cur_script
                cur_script = None
            continue
        if sc != cur_script:
            if cur_script is not None:
                yield start, i, cur_script
            cur_script, start = sc, i
    if cur_script is not None:
        yield start, len(text), cur_script


# Month and weekday names must be translated, so they aren't tolerated as "proper nouns".
_MUST_TRANSLATE = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
    "sept", "oct", "nov", "dec", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
}


def _source_latin_words(source_text: str) -> set[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-zÀ-ɏ]+", source_text or "")}
    return words - _MUST_TRANSLATE


# Letters that belong to the Arabic script but not to the target language: they reveal the model
# drifted into a neighbouring language (e.g. Sindhi or Pashto instead of Urdu).
_SINDHI_ONLY = set("ٿٽڪڏڳڱڻڦڄڃڇٺٻڀڊڌڍڙ۾")
_PASHTO_ONLY = set("ټډړښږځڅګڼېۍ")
_URDU_ONLY = set("ٹڈڑںے")
_PERSO_URDU = set("پچژگ") | _URDU_ONLY | {"\u06be"}  # ھ (do-chashmi he)
FOREIGN_LETTERS: dict[str, tuple[set[str], str]] = {
    "ur": (_SINDHI_ONLY | _PASHTO_ONLY, "Sindhi/Pashto letters, not Urdu"),
    "ps": (_SINDHI_ONLY | _URDU_ONLY, "Urdu/Sindhi letters, not Pashto"),
    "fa": (_SINDHI_ONLY | _PASHTO_ONLY | _URDU_ONLY, "Urdu/Sindhi/Pashto letters, not Persian"),
    "ar": (_SINDHI_ONLY | _PASHTO_ONLY | _PERSO_URDU, "Urdu/Persian letters, not Arabic"),
    # Same alphabet, different language: a model asked for Ukrainian can drift into Russian.
    "uk": (set("ыэъёЫЭЪЁ"), "Russian letters, not Ukrainian"),
    "ru": (set("іїєґІЇЄҐ"), "Ukrainian letters, not Russian"),
    "bn": (set("ৰৱ"), "Assamese letters, not Bengali"),
}


def find_foreign(text: str, lang: str, source_text: str = "") -> list[dict]:
    """Runs of characters in `text` whose script isn't expected for `lang`."""
    expected = set(lang_info(lang)["scripts"])
    latin_ok = _source_latin_words(source_text) if "Latin" not in expected else set()
    bad_letters, bad_label = FOREIGN_LETTERS.get(lang, (set(), ""))
    issues = []
    for s, e, sc in _runs(text or ""):
        if sc in expected:
            if bad_letters:
                for m in re.finditer(r"\S+", text[s:e]):
                    if any(ch in bad_letters for ch in m.group(0)):
                        issues.append({"text": m.group(0), "script": bad_label,
                                       "start": s + m.start(), "end": s + m.end()})
            continue
        run = text[s:e]
        if sc == "Latin" and run.lower() in latin_ok:
            continue
        issues.append({"text": run, "script": sc, "start": s, "end": e})
    return issues


def check_doc(doc: dict, lang: str, source_doc: dict | None = None) -> dict:
    from app.verify.numbers import bulletin_text_fields

    source_text = " ".join(bulletin_text_fields(source_doc or {}))
    issues = []
    for field in bulletin_text_fields(doc):
        for it in find_foreign(field, lang, source_text):
            issues.append({"text": it["text"], "script": it["script"]})
    scripts = sorted({i["script"] for i in issues})
    return {"ok": not issues, "issues": issues, "scripts": scripts,
            "expected": lang_info(lang)["scripts"]}


def describe(result: dict) -> str:
    if result["ok"]:
        return "Script OK"
    words = ", ".join(sorted({i["text"] for i in result["issues"]})[:6])
    return f"Mixed script ({', '.join(result['scripts'])}): {words}"
