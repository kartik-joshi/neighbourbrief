"""Short, plain-language prevention tips an officer can add to a bulletin (optional).

Each tip is linked to TPS categories so the form can suggest the ones that match the month's
reports. Text is written at about a Grade 5 level and contains no numbers except the TPS
non-emergency line in the reporting tip.
"""
from __future__ import annotations

TIPS = [
    {"id": "car-lock", "topic": "Theft from vehicles", "categories": ["Theft from Motor Vehicle"],
     "text": "Lock your car and keep bags, phones and packages out of sight, even for a few minutes."},
    {"id": "car-theft", "topic": "Stolen vehicles", "categories": ["Auto Theft"],
     "text": "Park in a garage or a well-lit spot, and consider a steering wheel lock."},
    {"id": "key-fob", "topic": "Stolen vehicles", "categories": ["Auto Theft"],
     "text": "Keep key fobs away from doors and windows at home, or in a signal-blocking pouch."},
    {"id": "home-lock", "topic": "Break-ins", "categories": ["Break and Enter"],
     "text": "Lock doors and windows, even when you are home, and use light timers when you are away."},
    {"id": "locker", "topic": "Break-ins", "categories": ["Break and Enter"],
     "text": "Use a strong lock on storage lockers and report broken doors or lights to your building manager."},
    {"id": "bike", "topic": "Bike thefts", "categories": ["Bike Theft"],
     "text": "Lock your bike frame and wheel to a fixed rack with a U-lock, and register the serial number."},
    {"id": "walking", "topic": "Robberies", "categories": ["Robbery"],
     "text": "Stay aware of your surroundings and keep your phone and valuables out of sight when walking."},
    {"id": "packages", "topic": "Thefts", "categories": ["Theft Over", "Theft from Motor Vehicle"],
     "text": "Have packages delivered when someone is home, or use a pickup point."},
    {"id": "neighbours", "topic": "Community", "categories": [],
     "text": "Get to know your neighbours and look out for each other."},
    {"id": "report", "topic": "Reporting", "categories": [],
     "text": "To report a crime that is not an emergency, call 416-808-2222 or report online at tps.ca."},
]
_BY_ID = {t["id"]: t for t in TIPS}
TIP_MAX_CHARS = 200


def get(tip_id: str) -> dict | None:
    return _BY_ID.get(tip_id)


def suggested(categories_on_bulletin: list[str]) -> list[dict]:
    """All tips, the ones matching this bulletin's categories first (stable order otherwise)."""
    cats = set(categories_on_bulletin)
    return sorted(TIPS, key=lambda t: 0 if cats & set(t["categories"]) else 1)


def resolve(tip_id: str, custom: str) -> str:
    """The tip text to use: the officer's own words win over a library pick."""
    custom = " ".join((custom or "").split())
    if custom:
        return custom
    t = get(tip_id or "")
    return t["text"] if t else ""
