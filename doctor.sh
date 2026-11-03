#!/usr/bin/env bash
# JARVIS health check. Run from the jarvis folder:   ./doctor.sh
# Add --no-audio to skip the microphone and voice tests, or --voice for a voice speed test.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "JARVIS isn't set up yet. Run ./setup.sh first."
  exit 1
fi

# Make Homebrew tools (npx, uvx) findable even from a bare shell.
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin"; do
  case ":$PATH:" in *":$dir:"*) ;; *) [ -d "$dir" ] && PATH="$dir:$PATH" ;; esac
done
export PATH

exec .venv/bin/python -m jarvis.doctor "$@"
