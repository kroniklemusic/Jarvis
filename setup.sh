#!/usr/bin/env bash
# JARVIS one-time setup. Run it from the jarvis folder:   ./setup.sh
# It is safe to run again at any time; it skips anything already done.

set -euo pipefail

cd "$(dirname "$0")"
REPO_DIR="$(pwd)"
VENV="$REPO_DIR/.venv"
PY_VERSION="3.12"

say_step() { printf '\n==> %s\n' "$1"; }
say_ok()   { printf '    OK: %s\n' "$1"; }
say_warn() { printf '    NOTE: %s\n' "$1"; }
fail()     { printf '\nSETUP STOPPED: %s\n' "$1" >&2; exit 1; }

# --------------------------------------------------------------------------
say_step "Checking this is a Mac"
[ "$(uname -s)" = "Darwin" ] || fail "JARVIS only runs on macOS."
if [ "$(uname -m)" = "arm64" ]; then
  say_ok "Apple Silicon Mac"
else
  say_ok "Intel Mac"
fi

# --------------------------------------------------------------------------
say_step "Checking Homebrew (the Mac app installer for developer tools)"
if ! command -v brew >/dev/null 2>&1; then
  for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [ -x "$candidate" ]; then eval "$("$candidate" shellenv)"; fi
  done
fi
command -v brew >/dev/null 2>&1 || fail "Homebrew is not installed.
Open https://brew.sh, copy the one-line install command into Terminal, follow its
instructions, then run ./setup.sh again."
say_ok "Homebrew found"

say_step "Installing helper tools: ffmpeg, portaudio, uv, node (this can take a few minutes)"
for pkg in ffmpeg portaudio uv node; do
  if brew list --formula "$pkg" >/dev/null 2>&1; then
    say_ok "$pkg is already installed"
  else
    echo "    Installing $pkg..."
    brew install "$pkg" || fail "Homebrew could not install $pkg. Check your internet connection and try again."
    say_ok "$pkg installed"
  fi
done

# --------------------------------------------------------------------------
say_step "Checking Claude Code"
if ! command -v claude >/dev/null 2>&1; then
  fail "Claude Code (the 'claude' command) is not installed.
Install it by pasting this into Terminal:
    curl -fsSL https://claude.ai/install.sh | bash
Then open a NEW Terminal window, type 'claude', sign in with your Claude Pro or Max
account, type /exit, and run ./setup.sh again."
fi
say_ok "Claude Code $(claude --version 2>/dev/null | head -1)"

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  say_warn "An ANTHROPIC_API_KEY is set in your Terminal. JARVIS ignores it and uses your"
  say_warn "Claude Pro/Max sign-in instead, so you won't be billed per request."
fi

if env -u ANTHROPIC_API_KEY claude auth status --json 2>/dev/null | grep -q '"loggedIn": *true'; then
  say_ok "You are signed in to Claude"
else
  fail "Claude Code is installed but not signed in.
Type 'claude' in Terminal, sign in with your Claude Pro or Max account, type /exit,
then run ./setup.sh again."
fi

# --------------------------------------------------------------------------
say_step "Creating JARVIS's private Python environment (Python $PY_VERSION)"
if [ ! -x "$VENV/bin/python" ]; then
  uv venv --python "$PY_VERSION" "$VENV" || fail "Could not create the Python environment."
fi
say_ok "Python environment ready in .venv"

say_step "Installing JARVIS's Python packages (first time: several minutes)"
uv pip install --python "$VENV/bin/python" -r requirements.txt \
  || fail "Installing Python packages failed. Check your internet connection and run ./setup.sh again."
say_ok "Python packages installed"

# --------------------------------------------------------------------------
say_step "Downloading voice and speech models (first time: a few gigabytes)"
"$VENV/bin/python" -m jarvis --prepare \
  || fail "Downloading the models failed. Check your internet connection and run ./setup.sh again.
Details are in logs/jarvis.log."

# --------------------------------------------------------------------------
if [ -x "$REPO_DIR/doctor.sh" ]; then
  say_step "Running the health check"
  "$REPO_DIR/doctor.sh" || say_warn "The health check found something to fix (see above)."
fi

cat <<'EOF'

==========================================================================
 Setup finished.

 To start JARVIS, type this in Terminal (inside the jarvis folder):

     .venv/bin/python -m jarvis

 The first time, macOS will ask to let Terminal use the microphone: click Allow.
 Then say "hey Jarvis" and ask a question. Press Control-C to stop.
==========================================================================
EOF
