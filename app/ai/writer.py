"""English bulletin text from facts: LLM writer with validation/retry, and a template fallback."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time

from app.verify import numbers as N

log = logging.getLogger(__name__)

OFFICER_MAX_CHARS = 400
FIELDS = ("headline", "summary", "bullets", "officer_section", "closing", "followup")


# ---------------------------------------------------------------- officer input

def clean_officer_text(text: str | None) -> tuple[str, bool]:
    """Collapse whitespace; shorten very long text at a sentence/word boundary.

    Returns (text, was_shortened).
    """
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= OFFICER_MAX_CHARS:
        return t, False
    cut = t[:OFFICER_MAX_CHARS]
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind("۔ "), cut.rfind("。"))
    if m > OFFICER_MAX_CHARS * 0.5:
        cut = cut[: m + 1]
    else:
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
        cut = cut.rstrip(",;:") + "…"
    return cut.strip(), True


def _sentence(t: str) -> str:
    t = t.strip()
    if t and t[-1] not in ".!?…۔。":
        t += "."
    return t


def clean_officer_inputs(raw: dict) -> tuple[dict, list[str]]:
    out, warnings = {}, []
    names = {"priority": "This month's priority", "action_taken": "What we did",
             "meeting_note": "Next meeting", "tip": "Tip", "prev_priority": "Last month's priority",
             "followup": "Since last month"}
    for k in ("priority", "action_taken", "meeting_note", "tip", "prev_priority", "followup"):
        if k in ("prev_priority", "followup") and k not in raw:
            continue
        text, short = clean_officer_text(raw.get(k))
        out[k] = text
        if short:
            warnings.append(f"“{names[k]}” was longer than {OFFICER_MAX_CHARS} characters and was "
                            "shortened to fit on one page. Please review it.")
    return out, warnings


FOLLOWUP_STATUS = {"done": "This is done.", "ongoing": "We are still working on it."}


def followup_text(officer: dict) -> str:
    """"You said, we did": last month's priority and what happened since, in the officer's words.
    Written by code (never the AI), then translated and checked like every other field."""
    prev, done = (officer.get("prev_priority") or "").strip(), (officer.get("followup") or "").strip()
    if not prev or not done:
        return ""
    status = FOLLOWUP_STATUS.get(officer.get("followup_status") or "", "")
    done = done[:FOLLOWUP_MAX_CHARS].rstrip(" ,;.")
    if _norm_words(prev) == _norm_words(officer.get("priority") or ""):
        text = f"Since last month: {done}."  # same priority again: don't print it twice
    else:
        text = f"Last month's priority: {prev.rstrip('.')}. Since then: {done}."
    return f"{text} {status}".strip()


FOLLOWUP_MAX_CHARS = 160  # keeps translated bulletins on one printed page


def _norm_words(s: str) -> str:
    return " ".join(re.findall(r"[\w']+", s.lower()))


# ---------------------------------------------------------------- template writer

def _month_name(facts: dict) -> str:
    return dt.date.fromisoformat(facts["period"]["month_start"]).strftime("%B")


def category_sentence(line: dict, month: str) -> str:
    """One short line per category (kept short so the bulletin stays within 120–180 words)."""
    label, m, ytd, prev = line["label"], line["month"], line["ytd"], line["ytd_prev"]
    first = f"{label}: {m} {'report' if m == 1 else 'reports'} in {month}."
    if line["counts_only"] or prev is None:
        return f"{first} {ytd} this year."
    pct = line["pct_change"]
    if pct is not None:
        if pct == 0:
            return f"{first} {ytd} this year, the same as by this time last year."
        return f"{first} {ytd} this year, {'up' if pct > 0 else 'down'} {abs(pct)}% from {prev} by this time last year."
    return f"{first} {ytd} this year, compared with {prev} by this time last year."


def officer_section(officer: dict) -> str:
    parts = []
    if officer.get("priority"):
        parts.append(f"This month's priority: {_sentence(officer['priority'])}")
    if officer.get("action_taken"):
        parts.append(f"What we did: {_sentence(officer['action_taken'])}")
    return " ".join(parts)


def template_draft(facts: dict, officer: dict) -> dict:
    name = facts["neighbourhood"]["name"]
    month = _month_name(facts)
    month_label = facts["period"]["month_label"]
    by_key = {l["key"]: l for l in facts["categories"]}
    shown = [by_key[k] for k in facts["display"] if k in by_key]
    total = facts["totals"]["month"]
    if total == 0:
        summary = (f"There were no reports to police in {name} in {month} in the categories we "
                   "track. A report to police does not mean anyone was charged.")
    else:
        rep = "report" if total == 1 else "reports"
        summary = (f"In {month}, there were {total} {rep} to police in {name}. "
                   "A report to police does not mean anyone was charged.")
    closing_parts = []
    if officer.get("meeting_note"):
        closing_parts.append(f"Next community meeting: {_sentence(officer['meeting_note'])}")
    if officer.get("tip"):
        closing_parts.append(f"Tip: {_sentence(officer['tip'])}")
    closing_parts.append("Questions? Talk to your Neighbourhood Community Officers. "
                         "In an emergency, call 911.")
    return {
        "headline": f"{name}: safety update for {month_label}",
        "summary": summary,
        "bullets": [category_sentence(l, month) for l in shown],
        "officer_section": officer_section(officer),
        "closing": " ".join(closing_parts),
    }


# ---------------------------------------------------------------- LLM writer

SYSTEM_PROMPT = """You write a one-page monthly neighbourhood safety bulletin for residents of a Toronto neighbourhood, on behalf of the neighbourhood's police community officers.

Rules (follow all of them):
- Plain language, Grade 6 reading level: short sentences (under 15 words), everyday words. Calm, neutral, respectful.
- LENGTH: 120-180 words in total, counting every field. Be brief.
- Do not use em dashes (—). Use a period or a comma instead.
- Use ONLY the numbers given in FACTS and in the officer's text. Write numbers as digits. Never invent numbers, causes, trends, explanations, or advice that was not given.
- Say "reports to police", never "crimes committed". Remind readers once that a report to police does not mean anyone was charged.
- No rankings, no comparisons with other neighbourhoods, no predictions. Never use words like "dangerous", "unsafe", "crime wave", "hotspot" or "surge".
- Never mention who commits or suffers crime (no age, race, religion, gender, immigration or other group statements).
- Keep the officer's priority and action faithful to what they wrote: reuse their words, only fix grammar. Do not change the tense, do not add words like "We will", do not add anything. If the officer text is not in English, translate it faithfully into English.
- headline: under 10 words.
- summary: exactly 2 short sentences: the total number of reports to police this month, then "A report to police does not mean anyone was charged."
- bullets: one short line (under 18 words) per category in DISPLAY_CATEGORIES, in that order, using that category's numbers from FACTS. Follow these patterns exactly:
  with pct_change: "Assaults: 10 reports in August. 89 this year, down 3% from 92 by this time last year."
  with ytd_prev but no pct_change: "Break-ins: 4 reports in August. 24 this year, compared with 9 by this time last year."
  without ytd_prev: "Shootings: 2 reports in August. 2 this year."
  Never write "4 break-ins happened" or "there were 10 assaults".
- officer_section: start with "This month's priority:" then the priority, then "What we did:" then the action. Leave it empty if the officer gave neither.
- closing: at most 3 short sentences: the next meeting details exactly as given (if any), then the officer's tip word for word starting with "Tip:" (if any), and "In an emergency, call 911."

Output strict JSON only, no markdown, with exactly these keys:
{"headline": string, "summary": string, "bullets": [string], "officer_section": string, "closing": string}"""


def _facts_for_prompt(facts: dict) -> dict:
    by_key = {l["key"]: l for l in facts["categories"]}
    return {
        "neighbourhood": facts["neighbourhood"]["name"],
        "police_division": facts["neighbourhood"]["division"],
        "month": facts["period"]["month_label"],
        "year_to_date_period": facts["period"]["ytd_label"],
        "same_period_last_year": facts["period"]["ytd_prev_label"],
        "total_reports_this_month_all_categories": facts["totals"]["month"],
        "DISPLAY_CATEGORIES": [
            {k: v for k, v in by_key[k].items() if k in ("label", "month", "ytd", "ytd_prev", "pct_change")}
            | {"note": "counts only, no percent" if by_key[k]["counts_only"] else ""}
            for k in facts["display"] if k in by_key
        ],
        "counting_rule": "counted by the date the report was made to police",
    }


def _user_prompt(facts: dict, officer: dict, errors: list[str] | None = None) -> str:
    msg = {
        "FACTS": _facts_for_prompt(facts),
        "OFFICER": {
            "priority": officer.get("priority", ""),
            "action_taken": officer.get("action_taken", ""),
            "next_meeting": officer.get("meeting_note", ""),
            "tip": officer.get("tip", ""),
        },
        "ALLOWED_NUMBERS": facts["allowed_numbers"],
    }
    text = "Write the bulletin as JSON.\n\n" + json.dumps(msg, ensure_ascii=False, indent=1)
    if errors:
        text += ("\n\nYour previous answer was rejected: " + " ".join(errors) +
                 " Rewrite it using only numbers from ALLOWED_NUMBERS.")
    return text


def extract_json(text: str) -> dict:
    """Parse the first JSON object in `text`, ignoring code fences or chatter around it."""
    if not text:
        raise ValueError("empty response")
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return json.loads(text[start:end + 1])


_EM_DASH = re.compile(r"\s*[\u2014\u2015]{1,2}\s*")


def strip_em_dashes(text: str, code: str = "en") -> str:
    """Replace em dashes with the language's own punctuation (product style: no em dashes)."""
    if "\u2014" not in text and "\u2015" not in text:
        return text
    from app.languages import lang_info
    scripts = lang_info(code)["scripts"]
    sep = "، " if scripts == ["Arabic"] else "，" if scripts == ["Han"] else ", "
    out = _EM_DASH.sub(sep, text).strip()
    return re.sub(r"^[,،，]\s*|[,،，]\s*$", "", out)


def normalize_doc(doc: dict, code: str = "en") -> dict:
    out = {}
    for k in FIELDS:
        v = doc.get(k, [] if k == "bullets" else "")
        if k == "bullets":
            if isinstance(v, str):
                v = [x for x in re.split(r"\n+", v) if x.strip()]
            v = [strip_em_dashes(str(x).strip().lstrip("-•* ").strip(), code) for x in (v or []) if str(x).strip()]
        else:
            v = strip_em_dashes(str(v or "").strip(), code)
        out[k] = v
    return out


WORDS_SOFT_MAX = 185  # target 120–180; a little slack before asking for a shorter version


def word_count(doc: dict) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", " ".join(N.bulletin_text_fields(doc))))


def _validate(doc: dict, facts: dict) -> list[str]:
    errors = []
    if not doc["headline"] or not doc["summary"] or not doc["bullets"]:
        errors.append("headline, summary and bullets must not be empty.")
    bad = N.not_allowed(N.numbers_in_doc(doc), facts["allowed_numbers"])
    if bad:
        errors.append("These numbers are not in the facts: " + ", ".join(bad) + ".")
    if facts.get("display") and facts.get("categories"):
        from app.verify import binding as B
        errors += B.english_binding(doc, facts)
    banned = [w for w in ("dangerous", "crime wave", "hotspot", "unsafe")
              if w in json.dumps(doc).lower()]
    if banned:
        errors.append("Do not use these words: " + ", ".join(banned) + ".")
    return errors


_STOP = {"with", "from", "that", "this", "they", "have", "were", "will", "their", "there", "into",
         "about", "near", "also", "been", "more", "some", "your", "ours", "what"}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP}


def officer_faithful(doc_section: str, officer: dict) -> bool:
    """True if most of the officer's own (English) words survive in the officer section."""
    src = " ".join([officer.get("priority", ""), officer.get("action_taken", "")])
    words = _content_words(src)
    if len(words) < 3:
        return True  # nothing (or non-Latin text) to compare against
    kept = _content_words(doc_section)
    if len(words & kept) / len(words) < 0.7:
        return False
    # "What we did" must not turn into a promise ("We will increase patrols").
    future = re.compile(r"\b(will|we'll|going to|plan to)\b", re.I)
    return not (future.search(doc_section or "") and not future.search(src))


DRAFT_BUDGET_SECONDS = 150  # stop retrying after this; the template draft is instant


def ensure_tip(doc: dict, officer: dict) -> dict:
    """The officer's tip must appear word for word; add it to the closing if the model dropped or
    reworded it."""
    tip = (officer.get("tip") or "").strip()
    if tip and _norm_ws(tip.rstrip(".")) not in _norm_ws(" ".join(N.bulletin_text_fields(doc))):
        doc["closing"] = (doc.get("closing", "").rstrip() + f" Tip: {_sentence(tip)}").strip()
    return doc


def _norm_ws(s: str) -> str:
    return " ".join(s.lower().split())


def llm_draft(facts: dict, officer: dict, complete=None, max_retries: int = 2,
              budget: float = DRAFT_BUDGET_SECONDS) -> tuple[dict | None, dict]:
    """Try the LLM writer. Returns (doc or None, info). Retries stop once `budget` seconds
    have passed, so a slow model can't keep the officer waiting for minutes."""
    from app.ai import llm

    complete = complete or llm.complete
    info = {"attempts": 0, "errors": [], "seconds": 0.0}
    errors: list[str] | None = None
    best_ok: dict | None = None  # a valid draft that was only too long
    t0 = time.monotonic()
    for attempt in range(1 + max_retries):
        if attempt and time.monotonic() - t0 > budget:
            info["out_of_time"] = True
            break
        info["attempts"] = attempt + 1
        try:
            raw = complete(SYSTEM_PROMPT, _user_prompt(facts, officer, errors), json=True)
            doc = normalize_doc(extract_json(raw))
        except llm.LLMUnavailable as exc:
            info["errors"].append(str(exc))
            info["unavailable"] = True
            break
        except (ValueError, json.JSONDecodeError) as exc:
            errors = [f"Your answer was not valid JSON ({exc}). Output JSON only."]
            info["errors"].append(errors[0])
            continue
        errors = _validate(doc, facts)
        words = word_count(doc)
        if not errors and words > WORDS_SOFT_MAX and attempt == 0:
            # Too long is a soft error: ask once for a shorter version, but never fall back to the
            # template just for length.
            errors = [f"It is {words} words; the limit is 180. Shorten every line; keep all numbers."]
            info["errors"].append(errors[0])
            info["too_long_first"] = words
            best_ok = doc
            continue
        if errors and best_ok is not None:
            doc, errors = best_ok, []  # the shorter retry broke a rule: keep the long valid one
        if not errors:
            if not officer_faithful(doc["officer_section"], officer):
                # Keep the officer's own words rather than spend another model call.
                doc["officer_section"] = officer_section(officer)
                info["officer_text_restored"] = True
            info["seconds"] = round(time.monotonic() - t0, 1)
            return doc, info
        info["errors"].append(" ".join(errors))
        log.info("LLM draft attempt %s rejected: %s", attempt + 1, errors)
    info["seconds"] = round(time.monotonic() - t0, 1)
    if best_ok is not None:  # the only problem was length: a long valid draft beats the template
        if not officer_faithful(best_ok["officer_section"], officer):
            best_ok["officer_section"] = officer_section(officer)
            info["officer_text_restored"] = True
        return best_ok, info
    return None, info


def write_draft(facts: dict, officer: dict, use_llm: bool = True, complete=None) -> dict:
    """Return {"doc", "source": "ai"|"template", "note", "info"}."""
    if use_llm:
        doc, info = llm_draft(facts, officer, complete=complete)
        if doc is not None:
            doc = ensure_tip(doc, officer)
            return {"doc": doc, "source": "ai", "note": f"AI draft ({info['seconds']} s)", "info": info}
        if info.get("out_of_time"):
            note = (f"The AI model was too slow to produce a checked draft within "
                    f"{DRAFT_BUDGET_SECONDS} seconds, so this is a template draft built from the facts.")
        elif info.get("unavailable"):
            note = ("The AI writer isn't available (is Ollama running?), so this is a template "
                    "draft built from the facts. Everything still works.")
        else:
            note = ("The AI draft didn't pass the number check after 3 tries, so this is a template "
                    "draft built from the facts.")
        return {"doc": template_draft(facts, officer), "source": "template", "note": note, "info": info}
    return {"doc": template_draft(facts, officer), "source": "template",
            "note": "Template draft built from the facts.", "info": {}}
