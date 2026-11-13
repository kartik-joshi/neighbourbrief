"""Check the Cohere setup in about 5 seconds, after adding COHERE_API_KEY to .env.

    .venv/bin/python scripts/cohere_smoke.py

One tiny call each to embed, rerank and chat (with a document, to see a citation). Never prints the key.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.ai import cohere  # noqa: E402
from app.config import get_settings  # noqa: E402

HINTS = {"401": "The key was rejected: check COHERE_API_KEY in .env (no quotes or spaces), save, try again.",
         "404": "A model name wasn't found: set COHERE_CHAT_MODEL / COHERE_EMBED_MODEL / COHERE_RERANK_MODEL in .env "
                "to names listed at docs.cohere.com/docs/models.",
         "429": "Rate limited (trial keys allow few calls per minute): wait a minute and run it again.",
         "unreachable": "No connection to api.cohere.com: check the internet connection."}


def step(name, fn):
    t = time.monotonic()
    try:
        out = fn()
        print(f"✅ {name} ({time.monotonic() - t:.1f} s): {out}")
        return True
    except cohere.CohereUnavailable as exc:
        msg = str(exc)
        hint = next((h for k, h in HINTS.items() if k in msg), "")
        print(f"❌ {name}: {msg}" + (f"\n   → {hint}" if hint else ""))
        return False


def main() -> int:
    s = get_settings()
    if not s.cohere_api_key:
        print("❌ COHERE_API_KEY is empty. Open .env, paste the key after COHERE_API_KEY=, save, and run this again.")
        return 1
    if s.demo_offline:
        print("❌ DEMO_OFFLINE=true: Cohere is never called in offline mode. Set it to false to test.")
        return 1
    print(f"Key set ({len(s.cohere_api_key)} characters). Models: chat {s.cohere_chat_model}, embed {s.cohere_embed_model}, "
          f"rerank {s.cohere_rerank_model}\n")
    ok = step("Embed", lambda: f"{len(cohere.embed(['Assaults in Thorncliffe Park'], 'search_query')[0])} dimensions")
    ok &= step("Rerank", lambda: "best match: " + ["weather report", "assaults reported to police"][
        cohere.rerank("How many assaults?", ["weather report", "assaults reported to police"], top_n=1)[0][0]])
    ok &= step("Chat with a document", lambda: (lambda r: f"\"{r['text'][:90]}\" · citations: {[c['doc_ids'] for c in r['citations']]}")(
        cohere.chat([{"role": "user", "content": "How many assaults were reported in September?"}],
                    documents=[{"id": "crime-055", "data": {"title": "Reported crime in Thorncliffe Park, September 2026",
                                                            "text": "Assaults: 6 in September; 95 so far in 2026."}}],
                    max_tokens=80)))
    print("\nAll good: open /ask and choose 'Best available', or run scripts/rag_eval.py --engines cohere,local,extractive"
          if ok else "\nFix the ❌ above, then run this again.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
