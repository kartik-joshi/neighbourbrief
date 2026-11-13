from app.verify import content

BASE = {
    "headline": "Thorncliffe Park safety update for August 2026",
    "summary": "In August, there were 30 reports to police. A report to police does not mean anyone was charged.",
    "bullets": ["Assaults: 10 reports to police in August."],
    "officer_section": "This month's priority: Car break-ins near the plaza.",
    "closing": "In an emergency, call 911.",
}


def titles(r):
    return [f["title"] for f in r["findings"]]


def test_clean_draft_has_no_warnings():
    r = content.check(BASE)
    assert r["ok"], r["findings"]


def test_alarming_words_and_crime_framing():
    r = content.check(dict(BASE, summary=BASE["summary"] + " The plaza is dangerous; crimes committed are a crime wave."))
    items = {i for f in r["findings"] for i in f["matches"]}
    assert {"dangerous", "crime wave", "crimes committed"} <= items and not r["ok"]


def test_group_statements_flagged():
    r = content.check(dict(BASE, officer_section="Newcomers and youths are behind the thefts."))
    assert "Mentions a group of people" in titles(r)


def test_personal_information_flagged_but_tps_contacts_ok():
    doc = dict(BASE, closing="Call 911 or 416-808-5300, or email 5313@tps.ca. "
                             "Resident John called from 647-555-0199, jane@gmail.com, 12 Overlea Blvd unit 4.")
    hits = next(f for f in content.check(doc)["findings"] if f["title"] == "Possible personal information")["matches"]
    assert hits == ["jane@gmail.com", "647-555-0199", "12 Overlea Blvd", "unit 4"]


def test_missing_charge_reminder():
    r = content.check(dict(BASE, summary="In August, there were 30 reports to police."))
    assert "Missing the “report ≠ charge” reminder" in titles(r)


def test_length_and_grade_are_info_only():
    long = dict(BASE, summary=BASE["summary"] + " " + "Residents appreciated the comprehensive neighbourhood communication initiative. " * 30)
    r = content.check(long)
    assert r["words"] > 180 and r["ok"]  # info-level findings don't make it "not ok"
    assert any(t.endswith("words") for t in titles(r))
    assert any(t.startswith("Reading level") for t in titles(r))


def test_officer_warnings():
    w = content.officer_warnings({"priority": "Noise from 45 Thorncliffe Park Drive, call me 416-555-0100", "action_taken": ""})
    assert w and "personal information" in w[0]
    assert content.officer_warnings({"priority": "Car break-ins near the plaza.", "action_taken": "More patrols."}) == []
