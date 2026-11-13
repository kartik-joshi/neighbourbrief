"""Bulletins are saved as JSON files in data/bulletins/ (no database, no personal data)."""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import threading
from pathlib import Path

from app.config import BULLETIN_DIR

_lock = threading.RLock()
_ID_RE = re.compile(r"^[a-z0-9-]{6,80}$")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(hood_name: str, month_start: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", hood_name.lower()).strip("-")[:40]
    return f"{slug}-{month_start[:7]}-{secrets.token_hex(3)}"


def _path(bid: str) -> Path:
    if not _ID_RE.match(bid or ""):
        raise KeyError(bid)
    return BULLETIN_DIR / f"{bid}.json"


def save(record: dict) -> dict:
    with _lock:
        record["updated_at"] = now_iso()
        path = _path(record["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
    return record


def load(bid: str) -> dict:
    with _lock:
        path = _path(bid)
        if not path.exists():
            raise KeyError(bid)
        return json.loads(path.read_text(encoding="utf-8"))


def update(bid: str, fn) -> dict:
    """Atomically load, modify (fn(record) mutates) and save."""
    with _lock:
        rec = load(bid)
        fn(rec)
        return save(rec)


def delete(bid: str) -> dict:
    """Move a bulletin to data/bulletins/.trash/ (recoverable by moving the file back)."""
    with _lock:
        rec = load(bid)
        trash = BULLETIN_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        _path(bid).replace(trash / f"{bid}.json")
        return rec


def _trash_path(bid: str) -> Path:
    _path(bid)  # validates the id
    return BULLETIN_DIR / ".trash" / f"{bid}.json"


def restore(bid: str) -> dict:
    """Move a bulletin back from the trash. Raises KeyError if it isn't there."""
    with _lock:
        src = _trash_path(bid)
        if not src.exists():
            raise KeyError(bid)
        dest = _path(bid)
        if dest.exists():
            raise FileExistsError(bid)
        src.replace(dest)
        return load(bid)


def list_trash() -> list[dict]:
    out = []
    for p in (BULLETIN_DIR / ".trash").glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(rec, dict) and "facts" in rec and "id" in rec:
                out.append((p.stat().st_mtime_ns, rec))
        except (OSError, json.JSONDecodeError):
            continue
    return [r for _, r in sorted(out, key=lambda t: t[0], reverse=True)]


MAX_IMPORT_FILES = 2000
MAX_IMPORT_BYTES = 50_000_000  # uncompressed


def backup_zip() -> bytes:
    """All bulletins (not the trash) as one zip, with a small manifest."""
    import io
    import zipfile
    buf = io.BytesIO()
    recs = list_all()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in recs:
            z.writestr(f"bulletins/{r['id']}.json", json.dumps(r, ensure_ascii=False, indent=1))
        z.writestr("manifest.json", json.dumps({"app": "NeighbourBrief TO", "created": now_iso(),
                                                "bulletins": len(recs)}, indent=1))
    return buf.getvalue()


def import_zip(data: bytes) -> dict:
    """Add bulletins from a backup zip. Never overwrites; skips anything that isn't a bulletin.
    Returns {"added": [...ids], "skipped_existing": [...], "rejected": [reasons]}."""
    import io
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("That file isn't a NeighbourBrief backup (.zip).")
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_IMPORT_FILES or sum(i.file_size for i in infos) > MAX_IMPORT_BYTES:
        raise ValueError("That backup is too large to import.")
    out = {"added": [], "skipped_existing": [], "rejected": []}
    with _lock:
        for info in infos:
            name = info.filename.rsplit("/", 1)[-1]
            if name == "manifest.json" or not name.endswith(".json"):
                continue
            try:
                rec = json.loads(z.read(info).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                out["rejected"].append(f"{name}: not valid JSON")
                continue
            bid = rec.get("id") if isinstance(rec, dict) else None
            if (not isinstance(bid, str) or not _ID_RE.match(bid) or name != f"{bid}.json"
                    or not isinstance(rec.get("facts"), dict) or not isinstance(rec.get("english"), dict)):
                out["rejected"].append(f"{name}: not a bulletin")
                continue
            if _path(bid).exists():
                out["skipped_existing"].append(bid)
                continue
            save(rec)
            out["added"].append(bid)
    return out


def list_all() -> list[dict]:
    out = []
    for p in BULLETIN_DIR.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or "facts" not in rec or "english" not in rec or "id" not in rec:
                continue  # not a bulletin (damaged or foreign file)
            # updated_at has 1-second precision; the file's mtime breaks ties (same-second saves)
            out.append((rec.get("updated_at", ""), p.stat().st_mtime_ns, rec))
        except (OSError, json.JSONDecodeError):
            continue
    out.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out = [t[2] for t in out]
    return out
