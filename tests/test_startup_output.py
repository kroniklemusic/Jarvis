import subprocess
import sys
from pathlib import Path

from jarvis.wake import hotkey_label

ROOT = Path(__file__).resolve().parent.parent


def test_hotkey_label_uses_mac_key_names():
    assert hotkey_label("<ctrl>+<alt>+j") == "Control + Option + J"
    assert hotkey_label("<cmd>+<shift>+<space>") == "Command + Shift + Space"
    assert hotkey_label("<f5>") == "F5"


def test_loading_noise_goes_to_the_log_not_the_screen(tmp_path):
    log = tmp_path / "logs" / "jarvis.log"
    code = f"""
import os, sys, warnings
sys.path.insert(0, {str(ROOT)!r})
from jarvis.logs import stderr_to_file
from pathlib import Path
print("visible message")
with stderr_to_file(Path({str(log)!r})):
    print("python warning text", file=sys.stderr)
    os.write(2, b"C library warning text\\n")
    print("still visible")
print("after loading", file=sys.stderr)
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "visible message" in r.stdout and "still visible" in r.stdout
    assert "warning text" not in r.stderr
    assert "after loading" in r.stderr          # the screen is restored afterwards
    text = log.read_text()
    assert "python warning text" in text and "C library warning text" in text


def test_importing_jarvis_silences_library_warnings():
    code = f"""
import sys, warnings
sys.path.insert(0, {str(ROOT)!r})
import jarvis
warnings.warn("old API", FutureWarning)
print("ok")
"""
    r = subprocess.run([sys.executable, "-W", "default", "-c", code], capture_output=True, text=True)
    assert r.stdout.strip() == "ok" and "old API" not in r.stderr
