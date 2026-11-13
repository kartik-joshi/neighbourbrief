import json

from app.ai import llm, translate

EN = {
    "headline": "Thorncliffe Park safety update for August 2026",
    "summary": "In August, there were 30 reports to police. A report does not mean anyone was charged.",
    "bullets": ["Assaults: 10 reports in August. 89 so far this year, down 3% from 92.",
                "Thefts over $5,000: 2 reports in August."],
    "officer_section": "This month's priority: Car break-ins near the plaza.",
    "closing": "In an emergency, call 911.",
}


def test_protect_and_restore_roundtrip():
    p, originals = translate.protect(EN)
    assert "⟦N0⟧" in p["headline"] and "2026" not in json.dumps(p)
    assert originals == ["2026", "30", "10", "89", "3", "92", "5,000", "2", "911"]
    back, missing, dup = translate.restore(p, originals)
    assert back == EN and not missing and not dup


def test_restore_tolerates_bracket_variants_and_reports_missing():
    p, originals = translate.protect({"headline": "a 1 b 2", "summary": "", "bullets": [],
                                      "officer_section": "", "closing": ""})
    out, missing, dup = translate.restore({"headline": "x [[N0]] y", "summary": "", "bullets": [],
                                           "officer_section": "", "closing": ""}, originals)
    assert out["headline"] == "x 1 y" and missing == [1]


def _as_urdu(protected, devanagari=False):
    verb = "हुईं" if devanagari else "ہوئیں"
    return json.dumps({
        "headline": "اگست ⟦N0⟧ کے لیے Thorncliffe Park کی حفاظتی خبریں",
        "summary": f"اگست میں پولیس کو ⟦N1⟧ رپورٹیں موصول {verb}۔ رپورٹ کا مطلب الزام نہیں۔",
        "bullets": ["حملے: اگست میں ⟦N2⟧ رپورٹیں۔ اس سال ⟦N3⟧، پچھلے سال ⟦N5⟧ سے ⟦N4⟧% کم۔",
                    "⟦N6⟧ ڈالر سے زیادہ کی چوری: اگست میں ⟦N7⟧ رپورٹیں۔"],
        "officer_section": "اس مہینے کی ترجیح: پلازہ کے قریب گاڑیوں میں چوری۔",
        "closing": "ہنگامی صورت میں ⟦N8⟧ پر کال کریں۔",
    }, ensure_ascii=False)


def test_llm_translation_script_retry(monkeypatch):
    calls = []

    def fake_complete(system, user, json=False, **kw):
        calls.append(user)
        return "Here you go:\n" + _as_urdu(None, devanagari=len(calls) == 1) + "\nThanks"
    monkeypatch.setattr(llm, "complete", fake_complete)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ur")
    assert len(calls) == 2 and "हुईं" in calls[1]  # retry names the wrong-script word
    assert info["script_ok"] and "हुईं" not in doc["summary"]
    assert "30" in doc["summary"] and "5,000" in doc["bullets"][1]


def test_llm_translation_still_mixed_after_retry_is_flagged(monkeypatch):
    # Uses a Devanagari *content* word: common function words like "हुईं" are now repaired
    # (see test_devanagari_function_words_repaired_in_urdu_after_retry).
    unrepairable = _as_urdu(None, devanagari=True).replace("हुईं", "प्राप्त")
    monkeypatch.setattr(llm, "complete", lambda s, u, json=False, **kw: unrepairable)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ur")
    assert info["attempts"] == 2 and not info["script_ok"]
    assert "Azure Translator" in info["suggestion"]


def test_model_down_raises_clear_error(monkeypatch):
    def down(*a, **k):
        raise llm.LLMUnavailable("connection refused")
    monkeypatch.setattr(llm, "complete", down)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    try:
        translate.translate_doc(EN, "ur")
        assert False, "expected TranslationFailed"
    except translate.TranslationFailed as e:
        assert "Ollama" in str(e)


def test_engine_choice(monkeypatch):
    monkeypatch.setenv("TRANSLATOR", "auto")
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    assert translate.engine_for()[0] == "llm"
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "abc")
    assert translate.engine_for()[0] == "azure"
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    assert translate.engine_for()[0] == "llm"
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    monkeypatch.setenv("TRANSLATOR", "azure")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    engine, note = translate.engine_for()
    assert engine == "llm" and "AZURE_TRANSLATOR_KEY" in note


def test_azure_html_protection_roundtrip():
    h = translate._azure_html("Assaults: 10 < 12 & 5,000")
    assert h == ('Assaults: <span class="notranslate">10</span> &lt; '
                 '<span class="notranslate">12</span> &amp; <span class="notranslate">5,000</span>')
    assert translate._from_azure_html(h) == "Assaults: 10 < 12 & 5,000"


def test_azure_request_shape(monkeypatch):
    import httpx
    seen = {}

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, body=json)
        out = [{"translations": [{"text": item["Text"].replace("Assaults", "حملے"), "to": "ur"}]} for item in json]
        return httpx.Response(200, json=out, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "k")
    monkeypatch.setenv("AZURE_TRANSLATOR_REGION", "canadacentral")
    monkeypatch.setenv("TRANSLATOR", "azure")
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    doc, info = translate.translate_doc(EN, "ur")
    assert info["engine"] == "azure"
    assert seen["params"]["textType"] == "html" and seen["params"]["to"] == "ur"
    assert seen["headers"]["Ocp-Apim-Subscription-Key"] == "k"
    assert seen["headers"]["Ocp-Apim-Subscription-Region"] == "canadacentral"
    assert doc["bullets"][0].startswith("حملے: 10")


def test_protect_names_and_numbers_together():
    doc = dict(EN, officer_section="Thorncliffe Park had 10 visits.")
    p, originals = translate.protect(doc, ["Thorncliffe Park"])
    assert p["headline"] == "⟦P0⟧ safety update for August ⟦N0⟧"
    assert "⟦P0⟧ had ⟦N" in p["officer_section"]
    back, missing, dup = translate.restore(p, originals, ["Thorncliffe Park"])
    assert back == doc and not missing and not dup


def test_digits_typed_next_to_placeholder_trigger_retry(monkeypatch):
    """Real gemma3 Russian output: '21 октября 21' — the model kept the token AND typed the digits."""
    calls = []
    good = {"headline": "⟦P0⟧: обновление за август ⟦N0⟧", "summary": "В августе ⟦N1⟧ заявлений.",
            "bullets": ["Нападения: ⟦N2⟧, ⟦N3⟧, ⟦N4⟧%, ⟦N5⟧.", "Кражи свыше $⟦N6⟧: ⟦N7⟧."],
            "officer_section": "Приоритет: кражи из машин у плазы.", "closing": "Звоните ⟦N8⟧."}
    bad = dict(good, summary="В августе 30 ⟦N1⟧ заявлений.")

    def fake(system, user, json=False, **kw):
        import json as _j
        calls.append(user)
        return _j.dumps(bad if len(calls) == 1 else good, ensure_ascii=False)
    monkeypatch.setattr(llm, "complete", fake)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ru", names=["Thorncliffe Park"])
    assert len(calls) == 2 and "numbers changed" in calls[1]
    assert doc["summary"] == "В августе 30 заявлений." and doc["headline"].startswith("Thorncliffe Park")


def test_untranslated_month_filled_from_cldr_after_retry(monkeypatch):
    """Real gemma3 Pashto output kept 'AUGUST' in Latin after the retry."""
    out = {"headline": "د ⟦P0⟧ خبرونه AUGUST ⟦N0⟧", "summary": "په AUGUST کې ⟦N1⟧ راپورونه.",
           "bullets": ["بریدونه: ⟦N2⟧، ⟦N3⟧، ⟦N4⟧٪، ⟦N5⟧.", "غلا $⟦N6⟧: ⟦N7⟧."],
           "officer_section": "لومړیتوب: د موټرو غلا.", "closing": "⟦N8⟧ ته زنګ ووهئ."}
    monkeypatch.setattr(llm, "complete", lambda s, u, json=False, **kw: __import__("json").dumps(out, ensure_ascii=False))
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ps", names=["Thorncliffe Park"])
    assert info["attempts"] == 2 and info["script_ok"]
    assert "اګست" in doc["headline"] and "AUGUST" not in doc["summary"]
    assert "CLDR" in info["note"]


def test_other_latin_words_are_not_repaired(monkeypatch):
    out = {"headline": "خبرونه ⟦N0⟧ hello", "summary": "⟦N1⟧", "bullets": ["⟦N2⟧ ⟦N3⟧ ⟦N4⟧ ⟦N5⟧", "⟦N6⟧ ⟦N7⟧"],
           "officer_section": "لومړیتوب", "closing": "⟦N8⟧"}
    monkeypatch.setattr(llm, "complete", lambda s, u, json=False, **kw: __import__("json").dumps(out, ensure_ascii=False))
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ps")
    assert not info["script_ok"] and "suggestion" in info


def test_devanagari_function_words_repaired_in_urdu_after_retry(monkeypatch):
    """Real gemma3 Urdu output (loop 11): 'हुईं' persisted after the retry."""
    monkeypatch.setattr(llm, "complete", lambda s, u, json=False, **kw: _as_urdu(None, devanagari=True))
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ur")
    assert info["attempts"] == 2 and info["script_ok"]
    assert "ہوئیں" in doc["summary"] and "हुईं" not in doc["summary"]
    assert info["devanagari_fixed"] == ["हुईं"] and "हुईं → ہوئیں" in info["note"]
    assert "suggestion" not in info


def test_unknown_devanagari_word_still_blocks(monkeypatch):
    bad = _as_urdu(None).replace("رپورٹیں", "रिपोर्टें")  # a content word, not in the table
    monkeypatch.setattr(llm, "complete", lambda s, u, json=False, **kw: bad)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ur")
    assert not info["script_ok"] and "रिपोर्टें" in doc["summary"]


def test_devanagari_repair_only_for_urdu_and_whole_words():
    fixed_doc, fixed = translate.repair_devanagari_urdu({"summary": "رپورٹیں हुईं। हुईंगे", "bullets": ["है"]})
    assert fixed_doc["summary"] == "رپورٹیں ہوئیں۔ हुईंगे" and fixed_doc["bullets"] == ["ہے"]
    assert not translate._repairable({"script": "Devanagari", "text": "है"}, "ps")


def test_glossary_in_prompt_and_usage_recorded(monkeypatch):
    from app import glossary as G
    seen = {}

    def fake(system, user, json=False, **kw):
        seen["system"] = system
        return _as_urdu(None).replace("حملے", "حملے")  # uses the glossary term for Assaults
    monkeypatch.setattr(llm, "complete", fake)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    doc, info = translate.translate_doc(EN, "ur")
    assert '"Assaults" → "حملے"' in seen["system"]
    assert "Thefts over" not in seen["system"]  # no digits in glossary terms
    assert info["glossary"] == {"used": 1, "expected": 1}
    assert G.terms_for("xx", "Assaults") == {}
    for row in G.GLOSSARY.values():
        assert not any(ch.isdigit() for t in row for ch in t)


def test_glossary_terms_pass_their_own_script_check():
    from app import glossary as G
    from app.verify import script as S
    for code, row in G.GLOSSARY.items():
        for term in row:
            assert S.find_foreign(term, code) == [], (code, term)


def test_azure_percent_protected_and_spaces_restored_for_arabic_script():
    h = translate._azure_html("down 3% from 92")
    assert '<span class="notranslate">3%</span>' in h and '<span class="notranslate">92</span>' in h
    # real Azure Urdu output glued text to the protected number
    assert translate._from_azure_html('ہنگامی صورت میں، <span class="notranslate">911</span>کال کریں۔', "ur") == "ہنگامی صورت میں، 911 کال کریں۔"
    assert translate._from_azure_html('اس سال <span class="notranslate">3%</span>کم', "ur") == "اس سال 3% کم"
    # CJK/Korean: a number touching the next character is correct — leave it
    assert translate._from_azure_html('<span class="notranslate">8</span>월', "ko") == "8월"
    assert translate._from_azure_html('<span class="notranslate">10</span>月', "zh-Hant") == "10月"


def test_azure_adjacent_numbers_never_merge():
    """Real Azure zh-Hant output put '92' and '3%' side by side → read as '923%'."""
    raw = '比去年同期<span class="notranslate">92</span><span class="notranslate">3%</span>。'
    out = translate._from_azure_html(raw, "zh-Hant")
    assert "923" not in out and "92 3%" in out
    assert N_extract(out) == ["92", "3"]



def test_azure_adjacent_three_digit_numbers_stay_two_numbers():
    """Real Azure ko output: '312' and '321' side by side, and a plain space before three digits is a
    thousands separator (French style), so the checker read 312321."""
    raw = '올해<span class="notranslate">312</span> <span class="notranslate">321</span> 건으로 작년 이맘때보다 <span class="notranslate">3%</span> 줄었습니다.'
    assert N_extract(translate._from_azure_html(raw, "ko")) == ["312", "321", "3"]


def test_azure_clock_time_is_one_protected_unit():
    """Protected as '6' and '30', Azure wrote '6시: 오후30 시' for '6:30 p.m.'."""
    h = translate._azure_html("Thursday, November 12 at 6:30 p.m.")
    assert '<span class="notranslate">6:30</span>' in h and '<span class="notranslate">12</span>' in h
    assert translate._from_azure_html(h) == "Thursday, November 12 at 6:30 p.m."

def N_extract(t):
    from app.verify import numbers as N
    return N.extract_numbers(t)


def test_azure_429_is_retried(monkeypatch):
    import httpx
    calls = []

    def fake_post(url, params=None, headers=None, json=None, timeout=None):
        calls.append(1)
        req = httpx.Request("POST", url)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=req)
        return httpx.Response(200, json=[{"translations": [{"text": i["Text"], "to": "ur"}]} for i in json], request=req)
    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(translate.time, "sleep", lambda s: None)
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "k")
    doc, info = translate.translate_azure(EN, "ur")
    assert len(calls) == 2 and info["engine"] == "azure"


def test_month_year_order_repair():
    from app.ai import translate as T
    doc = {"headline": "Milliken：9月2026日安全更新", "bullets": ["九月有10報告。"], "summary": "2026年9月"}
    out, n = T.repair_month_year(doc, "zh-Hant")
    assert n == 1 and out["headline"] == "Milliken：2026年9月安全更新" and out["summary"] == "2026年9月"
    assert T.repair_month_year({"headline": "밀리컨: 9월 2026일 안전 소식"}, "ko")[0]["headline"] == "밀리컨: 2026년 9월 안전 소식"
    assert T.repair_month_year({"headline": "9月2026日"}, "ur")[1] == 0          # other languages untouched
    assert T.repair_month_year({"headline": "13月2026日"}, "zh-Hans")[1] == 0     # not a month


def test_azure_category_label_sent_pre_translated():
    """Azure wrote "Theft from vehicles" as "Vol de véhicules" (vehicle theft) in French: the label now
    goes over already translated from the glossary and protected, so the category can't change."""
    h = translate._azure_text("Theft from vehicles: 5 reports in September. 25 this year.", "fr")
    assert h.startswith('<span class="notranslate">Vols dans des véhicules</span>:')
    assert translate._from_azure_html(h, "fr").startswith("Vols dans des véhicules: 5 reports")
    # a language without a glossary row, or a line that isn't a category line: unchanged
    assert translate._azure_text("Assaults: 3 reports.", "xx") == translate._azure_html("Assaults: 3 reports.")
    assert translate._azure_text("In September, there were 3 reports.", "fr") == translate._azure_html("In September, there were 3 reports.")


def test_french_is_a_language():
    from app.languages import LANGUAGES, labels
    assert "fr" in LANGUAGES and labels("fr")["title"] == "Bulletin de sécurité du quartier"
    assert set(labels("en")) == set(labels("fr"))
