import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYTHONUTF8", "1")


# No test may reach the internet: TPS, Azure and model calls are faked. (Added after a page-sweep
# test was found calling live services through a newly added page.)
import socket as _socket

_real_connect = _socket.socket.connect


def _local_only(self, addr):
    if isinstance(addr, tuple) and (addr[0] not in ("127.0.0.1", "::1", "localhost") or addr[1] == 11434):
        raise OSError(f"network blocked in tests: {addr}")  # 11434: the local Ollama model
    return _real_connect(self, addr)


_socket.socket.connect = _local_only


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_translation_memory(monkeypatch, tmp_path):
    """Tests never read or write the real translation memory (data/translation_memory/)."""
    from app import tm
    monkeypatch.setattr(tm, "TM_DIR", tmp_path / "translation_memory")
