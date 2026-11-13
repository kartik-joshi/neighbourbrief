"""In-memory rate limits (no cookies, nothing written to disk): per visitor per hour, plus a total per hour
so a public deployment can't run through the Azure or Cohere quota."""
from __future__ import annotations

import threading
import time

_lock = threading.Lock()
_recent: dict[tuple[str, str], list[float]] = {}


def allow(bucket: str, visitor: str, limit: int, window: float = 3600) -> bool:
    """True and counted if `visitor` has made fewer than `limit` requests in `bucket` within `window` seconds."""
    now = time.time()
    with _lock:
        k = (bucket, visitor)
        times = [t for t in _recent.get(k, []) if now - t < window]
        if len(times) >= limit:
            _recent[k] = times
            return False
        times.append(now)
        _recent[k] = times
        if len(_recent) > 10000:  # keep memory bounded
            _recent.clear()
        return True


def clear() -> None:
    with _lock:
        _recent.clear()


def visitor(request) -> str:
    """Who to count. Behind the Cloudflare tunnel (PUBLIC_DEPLOY) every visitor arrives from the tunnel's
    address, so Cloudflare's CF-Connecting-IP is used; anywhere else that header could be made up."""
    from app.config import get_settings
    if get_settings().public_deploy and request.headers.get("cf-connecting-ip"):
        return request.headers["cf-connecting-ip"]
    return request.client.host if request.client else ""
