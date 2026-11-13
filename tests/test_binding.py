"""Number-binding checks, with real model outputs from the test rounds as fixtures."""
from app.verify import binding as B

FACTS = {
    "period": {"year": 2026, "prev_year": 2025},
    "categories": [
        {"key": "Assault", "label": "Assaults", "month": 10, "ytd": 89, "ytd_prev": 92, "pct_change": -3},
        {"key": "Break and Enter", "label": "Break-ins", "month": 4, "ytd": 24, "ytd_prev": 9, "pct_change": None},
        {"key": "Theft Over", "label": "Thefts over $5,000", "month": 2, "ytd": 8, "ytd_prev": 11, "pct_change": None},
    ],
    "display": ["Assault", "Break and Enter", "Theft Over"],
}
EN = {"headline": "Thorncliffe Park: August 2026", "summary": "There were 16 reports to police.",
      "bullets": ["Assaults: 10 reports in August. 89 this year, down 3% from 92 by this time last year.",
                  "Break-ins: 4 reports in August. 24 this year, compared with 9 by this time last year.",
                  "Thefts over $5,000: 2 reports in August. 8 this year, compared with 11 by this time last year."],
      "officer_section": "", "closing": "In an emergency, call 911."}


def doc(**kw):
    d = {k: (list(v) if isinstance(v, list) else v) for k, v in EN.items()}
    d.update(kw)
    return d


def test_clean_english_passes():
    assert B.english_binding(EN, FACTS) == []


def test_english_line_using_another_categorys_number():
    bad = doc(bullets=["Assaults: 4 reports in August. 89 this year, down 3% from 92 by this time last year.",
                       EN["bullets"][1], EN["bullets"][2]])
    issues = B.english_binding(bad, FACTS)
    assert len(issues) == 1 and "Assaults line has 4" in issues[0]


def test_english_wrong_number_of_lines():
    assert "exactly one line" in B.english_binding(doc(bullets=EN["bullets"][:2]), FACTS)[0]


def test_numbers_swapped_between_lines_in_translation():
    tr = doc(bullets=["حملے: اگست میں 4 رپورٹیں۔ اس سال 89، پچھلے سال 92 سے 3% کم۔",
                      "نقب زنی: اگست میں 10 رپورٹیں۔ اس سال 24، پچھلے سال 9۔", EN["bullets"][2]])
    issues = B.translation_alignment(EN, tr)
    assert any("line 1" in i and "missing 10" in i for i in issues)
    assert any("line 2" in i and "missing 4" in i for i in issues)


def test_merged_numbers_explained():
    """Real Azure zh-Hant output: '92' and '3%' side by side → '923%'."""
    tr = doc(bullets=["攻擊事件：八月有10報告。今年89，比去年同期923%。", EN["bullets"][1], EN["bullets"][2]])
    issues = B.translation_alignment(EN, tr)
    assert "92 and 3 were merged into 923" in issues[0]


def test_percent_lost_real_cantonese_output():
    """Real gemma3 zh-Hant (published in round 3!): '11 percent' → '11 厘'."""
    en = doc(bullets=["Assaults: 6 reports to police in August; 49 so far this year, compared with 55 at this "
                      "time last year, a decrease of 11 percent."] + EN["bullets"][1:])
    tr = doc(bullets=["襲擊：八月向警方向嘅報告有 6 宗；今年至今有 49 宗，相比之下，去年同期有 55 宗，減少咗 11 厘。"]
             + EN["bullets"][1:])
    assert "11% is no longer shown as a percentage" in B.percent_binding(en, tr)[0]


def test_percent_words_in_other_languages_accepted():
    for text in ["89، 3 سلنې کمه، 92",            # Pashto (inflected)
                 "89، جو 92 سے 3 فیصد کم ہیں",      # Urdu
                 "89، 3٪ کمتر از 92",               # Persian sign
                 "89، بانخفاض ‎%3 من 92",           # Arabic, sign before the number
                 "今年89宗，比去年同期92宗減少百分之3",  # Chinese word form
                 "89, в сравнении с 92 меньше на 3 процента",
                 "89, 3 퍼센트 감소"]:
        tr = doc(bullets=[text] + EN["bullets"][1:])
        assert B.percent_binding(EN, tr) == [], text


def test_real_russian_duplicate_number_in_closing():
    en = doc(closing="Our next meeting is Wednesday, October 21 at 7 p.m. In case of emergency, please call 911.")
    tr = doc(closing="Наша следующая встреча состоится в среду, 21 октября 21 в 7 вечера. Звоните 911.")
    issues = B.translation_alignment(en, tr)
    assert "closing" in issues[0] and "has 21 instead" in issues[0]
