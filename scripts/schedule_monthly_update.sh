#!/usr/bin/env bash
# Install (or remove) the monthly site update as a macOS launchd job for this user.
# It runs scripts/monthly_update.sh at 7 a.m. on days 4 to 10 of every month; a run the Mac slept through
# happens when it wakes. Nothing runs as root and nothing leaves this Mac except the site push.
#
#   scripts/schedule_monthly_update.sh            # install / update the schedule
#   scripts/schedule_monthly_update.sh --remove   # remove it
#   launchctl print gui/$(id -u)/com.neighbourbrief.monthly-site-update   # see its state
set -euo pipefail
cd "$(dirname "$0")/.."
LABEL=com.neighbourbrief.monthly-site-update
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
if [ "${1:-}" = "--remove" ]; then
  rm -f "$PLIST"; echo "Removed the monthly site update."; exit 0
fi
ROOT=$(pwd)
mkdir -p "$HOME/Library/LaunchAgents" deploy/logs
{
  cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/monthly_update.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StandardOutPath</key><string>$ROOT/deploy/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$ROOT/deploy/logs/launchd.log</string>
  <key>StartCalendarInterval</key>
  <array>
EOF
  for d in 4 5 6 7 8 9 10; do
    echo "    <dict><key>Day</key><integer>$d</integer><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>"
  done
  cat <<EOF
  </array>
</dict>
</plist>
EOF
} > "$PLIST"
plutil -lint "$PLIST" >/dev/null
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "Scheduled: 7 a.m. on days 4-10 of each month ($PLIST)."
echo "Test it now: launchctl kickstart $DOMAIN/$LABEL  (then see deploy/logs/)"
