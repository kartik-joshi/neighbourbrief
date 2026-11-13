#!/usr/bin/env bash
# Build the static public site (all 158 neighbourhoods, automatic bulletins, ward briefs) and publish it
# to GitHub Pages. Only the built site is pushed (plain HTML/CSS/JS/JSON), never the source code, .env,
# drafts or resident feedback.
#
#   scripts/publish_site.sh                 # build + publish to github.com/<you>/neighbourbrief-to
#   scripts/publish_site.sh --repo name     # another repository name
#   scripts/publish_site.sh --no-build      # publish what's already in public_site/
#   scripts/publish_site.sh --owner kartik-joshi --ssh-host github-kartik
#                                           # push over SSH to an account gh isn't signed in to (create the
#                                           # empty repo on github.com first; turn on Pages once, see below)
#
# Needs: gh (signed in), internet, and an Azure key in .env for the translated automatic bulletins
# (without one, they're English only). Building takes 20-40 minutes the first time in a month
# (translations are then cached in data/cache/auto/).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO_NAME=neighbourbrief-to
BUILD=1
OWNER="" SSH_HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_NAME="$2"; shift 2 ;;
    --no-build) BUILD=0; shift ;;
    --owner) OWNER="$2"; shift 2 ;;
    --ssh-host) SSH_HOST="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if [ -n "$SSH_HOST" ]; then
  [ -n "$OWNER" ] || { echo "--ssh-host needs --owner" >&2; exit 2; }
  # commit as this checkout's own git identity (git account), push over that account's SSH key
  NAME=$(git config user.name); EMAIL=$(git config user.email)
  REMOTE="git@${SSH_HOST}:${OWNER}/${REPO_NAME}.git"
  git ls-remote "$REMOTE" >/dev/null 2>&1 || { echo "Create an empty public repo ${OWNER}/${REPO_NAME} on github.com first." >&2; exit 1; }
else
  OWNER=${OWNER:-$(gh api user --jq .login)}
  NAME="NeighbourBrief"; EMAIL="$(gh api user --jq .id)+${OWNER}@users.noreply.github.com"   # no personal email
  REMOTE="https://github.com/$OWNER/$REPO_NAME.git"
fi
URL="https://${OWNER}.github.io/${REPO_NAME}/"
SITE=public_site
if [ "$BUILD" = 1 ]; then
  .venv/bin/python scripts/build_public_site.py --hoods all --base-url "$URL" --out "$SITE"
fi
[ -f "$SITE/index.html" ] || { echo "No site in $SITE/; build it first." >&2; exit 1; }
# never publish anything that isn't part of the site
if find "$SITE" -name '*.env' -o -name 'feedback*' -o -name '*.jsonl' | grep -q .; then
  echo "Refusing: $SITE contains files that must not be published." >&2; exit 1
fi
touch "$SITE/.nojekyll"
cat > "$SITE/README.md" <<EOF
# NeighbourBrief TO: public site

Monthly neighbourhood safety bulletins for Toronto's 158 neighbourhoods, in plain language and residents'
languages, with every number checked against Toronto Police Service open data. Live at ${URL}

Built automatically by NeighbourBrief TO (scripts/publish_site.sh).

Contains information licensed under the [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario)
(Toronto Police Service data) and the [Open Government Licence – Toronto](https://www.toronto.ca/city-government/data-research-maps/open-data/open-data-licence/)
(City of Toronto data). The data is provided as is, without warranty. Map tiles © OpenStreetMap contributors.
Third-party software notices: static/vendor/THIRD_PARTY_NOTICES.txt.
Not affiliated with or endorsed by the Toronto Police Service or the City of Toronto.
EOF
WORK=deploy/site-repo
rm -rf "$WORK"; mkdir -p "$WORK"
if [ -n "$SSH_HOST" ]; then
  NEW=0
elif ! gh repo view "$OWNER/$REPO_NAME" >/dev/null 2>&1; then
  gh repo create "$OWNER/$REPO_NAME" --public --description "NeighbourBrief TO public site: checked, multilingual neighbourhood safety bulletins for Toronto (not affiliated with TPS)" >/dev/null
  NEW=1
else
  NEW=0
fi
cp -R "$SITE"/. "$WORK"/
(
  cd "$WORK"
  git init -q -b main
  # same GitHub account as this checkout, for a global git hook that asks for one (git account), if used here
  ACCOUNT=$(git -C ../.. config --local account.owner || true)
  if [ -n "$ACCOUNT" ] && command -v git-account >/dev/null; then git account "$ACCOUNT" >/dev/null; fi
  git add -A
  git -c user.name="$NAME" -c user.email="$EMAIL" commit -q -m "Publish site $(date +%Y-%m-%d)"
  git push -q --force "$REMOTE" main
)
if [ -n "$SSH_HOST" ]; then
  echo "First time only: on github.com/${OWNER}/${REPO_NAME} → Settings → Pages, deploy from branch main, folder / (root)."
elif [ "$NEW" = 1 ] || ! gh api "repos/$OWNER/$REPO_NAME/pages" >/dev/null 2>&1; then
  gh api -X POST "repos/$OWNER/$REPO_NAME/pages" -f "source[branch]=main" -f "source[path]=/" >/dev/null
fi
rm -rf "$WORK"
echo "Published. It can take a minute or two to appear at: $URL"
