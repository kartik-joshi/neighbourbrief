"""Sentence splitting for 'Check my numbers'."""
from app.verify import factcheck as FC


def test_sentence_ends_after_a_number():
    text = "Break-ins rose to 38. There were 14 robberies. Rates fell 2.5 points."
    spans = [text[a:b].strip() for a, b in FC._sentences(text)]
    assert spans == ["Break-ins rose to 38.", "There were 14 robberies.", "Rates fell 2.5 points."]
    assert FC._categories_in(spans[0]) == {"Break and Enter"}


def test_dollar_amounts_are_skipped():
    facts = {"neighbourhood": {"name": "X"}, "period": {"month_label": "August 2026", "year": 2026, "prev_year": 2025},
             "categories": [{"key": "Theft Over", "label": "Thefts over $5,000", "month": 1, "ytd": 8, "ytd_prev": 12, "pct_change": None}],
             "totals": {"month": 1, "ytd": 8}}
    r = FC.check_text("Thefts over $5,000: 8 so far this year.", facts)
    assert [(i["text"], i["verdict"]) for i in r["items"]] == [("$5,000", "skipped"), ("8", "match")]
