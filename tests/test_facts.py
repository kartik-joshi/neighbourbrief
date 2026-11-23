import datetime as dt

import pytest

from app.data import facts as F


def ms(y, m, d, h=0, tz="America/Toronto"):
    from zoneinfo import ZoneInfo
    return int(dt.datetime(y, m, d, h, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def test_last_full_month():
    assert F.last_full_month(dt.date(2026, 9, 29)) == (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert F.last_full_month(dt.date(2026, 1, 3)) == (dt.date(2025, 12, 1), dt.date(2025, 12, 31))
    assert F.last_full_month(dt.date(2024, 3, 1)) == (dt.date(2024, 2, 1), dt.date(2024, 2, 29))


def test_toronto_conversion_crosses_month_boundary():
    # 2026-09-01 02:30 UTC is still August 31 in Toronto (EDT, UTC-4).
    utc_ms = int(dt.datetime(2026, 9, 1, 2, 30, tzinfo=dt.timezone.utc).timestamp() * 1000)
    assert F.to_toronto(utc_ms).date() == dt.date(2026, 8, 31)
    # TPS stores dates as Toronto midnight: 2026-06-30T04:00Z -> June 30.
    assert F.to_toronto(1782792000000).date() == dt.date(2026, 6, 30)
    # Winter (EST, UTC-5): 2026-01-01T05:00Z is Jan 1 midnight.
    assert F.to_toronto(1767243600000).date() == dt.date(2026, 1, 1)


def test_dedupe_by_event_and_category():
    rows = [
        {"EVENT_UNIQUE_ID": "GO-1", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 8, 3)},
        {"EVENT_UNIQUE_ID": "GO-1", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 8, 3)},  # 2nd victim
        {"EVENT_UNIQUE_ID": "GO-1", "CRIME_TYPE": "Robbery", "REPORT_DATE_AGOL": ms(2026, 8, 3)},  # other type
        {"EVENT_UNIQUE_ID": "GO-2", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 8, 4)},
    ]
    df = F.dedupe_frame(rows, "EVENT_UNIQUE_ID", "CRIME_TYPE", "REPORT_DATE_AGOL")
    c = F.count_between(df, dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    assert c == {"Assault": 2, "Robbery": 1}


def test_dedupe_empty():
    df = F.dedupe_frame([], "EVENT_UNIQUE_ID", "CRIME_TYPE", "REPORT_DATE_AGOL")
    assert df.empty
    assert F.count_between(df, dt.date(2026, 8, 1), dt.date(2026, 8, 31)) == {}


@pytest.mark.parametrize("cur,prev,expected", [
    (12, 9, None),     # prev below 10 -> show counts only
    (9, 12, None),
    (10, 10, 0),
    (40, 32, 25),
    (89, 92, -3),
    (11, 10, 10),
    (13, 12, 8),       # 8.33 -> 8 (whole numbers)
])
def test_small_number_rule(cur, prev, expected):
    assert F.pct_change(cur, prev) == expected


def _lines(ytd_rows, mci_rows):
    ytd = F.dedupe_frame(ytd_rows, "EVENT_UNIQUE_ID", "CRIME_TYPE", "REPORT_DATE_AGOL")
    mci = F.dedupe_frame(mci_rows, "EVENT_UNIQUE_ID", "CSI_CATEGORY", "REPORT_DATE")
    return {l["key"]: l for l in F.category_lines(ytd, mci, dt.date(2026, 8, 1), dt.date(2026, 8, 31))}


def test_category_lines_periods_and_rules():
    ytd = [{"EVENT_UNIQUE_ID": f"A{i}", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 3, 1)} for i in range(15)]
    ytd += [{"EVENT_UNIQUE_ID": f"B{i}", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 8, 10)} for i in range(5)]
    ytd += [{"EVENT_UNIQUE_ID": "S1", "CRIME_TYPE": "Shooting", "REPORT_DATE_AGOL": ms(2026, 8, 2)}]
    ytd += [{"EVENT_UNIQUE_ID": "X1", "CRIME_TYPE": "Sexual Violation", "REPORT_DATE_AGOL": ms(2026, 8, 2)}]
    ytd += [{"EVENT_UNIQUE_ID": "L1", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 9, 2)}]  # after period
    mci = [{"EVENT_UNIQUE_ID": f"P{i}", "CSI_CATEGORY": "Assault", "REPORT_DATE": ms(2025, 5, 1)} for i in range(16)]
    mci += [{"EVENT_UNIQUE_ID": "P99", "CSI_CATEGORY": "Assault", "REPORT_DATE": ms(2025, 9, 15)}]  # outside same period
    lines = _lines(ytd, mci)
    a = lines["Assault"]
    assert (a["month"], a["ytd"], a["ytd_prev"]) == (5, 20, 16)
    assert a["pct_change"] == 25 and a["direction"] == "up"
    s = lines["Shooting"]
    assert s["month"] == 1 and s["pct_change"] is None and s["counts_only"]
    assert "Sexual Violation" not in lines
    assert lines["Theft from Motor Vehicle"]["ytd_prev"] is None  # no history outside MCI
    for l in lines.values():
        assert l["month"] >= 0 and l["ytd"] >= 0 and l["ytd"] >= l["month"]


def test_pick_display_quiet_month_has_three_lines():
    lines = _lines([], [])
    shown = F.pick_display(list(lines.values()))
    assert 3 <= len(shown) <= 6


def test_pick_display_caps_at_six_and_keeps_shootings():
    ytd = []
    for i, (key, _) in enumerate(F.CATEGORIES):
        ytd += [{"EVENT_UNIQUE_ID": f"{key}{j}", "CRIME_TYPE": key, "REPORT_DATE_AGOL": ms(2026, 8, 5)}
                for j in range(10 - i)]
    shown = F.pick_display(list(_lines(ytd, []).values()))
    assert len(shown) == 6
    assert "Shooting" in shown and "Homicide" in shown


def test_allowed_numbers_include_officer_text():
    facts = {
        "period": {"year": 2026, "prev_year": 2025, "month_last_day": 31, "month_number": 8,
                   "days_in_month": 31},
        "categories": [{"label": "Assaults", "month": 5, "ytd": 20, "ytd_prev": 16, "pct_change": 25}],
        "totals": {"month": 5, "ytd": 20},
        "neighbourhood": {"division": "53 Division"},
    }
    allowed = F.allowed_numbers(facts, {"meeting_note": "Oct 14 at 7 pm, 45 Overlea Blvd"})
    for n in ["2026", "2025", "31", "5", "20", "16", "25", "53", "14", "7", "45"]:
        assert n in allowed


def test_clean_hood_name_and_division():
    assert F.clean_hood_name("Thorncliffe Park (55)") == "Thorncliffe Park"
    assert F.division_label("D53") == "53 Division"


def test_data_warnings():
    end = dt.date(2026, 8, 31)
    assert F.data_warnings(end, dt.date(2026, 8, 30), dt.date(2026, 9, 29), dt.date(2026, 9, 29)) == []
    w = F.data_warnings(end, dt.date(2026, 8, 30), dt.date(2026, 9, 20), dt.date(2026, 9, 29))
    assert len(w) == 1 and "9 days ago" in w[0]
    w = F.data_warnings(end, dt.date(2026, 8, 25), dt.date(2026, 8, 29), dt.date(2026, 9, 1))
    assert any("may be incomplete" in x for x in w)
    w = F.data_warnings(end, dt.date(2026, 8, 12), dt.date(2026, 9, 29), dt.date(2026, 9, 29))
    assert len(w) == 1 and "August 12" in w[0]


def test_available_months_and_parse():
    months = F.available_months(dt.date(2026, 9, 29))
    assert months[0] == dt.date(2026, 8, 1) and months[-1] == dt.date(2026, 1, 1) and len(months) == 8
    assert F.parse_month("2026-02", dt.date(2026, 9, 29)) == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert F.parse_month("", dt.date(2026, 9, 29)) == (dt.date(2026, 8, 1), dt.date(2026, 8, 31))
    for bad in ("2026-09", "2025-12", "banana"):  # current (incomplete) month, other year, junk
        with pytest.raises(F.MonthNotAvailable):
            F.parse_month(bad, dt.date(2026, 9, 29))
    # In January the list is last year's months (newest first); build_facts then explains if the
    # current-year feed has already moved on (see test_app.test_january_bug_…).
    jan = F.available_months(dt.date(2027, 1, 10))
    assert jan[0] == dt.date(2026, 12, 1) and len(jan) == 12


def test_effective_today_offline_uses_each_neighbourhoods_save_date(monkeypatch, tmp_path):
    import json
    from app import snapshot
    monkeypatch.setattr(snapshot, "SNAPSHOT_DIR", tmp_path)
    (tmp_path / "snapshot_meta.json").write_text(json.dumps({
        "snapshot_date": "2026-10-02",
        "saved": {"055": {"name": "Thorncliffe Park", "date": "2026-09-29"},
                  "130": {"name": "Milliken", "date": "2026-10-02"}}}))
    monkeypatch.setenv("DEMO_OFFLINE", "true")
    assert F.effective_today("055") == dt.date(2026, 9, 29)   # → last full month August
    assert F.effective_today("130") == dt.date(2026, 10, 2)   # → last full month September
    assert F.effective_today() == dt.date(2026, 10, 2)
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    assert F.effective_today("055") == F.toronto_today()


def test_neighbourhood_names_with_typographic_apostrophes_and_dashes():
    from app.languages import normalize_name
    assert normalize_name("Parkwoods–O’Connor Hills") == normalize_name("Parkwoods-O'Connor Hills (149)")
    assert normalize_name("Westminster — Branson") == normalize_name("Westminster-Branson")


def test_census_languages_cover_all_158_neighbourhoods():
    from app.languages import census_languages, other_languages, suggested_languages
    data = census_languages()
    assert len(data) == 158 and all(len(k) == 3 for k in data)
    # 2021 Census (City Neighbourhood Profiles): top translatable mother tongues ≥ 3%
    assert [l["code"] for l in suggested_languages("Thorncliffe Park", "055")] == ["ur", "ps", "ar"]
    assert [l["code"] for l in suggested_languages("Milliken", "130")] == ["zh-Hant", "zh-Hans", "ta"]
    assert [l["code"] for l in suggested_languages("Westminster-Branson")] == ["ru", "fil"]  # by name too
    # Gujarati became translatable on Oct 23, 2026 (was listed as "no translation yet" here)
    assert [l["code"] for l in suggested_languages("Morningside Heights", "144")] == ["ta", "fil", "gu"]
    # Punjabi became translatable too: it now leads West Humber-Clairville's suggestions
    assert [l["code"] for l in suggested_languages("West Humber-Clairville", "001")][0] == "pa"
    assert any(l["language"] == "Tibetan" and l["share"] > 6 for l in other_languages("South Parkdale", "085"))
    assert all(l["share"] >= 3 for l in suggested_languages("Thorncliffe Park", "055"))
    # every neighbourhood gets an answer (possibly empty), never an error
    for hid, h in data.items():
        suggested_languages(h["name"], hid)


def test_layer_refresh_guard_keeps_the_last_good_copy(tmp_path, monkeypatch):
    """Live on Oct 23, 2026 the current-year layer was empty for a while after TPS's daily refresh.
    Data fetched then must never replace a good copy (or become a bulletin full of zeros)."""
    import pytest
    from app.data import tps
    monkeypatch.setattr(tps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(tps, "SNAPSHOT_DIR", tmp_path / "snap")
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    total = {"n": 42000}
    monkeypatch.setattr(tps, "layer_total", lambda layer: total["n"])
    rows = {"n": [{"EVENT_UNIQUE_ID": "A1"}]}
    monkeypatch.setattr(tps, "query_all", lambda layer, where, fields: rows["n"])
    assert tps.fetch_ytd("055") == [{"EVENT_UNIQUE_ID": "A1"}]          # good copy cached
    (tmp_path / "ytd_055.json").touch()
    import os, time
    os.utime(tmp_path / "ytd_055.json", (time.time() - 13 * 3600,) * 2)   # cache expired
    total["n"], rows["n"] = 0, []                                          # TPS mid-refresh: empty layer
    assert tps.fetch_ytd("055") == [{"EVENT_UNIQUE_ID": "A1"}]          # stale good copy, not []
    total["n"] = 9000                                                       # partly reloaded
    assert tps.fetch_ytd("055") == [{"EVENT_UNIQUE_ID": "A1"}]
    with pytest.raises(tps.TPSUnavailable, match="being refreshed"):     # no copy at all: say so
        tps.fetch_ytd("130")
    total["n"], rows["n"] = 43000, [{"EVENT_UNIQUE_ID": "B2"}]             # refresh finished
    assert tps.fetch_ytd("130") == [{"EVENT_UNIQUE_ID": "B2"}]


def test_refresh_guard_baseline_comes_from_any_check(tmp_path, monkeypatch):
    """A partly reloaded layer is refused even before the guard's first fetch, as long as the size
    was seen anywhere (Status page, Data health)."""
    import pytest
    from app.data import tps
    monkeypatch.setattr(tps, "CACHE_DIR", tmp_path)
    tps.note_total("YTD_CRIME_WM", 42107, "2026")
    tps.note_total("YTD_CRIME_WM", 19372, "2026")          # a partial total never lowers the baseline
    assert tps.baseline("YTD_CRIME_WM", "2026") == 42107
    monkeypatch.setattr(tps, "layer_total", lambda layer: 19372)
    with pytest.raises(tps.TPSUnavailable, match="partly loaded: 19,372 of about 42,107"):
        tps.guard_complete("YTD_CRIME_WM", "2026")
    assert tps.baseline("YTD_CRIME_WM", "2027") == 0        # a new year starts a new baseline


def test_october_bulletin_across_the_dst_change(monkeypatch):
    """The November jam's bulletins are for October 2026; DST ends Nov 1. A report at 23:30 on
    Oct 31 Toronto time (03:30 UTC on Nov 1) belongs to October; 00:30 on Nov 1 does not."""
    import datetime as dt
    from app.data import facts as F, tps

    def ms(y, mo, d, h=12, mi=0):
        return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp() * 1000)
    rows = [{"EVENT_UNIQUE_ID": "A", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 11, 1, 3, 30), "DIVISION": "D53"},  # Oct 31 23:30 EDT
            {"EVENT_UNIQUE_ID": "B", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 11, 1, 4, 30), "DIVISION": "D53"},  # Nov 1 00:30 EDT
            {"EVENT_UNIQUE_ID": "C", "CRIME_TYPE": "Assault", "REPORT_DATE_AGOL": ms(2026, 10, 1, 4, 0), "DIVISION": "D53"}]   # Oct 1 00:00 EDT
    monkeypatch.setattr(tps, "fetch_ytd", lambda hid: rows)
    monkeypatch.setattr(tps, "fetch_mci_year", lambda hid, y: [])
    monkeypatch.setattr(tps, "fetch_nco_all", lambda: [])
    monkeypatch.setattr(tps, "fetch_neighbourhood_list_from_ytd", lambda: [{"HOOD_158": "055", "NEIGHBOURHOOD_158": "Thorncliffe Park (55)", "DIVISION": "D53"}])
    monkeypatch.setattr(tps, "fetch_assets", lambda hid: [])
    monkeypatch.setattr(tps, "data_freshness_ms", lambda: ms(2026, 11, 10))
    f = F.build_facts("055", today=dt.date(2026, 11, 15))
    a = next(c for c in f["categories"] if c["key"] == "Assault")
    assert f["period"]["month_label"] == "October 2026" and f["period"]["ytd_label"] == "January 1 – October 31, 2026"
    assert a["month"] == 2 and a["ytd"] == 2          # A and C in October; B is November
    from app.data import provenance
    q = provenance.queries(f)["Assault"]["month"]
    assert "2026-10-01+04%3A00%3A00" in q and "2026-11-01+04%3A00%3A00" in q   # Toronto midnights in UTC


def test_last_year_for_theft_from_vehicles_and_bike_thefts(monkeypatch):
    """The Major Crime Indicators dataset has no theft from vehicles or bike thefts; their own TPS
    datasets (by report date) give last year's same-period count. Shootings stay counts only."""
    import datetime as dt
    from app.data import facts as F, tps

    def ms(y, mo, d):
        return int(dt.datetime(y, mo, d, 16, tzinfo=dt.timezone.utc).timestamp() * 1000)
    ytd = [{"EVENT_UNIQUE_ID": f"T{i}", "CRIME_TYPE": "Theft from Motor Vehicle", "REPORT_DATE_AGOL": ms(2026, 3, 1 + i), "DIVISION": "D53"} for i in range(12)]
    ytd += [{"EVENT_UNIQUE_ID": "S1", "CRIME_TYPE": "Shooting", "REPORT_DATE_AGOL": ms(2026, 9, 3), "DIVISION": "D53"}]
    last_year = {"Theft from Motor Vehicle": [{"EVENT_UNIQUE_ID": f"P{i}", "REPORT_DATE": ms(2025, 4, 1 + i)} for i in range(20)]
                 + [{"EVENT_UNIQUE_ID": "LATE", "REPORT_DATE": ms(2025, 11, 20)}],   # after the same period: not counted
                 "Bike Theft": []}
    calls = []

    def fake(key, hid, year):
        calls.append((key, year))
        return [{**r, "CATEGORY": key} for r in last_year[key]]
    monkeypatch.setattr(tps, "fetch_ytd", lambda hid: ytd)
    monkeypatch.setattr(tps, "fetch_mci_year", lambda hid, y: [])
    monkeypatch.setattr(tps, "fetch_category_year", fake)
    monkeypatch.setattr(tps, "fetch_nco_all", lambda: [])
    monkeypatch.setattr(tps, "fetch_neighbourhood_list_from_ytd", lambda: [{"HOOD_158": "055", "NEIGHBOURHOOD_158": "Thorncliffe Park (55)", "DIVISION": "D53"}])
    monkeypatch.setattr(tps, "fetch_assets", lambda hid: [])
    monkeypatch.setattr(tps, "data_freshness_ms", lambda: ms(2026, 10, 10))
    f = F.build_facts("055", today=dt.date(2026, 10, 15))
    by = {c["key"]: c for c in f["categories"]}
    assert ("Theft from Motor Vehicle", 2025) in calls
    assert by["Theft from Motor Vehicle"]["ytd"] == 12 and by["Theft from Motor Vehicle"]["ytd_prev"] == 20
    assert by["Theft from Motor Vehicle"]["pct_change"] == -40
    assert by["Bike Theft"]["ytd_prev"] == 0
    assert by["Shooting"]["ytd_prev"] is None and by["Shooting"]["pct_change"] is None

    def down(key, hid, year):
        raise tps.TPSUnavailable("offline")
    monkeypatch.setattr(tps, "fetch_category_year", down)
    by = {c["key"]: c for c in F.build_facts("055", today=dt.date(2026, 10, 15))["categories"]}
    assert by["Theft from Motor Vehicle"]["ytd_prev"] is None   # no data: no comparison, never a made-up 0


def test_layer_schema_never_keeps_map_styling(monkeypatch, tmp_path):
    """TPS's map styling holds signed S3 links with temporary AWS key IDs (flagged by secret scanners on
    GitHub): neither the cache nor what callers get may contain it, even from an older cached copy."""
    import json

    from app.data import tps
    monkeypatch.setenv("DEMO_OFFLINE", "false")
    monkeypatch.setattr(tps, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(tps, "SNAPSHOT_DIR", tmp_path / "snap")
    link = "https://s3.amazonaws.com/x.png?X-Amz-Credential=ASIAEXAMPLEEXAMPLE12%2F20230603"
    meta = {"fields": [{"name": "OCC_DATE"}], "drawingInfo": {"renderer": {"symbol": {"url": link}}}}
    monkeypatch.setattr(tps, "_get", lambda url, params: meta)
    assert "drawingInfo" not in tps.schema("SOME_LAYER") and tps.schema("SOME_LAYER")["fields"]
    assert "X-Amz" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))
    (tmp_path / "schema_OLD_LAYER.json").write_text(json.dumps(meta))  # a copy saved before the fix
    assert "drawingInfo" not in tps.schema("OLD_LAYER")
