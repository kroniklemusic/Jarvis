#!/usr/bin/env bash
# Builds JARVIS.app and puts it in your Applications folder.
# setup.sh runs this for you. To rebuild by hand:   ./app/build-app.sh
#
# Only ONE copy of the app ever exists: it is assembled in a temporary folder, moved into
# Applications, and any older copy (inside the jarvis folder, or in the other Applications
# folder) is removed, so Spotlight and Launchpad show a single "JARVIS".
set -euo pipefail

JARVIS_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$JARVIS_DIR/build"
BUNDLE_ID="com.jarvis.assistant"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

say_ok() { printf '    OK: %s\n' "$1"; }
fail()   { printf '\nCOULD NOT BUILD THE APP: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "JARVIS.app can only be built on a Mac."
[ -x "$JARVIS_DIR/.venv/bin/python" ] || fail "JARVIS isn't set up yet. Run ./setup.sh first."
case "$JARVIS_DIR" in
  *"&"* | *"<"* | *">"* | *"|"*)
    fail "The jarvis folder path contains a character the app can't handle (&, <, > or |). Move the folder (for example to your home folder) and try again." ;;
esac
if ! command -v swiftc >/dev/null 2>&1; then
  fail "Apple's command line tools are missing. Run:  xcode-select --install   then run ./app/build-app.sh again."
fi

# Is this folder an app we made (and not some other app called JARVIS)?
is_our_app() {
  [ -f "$1/Contents/Info.plist" ] &&
    [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$1/Contents/Info.plist" 2>/dev/null)" = "$BUNDLE_ID" ]
}

# Remove an app copy and tell macOS to forget it, so it disappears from Spotlight and Launchpad.
forget_app() {
  if [ -d "$1" ]; then
    [ -x "$LSREGISTER" ] && "$LSREGISTER" -u "$1" >/dev/null 2>&1 || true
    rm -rf "$1"
  fi
}

mkdir -p "$BUILD"
touch "$BUILD/.metadata_never_index"          # keep build files out of Spotlight
forget_app "$BUILD/JARVIS.app"                # older versions of this script left a copy here

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/jarvis-build.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
APP="$STAGE/JARVIS.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "    Compiling the app (about a minute)..."
if ! swiftc -O -target "$(uname -m)-apple-macos13.0" \
      -o "$APP/Contents/MacOS/JARVIS" "$JARVIS_DIR/app/main.swift" 2>"$BUILD/swiftc.log"; then
  cat "$BUILD/swiftc.log" >&2
  fail "The app didn't compile (details above and in build/swiftc.log). You can still start JARVIS from Terminal with:  .venv/bin/python -m jarvis"
fi
say_ok "App compiled"

sed "s|__JARVIS_DIR__|$JARVIS_DIR|g" "$JARVIS_DIR/app/Info.plist" > "$APP/Contents/Info.plist"
plutil -lint "$APP/Contents/Info.plist" >/dev/null || fail "The app's settings file is invalid."

ICONSET="$STAGE/AppIcon.iconset"
mkdir -p "$ICONSET"
"$JARVIS_DIR/.venv/bin/python" -m jarvis.hud.icon "$STAGE/icon-1024.png" 1024
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$STAGE/icon-1024.png" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" "$STAGE/icon-1024.png" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
say_ok "Icon created"

# A local ("ad hoc") signature lets macOS remember the microphone permission you give JARVIS.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || echo "    NOTE: couldn't sign the app; macOS may ask for permissions again after each rebuild."

if [ -w /Applications ]; then
  DEST="/Applications"; OTHER="$HOME/Applications"
else
  DEST="$HOME/Applications"; OTHER="/Applications"
fi
mkdir -p "$DEST"
if is_our_app "$OTHER/JARVIS.app" && [ -w "$OTHER" ]; then
  forget_app "$OTHER/JARVIS.app"              # don't leave a second JARVIS in the other folder
fi
forget_app "$DEST/JARVIS.app"
ditto "$APP" "$DEST/JARVIS.app"
[ -x "$LSREGISTER" ] && "$LSREGISTER" -f "$DEST/JARVIS.app" >/dev/null 2>&1 || true
say_ok "JARVIS.app installed in $DEST"
echo "$DEST/JARVIS.app" > "$BUILD/installed-path"
