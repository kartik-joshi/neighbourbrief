#!/usr/bin/env bash
# Run every automatic check; exit non-zero if any reports a problem.
#   scripts/check_all.sh
set -u
cd "$(dirname "$0")/.."
fail=0
echo "== styles (pre-built Tailwind up to date)"
if [ -d tools/tailwind/node_modules ]; then
  before=$(shasum app/static/tw.css); (cd tools/tailwind && npm run -s build >/dev/null 2>&1) || fail=1
  [ "$before" = "$(shasum app/static/tw.css)" ] && echo "up to date" || { echo "app/static/tw.css was stale: rebuilt, commit it"; fail=1; }
else echo "skipped (run: cd tools/tailwind && npm install)"; fi
echo "== pytest";        out=$(.venv/bin/python -m pytest -q 2>&1); echo "$out" | tail -1; echo "$out" | tail -1 | grep -q " passed" && ! echo "$out" | tail -1 | grep -q "failed" || fail=1
echo "== visual";        out=$(.venv/bin/python scripts/visual_check.py 2>&1); echo "$out" | grep -A20 "^Problems" || echo "$out" | tail -1; echo "$out" | grep -q "No automatic problems found." || fail=1
echo "== accessibility"; out=$(.venv/bin/python scripts/a11y_check.py 2>&1); echo "$out" | tail -1; echo "$out" | grep -q "^0 distinct violation types" || fail=1
echo "$out" | grep -q "Keyboard: OK" || { echo "keyboard test failed"; fail=1; }
echo "== JS checker parity"; out=$(.venv/bin/python scripts/js_parity_check.py 2>&1); echo "$out" | tail -1; echo "$out" | grep -q "agree on every sample" || fail=1
[ $fail -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOMETHING FAILED"
exit $fail
