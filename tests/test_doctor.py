import json
import subprocess
import sys
from pathlib import Path

from jarvis import doctor

FAKE_MCP = str(Path(__file__).parent / "fake_mcp_server.py")


def test_api_key_check():
    assert doctor.check_api_key({"PATH": "/bin"}).ok
    c = doctor.check_api_key({"ANTHROPIC_API_KEY": "x"})
    assert not c.ok and c.warning and "ANTHROPIC_API_KEY" in c.detail
    c = doctor.check_api_key({"ANTHROPIC_BASE_URL": "x"})
    assert c.ok and "ignores" in c.detail


def test_trust_check(tmp_path):
    cj = tmp_path / ".claude.json"
    repo = tmp_path / "jarvis"
    assert not doctor.check_trust(cj, repo).ok                       # file missing
    cj.write_text(json.dumps({"projects": {str(repo): {"hasTrustDialogAccepted": True}}}))
    trusted = doctor.check_trust(cj, repo)
    assert trusted.ok and not trusted.warning        # no "How to fix" note when all is well
    cj.write_text(json.dumps({"projects": {str(repo): {"hasTrustDialogAccepted": False}}}))
    c = doctor.check_trust(cj, repo)
    assert not c.ok and "trust" in c.fix


def fake_run(version="2.1.316 (Claude Code)", logged_in=True):
    def run(cmd, **kw):
        assert not any(k.startswith("ANTHROPIC_") for k in kw["env"])
        out = version if cmd[1] == "--version" else json.dumps({"loggedIn": logged_in})
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


def test_claude_checks(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    checks = doctor.check_claude(fake_run(), which=lambda n: "/usr/local/bin/claude")
    assert [c.ok for c in checks] == [True, True]
    checks = doctor.check_claude(fake_run(logged_in=False), which=lambda n: "/x")
    assert not checks[1].ok and "sign in" in checks[1].fix
    checks = doctor.check_claude(fake_run(), which=lambda n: None)
    assert len(checks) == 1 and not checks[0].ok and "install.sh" in checks[0].fix


def test_microphone_check():
    assert doctor.check_microphone(lambda: 0.02).ok
    silent = doctor.check_microphone(lambda: 0.0)
    assert not silent.ok and "Privacy & Security > Microphone" in silent.fix

    def broken():
        raise OSError("no device")
    assert not doctor.check_microphone(broken).ok


def test_voice_check():
    assert doctor.check_voice(lambda: "kokoro").ok
    backup = doctor.check_voice(lambda: "say")
    assert backup.ok and backup.warning

    def broken():
        raise RuntimeError("no voice engine could load")
    assert not doctor.check_voice(broken).ok


def test_mcp_handshake_ok_crash_silent_missing():
    ok, detail = doctor.mcp_handshake([sys.executable, FAKE_MCP])
    assert ok and detail == "fake-server 9.9"
    ok, detail = doctor.mcp_handshake([sys.executable, FAKE_MCP, "crash"], timeout=5)
    assert not ok and "stopped" in detail
    ok, detail = doctor.mcp_handshake([sys.executable, FAKE_MCP, "silent"], timeout=1)
    assert not ok and "no answer" in detail
    ok, detail = doctor.mcp_handshake(["no-such-program-xyz"])
    assert not ok and "not installed" in detail


def test_mcp_check_skips_disabled_servers(tmp_path):
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {
        "good": {"command": sys.executable, "args": [FAKE_MCP]},
        "off": {"command": "no-such-program-xyz", "args": []}}}))
    (tmp_path / ".claude" / "settings.json").write_text(json.dumps({"disabledMcpjsonServers": ["off"]}))
    checks = doctor.check_mcp_servers(tmp_path, timeout=5)
    assert [(c.name, c.ok) for c in checks] == [("Tool server 'good' starts", True)]


def test_report_and_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "run_all", lambda skip_audio: [doctor.Check("A", True), doctor.Check("B", False, "bad", "do X")])
    assert doctor.main(["--no-audio"]) == 1
    monkeypatch.setattr(doctor, "run_all", lambda skip_audio: [doctor.Check("A", True)])
    assert doctor.main(["--no-audio"]) == 0
    out = capsys.readouterr().out
    assert "Everything looks good" in out


def test_voice_speed_test_prints_plain_results(monkeypatch):
    import numpy as np

    import jarvis.stt
    import jarvis.tts

    class Fast:
        name = "kokoro"

        def __init__(self, *a):
            pass

        def synthesize(self, text):
            yield np.full(2400, 0.1, dtype=np.float32), 24000

        def stop(self):
            pass

    class Broken:
        def __init__(self, *a):
            raise FileNotFoundError("Piper voice file not found")

    class QuietSpeaker:
        def __init__(self, engines, on_timing=None):
            self.on_timing = on_timing

        def say(self, text):
            self.on_timing({"stage": "started_speaking", "seconds": 0.12, "text": text})

        def wait(self, t):
            return True

        def close(self):
            pass

    monkeypatch.setattr(jarvis.stt, "detect_chip", lambda: "intel")
    monkeypatch.setattr(jarvis.tts, "KokoroTTS", Fast)
    monkeypatch.setattr(jarvis.tts, "PiperTTS", Broken)
    monkeypatch.setattr(jarvis.tts, "Speaker", QuietSpeaker)
    lines = []
    assert doctor.voice_speed_test(lines.append) == 0
    text = "\n".join(lines)
    assert "Kokoro: loaded in" in text and "fast enough" in text
    assert "Piper: could not run" in text
    assert "started playing 0.12 s" in text
    assert "Best choice on this Mac: Kokoro" in text
