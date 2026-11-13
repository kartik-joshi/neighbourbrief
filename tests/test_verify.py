import pytest

from app.verify import numbers as N
from app.verify import script as S


@pytest.mark.parametrize("text,expected", [
    ("There were 12 reports, up 25% from 1,234.", ["12", "25", "1234"]),
    ("٢٠٢٦ میں ١٢ رپورٹیں", ["2026", "12"]),                   # Eastern Arabic digits
    ("۲۰۲۶ میں ۱۲ رپورٹیں اور ۲۵٪", ["2026", "12", "25"]),       # Persian/Urdu digits + Arabic percent
    ("२०२६ में १२", ["2026", "12"]),                            # Devanagari digits
    ("௧௨ புகார்கள்", ["12"]),                                  # Tamil digits
    ("１２件と３０件", ["12", "30"]),                           # full-width
    ("5 000 $ и 5.000 € и 5,000", ["5000", "5000", "5000"]),    # thousands separators
    ("3,5 процента", ["3.5"]),                                 # decimal comma
    ("2026 12 reports", ["2026", "12"]),                       # not a thousands group
    ("October 13th at 7:00 p.m.", ["13", "7", "0"]),
])
def test_extract_numbers(text, expected):
    assert N.extract_numbers(text) == expected


def test_urdu_sample_with_eastern_arabic_digits_matches_english():
    en = {"headline": "Thorncliffe Park, August 2026", "summary": "There were 30 reports to police.",
          "bullets": ["Assaults: 10 in August; 89 this year, down 3% from 92."],
          "officer_section": "", "closing": "Call 911."}
    ur = {"headline": "تھارنکلف پارک، اگست ۲۰۲۶", "summary": "پولیس کو ۳۰ رپورٹیں موصول ہوئیں۔",
          "bullets": ["حملے: اگست میں ١٠؛ اس سال ٨٩، پچھلے سال ٩٢ سے ٣٪ کم۔"],
          "officer_section": "", "closing": "۹۱۱ پر کال کریں۔"}
    allowed = ["2026", "30", "10", "89", "92", "3", "911"]
    r = N.check_translation(en, ur, allowed)
    assert r["ok"], r


def test_translation_mismatch_detected():
    en = {"headline": "", "summary": "There were 30 reports; 12 last year.", "bullets": [],
          "officer_section": "", "closing": ""}
    tr = {"headline": "", "summary": "پولیس کو ۳۰ رپورٹیں، پچھلے سال ۲۱۔", "bullets": [],
          "officer_section": "", "closing": ""}
    r = N.check_translation(en, tr, ["30", "12", "21"])
    assert not r["ok"] and r["missing"] == ["12"] and r["extra"] == ["21"]
    assert "missing from translation: 12" in N.describe(r)


def test_english_not_allowed():
    r = N.check_english({"headline": "", "summary": "There were 31 reports.", "bullets": [],
                         "officer_section": "", "closing": ""}, ["30"])
    assert not r["ok"] and r["not_allowed"] == ["31"]


def test_highlight_marks_verified_and_unverified():
    html = str(N.highlight("12 and 13 <b>", ["12"], "Matches"))
    assert '<span class="num-ok" title="Matches">12</span>' in html
    assert '<span class="num-bad"' in html and "&lt;b&gt;" in html


# ---- script check ----------------------------------------------------------------------------

def test_real_gemma_urdu_output_devanagari_flagged():
    text = "اس مہینے پولیس میں 12 رپورٹیں موصول हुईं."
    issues = S.find_foreign(text, "ur")
    assert [i["text"] for i in issues] == ["हुईं"]
    assert issues[0]["script"] == "Devanagari"
    doc = {"headline": "", "summary": text, "bullets": [], "officer_section": "", "closing": ""}
    r = S.check_doc(doc, "ur")
    assert not r["ok"] and r["scripts"] == ["Devanagari"]


@pytest.mark.parametrize("lang,text", [
    ("ur", "اگست میں پولیس کو 30 رپورٹیں موصول ہوئیں۔"),
    ("ar", "في شهر أغسطس، وردت 30 بلاغًا للشرطة."),
    ("ps", "په اګست کې پولیسو ته 30 راپورونه ورکړل شول."),
    ("fa", "در ماه اوت، ۳۰ گزارش به پلیس داده شد."),
    ("ta", "ஆகஸ்ட் மாதத்தில் காவல்துறைக்கு 30 புகார்கள் வந்தன."),
    ("zh-Hant", "八月份，警方共收到 30 宗報案。"),
    ("zh-Hans", "八月份，警方共收到 30 起报案。"),
    ("ru", "В августе в полицию поступило 30 заявлений."),
    ("ko", "8월에 경찰에 30건의 신고가 접수되었습니다. 市民"),   # Han allowed in Korean
    ("fil", "Noong Agosto, may 30 ulat sa pulisya. 🚓"),
    ("pt-pt", "Em agosto, houve 30 participações à polícia."),
])
def test_clean_text_passes(lang, text):
    assert S.find_foreign(text, lang) == [], lang


@pytest.mark.parametrize("lang,text,bad", [
    ("zh-Hans", "八月份，警方共收到 30 起报案 reports。", "reports"),
    ("ru", "В августе 30 заявлений, 報告.", "報告"),
    ("ta", "ஆகஸ்ட் 30 புகார்கள் police", "police"),
    ("ko", "8월에 30건 신고 заявление", "заявление"),
    ("it", "Ad agosto 30 denunce полиция", "полиция"),
    ("ur", "August میں 30 رپورٹیں", "August"),                  # month names must be translated
    ("ur", "تھورنکليف پارک ۾ هيٺين شعبن", "هيٺين"),               # Sindhi letters in "Urdu"
    ("ar", "في شهر گست 30 بلاغًا", "گست"),                       # Persian/Urdu letter in Arabic
])
def test_foreign_script_flagged(lang, text, bad):
    assert bad in [i["text"] for i in S.find_foreign(text, lang)]


def test_latin_proper_nouns_from_source_allowed():
    src = "Thorncliffe Park safety update. Call TPS."
    assert S.find_foreign("Thorncliffe Park کی خبریں TPS", "ur", src) == []
    assert [i["text"] for i in S.find_foreign("Thorncliffe hello", "ur", src)] == ["hello"]


def test_cjk_month_names_are_not_counts():
    # Real gemma3:12b zh-Hant output style: "8月" = August, "10月13日" = October 13.
    assert N.extract_numbers("8月份共有 10 宗報案，下次會議在10月13日。") == ["10", "13"]
    assert N.extract_numbers("8월에 30건") == ["30"]
    assert N.extract_numbers("收到 30 宗報案") == ["30"]


def test_ukrainian_russian_and_bengali_letter_rules():
    from app.verify import script as S
    assert S.find_foreign("Звіти до поліції", "uk") == []
    assert [i["script"] for i in S.find_foreign("Отчёты в полицию", "uk")] == ["Russian letters, not Ukrainian"]
    assert [i["script"] for i in S.find_foreign("Звіти в полицию", "ru")] == ["Ukrainian letters, not Russian"]
    assert S.find_foreign("পুলিশের কাছে রিপোর্ট", "bn") == []
    assert S.find_foreign("পুলিশৰ কাছে", "bn")[0]["script"] == "Assamese letters, not Bengali"
    assert S.find_foreign("Báo cáo với cảnh sát", "vi") == [] and S.find_foreign("સ્થાનિક સહાય", "gu") == []
    assert S.find_foreign("Αναφορές στην αστυνομία", "el") == []
    assert S.find_foreign("Αναφορές полиция", "el")[0]["text"] == "полиция"


def test_new_languages_have_every_label_and_no_em_dashes():
    from app.languages import LABELS, LANGUAGES
    for code in ("vi", "pl", "bn", "el", "gu", "uk", "pa", "so"):
        assert code in LANGUAGES and set(LABELS[code]) == set(LABELS["en"]), code
        assert "416-808-2222" in LABELS[code]["emergency"] and "911" in LABELS[code]["emergency"]
        assert all("—" not in v for v in LABELS[code].values())


def test_vietnamese_month_names_are_not_counts():
    from app.verify import numbers as N
    assert N.extract_numbers("Báo cáo tháng 9 năm 2026: 12 vụ.") == ["2026", "12"]
    assert N.extract_numbers("Tháng 11, thứ Ba ngày 10") == ["10"]
    assert N.extract_numbers("9 tháng qua có 40 vụ") == ["9", "40"]        # "9 months": a count
    assert N.extract_numbers("tháng 13") == ["13"]                        # not a month


def test_punjabi_gurmukhi_with_danda_and_no_devanagari():
    from app.verify import script as S
    assert S.find_foreign("ਇਸ ਅਨੁਵਾਦ ਦੀ ਜਾਂਚ ਇੱਕ ਅਨੁਵਾਦਕ ਨੇ ਕੀਤੀ ਹੈ।", "pa") == []
    assert S.find_foreign("ਪੁਲਿਸ पुलिस", "pa")[0]["script"] == "Devanagari"


def test_meaning_check_flags_only_flipped_directions():
    from app.verify import meaning as M
    en = {"headline": "Fewer break-ins", "summary": "", "bullets": [
        "Assaults: 10 in August. 89 this year, down 3% from 92.", "Break-ins: 4 in August. 24 this year, compared with 9.",
        "Robberies: 2 in August. 11 this year, the same as 11."]}
    back = {"headline": "Fewer burglaries", "summary": "", "bullets": [
        "Attacks: 10 in August. 89 this year, up 3% from 92.", "Digging: 4 in August. 24 this year, up from 9.",
        "Robbery: 2 in August. 11 this year, equal to 11."]}
    issues = M.compare(en, back)
    assert [(i["field"], i["index"], i["en_dir"], i["back_dir"]) for i in issues] == [("bullets", 0, "down", "up")]
    # stale or missing back-translations are never used
    tr = {"headline": "x", "bullets": ["a"]}
    assert M.check({"doc": tr, "back": {"doc": back, "for": "old"}}, en) == {"checked": False, "ok": True, "issues": [], "stale": True}
    assert M.check({"doc": tr}, en)["checked"] is False
    assert M.check({"doc": tr, "back": {"doc": back, "for": M.doc_hash(tr)}}, en)["ok"] is False


def test_space_before_a_percentage_is_not_a_thousands_separator():
    """A fake translation put "26" next to "136%"; the space-thousands rule read 26136."""
    from app.verify import numbers as N
    assert N.extract_numbers("26 136%") == ["26", "136"]
    assert N.extract_numbers("26 136 %") == ["26", "136"]
    assert N.extract_numbers("1 234 signalements") == ["1234"]


def test_place_names_are_not_group_words():
    """"Black Creek" is a neighbourhood: it once stopped Black Creek getting an automatic bulletin."""
    from app.verify import content
    doc = {"headline": "Black Creek: safety update", "summary": "In September, there were 46 reports to police in "
           "Black Creek. A report to police does not mean anyone was charged.", "bullets": [], "officer_section": "", "closing": ""}
    found = [f for f in content.check(doc, {"neighbourhood": {"name": "Black Creek"}})["findings"] if f["title"] == "Mentions a group of people"]
    assert not found
    doc["officer_section"] = "Black youths were stopped."           # a real statement about people is still caught
    found = [f for f in content.check(doc, {"neighbourhood": {"name": "Black Creek"}})["findings"] if f["title"] == "Mentions a group of people"]
    assert found and found[0]["matches"] == ["black", "youths"]
    assert content.officer_warnings({"priority": "Bike thefts near Black Creek Drive in Black Creek."}) == []


# ---------------------------------------------------------------- translation guard rails (back-translation)

EN = {"headline": "Thorncliffe Park: safety update for September 2026",
      "summary": "In September, there were 23 reports to police in Thorncliffe Park. A report to police does not mean anyone was charged.",
      "bullets": ["Theft from vehicles: 8 reports in September. 28 this year.",
                  "Assaults: 6 reports in September. 95 this year, down 7% from 102 by this time last year."],
      "officer_section": "", "closing": "In an emergency, call 911."}
CATS = ["Theft from Motor Vehicle", "Assault"]


def _q(code, tr, back):
    from app.verify import quality
    return quality.check(code, EN, tr, back, CATS)


def _tr(**over):
    """A 'translation': every field different from the English unless overridden."""
    t = {"headline": "T1", "summary": "T2", "bullets": ["T3", "T4"], "officer_section": "", "closing": "T5"}
    t.update(over)
    return t


def test_guard_not_charged_losing_its_not_blocks():
    back = dict(EN, summary="In September, there were 23 reports to police. A report to police means someone was charged.")
    r = _q("xx", _tr(), back)
    assert [b["kind"] for b in r["blocking"]] == ["negation"]
    back = dict(EN, summary="In September, there were 23 reports to police in Thorncliffe Park.")   # reminder gone
    assert [f["kind"] for f in _q("xx", _tr(), back)["flags"]] == ["lost_reminder"]
    assert _q("xx", _tr(), EN) == {"blocking": [], "flags": []}


def test_guard_alarming_words_added_block():
    back = dict(EN, headline="Thorncliffe Park: a dangerous September 2026 safety update")
    r = _q("xx", _tr(), back)
    assert r["blocking"] and r["blocking"][0]["kind"] == "alarming" and "dangerous" in r["blocking"][0]["text"]


def test_guard_category_drift_flagged_unless_the_label_is_locked():
    tr = _tr(bullets=["Vol de véhicules: 8 rapports en septembre. 28 cette année.", "T4"])
    back = dict(EN, bullets=["Vehicle theft: 8 reports in September. 28 this year.", EN["bullets"][1]])
    r = _q("xx", tr, back)                                         # no glossary: the drift is flagged
    assert [f["kind"] for f in r["flags"]] == ["category"] and "theft from motor vehicle" in r["flags"][0]["text"]
    tr["bullets"][0] = "Kradzieże z pojazdów: 8 zgłoszeń we wrześniu. 28 w tym roku."
    assert not _q("pl", tr, back)["flags"]                         # Polish label is the glossary's own: read-back noise


def test_guard_untranslated_lost_content_and_korean_register():
    r = _q("xx", _tr(bullets=[EN["bullets"][0], "T4"]), None)
    assert [f["kind"] for f in r["flags"]] == ["untranslated"]
    back = dict(EN, bullets=[EN["bullets"][0], "Assaults: 6 reports in September."])
    assert [f["kind"] for f in _q("xx", _tr(), back)["flags"]] == ["length"]
    back = dict(EN, headline="The weather is nice and the beach is open for picnics.")
    assert [f["kind"] for f in _q("xx", _tr(), back)["flags"]] == ["lost_content"]
    assert [f["kind"] for f in _q("ko", _tr(closing="긴급 상황에서는 911에 전화해."), None)["flags"]] == ["register"]
    assert not _q("ko", _tr(closing="긴급 상황에서는 911에 전화하세요."), None)["flags"]


def test_guard_runs_inside_verification_and_blocks_publishing():
    from app.verify import checks, meaning
    tr = _tr()
    rec = {"facts": {"allowed_numbers": ["2026", "23", "8", "28", "6", "95", "7", "102", "911"], "display": CATS, "categories": []},
           "english": EN, "languages": ["fr"],
           "translations": {"fr": {"doc": tr, "back": {"doc": dict(EN, summary="A report to police means someone was charged. 23"),
                                                       "for": meaning.doc_hash(tr)}}}}
    v = checks.verify_language(rec, "fr")
    assert not v["ok"] and "not charged" in v["summary"]


def _fake_scores(monkeypatch, score, tr_len=None):
    from app.verify import similarity as SIM
    monkeypatch.setattr(SIM, "thresholds", lambda: {"xx": {"bullet": {"score": 0.80, "ratio": [0.9, 1.3]},
                                                           "other": {"score": 0.80, "ratio": [0.9, 1.3]}}})

    def scores(en, tr):
        out = []
        for i, e in enumerate(en.get("bullets") or []):
            out.append({"field": "bullets", "index": i, "score": score, "en": e, "tr": "x" * (tr_len or len(e))})
        return out
    monkeypatch.setattr(SIM, "line_scores", scores)


def test_meaning_score_two_levels(monkeypatch):
    from app.verify import quality
    _fake_scores(monkeypatch, 0.78)                                   # just below: ask a person
    f = [x for x in quality.check("xx", EN, _tr(), None, CATS)["flags"] if x["kind"] == "meaning_score"]
    assert f and not f[0]["strong"]
    _fake_scores(monkeypatch, 0.60)                                   # far below: strong
    f = [x for x in quality.check("xx", EN, _tr(), None, CATS)["flags"] if x["kind"] == "meaning_score"]
    assert f and f[0]["strong"]
    _fake_scores(monkeypatch, 0.95, tr_len=20)                        # meaning fine, but half the line is missing
    f = [x for x in quality.check("xx", EN, _tr(), None, CATS)["flags"] if x["kind"] == "length_direct"]
    assert f and f[0]["strong"] and "shorter" in f[0]["text"]
    _fake_scores(monkeypatch, 0.95)
    assert not quality.check("xx", EN, _tr(), None, CATS)["flags"]


def test_meaning_score_skipped_when_the_model_is_not_there(monkeypatch):
    from app.verify import quality, similarity as SIM
    monkeypatch.setattr(SIM, "embed", lambda texts: None)
    assert SIM.line_scores(EN, _tr()) is None
    assert quality.check("ur", EN, _tr(), None, CATS) == {"blocking": [], "flags": []}
