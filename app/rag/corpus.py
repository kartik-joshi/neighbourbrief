"""Documents for retrieval, written from NeighbourBrief's own facts (the same numbers as the
bulletins and profiles), plus the counting rules, dataset notes and published bulletins.

Each document is short plain English with every number labelled, an id, a title and the page it
comes from, so an answer can cite it and a reader can click through."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from app.data import facts as F
from app.data import tps
from app.languages import normalize_name


@dataclass
class Doc:
    id: str
    title: str
    text: str
    url: str
    kind: str
    hood_id: str | None = None
    meta: dict = field(default_factory=dict)


def _change(pct, cur, prev):
    if pct is None:
        return ""
    return f" ({'up' if pct > 0 else 'down' if pct < 0 else 'no change'}{' ' + str(abs(pct)) + '%' if pct else ''})"


def hood_documents(hood_id: str) -> list[Doc]:
    """Crime, road safety, trends, month by month, languages, officers and services for one neighbourhood."""
    from app.data import profile as P
    p = P.build_profile(hood_id)
    f = p["facts"]
    n, per = f["neighbourhood"], f["period"]
    name, hid = n["name"], n["id"]
    month, year, prev_year = per["month_label"], per["year"], per["prev_year"]
    mname = month.split(" ")[0]
    url = f"/neighbourhoods/{hid}"
    docs = []
    lines = []
    for c in f["categories"]:
        prev = f"; {c['ytd_prev']} in the same period of {prev_year}" if c.get("ytd_prev") is not None else ""
        lines.append(f"{c['label']}: {c['month']} in {mname}; {c['ytd']} so far in {year}{prev}{_change(c.get('pct_change'), c['ytd'], c.get('ytd_prev'))}.")
    docs.append(Doc(f"crime-{hid}", f"Reported crime in {name}, {month}",
                    f"{name} ({n['division']}), {month}, the latest complete month (last month). Reports to police by report date ({per['ytd_label']} for the year so far). "
                    + " ".join(lines) + f" Total: {f['totals']['month']} reports in {mname}, {f['totals']['ytd']} so far in {year}. "
                    "A report to police does not mean anyone was charged.", url, "crime", hid))
    r = p.get("road")
    if r:
        def part(k, label):
            return f"{label}: {r['month'][k]} in {mname}, {r['ytd'][k]} so far in {year}, {r['ytd_prev'][k]} in the same period of {prev_year}."
        pc = r.get("pct_change")
        total = (f"All collisions: {r['month']['total']} in {mname}, {r['ytd']['total']} so far in {year}, "
                 f"{r['ytd_prev']['total']} in the same period of {prev_year}"
                 + (f" ({'up' if pc > 0 else 'down' if pc < 0 else 'no change'} {abs(pc)}%)." if pc else "."))
        docs.append(Doc(f"road-{hid}", f"Traffic collisions in {name}, {month}",
                        f"Traffic collisions in {name} by date of the collision. {total} "
                        + " ".join(part(k, lbl) for k, lbl in (("injury", "With injuries"), ("pedestrian", "Involving a pedestrian"),
                                                               ("cyclist", "Involving a cyclist"), ("fatal", "Fatal")))
                        + (f" {r['by_month'][-1]['label']} may still be incomplete (late reports)." if r.get("looks_partial") else ""),
                        url, "road", hid))
    t = p.get("trend")
    if t:
        docs.append(Doc(f"trend-{hid}", f"Yearly reports in {name}, {t['years'][0]} to {t['years'][-1]}",
                        f"Reports per calendar year in {name}. " + " ".join(
                            f"{s['label']}: " + ", ".join(f"{y}: {v}" for y, v in zip(t["years"], s["values"])) + "." for s in t["series"]),
                        url, "trend", hid))
    mo = p.get("monthly")
    if mo:
        docs.append(Doc(f"monthly-{hid}", f"Reports per month in {name}, {year}",
                        f"Reports per month in {name} in {year}, by report date. " + " ".join(
                            f"{s['label']}: " + ", ".join(f"{m} {v}" for m, v in zip(mo["months"], s["values"])) + "."
                            for s in mo["series"] if sum(s["values"])), url, "monthly", hid))
    c = p.get("census")
    if c and c.get("languages"):
        docs.append(Doc(f"languages-{hid}", f"Languages spoken at home in {name}",
                        f"Languages spoken at home in {name}: 2021 Census mother tongue (other than English or French), share of about "
                        f"{c.get('population', 0):,} residents: " + ", ".join(f"{l['language']} {l['share']}%" for l in c["languages"][:8]) + ".",
                        url, "languages", hid))
    nco = f.get("nco") or {}
    who = (f"Neighbourhood Community Officers for {name}: " + ", ".join(o["name"] for o in nco.get("officers", [])) + "."
           if nco.get("has_nco") else f"{name} has no Neighbourhood Community Officers listed; contact {(nco.get('division') or {}).get('name', n['division'])}"
           f" at {(nco.get('division') or {}).get('phone', '416-808-2222')}.")
    services = "; ".join(f"{s['name']} ({s['category']}), {s['address']}, {s['phone']}".rstrip(", ") for s in f.get("services", [])[:6])
    docs.append(Doc(f"help-{hid}", f"Officers and local help in {name}",
                    who + " Emergency: 911. Non-emergency police: 416-808-2222." + (f" Local support services: {services}." if services else ""),
                    url, "help", hid))
    return docs


RULES = [
    ("rule-counting", "How reports are counted", "Counts are reports made to police, by the date they were reported, in Toronto time. "
     "Each report counts once per category. A report does not mean anyone was charged or that a crime was proven."),
    ("rule-small-numbers", "Why some percentages are missing", "A percent change is shown only when both numbers are 10 or more. "
     "Small numbers jump around, so a big percentage would mislead. Shootings and homicides are always counts only."),
    ("rule-no-rankings", "No rankings", "NeighbourBrief never ranks or compares neighbourhoods as safer or more dangerous, shows no "
     "maps of incidents, and uses no labels like 'dangerous' or 'crime wave'."),
    ("rule-checks", "How numbers are checked", "Code calculates every number from open data; the AI only writes sentences. Every "
     "number in English and in every translation must match the data, each translation must use the right alphabet, and each "
     "translation is read back in English to make sure no line changed direction. If anything fails, publishing is blocked."),
    ("rule-refresh", "When the data is updated", "Toronto Police update the current-year data daily. While they reload it, it is "
     "empty or partly loaded for a few minutes; NeighbourBrief keeps the last good copy instead."),
    ("rule-privacy", "Privacy", "NeighbourBrief has no personal information about residents, no incident locations, no accounts, "
     "cookies or tracking. It is an independent prototype, not affiliated with or endorsed by the Toronto Police Service."),
    ("rule-languages", "Which languages are suggested", "Languages are suggested from the 2021 Census mother tongue for each "
     "neighbourhood: up to three languages spoken by at least 3% of residents that NeighbourBrief can translate."),
]
RULE_URLS = {"rule-privacy": "/privacy-ai"}


def global_documents() -> list[Doc]:
    docs = [Doc(i, t, x, RULE_URLS.get(i, "/how-we-count"), "rule") for i, t, x in RULES]
    from app.data.health import DATASETS, EXPLAIN
    for d in DATASETS:
        e = EXPLAIN.get(d[0])
        if e:
            docs.append(Doc(f"dataset-{d[0]}", f"Dataset: {d[1]}", f"{d[1]} ({d[2]}). One record is: {e[0]} Used for: {e[1]} "
                            f"Keep in mind: {e[2]}", "/data-health", "dataset"))
    return docs


def bulletin_documents(hood_id: str | None = None, latest: str | None = None) -> list[Doc]:
    """Published bulletins (officers' own words). Ones for an earlier month than `latest` say so."""
    from app import main
    out = []
    for b in main.published_bulletins():
        if hood_id and b["hood"]["id"] != hood_id:
            continue
        e = b["rec"]["english"]
        text = " ".join([e.get("headline", ""), e.get("summary", "")] + list(e.get("bullets") or [])
                        + [e.get("followup", ""), e.get("officer_section", ""), e.get("closing", "")]).strip()
        older = bool(latest) and b["month"] != latest
        title = f"Bulletin: {b['hood']['name']}, {b['month']}" + (f" (older; the latest data is {latest})" if older else "")
        out.append(Doc(f"bulletin-{b['id']}", title, text, f"/bulletin/{b['id']}/en", "bulletin", b["hood"]["id"],
                       meta={"older": older}))
    return out


def find_neighbourhoods(question: str, limit: int = 3) -> list[dict]:
    """Neighbourhoods named in the question (longest names first, so 'Agincourt South-Malvern West'
    beats 'Agincourt North')."""
    q = " " + normalize_name(question) + " "
    try:
        hoods = F.list_neighbourhoods()
    except tps.TPSUnavailable:
        return []
    found = []
    for h in sorted(hoods, key=lambda h: -len(h["name"])):
        nm = normalize_name(h["name"])
        if nm and re.search(rf"(?<![a-z0-9]){re.escape(nm)}(?![a-z0-9])", q) and not any(nm in normalize_name(x["name"]) for x in found):
            found.append(h)
        if len(found) >= limit:
            break
    return found


def candidates(question: str, hood_id: str | None = None) -> tuple[list[Doc], list[dict], list[str]]:
    """(documents to rank, neighbourhoods used, warnings)."""
    hoods = find_neighbourhoods(question)
    if hood_id and not any(h["id"] == hood_id for h in hoods):
        h = F.find_neighbourhood(hood_id)
        if h:
            hoods.insert(0, h)
    docs, warnings = global_documents(), []
    for h in hoods[:3]:
        latest = None
        try:
            hd = hood_documents(h["id"])
            docs += hd
            latest = next((d.title.split(", ")[-1] for d in hd if d.kind == "crime"), None)
        except (tps.TPSUnavailable, F.MonthNotAvailable, ValueError) as exc:
            warnings.append(f"{h['name']}: {exc}")
        docs += bulletin_documents(h["id"], latest)
    return docs, hoods[:3], warnings


def today() -> dt.date:
    return F.toronto_today()
