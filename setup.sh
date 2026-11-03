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
# node provides npx (runs the Mac-control and browser tool servers); uv provides uvx (runs the web-search tool server).
for pkg in ffmpeg portaudio uv node; do
  if brew list --formula "$pkg" >/dev/null 2>&1; then
    say_ok "$pkg is already installed"
  else
    echo "    Installing $pkg..."
    brew install "$pkg" || fail "Homebrew could not install $pkg. Check your internet connection and try again."
    say_ok "$pkg installed"
  fi
done

node_major="$(node --version 2>/dev/null | sed 's/^v//; s/\..*//')"
if [ -z "$node_major" ] || [ "$node_major" -lt 24 ]; then
  echo "    Your Node is older than version 24; updating it..."
  brew upgrade node || brew install node || fail "Could not update Node to version 24 or newer."
fi
say_ok "Node $(node --version)"

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

# The Kokoro voice needs spaCy's small English language file to work out pronunciation.
SPACY_EN="en-core-web-sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
if "$VENV/bin/python" -c "import en_core_web_sm" >/dev/null 2>&1; then
  say_ok "English pronunciation data already installed"
else
  uv pip install --python "$VENV/bin/python" "$SPACY_EN" \
    || fail "Downloading the English pronunciation data failed. Check your internet connection and run ./setup.sh again."
  say_ok "English pronunciation data installed"
fi

say_step "Installing the web browser JARVIS uses for browsing (Playwright's open-source Firefox)"
npx -y @playwright/mcp@0.0.84 install-browser firefox \
  || say_warn "The browser download failed. Web browsing won't work until you run ./setup.sh again."

# --------------------------------------------------------------------------
say_step "Downloading voice and speech models (first time: a few gigabytes)"
"$VENV/bin/python" -m jarvis --prepare \
  || fail "Downloading the models failed. Check your internet connection and run ./setup.sh again.
Details are in logs/jarvis.log."

# --------------------------------------------------------------------------
say_step "Letting Claude Code trust the JARVIS folder (one time only)"
if "$VENV/bin/python" -c "import sys; from jarvis.doctor import check_trust; sys.exit(0 if check_trust().ok else 1)"; then
  say_ok "Already trusted"
else
  cat <<'MSG'
    Claude Code is about to open in this window. When it asks whether you trust
    this folder, choose "Yes, I trust this folder". If it asks about MCP servers,
    choose to use them. Then type  /exit  and press Return to come back here.
MSG
  read -r -p "    Press Return to open Claude Code..." _
  env -u ANTHROPIC_API_KEY claude || true
  if "$VENV/bin/python" -c "import sys; from jarvis.doctor import check_trust; sys.exit(0 if check_trust().ok else 1)"; then
    say_ok "Folder trusted"
  else
    say_warn "The folder still isn't trusted. JARVIS will work, but will ask permission more often."
    say_warn "To fix it later: run  claude  inside the jarvis folder and accept the trust question."
  fi
fi

# Keep your personal memory out of git, so it can never be uploaded by accident.
git update-index --skip-worktree memory/facts.md >/dev/null 2>&1 || true

# --------------------------------------------------------------------------
if [ -x "$REPO_DIR/doctor.sh" ]; then
  say_step "Running the health check"
  "$REPO_DIR/doctor.sh" || say_warn "The health check found something to fix (see above)."
fi

# --------------------------------------------------------------------------
say_step "Building the JARVIS app"
APP_BUILT=0
if "$REPO_DIR/app/build-app.sh"; then
  APP_BUILT=1
else
  say_warn "The app couldn't be built (see above). You can still start JARVIS from Terminal."
fi

# --------------------------------------------------------------------------
say_step "Start automatically when you log in?"
if [ -t 0 ]; then
  read -r -p "    Open JARVIS automatically every time you log in? (y/N) " answer
  case "$answer" in
    [Yy]*) "$REPO_DIR/launchd/install-autostart.sh" || say_warn "Autostart could not be turned on." ;;
    *) say_ok "Not now. You can turn it on later with ./launchd/install-autostart.sh" ;;
  esac
fi

if [ "$APP_BUILT" = 1 ]; then
  cat <<'DONE'

==========================================================================
 Setup finished.

 Open JARVIS like any other app: press Command-Space, type JARVIS, and press
 Return. It's also in your Applications folder and Launchpad. To keep it in
 the Dock, right-click its Dock icon > Options > Keep in Dock.

 The first time, macOS asks to let JARVIS use the microphone: click Allow.
 Then say "hey Jarvis", or press Space in the JARVIS window. Quit with Command-Q.
==========================================================================
DONE
else
  cat <<'DONE'

==========================================================================
 Setup finished (without the app).

 To start JARVIS, type this in Terminal (inside the jarvis folder):

     .venv/bin/python -m jarvis

 The first time, macOS will ask to let Terminal use the microphone: click Allow.
 Then say "hey Jarvis" and ask a question. Press Control-C to stop.
==========================================================================
DONE
fi
