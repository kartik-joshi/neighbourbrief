#!/bin/bash
# Show NeighbourBrief from this Mac through a temporary public HTTPS link (Cloudflare quick tunnel),
# running the Docker image.
#
#     scripts/demo_tunnel.sh                 # link open for 2 hours, then everything shuts itself off
#     scripts/demo_tunnel.sh --hours 4       # 1-12 hours
#     scripts/demo_tunnel.sh --azure         # let staff translate with Azure (key from .env, passed to the container only)
#     scripts/demo_tunnel.sh --cohere        # let Ask use Cohere (key from .env, same way)
#     scripts/demo_tunnel.sh --new-password  # new staff password (do this after every demo)
#     scripts/demo_tunnel.sh --rebuild       # rebuild the image from this checkout first
#     scripts/demo_tunnel.sh --background    # keep running after this Terminal closes (log in deploy/logs/)
#     scripts/demo_tunnel.sh --stop          # close everything (if a run was killed)
#
# What visitors get: the resident site (home, neighbourhood profiles and maps, published bulletins in every
# language, Check my numbers, Ask with sources, How it works, Data health, Privacy and AI). Staff pages (make,
# review, publish, history, translations, status) ask for the staff password printed below.
#
# What keeps this Mac safe:
#   - Only the demo is reachable, only through the tunnel: the container listens on this Mac's loopback, no router
#     port is opened, and the link is a random name that stops working when the tunnel closes.
#   - It runs in its own container with its own data volume (the 3 demo bulletins), never your saved bulletins.
#   - A firewall in colima's VM stops the container from reaching this Mac (including other local apps and
#     Ollama) or the home network. It's checked from inside the container before the link opens: no check, no link.
#   - No API keys unless you ask (--azure / --cohere); then they're given to the running container only, which is
#     removed when the demo closes. Your .env never goes into the image.
#   - It closes itself after --hours, on Ctrl-C, and when this Terminal window closes (unless --background).
set -euo pipefail
cd "$(dirname "$0")/.."

HOURS=2 NEW_PASSWORD=0 REBUILD=0 STOP_ONLY=0 AZURE=0 COHERE=0 BACKGROUND=0
while [ $# -gt 0 ]; do
  case "$1" in
    --hours) HOURS=${2:?--hours needs a number}; shift ;;
    --new-password) NEW_PASSWORD=1 ;;
    --rebuild) REBUILD=1 ;;
    --stop) STOP_ONLY=1 ;;
    --azure) AZURE=1 ;;
    --cohere) COHERE=1 ;;
    --background) BACKGROUND=1 ;;
    -h|--help) sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown option $1 (see --help)"; exit 2 ;;
  esac
  shift
done
case "$HOURS" in ''|*[!0-9]*) echo "--hours must be a whole number"; exit 2 ;; esac
[ "$HOURS" -ge 1 ] && [ "$HOURS" -le 12 ] || { echo "--hours must be 1-12"; exit 2; }

NAME=neighbourbrief-demo
NETWORK=neighbourbrief-demo
SUBNET=172.31.251.0/24
VOLUME=neighbourbrief-demo-data
IMAGE=neighbourbrief:demo-local
PORT=8091
ENV_FILE=deploy/.env.demo
LOG_DIR=deploy/logs

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

if [ "$BACKGROUND" = 1 ]; then  # re-run detached, so closing this Terminal doesn't close the link
  mkdir -p "$LOG_DIR"
  out="$LOG_DIR/tunnel-$(date +%Y%m%d-%H%M%S).out"
  nohup "$0" --hours "$HOURS" $([ "$AZURE" = 1 ] && echo --azure) $([ "$COHERE" = 1 ] && echo --cohere) \
    $([ "$NEW_PASSWORD" = 1 ] && echo --new-password) $([ "$REBUILD" = 1 ] && echo --rebuild) > "$out" 2>&1 < /dev/null &
  echo "Started in the background (log: $out). The link and password appear in that file within a minute or two."
  echo "Close it early with: scripts/demo_tunnel.sh --stop"
  exit 0
fi

# --- firewall: the container may reach the internet, not this Mac or the home network --------------------------
firewall_on() {
  firewall_off
  colima ssh -- sudo sh -c "
    iptables -I DOCKER-USER 1 -s $SUBNET -d 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,100.64.0.0/10,169.254.0.0/16 \
      -m comment --comment neighbourbrief-demo -j REJECT &&
    iptables -I DOCKER-USER 1 -s $SUBNET -d $SUBNET -m comment --comment neighbourbrief-demo -j RETURN &&
    iptables -I DOCKER-USER 1 -s $SUBNET -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment neighbourbrief-demo -j RETURN"
}
firewall_off() {
  colima ssh -- sudo sh -c \
    "iptables -S DOCKER-USER | grep neighbourbrief-demo | sed 's/^-A /-D /' | while read -r rule; do eval iptables \$rule; done" \
    2>/dev/null || true
}

# From inside the container: this Mac and private addresses unreachable; TPS open data reachable.
check_isolation() {
  docker exec -i "$NAME" python - <<'PY'
import socket, sys
blocked = [("192.168.5.2", p) for p in (8000, 8010, 11434, 5432, 3000)]   # this Mac, as colima's VM sees it
try:
    blocked += [(socket.gethostbyname("host.docker.internal"), p) for p in (8000, 11434)]
except OSError:
    pass
leaks = []
for host, port in blocked:
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect((host, port)); leaks.append(f"{host}:{port}")
    except OSError:
        pass
    finally:
        s.close()
if leaks:
    sys.exit("the demo can still reach this Mac: " + ", ".join(leaks))
s = socket.create_connection(("services.arcgis.com", 443), timeout=10); s.close()
print("isolated: no route to this Mac or the home network; TPS open data reachable")
PY
}

TUNNEL_PID="" CAFFEINATE_PID="" WATCHDOG_PID=""
cleanup() {
  trap - EXIT INT TERM HUP
  echo
  say "Closing the demo..."
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "$WATCHDOG_PID" ] && kill "$WATCHDOG_PID" 2>/dev/null || true
  [ -n "$CAFFEINATE_PID" ] && kill "$CAFFEINATE_PID" 2>/dev/null || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  firewall_off
  [ "$(cat "$PID_FILE" 2>/dev/null)" = "$$" ] && rm -f "$PID_FILE"
  say "Closed: the link no longer works and the container is removed (the demo data volume is kept)."
}

# --- checks before anything is public -------------------------------------------------------------------------
command -v docker >/dev/null || die "Docker is not installed."
command -v colima >/dev/null || die "This script expects colima (it firewalls the demo inside colima's VM)."
command -v cloudflared >/dev/null || die "Install the tunnel client first: brew install cloudflared"
docker info >/dev/null 2>&1 || die "Docker isn't running. Start it: colima start --cpu 4 --memory 6 --disk 40"

# One demo at a time, tracked by a PID file (process names are unreliable: subshells share the command line).
PID_FILE=deploy/logs/demo.pid
running_pid() {
  local pid; pid=$(cat "$PID_FILE" 2>/dev/null || true)
  [ -n "$pid" ] && [ "$pid" != "$$" ] && kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= | grep -q demo_tunnel.sh && echo "$pid"
  return 0
}
if [ "$STOP_ONLY" = 1 ]; then
  pid=$(running_pid)
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    # Wait for its own clean-up to finish, so it can't remove a container or firewall rules started later.
    for _ in $(seq 1 60); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
  fi
  pkill -f "cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT" 2>/dev/null || true
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  firewall_off
  rm -f "$PID_FILE"
  say "Stopped."
  exit 0
fi
[ -z "$(running_pid)" ] || die "Another demo is still running (PID $(running_pid)). Stop it first: scripts/demo_tunnel.sh --stop"
mkdir -p deploy/logs && echo $$ > "$PID_FILE"

[ ! -f "$HOME/.cloudflared/config.yaml" ] && [ ! -f "$HOME/.cloudflared/config.yml" ] \
  || die "~/.cloudflared/config.yaml exists; quick tunnels don't work with it. Move it aside and retry."

if [ ! -f "$ENV_FILE" ] || [ "$NEW_PASSWORD" = 1 ]; then
  umask 077
  mkdir -p deploy
  printf '# demo_tunnel.sh: staff password for the public demo. Not committed. No API keys here, on purpose.\nSTAFF_PASSWORD=%s\n' \
    "$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)" > "$ENV_FILE"
  say "New staff password saved in $ENV_FILE."
fi
grep -Eq '^(AZURE|COHERE|OPENAI|ANTHROPIC)[A-Z_]*KEY=.+' "$ENV_FILE" && die "$ENV_FILE must not hold API keys."
STAFF_PASSWORD=$(grep -E '^STAFF_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)
[ "${#STAFF_PASSWORD}" -ge 12 ] || die "STAFF_PASSWORD in $ENV_FILE must be at least 12 characters."

# Keys only when asked for: read from .env into this process, handed to the container's environment only.
envval() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]#.*//' | tr -d '"' | xargs || true; }
KEYS=()
if [ "$AZURE" = 1 ]; then
  export AZURE_TRANSLATOR_KEY="$(envval AZURE_TRANSLATOR_KEY)" AZURE_TRANSLATOR_REGION="$(envval AZURE_TRANSLATOR_REGION)"
  [ -n "$AZURE_TRANSLATOR_KEY" ] || die "--azure needs AZURE_TRANSLATOR_KEY in .env"
  KEYS+=(-e AZURE_TRANSLATOR_KEY -e AZURE_TRANSLATOR_REGION)
fi
if [ "$COHERE" = 1 ]; then
  export COHERE_API_KEY="$(envval COHERE_API_KEY)"
  [ -n "$COHERE_API_KEY" ] || die "--cohere needs COHERE_API_KEY in .env"
  KEYS+=(-e COHERE_API_KEY)
fi

trap cleanup EXIT INT TERM HUP

# --- start the container, locked down -------------------------------------------------------------------------
if [ "$REBUILD" = 1 ] || ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  say "Building the image from this checkout (a few minutes the first time)..."
  mkdir -p "$LOG_DIR"
  docker build -t "$IMAGE" . > "$LOG_DIR/build.log" 2>&1 || die "The image build failed; see $LOG_DIR/build.log"
fi
docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create --subnet "$SUBNET" "$NETWORK" >/dev/null
docker rm -f "$NAME" >/dev/null 2>&1 || true
if [ "$REBUILD" = 1 ] && docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  # a new image may carry new demo bulletins; the volume only takes the image's copy when it's empty
  docker volume rm "$VOLUME" >/dev/null && say "Reset the demo bulletins to the ones in this checkout."
fi
firewall_on

say "Starting the container..."
docker run -d --rm --name "$NAME" --network "$NETWORK" -p "127.0.0.1:$PORT:8000" \
  -v "$VOLUME:/app/data/bulletins" \
  -e PUBLIC_DEPLOY=true -e STAFF_PASSWORD="$STAFF_PASSWORD" -e ALLOWED_HOSTS=".trycloudflare.com" \
  -e ALLOW_CLIENTS="172.16.0.0/12,192.168.0.0/16" -e DEMO_OFFLINE=false -e TRANSLATOR=auto \
  -e LLM_BASE_URL="http://host.docker.internal:11434/v1" \
  ${KEYS[@]+"${KEYS[@]}"} "$IMAGE" >/dev/null
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null || die "The app didn't start; see: docker logs $NAME"

say "Checking the firewall..."
check_isolation || die "Not opening the link: the container could reach this Mac."

say "Checking what visitors can and can't open..."
code() { curl -s -o /dev/null -w '%{http_code}' -H "Host: check.trycloudflare.com" "http://127.0.0.1:$PORT$1"; }
[ "$(code /)" = 200 ] && [ "$(code /neighbourhoods/055)" = 200 ] || die "Public pages aren't answering."
[ "$(code /new)" = 401 ] && [ "$(code /history)" = 401 ] || die "Staff pages are open without the password; not opening the link."

# --- open the link --------------------------------------------------------------------------------------------
mkdir -p "$LOG_DIR"
SCRIPT_PID=$$
END=$(( $(date +%s) + HOURS * 3600 ))
TUNNEL_LOG="$LOG_DIR/$(date +%Y%m%d-%H%M%S).log"
say "Opening the public link..."
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > "$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!
( while kill -0 "$SCRIPT_PID" 2>/dev/null && [ "$(date +%s)" -lt "$END" ]; do sleep 5; done
  kill "$TUNNEL_PID" 2>/dev/null ) >/dev/null 2>&1 &
WATCHDOG_PID=$!
URL=""
for _ in $(seq 1 45); do
  URL=$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -1 || true)
  [ -n "$URL" ] && break
  kill -0 "$TUNNEL_PID" 2>/dev/null || die "cloudflared stopped: $(tail -5 "$TUNNEL_LOG")"
  sleep 1
done
[ -n "$URL" ] || die "No link from Cloudflare after 45 s; see $TUNNEL_LOG"
for _ in $(seq 1 60); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/" 2>/dev/null)" = 200 ] && break
  sleep 2
done
# Same checks again, through the tunnel this time (proxy headers once made every page 403 out there).
pub() { curl -s -o /dev/null -w '%{http_code}' "$URL$1" 2>/dev/null; }
HOME_CODE=$(pub /)
if [ "$HOME_CODE" = 000 ]; then  # this Mac can't resolve the new name yet; nothing to check against
  say "Couldn't reach the link from this Mac yet (DNS); open it in a browser in a minute to check."
else
  [ "$HOME_CODE" = 200 ] || { kill "$TUNNEL_PID" 2>/dev/null; die "The public link doesn't serve the home page (HTTP $HOME_CODE); closed it. See: docker logs $NAME"; }
  [ "$(pub /new)" = 401 ] || { kill "$TUNNEL_PID" 2>/dev/null; die "Staff pages aren't closed through the link; closed it."; }
fi

caffeinate -i -w $$ &
CAFFEINATE_PID=$!

cat <<EOF

  ┌───────────────────────────────────────────────────────────────────────────┐
    NeighbourBrief demo is live
      Link            $URL
      Staff pages     any user name, password $STAFF_PASSWORD
      Closes at       $(date -r "$END" '+%-I:%M %p') (in $HOURS h), or press Ctrl-C
      Azure / Cohere  $([ "$AZURE" = 1 ] && echo on || echo off) / $([ "$COHERE" = 1 ] && echo on || echo off)
  └───────────────────────────────────────────────────────────────────────────┘
  Visitors see the resident site; staff pages ask for the password. A new link can take ~30 s to open everywhere.
  After the demo: --new-password next time.

EOF

while [ "$(date +%s)" -lt "$END" ]; do
  kill -0 "$TUNNEL_PID" 2>/dev/null || { say "The tunnel dropped; run the script again for a new link."; exit 1; }
  sleep 15
done
say "Time's up."
