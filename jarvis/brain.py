"""Talks to the official `claude` command-line program.

One long-lived process is started with
    claude -p --input-format stream-json --output-format stream-json
           --verbose --include-partial-messages --model <model>
Each request is written to its stdin as one JSON line; answers stream back as
JSON lines on stdout. If the process dies (or a turn has to be abandoned) it is
restarted with --resume <session_id> so the conversation carries on.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import REPO_ROOT, Config
from .logs import TurnLogger

log = logging.getLogger(__name__)


# ------------------------------------------------------------------------ events
@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCall:
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    id: str = ""


@dataclass
class ToolResult:
    tool_use_id: str
    is_error: bool = False
    content: str = ""


@dataclass
class TurnDone:
    text: str = ""
    is_error: bool = False
    session_id: str | None = None


@dataclass
class BrainError:
    message: str


@dataclass
class _Exited:
    code: int | None


Event = TextDelta | ToolCall | ToolResult | TurnDone | BrainError


# ---------------------------------------------------------------------- helpers
def clean_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Copy of the environment with every ANTHROPIC_* variable removed.

    This makes sure `claude` uses your Pro/Max login, never an API key."""
    env = dict(os.environ if env is None else env)
    return {k: v for k, v in env.items() if not k.upper().startswith("ANTHROPIC_")}


def build_command(cfg: Config, resume: str | None = None) -> list[str]:
    cmd = [
        cfg.claude_command, "-p",
        "--input-format", "stream-json",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", cfg.model,
        # Phase 1: no MCP tools are loaded, and anything that would need your
        # permission is refused automatically (spoken confirmation comes in Phase 2).
        "--strict-mcp-config",
        "--permission-prompts", "none",
    ]
    if resume:
        cmd += ["--resume", resume]
    assert "--dangerously-skip-permissions" not in cmd
    return cmd


def user_message(text: str) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n"


def _tool_result_text(content: Any, limit: int = 500) -> str:
    if isinstance(content, list):
        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content or "")[:limit]


def parse_line(line: str) -> list[Event | dict]:
    """Turn one stdout line into zero or more events. Unknown lines are ignored."""
    line = line.strip()
    if not line:
        return []
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        log.debug("non-JSON line from claude: %s", line[:200])
        return []
    t = msg.get("type")
    if t == "stream_event":
        ev = msg.get("event", {})
        if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            return [TextDelta(ev["delta"].get("text", ""))]
        return []
    if t == "assistant":
        return [ToolCall(c.get("name", "?"), c.get("input") or {}, c.get("id", ""))
                for c in msg.get("message", {}).get("content", []) if c.get("type") == "tool_use"]
    if t == "user":
        return [ToolResult(c.get("tool_use_id", ""), bool(c.get("is_error")), _tool_result_text(c.get("content")))
                for c in msg.get("message", {}).get("content", []) or []
                if isinstance(c, dict) and c.get("type") == "tool_result"]
    if t == "result":
        return [TurnDone(str(msg.get("result") or ""), bool(msg.get("is_error")), msg.get("session_id"))]
    if t == "system" and msg.get("subtype") == "init":
        return [{"session_id": msg.get("session_id"), "model": msg.get("model")}]
    return []


# ------------------------------------------------------------------------ brain
class ClaudeBrain:
    def __init__(self, cfg: Config, logger: TurnLogger | None = None, cwd: Path = REPO_ROOT,
                 command_builder: Callable[[Config, str | None], list[str]] = build_command,
                 env: dict[str, str] | None = None, drain_grace_s: float = 3.0):
        self.cfg = cfg
        self.logger = logger
        self.cwd = cwd
        self.command_builder = command_builder
        self.env = clean_env(env)
        self.drain_grace_s = drain_grace_s
        self.session_id: str | None = None
        self.proc: subprocess.Popen | None = None
        self._events: queue.Queue[Event | _Exited] = queue.Queue()
        self._in_turn = False          # a turn was sent and its result not yet read
        self._lock = threading.Lock()

    # -- process lifecycle
    def start(self) -> None:
        cmd = self.command_builder(self.cfg, self.session_id)
        log.info("Starting Claude: %s", " ".join(cmd))
        self._events = queue.Queue()
        self._in_turn = False
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=self.cwd, env=self.env, text=True, bufsize=1,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "I can't find the 'claude' program. Install Claude Code, then run ./setup.sh again.") from exc
        proc, events = self.proc, self._events
        threading.Thread(target=self._read_stdout, args=(proc, events), daemon=True, name="claude-out").start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True, name="claude-err").start()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, graceful: bool = True) -> None:
        """Close Claude. graceful=False kills it at once (used when it is stuck)."""
        proc, self.proc = self.proc, None
        self._in_turn = False
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            if graceful:
                proc.wait(timeout=3)
        except Exception:
            pass
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    def restart(self) -> None:
        self.stop(graceful=False)
        self.start()

    def _read_stdout(self, proc: subprocess.Popen, events: queue.Queue) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            for ev in parse_line(line):
                if isinstance(ev, dict):
                    if ev.get("session_id"):
                        self.session_id = ev["session_id"]
                    continue
                if isinstance(ev, TurnDone) and ev.session_id:
                    self.session_id = ev.session_id
                events.put(ev)
        events.put(_Exited(proc.wait()))

    @staticmethod
    def _read_stderr(proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            if line.strip():
                log.warning("claude: %s", line.rstrip())

    # -- conversation
    def _log(self, kind: str, **data: Any) -> None:
        if self.logger:
            self.logger.log(kind, **data)

    def _note(self, ev: Event) -> None:
        if isinstance(ev, ToolCall):
            self._log("tool_call", name=ev.name, input=ev.input, id=ev.id)
        elif isinstance(ev, ToolResult):
            self._log("tool_result", id=ev.tool_use_id, is_error=ev.is_error, content=ev.content)
        elif isinstance(ev, TurnDone):
            self._log("assistant", text=ev.text, is_error=ev.is_error, session_id=ev.session_id)

    def _finish_abandoned_turn(self) -> None:
        """A previous answer was interrupted. Let it finish quietly, or restart if it takes too long."""
        deadline = time.monotonic() + self.drain_grace_s
        while time.monotonic() < deadline:
            try:
                ev = self._events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if isinstance(ev, _Exited):
                break
            self._note(ev)
            if isinstance(ev, TurnDone):
                self._in_turn = False
                return
        log.info("Previous answer still running; restarting Claude and resuming the conversation.")
        self.restart()

    def ask(self, text: str) -> Iterator[Event]:
        """Send one request and yield events until the answer is finished."""
        with self._lock:
            if self._in_turn and self.alive():
                self._finish_abandoned_turn()
            if not self.alive():
                self.start()
            try:
                assert self.proc is not None and self.proc.stdin is not None
                self.proc.stdin.write(user_message(text))
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                self.restart()
                assert self.proc is not None and self.proc.stdin is not None
                self.proc.stdin.write(user_message(text))
                self.proc.stdin.flush()
            self._in_turn = True
        self._log("user", text=text)

        deadline = time.monotonic() + self.cfg.claude_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._log("error", message="timeout")
                self.stop(graceful=False)   # the next request restarts it with --resume
                yield BrainError("Claude took too long to answer.")
                return
            try:
                ev = self._events.get(timeout=min(remaining, 0.5))
            except queue.Empty:
                continue
            if isinstance(ev, _Exited):
                self._in_turn = False
                self._log("error", message=f"claude exited with code {ev.code}")
                yield BrainError("Claude stopped unexpectedly.")
                return
            self._note(ev)
            if isinstance(ev, TurnDone):
                self._in_turn = False
                yield ev
                return
            yield ev
