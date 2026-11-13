import json

import pytest

from app.ai import llm, writer

FACTS = {
    "neighbourhood": {"id": "055", "name": "Thorncliffe Park", "division": "53 Division", "division_code": "D53"},
    "period": {"month_label": "August 2026", "month_start": "2026-08-01", "month_end": "2026-08-31",
               "month_number": 8, "month_last_day": 31, "days_in_month": 31, "year": 2026, "prev_year": 2025,
               "ytd_label": "January 1 – August 31, 2026", "ytd_prev_label": "January 1 – August 31, 2025"},
    "categories": [
        {"key": "Assault", "label": "Assaults", "month": 10, "ytd": 89, "ytd_prev": 92, "pct_change": -3,
         "direction": "down", "counts_only": False, "mci": True},
        {"key": "Break and Enter", "label": "Break-ins", "month": 4, "ytd": 24, "ytd_prev": 9, "pct_change": None,
         "direction": "up", "counts_only": False, "mci": True},
        {"key": "Shooting", "label": "Shootings", "month": 2, "ytd": 2, "ytd_prev": None, "pct_change": None,
         "direction": None, "counts_only": True, "mci": False},
    ],
    "display": ["Assault", "Break and Enter", "Shooting"],
    "totals": {"month": 16, "ytd": 115},
    "allowed_numbers": ["2", "3", "4", "9", "10", "16", "24", "89", "92", "115", "2025", "2026", "31", "1", "911", "53"],
}
OFFICER = {"priority": "Car break-ins near the plaza parking lots in the evening.",
           "action_taken": "More evening patrols and a lock-your-car reminder session with the tenant association.",
           "meeting_note": ""}

GOOD = {
    "headline": "Thorncliffe Park safety update, August 2026",
    "summary": "In August there were 16 reports to police. A report does not mean anyone was charged.",
    "bullets": ["Assaults: 10 in August; 89 this year, down 3% from 92.",
                "Break-ins: 4 in August; 24 this year vs 9 last year.",
                "Shootings: 2 in August."],
    "officer_section": "This month's priority: Car break-ins near the plaza parking lots in the evening. "
                       "What we did: More evening patrols and a lock-your-car reminder session with the tenant association.",
    "closing": "In an emergency, call 911.",
}


def fake(responses):
    calls = []

    def complete(system, user, json=False):
        calls.append(user)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    complete.calls = calls
    return complete


def test_ai_draft_accepted():
    c = fake([json.dumps(GOOD)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "ai" and r["doc"]["bullets"][0].startswith("Assaults")
    assert len(c.calls) == 1


def test_json_with_chatter_and_fences_is_parsed():
    c = fake(["Sure! Here is the bulletin:\n```json\n" + json.dumps(GOOD) + "\n```\nHope this helps."])
    assert writer.write_draft(FACTS, OFFICER, complete=c)["source"] == "ai"


def test_invented_number_retries_then_succeeds():
    bad = dict(GOOD, summary="There were 17 reports to police.")
    c = fake([json.dumps(bad), json.dumps(GOOD)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "ai" and len(c.calls) == 2
    assert "17" in c.calls[1]  # the error was stated in the retry prompt


def test_three_bad_answers_fall_back_to_template():
    bad = dict(GOOD, summary="There were 17 reports to police.")
    c = fake([json.dumps(bad)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "template" and len(c.calls) == 3
    assert "17" not in json.dumps(r["doc"])


def test_model_unavailable_falls_back_with_message():
    c = fake([llm.LLMUnavailable("connection refused")])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "template"
    assert "Ollama" in r["note"]


def test_unfaithful_officer_text_is_restored():
    changed = dict(GOOD, officer_section="This month's priority: Car break-ins near the plaza parking lots in the evening. "
                                         "What we did: We will increase evening patrols and hold a lock-your-car reminder session with the tenant association.")
    r = writer.write_draft(FACTS, OFFICER, complete=fake([json.dumps(changed)]))
    assert r["source"] == "ai"
    assert "We will" not in r["doc"]["officer_section"]
    assert "More evening patrols" in r["doc"]["officer_section"]


def test_template_draft_numbers_all_allowed():
    from app.verify import numbers as N
    doc = writer.template_draft(FACTS, OFFICER)
    assert N.not_allowed(N.numbers_in_doc(doc), FACTS["allowed_numbers"]) == []
    assert "does not mean anyone was charged" in doc["summary"]
    assert "down 3%" in doc["bullets"][0]
    assert "compared with 9" in doc["bullets"][1]  # small numbers: counts, no percent
    assert "%" not in doc["bullets"][2]


def test_template_quiet_month():
    facts = json.loads(json.dumps(FACTS))
    for l in facts["categories"]:
        l["month"] = 0
    facts["totals"]["month"] = 0
    doc = writer.template_draft(facts, {"priority": "", "action_taken": "", "meeting_note": ""})
    assert "no reports to police" in doc["summary"]
    assert doc["officer_section"] == ""


@pytest.mark.parametrize("text", ["a" * 900, ("Word " * 300)])
def test_long_officer_text_shortened(text):
    out, warnings = writer.clean_officer_inputs({"priority": text})
    assert len(out["priority"]) <= writer.OFFICER_MAX_CHARS + 1
    assert warnings


def test_officer_text_emoji_and_urdu_kept():
    out, warnings = writer.clean_officer_inputs({"priority": "🚗 گاڑیوں کی چوری پر توجہ", "action_taken": ""})
    assert out["priority"] == "🚗 گاڑیوں کی چوری پر توجہ" and not warnings


def test_time_budget_stops_retries(monkeypatch):
    bad = dict(GOOD, summary="There were 17 reports to police.")
    ticks = []

    def clock():  # start at 0, then every later reading is 200 s on (a very slow model)
        ticks.append(1)
        return 0 if len(ticks) == 1 else 200
    monkeypatch.setattr(writer.time, "monotonic", clock)
    c = fake([json.dumps(bad)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "template" and len(c.calls) == 1
    assert "too slow" in r["note"]


LONG = dict(GOOD, summary=GOOD["summary"] + " " + "Residents can read this update and share it with neighbours. " * 20)


def test_too_long_draft_asks_once_for_shorter():
    c = fake([json.dumps(LONG), json.dumps(GOOD)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "ai" and len(c.calls) == 2
    assert "limit is 180" in c.calls[1]
    assert r["doc"]["summary"] == GOOD["summary"]


def test_still_long_after_retry_is_accepted_not_template():
    c = fake([json.dumps(LONG)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "ai" and len(c.calls) == 2


def test_shorter_retry_that_breaks_a_rule_keeps_the_valid_long_draft():
    bad_short = dict(GOOD, summary="There were 17 reports to police.")
    c = fake([json.dumps(LONG), json.dumps(bad_short)])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "ai" and "17" not in r["doc"]["summary"]
    assert r["doc"]["summary"] == LONG["summary"].strip()


def test_template_draft_within_length_and_grade():
    from app.verify import content
    c = content.check(writer.template_draft(FACTS, OFFICER))
    assert c["words"] <= 180 and c["ok"]


def test_shorter_retry_with_broken_json_keeps_the_valid_long_draft():
    c = fake([json.dumps(LONG), "not json at all", "still not json"])
    r = writer.write_draft(FACTS, OFFICER, complete=c)
    assert r["source"] == "ai" and r["doc"]["summary"] == LONG["summary"].strip()


def test_tip_in_template_and_forced_into_ai_draft():
    from app import tips
    off = dict(OFFICER, tip=tips.get("car-lock")["text"])
    doc = writer.template_draft(FACTS, off)
    assert "Tip: Lock your car and keep bags" in doc["closing"]
    # model dropped the tip → added back word for word
    r = writer.write_draft(FACTS, off, complete=fake([json.dumps(GOOD)]))
    assert r["source"] == "ai" and r["doc"]["closing"].endswith("Tip: " + off["tip"])
    # model kept it → not duplicated
    kept = dict(GOOD, closing="In an emergency, call 911. Tip: " + off["tip"])
    r = writer.write_draft(FACTS, off, complete=fake([json.dumps(kept)]))
    assert r["doc"]["closing"].count("Tip:") == 1


def test_tips_library():
    from app import tips
    assert tips.suggested(["Auto Theft"])[0]["id"] in ("car-theft", "key-fob")
    assert tips.resolve("bike", "") == tips.get("bike")["text"]
    assert tips.resolve("bike", "  My own   tip. ") == "My own tip."
    assert tips.resolve("nope", "") == ""
    for t in tips.TIPS:
        assert len(t["text"]) <= tips.TIP_MAX_CHARS


def test_em_dashes_removed_from_generated_text():
    d = writer.normalize_doc({"headline": "Thorncliffe Park — August 2026", "summary": "Reports went down — a little.",
                              "bullets": ["Assaults — 10 reports."], "officer_section": "", "closing": "—Call 911."})
    assert "\u2014" not in str(d)
    assert d["headline"] == "Thorncliffe Park, August 2026" and d["summary"] == "Reports went down, a little."
    assert d["closing"] == "Call 911."
    ur = writer.normalize_doc({"summary": "مصنوعی ذہانت کی مدد سے ترجمہ — ابھی نہیں"}, "ur")
    assert ur["summary"] == "مصنوعی ذہانت کی مدد سے ترجمہ، ابھی نہیں"
    zh = writer.normalize_doc({"summary": "人工智能輔助翻譯——尚未審閱"}, "zh-Hant")
    assert zh["summary"] == "人工智能輔助翻譯，尚未審閱"
