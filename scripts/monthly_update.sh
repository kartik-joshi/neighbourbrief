#!/usr/bin/env bash
# Monthly update of the live public site with the latest complete month's numbers.
#
# Run by launchd (scripts/schedule_monthly_update.sh installs it) at 7 a.m. on the 4th to the 10th of each
# month. Each run:
#   1. checks it can reach the site repository over SSH and that the Azure key is set (.env);
#   2. stops if this month's update is already published;
#   3. waits (exits, to try again the next morning) until TPS has updated its data at least 3 days after
#      the month ended, so late reports for the last days of the month are in;
#   4. builds all 158 neighbourhoods, crawls the built site, and publishes only if the crawl is clean;
#   5. checks the live site, records the month as done and shows a Mac notification either way.
# Logs: deploy/logs/monthly-YYYY-MM.log
#
#   scripts/monthly_update.sh          # what launchd runs
#   scripts/monthly_update.sh --now    # publish now, even if this month was already published
#   scripts/monthly_update.sh --check  # only the checks in 1-3, never builds or publishes
set -uo pipefail
cd "$(dirname "$0")/.."
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export COHERE_API_KEY=""  # the site build never needs Cohere: keep the shared quota for Ask
OWNER=${NB_SITE_OWNER:-kartik-joshi}
SSH_HOST=${NB_SSH_HOST:-github-kartik}
REPO=neighbourbrief-to
URL="https://${OWNER}.github.io/${REPO}/"
MODE=${1:-}

MONTH=$(.venv/bin/python -c "from app.data import facts as F; print(F.last_full_month(F.toronto_today())[0].strftime('%Y-%m'))")
LOGDIR=deploy/logs; mkdir -p "$LOGDIR"
LOG="$LOGDIR/monthly-$MONTH.log"
MARK="$LOGDIR/site-published-$MONTH"
notify() { osascript -e "display notification \"$2\" with title \"NeighbourBrief: $1\"" >/dev/null 2>&1 || true; }
fail() { echo "FAILED: $1"; notify "site update failed" "$1 (see $LOG)"; exit 1; }
[ "$MODE" = "--check" ] || exec >>"$LOG" 2>&1
echo "== $(date '+%Y-%m-%d %H:%M') month $MONTH ${MODE}"

# 1. preflight
git ls-remote "git@${SSH_HOST}:${OWNER}/${REPO}.git" >/dev/null 2>&1 || fail "can't reach ${OWNER}/${REPO} over SSH (${SSH_HOST})"
grep -q '^AZURE_TRANSLATOR_KEY=.\+' .env || fail "AZURE_TRANSLATOR_KEY is empty in .env, so translations would be missing"
echo "preflight ok"

# 2. already done this month?
if [ -f "$MARK" ] && [ "$MODE" != "--now" ]; then echo "already published for $MONTH"; exit 0; fi

# 3. is the month complete in the TPS data?
READY=$(.venv/bin/python - <<'EOF'
import datetime as dt
from app.data import tps, facts as F
start, end = F.last_full_month(F.toronto_today())
try:
    fresh = F.to_toronto(tps.data_freshness_ms()).date()
except Exception as exc:  # TPS unreachable or mid-reload: try again tomorrow
    print(f"wait: TPS data not available ({type(exc).__name__})")
else:
    need = end + dt.timedelta(days=3)
    print("ready" if fresh >= need else f"wait: TPS data updated {fresh}, {start:%B} is complete once it's updated on or after {need}")
EOF
)
echo "$READY"
if [ "$MODE" = "--check" ]; then exit 0; fi
if [ "$READY" != "ready" ] && [ "$MODE" != "--now" ]; then
  [ "$(date +%d)" -ge 10 ] && notify "site not updated" "TPS data for $MONTH still looks incomplete; run scripts/monthly_update.sh --now to publish anyway"
  exit 0
fi

# 4. build, crawl, publish (caffeinate keeps the Mac awake for the 20-40 minute build)
notify "updating the site" "Building $MONTH for all 158 neighbourhoods (20-40 min)"
caffeinate -i .venv/bin/python scripts/build_public_site.py --hoods all --base-url "$URL" --out public_site || fail "site build failed"
.venv/bin/python scripts/check_public_site.py || fail "the built site has broken pages, so nothing was published"
scripts/publish_site.sh --no-build --owner "$OWNER" --ssh-host "$SSH_HOST" || fail "publishing to GitHub failed"

# 5. the live site
sleep 120
code=$(curl -s -o /dev/null -w '%{http_code}' "$URL")
[ "$code" = 200 ] || fail "the live site answered $code after publishing"
touch "$MARK"
echo "published $MONTH: $URL"
notify "site updated" "Live with $MONTH numbers: $URL"
