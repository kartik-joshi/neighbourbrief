"""Plain-text versions of a bulletin for WhatsApp (≤ 1000 chars) and email."""
from __future__ import annotations

import datetime as dt
from urllib.parse import quote

from app.languages import labels, month_label

WHATSAPP_LIMIT = 1000


def doc_for(rec: dict, code: str) -> dict:
    if code == "en":
        return rec["english"]
    return (rec.get("translations", {}).get(code) or {}).get("doc") or rec["english"]


def title(rec: dict, code: str) -> str:
    f = rec["facts"]
    month = month_label(dt.date.fromisoformat(f["period"]["month_start"]), code)
    return f"{labels(code)['title']} · {f['neighbourhood']['name']} · {month}"


def plain_text(rec: dict, code: str, limit: int | None = WHATSAPP_LIMIT) -> str:
    d = doc_for(rec, code)
    L = labels(code)
    head = [f"*{title(rec, code)}*", d.get("headline", "")]
    body = [d.get("summary", "")]
    bullets = [f"• {b}" for b in d.get("bullets", [])]
    officer = [x for x in (d.get("followup", ""), d.get("officer_section", "")) if x]
    tail = [d.get("closing", ""), L["emergency"]]
    if code != "en":
        tail.append(f"({L['reviewed'] if _reviewed(rec, code) else L['notice']})")
    tail.append("Source: Toronto Police Service open data. Not affiliated with TPS.")

    def join(parts):
        return "\n\n".join(p for p in parts if p).strip()

    text = join(head + body + ["\n".join(bullets)] + officer + tail)
    if limit is None or len(text) <= limit:
        return text
    # Too long. The closing lines (translation notice, source, not-affiliated) are mandatory, so
    # shorten the body instead: drop the summary, then trailing bullets, then hard-trim the body.
    text = join(head + ["\n".join(bullets)] + officer + tail)
    while len(text) > limit and len(bullets) > 1:
        bullets.pop()
        text = join(head + ["\n".join(bullets + ['…'])] + officer + tail)
    if len(text) <= limit:
        return text
    must = join([f"({L['reviewed'] if _reviewed(rec, code) else L['notice']})"] if code != "en" else [])
    must = join([must, "Source: Toronto Police Service open data. Not affiliated with TPS."])
    body = join(head + ["\n".join(bullets + ['…'])] + officer + [d.get("closing", ""), L["emergency"]])
    room = max(0, limit - len(must) - 3)
    return (body[:room].rstrip() + "…\n\n" + must) if room else must[:limit]


def _reviewed(rec: dict, code: str) -> bool:
    return bool((rec.get("translations", {}).get(code) or {}).get("human_reviewed"))


def newsletter_text(rec: dict, code: str) -> str:
    """The whole bulletin as plain text (no length limit) for CPLC newsletters and emails."""
    d = doc_for(rec, code)
    L = labels(code)
    f = rec["facts"]
    parts = [title(rec, code), d.get("headline", ""), d.get("summary", "")]
    if d.get("bullets"):
        parts.append(L["reports"] + "\n" + "\n".join(f"• {b}" for b in d["bullets"]))
    if d.get("officer_section") or d.get("followup"):
        sign = f"\n{rec['signoff']}, {f['neighbourhood']['division']}" if rec.get("signoff") else ""
        body = "\n".join(x for x in (d.get("followup", ""), d.get("officer_section", "")) if x)
        parts.append(L["from_officers"] + "\n" + body + sign)
    parts.append(d.get("closing", ""))
    nco = f.get("nco") or {}
    if nco.get("has_nco"):
        parts.append(L["ncos"] + "\n" + "\n".join(
            f"{o['name']}" + (f" · {o['phone']}" if o.get("phone") else "") + (f" · {o['email']}" if o.get("email") else "")
            for o in nco["officers"]))
    elif nco.get("division"):
        dv = nco["division"]
        parts.append(L["division"] + "\n" + " · ".join(x for x in [dv.get("name"), dv.get("address"), dv.get("phone"), dv.get("email")] if x))
    shown = rec.get("services_shown", [])
    services = [s for s in f.get("services", []) if s["id"] in shown]
    if services:
        parts.append(L["services"] + "\n" + "\n".join(
            f"{s['name']} · {s['address']}" + (f" · {s['phone']}" if s.get("phone") else "") for s in services)
            + "\n" + L["services_note"])
    parts.append(L["emergency"])
    if code != "en":
        parts.append(L["reviewed"] if _reviewed(rec, code) else L["notice"])
    p = f["period"]
    parts.append(
        "Prototype built on Toronto Police Service open data (Open Government Licence – Ontario). "
        "Not affiliated with or endorsed by the Toronto Police Service. Counts are reports to police by "
        f"report date ({p['month_label']}; year to date {p['ytd_label']} vs {p['ytd_prev_label']}); a report "
        f"does not mean anyone was charged. TPS data updated: {f['freshness']['tps_updated_label']}.")
    return "\n\n".join(x.strip() for x in parts if x and x.strip())


def whatsapp_url(text: str) -> str:
    return "https://wa.me/?text=" + quote(text, safe="")


MAILTO_MAX = 2000  # Outlook on Windows and some webmail handlers reject longer mailto: links


def mailto_url(rec: dict, code: str) -> str:
    """mailto: link that stays under ~2,000 characters. Non-Latin text is percent-encoded to up
    to 12 characters per letter, so the body is shortened until the whole link fits."""
    subject = quote(title(rec, code), safe="")
    for limit in (1800, 1200, 800, 500, 300, 160):
        url = f"mailto:?subject={subject}&body={quote(plain_text(rec, code, limit=limit), safe='')}"
        if len(url) <= MAILTO_MAX:
            return url
    d = doc_for(rec, code)  # last resort: headline only, and point to the printed/PDF version
    body = f"{d.get('headline', '')}\n\n(Full bulletin: see the attached PDF or printed copy.)"
    return f"mailto:?subject={subject}&body={quote(body, safe='')}"[:MAILTO_MAX]
