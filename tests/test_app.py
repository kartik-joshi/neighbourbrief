"""App-level tests with a fake model and fake TPS data (fast, no network).

Edge cases: zero reports, no NCO info, empty officer fields, very long officer text, emoji / Urdu
officer text, model down, Azure key missing, TPS unreachable, publish gate, offline snapshot mode.
"""
import datetime as dt
import html
import json
import socket
import time
from zoneinfo import ZoneInfo

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from app import main, store
from app.ai import llm
from app.data import tps

TODAY = dt.date(2026, 9, 29)


def ms(y, m, d):
    return int(dt.datetime(y, m, d, tzinfo=ZoneInfo("America/Toronto")).timestamp() * 1000)


NCO_ROWS = [
    {"Neighbourh": "55", "Neighbour2": "Thorncliffe Park", "Neighbour3": "Thorncliffe Park (55)", "DIV": "D53",
     "UNIT_NAME": "53 Division", "NCO": "Yes", "Rank": "PC", "FirstName": "Pat", "LastName": "EXAMPLE",
     "WorkPhoneN": "416-555-0100", "EmailAddre": "1@example.ca", "PHONE": "416-808-5300", "ADDRESS": "75 Eglinton Av W", "CITY": "Toronto"},
    {"Neighbourh": "130", "Neighbour2": "Milliken", "Neighbour3": "Milliken (130)", "DIV": "D42",
     "UNIT_NAME": "42 Division", "NCO": "No", "FirstName": "No NCO. Call Division", "LastName": "No NCO. Call Division",
     "Rank": "No NCO. Call Division", "EmailAddre": "42division@example.ca", "PHONE": "416-808-4200", "ADDRESS": "242 Milner Av E", "CITY": "Toronto"},
]


ASSET_ROWS = [
    {"ResourceAgencyNum": 1, "PublicName": "Thorncliffe Food Bank - Thorncliffe Food Bank", "Category": "Food",
     "PhysicalAddress1": "1 Leaside Park Dr", "Phone1Number": "647-428-9755", "AgencyStatus": "Active"},
    {"ResourceAgencyNum": 2, "PublicName": "Don Valley Community Legal Services - Legal clinic", "Category": "Legal Services (Abuse,Safety)",
     "PhysicalAddress1": "1 Leaside Park Dr", "Phone1Number": "", "PhoneNumberBusinessLine": "416-441-1764 ext 12", "AgencyStatus": "Active"},
    {"ResourceAgencyNum": 3, "PublicName": "Don Valley Community Legal Services - Second program", "Category": "Health",
     "PhysicalAddress1": "1 Leaside Park Dr", "Phone1Number": "416-441-1764", "AgencyStatus": "Active"},  # same org: deduped
    {"ResourceAgencyNum": 4, "PublicName": "Closed Place", "Category": "Food", "AgencyStatus": "Inactive"},
    {"ResourceAgencyNum": 5, "PublicName": "Tiny Tots Daycare", "Category": "General/Other", "AgencyStatus": "Active"},
    {"ResourceAgencyNum": 6, "PublicName": "Seniors Club", "Category": "Seniors", "PhysicalAddress1": "10 Overlea Blvd", "AgencyStatus": "Active"},
    {"ResourceAgencyNum": 7, "PublicName": "Health Centre", "Category": "Health", "PhysicalAddress1": "45 Overlea Blvd", "Phone1Number": "416-696-9836", "AgencyStatus": "Active",
     "Latitude": 43.7055, "Longitude": -79.3452},
    {"ResourceAgencyNum": 8, "PublicName": "Second Food Bank", "Category": "Food", "PhysicalAddress1": "2 Overlea Blvd", "AgencyStatus": "Active"},
]


BOUNDARIES = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"id": "055", "name": "Thorncliffe Park"},  # a square with a hole
     "geometry": {"type": "Polygon", "coordinates": [[[-79.36, 43.70], [-79.33, 43.70], [-79.33, 43.72], [-79.36, 43.72], [-79.36, 43.70]],
                                                     [[-79.35, 43.71], [-79.349, 43.71], [-79.349, 43.711], [-79.35, 43.711], [-79.35, 43.71]]]}},
    {"type": "Feature", "properties": {"id": "130", "name": "Milliken"},
     "geometry": {"type": "MultiPolygon", "coordinates": [[[[-79.30, 43.80], [-79.26, 43.80], [-79.26, 43.83], [-79.30, 43.83], [-79.30, 43.80]]]]}},
]}


COLLISIONS = (
    [{"EVENT_UNIQUE_ID": f"T{i}", "OCC_YEAR": "2026", "OCC_MONTH": "August", "INJURY_COLLISIONS": "YES" if i < 3 else "NO",
      "PEDESTRIAN": "YES" if i == 0 else "NO", "BICYCLE": "NO", "FATALITIES": 0} for i in range(12)]
    + [{"EVENT_UNIQUE_ID": f"J{i}", "OCC_YEAR": "2026", "OCC_MONTH": "July", "INJURY_COLLISIONS": "NO", "PEDESTRIAN": "NO",
        "BICYCLE": "YES" if i == 0 else "NO", "FATALITIES": 1 if i == 0 else 0} for i in range(20)]
    + [{"EVENT_UNIQUE_ID": f"S{i}", "OCC_YEAR": "2026", "OCC_MONTH": "September", "INJURY_COLLISIONS": "NO"} for i in range(5)]
    + [{"EVENT_UNIQUE_ID": f"L{i}", "OCC_YEAR": "2025", "OCC_MONTH": "March", "INJURY_COLLISIONS": "NO"} for i in range(40)]
    + [{"EVENT_UNIQUE_ID": "T0", "OCC_YEAR": "2026", "OCC_MONTH": "August", "INJURY_COLLISIONS": "YES", "PEDESTRIAN": "YES"}]
)
TREND_ROWS = [{"HOOD_ID": 55.0, "AREA_NAME": "Thorncliffe Park", "ASSAULT_2024": 145, "ASSAULT_2025": 152,
               "ROBBERY_2024": 9, "ROBBERY_2025": None, "POPULATION_2025": 23826}]


def ytd_rows(hid):
    if hid == "130":
        return []  # quiet: zero reports
    rows = [{"EVENT_UNIQUE_ID": f"A{i}", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 3, 2), "DIVISION": "D53"} for i in range(12)]
    rows += [{"EVENT_UNIQUE_ID": f"B{i}", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 8, 5), "DIVISION": "D53"} for i in range(4)]
    rows += [{"EVENT_UNIQUE_ID": "C1", "CRIME_TYPE": "Break and Enter", "REPORT_DATE_AGOL": ms(2026, 8, 9), "DIVISION": "D53"}]
    return rows


def mci_rows(hid, year):
    if hid == "130":
        return []
    return [{"EVENT_UNIQUE_ID": f"P{i}", "CSI_CATEGORY": "Assault", "REPORT_DATE": ms(2025, 4, 1)} for i in range(20)]


@pytest.fixture()
def fake_tps(monkeypatch):
    monkeypatch.setattr(tps, "fetch_nco_all", lambda: NCO_ROWS)
    monkeypatch.setattr(tps, "fetch_ytd", ytd_rows)
    monkeypatch.setattr(tps, "fetch_mci_year", mci_rows)
    def no_extra(key, hid, year):  # the two extra last-year datasets: tested on their own
        raise tps.TPSUnavailable("not in this fixture")
    monkeypatch.setattr(tps, "fetch_category_year", no_extra)
    monkeypatch.setattr(tps, "mci_fields", lambda: {"category": "CSI_CATEGORY", "report_date": "REPORT_DATE",
                                                      "report_year": "REPORT_YEAR", "hood": "HOOD_158"})
    monkeypatch.setattr(tps, "data_freshness_ms", lambda: ms(2026, 9, 29))
    monkeypatch.setattr(tps, "fetch_assets", lambda hid: ASSET_ROWS if hid == "055" else [])
    from app.data import facts
    monkeypatch.setattr(facts, "effective_today", lambda hood_id=None: TODAY)
    from app import geo
    monkeypatch.setattr(geo, "boundaries", lambda: BOUNDARIES)
    from app.data import profile
    monkeypatch.setattr(profile, "fetch_collisions", lambda hid, year: COLLISIONS if hid == "055" else [])
    monkeypatch.setattr(profile, "fetch_trend_all", lambda: TREND_ROWS)
    monkeypatch.setattr(profile, "_updated", lambda layer: "October 8, 2026")
    from app.data import health
    monkeypatch.setattr(health, "_schema", lambda service, layer: {"fields": [{"name": n} for d in health.DATASETS if d[3] == service
                                                                              for n in [a.split("|")[0] for a in d[5]]]})
    monkeypatch.setattr(health, "_stats", lambda s, l, d: {"count": 10})


@pytest.fixture()
def tmp_store(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "BULLETIN_DIR", tmp_path / "bulletins")
    monkeypatch.setattr(tps, "CACHE_DIR", tmp_path / "cache")
    from app import auto
    monkeypatch.setattr(auto, "AUTO_DIR", tmp_path / "auto")
    from app import feedback
    monkeypatch.setattr(feedback, "FEEDBACK_DIR", tmp_path / "feedback")
    from app import usage
    monkeypatch.setattr(usage, "USAGE_DIR", tmp_path / "usage")
    from app import ratelimit
    ratelimit.clear()
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "")
    monkeypatch.setenv("TRANSLATOR", "auto")
    from app.ai import translate
    monkeypatch.setattr(translate, "azure_supported_codes", lambda: None)
    llm._status_cache.update(t=0.0, value=None)
    return tmp_path


def urdu_translator(system, user, json=False, **kw):
    """Fake model: English JSON in → 'Urdu' JSON out, keeping the ⟦N⟧ placeholders."""
    import json as _json
    import re
    data = _json.loads(user.split("\n\n")[0]) if user.strip().startswith("{") else None
    if data is None:  # writer call
        raise llm.LLMUnavailable("writer not faked in this test")
    def tr(t):
        toks = re.findall(r"⟦N\d+⟧%?", t)  # keep a percent sign with its number, like a real translation
        # a word between numbers, as in any real sentence ("16 111" would rightly read as one number, 16 111)
        return ("خبر " + " اور ".join(toks) + "۔").strip()
    return _json.dumps({k: ([tr(x) for x in v] if isinstance(v, list) else tr(v)) for k, v in data.items()},
                       ensure_ascii=False)


def model_down(*a, **k):
    raise llm.LLMUnavailable("Connection refused (Ollama stopped)")


@pytest.fixture()
def client(fake_tps, tmp_store):
    return TestClient(main.app)


def draft(client, **form):
    data = {"neighbourhood": "Thorncliffe Park", "priority": "Car break-ins near the plaza.",
            "action_taken": "More evening patrols.", "meeting_note": "", **form}
    r = client.post("/draft", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    return r.headers["location"].rsplit("/", 1)[-1]


def test_full_flow_with_model_down_uses_template_and_clear_message(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, languages=["ur"])
    rec = store.load(bid)
    assert rec["draft_source"] == "template" and "Ollama" in rec["draft_note"]
    page = client.get(f"/review/{bid}").text
    assert "Template draft" in page and "Ollama" in page
    # translation can't run either: clear error on the tab, English still publishable
    r = client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"}, follow_redirects=False)
    assert r.status_code == 303
    rec = store.load(bid)
    assert rec["translations"]["ur"]["doc"] is None
    assert "Ollama" in rec["translations"]["ur"]["error"]
    assert "ur" in main.checks.publish_blocked(rec)
    assert "isn't available" in html.unescape(client.get(f"/publish/{bid}").text)


def test_full_flow_llm_translation_without_azure_key(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)  # writer falls back; translator works
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    rec = store.load(bid)
    assert rec["translations"]["ur"]["engine"] == "llm"
    assert rec["verification"]["ur"]["ok"], rec["verification"]["ur"]
    assert main.checks.publish_blocked(rec) == []
    r = client.post(f"/publish/{bid}", follow_redirects=False)
    assert r.status_code in (302, 303), r.text
    assert store.load(bid)["status"] == "published"
    page = client.get(f"/publish/{bid}?lang=ur").text
    assert 'dir="rtl"' in page and 'lang="ur"' in page
    assert "AI-assisted translation, not yet reviewed by a human translator. This translation may contain mistakes." in page
    assert "wa.me/?text=" in page and "mailto:?subject=" in page
    assert client.get(f"/bulletin/{bid}/ur").status_code == 200


def test_publish_gate_blocks_number_mismatch_until_manual_ok(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    rec = store.load(bid)
    client.post(f"/review/{bid}/save", data={**rec["english"], "bullets": "\n".join(rec["english"]["bullets"]),
                                             "summary": rec["english"]["summary"] + " There were 999 reports."})
    rec = store.load(bid)
    assert not rec["verification"]["en"]["ok"] and rec["verification"]["en"]["numbers"]["not_allowed"] == ["999"]
    assert 'class="num-bad"' in client.get(f"/review/{bid}").text
    client.post(f"/publish/{bid}", data={}, follow_redirects=False)
    assert store.load(bid)["status"] == "draft"
    client.post(f"/publish/{bid}", data={"manual_ok": "1"}, follow_redirects=False)
    rec = store.load(bid)
    assert rec["status"] == "published" and rec["manual_override"]


def test_zero_reports_and_no_nco(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, neighbourhood="Milliken", priority="", action_taken="")
    rec = store.load(bid)
    assert rec["facts"]["totals"]["month"] == 0
    assert 3 <= len(rec["english"]["bullets"]) <= 6
    assert "no reports to police" in rec["english"]["summary"]
    assert rec["english"]["officer_section"] == ""
    page = client.get(f"/publish/{bid}").text
    assert "Contact your police division" in page and "42 Division" in page
    assert rec["verification"]["en"]["ok"]


def test_no_nco_info_at_all(client, monkeypatch):
    monkeypatch.setattr(tps, "fetch_nco_all", lambda: [])
    monkeypatch.setattr(tps, "fetch_neighbourhood_list_from_ytd",
                        lambda: [{"HOOD_158": "055", "NEIGHBOURHOOD_158": "Thorncliffe Park (55)", "DIVISION": "D53"}])
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    rec = store.load(bid)
    assert rec["facts"]["nco"] is None
    assert client.get(f"/publish/{bid}").status_code == 200


def test_very_long_officer_text_is_shortened_with_warning(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    long = "We walked the plaza every evening and talked to shop owners about lighting. " * 20
    bid = draft(client, priority=long)
    rec = store.load(bid)
    assert len(rec["officer"]["priority"]) <= 401
    assert rec["officer_warnings"]
    assert "shortened" in client.get(f"/review/{bid}").text


def test_officer_text_with_emoji_and_urdu(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, priority="🚗 پلازہ کی پارکنگ میں گاڑیوں سے چوری", action_taken="شام کی گشت 🚓 بڑھا دی")
    rec = store.load(bid)
    assert "🚗" in rec["english"]["officer_section"] and "پلازہ" in rec["english"]["officer_section"]
    assert rec["verification"]["en"]["ok"]
    assert client.get(f"/review/{bid}").status_code == 200


def test_tps_unreachable_shows_clear_error(client, monkeypatch):
    def down(*a, **k):
        raise tps.TPSUnavailable("Couldn't reach Toronto Police open data right now.")
    monkeypatch.setattr(tps, "fetch_ytd", down)
    r = client.post("/draft", data={"neighbourhood": "Thorncliffe Park"}, follow_redirects=False)
    assert r.status_code == 200 and "Couldn't reach Toronto Police open data" in html.unescape(r.text)
    monkeypatch.setattr(tps, "fetch_nco_all", down)
    monkeypatch.setattr(tps, "fetch_neighbourhood_list_from_ytd", down)
    for path in ("/", "/new"):
        r = client.get(path)
        assert r.status_code == 200 and "Couldn't reach Toronto Police open data" in html.unescape(r.text), path
    assert client.get("/api/neighbourhood/055").status_code == 503


def test_tps_network_error_is_wrapped(monkeypatch, tmp_path):
    """A real httpx failure (no cache, no snapshot) becomes TPSUnavailable with a clear message."""
    import httpx
    monkeypatch.setattr(tps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(tps, "SNAPSHOT_DIR", tmp_path)
    monkeypatch.setenv("DEMO_OFFLINE", "false")

    def boom():
        raise httpx.ConnectError("no route to host")
    with pytest.raises(tps.TPSUnavailable, match="Couldn't reach"):
        tps.cached("x_unreachable", boom)


def test_unknown_neighbourhood(client):
    r = client.post("/draft", data={"neighbourhood": "Atlantis"}, follow_redirects=False)
    assert r.status_code == 200 and "isn't one of Toronto's 158 neighbourhoods. Please choose one from the list." in html.unescape(r.text)


def test_whatsapp_text_limit(client, monkeypatch):
    from app import share
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, priority="x " * 190, action_taken="y " * 190, meeting_note="z " * 90)
    rec = store.load(bid)
    assert len(share.plain_text(rec, "en")) <= 1000


def test_history_lists_bulletins(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    draft(client)
    page = client.get("/history").text
    assert "Thorncliffe Park" in page and "Template draft" in page


# ---- offline mode ----------------------------------------------------------------------------

@pytest.fixture()
def no_network(monkeypatch):
    """Disable all non-localhost network access in code."""
    real = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def guard_host(host):
        if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
            raise OSError(f"network disabled in test: {host}")

    def create_connection(address, *a, **k):
        guard_host(address[0])
        return real(address, *a, **k)

    def getaddrinfo(host, *a, **k):
        guard_host(host)
        return real_getaddrinfo(host, *a, **k)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


def test_offline_mode_full_flow_from_snapshot(monkeypatch, tmp_path, no_network):
    from app.config import SNAPSHOT_DIR
    if not (SNAPSHOT_DIR / "snapshot_meta.json").exists():
        pytest.skip("demo snapshot not generated yet (scripts/snapshot_demo.py)")
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "should-not-be-used")
    monkeypatch.setattr(store, "BULLETIN_DIR", tmp_path / "bulletins")
    monkeypatch.setattr(tps, "CACHE_DIR", tmp_path / "empty-cache")
    monkeypatch.setattr(llm, "complete", urdu_translator)
    c = TestClient(main.app)
    home = c.get("/").text
    assert "Offline demo mode" in home and "fonts.googleapis.com" not in home
    for hood in ("Thorncliffe Park", "Milliken", "Westminster-Branson"):
        info = c.get("/api/neighbourhood/" + hood).json()
        assert info["name"] == hood
    bid = draft(c, languages=["ur"])
    c.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    rec = store.load(bid)
    assert rec["translations"]["ur"]["engine"] == "llm"  # never Azure offline
    assert rec["verification"]["ur"]["ok"], rec["verification"]["ur"]
    c.post(f"/publish/{bid}")
    assert store.load(bid)["status"] == "published"
    # a neighbourhood outside the snapshot gives a clear message, no crash
    r = c.post("/draft", data={"neighbourhood": "Rosedale-Moore Park"}, follow_redirects=False)
    assert r.status_code == 200 and "not in the demo snapshot" in html.unescape(r.text)


# ---- product additions: retry, edit/paste translation, human review, reuse, checks ------------

def test_review_shows_content_checks_and_officer_pii_warning(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, priority="Break-ins at 12 Overlea Blvd, call Jane at 647-555-0199.")
    rec = store.load(bid)
    assert any("personal information" in w for w in rec["officer_warnings"])
    page = html.unescape(client.get(f"/review/{bid}").text)
    assert "Plain-language & safety checks" in page and "Possible personal information" in page


def test_retry_one_language(client, monkeypatch):
    calls = []

    def flaky(system, user, json=False, **kw):
        calls.append(1)
        if len(calls) <= 2:  # first job: both attempts come back in Devanagari
            return urdu_translator(system, user, json).replace("خبر", "खबर")
        return urdu_translator(system, user, json)
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, languages=["ur"])
    monkeypatch.setattr(llm, "complete", flaky)
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    assert not store.load(bid)["verification"]["ur"]["ok"]
    assert "Translate again" in client.get(f"/publish/{bid}?lang=ur").text
    r = client.post(f"/publish/{bid}/retry/ur", data={"wait": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert store.load(bid)["verification"]["ur"]["ok"]
    assert client.post(f"/publish/{bid}/retry/xx", follow_redirects=False).status_code == 404


def test_paste_translation_by_hand_and_mark_reviewed(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)  # no model: translation fails
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    rec = store.load(bid)
    assert rec["translations"]["ur"]["doc"] is None
    assert "Paste a translation" in client.get(f"/publish/{bid}?lang=ur").text
    # A community translator's text, with numbers in Eastern Arabic digits.
    import re
    en = rec["english"]
    to_urdu_digits = lambda t: re.sub(r"\d", lambda m: "۰۱۲۳۴۵۶۷۸۹"[int(m.group(0))], t)
    def ur(t):
        return "خبر " + " ".join(to_urdu_digits(n) for n in re.findall(r"\d+(?:,\d{3})*%?", t)) + "۔"
    form = {"headline": ur(en["headline"]), "summary": ur(en["summary"]),
            "bullets": "\n".join(ur(b) for b in en["bullets"]),
            "officer_section": ur(en["officer_section"]), "closing": ur(en["closing"]),
            "human_reviewed": "1"}
    client.post(f"/publish/{bid}/edit/ur", data=form, follow_redirects=False)
    rec = store.load(bid)
    t = rec["translations"]["ur"]
    assert t["edited"] and t["human_reviewed"] and t["engine"] == "manual"
    assert rec["verification"]["ur"]["ok"], rec["verification"]["ur"]
    page = client.get(f"/publish/{bid}?lang=ur").text
    assert "Translation reviewed by a human translator." in page
    from app import share
    assert "اس ترجمے پر ایک انسانی مترجم نے نظرثانی کی ہے۔" in share.plain_text(rec, "ur")


def test_edit_after_publish_requires_republish_and_bad_edit_blocks(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    client.post(f"/publish/{bid}")
    assert store.load(bid)["status"] == "published"
    doc = store.load(bid)["translations"]["ur"]["doc"]
    client.post(f"/publish/{bid}/edit/ur", data={**doc, "bullets": "\n".join(doc["bullets"]),
                                                 "summary": doc["summary"] + " ۹۹۹ हुईं"})
    rec = store.load(bid)
    assert rec["status"] == "draft"
    v = rec["verification"]["ur"]
    assert not v["numbers"]["ok"] and not v["script"]["ok"]


def test_previous_bulletin_offered_for_reuse(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    assert client.get("/api/neighbourhood/055").json()["previous"] is None
    draft(client, priority="Porch thefts.", action_taken="Patrols.", meeting_note="Oct 13", languages=["ur", "ar"])
    prev = client.get("/api/neighbourhood/055").json()["previous"]
    assert prev["priority"] == "Porch thefts." and prev["languages"] == ["ur", "ar"]
    assert prev["month"] == "August 2026"
    assert client.get("/api/neighbourhood/130").json()["previous"] is None


def test_safety_findings_block_publish_until_confirmed(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    rec = store.load(bid)
    assert main.checks.content_blockers(rec) == []
    client.post(f"/review/{bid}/save", data={**rec["english"], "bullets": "\n".join(rec["english"]["bullets"]),
                                             "closing": "The plaza is dangerous at night. Email jane@gmail.com with tips."})
    rec = store.load(bid)
    titles = [f["title"] for f in main.checks.content_blockers(rec)]
    assert "Alarming words" in titles and "Possible personal information" in titles
    assert rec["verification"]["en"]["ok"]  # numbers are fine: it's the wording that blocks
    page = html.unescape(client.get(f"/publish/{bid}").text)
    assert "the English text needs a safety review" in page and "Fix on the review page" in page
    client.post(f"/publish/{bid}", data={}, follow_redirects=False)
    assert store.load(bid)["status"] == "draft"
    client.post(f"/publish/{bid}", data={"manual_ok": "1"}, follow_redirects=False)
    rec = store.load(bid)
    assert rec["status"] == "published" and rec["manual_override"]
    assert "Alarming words" in rec["manual_override_content"]


def test_length_advice_does_not_block(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    rec = store.load(bid)
    long = rec["english"]["summary"] + " Thank you for reading this update." * 30
    client.post(f"/review/{bid}/save", data={**rec["english"], "bullets": "\n".join(rec["english"]["bullets"]), "summary": long})
    assert main.checks.content_blockers(store.load(bid)) == []
    client.post(f"/publish/{bid}", data={}, follow_redirects=False)
    assert store.load(bid)["status"] == "published"


def test_draft_for_an_earlier_month(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    assert 'name="month"' in client.get("/new").text
    bid = draft(client, month="2026-03")
    rec = store.load(bid)
    p = rec["facts"]["period"]
    assert p["month_label"] == "March 2026" and p["ytd_label"] == "January 1 – March 31, 2026"
    assert rec["facts"]["categories"][0]["month"] == 12  # 12 assaults reported March 2
    assert rec["facts"]["categories"][0]["ytd"] == 12
    assert rec["verification"]["en"]["ok"]
    r = client.post("/draft", data={"neighbourhood": "Thorncliffe Park", "month": "2026-09"})
    assert "can't be drafted" in html.unescape(r.text)


def test_january_bug_last_december_not_in_current_year_feed(client, monkeypatch):
    """Jan 10, 2027: last full month is Dec 2026 but the feed now holds only 2027 rows."""
    from app.data import facts
    monkeypatch.setattr(facts, "effective_today", lambda hood_id=None: dt.date(2027, 1, 10))
    monkeypatch.setattr(tps, "fetch_ytd", lambda hid: [
        {"EVENT_UNIQUE_ID": "N1", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2027, 1, 3), "DIVISION": "D53"}])
    r = client.post("/draft", data={"neighbourhood": "Thorncliffe Park"}, follow_redirects=False)
    assert r.status_code == 200
    assert "has moved on to 2027" in html.unescape(r.text)


def test_print_all_languages(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur", "ar"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur", "ar"], "wait": "1"})
    assert "Print all" not in client.get(f"/publish/{bid}").text  # only after publishing
    client.post(f"/publish/{bid}")
    assert "Print all 3 languages" in client.get(f"/publish/{bid}").text
    page = client.get(f"/bulletin/{bid}/all?print=1").text
    assert page.count('class="sheet sheet-page') == 3
    assert 'lang="ur" dir="rtl"' in page and 'lang="ar" dir="rtl"' in page and "window.print()" in page


def test_status_page(client, monkeypatch):
    import httpx
    from app import status
    monkeypatch.setattr(llm, "status", lambda max_age=20: {"ok": False, "label": "gemma3:12b (local)", "detail": "connection refused"})

    def unreachable(*a, **k):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(status.httpx, "get", unreachable)
    st = status.run_all()
    by = {i["name"]: i for i in st["items"]}
    assert st["overall"] == "fail"
    assert by["TPS open data"]["state"] == "fail" and "DEMO_OFFLINE" in by["TPS open data"]["fix"]
    assert by["AI writer"]["state"] == "warn" and "template" in by["AI writer"]["detail"]
    assert by["Translation"]["state"] == "fail"
    page = html.unescape(client.get("/status").text)
    assert "Something needs fixing" in page and "What to do" in page
    assert client.get("/api/status").json()["overall"] == "fail"


def test_status_offline_ready(client, monkeypatch):
    from app import status
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    monkeypatch.setattr(llm, "status", lambda max_age=20: {"ok": True, "label": "gemma3:12b (local)", "detail": ""})
    by = {i["name"]: i for i in status.run_all()["items"]}
    assert by["TPS open data"]["state"] == "info"
    assert by["AI writer"]["state"] == "ok" and by["Translation"]["state"] == "ok"


def test_save_neighbourhood_for_offline_then_use_it(client, monkeypatch, tmp_path):
    from app import snapshot
    from app.data import facts
    snap = tmp_path / "snap"
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", snap)
    monkeypatch.setattr(tps, "SNAPSHOT_DIR", snap)
    monkeypatch.setattr(snapshot, "CACHE_DIR", tps.CACHE_DIR)
    # fetches go through the cache so they land in CACHE_DIR, as in real use
    monkeypatch.setattr(tps, "fetch_ytd", lambda hid: tps.cached(f"ytd_{hid}", lambda: ytd_rows(hid)))
    monkeypatch.setattr(tps, "fetch_mci_year", lambda hid, y: tps.cached(f"mci_{hid}_{y}", lambda: mci_rows(hid, y)))
    monkeypatch.setattr(tps, "fetch_nco_all", lambda: tps.cached("nco_all", lambda: NCO_ROWS))
    monkeypatch.setattr(tps, "schema", lambda layer: tps.cached(f"schema_{layer}", lambda: {"fields": [], "editingInfo": {"dataLastEditDate": ms(2026, 9, 29)}}))
    monkeypatch.setattr(tps, "fetch_neighbourhood_list_from_ytd", lambda: [])
    monkeypatch.setattr(facts, "toronto_today", lambda: dt.date(2026, 9, 29))
    r = client.post("/offline/save", data={"neighbourhood": "Milliken"}, follow_redirects=False)
    assert r.status_code == 303 and "saved=Milliken" in r.headers["location"]
    assert snapshot.saved_neighbourhoods()["130"] == {"name": "Milliken", "date": "2026-09-29"}
    assert (snap / "ytd_130.json").exists() and (snap / "mci_130_2025.json").exists()
    assert "Milliken · 2026-09-29" in client.get("/status").text
    r = client.post("/offline/save", data={"neighbourhood": "Atlantis"}, follow_redirects=False)
    assert "error=" in r.headers["location"]
    # Offline: the New form only lists saved neighbourhoods; "today" is that neighbourhood's save date.
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    page = client.get("/new").text
    assert "Offline mode: 1 saved neighbourhood: Milliken" in html.unescape(page)
    assert snapshot.date_for("130") == dt.date(2026, 9, 29)  # used as "today" for Milliken offline
    assert snapshot.date_for("055") == dt.date(2026, 9, 29)  # not saved: falls back to snapshot date
    r = client.post("/offline/save", data={"neighbourhood": "Milliken"}, follow_redirects=False)
    assert "Turn%20offline%20mode%20off" in r.headers["location"]


def test_history_delete_to_trash_and_demo_protected(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    page = client.get("/history").text
    assert 'id="h-search"' in page and f'/history/{bid}/delete' in page and "New from this" in page
    r = client.post(f"/history/{bid}/delete", follow_redirects=False)
    assert r.status_code == 303 and "deleted=" in r.headers["location"]
    assert not (store.BULLETIN_DIR / f"{bid}.json").exists()
    assert (store.BULLETIN_DIR / ".trash" / f"{bid}.json").exists()
    page = client.get("/history").text
    # not in the active list any more (it's only offered under "Trash" with a Restore button)
    assert f'href="/publish/{bid}"' not in page and f'href="/review/{bid}"' not in page
    assert f"/history/{bid}/restore" in page
    assert client.get(f"/review/{bid}").status_code == 404
    # demo bulletins can't be deleted
    bid2 = draft(client)
    store.update(bid2, lambda r: r.update(demo=True))
    assert f"/history/{bid2}/delete" not in client.get("/history").text
    r = client.post(f"/history/{bid2}/delete", follow_redirects=False)
    assert "error=" in r.headers["location"] and store.load(bid2)


def test_new_from_a_specific_bulletin(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    older = draft(client, priority="Older priority.", languages=["ur"])
    newer = draft(client, priority="Newer priority.", languages=["ar"])
    assert client.get("/api/neighbourhood/055").json()["previous"]["id"] == newer
    prev = client.get(f"/api/neighbourhood/055?reuse={older}").json()["previous"]
    assert prev["id"] == older and prev["priority"] == "Older priority." and prev["languages"] == ["ur"]
    assert f'data-reuse="{older}"' in client.get(f"/new?hood=Thorncliffe%20Park&reuse={older}").text


def test_local_services_selection_and_display(client, monkeypatch):
    from app.data import facts
    svc = facts.local_services("055")
    names = [x["name"] for x in svc]
    # legal first, one per category before repeats; inactive, General/Other and duplicate orgs dropped
    assert names == ["Don Valley Community Legal Services: Legal clinic", "Thorncliffe Food Bank", "Seniors Club",
                     "Health Centre", "Second Food Bank"]
    assert svc[0]["phone"] == "416-441-1764"  # "ext 12" stripped
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    rec = store.load(bid)
    assert rec["services_shown"] == ["2", "1", "6", "7"]
    page = client.get(f"/publish/{bid}").text
    assert "Local support services" in page and "Thorncliffe Food Bank" in page and "Second Food Bank" not in page
    # officer picks: max 4 kept, unknown ids ignored
    client.post(f"/review/{bid}/services", data={"services": ["8", "1", "999", "2", "6", "7"]})
    assert store.load(bid)["services_shown"] == ["8", "1", "2", "6"]
    client.post(f"/review/{bid}/services", data={})
    assert "Local support services" not in client.get(f"/publish/{bid}").text
    # neighbourhood without services: no section, no error
    bid2 = draft(client, neighbourhood="Milliken")
    assert store.load(bid2)["services_shown"] == []


def test_resume_interrupted_translation(client, monkeypatch):
    """Simulate a restart mid-job: ur done, ar was running, fa pending → ar and fa are resumed."""
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})

    def crash(r):
        r["languages"] = ["ur", "ar", "fa"]
        r["job"] = {"status": "running", "started": time.time() - 100,
                    "langs": {"ur": {"status": "done"}, "ar": {"status": "running", "started": time.time() - 50},
                              "fa": {"status": "pending"}}}
    store.update(bid, crash)
    assert client.get(f"/api/bulletins/{bid}/progress").json()["status"] == "stalled"
    resumed = main.resume_interrupted_jobs(background=False)
    assert resumed == {bid: ["ar", "fa"]}
    rec = store.load(bid)
    assert rec["job"]["status"] == "done"
    assert all(rec["translations"][c]["doc"] for c in ("ur", "ar", "fa"))
    assert all(rec["verification"][c]["ok"] for c in ("ur", "ar", "fa")), rec["verification"]
    assert main.resume_interrupted_jobs(background=False) == {}  # nothing left


def test_app_startup_resumes_jobs(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "resume_interrupted_jobs", lambda background=True: calls.append(1) or {})
    with TestClient(main.app):  # runs the lifespan startup
        pass
    assert calls == [1]


def test_numbers_behind_the_bulletin(client, monkeypatch):
    import csv, io
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    r = client.get(f"/bulletin/{bid}/numbers.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert 'filename="neighbourbrief-thorncliffe-park-2026-08-numbers.csv"' in r.headers["content-disposition"]
    text = r.content.decode("utf-8-sig")
    table = text.split("\n\n")[0]
    rows = list(csv.DictReader(io.StringIO(table)))
    a = next(x for x in rows if x["tps_category"] == "Assault")
    assert a["reports_August 2026"] == "4" and a["year_to_date_2026"] == "16" and a["same_period_2025"] == "20"
    assert a["percent_change"] == "-20" and a["on_bulletin"] == "yes"
    s = next(x for x in rows if x["tps_category"] == "Shooting")
    assert s["same_period_2025"] == "" and "Counts only" in s["note"]
    assert "Sexual Violation" in text and "not affiliated" in text
    j = client.get(f"/bulletin/{bid}/numbers.json").json()
    assert j["neighbourhood"]["name"] == "Thorncliffe Park" and len(j["rows"]) == len(rows)
    assert "numbers.csv" in client.get(f"/publish/{bid}").text
    assert client.get(f"/bulletin/{bid}/en").status_code == 200  # the language route still works


def test_friendly_error_pages(client, monkeypatch, tmp_store):
    r = client.get("/review/does-not-exist-123")
    assert r.status_code == 404 and "couldn&#39;t find that bulletin" in r.text and "Your saved bulletins" in r.text
    r = client.get("/no/such/page")
    assert r.status_code == 404 and "Page not found" in r.text
    r = client.get("/api/bulletins/does-not-exist-123/progress")
    assert r.status_code == 404 and r.json()["error"].startswith("We couldn't find")
    # missing neighbourhood on the bulletin form: the form again with a message (changed on Oct 24, 2026: it was a
    # generic 422 page, and lost what the officer typed); the generic handler still covers other forms
    r = client.post("/draft", data={})
    assert r.status_code == 200 and "Please choose a neighbourhood" in html.unescape(r.text)
    r = client.post("/offline/save", data={})
    assert r.status_code == 422 and "missing" in r.text
    # a damaged bulletin file doesn't crash the app
    store.BULLETIN_DIR.mkdir(parents=True, exist_ok=True)
    (store.BULLETIN_DIR / "broken-file-2026-08-abc123.json").write_text("{ not json")
    r = client.get("/review/broken-file-2026-08-abc123")
    assert r.status_code == 422 and "damaged" in r.text
    assert client.get("/history").status_code == 200  # skipped in the list
    (store.BULLETIN_DIR / "wrong-shape-2026-08-abc123.json").write_text("[1, 2, 3]")
    assert client.get("/history").status_code == 200
    assert client.get("/review/wrong-shape-2026-08-abc123").status_code == 422
    # unexpected exceptions: friendly 500, no traceback shown
    monkeypatch.setattr(main.F, "list_neighbourhoods", lambda: 1 / 0)
    c2 = TestClient(main.app, raise_server_exceptions=False)
    r = c2.get("/")
    assert r.status_code == 500 and "Something unexpected went wrong" in r.text and "ZeroDivisionError" not in r.text


def test_tip_on_bulletin_numbers_allowed_and_reused(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    assert 'name="tip_id"' in client.get("/new").text
    bid = draft(client, tip_id="report")
    rec = store.load(bid)
    assert "416-808-2222" in rec["english"]["closing"]
    assert rec["verification"]["en"]["ok"]  # the tip's phone digits are allowed (officer-chosen text)
    assert main.checks.content_blockers(rec) == []  # TPS number is not "personal information"
    prev = client.get("/api/neighbourhood/055").json()["previous"]
    assert prev["tip_id"] == "report"
    bid2 = draft(client, tip_id="report", tip_custom="Say hello to a neighbour this week.")
    rec2 = store.load(bid2)
    assert "Tip: Say hello to a neighbour this week." in rec2["english"]["closing"]
    assert rec2["officer"]["tip_id"] == ""


def test_neighbourhood_api_returns_categories_for_tip_suggestions(client):
    d = client.get("/api/neighbourhood/055").json()
    assert "Assault" in d["categories"] and "Break and Enter" in d["categories"]


def test_signoff_only_from_published_nco_list(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, signoff="PC Pat Example")
    assert store.load(bid)["signoff"] == "PC Pat Example"
    page = client.get(f"/publish/{bid}").text
    assert "PC Pat Example, 53 Division" in page
    assert client.get("/api/neighbourhood/055").json()["previous"]["signoff"] == "PC Pat Example"
    bid2 = draft(client, signoff="Jane Resident")  # not a listed NCO → dropped
    assert store.load(bid2)["signoff"] == ""
    client.post(f"/review/{bid2}/signoff", data={"signoff": "Someone Else"})
    assert store.load(bid2)["signoff"] == ""
    client.post(f"/review/{bid2}/signoff", data={"signoff": "PC Pat Example"})
    assert store.load(bid2)["signoff"] == "PC Pat Example"
    bid3 = draft(client, neighbourhood="Milliken", signoff="PC Pat Example")  # Milliken has no NCO listed
    assert store.load(bid3)["signoff"] == ""


def test_mailto_links_stay_short_for_non_latin_languages(client, monkeypatch):
    from app import share
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"], priority="پلازہ کی پارکنگ میں گاڑیوں سے چوری " * 8)
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    rec = store.load(bid)
    for code in ("en", "ur"):
        url = share.mailto_url(rec, code)
        assert len(url) <= share.MAILTO_MAX and url.startswith("mailto:?subject=") and "&body=" in url
    assert len(share.plain_text(rec, "ur")) <= 1000


def test_shortened_share_text_always_keeps_notice_and_affiliation(client, monkeypatch):
    from app import share
    from app.languages import labels
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"], priority="پلازہ کی پارکنگ میں گاڑیوں سے چوری " * 8,
                action_taken="شام کی گشت بڑھا دی " * 10)
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    rec = store.load(bid)
    for limit in (1000, 500, 300, 160):
        t = share.plain_text(rec, "ur", limit=limit)
        assert len(t) <= limit, (limit, len(t))
        assert labels("ur")["notice"] in t and "Not affiliated with TPS" in t, limit
    from urllib.parse import unquote
    body = unquote(share.mailto_url(rec, "ur").split("&body=")[1])
    assert labels("ur")["notice"] in body and "Not affiliated" in body
    # A notice as long as the longest real one (Tamil, 138 characters) is never cut either, and the
    # email link still fits.
    from app.languages import LABELS
    monkeypatch.setitem(LABELS["ur"], "notice", "ن" * 140)
    t = share.plain_text(rec, "ur", limit=160)
    assert "ن" * 140 in t and "Not affiliated with TPS" in t
    assert len(share.mailto_url(rec, "ur")) <= share.MAILTO_MAX


def test_removing_a_language_after_translating(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur", "ar"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur", "ar"], "wait": "1"})
    calls = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: calls.append(1) or urdu_translator(*a, **k))
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})  # drop Arabic
    rec = store.load(bid)
    assert rec["languages"] == ["ur"] and "ar" not in rec["translations"] and "ar" not in rec["verification"]
    assert calls == []  # Urdu kept, not re-translated
    page = client.get(f"/publish/{bid}").text
    assert 'data-tab="ar"' not in page and 'data-tab="ur"' in page
    assert client.get(f"/bulletin/{bid}/ar").status_code == 404
    assert main.checks.publish_blocked(rec) == []


def test_large_print_version(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    client.post(f"/publish/{bid}")
    assert "?large=1&amp;print=1" in client.get(f"/publish/{bid}").text
    page = client.get(f"/bulletin/{bid}/en?large=1&print=1").text
    assert "large-print" in page and "(large print)" in page and "window.print()" in page
    assert "large-print" not in client.get(f"/bulletin/{bid}/en").text


def test_only_this_computer_can_use_the_app(tmp_store, fake_tps, monkeypatch):
    lan = TestClient(main.app, client=("192.168.1.23", 50000))
    r = lan.get("/history")
    assert r.status_code == 403 and "only works on the computer it runs on" in r.text
    assert lan.get("/api/status").status_code == 403
    assert TestClient(main.app, client=("127.0.0.1", 50000)).get("/history").status_code == 200
    assert TestClient(main.app, client=("::1", 50000)).get("/history").status_code == 200
    monkeypatch.setenv("ALLOW_LAN", "true")
    assert lan.get("/history").status_code == 200
    from app import status
    assert any(i["name"] == "Access" and i["state"] == "warn" for i in status.run_all()["items"])


def test_restore_from_trash_and_undo(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    r = client.post(f"/history/{bid}/delete", follow_redirects=False)
    assert f"deleted_id={bid}" in r.headers["location"]
    page = client.get(r.headers["location"]).text
    assert f"/history/{bid}/restore" in page and "Undo" in page and "Trash (1)" in page
    r = client.post(f"/history/{bid}/restore", follow_redirects=False)
    assert "restored=" in r.headers["location"]
    assert store.load(bid)["id"] == bid and store.list_trash() == []
    assert "Trash (" not in client.get("/history").text
    r = client.post(f"/history/{bid}/restore", follow_redirects=False)  # not in trash any more
    assert "error=" in r.headers["location"]
    # same id already present → refuse rather than overwrite
    client.post(f"/history/{bid}/delete")
    store.save(dict(store.list_trash()[0]))
    r = client.post(f"/history/{bid}/restore", follow_redirects=False)
    assert "already%20exists" in r.headers["location"]


def test_copy_for_newsletter_full_text(client, monkeypatch):
    from app import share
    from app.languages import labels
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"], signoff="PC Pat Example", meeting_note="Oct 13, 7 p.m.")
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    client.post(f"/publish/{bid}")
    rec = store.load(bid)
    en = share.newsletter_text(rec, "en")
    for part in ["Neighbourhood Safety Update · Thorncliffe Park · August 2026", "Reports to police\n• ",
                 "\nPC Pat Example, 53 Division", "Your Neighbourhood Community Officers\nPC Pat Example · 416-555-0100",
                 "Local support services\nDon Valley", "Emergency: 911", "Not affiliated with or endorsed"]:
        assert part in en, part
    assert len(en) > 1000  # not limited like WhatsApp
    ur = share.newsletter_text(rec, "ur")
    assert labels("ur")["notice"] in ur and labels("ur")["reports"] in ur
    page = client.get(f"/publish/{bid}").text
    assert "Copy for newsletter" in page and 'id="nl-ur"' in page and 'data-copy-src="nl-en"' in page


def test_backup_and_import(client, monkeypatch, tmp_path):
    import io, zipfile, json as _json
    monkeypatch.setattr(llm, "complete", model_down)
    a, b = draft(client), draft(client, neighbourhood="Milliken")
    r = client.get("/history/backup.zip")
    assert r.status_code == 200 and "neighbourbrief-backup-" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == sorted([f"bulletins/{a}.json", f"bulletins/{b}.json", "manifest.json"])
    backup = r.content
    # import into an empty store ("another computer")
    monkeypatch.setattr(store, "BULLETIN_DIR", tmp_path / "other")
    r = client.post("/history/import", files={"file": ("b.zip", backup, "application/zip")}, follow_redirects=False)
    assert "Imported%202%20bulletins" in r.headers["location"]
    assert {x["id"] for x in store.list_all()} == {a, b}
    # importing again keeps existing ones
    r = client.post("/history/import", files={"file": ("b.zip", backup, "application/zip")}, follow_redirects=False)
    assert "already%20here" in r.headers["location"] and "Imported%200" in r.headers["location"]
    # hostile / broken content
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zz:
        zz.writestr("../../evil.json", _json.dumps({"id": "evil", "facts": {}, "english": {}}))
        zz.writestr("x/not-json.json", "{nope")
        zz.writestr("bulletins/wrong-name-2026-08-aaaaaa.json", _json.dumps({"id": "other-id-2026-08-bbbbbb", "facts": {}, "english": {}}))
        zz.writestr("readme.txt", "hello")
    r = client.post("/history/import", files={"file": ("h.zip", buf.getvalue(), "application/zip")}, follow_redirects=False)
    assert "Imported%200" in r.headers["location"] and "3%20file" in r.headers["location"]
    assert not (tmp_path / "evil.json").exists() and len(store.list_all()) == 2
    r = client.post("/history/import", files={"file": ("x.zip", b"not a zip", "application/zip")}, follow_redirects=False)
    assert "isn%27t%20a%20NeighbourBrief%20backup" in r.headers["location"]
    assert "Backup &amp; restore" in client.get("/history").text


def test_neighbourhood_api_lists_existing_bulletins_for_duplicate_warning(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    assert client.get("/api/neighbourhood/055").json()["existing"] == []
    bid = draft(client)
    draft(client, month="2026-03")
    ex = client.get("/api/neighbourhood/055").json()["existing"]
    assert {(e["month"], e["status"]) for e in ex} == {("2026-08", "draft"), ("2026-03", "draft")}
    assert any(e["id"] == bid and e["month_label"] == "August 2026" for e in ex)
    assert client.get("/api/neighbourhood/130").json()["existing"] == []
    assert 'id="dup-warning"' in client.get("/new").text


def test_security_headers_on_pages_and_downloads(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    for path in ["/", "/new", "/how-we-count", "/check", "/neighbourhoods", "/neighbourhoods/055", "/data-health", "/impact", "/public", "/accessibility", "/privacy-ai", "/help", "/ask", "/translations", "/history", f"/publish/{bid}", f"/bulletin/{bid}/numbers.csv", "/api/health", "/no-such-page"]:
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff", path
        assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer", path
        assert "frame-ancestors 'none'" in h["content-security-policy"], path


def test_no_em_dashes_anywhere_in_the_product(client, monkeypatch):
    """Owner's style rule: no em dashes in any screen, bulletin, or shared text."""
    from app import share
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur", "zh-Hant"], signoff="PC Pat Example", tip_id="car-lock")
    client.post(f"/review/{bid}/translate", data={"languages": ["ur", "zh-Hant"], "wait": "1"})
    client.post(f"/publish/{bid}")
    rec = store.load(bid)
    pages = ["/", "/new", "/how-we-count", "/check", "/neighbourhoods", "/neighbourhoods/055", "/data-health", "/impact", "/public", "/accessibility", "/privacy-ai", "/help", "/ask", "/translations", "/history", "/status", f"/review/{bid}", f"/publish/{bid}", f"/bulletin/{bid}/all",
             f"/bulletin/{bid}/en?large=1", "/no-such-page"]
    for path in pages:
        assert "\u2014" not in client.get(path).text, path
    for code in ("en", "ur", "zh-Hant"):
        for text in (share.plain_text(rec, code), share.newsletter_text(rec, code), share.mailto_url(rec, code)):
            assert "\u2014" not in text and "%E2%80%94" not in text, code


def test_progress_wording_matches_engine(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    assert "The local model takes about 30–90 seconds per language." in client.get(f"/review/{bid}").text
    monkeypatch.setenv("AZURE_TRANSLATOR_KEY", "k")
    page = client.get(f"/review/{bid}").text
    assert "This usually takes a few seconds." in page and "30–90 seconds" not in page


def test_home_and_how_we_count_pages(client):
    home = html.unescape(client.get("/").text)
    for part in ["Make a bulletin", "Saved bulletins", "How we count", "Pre-flight check", 'action="/new"',
                 "Not affiliated with or endorsed by the Toronto Police Service"]:
        assert part in home, part
    hw = html.unescape(client.get("/how-we-count").text)
    for part in ["Reports to police, not proven crimes", "both counts are 10 or more", "Sexual Violation",
                 "YTD_CRIME_WM", "Each translated line"]:
        assert part in hw, part
    # navigation marks where you are
    assert 'aria-current="page">How we count' in client.get("/how-we-count").text
    assert 'aria-current="step"' in client.get("/new").text


def test_check_my_numbers(client):
    from app.verify import factcheck as FC
    from app.data import facts
    f = facts.build_facts("Thorncliffe Park")
    a = next(l for l in f["categories"] if l["key"] == "Assault")      # month 4, ytd 16, prev 20, pct -20
    text = (f"Assaults were down {abs(a['pct_change'])}% this year, with {a['ytd']} reports so far.\n"
            f"Break-ins rose to {a['ytd']} this year.\n"
            f"Assaults are up {abs(a['pct_change'])}% compared with last year.\n"
            "There were 999 robberies in August.\n"
            "Meet us on Tuesday, October 13 at 7 p.m. at 48 Thorncliffe Park Drive, or call 416-808-5300.")
    r = FC.check_text(text, f)
    v = [(i["text"], i["verdict"]) for i in r["items"]]
    assert v[0] == ("20", "match") and v[1] == ("16", "match")
    assert ("16", "wrong_category") in v          # assault number used for break-ins
    assert ("20", "direction") in v               # "up" but the data says down
    assert ("999", "not_found") in v
    skipped = [i["text"] for i in r["items"] if i["verdict"] == "skipped"]
    assert "October 13" in skipped and "416-808-5300" in skipped and "48 Thorncliffe Park Drive" in skipped
    assert sum(1 for s in skipped if "416" in s) == 1   # one item per phone number
    assert not r["ok"]
    page = html.unescape(client.post("/check", data={"neighbourhood": "Thorncliffe Park", "text": text}).text)
    assert "3 numbers to fix" in page and "wrong direction" in page and 'class="fc-bad"' in page
    ok = client.post("/check", data={"neighbourhood": "Thorncliffe Park", "text": f"Assaults: {a['month']} reports in August."}).text
    assert "Every number checks out" in html.unescape(ok)
    assert "choose a neighbourhood" in html.unescape(client.post("/check", data={"neighbourhood": "Atlantis", "text": "1"}).text)
    assert client.get("/check").status_code == 200


def test_maps_boundary_and_service_pins_only(client):
    from app import geo
    d = client.get("/api/neighbourhood/055").json()
    m = d["map"]
    assert m["geometry"]["type"] == "Polygon" and m["tiles"] is True
    assert m["bbox"] == [-79.36, 43.70, -79.33, 43.72]
    assert m["outline"]["d"].startswith("M") and m["outline"]["d"].count("Z") == 2   # outer ring + hole
    assert len(m["outline"]["points"]) == 1                                         # the pin, for the offline map
    assert [s["name"] for s in m["services"]] == ["Health Centre"]                  # only services TPS placed
    assert set(m["services"][0]) == {"name", "category", "address", "lat", "lon"}   # no incident data in the map
    assert client.get("/api/neighbourhood/130").json()["map"]["services"] == []
    far = dict(ASSET_ROWS[6], ResourceAgencyNum=99, PublicName="Head Office Far Away", Category="Food", Latitude=43.65, Longitude=-79.38)
    import app.data.tps as T
    T_fetch = T.fetch_assets
    try:
        T.fetch_assets = lambda hid: ASSET_ROWS + [far]
        assert "Head Office Far Away" not in [s["name"] for s in main.neighbourhood_map("055")["services"]]
    finally:
        T.fetch_assets = T_fetch
    # point in polygon, holes and multipolygons
    assert geo.locate(-79.34, 43.71)["name"] == "Thorncliffe Park"
    assert geo.locate(-79.3495, 43.7105) is None          # inside the hole
    assert geo.locate(-79.28, 43.81)["id"] == "130"
    assert geo.locate(-79.0, 43.0) is None
    fc = client.get("/api/boundaries.geojson")
    assert fc.status_code == 200 and len(fc.json()["features"]) == 2 and "max-age" in fc.headers["cache-control"]
    page = client.get("/new").text
    assert 'data-locate="hood-input"' in page and "isn't sent or saved" in page and "/static/vendor/leaflet/leaflet.js" in page
    assert 'data-locate="home-hood"' in client.get("/").text
    h = client.get("/").headers
    assert "https://tile.openstreetmap.org" in h["content-security-policy"] and "geolocation=(self)" in h["permissions-policy"]


def test_map_offline_uses_outline_not_tiles(client, monkeypatch):
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    from app import geo
    assert main.neighbourhood_map("055")["tiles"] is False
    monkeypatch.setattr(geo, "boundaries", lambda: (_ for _ in ()).throw(tps.TPSUnavailable("down")))
    assert main.neighbourhood_map("055") is None


def test_neighbourhood_profile_joins_datasets(client):
    from app.data import profile as P
    p = P.build_profile("Thorncliffe Park")
    r = p["road"]
    assert r["month"] == {"total": 12, "injury": 3, "pedestrian": 1, "cyclist": 0, "fatal": 0}   # duplicate counted once
    assert r["ytd"]["total"] == 32 and r["ytd"]["fatal"] == 1 and r["ytd"]["cyclist"] == 1       # September excluded
    assert r["ytd_prev"]["total"] == 40 and r["pct_change"] == -20
    assert [b["count"] for b in r["by_month"][-2:]] == [20, 12] and r["by_month"][-1]["label"] == "Aug 2026"
    assert r["looks_partial"] is False and len(r["by_month"]) == 12
    t = p["trend"]
    assert t["years"] == [2024, 2025] and t["population"] == 23826
    assert [(s["label"], s["values"]) for s in t["series"]] == [("Assaults", [145, 152]), ("Robberies", [9, 0])]
    assert p["census"]["languages"][0]["language"] == "Urdu"
    page = html.unescape(client.get("/neighbourhoods/055").text)
    for text in ("Thorncliffe Park", "Road safety", "Twelve years of reports, 2024 to 2025", "Languages spoken at home",
                 "Traffic Collisions", "updated October 8, 2026", "down 20%", "Make a bulletin for Thorncliffe Park"):
        assert text in page, text
    assert "—" not in page
    j = client.get("/neighbourhoods/055/data.json").json()
    assert j["road_safety"]["ytd"]["total"] == 32 and j["yearly_counts"]["series"][0]["values"] == [145, 152]
    assert "Not affiliated with or endorsed by the Toronto Police Service." in j["notes"]
    assert client.get("/neighbourhoods/999").status_code == 404
    assert client.get("/neighbourhoods/999/data.json").status_code == 404
    d = html.unescape(client.get("/neighbourhoods").text)
    assert 'href="/neighbourhoods/055"' in d and "Has NCOs" in d and "Urdu" in d
    assert 'aria-current="page">Neighbourhoods' in client.get("/neighbourhoods/055").text


def test_profile_survives_missing_datasets(client, monkeypatch):
    from app.data import profile as P

    def down(*a):
        raise tps.TPSUnavailable("down")
    monkeypatch.setattr(P, "fetch_collisions", down)
    monkeypatch.setattr(P, "fetch_trend_all", down)
    page = html.unescape(client.get("/neighbourhoods/055").text)
    assert "Traffic collisions unavailable" in page and "Long-term counts unavailable" in page and "Reported crime" in page


def test_road_safety_flags_a_partial_latest_month(monkeypatch):
    import datetime as dt
    from app.data import profile as P
    rows = [{"EVENT_UNIQUE_ID": f"{m}{i}", "OCC_YEAR": "2026", "OCC_MONTH": m} for m in ("March", "April", "May", "June", "July")
            for i in range(30)] + [{"EVENT_UNIQUE_ID": f"A{i}", "OCC_YEAR": "2026", "OCC_MONTH": "August"} for i in range(10)]
    monkeypatch.setattr(P, "fetch_collisions", lambda hid, year: rows)
    monkeypatch.setattr(P, "_updated", lambda layer: None)
    assert P.road_safety("055", dt.date(2026, 8, 1))["looks_partial"] is True


def test_data_health_flags_stale_and_changed_datasets(client, monkeypatch):
    import datetime as dt
    from app.data import health
    fields = lambda names: {"fields": [{"name": n} for n in names]}
    good = {d[3]: fields([a.split("|")[0] for a in d[5]] + ([d[6]] if d[6] else [])) for d in health.DATASETS}
    good["Traffic_Collisions_Open_Data"]["editingInfo"] = {"dataLastEditDate": ms(2026, 6, 1)}     # 92 days old (> 45)
    good["YTD_CRIME_WM"] = fields(["EVENT_UNIQUE_ID", "REPORT_DATE_AGOL", "HOOD_158", "DIVISION"])   # CRIME_TYPE renamed
    good["YTD_CRIME_WM"]["editingInfo"] = {"dataLastEditDate": ms(2026, 9, 29)}

    def schema(service, layer):
        if service == "Community_Assets_TO":
            raise tps.TPSUnavailable("down")
        return good[service]
    monkeypatch.setattr(health, "_schema", schema)
    monkeypatch.setattr(health, "_stats", lambda s, l, d: {"count": 1234, "latest": ms(2026, 8, 31) if d else None})
    h = health.run(dt.date(2026, 9, 1))
    by = {i["key"]: i for i in h["items"]}
    assert by["ytd"]["state"] == "bad" and "CRIME_TYPE" in by["ytd"]["problems"][0]
    assert by["collisions"]["state"] == "warn" and by["collisions"]["age_days"] == 92
    assert by["assets"]["state"] == "unknown"
    assert by["mci"]["state"] == "ok" and by["mci"]["count"] == 1234 and by["mci"]["latest"] == "August 31, 2026"
    assert h["counts"] == {"ok": 4, "warn": 1, "bad": 1, "unknown": 1}
    assert any("1 of" in x["text"] and "Neighbourhood Community Officers" in x["text"] for x in h["findings"])
    page = html.unescape(client.get("/data-health").text)
    assert "Data health" in page
    assert "✗ 1 changed shape" in page and "⚠️ 1 stale" in page and "Fields the app needs are missing: CRIME_TYPE." in page
    assert client.get("/data-health.json").json()["counts"]["bad"] == 1
    assert 'href="/data-health"' in client.get("/").text


def test_static_public_site(client, monkeypatch, tmp_path):
    """The public site builds from published, verified bulletins only, with resident navigation and
    working relative links; drafts and staff pages never appear."""
    import importlib.util
    import xml.dom.minidom
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    client.post(f"/publish/{bid}")
    draft_only = draft(client, neighbourhood="Milliken")
    spec = importlib.util.spec_from_file_location("bps", Path(__file__).resolve().parent.parent / "scripts" / "build_public_site.py")
    bps = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bps)
    res = bps.build(tmp_path / "site", base_url="https://example.org/nb/")
    site = tmp_path / "site"
    assert res["bulletins"] == 1 and res["profiles"] == 1
    for f in ["index.html", "how-we-count.html", "data-health.html", "accessibility.html", "feed.xml", "neighbourhoods/index.html",
              "neighbourhoods/055.html", "neighbourhoods/055.json", f"bulletins/{bid}/en.html", f"bulletins/{bid}/ur.html",
              f"bulletins/{bid}/ur-large.html", f"bulletins/{bid}/all.html", f"bulletins/{bid}/numbers.csv", "embed/055.html",
              "static/app.css", "static/vendor/leaflet/leaflet.js", "boundaries.geojson"]:
        assert (site / f).exists(), f
    assert not list(site.rglob(f"*{draft_only}*"))                               # drafts never published
    for page in site.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        assert 'href="/' not in text and 'src="/' not in text, page               # all links relative
        for staff in ("Make a bulletin", "Saved bulletins", "Check my numbers", "/review/", "/publish/"):
            assert staff not in text, (page, staff)
        if page.name != "numbers.csv":
            assert "Not affiliated with or endorsed by the Toronto Police Service" in text or "not affiliated" in text.lower(), page
            # Open Government Licence – Ontario s.4: the attribution statement and a link to the licence
            assert "Contains information licensed under the" in text and "open-government-licence-ontario" in text, page
    assert (site / "static/vendor/THIRD_PARTY_NOTICES.txt").exists()
    index = (site / "index.html").read_text(encoding="utf-8")
    assert f'href="bulletins/{bid}/ur.html"' in index and 'href="static/app.css"' in index
    assert 'href="../index.html"' in (site / "neighbourhoods" / "055.html").read_text(encoding="utf-8")
    assert 'data-src="../boundaries.geojson"' in (site / "neighbourhoods" / "index.html").read_text(encoding="utf-8")
    feed = xml.dom.minidom.parse(str(site / "feed.xml"))
    assert feed.getElementsByTagName("link")[1].getAttribute("href") == f"https://example.org/nb/bulletins/{bid}/en.html"
    assert "testserver" not in (site / "feed.xml").read_text()
    hood_feed = (site / "neighbourhoods" / "055.xml").read_text()
    assert "testserver" not in hood_feed and 'href="https://example.org/nb/neighbourhoods/055.xml"' in hood_feed
    assert f"https://example.org/nb/bulletins/{bid}/en.html" in hood_feed
    assert 'href="../neighbourhoods/055.xml"' in (site / "neighbourhoods" / "055.html").read_text(encoding="utf-8")
    # a link the site can't serve stops the build
    with pytest.raises(bps.BuildError):
        bps.rewrite('<a href="/status">x</a>', "index.html", {"index.html"})
    r = client.get("/embed/055")
    assert "frame-ancestors *" in r.headers["content-security-policy"] and "x-frame-options" not in r.headers
    assert client.get("/public").headers["x-frame-options"] == "DENY"


def test_impact_page_counts_from_records(client, monkeypatch):
    from app import impact
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    client.post(f"/publish/{bid}")
    draft(client, neighbourhood="Milliken")
    m = impact.compute()
    assert m["bulletins"] == 2 and m["published"] == 1 and m["languages"] == ["Urdu"] and m["pages"] == 2
    assert m["numbers_checked"] > 0 and m["all_demo"] is False
    urdu = next(l for l in json_census()["055"]["languages"] if l["code"] == "ur")
    assert m["speakers"] == urdu["speakers"] and m["reach"][0]["name"] == "Thorncliffe Park"
    page = html.unescape(client.get("/impact").text)
    assert "Bulletins published" in page and f"{urdu['speakers']:,}" in page and "including the officer's review" in page
    assert 'href="/impact"' in client.get("/").text


def json_census():
    from app.languages import census_languages
    return census_languages()


def test_you_said_we_did_followup(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    form = {"prev_priority": "Car break-ins near the plaza (12 reports).",
            "followup": "Evening patrols started and 3 tenant sessions were held", "followup_status": "done"}
    bid = draft(client, **form)
    rec = store.load(bid)
    assert rec["english"]["followup"] == ("Last month's priority: Car break-ins near the plaza (12 reports). "
                                          "Since then: Evening patrols started and 3 tenant sessions were held. This is done.")
    assert rec["verification"]["en"]["ok"]                     # the officer's own numbers are allowed
    page = html.unescape(client.get(f"/review/{bid}").text)
    assert 'class="b-followup"' in page and "Since last month (you said, we did)" in page
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    ur = store.load(bid)["translations"]["ur"]["doc"]
    assert ur["followup"] and "12" in ur["followup"] and "3" in ur["followup"]   # translated, numbers kept
    from app import share
    assert "Since then: Evening patrols" in share.plain_text(store.load(bid), "en")
    # no previous priority or no update → nothing added; a bad status is ignored
    assert store.load(draft(client, followup="Something"))["english"]["followup"] == ""
    odd = draft(client, prev_priority="Noise.", followup="Talked to the landlord", followup_status="hacked")
    assert store.load(odd)["english"]["followup"] == "Last month's priority: Noise. Since then: Talked to the landlord."
    same = draft(client, prev_priority="Car break-ins near the plaza.", followup="x" * 300, followup_status="ongoing")
    assert store.load(same)["english"]["followup"] == "Since last month: " + "x" * 160 + ". We are still working on it."


def test_saving_an_older_record_unchanged_keeps_translations(client, monkeypatch):
    """Records made before the follow-up field existed have no "followup" key: saving them unchanged
    must not look like an edit (which would throw away their translations)."""
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})

    def strip(r):
        r["english"].pop("followup", None)
        r["translations"]["ur"]["doc"].pop("followup", None)
    store.update(bid, strip)
    e = store.load(bid)["english"]
    client.post(f"/review/{bid}/save", data={"headline": e["headline"], "summary": e["summary"], "bullets": "\n".join(e["bullets"]),
                                             "officer_section": e["officer_section"], "closing": e["closing"]})
    assert store.load(bid)["translations"].get("ur", {}).get("doc")


def test_check_example_has_one_mistake_of_each_kind(client):
    from app.data import facts
    from app.verify import factcheck as FC
    f = facts.build_facts("Thorncliffe Park")
    text = FC.example_text(f)
    r = FC.check_text(text, f)
    assert r["counts"]["match"] >= 2 and r["counts"]["not_found"] == 1 and r["counts"]["direction"] == 1
    page = html.unescape(client.get("/check?hood=Thorncliffe%20Park&example=1").text)
    assert "Thorncliffe Park safety update" in page and "deliberate mistakes" in page


def test_two_person_check(client, monkeypatch):
    from app import approval
    monkeypatch.setattr(llm, "complete", urdu_translator)
    monkeypatch.setenv("REQUIRE_REVIEW", "true")
    monkeypatch.setenv("ROLE_AUTHOR", "Officer")
    monkeypatch.setenv("ROLE_REVIEWER", "Sergeant")
    bid = draft(client, languages=["ur"], author="Pat Example")
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    page = html.unescape(client.get(f"/publish/{bid}").text)
    assert "Second check" in page and "required before publishing" in page and "Sign off as Sergeant" in page
    assert "I'm not the officer (Pat Example)" in page
    # publishing without a sign-off is refused
    r = client.post(f"/publish/{bid}", follow_redirects=False)
    assert "error=review" in r.headers["location"] and store.load(bid)["status"] == "draft"
    # the author can't sign off their own bulletin; the box must be ticked; no names in the URL
    r = client.post(f"/publish/{bid}/review-signoff", data={"name": " pat  example ", "confirm": "1"}, follow_redirects=False)
    assert r.headers["location"].endswith("error=same#second-check") and "Pat" not in r.headers["location"]
    assert "different person from the officer" in html.unescape(client.get(f"/publish/{bid}?error=same").text)
    r = client.post(f"/publish/{bid}/review-signoff", data={"name": "Sam Reviewer"}, follow_redirects=False)
    assert "error=confirm" in r.headers["location"]
    client.post(f"/publish/{bid}/review-signoff", data={"name": "Sam Reviewer", "confirm": "1"})
    st = approval.state(store.load(bid))
    assert st["current"] and st["review"]["role"] == "Sergeant"
    # a change after the sign-off makes it stale
    e = store.load(bid)["english"]
    d = store.load(bid)["translations"]["ur"]["doc"]   # a small wording change that keeps every number
    client.post(f"/publish/{bid}/edit/ur", data={**d, "bullets": "\n".join(d["bullets"]), "closing": d["closing"] + " شکریہ۔"})
    assert approval.state(store.load(bid))["stale"]
    assert "signed off an earlier version" in html.unescape(client.get(f"/publish/{bid}").text)
    client.post(f"/publish/{bid}/review-signoff", data={"name": "Sam Reviewer", "confirm": "1"})
    client.post(f"/publish/{bid}")
    rec = store.load(bid)
    assert rec["status"] == "published" and rec["published_review"]["name"] == "Sam Reviewer"
    assert [ev["action"] for ev in rec["audit"]] == ["drafted", "reviewed", "edited", "reviewed", "published"]
    assert rec["audit"][0]["who"] == "Pat Example" and rec["audit"][0]["role"] == "Officer"
    page = html.unescape(client.get(f"/publish/{bid}").text)
    assert "second check by Sam Reviewer (Sergeant)" in page and "History of this bulletin (5)" in page
    assert e["headline"]  # English untouched by the translation edit


def test_second_check_is_optional_by_default(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client)
    assert "(optional)" in html.unescape(client.get(f"/publish/{bid}").text)
    client.post(f"/publish/{bid}")
    rec = store.load(bid)
    assert rec["status"] == "published" and rec["published_review"] is None
    assert rec["audit"][-1]["detail"] == "Published without a second check"


def test_back_translation_blocks_a_flipped_direction(client, monkeypatch):
    from app.ai import translate
    monkeypatch.setattr(llm, "complete", urdu_translator)
    calls = []

    def fake_back(doc, code):
        calls.append(code)
        b = store.load(bid)["english"] if calls else None
        out = {k: (list(v) if isinstance(v, list) else v) for k, v in b.items()}
        out["bullets"] = [x.replace("down", "up") for x in out["bullets"]]   # the translation "says" up
        return out
    bid = draft(client, languages=["ur"])
    monkeypatch.setattr(translate, "back_translate", fake_back)
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    rec = store.load(bid)
    v = rec["verification"]["ur"]
    assert calls == ["ur"] and rec["translations"]["ur"]["back"]["doc"]
    assert not v["ok"] and v["meaning"]["checked"] and "Meaning changed" in v["summary"] and "says down" in v["summary"]
    from app.verify import checks
    assert "ur" in checks.publish_blocked(rec)
    page = html.unescape(client.get(f"/publish/{bid}?lang=ur").text)
    assert "Read it back in English" in page and "a line changed direction" in page
    # a hand fix is read back again; now the directions agree
    monkeypatch.setattr(translate, "back_translate", lambda doc, code: store.load(bid)["english"])
    d = rec["translations"]["ur"]["doc"]
    client.post(f"/publish/{bid}/edit/ur", data={**d, "bullets": "\n".join(d["bullets"]), "closing": d["closing"] + " ۔"})
    rec = store.load(bid)
    assert rec["verification"]["ur"]["meaning"]["checked"] and rec["verification"]["ur"]["ok"]


def test_data_health_flags_an_empty_layer(monkeypatch):
    import datetime as dt
    from app.data import health
    monkeypatch.setattr(health, "_schema", lambda s, l: {"fields": [{"name": n} for d in health.DATASETS if d[3] == s for n in [a.split("|")[0] for a in d[5]]]})
    monkeypatch.setattr(health, "_stats", lambda s, l, d: {"count": 0 if s == "YTD_CRIME_WM" else 5})
    item = next(i for i in health.run(dt.date(2026, 10, 23))["items"] if i["key"] == "ytd")
    assert item["state"] == "warn" and "probably refreshing" in item["problems"][0]


def test_cross_site_posts_and_rebinding_hosts_are_refused(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    r = client.post(f"/publish/{bid}", headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403 and store.load(bid)["status"] == "draft"
    r = client.post("/history/delete", data={"bid": bid}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403
    assert client.post(f"/publish/{bid}", headers={"sec-fetch-site": "same-origin"}, follow_redirects=False).status_code == 303
    assert client.get("/", headers={"host": "evil.example:8010"}).status_code == 403
    assert client.get("/", headers={"host": "127.0.0.1:8010"}).status_code == 200
    assert client.get("/", headers={"host": "localhost:8010"}).status_code == 200
    assert client.get("/", headers={"host": "[::1]:8010"}).status_code == 200
    assert client.get("/neighbourhoods/055", headers={"sec-fetch-site": "cross-site"}).status_code == 200  # reading is fine


def test_provenance_links_reproduce_each_count(client):
    import datetime as dt
    from urllib.parse import parse_qs, urlparse
    from app.data import facts, provenance
    f = facts.build_facts("Thorncliffe Park")          # August 2026 in the test data
    q = provenance.queries(f)["Assault"]
    w = parse_qs(urlparse(q["month"]).query)
    assert w["returnDistinctValues"] == ["true"] and w["outFields"] == ["EVENT_UNIQUE_ID"] and w["returnCountOnly"] == ["true"]
    assert "HOOD_158='055' AND CRIME_TYPE='Assault'" in w["where"][0]
    assert "TIMESTAMP '2026-08-01 04:00:00'" in w["where"][0] and "TIMESTAMP '2026-09-01 04:00:00'" in w["where"][0]
    assert "TIMESTAMP '2026-01-01 05:00:00'" in parse_qs(urlparse(q["ytd"]).query)["where"][0]   # winter: UTC-5
    assert "TIMESTAMP '2025-09-01 04:00:00'" in parse_qs(urlparse(q["prev"]).query)["where"][0]
    assert provenance._utc(dt.date(2026, 11, 2)) == "2026-11-02 05:00:00"                  # after DST ends
    page = html.unescape(client.get("/neighbourhoods/055").text)
    assert "Check it yourself" in page and "returnDistinctValues=true" in page
    bid = draft(client)
    csv_text = client.get(f"/bulletin/{bid}/numbers.csv").text
    assert "check_month_url" in csv_text and "returnCountOnly" in csv_text


def test_privacy_and_ai_page(client):
    page = html.unescape(client.get("/privacy-ai").text)
    for text in ("Numbers come from code, not AI.", "No accounts, no cookies, no tracking.", "never sent or stored",
                 "not affiliated with or endorsed by the Toronto Police Service"):
        assert text in page, text
    assert 'href="/privacy-ai"' in client.get("/").text


def test_listen_buttons(client):
    page = client.get("/publish/" + draft_with_translation(client)).text
    assert 'data-listen="en"' in page and 'data-listen="ur"' in page and "hidden>🔊 Listen" in page


def draft_with_translation(client):
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    mp.undo()
    return bid


def test_allow_clients_networks(monkeypatch):
    monkeypatch.setenv("ALLOW_CLIENTS", "172.16.0.0/12, 192.168.65.0/24")
    assert main._in_allowed_networks("172.17.0.1") and main._in_allowed_networks("192.168.65.1")
    assert not main._in_allowed_networks("10.0.0.5") and not main._in_allowed_networks("not-an-ip")
    monkeypatch.setenv("ALLOW_CLIENTS", "")
    assert not main._in_allowed_networks("172.17.0.1")


def test_translator_review_queue(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft(client, languages=["ur"])
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    client.post(f"/publish/{bid}")
    q = html.unescape(client.get("/translations").text)
    assert "1 of 1 need a review." in q and f'/translations/{bid}/ur' in q
    page = html.unescape(client.get(f"/translations/{bid}/ur").text)
    assert "Read back in English (rough)" in page and "Line 1" in page and 'name="next"' in page
    d = store.load(bid)["translations"]["ur"]["doc"]
    # confirm without changes: stays published, bulletin now says human-reviewed, history records it
    r = client.post(f"/publish/{bid}/edit/ur", data={**d, "bullets": "\n".join(d["bullets"]), "human_reviewed": "1",
                                                      "reviewer": "Ayesha Translator", "next": f"/translations/{bid}/ur"},
                    follow_redirects=False)
    assert r.headers["location"] == f"/translations/{bid}/ur"
    rec = store.load(bid)
    t = rec["translations"]["ur"]
    assert rec["status"] == "published" and t["human_reviewed"] and t["reviewed_by"] == "Ayesha Translator"
    assert rec["audit"][-1]["action"] == "translation reviewed" and rec["audit"][-1]["who"] == "Ayesha Translator"
    assert "0 of 1 need a review." in html.unescape(client.get("/translations").text)
    from app.languages import LABELS
    ur_page = html.unescape(client.get(f"/bulletin/{bid}/ur").text)
    assert LABELS["ur"]["reviewed"] in ur_page and LABELS["ur"]["notice"] not in ur_page
    # an open redirect is not possible
    r = client.post(f"/publish/{bid}/edit/ur", data={**d, "bullets": "\n".join(d["bullets"]), "next": "https://evil.example"},
                    follow_redirects=False)
    assert r.headers["location"].startswith(f"/publish/{bid}")
    assert client.get(f"/translations/{bid}/fr").status_code == 404


def test_help_page(client):
    page = html.unescape(client.get("/help").text)
    assert "Your first bulletin in five steps" in page and "Publishing is blocked" in page
    assert 'aria-current="page">Help' in client.get("/help").text


def test_check_my_numbers_covers_traffic_collisions(client):
    # fake collisions for 055 (August 2026): 12 this month, 3 with injuries, 32 so far, 40 last year → down 20%
    text = ("Traffic collisions are down 20% this year.\nThere were 12 collisions in August, 3 with injuries.\n"
            "Crashes are up 20% compared with last year.\nAssaults: 12 collisions.")
    page = html.unescape(client.post("/check", data={"neighbourhood": "Thorncliffe Park", "text": text}).text)
    from app.data import facts, profile
    from app.verify import factcheck as FC
    import datetime as dt
    f = facts.build_facts("Thorncliffe Park")
    r = FC.check_text(text, f, profile.road_safety("055", dt.date(2026, 8, 1)))
    v = [(i["text"], i["verdict"]) for i in r["items"]]
    assert v[:3] == [("20", "match"), ("12", "match"), ("3", "match")]
    assert ("20", "direction") in v                                   # crashes "up" but the data says down
    assert "Traffic collisions: down 20% year to date vs 2025" in page


def test_meaning_check_not_run_is_said(client, monkeypatch):
    bid = draft_with_translation(client)          # no Azure key in tests: no back-translation
    page = html.unescape(client.get(f"/publish/{bid}?lang=ur").text)
    assert "Meaning check (read back in English) not run: it needs Azure Translator" in page


def test_share_image_button_carries_the_notices(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", urdu_translator)
    bid = draft_with_translation(client)
    client.post(f"/publish/{bid}")
    page = html.unescape(client.get(f"/publish/{bid}?lang=ur").text)
    from app.languages import LABELS
    assert 'data-card="ur"' in page and f'data-notice="{LABELS["ur"]["notice"]}"' in page and 'data-rtl="1"' in page
    assert 'data-card="en"' in page and 'data-notice=""' in page


def test_neighbourhood_feed(client, monkeypatch):
    import xml.dom.minidom
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    client.post(f"/publish/{bid}")
    other = draft(client, neighbourhood="Milliken")
    client.post(f"/publish/{other}")
    doc = xml.dom.minidom.parseString(client.get("/neighbourhoods/055/feed.xml").content)
    links = [l.getAttribute("href") for l in doc.getElementsByTagName("link")]
    assert any(bid in l for l in links) and not any(other in l for l in links)
    assert "Thorncliffe Park safety bulletins" in doc.getElementsByTagName("title")[0].firstChild.data
    assert client.get("/neighbourhoods/999/feed.xml").status_code == 404
    assert 'type="application/atom+xml"' in client.get("/neighbourhoods/055").text


def test_profile_this_year_by_month(client):
    from app.data import facts, profile
    f = facts.build_facts("Thorncliffe Park")        # August 2026; fake rows: 12 assaults in March, 4 in August
    mo = profile.this_year_by_month(f)
    a = next(s for s in mo["series"] if s["key"] == "Assault")
    assert mo["months"] == ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug"]
    assert a["values"] == [0, 0, 12, 0, 0, 0, 0, 4] and a["values"][-1] == next(c for c in f["categories"] if c["key"] == "Assault")["month"]
    page = html.unescape(client.get("/neighbourhoods/055").text)
    assert "This year, month by month" in page and "mini-bar-last" in page


def test_public_listing_rechecks_with_current_rules(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    client.post(f"/publish/{bid}")
    assert [b["id"] for b in main.published_bulletins()] == [bid]

    def tamper(r):  # a number changed in the file after publishing; the stored result still says OK
        r["english"]["bullets"][0] = r["english"]["bullets"][0].replace("4", "44", 1)
    store.update(bid, tamper)
    assert store.load(bid)["verification"]["en"]["ok"]
    assert main.published_bulletins() == []


def test_division_summary(client):
    from app.data import division
    divs = division.divisions()
    d53 = next(d for d in divs if d["number"] == 53)
    assert [h["name"] for h in d53["hoods"]] == sorted(h["name"] for h in d53["hoods"])     # A to Z, never ranked
    d = division.build_division(53)
    hood_sum = sum(r["ytd"] for r in d["rows"])
    assert d["totals"]["ytd"] == hood_sum and sum(c["ytd"] for c in d["categories"]) == hood_sum
    page = html.unescape(client.get("/divisions/53").text)
    assert "53 Division" in page and "never ranks neighbourhoods" in page and "can differ slightly" in page
    assert client.get("/divisions/99").status_code == 404
    assert 'href="/divisions/53"' in client.get("/neighbourhoods/055").text
    assert 'href="/divisions"' in client.get("/neighbourhoods").text


def test_home_monthly_routine(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    done = draft(client)                                          # Thorncliffe, August 2026 (latest)
    old = draft(client, neighbourhood="Milliken", month="2026-07")  # Milliken only has July
    page = html.unescape(client.get("/").text)
    assert "August 2026: 1 bulletin to make" in page
    assert f"/new?hood=Milliken&reuse={old}" in page and f'/publish/{done}' in page


def test_publisher_name_on_public_pages_only(client, monkeypatch):
    monkeypatch.setenv("PUBLISHER_NAME", "53 Division CPLC")
    monkeypatch.setenv("PUBLISHER_CONTACT", "cplc53@example.org")
    assert "Published by 53 Division CPLC" in client.get("/public").text
    assert "Published by <strong>53 Division CPLC</strong> · cplc53@example.org" in client.get("/neighbourhoods/055", headers={"x-nb-public": "1"}).text
    assert "53 Division CPLC" not in client.get("/neighbourhoods/055").text          # staff pages unchanged
    assert "Published by 53 Division CPLC" in client.get("/embed/055").text
    monkeypatch.setenv("PUBLISHER_NAME", "")
    assert "Published by" not in client.get("/public").text


def test_how_it_works_page(client):
    page = html.unescape(client.get("/how-it-works").text)
    assert "From open data to a checked bulletin in every language" in page and "Publishing is blocked until it's fixed" in page
    assert "The same, in words" in page and 'href="/how-it-works"' in client.get("/how-we-count").text


def test_check_example_stays_one_of_each_with_collisions(client):
    import datetime as dt
    from app.data import facts, profile
    from app.verify import factcheck as FC
    f = facts.build_facts("Thorncliffe Park")
    road = profile.road_safety("055", dt.date(2026, 8, 1))
    r = FC.check_text(FC.example_text(f, road), f, road)
    assert r["counts"]["not_found"] == 1 and r["counts"]["wrong_category"] == 1 and r["counts"]["direction"] == 1


def test_public_profile_has_in_browser_checker(client):
    import re
    pub = client.get("/neighbourhoods/055", headers={"x-nb-public": "1"}).text
    assert 'id="jsfc-form"' in pub and "/static/factcheck.js" in pub
    data = json.loads(html.unescape(re.search(r'<script type="application/json" id="jsfc-data">(.*?)</script>', pub, re.S).group(1)))
    assert data["neighbourhood"]["id"] == "055" and data["reported_crime"] and data["road_safety"]["ytd"]["total"] == 32
    assert 'id="jsfc-form"' not in client.get("/neighbourhoods/055").text          # staff use /check


def test_data_health_explains_each_dataset(client):
    page = html.unescape(client.get("/data-health").text)
    assert "About each dataset, in plain language" in page and "One record is:" in page
    assert "Neighbourhood Profiles 2021" in page and "A report is not a charge or a conviction." in page


def test_meeting_calendar_file(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, meeting_date="2026-11-10", meeting_time="19:00", meeting_place="Thorncliffe Park Public Library, Room 2")
    rec = store.load(bid)
    assert rec["officer"]["meeting_note"] == "Tuesday, November 10 at 7 p.m., Thorncliffe Park Public Library, Room 2"
    assert rec["verification"]["en"]["ok"]                                   # 10 and 7 come from the officer
    ics = client.get(f"/bulletin/{bid}/meeting.ics")
    assert ics.headers["content-type"].startswith("text/calendar")
    body = ics.text
    assert "DTSTART:20261111T000000Z" in body                                 # 7 p.m. EST (after DST) = 00:00 UTC next day
    assert "LOCATION:Thorncliffe Park Public Library\\, Room 2" in body and "\r\n" in body
    assert "not affiliated with or endorsed by the Toronto Police Service" in body
    assert f'/bulletin/{bid}/meeting.ics' in client.get(f"/publish/{bid}").text
    # a typed meeting note wins; no date means no calendar file
    other = draft(client, meeting_note="Every second Tuesday", meeting_date="2026-11-10")
    assert store.load(other)["officer"]["meeting_note"] == "Every second Tuesday"
    assert client.get(f"/bulletin/{draft(client)}/meeting.ics").status_code == 404
    assert client.get(f"/bulletin/{draft(client, meeting_date='not a date')}/meeting.ics").status_code == 404


def test_version_history_and_restore(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    first = store.load(bid)["english"]

    def save(headline):
        e = store.load(bid)["english"]
        client.post(f"/review/{bid}/save", data={**{k: v for k, v in e.items() if k != "bullets"}, "bullets": "\n".join(e["bullets"]), "headline": headline})
    save("Second headline")
    save("Third headline")
    rec = store.load(bid)
    assert [v["english"]["headline"] for v in rec["versions"]] == [first["headline"], "Second headline"]
    assert "Earlier versions (2)" in html.unescape(client.get(f"/review/{bid}").text)
    client.post(f"/review/{bid}/restore/0")
    rec = store.load(bid)
    assert rec["english"]["headline"] == first["headline"] and rec["versions"][-1]["english"]["headline"] == "Third headline"
    assert rec["audit"][-1]["action"] == "restored" and rec["verification"]["en"]["ok"]
    assert client.post(f"/review/{bid}/restore/99").status_code == 404
    for i in range(12):
        save(f"H{i}")
    assert len(store.load(bid)["versions"]) == 10


def test_division_with_missing_neighbourhoods_says_so_once(client, monkeypatch):
    from app.data import division, tps as T
    real = T.fetch_ytd
    d53 = next(d for d in division.divisions() if d["number"] == 53)
    keep = d53["hoods"][0]["id"]

    def some_down(hid):
        if hid != keep:
            raise T.TPSUnavailable("not saved for offline use")
        return real(hid)
    monkeypatch.setattr(T, "fetch_ytd", some_down)
    d = division.build_division(53)
    assert len(d["rows"]) == 1 and len(d["warnings"]) == (1 if len(d53["hoods"]) > 1 else 0)


def test_public_bulletin_page_has_qr_code(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    pub = client.get(f"/bulletin/{bid}/en", headers={"x-nb-public": "1"}).text
    assert 'id="qr-btn"' in pub and "/static/vendor/qrcode.min.js" in pub
    assert 'id="qr-btn"' not in client.get(f"/bulletin/{bid}/en").text


def test_ask_engines_guard_and_page(client, monkeypatch):
    from app.rag import answer as A, corpus
    monkeypatch.setenv("COHERE_API_KEY", "")
    # the neighbourhood is found in the question; documents carry the facts
    docs, hoods, _ = corpus.candidates("How many assaults in Thorncliffe Park last month?")
    assert [h["name"] for h in hoods] == ["Thorncliffe Park"]
    crime = next(d for d in docs if d.id == "crime-055")
    assert "Assaults: 4 in August; 16 so far in 2026; 20 in the same period of 2025 (down 20%)." in crime.text
    assert next(d for d in docs if d.id == "road-055").text.count("32 so far in 2026") == 1
    # rankings are declined before any model is called
    r = A.ask("Which is the most dangerous neighbourhood?", engine="extractive")
    assert r["declined"] and "doesn't rank" in r["answer"]
    # sources-only answer always works and cites the top documents
    r = A.ask("How many assaults in Thorncliffe Park last month?", engine="extractive")
    assert r["engine"] == "extractive" and r["sources"][0]["id"] == "crime-055" and r["guard"]["ok"]
    # local model: [n] markers become citations
    monkeypatch.setattr(llm, "complete", lambda system, user, **k: "There were 4 assaults in August 2026 [1].")
    r = A.ask("How many assaults in Thorncliffe Park last month?", engine="local")
    assert r["engine"] == "local" and r["guard"]["ok"] and r["citations"][0]["doc_ids"] == ["crime-055"]
    assert '<sup><a href="#src-1"' in str(A.render(r))
    # an invented number is held back, not shown
    monkeypatch.setattr(llm, "complete", lambda system, user, **k: "There were 44 assaults [1].")
    r = A.ask("How many assaults in Thorncliffe Park last month?", engine="local")
    assert not r["guard"]["ok"] and r["guard"]["unsupported"] == ["44"] and "44" not in r["answer"]
    # model down: falls back to the sources, says so
    monkeypatch.setattr(llm, "complete", model_down)
    r = A.ask("How many assaults in Thorncliffe Park last month?", engine="local")
    assert r["engine"] == "extractive" and any("unavailable" in w for w in r["warnings"])
    page = html.unescape(client.post("/ask", data={"question": "How many assaults in Thorncliffe Park last month?", "engine": "extractive"}).text)
    assert "Sources only (no AI)" in page and "every number found in the sources" in page and 'id="src-1"' in page
    assert client.get("/ask").status_code == 200 and 'href="/ask"' in client.get("/").text


def test_ask_with_cohere(client, monkeypatch, tmp_path):
    from app.ai import cohere
    from app.rag import answer as A, retrieve
    monkeypatch.setenv("COHERE_API_KEY", "test-key")
    monkeypatch.setattr(retrieve, "EMBED_CACHE", tmp_path / "emb.json")
    embedded = []

    def embed(texts, input_type, model=None):
        embedded.append((input_type, len(texts)))
        return [[1.0, float("assault" in t.lower())] for t in texts]
    monkeypatch.setattr(cohere, "embed", embed)
    monkeypatch.setattr(cohere, "rerank", lambda q, docs, top_n, model=None: [(i, 1 - i / 10) for i in range(min(top_n, len(docs)))])
    sent = {}

    def chat(messages, documents=None, **kw):
        sent.update(messages=messages, documents=documents)
        text = "There were 4 assaults in August 2026."
        return {"text": text, "citations": [{"start": 11, "end": 21, "text": "4 assaults", "doc_ids": [documents[0]["id"]]}]}
    monkeypatch.setattr(cohere, "chat", chat)
    r = A.ask("How many assaults in Thorncliffe Park and Milliken?", engine="auto")
    assert r["engine"] == "cohere" and r["retrieval"] == "cohere" and r["guard"]["ok"]
    assert sent["messages"][0]["role"] == "system" and "Never rank" in sent["messages"][0]["content"]
    assert all(set(d) == {"id", "data"} and set(d["data"]) == {"title", "text"} for d in sent["documents"])
    html_answer = str(A.render(r))
    assert '<span class="cite">4 assaults</span><sup>' in html_answer
    first = list(embedded)
    A.ask("How many assaults in Thorncliffe Park and Milliken?", engine="auto")   # documents' embeddings come from the cache
    assert [e for e in embedded[len(first):] if e[0] == "search_document"] == []
    # Cohere failing mid-way: lexical ranking and sources instead
    monkeypatch.setattr(cohere, "rerank", lambda *a, **k: (_ for _ in ()).throw(cohere.CohereUnavailable("down")))
    monkeypatch.setattr(cohere, "chat", lambda *a, **k: (_ for _ in ()).throw(cohere.CohereUnavailable("down")))
    r = A.ask("How many assaults in Thorncliffe Park?", engine="auto")
    assert r["retrieval"] == "bm25" and r["engine"] == "extractive"


def test_number_guard_ignores_list_numbering(client):
    from app.rag import answer as A
    from app.rag.corpus import Doc
    docs = [(Doc("d", "t", "Assaults: 4 in August.", "/", "crime"), 1.0)]
    assert A._number_guard("1. Assaults: 4 in August [1].\n2) No charge is implied.", docs, "q") == []
    assert A._number_guard("There were 14 assaults.", docs, "q") == ["14"]
    # a wrong number repeated from the question is still caught
    assert A._number_guard("Yes, there were 950 assaults [1].", docs, "Were there 950 assaults?") == ["950"]


def test_status_shows_cohere_setup(monkeypatch):
    from app import status
    monkeypatch.setenv("COHERE_API_KEY", "")
    assert status.check_cohere()["state"] == status.INFO and "add COHERE_API_KEY" in status.check_cohere()["detail"]
    monkeypatch.setenv("COHERE_API_KEY", "k")
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    item = status.check_cohere()
    assert item["state"] == status.OK and "rerank-v5.0-fast" in item["detail"] and "k" not in item["detail"].split(":")[0]


def test_ask_non_latin_question_uses_chosen_neighbourhood(client, monkeypatch):
    from app.rag import answer as A
    monkeypatch.setenv("COHERE_API_KEY", "")
    r = A.ask("گزشتہ ماہ تھورن کلف پارک میں کتنے حملے رپورٹ ہوئے؟", "055", engine="extractive")
    assert r["retrieval"].startswith("bm25 (no keyword match") and r["sources"][0]["id"] == "crime-055"
    assert "digits" in A.SYSTEM


def test_search_synonyms_are_stemmed():
    from app.rag import retrieve as R
    assert R.expand({"attacks", "reported"}) == {"attack", "assault", "report"}


def test_ask_in_another_language(client, monkeypatch):
    from app.ai import translate
    from app.rag import answer as A
    monkeypatch.setenv("COHERE_API_KEY", "")
    english = {"کتنے حملے؟": "How many attacks in the past month?", "سب سے خطرناک محلہ؟": "What is the most dangerous neighbourhood?"}
    monkeypatch.setattr(translate, "detect_language", lambda text: "ur" if not text.isascii() else "en")
    back = {"text": None}

    def tr(text, to, source=None):
        return english.get(text) if to == "en" else back["text"] or ("حملے: اگست میں " + " ".join(N for N in __import__("re").findall(r"\d+", text)))
    monkeypatch.setattr(translate, "translate_plain", tr)
    # a ranking question in Urdu is declined too
    assert A.ask("سب سے خطرناک محلہ؟", "055", engine="extractive")["declined"]
    r = A.ask("کتنے حملے؟", "055", engine="extractive")
    assert r["language"] == "ur" and r["question_en"].startswith("How many attacks")
    assert "Assaults: 4 in August; 16 so far in 2026" in r["answer"] and r["translated"]["code"] == "ur"
    assert sorted(__import__("re").findall(r"\d+", r["translated"]["text"])) == sorted(__import__("re").findall(r"\d+", __import__("re").sub(r"\[\d+\]", "", r["answer"])))
    back["text"] = "حملے: اگست میں 44"                 # a translation that changes a number is not shown
    r = A.ask("کتنے حملے؟", "055", engine="extractive")
    assert r["translated"] is None and any("same numbers" in w for w in r["warnings"])
    page = html.unescape(client.post("/ask", data={"question": "What languages are spoken in Thorncliffe Park?", "engine": "extractive"}).text)
    assert "machine translation of the answer above" not in page                       # English question: no translation


def test_public_deployment_mode(client, monkeypatch):
    import base64
    monkeypatch.setattr(llm, "complete", model_down)
    pub = draft(client)
    client.post(f"/publish/{pub}")
    priv = draft(client, neighbourhood="Milliken")
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    monkeypatch.setenv("STAFF_PASSWORD", "correct horse battery")
    monkeypatch.setenv("ALLOWED_HOSTS", ".trycloudflare.com")
    host = {"host": "blue-cat-123.trycloudflare.com"}
    # visitors: the resident site, through the tunnel's host name
    home = client.get("/", headers=host)
    assert home.status_code == 200 and "Latest bulletins" in home.text and "Make a bulletin" not in home.text
    # behind the tunnel: https from X-Forwarded-Proto; X-Forwarded-For never replaces the real peer
    tunnel = {**host, "x-forwarded-proto": "https", "x-forwarded-for": "203.0.113.9"}
    r = client.get("/", headers=tunnel)
    assert r.status_code == 200 and "https://blue-cat-123.trycloudflare.com" in r.text
    for path in ["/neighbourhoods", "/neighbourhoods/055", "/check", "/ask", "/how-it-works", "/data-health", f"/bulletin/{pub}/en", "/feed.xml"]:
        r = client.get(path, headers=host)
        assert r.status_code == 200, path
        assert "Saved bulletins" not in r.text, path
    # staff pages ask for the password; drafts stay hidden; other hosts are refused
    for path in ["/new", f"/review/{priv}", f"/publish/{pub}", "/history", "/status", "/translations", "/impact", "/api/neighbourhood/055",
                 f"/api/bulletins/{priv}/progress"]:
        r = client.get(path, headers=host)
        assert r.status_code == 401 and "Basic" in r.headers["www-authenticate"], path
    assert client.post("/draft", data={"neighbourhood": "Thorncliffe Park"}, headers=host).status_code == 401
    assert client.get(f"/bulletin/{priv}/en", headers=host).status_code == 404
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403
    bad = {**host, "authorization": "Basic " + base64.b64encode(b"staff:wrong").decode()}
    assert client.get("/new", headers=bad).status_code == 401
    # staff: full app with the password
    ok = {**host, "authorization": "Basic " + base64.b64encode(b"anyone:correct horse battery").decode()}
    assert client.get("/new", headers=ok).status_code == 200 and "Make a bulletin" in client.get("/", headers=ok).text
    assert client.get(f"/bulletin/{priv}/en", headers=ok).status_code == 200
    # no staff password configured: staff pages simply don't exist
    monkeypatch.setenv("STAFF_PASSWORD", "")
    assert client.get("/new", headers=host).status_code == 404


def test_form_keeps_everything_typed_when_it_comes_back_with_an_error(client):
    form = {"neighbourhood": "Atlantis", "priority": "Package thefts on Overlea", "action_taken": "Door-knocking",
            "meeting_note": "Tuesday at 7", "languages": ["ur", "ps"], "month": "2026-07", "tip_id": "car-lock",
            "followup": "Patrols done", "followup_status": "ongoing", "author": "Pat", "writer_mode": "template"}
    page = html.unescape(client.post("/draft", data=form).text)
    assert "Please check the form" in page and "“Atlantis” isn't one of Toronto's 158 neighbourhoods" in page
    for text in ("Package thefts on Overlea", "Door-knocking", "Tuesday at 7", "Patrols done", 'value="Pat"'):
        assert text in page, text
    assert 'value="ur" class="sr-only peer"  checked' in page and 'value="ps" class="sr-only peer"  checked' in page
    assert 'value="2026-07" selected' in page and 'value="car-lock"' in page and 'value="ongoing" selected' in page
    assert 'data-keep-langs="1"' in page and 'value="template" class="h-4 w-4" checked' in page
    # no neighbourhood at all: the same form with a message, not an error page
    r = client.post("/draft", data={"priority": "kept"})
    assert r.status_code == 200 and "Please choose a neighbourhood" in html.unescape(r.text) and "kept" in r.text
    # a month that can't be drafted: the officer's mistake, said so
    page = html.unescape(client.post("/draft", data={"neighbourhood": "Thorncliffe Park", "month": "2027-05"}).text)
    assert "Please check the form" in page and "We couldn't load the data" not in page


def test_take_down_a_published_bulletin(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    client.post(f"/publish/{bid}")
    assert [b["id"] for b in main.published_bulletins()] == [bid]
    page = html.unescape(client.get(f"/publish/{bid}").text)
    assert "Take it down (back to draft)" in page
    r = client.post(f"/publish/{bid}/unpublish", follow_redirects=False)
    assert r.headers["location"].endswith("taken_down=1")
    rec = store.load(bid)
    assert rec["status"] == "draft" and rec["audit"][-1]["action"] == "unpublished" and main.published_bulletins() == []
    assert "Taken down: it's a draft again" in html.unescape(client.get(f"/publish/{bid}?taken_down=1").text)
    assert bid not in client.get("/feed.xml").text


def test_no_false_all_clear_and_friendly_errors(client):
    for text, expect in [("", "No numbers to check in this text"), ("Assaults went down this year.", "No numbers to check in this text"),
                         ("Call 416-808-5300 on October 13 at 7 p.m.", "No statistics to check")]:
        page = html.unescape(client.post("/check", data={"neighbourhood": "Thorncliffe Park", "text": text}).text)
        assert expect in page and "Every number checks out" not in page, text
    r = client.get("/neighbourhoods/055?month=2027-01")
    assert r.status_code == 400 and "That month isn't available" in html.unescape(r.text) and 'href="/neighbourhoods/055"' in r.text
    assert client.get("/neighbourhoods/abc").status_code == 404
    r = client.get("/divisions/abc")
    assert r.status_code == 404 and "Division not found" in r.text


def test_error_page_links_fit_the_visitor(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    page = client.get("/divisions/abc").text
    assert 'href="/neighbourhoods"' in page and 'href="/history"' not in page and 'href="/new"' not in page
    monkeypatch.setenv("PUBLIC_DEPLOY", "false")
    assert 'href="/history"' in client.get("/divisions/abc").text


def test_review_and_live_site_ux(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    page = html.unescape(client.get(f"/review/{bid}").text)
    assert "Key:" in page and 'href="#translate-form"' in page and 'Save "Prepared by"' in page
    assert "Update sign-off" not in page
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    home = html.unescape(client.get("/").text)
    assert 'href="/check"' in home and 'href="/ask"' in home and "http://testserver/embed/055" in home
    assert 'id="ask-engine"' not in client.get("/ask").text
    monkeypatch.setenv("PUBLIC_DEPLOY", "false")
    static = html.unescape(client.get("/public", headers={"x-nb-public": "1"}).text)
    assert 'src="embed/055.html"' in static and 'href="/ask"' not in static     # the static site can't run Ask
    assert 'id="ask-engine"' in client.get("/ask").text


def test_status_offline_advice_is_safe():
    from app import status
    import datetime as dt
    item = status.check_snapshot(dt.date(2027, 1, 1), offline=False)
    assert "Save for offline" in item["fix"] and "snapshot_demo" not in item["fix"]


# ---------------------------------------------------------------- automatic bulletins (every neighbourhood)

def _auto_link(text):
    import re
    m = re.search(r"/bulletin/(auto-\d{3}-\d{4}-\d{2})/en", text)
    return m.group(1) if m else None


def test_automatic_bulletin_for_a_neighbourhood_without_one(client):
    page_ = client.get("/neighbourhoods/055")
    assert page_.status_code == 200 and "This month in plain language" in page_.text
    bid = _auto_link(page_.text)
    assert bid == "auto-055-2026-08"
    r = client.get(f"/bulletin/{bid}/en")
    assert r.status_code == 200
    assert "Automatic summary from open data" in r.text and "reports to police in Thorncliffe Park" in r.text
    assert "This month&#39;s priority" not in r.text and "From your officers" not in r.text
    assert "All numbers match" in r.text or "✅" in r.text
    assert client.get(f"/bulletin/{bid}/numbers.csv").status_code == 200
    assert client.get(f"/bulletin/{bid}/all").status_code == 200
    assert client.get(f"/bulletin/{bid}/ur").status_code == 404  # no translator here: English only


def test_automatic_bulletin_steps_aside_for_an_officer_bulletin(client, monkeypatch):
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    client.post(f"/publish/{bid}")
    page_ = client.get("/neighbourhoods/055", headers={"x-nb-public": "1"})
    assert _auto_link(page_.text) is None and f"/bulletin/{bid}/en" in page_.text


def test_automatic_bulletin_unknown_ids(client):
    for bid in ["auto-999-2026-09", "auto-055-2019-01", "auto-55-2026-09"]:
        assert client.get(f"/bulletin/{bid}/en").status_code == 404, bid


def test_automatic_translation_must_pass_every_check(client, monkeypatch):
    """A translation that changes a number is left out, and the failure is remembered (not retried)."""
    import json as _json
    import re
    from app import auto
    calls = []

    def bad(system, user, json=False, **kw):
        calls.append(1)
        data = _json.loads(user.split("\n\n")[0])
        tr = lambda t: re.sub(r"⟦N\d+⟧", "7", "خبر " + t)  # every number becomes 7
        return _json.dumps({k: ([tr(x) for x in v] if isinstance(v, list) else tr(v)) for k, v in data.items()}, ensure_ascii=False)
    monkeypatch.setattr(llm, "complete", bad)
    rec = auto.build("055", translate_missing=True)
    assert rec["languages"] == [] and calls
    n = len(calls)
    rec = auto.build("055", translate_missing=True)
    assert len(calls) == n and rec["untried"] == []


def test_automatic_translation_shown_when_checks_pass(client, monkeypatch):
    from app import auto
    monkeypatch.setattr(llm, "complete", urdu_translator)
    rec = auto.build("055", translate_missing=True)
    assert "ur" in rec["languages"] and rec["verification"]["ur"]["ok"]
    r = client.get(f"/bulletin/{rec['id']}/ur")
    assert r.status_code == 200 and "اوپن ڈیٹا سے خودکار خلاصہ" in r.text


def test_automatic_bulletin_on_public_deploy_and_embed(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    monkeypatch.setenv("STAFF_PASSWORD", "pw")
    monkeypatch.setenv("ALLOWED_HOSTS", ".trycloudflare.com")
    host = {"host": "x.trycloudflare.com"}
    assert client.get("/bulletin/auto-055-2026-08/en", headers=host).status_code == 200
    card = client.get("/embed/055", headers=host)
    assert card.status_code == 200 and "Automatic summary" in card.text and "/bulletin/auto-055-2026-08/en" in card.text


# ---------------------------------------------------------------- resident feedback

def test_resident_feedback_saved_anonymously_and_shown_to_staff(client, tmp_path):
    import json as _json
    page_ = client.get("/neighbourhoods/055")
    assert "What should your officers focus on next month?" in page_.text and 'action="/feedback/055"' in page_.text
    r = client.post("/feedback/055", data={"topic": "packages", "comment": "Parcels go missing from our lobby.",
                                            "useful": "yes", "lang": "ur", "back": "/neighbourhoods/055"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/neighbourhoods/055?thanks=1#feedback"
    assert "Thank you" in client.get(r.headers["location"]).text
    saved = [_json.loads(l) for l in (tmp_path / "feedback" / "055.jsonl").read_text().splitlines()]
    assert saved == [{"date": saved[0]["date"], "topic": "packages", "comment": "Parcels go missing from our lobby.",
                      "useful": "yes", "lang": "ur"}]           # no address, IP or name kept
    info = client.get("/api/neighbourhood/055").json()["residents"]
    assert info["count"] == 1 and info["topics"] == [["Package thefts", 1]] and info["comments"][0]["comment"].startswith("Parcels")
    assert 'id="residents"' in client.get("/new?hood=Thorncliffe%20Park").text


def test_resident_feedback_refuses_personal_details_links_and_floods(client):
    def post(**data):
        return client.post("/feedback/055", data={"back": "/neighbourhoods/055", **data}, follow_redirects=False)
    for comment in ["Call me at 416-555-1234", "me@example.com", "see www.spam.com"]:
        r = post(comment=comment)
        assert "fb_error=" in r.headers["location"], comment
    assert "fb_error=" in post().headers["location"]              # nothing picked
    assert "thanks=1" in post(topic="robberies").headers["location"]
    for _ in range(4):
        post(topic="assaults")
    r = post(topic="assaults")                                     # 6th answer within the hour
    assert "fb_error=" in r.headers["location"] and "already" in r.headers["location"]


def test_resident_feedback_only_returns_to_our_own_pages(client):
    r = client.post("/feedback/055", data={"topic": "other", "back": "https://evil.example/"}, follow_redirects=False)
    assert r.headers["location"].startswith("/neighbourhoods/055?")
    r = client.post("/feedback/055", data={"topic": "other", "back": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"].startswith("/neighbourhoods/055?")
    assert client.post("/feedback/999", data={"topic": "other"}).status_code == 404


def test_resident_feedback_on_public_deploy_but_not_on_the_static_site(client, monkeypatch):
    # the static site has no server to receive answers: no form there
    assert 'action="/feedback/055"' not in client.get("/neighbourhoods/055", headers={"x-nb-public": "1"}).text
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    monkeypatch.setenv("STAFF_PASSWORD", "pw")
    monkeypatch.setenv("ALLOWED_HOSTS", ".trycloudflare.com")
    host = {"host": "x.trycloudflare.com"}
    assert 'action="/feedback/055"' in client.get("/bulletin/auto-055-2026-08/en", headers=host).text
    r = client.post("/feedback/055", data={"topic": "road-safety", "back": "/bulletin/auto-055-2026-08/en"},
                    headers={**host, "cf-connecting-ip": "203.0.113.5"}, follow_redirects=False)
    assert r.status_code == 303 and "thanks=1" in r.headers["location"]
    assert client.get("/api/neighbourhood/055", headers=host).status_code == 401   # summaries are for staff


def test_residents_can_share_a_bulletin_in_its_language(client, monkeypatch):
    import html as _html
    monkeypatch.setattr(llm, "complete", urdu_translator)
    from app import auto
    rec = auto.build("055", translate_missing=True)
    page_ = client.get(f"/bulletin/{rec['id']}/ur", headers={"x-nb-public": "1"}).text
    assert 'id="wa-share"' in page_
    text = _html.unescape(page_.split('data-text="', 1)[1].split('"', 1)[0])
    assert rec["translations"]["ur"]["doc"]["headline"] in text and "Thorncliffe Park" in text


def test_ward_briefs(client, monkeypatch):
    from app.data import ward
    ws = ward.wards()
    assert len(ws) == 25 and ws[14]["name"] == "Don Valley West"
    page_ = client.get("/wards/15")
    assert page_.status_code == 200 and "Thorncliffe Park" in page_.text and "/bulletin/auto-055-2026-08/en" in page_.text
    assert "never ranks" in page_.text
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client)
    client.post(f"/publish/{bid}")
    assert f"/bulletin/{bid}/en" in client.get("/wards/15").text      # the officers' bulletin replaces the automatic one
    assert client.get("/wards/26").status_code == 404 and client.get("/wards/x").status_code == 404
    assert client.get("/wards").status_code == 200


def test_easy_read_version(client, monkeypatch):
    import re
    from app import easyread
    from app.verify import numbers as N
    monkeypatch.setattr(llm, "complete", model_down)
    bid = draft(client, meeting_date="2026-11-10", meeting_time="19:00", meeting_place="Library")
    r = client.get(f"/bulletin/{bid}/easy")
    assert r.status_code == 200 and "Easy Read" in r.text and "In an emergency, call 911." in r.text
    assert "%" not in re.sub(r"<[^>]+>|<style[\s\S]*?</style>", "", r.text.split("<article", 1)[1].split("</article>")[0])
    rec = store.load(bid)
    text = " ".join(t for _, t in easyread.lines(rec))
    allowed = set(N.extract_numbers(easyread.allowed_text(rec))) | set(rec["facts"]["allowed_numbers"])
    assert set(N.extract_numbers(text)) <= allowed, set(N.extract_numbers(text)) - allowed   # every number from the facts
    assert "You can come to a meeting on Tuesday, November 10." in text
    assert client.get("/bulletin/auto-055-2026-08/easy").status_code == 200


def test_usage_counts_are_totals_only_and_only_for_resident_visits(client, monkeypatch, tmp_path):
    import json as _json
    from app import usage
    client.get("/bulletin/auto-055-2026-08/en")                  # on this computer: never counted
    assert usage.last_days()["total"] == 0
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    monkeypatch.setenv("STAFF_PASSWORD", "pw")
    monkeypatch.setenv("ALLOWED_HOSTS", ".trycloudflare.com")
    host = {"host": "x.trycloudflare.com", "user-agent": "Mozilla/5.0 (iPhone)"}
    client.get("/bulletin/auto-055-2026-08/en", headers=host)
    client.get("/bulletin/auto-055-2026-08/easy", headers=host)
    client.get("/neighbourhoods/055", headers=host)
    client.get("/neighbourhoods/055", headers={**host, "user-agent": "Googlebot/2.1"})     # bots aren't residents
    client.get("/bulletin/auto-999-2026-08/en", headers=host)                               # 404s aren't reads
    import base64
    staff = {**host, "authorization": "Basic " + base64.b64encode(b"x:pw").decode()}
    client.get("/neighbourhoods/055", headers=staff)                                       # staff aren't residents
    u = usage.last_days()
    assert u["total"] == 3 and dict(u["by_kind"]) == {"bulletin": 1, "easy_read": 1, "profile": 1}
    assert u["bulletins_by_language"] == [("en", 1)]
    stored = _json.loads(next((tmp_path / "usage").glob("*.json")).read_text())
    assert set(stored) == {__import__("datetime").date.today().isoformat()}                # day totals, nothing else


def test_styles_are_prebuilt_not_compiled_in_the_browser(client):
    html = client.get("/").text
    assert "/static/tw.css" in html and "tailwind.js" not in html
    assert html.index("/static/app.css") < html.index("/static/tw.css")   # same cascade as the old in-browser compiler
    css = client.get("/static/tw.css").text
    for cls in (".bg-brand-900", ".max-w-\\[8\\.5in\\]", ".sm\\:grid-cols-2", ".text-ink"):
        assert cls in css, cls


def test_automatic_bulletin_leaves_out_a_translation_flagged_for_a_human_look(client, monkeypatch):
    """Numbers fine, but the 'French' is the English text: an officer would be asked to look; an
    automatic bulletin has no officer, so the language is left out."""
    from app import auto

    def echo(system, user, json=False, **kw):
        return user.split("\n\n")[0]
    monkeypatch.setattr(llm, "complete", echo)
    rec = auto.build("055", translate_missing=True)
    assert "fr" not in rec["languages"] and rec["translations"]["fr"]["failed_checks"]
    from app.verify import checks
    v = checks.verify_language({**rec, "languages": ["fr"]}, "fr")
    assert v["ok"] and [f["kind"] for f in v["quality"]["flags"]][0] == "untranslated"   # numbers fine: the flag left it out
    assert client.get(f"/bulletin/{rec['id']}/fr").status_code == 404


def test_officer_is_asked_to_get_flagged_lines_checked(client, monkeypatch):
    def echo(system, user, json=False, **kw):
        if not user.strip().startswith("{"):
            raise llm.LLMUnavailable("writer not faked")
        return user.split("\n\n")[0]
    monkeypatch.setattr(llm, "complete", echo)
    bid = draft(client, languages=["fr"])
    client.post(f"/review/{bid}/translate", data={"languages": ["fr"], "wait": "1"})
    page_ = client.get(f"/publish/{bid}?lang=fr").text
    assert "Please have someone who reads French check" in page_ and "still in English" in page_
    assert "👀" in client.get("/translations").text


# ---------------------------------------------------------------- translation memory

def tm_translator(system, user, json=False, **kw):
    """Fake model that behaves like the real path: locked category names (Urdu glossary), the
    neighbourhood's name and every number kept as placeholders, the rest 'translated'."""
    import json as _json
    import re
    from app.glossary import GLOSSARY, KEYS
    data = _json.loads(user.split("\n\n")[0]) if user.strip().startswith("{") else None
    if data is None:
        raise llm.LLMUnavailable("writer not faked in this test")

    def tr(t):
        head = ""
        for en_label, ur_label in zip(KEYS, GLOSSARY["ur"]):
            if t.startswith(en_label + ":"):
                head, t = ur_label + ": ", t[len(en_label) + 1:]
                break
        return (head + "خبر " + " ".join(re.findall(r"⟦[NP]\d+⟧%?", t)) + "۔").strip()
    return _json.dumps({k: ([tr(x) for x in v] if isinstance(v, list) else tr(v)) for k, v in data.items()}, ensure_ascii=False)


def test_memory_learns_standard_lines_and_they_win_over_a_new_machine_translation(client, monkeypatch):
    import shutil
    from app import auto, tm
    monkeypatch.setattr(llm, "complete", tm_translator)
    rec = auto.build("055", translate_missing=True)
    assert "ur" in rec["languages"]
    entries = tm.load("ur")
    assert all(e["status"] == "machine" for e in entries.values())
    ens = {e["en"] for e in entries.values()}
    assert "Assaults: {0} reports in August. {1} this year, down {2}% from {3} by this time last year." in ens
    assert "{name}: safety update for August {0}" in ens and any(e.startswith("In August, there were {0}") for e in ens)
    assert not any(e.startswith("Break-ins") for e in ens)   # "1 report ... 1 this year": can't tell the 1s apart
    # a different machine translation next time: the lines memory knows stay exactly as they were
    shutil.rmtree(auto.AUTO_DIR)

    def other(system, user, json=False, **kw):
        return tm_translator(system, user, json=json, **kw).replace("خبر", "نیا")
    monkeypatch.setattr(llm, "complete", other)
    rec2 = auto.build("055", translate_missing=True)
    b = rec2["translations"]["ur"]["doc"]["bullets"]
    assert b[0] == rec["translations"]["ur"]["doc"]["bullets"][0] and "نیا" in b[1]
    assert rec2["translations"]["ur"]["memory"]["from_memory"] >= 3 and rec2["verification"]["ur"]["ok"]


def test_memory_translates_a_bulletin_alone_and_reviewed_lines_earn_the_reviewed_badge(client, monkeypatch):
    from app import auto, tm
    from app.ai import translate
    monkeypatch.setattr(llm, "complete", tm_translator)
    rec = auto.build("055", translate_missing=True)
    en = dict(rec["english"], bullets=rec["english"]["bullets"][:1])      # only lines memory knows
    monkeypatch.setattr(llm, "complete", model_down)                        # no translator at all
    doc, info = translate.translate_doc(en, "ur", names=["Thorncliffe Park"])
    assert info["engine"] == "memory" and info["memory"]["from_memory"] == info["memory"]["lines"] == 4
    assert not info["memory"]["all_reviewed"]
    for k in tm.load("ur"):
        tm.review("ur", k, "A. Translator")
    doc, info = translate.translate_doc(en, "ur", names=["Thorncliffe Park"])
    assert info["memory"]["all_reviewed"]


def test_memory_review_keeps_the_slots_and_plurals_are_separate_entries(client, monkeypatch):
    from app import tm
    import pytest
    assert tm.key("ru", "Assaults: {0} reports", ["1"]) != tm.key("ru", "Assaults: {0} reports", ["3"])
    assert tm.key("ru", "Assaults: {0} reports", ["3"]) == tm.key("ru", "Assaults: {0} reports", ["4"])
    assert tm.key("zh-Hans", "Assaults: {0} reports", ["1"]) == tm.key("zh-Hans", "Assaults: {0} reports", ["7"])
    assert tm.pattern("Assaults: 6 reports in September. 95 this year, down 7% from 102 by this time last year.") == \
        ("Assaults: {0} reports in September. {1} this year, down {2}% from {3} by this time last year.", ["6", "95", "7", "102"])
    assert tm.pattern("This month's priority: Car break-ins near the plaza.") is None          # officers' words: never stored
    assert tm._template_from("خبر 5 5", ["5", "5"], "") is None                               # can't tell which 5 is which
    monkeypatch.setattr(llm, "complete", tm_translator)
    from app import auto
    auto.build("055", translate_missing=True)
    k = next(k for k, e in tm.load("ur").items() if "{1}" in e["tr"])
    with pytest.raises(ValueError):
        tm.review("ur", k, "A. Translator", new_text="خبر بغیر نمبر")
    e = tm.review("ur", k, "A. Translator", new_text=tm.load("ur")[k]["tr"] + " ")
    assert e["status"] == "reviewed" and e["reviewed_by"] == "A. Translator"


def test_translator_confirming_a_bulletin_reviews_its_standard_lines(client, monkeypatch):
    from app import tm
    monkeypatch.setattr(llm, "complete", tm_translator)
    bid = draft(client, languages=["ur"], writer_mode="template")
    client.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
    assert tm.load("ur") and all(e["status"] == "machine" for e in tm.load("ur").values())
    t = store.load(bid)["translations"]["ur"]["doc"]
    client.post(f"/publish/{bid}/edit/ur", data={**{k: t[k] for k in ("headline", "summary", "officer_section", "closing")},
                                                 "bullets": "\n".join(t["bullets"]), "human_reviewed": "1", "reviewer": "S. Khan"})
    assert all(e["status"] == "reviewed" and e["reviewed_by"] == "S. Khan" for e in tm.load("ur").values())


def test_memory_review_page(client, monkeypatch):
    from app import auto, tm
    monkeypatch.setattr(llm, "complete", tm_translator)
    auto.build("055", translate_missing=True)
    assert "Translation memory" in client.get("/translations/memory").text
    page_ = client.get("/translations/memory/ur").text
    assert "Approve" in page_ and "{0}" in page_
    k, e = next((k, e) for k, e in tm.load("ur").items() if "{1}" in e["tr"])
    r = client.post("/translations/memory/ur/review", data={"key": k, "text": e["tr"], "reviewer": ""}, follow_redirects=False)
    assert "error=" in r.headers["location"]                                        # a name is needed
    r = client.post("/translations/memory/ur/review", data={"key": k, "text": "خبر {0}", "reviewer": "S. Khan"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and tm.load("ur")[k]["status"] == "machine"   # a slot went missing
    r = client.post("/translations/memory/ur/review", data={"key": k, "text": e["tr"] + " news report", "reviewer": "S. Khan"}, follow_redirects=False)
    assert "error=" in r.headers["location"]                                        # English letters in Urdu
    r = client.post("/translations/memory/ur/review", data={"key": k, "text": e["tr"], "reviewer": "S. Khan"}, follow_redirects=False)
    assert "error=" not in r.headers["location"] and tm.load("ur")[k]["status"] == "reviewed"
    assert "Approved by S. Khan" in client.get("/translations/memory/ur").text
    assert client.get("/translations/memory/xx").status_code == 404


def test_automatic_bulletin_keeps_a_language_with_only_a_weak_flag(client, monkeypatch):
    from app import auto
    from app.verify import similarity as SIM
    monkeypatch.setattr(llm, "complete", tm_translator)
    monkeypatch.setattr(SIM, "thresholds", lambda: {"ur": {"bullet": {"score": 0.8, "ratio": [0.0, 99]}, "other": {"score": 0.8, "ratio": [0.0, 99]}}})
    monkeypatch.setattr(SIM, "line_scores", lambda en, tr: [{"field": "bullets", "index": 0, "score": 0.78, "en": en["bullets"][0], "tr": tr["bullets"][0]}])
    assert "ur" in auto.build("055", translate_missing=True)["languages"]        # near the edge: an officer would be asked
    import shutil
    shutil.rmtree(auto.AUTO_DIR)
    monkeypatch.setattr(SIM, "line_scores", lambda en, tr: [{"field": "bullets", "index": 0, "score": 0.5, "en": en["bullets"][0], "tr": tr["bullets"][0]}])
    assert "ur" not in auto.build("055", translate_missing=True)["languages"]    # far off: left out


def test_ranking_guard_declines_comparisons_but_not_comparisons_with_the_past():
    from app.rag.answer import RANKING
    for q in ["Is Agincourt North safer than Milliken?", "Is Milliken more dangerous than Thorncliffe Park?",
              "Compare crime in Milliken and Agincourt North", "Is my area safer than average?"]:
        assert RANKING.search(q), q
    for q in ["Was there more crime than last year in Milliken?", "Were assaults worse than the previous month?",
              "Is it safer than in 2024?", "How many break-ins in Thorncliffe Park last month?"]:
        assert not RANKING.search(q), q


def test_ask_is_rate_limited_on_public_deploy(client, monkeypatch):
    from app import main as M, ratelimit
    from app.rag import answer as A
    calls = []
    monkeypatch.setattr(A, "ask", lambda *a, **k: calls.append(1) or {"question": "q", "answer": "", "sources": [], "citations": [], "warnings": []})
    monkeypatch.setenv("PUBLIC_DEPLOY", "true")
    monkeypatch.setattr(M, "ASK_PER_VISITOR", 2)
    ratelimit.clear()
    for _ in range(3):
        r = client.post("/ask", data={"question": "How many assaults?"}, headers={"cf-connecting-ip": "203.0.113.5"})
    assert len(calls) == 2 and "Lots of questions right now" in r.text
    # another visitor still gets answers
    client.post("/ask", data={"question": "How many assaults?"}, headers={"cf-connecting-ip": "203.0.113.6"})
    assert len(calls) == 3
    # off a public deployment, CF-Connecting-IP isn't trusted (anyone could set it)
    monkeypatch.setenv("PUBLIC_DEPLOY", "false")
    assert ratelimit.visitor(type("R", (), {"headers": {"cf-connecting-ip": "1.2.3.4"}, "client": None})()) == ""
    ratelimit.clear()
