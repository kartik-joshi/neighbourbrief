"""Re-translate the demo bulletins' languages with the current translation pipeline (e.g. after
glossary changes). English is unchanged. A new translation replaces the old one ONLY if it passes
the number and script checks; otherwise the existing verified translation is kept.

    .venv/bin/python scripts/retranslate_demo.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import store  # noqa: E402
from app.ai import translate  # noqa: E402
from app.verify import checks  # noqa: E402
from app.verify import numbers as N  # noqa: E402
from app.verify import script as S  # noqa: E402


def main() -> int:
    for rec in [r for r in store.list_all() if r.get("demo")]:
        name = rec["facts"]["neighbourhood"]["name"]
        for code in rec.get("languages", []):
            t0 = time.monotonic()
            try:
                doc, info = translate.translate_doc(rec["english"], code, names=[name])
            except translate.TranslationFailed as exc:
                print(f"  {name} {code}: failed ({exc}); kept existing")
                continue
            ok_num = N.check_translation(rec["english"], doc, rec["facts"]["allowed_numbers"])["ok"]
            ok_scr = S.check_doc(doc, code, rec["english"])["ok"]
            if ok_num and ok_scr:
                def fn(r, code=code, doc=doc, info=info):
                    r["translations"][code] = {"doc": doc, **info, "translated_at": store.now_iso()}
                    checks.verify_record(r)
                store.update(rec["id"], fn)
                verdict = "replaced"
            else:
                verdict = f"kept existing (new one failed: numbers {ok_num}, script {ok_scr})"
            print(f"  {name} {code}: {round(time.monotonic() - t0)} s, glossary {info.get('glossary')}, {verdict}")
        rec = store.load(rec["id"])
        checks.verify_record(rec)
        bad = checks.publish_blocked(rec)
        print(f"{name}: status {rec['status']}, all verified: {not bad}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
