#!/usr/bin/env bash
# Starts JARVIS in a Terminal window. Double-click this file in Finder, or let
# the autostart (install-autostart.sh) open it when you log in.
cd "$(dirname "$0")/.." || exit 1

for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in *":$dir:"*) ;; *) [ -d "$dir" ] && PATH="$dir:$PATH" ;; esac
done
export PATH

if [ ! -x ".venv/bin/python" ]; then
  echo "JARVIS isn't set up yet. Open Terminal in the jarvis folder and run ./setup.sh"
  read -r -p "Press Return to close this window."
  exit 1
fi

printf '\033]0;JARVIS\007'   # window title
.venv/bin/python -m jarvis
status=$?
if [ "$status" -ne 0 ]; then
  echo
  read -r -p "JARVIS stopped with a problem (see above). Press Return to close this window."
fi
exit "$status"
