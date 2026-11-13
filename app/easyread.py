"""Easy Read version of a bulletin: one short sentence per line, a picture for each line, big type.
For residents with a learning disability or low literacy, and anyone who wants the short version.

Written by code from the checked facts (no AI), in English. Directions are said in words ("fewer",
"more", "about the same") and percentages are left out, as Easy Read guidance recommends.
"""
from __future__ import annotations

import datetime as dt

ICONS = {"Assault": "🤕", "Break and Enter": "🚪", "Auto Theft": "🚗", "Theft from Motor Vehicle": "🧳",
         "Robbery": "👜", "Theft Over": "💰", "Bike Theft": "🚲", "Shooting": "⚠️", "Homicide": "🕯️"}
EASY_NAMES = {"Assault": "someone was hurt or attacked", "Break and Enter": "someone broke into a home or business",
              "Auto Theft": "a car or truck was stolen", "Theft from Motor Vehicle": "something was stolen from a car",
              "Robbery": "someone took things by force", "Theft Over": "things worth over $5,000 were stolen",
              "Bike Theft": "a bike was stolen", "Shooting": "a gun was fired", "Homicide": "someone was killed"}


def _times(n: int) -> str:
    return "1 time" if n == 1 else f"{n} times"


def lines(rec: dict) -> list[tuple[str, str]]:
    """(icon, sentence) pairs."""
    f = rec["facts"]
    name = f["neighbourhood"]["name"]
    month = dt.date.fromisoformat(f["period"]["month_start"]).strftime("%B")
    by_key = {c["key"]: c for c in f["categories"]}
    out = [("📍", f"This is about {name}."),
           ("📅", f"It is about {f['period']['month_label']}."),
           ("👮", f"People told the police about {f['totals']['month']} things in {month}.")]
    for key in f["display"]:
        c = by_key.get(key)
        if not c:
            continue
        what = EASY_NAMES.get(key, c["label"].lower())
        out.append((ICONS.get(key, "•"), f"In {month}, no one told the police that {what}." if c["month"] == 0
                    else f"{_times(c['month']).capitalize()} in {month}, people said {what}."))
        if c["ytd_prev"] is not None and not c["counts_only"]:
            d = c["ytd"] - c["ytd_prev"]
            word = "about the same as" if abs(d) <= max(2, c["ytd_prev"] // 10) else ("fewer than" if d < 0 else "more than")
            out.append(("📊", f"This year so far: {c['ytd']}. That is {word} last year."))
    out.append(("⚖️", "Telling the police does not mean someone was charged."))
    officer = rec.get("officer") or {}
    if officer.get("priority"):
        out.append(("🎯", f"The police are working on this: {officer['priority'].rstrip('.')}."))
    meeting = officer.get("meeting")
    if meeting:
        when = dt.date.fromisoformat(meeting["date"]).strftime("%A, %B %-d")
        out.append(("🗣️", f"You can come to a meeting on {when}."))
    nco = f.get("nco") or {}
    if nco.get("has_nco") and nco.get("officers"):
        o = nco["officers"][0]
        out.append(("☎️", f"You can call your local officer, {o['name']}, at {o['phone']}." if o.get("phone")
                    else f"Your local officer is {o['name']}."))
    elif nco.get("division"):
        out.append(("☎️", f"You can call {nco['division']['name']} at {nco['division']['phone']}."))
    out.append(("🚨", "In an emergency, call 911."))
    out.append(("📞", "If it is not an emergency, call 416-808-2222."))
    return out


def allowed_text(rec: dict) -> str:
    """Numbers allowed besides the facts: officer contacts, emergency numbers, the date."""
    f, o = rec["facts"], rec.get("officer") or {}
    parts = [o.get("priority", ""), f["period"]["month_label"], "911 416-808-2222 5,000"]
    if o.get("meeting"):
        parts.append(dt.date.fromisoformat(o["meeting"]["date"]).strftime("%B %-d"))
    nco = f.get("nco") or {}
    parts += [x.get("phone", "") for x in nco.get("officers") or []] + [(nco.get("division") or {}).get("name", ""),
                                                                       (nco.get("division") or {}).get("phone", "")]
    return " ".join(parts)
