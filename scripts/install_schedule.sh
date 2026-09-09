#!/bin/bash
# Install (or reinstall) the daily crypto report LaunchAgent.
#
#   ./scripts/install_schedule.sh            # install, runs daily at 08:07
#   ./scripts/install_schedule.sh --uninstall
#   ./scripts/install_schedule.sh --run-now  # force one run immediately
#
# launchd fires a missed StartCalendarInterval job once on wake, so a sleeping
# Mac delays the report rather than skipping it entirely.
set -euo pipefail

LABEL="com.deepakbawa.cryptoreport"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

case "${1:-install}" in
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$TARGET"
    echo "uninstalled $LABEL"
    exit 0
    ;;
  --run-now)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "kicked off $LABEL; follow it with:"
    echo "  tail -f $ROOT/.tmp/logs/\$(date +%F).log"
    exit 0
    ;;
esac

if [ ! -x "$ROOT/.venv/bin/python" ]; then
  echo "error: $ROOT/.venv/bin/python not found. Create the venv first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

if [ ! -f "$ROOT/.env" ]; then
  echo "warning: $ROOT/.env not found - the scheduled run will fail without" >&2
  echo "         ANTHROPIC_API_KEY and the email credentials. Copy .env.example." >&2
fi

mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/.tmp/logs"

# Substitute the real project path into the plist template.
sed "s|__ROOT__|$ROOT|g" "$ROOT/scripts/$LABEL.plist" > "$TARGET"

# bootout first so a reinstall picks up changes; ignore "not loaded" on first run.
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"

echo "installed $LABEL -> $TARGET"
echo "  runs daily at 08:07 local"
echo "  status:    launchctl print $DOMAIN/$LABEL | head -20"
echo "  run now:   ./scripts/install_schedule.sh --run-now"
echo "  logs:      $ROOT/.tmp/logs/"
