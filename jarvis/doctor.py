"""Health check for JARVIS. Run with ./doctor.sh (or: .venv/bin/python -m jarvis.doctor)."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import REPO_ROOT, ConfigError, load_config


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""
    warning: bool = False   # a problem worth knowing about, but JARVIS still works


# ---------------------------------------------------------------- individual checks
def check_config() -> Check:
    try:
        load_config()
        return Check("Settings file (config.yaml)", True)
    except ConfigError as exc:
        return Check("Settings file (config.yaml)", False, str(exc), "Fix the line mentioned above in config.yaml.")


def check_claude(run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                 which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    if which("claude") is None:
        return [Check("Claude Code installed", False, "The 'claude' command was not found.",
                      "Install it with:  curl -fsSL https://claude.ai/install.sh | bash   then open a new Terminal window.")]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("ANTHROPIC_")}
    try:
        version = run(["claude", "--version"], capture_output=True, text=True, timeout=30, env=env).stdout.strip()
    except Exception:
        version = "unknown version"
    checks = [Check("Claude Code installed", True, version)]
    try:
        out = run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=30, env=env).stdout
        logged_in = bool(json.loads(out or "{}").get("loggedIn"))
    except Exception:
        logged_in = False
    checks.append(Check("Signed in to Claude", logged_in, "" if logged_in else "Claude Code is not signed in.",
                        "Type 'claude' in Terminal, sign in with your Pro/Max account, then type /exit."))
    return checks


def check_api_key(env: dict[str, str] | None = None) -> Check:
    env = os.environ if env is None else env
    found = sorted(k for k in env if k.upper().startswith("ANTHROPIC_"))
    keys = [k for k in found if k.upper() in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")]
    if keys:
        return Check("No ANTHROPIC_API_KEY set", False, f"Found: {', '.join(keys)}. JARVIS removes it before starting "
                     "Claude, so JARVIS still uses your subscription, but other programs may bill your API account.",
                     "Remove the 'export ANTHROPIC_...' line from ~/.zshrc (open it with: open -e ~/.zshrc), "
                     "then open a new Terminal window.", warning=True)
    if found:
        return Check("No ANTHROPIC_API_KEY set", True, f"other Anthropic settings found ({', '.join(found)}); "
                     "JARVIS ignores them")
    return Check("No ANTHROPIC_API_KEY set", True)


def check_trust(claude_json: Path | None = None, repo: Path = REPO_ROOT) -> Check:
    claude_json = claude_json or Path.home() / ".claude.json"
    try:
        projects = json.loads(claude_json.read_text()).get("projects", {})
        trusted = bool(projects.get(str(repo), {}).get("hasTrustDialogAccepted"))
    except Exception:
        trusted = False
    return Check("JARVIS folder trusted by Claude Code", trusted,
                 "" if trusted else "Until you trust the folder, Claude asks permission even for safe, read-only actions.",
                 f"In Terminal run:  cd \"{repo}\" && claude   then choose 'Yes, I trust this folder', and type /exit.",
                 warning=not trusted)


def check_microphone(record: Callable[[], float] | None = None) -> Check:
    def _record() -> float:
        import numpy as np
        import sounddevice as sd

        audio = sd.rec(int(16000 * 1.5), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        return float(np.abs(audio).max())

    try:
        peak = (record or _record)()
    except Exception as exc:
        return Check("Microphone", False, f"The microphone could not be opened ({exc}).",
                     "Check System Settings > Sound > Input, and allow Terminal under "
                     "System Settings > Privacy & Security > Microphone.")
    if peak == 0.0:
        return Check("Microphone", False, "The microphone gave complete silence, which usually means access is blocked.",
                     "Allow Terminal under System Settings > Privacy & Security > Microphone, then run ./doctor.sh again.")
    return Check("Microphone", True, f"sound level {peak:.3f}")


def _enabled_servers(repo: Path) -> dict[str, dict]:
    servers = json.loads((repo / ".mcp.json").read_text()).get("mcpServers", {})
    try:
        settings = json.loads((repo / ".claude" / "settings.json").read_text())
    except FileNotFoundError:
        settings = {}
    disabled = set(settings.get("disabledMcpjsonServers", []))
    return {name: spec for name, spec in servers.items() if name not in disabled}


def mcp_handshake(command: list[str], env: dict[str, str] | None = None, timeout: float = 120.0,
                  cwd: Path = REPO_ROOT) -> tuple[bool, str]:
    """Start an MCP server and check it answers the 'initialize' request."""
    try:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, bufsize=1, cwd=cwd, env={**os.environ, **(env or {})})
    except FileNotFoundError:
        return False, f"'{command[0]}' is not installed"
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l) for l in proc.stdout], daemon=True).start()  # type: ignore[union-attr]
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "jarvis-doctor", "version": "1"}}}
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None:
                    return False, f"it stopped straight away (exit code {proc.returncode})"
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 1:
                if "result" in msg:
                    info = msg["result"].get("serverInfo", {})
                    return True, f"{info.get('name', 'server')} {info.get('version', '')}".strip()
                return False, str(msg.get("error", "error"))
        return False, f"no answer within {int(timeout)} seconds"
    finally:
        proc.kill()
        proc.wait()


def check_mcp_servers(repo: Path = REPO_ROOT, timeout: float = 120.0) -> list[Check]:
    fixes = {"npx": "Run ./setup.sh again (it installs Node).", "uvx": "Run ./setup.sh again (it installs uv)."}
    checks = []
    for name, spec in _enabled_servers(repo).items():
        cmd = [spec["command"], *spec.get("args", [])]
        ok, detail = mcp_handshake(cmd, spec.get("env"), timeout, repo)
        checks.append(Check(f"Tool server '{name}' starts", ok, detail,
                            fixes.get(spec["command"], "Check your internet connection, then run ./doctor.sh again.")))
    return checks


def check_voice(speak: Callable[[], str] | None = None) -> Check:
    def _speak() -> str:
        from .logs import stderr_to_file
        from .tts import Speaker, load_engines

        cfg = load_config()
        with stderr_to_file(cfg.log_path / "jarvis.log"):   # keep library warnings off the screen
            engines = load_engines(cfg.tts_engines, cfg, notify=print)
        if not engines:
            raise RuntimeError("no voice engine could load")
        speaker = Speaker(engines)
        speaker.say("Good day. This is JARVIS, testing my voice.")
        speaker.wait(30)
        name = speaker.engine.name
        speaker.close()
        return name

    try:
        name = (speak or _speak)()
    except Exception as exc:
        return Check("Voice (you should have heard a test sentence)", False, str(exc), "Run ./setup.sh again.")
    warn = name == "say"
    return Check("Voice (you should have heard a test sentence)", True,
                 f"using {name}" + (" - the Kokoro and Piper voices didn't work, so the Mac's own voice was used"
                                    if warn else ""),
                 "Run ./setup.sh again to repair the voices." if warn else "", warning=warn)


# ------------------------------------------------------------------------- report
def run_all(skip_audio: bool = False) -> list[Check]:
    checks = [check_config(), *check_claude(), check_api_key(), check_trust()]
    print_report(checks, header=True)
    if not skip_audio:
        print("Checking the microphone (stay quiet or say something for 2 seconds)...", flush=True)
        more = [check_microphone()]
        print_report(more)
        checks += more
    print("Starting each tool server (the first time can take a minute or two)...", flush=True)
    more = check_mcp_servers()
    print_report(more)
    checks += more
    if not skip_audio:
        print("Testing the voice - listen for a test sentence...", flush=True)
        more = [check_voice()]
        print_report(more)
        checks += more
    return checks


def print_report(checks: list[Check], header: bool = False) -> None:
    if header:
        print("\nJARVIS health check\n-------------------")
    for c in checks:
        mark = "OK  " if c.ok else ("WARN" if c.warning else "FAIL")
        line = f"[{mark}] {c.name}"
        if c.detail:
            line += f" - {c.detail}"
        print(line)
        if (not c.ok or c.warning) and c.fix:
            print(f"       How to fix: {c.fix}")
    sys.stdout.flush()


VOICE_TEST_SENTENCES = (
    "Good afternoon.",
    "I'm in fine form, thank you, and all systems are running smoothly.",
    "Tomorrow looks mild in London, with light rain arriving in the late afternoon.",
)


def voice_speed_test(out: Callable[[str], None] = print) -> int:
    """Time each voice on this Mac and print the results in plain English (./doctor.sh --voice)."""
    from .logs import stderr_to_file
    from .stt import detect_chip
    from .tts import KokoroTTS, PiperTTS, Speaker, performance_cores

    cfg = load_config()
    log_file = cfg.log_path / "jarvis.log"
    chip = detect_chip()
    out("\nVoice speed test (about a minute)\n--------------------------------")
    out("For each voice: how long it takes to prepare a sentence, compared with how long the sentence")
    out("lasts when spoken. Smaller is better; under 0.5 means replies can start without a noticeable wait.\n")

    candidates: list[tuple[str, Callable[[], object]]] = []
    if chip == "apple":
        with stderr_to_file(log_file):
            import torch
            cores = performance_cores()
            if cores:
                torch.set_num_threads(cores)
            has_mps = bool(torch.backends.mps.is_available())
        candidates.append(("Kokoro on the processor", lambda: KokoroTTS(cfg.kokoro_voice, cfg.speech_speed, "cpu")))
        if has_mps:
            candidates.append(("Kokoro on the graphics chip",
                               lambda: KokoroTTS(cfg.kokoro_voice, cfg.speech_speed, "mps")))
    else:
        candidates.append(("Kokoro", lambda: KokoroTTS(cfg.kokoro_voice, cfg.speech_speed, "cpu")))
    candidates.append(("Piper", lambda: PiperTTS(cfg.piper_voice, cfg.piper_dir, cfg.speech_speed)))

    best = None
    for label, make in candidates:
        try:
            t0 = time.monotonic()
            with stderr_to_file(log_file):
                engine = make()
            out(f"{label}: loaded in {time.monotonic() - t0:.1f} s")
            ratios = []
            for sentence in VOICE_TEST_SENTENCES:
                t0 = time.monotonic()
                with stderr_to_file(log_file):
                    chunks = list(engine.synthesize(sentence))
                took = time.monotonic() - t0
                length = sum(len(a) / sr for a, sr in chunks)
                ratios.append(took / length if length else float("inf"))
                out(f"   prepared {length:4.1f} s of speech in {took:4.2f} s   \"{sentence[:45]}\"")
            worst = max(ratios)
            verdict = "fast enough" if worst <= 0.5 else ("a little slow" if worst <= 1.0 else "too slow")
            out(f"   -> worst case {worst:.2f}: {verdict}\n")
            if best is None or worst < best[1]:
                best = (label, worst, engine)
        except Exception as exc:
            out(f"{label}: could not run ({exc})\n")

    if best is None:
        out("No voice could run. Please run ./setup.sh again.")
        return 1

    label, worst, engine = best
    out(f"Fastest on this Mac: {label}. Playing three test sentences with it now - listen for choppiness.")
    timings: list[dict] = []
    speaker = Speaker([engine], on_timing=timings.append)
    for sentence in VOICE_TEST_SENTENCES:
        speaker.say(sentence)
    speaker.wait(60)
    speaker.close()
    started = [t["seconds"] for t in timings if t["stage"] == "started_speaking"]
    if started:
        out(f"The first sentence started playing {started[0]:.2f} s after it was handed to the voice.")
    out("\nPlease copy everything above and send it to whoever is helping you with JARVIS.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--voice" in argv:
        return voice_speed_test()
    checks = run_all(skip_audio="--no-audio" in argv)
    failed = [c for c in checks if not c.ok and not c.warning]
    warned = [c for c in checks if c.warning and (not c.ok or c.fix)]
    print()
    if failed:
        print(f"{len(failed)} problem(s) need fixing before JARVIS will work properly. See 'How to fix' above.")
        return 1
    if warned:
        print("JARVIS should work. There are some notes above you may want to look at.")
    else:
        print("Everything looks good. Start JARVIS with:  .venv/bin/python -m jarvis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
