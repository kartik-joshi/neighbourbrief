"""Get Saved bulletins back to the demo state before presenting.

    .venv/bin/python scripts/reset_demo.py          # shows what it would do (changes nothing)
    .venv/bin/python scripts/reset_demo.py --yes    # does it

Non-demo bulletins go to the trash (data/bulletins/.trash/, recoverable: move the file back or use
Saved bulletins → Restore). Demo bulletins changed since the last commit are restored from git.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402


def main(apply: bool) -> int:
    recs = store.list_all()
    demo = [r for r in recs if r.get("demo")]
    other = [r for r in recs if not r.get("demo")]
    rel = [str((store.BULLETIN_DIR / f"{r['id']}.json").relative_to(ROOT)) for r in demo]
    changed = subprocess.run(["git", "diff", "--name-only", "--", *rel], cwd=ROOT, capture_output=True, text=True).stdout.split()
    print(f"Demo bulletins ({len(demo)}): " + ", ".join(r["id"] for r in demo))
    print(f"  changed since last commit, to restore: {', '.join(changed) or 'none'}")
    print(f"Other bulletins to move to the trash ({len(other)}): " + (", ".join(r["id"] for r in other) or "none"))
    if not apply:
        print("\nNothing changed. Run again with --yes to do it.")
        return 0
    for r in other:
        store.delete(r["id"])
    if changed:
        subprocess.run(["git", "checkout", "--", *changed], cwd=ROOT, check=True)
    print("\nDone. Moved to the trash:", len(other), "· restored:", len(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main("--yes" in sys.argv))
