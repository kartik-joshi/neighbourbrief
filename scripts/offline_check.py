"""Offline demo check with the real local model: DEMO_OFFLINE=true and every non-localhost socket
blocked in code. Runs New → Draft → Translate (Urdu) → Publish for Thorncliffe Park from the snapshot.

    .venv/bin/python scripts/offline_check.py
"""
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DEMO_OFFLINE"] = "true"

_real_conn, _real_gai = socket.create_connection, socket.getaddrinfo
blocked = []


def _guard(host):
    if host not in ("127.0.0.1", "localhost", "::1", "testserver"):
        blocked.append(host)
        raise OSError(f"network disabled (offline check): {host}")


socket.create_connection = lambda a, *x, **k: (_guard(a[0]), _real_conn(a, *x, **k))[1]
socket.getaddrinfo = lambda h, *x, **k: (_guard(h), _real_gai(h, *x, **k))[1]

from fastapi.testclient import TestClient  # noqa: E402

from app import main, store  # noqa: E402
from app.data import tps  # noqa: E402

store.BULLETIN_DIR = Path(tempfile.mkdtemp())
tps.CACHE_DIR = Path(tempfile.mkdtemp())  # empty cache: must come from the snapshot
c = TestClient(main.app)
t0 = time.monotonic()
assert "Offline demo mode" in c.get("/").text
r = c.post("/draft", data={"neighbourhood": "Thorncliffe Park",
                           "priority": "Car break-ins near the plaza parking lots in the evening.",
                           "action_taken": "More evening patrols and a lock-your-car reminder session with the tenant association.",
                           "languages": ["ur"]}, follow_redirects=False)
bid = r.headers["location"].rsplit("/", 1)[-1]
rec = store.load(bid)
print(f"draft: {rec['draft_source']} in {rec['timings']['draft_seconds']} s, en ok={rec['verification']['en']['ok']}")
c.post(f"/review/{bid}/translate", data={"languages": ["ur"], "wait": "1"})
rec = store.load(bid)
print(f"ur: {rec['translations']['ur'].get('engine')} {rec['translations']['ur'].get('seconds')} s, {rec['verification']['ur']['summary']}")
c.post(f"/publish/{bid}")
print("published:", store.load(bid)["status"] == "published", f"({round(time.monotonic() - t0)} s)")
print("demo bulletins in History:", sum(1 for x in os.listdir(ROOT / "data/bulletins") if x.endswith(".json")))
print("blocked non-local connection attempts:", blocked or "none")
