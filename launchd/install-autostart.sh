#!/usr/bin/env bash
# Make JARVIS start automatically when you log in to your Mac.
#   ./launchd/install-autostart.sh            turn autostart on
#   ./launchd/install-autostart.sh --remove   turn autostart off
set -euo pipefail

LABEL="com.jarvis"
JARVIS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$JARVIS_DIR/launchd/com.jarvis.plist"
AGENTS_DIR="$HOME/Library/LaunchAgents"
TARGET="$AGENTS_DIR/$LABEL.plist"
DOMAIN="gui/$(id -u)"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Autostart only works on macOS."
  exit 1
fi

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
  rm -f "$TARGET"
  echo "Autostart is OFF. JARVIS will no longer start when you log in."
  exit 0
fi

case "$JARVIS_DIR" in
  *"&"* | *"<"* | *">"*)
    echo "The jarvis folder path contains a character autostart can't handle (&, < or >)."
    echo "Move the jarvis folder somewhere simpler, such as your home folder, and try again."
    exit 1 ;;
esac

if [ ! -x "$JARVIS_DIR/.venv/bin/python" ]; then
  echo "JARVIS isn't set up yet. Run ./setup.sh first."
  exit 1
fi

chmod +x "$JARVIS_DIR/launchd/start-jarvis.command"
mkdir -p "$AGENTS_DIR" "$JARVIS_DIR/logs"
sed "s|__JARVIS_DIR__|$JARVIS_DIR|g" "$TEMPLATE" > "$TARGET"

launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
if ! launchctl bootstrap "$DOMAIN" "$TARGET"; then
  echo "macOS refused to register the autostart. Try logging out and in, then run this again."
  exit 1
fi

echo "Autostart is ON. From now on JARVIS opens in a Terminal window when you log in."
echo "(It may have just opened one now, too.)"
echo "To turn it off:  ./launchd/install-autostart.sh --remove"
