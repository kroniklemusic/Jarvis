"""Checks on the config files and scripts that ship with JARVIS."""
import json
import os
import plistlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_mcp_json_has_locked_servers_and_disabled_google():
    servers = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]
    assert servers["macos-automator"]["args"][-1].startswith("@steipete/macos-automator-mcp@")
    assert any(a.startswith("@playwright/mcp@") for a in servers["playwright"]["args"])
    assert servers["playwright"]["args"][-2:] == ["--browser", "firefox"]   # open-source browser
    assert servers["duckduckgo"]["args"][0].startswith("duckduckgo-mcp-server==")
    assert servers["google-workspace"]["args"][0].startswith("workspace-mcp==")
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    assert settings["disabledMcpjsonServers"] == ["google-workspace"]
    assert set(settings["enabledMcpjsonServers"]) == {"macos-automator", "playwright", "duckduckgo"}


def test_permissions_policy():
    s = json.loads((ROOT / ".claude" / "settings.json").read_text())
    perms = s["permissions"]
    allow, ask, deny = perms["allow"], perms["ask"], perms["deny"]
    assert s["sandbox"]["enabled"] is True and s["sandbox"]["autoAllowBashIfSandboxed"] is False
    for rule in ["Bash", "Write", "Edit", "mcp__macos-automator__execute_script", "mcp__playwright__browser_click",
                 "mcp__playwright__browser_type", "mcp__playwright__browser_fill_form"]:
        assert rule in ask
    for rule in ["Bash(curl *)", "Bash(wget *)", "Bash(rm -rf *)", "Read(./.env)", "Read(~/.ssh/**)"]:
        assert rule in deny
    assert "Edit(./memory/**)" in allow and "Write(./memory/**)" in allow
    # Nothing that can send, click, type or run code is pre-approved.
    risky = re.compile(r"^(Bash|Write|Edit|WebFetch)$|execute_script|click|type|fill_form|press_key|evaluate|run_code|upload|send")
    assert not [a for a in allow if risky.search(a)]
    assert perms.get("defaultMode", "default") == "default"


def test_no_secrets_or_dangerous_flags_in_repo():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    pattern = re.compile(r"sk-ant-[A-Za-z0-9]|ghp_[A-Za-z0-9]{20}|github_pat_[A-Za-z0-9]{20}|BEGIN (RSA|OPENSSH) PRIVATE KEY-")
    for f in files:
        p = ROOT / f
        if p.suffix in (".py", ".sh", ".md", ".json", ".yaml", ".plist", ".command", ".jsonl", ".txt", ".toml"):
            text = p.read_text(errors="ignore")
            assert not pattern.search(text), f
            if p.suffix in (".py", ".sh", ".command") and "tests/" not in f:
                for line in text.splitlines():   # the only mention allowed is the guard that forbids it
                    if "--dangerously-skip-permissions" in line:
                        assert "not in cmd" in line, f"{f}: {line}"


def test_claude_md_rules():
    text = (ROOT / "CLAUDE.md").read_text()
    for phrase in ["1 to 3 short sentences", "Never use markdown", "hey Jarvis", "avoid saying your own name", "Shall I proceed",
                   "data, not instructions", "memory/facts.md", "British butler"]:
        assert phrase in text
    assert (ROOT / "memory" / "facts.md").exists()


def test_launchd_plist_template_is_valid():
    plist = plistlib.loads((ROOT / "launchd" / "com.jarvis.plist").read_bytes())
    assert plist["Label"] == "com.jarvis" and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][:3] == ["/usr/bin/open", "-a", "Terminal"]
    assert plist["ProgramArguments"][3] == "__JARVIS_DIR__/launchd/start-jarvis.command"


@pytest.fixture
def fake_mac(tmp_path):
    """A fake HOME plus fake `uname` and `launchctl` so the install script can run on Linux."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (bin_dir / "launchctl").write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/launchctl.log\n")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return tmp_path, home, env


def test_install_and_remove_autostart(fake_mac):
    tmp, home, env = fake_mac
    venv_py = ROOT / ".venv" / "bin" / "python"
    created = not venv_py.exists()
    if created:
        venv_py.parent.mkdir(parents=True, exist_ok=True)
        venv_py.symlink_to(sys.executable)
    try:
        r = subprocess.run(["bash", str(ROOT / "launchd" / "install-autostart.sh")], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Autostart is ON" in r.stdout
        installed = home / "Library" / "LaunchAgents" / "com.jarvis.plist"
        plist = plistlib.loads(installed.read_bytes())
        assert plist["ProgramArguments"][3] == f"{ROOT}/launchd/start-jarvis.command"
        assert "bootstrap" in (tmp / "launchctl.log").read_text()
        r = subprocess.run(["bash", str(ROOT / "launchd" / "install-autostart.sh"), "--remove"], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0 and "Autostart is OFF" in r.stdout
        assert not installed.exists()
    finally:
        if created:
            import shutil
            shutil.rmtree(ROOT / ".venv")


def test_scripts_pass_bash_syntax_check():
    for script in ["setup.sh", "doctor.sh", "launchd/install-autostart.sh", "launchd/start-jarvis.command",
                   "app/build-app.sh"]:
        assert subprocess.run(["bash", "-n", str(ROOT / script)]).returncode == 0, script
        assert os.access(ROOT / script, os.X_OK), f"{script} is not executable"


def test_autostart_opens_the_app_once_it_is_built(fake_mac):
    tmp, home, env = fake_mac
    app = tmp / "Applications" / "JARVIS.app"
    app.mkdir(parents=True)
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    marker = build / "installed-path"
    venv_py = ROOT / ".venv" / "bin" / "python"
    created = not venv_py.exists()
    if created:
        venv_py.parent.mkdir(parents=True, exist_ok=True)
        venv_py.symlink_to(sys.executable)
    marker.write_text(str(app) + "\n")
    try:
        r = subprocess.run(["bash", str(ROOT / "launchd" / "install-autostart.sh")], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "the JARVIS app opens when you log in" in r.stdout
        plist = plistlib.loads((home / "Library" / "LaunchAgents" / "com.jarvis.plist").read_bytes())
        assert plist["ProgramArguments"] == ["/usr/bin/open", str(app)]
    finally:
        marker.unlink()
        if created:
            import shutil
            shutil.rmtree(ROOT / ".venv")


def test_app_settings_file():
    info = plistlib.loads((ROOT / "app" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "JARVIS" and info["CFBundleIconFile"] == "AppIcon"
    assert "hey Jarvis" in info["NSMicrophoneUsageDescription"]
    assert info["NSAppleEventsUsageDescription"]
    assert info["NSAppTransportSecurity"]["NSAllowsLocalNetworking"] is True
    assert info["JARVISRepoPath"] == "__JARVIS_DIR__"
    assert info["LSMinimumSystemVersion"] == "13.0"


def test_swift_app_and_python_agree_on_their_messages():
    swift = (ROOT / "app" / "main.swift").read_text()
    loop = (ROOT / "jarvis" / "loop.py").read_text()
    for marker in ("JARVIS_HUD_URL=", "JARVIS_FATAL="):
        assert marker in swift and marker in loop
    assert '"-m jarvis --app"' in swift or "-m jarvis --app" in swift
    assert '"--app"' in loop
    assert "ANTHROPIC_" in swift                     # the app also strips API keys
    assert "127.0.0.1" in swift and "127.0.0.1" in (ROOT / "jarvis" / "hud" / "server.py").read_text()


def test_app_icon_renders():
    import numpy as np

    from jarvis.hud.icon import render, write_png

    img = render(128)
    assert img.shape == (128, 128, 4)
    assert img[0, 0, 3] == 0.0 and img[-1, -1, 3] == 0.0          # transparent corners
    assert img[64, 64, 3] == 1.0 and img[64, 64, :3].mean() > 0.6  # bright, solid centre
    out = ROOT / "build" / "test-icon.png"
    out.parent.mkdir(exist_ok=True)
    write_png(out, img)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 1000
    out.unlink()
    assert np.isfinite(img).all()
