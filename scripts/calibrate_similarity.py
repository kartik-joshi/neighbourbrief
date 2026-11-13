"""Set the meaning-score thresholds (app/verify/similarity.py) from real translations, and measure what
they catch.

    .venv/bin/python scripts/calibrate_similarity.py

Good lines: every line of the translations that passed every check (automatic bulletins' cache and the
demo bulletins). Broken lines, made on purpose from the same translations:
  - dropped clause: the translation cut to its first sentence;
  - wrong line: an English line paired with another line's translation from the same bulletin
    (e.g. "Assaults: ..." against the Break-ins line: same shape, different crime and numbers);
  - unrelated: a category line paired with the closing's translation.
Per language and line type: score threshold = the 1st percentile of good lines' scores; length range =
the 1st to 99th percentile of good lines' length ratio (translation / English, in characters), which
catches dropped clauses the score can't see. About 1 good line in 100 gets flagged for a person.
Written to config/similarity_thresholds.json with the measured catch rates.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auto, store  # noqa: E402
from app.data import facts as F  # noqa: E402
from app.verify import similarity as SIM  # noqa: E402

_SENT = re.compile(r"(?<=[.!?。۔।])\s*")


def kind(field: str) -> str:
    return "bullet" if field == "bullets" else "other"


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[max(0, min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1)))))]


def main() -> int:
    recs = [auto.build(h["id"], translate_missing=False) for h in F.list_neighbourhoods()]
    recs += [r for r in store.list_all() if r.get("demo")]
    good, bad = defaultdict(list), defaultdict(lambda: defaultdict(list))
    ratio_good, ratio_bad = defaultdict(list), defaultdict(list)
    for r in recs:
        for code in r["languages"]:
            t = (r["translations"].get(code) or {}).get("doc")
            if not t:
                continue
            en = r["english"]
            scores = SIM.line_scores(en, t)
            if scores is None:
                print("bge-m3 isn't available: run `ollama pull bge-m3` and keep Ollama running.")
                return 1
            for s in scores:
                good[(code, kind(s["field"]))].append(s["score"])
                ratio_good[(code, kind(s["field"]))].append(len(s["tr"]) / max(1, len(s["en"])))
            # broken versions
            eb, tb = en.get("bullets") or [], t.get("bullets") or []
            cut = {"headline": "", "summary": "", "officer_section": "", "closing": "", "followup": "",
                   "bullets": [(_SENT.split(x.strip())[0] if len([p for p in _SENT.split(x.strip()) if p]) > 1 else "") for x in tb]}
            for s in SIM.line_scores(en, cut) or []:
                bad[(code, "bullet")]["dropped clause"].append(s["score"])
                ratio_bad[(code, "bullet")].append(len(s["tr"]) / max(1, len(s["en"])))
            if len(eb) > 1:
                shifted = dict(t, bullets=tb[1:] + tb[:1], headline="", summary="", closing="", officer_section="", followup="")
                for s in SIM.line_scores(en, shifted) or []:
                    bad[(code, "bullet")]["wrong line"].append(s["score"])
            if t.get("closing") and tb:
                odd = {"bullets": [t["closing"]] * len(tb)}
                for s in SIM.line_scores({"bullets": eb}, odd) or []:
                    bad[(code, "bullet")]["unrelated"].append(s["score"])
    out, report = {}, []
    for (code, k), xs in sorted(good.items()):
        if len(xs) < 8:
            continue
        thr = pct(xs, 1)
        rg = ratio_good[(code, k)]
        lo, hi = pct(rg, 1), pct(rg, 99)
        out.setdefault(code, {})[k] = {"score": round(thr, 4), "ratio": [round(lo, 3), round(hi, 3)]}
        caught = {name: f"{sum(1 for v in vs if v < thr) / len(vs):.0%}" for name, vs in bad[(code, k)].items() if vs}
        rb = ratio_bad.get((code, k)) or []
        if rb:
            from app.verify.quality import RATIO_MARGIN
            caught["dropped clause, by length"] = f"{sum(1 for v in rb if not lo * RATIO_MARGIN[0] <= v <= hi * RATIO_MARGIN[1]) / len(rb):.0%}"
        from app.verify.quality import RATIO_MARGIN
        flagged = sum(1 for v, r in zip(xs, rg) if v < thr or not lo * RATIO_MARGIN[0] <= r <= hi * RATIO_MARGIN[1]) / len(xs)
        report.append(f"{code:8} {k:6} n={len(xs):4} score>={thr:.3f} length {lo:.2f}-{hi:.2f} flags {flagged:.1%} of good | caught: {caught}")
    SIM.THRESHOLDS_FILE.write_text(json.dumps({"_model": SIM.MODEL, "_how": "scripts/calibrate_similarity.py", **out},
                                              ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(report))
    print(f"Wrote {SIM.THRESHOLDS_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
