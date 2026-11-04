"""The main loop: wake word -> listen -> transcribe -> Claude -> speak -> follow-up."""

from __future__ import annotations

import argparse
import collections
import logging
import shutil
import sys
import threading
import time
from typing import Any, Callable

import numpy as np

from .audio import Endpointer, play_chime
from .brain import BrainError, PermissionRequest, TextDelta, ToolCall, TurnDone
from .config import Config, ConfigError, load_config
from .logs import TurnLogger, setup_logging, stderr_to_file
from .permissions import REPROMPT, parse_answer, permission_question
from .stt import clean_transcript
from .tts import SentenceSplitter

log = logging.getLogger(__name__)

IDLE, LISTENING, RESPONDING = "idle", "listening", "responding"


def tool_label(name: str) -> str:
    """'mcp__duckduckgo__search' -> 'DuckDuckGo: search'; 'Bash' -> 'Bash'."""
    if not name.startswith("mcp__"):
        return name
    _, server, tool = (name.split("__", 2) + ["", ""])[:3]
    server = {"duckduckgo": "DuckDuckGo", "playwright": "Browser", "macos-automator": "Mac automation",
              "google-workspace": "Google"}.get(server, server)
    return f"{server}: {tool.replace('browser_', '').replace('_', ' ')}"


class PendingPermission:
    """A permission question waiting for your spoken yes or no."""

    def __init__(self, request: PermissionRequest):
        self.request = request
        self.done = threading.Event()
        self.allowed = False
        self.listening = False
        self.attempts = 0

    def resolve(self, allowed: bool) -> None:
        self.allowed = allowed
        self.done.set()


# Set when JARVIS runs inside JARVIS.app: status lines and events also go to the HUD.
HUD_PUBLISH: Callable[[str, dict], None] | None = None
CONVERSATION_PREFIXES = ("You: ", "JARVIS: ", "(using ", "(timing: ")


def say_to_user(msg: str) -> None:
    """Plain-English status line in the Terminal window (and the HUD's system log)."""
    print(msg, flush=True)
    if HUD_PUBLISH and msg.strip() and not msg.startswith(CONVERSATION_PREFIXES):
        HUD_PUBLISH("notice", {"text": msg.strip()})


def boot_progress(step: int, total: int, label: str) -> None:
    say_to_user(f"  {step}/{total}  {label}")
    if HUD_PUBLISH:
        HUD_PUBLISH("boot", {"step": step, "total": total, "label": label})


class Jarvis:
    def __init__(self, cfg: Config, wake: Any, endpointer: Endpointer, transcriber: Any,
                 speaker: Any, brain: Any, logger: TurnLogger, hotkey: Any = None,
                 chime: Callable[[bool], None] = play_chime, ui: Callable[[str], None] = say_to_user,
                 clock: Callable[[], float] = time.monotonic,
                 events: Callable[[str, dict], None] | None = None,
                 commands: Callable[[], Any] | None = None,
                 save_settings: Callable[[dict], None] | None = None):
        self.cfg, self.wake, self.endpointer = cfg, wake, endpointer
        self.transcriber, self.speaker, self.brain = transcriber, speaker, brain
        self.logger, self.hotkey, self.chime, self.ui = logger, hotkey, chime, ui
        self.state = IDLE
        self.follow_up = False
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self.pending: PendingPermission | None = None
        self.clock = clock
        self.timing: dict[str, Any] = {}      # how long each step of the current turn took
        self._room: collections.deque[float] = collections.deque(maxlen=60)   # ~5 s of idle loudness
        self._max_backlog = 0
        self._listen_step_s: list[float] = []
        self._speak_lock = threading.Lock()   # makes "cancel" and "queue a sentence" atomic
        self.events = events                  # HUD: publish(kind, data)
        self.commands = commands              # HUD: returns (name, payload) or None
        self.save_settings = save_settings    # writes changed settings to config.yaml
        self._hud_state: tuple | None = None
        self._ui_talk = False
        self.muted = False                    # microphone off: no wake word, no listening
        self.voice_replies = True             # False: answers are shown but not spoken

    def _emit(self, kind: str, **data: Any) -> None:
        if self.events:
            try:
                self.events(kind, data)
            except Exception:
                log.debug("HUD event failed", exc_info=True)

    def hud_state(self) -> tuple[str, bool]:
        """What the HUD should show: standby / listening / thinking / speaking / confirm."""
        if self.state == IDLE:
            return "standby", False
        if self.pending is not None:
            return "confirm", False
        if self.state == LISTENING:
            return "listening", self.follow_up
        return ("speaking" if self.speaker.is_speaking() else "thinking"), False

    # ------------------------------------------------------------ HUD buttons
    def emit_toggles(self) -> None:
        self._emit("toggles", muted=self.muted, voice_replies=self.voice_replies)

    def handle_command(self, name: str, payload: dict | None = None) -> None:
        """A button, key, typed request or settings change from the HUD.

        Runs on the main loop, like everything else, so it never races with the microphone."""
        payload = payload or {}
        if name == "talk":
            if self.muted:
                self.set_muted(False)
            if self.state == IDLE:
                self._ui_talk = True
            elif self.state == RESPONDING:
                self.barge_in()
        elif name == "stop":
            self._stop_everything()
        elif name == "ask" and payload.get("text"):
            self.typed_request(str(payload["text"]))
        elif name in ("mute", "unmute"):
            self.set_muted(name == "mute")
        elif name in ("voice_on", "voice_off"):
            self.voice_replies = name == "voice_on"
            if not self.voice_replies:
                self.speaker.interrupt()      # stop talking now; text keeps coming
            self.logger.log(name)
            self.emit_toggles()
        elif name == "new_conversation":
            self._stop_everything(quiet=True)
            new = getattr(self.brain, "new_conversation", None)
            if callable(new):
                new()
            self.logger.log("new_conversation")
            self._emit("cleared")
            self.ui("Started a new conversation.")
        elif name == "settings" and payload.get("values"):
            self.apply_settings(payload["values"])
        elif name in ("yes", "no") and self.pending is not None and not self.pending.done.is_set():
            p, allowed = self.pending, name == "yes"
            self.logger.log("permission_reply", text=f"({name} button)", understood=allowed)
            self.pending = None
            if self.state == LISTENING:
                self.state = RESPONDING
            p.listening = False
            p.resolve(allowed)

    def _stop_everything(self, quiet: bool = False) -> None:
        if self.state == IDLE:
            return
        with self._speak_lock:
            self._cancel.set()
            self.speaker.interrupt()
        if self.pending:
            self.pending.resolve(False)
            self._emit("permission_done", allowed=False)
            self.pending = None
        self.logger.log("stopped_from_hud")
        self._go_idle()
        if not quiet:
            self.ui("Stopped. Say \"hey Jarvis\" when you need me.")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted and self.state == LISTENING and not (self.pending and self.pending.listening):
            self._go_idle()
        self.logger.log("mute" if muted else "unmute")
        self.emit_toggles()
        self.ui("Microphone off. Type below, or press Talk to turn it back on." if muted
                else "Microphone on.")

    def typed_request(self, text: str) -> None:
        """A request typed into the command bar: handled exactly like a spoken one."""
        if self.pending is not None:
            self._emit("notice", text="Please answer the authorisation question first.")
            return
        if self.state == RESPONDING:          # a new request replaces the current answer
            with self._speak_lock:
                self._cancel.set()
                self.speaker.interrupt()
        self.logger.log("typed", text=text)
        self.timing = {"heard_at": self.clock(), "transcribe_s": 0.0, "typed": True}
        self.ui(f"You: {text}")
        self._emit("heard", text=text, typed=True)
        self.start_response(text)

    def apply_settings(self, values: dict) -> None:
        """Settings changed in the HUD: apply them now and save them to config.yaml."""
        from . import settings as settings_mod

        for key, value in values.items():
            setattr(self.cfg, key, value)
        if "wake_threshold" in values and hasattr(self.wake, "threshold"):
            self.wake.threshold = values["wake_threshold"]
        for engine in getattr(self.speaker, "engines", []):
            if "kokoro_voice" in values and hasattr(engine, "set_voice"):
                engine.set_voice(values["kokoro_voice"])
            if "speech_speed" in values and hasattr(engine, "set_speed"):
                engine.set_speed(values["speech_speed"])
        if "model" in values and hasattr(self.brain, "set_model"):
            self.brain.set_model(values["model"])
        saved = True
        if self.save_settings:
            try:
                self.save_settings(values)
            except Exception as exc:
                saved = False
                log.warning("Couldn't save settings: %s", exc)
        self.logger.log("settings_changed", **values)
        self._emit("settings", values=settings_mod.current(self.cfg), options=settings_mod.options())
        engines = getattr(self.speaker, "engines", None)
        if self.events and engines:
            try:   # refresh the Systems panel (model, voice)
                self._emit("config", **config_summary(self.cfg, engines, self.transcriber))
            except Exception:
                log.debug("couldn't refresh the HUD config", exc_info=True)
        self.ui("Settings updated." if saved else "Settings updated for now, but couldn't be saved to config.yaml.")

    # ------------------------------------------------------------- transitions
    def _hotkey_pressed(self) -> bool:
        if self._ui_talk:
            self._ui_talk = False
            return True
        return bool(self.hotkey and self.hotkey.consume())

    def _go_idle(self) -> None:
        self.state, self.follow_up = IDLE, False
        self.wake.reset()

    def _go_listening(self, timeout_s: float, follow_up: bool = False) -> None:
        self.state, self.follow_up = LISTENING, follow_up
        self.endpointer.reset(timeout_s, noise_floor=self.room_level())
        if not follow_up:
            self.chime(self.cfg.chime)
        self.ui("Listening for a follow-up..." if follow_up else "Listening...")

    def barge_in(self) -> None:
        """Stop talking right now and listen for a new request."""
        with self._speak_lock:
            self._cancel.set()
            self.speaker.interrupt()
        if self.pending:
            self.pending.resolve(False)
            self.pending = None
        self.logger.log("barge_in")
        self._go_listening(self.cfg.listen_timeout_seconds)

    def room_level(self) -> float | None:
        """Background loudness of the room, measured while JARVIS was idle (None if not known yet)."""
        if len(self._room) < 10:
            return None
        return float(np.percentile(self._room, 30))

    def _speaker_quiet(self) -> bool:
        quiet_for = getattr(self.speaker, "quiet_for", None)
        if quiet_for is None:
            return not self.speaker.is_speaking()
        return quiet_for() >= self.cfg.echo_guard_seconds

    def note_backlog(self, frames: int) -> None:
        """How far behind the microphone JARVIS is (called by the main loop)."""
        self._max_backlog = max(self._max_backlog, frames)

    # -------------------------------------------------------------------- step
    def step(self, frame: np.ndarray) -> None:
        """Process one 80 ms microphone frame."""
        if self.commands:
            cmd = self.commands()
            while cmd:
                name, payload = cmd if isinstance(cmd, tuple) else (cmd, {})
                self.handle_command(name, payload)
                cmd = self.commands()
        if self.events:
            f = frame.astype(np.float32) / 32768.0 if frame.dtype == np.int16 else frame
            level = 0.0 if self.muted or not len(f) else float(np.sqrt(np.mean(f * f)))
            self._emit("level", mic=round(level, 4))
        if self.state == LISTENING:
            started = self.clock()
            self._step(frame)
            self._listen_step_s.append(self.clock() - started)
        else:
            self._step(frame)
        if self.events:
            state = self.hud_state()
            if state != self._hud_state:
                self._hud_state = state
                self._emit("state", state=state[0], follow_up=state[1])

    def _step(self, frame: np.ndarray) -> None:
        if self.state == IDLE:
            f = frame.astype(np.float32) / 32768.0 if frame.dtype == np.int16 else frame
            self._room.append(float(np.sqrt(np.mean(f * f))) if len(f) else 0.0)
            woke = False if self.muted else self.wake.process(frame)
            if woke or self._hotkey_pressed():
                self.logger.log("wake", source="wake_word" if woke else "hotkey")
                self._go_listening(self.cfg.listen_timeout_seconds)
            return

        if self.state == RESPONDING:
            woke = False if self.muted else self.wake.process(frame)
            if woke or self._hotkey_pressed():
                self.barge_in()
                return
            p = self.pending
            if p and not p.listening and not p.done.is_set() and self._speaker_quiet() and not self.muted:
                p.listening = True
                self._go_listening(self.cfg.confirm_timeout_seconds)
                self.ui("Waiting for you to say \"yes, proceed\" or \"no\"...")
                return
            worker_done = self._worker is None or not self._worker.is_alive()
            if worker_done and self._speaker_quiet():
                self._report_timing()
                if self.cfg.follow_up_seconds > 0:
                    self._go_listening(self.cfg.follow_up_seconds, follow_up=True)
                else:
                    self._go_idle()
                    self.ui("Say \"hey Jarvis\" when you need me.")
            return

        # LISTENING
        result = self.endpointer.feed(frame)
        if self.pending and self.pending.listening:
            self._permission_listen(result)
            return
        if result == "timeout":
            if not self.follow_up:
                self.ui("I didn't hear anything.")
            self._go_idle()
            self.ui("Say \"hey Jarvis\" when you need me.")
        elif result == "done":
            self._handle_utterance(self.endpointer.audio())

    def _permission_listen(self, result: str) -> None:
        p = self.pending
        assert p is not None
        if result not in ("timeout", "done"):
            return
        answer = None
        if result == "done":
            self._show_transcribing()
            heard = clean_transcript(self.transcriber.transcribe(self.endpointer.audio()))
            self.ui(f"You: {heard or '(unclear)'}")
            answer = parse_answer(heard)
            self.logger.log("permission_reply", text=heard, understood=answer)
            if heard:
                self._emit("heard", text=heard)
        self.state = RESPONDING
        p.listening = False
        if answer is None:
            p.attempts += 1
            if result == "done" and p.attempts < 2:
                if self.voice_replies:
                    self.speaker.say(REPROMPT)  # ask once more, then listen again
                self._emit("notice", text=REPROMPT)
                return
            if self.voice_replies:
                self.speaker.say("I'll take that as a no.")
            answer = False
        self.pending = None
        p.resolve(answer)

    def _show_transcribing(self) -> None:
        """Tell the HUD right away: transcription blocks this loop for a moment."""
        self._hud_state = ("transcribing", False)
        self._emit("state", state="transcribing", follow_up=False)

    def _handle_utterance(self, audio: np.ndarray) -> None:
        started = self.clock()
        self._show_transcribing()
        raw = self.transcriber.transcribe(audio)
        self.timing = {"heard_at": started, "transcribe_s": self.clock() - started,
                       **getattr(self.endpointer, "stats", {})}
        text = clean_transcript(raw)
        if text is None:
            self.logger.log("dropped_transcript", text=raw)
            self._emit("notice", text="Didn't catch that.")
            self._go_idle()
            self.ui("Say \"hey Jarvis\" when you need me.")
            return
        self.ui(f"You: {text}")
        self._emit("heard", text=text)
        self.start_response(text)

    # ---------------------------------------------------------------- respond
    def start_response(self, text: str) -> None:
        self.state = RESPONDING
        self.wake.reset()
        self._cancel = threading.Event()
        self._worker = threading.Thread(target=self._respond, args=(text, self._cancel),
                                        name="responder", daemon=True)
        self._worker.start()

    def _speak(self, sentence: str, cancel: threading.Event) -> None:
        with self._speak_lock:
            if cancel.is_set() or not sentence:
                return
            if self.voice_replies:
                self.speaker.say(sentence)
            self.timing.setdefault("first_text_at", self.clock())
        self.ui(f"JARVIS: {sentence}")
        self._emit("said", text=sentence)
        self.logger.log("spoken", text=sentence)

    def _respond(self, text: str, cancel: threading.Event) -> None:
        splitter = SentenceSplitter()
        spoke_anything = False
        try:
            for ev in self.brain.ask(text):
                if cancel.is_set():
                    return  # barge-in: the brain finishes or restarts the old turn by itself
                if isinstance(ev, TextDelta):
                    for s in splitter.feed(ev.text):
                        self._speak(s, cancel)
                        spoke_anything = True
                elif isinstance(ev, ToolCall):
                    self.ui(f"(using {ev.name})")
                    self._emit("tool", name=ev.name, label=tool_label(ev.name))
                elif isinstance(ev, PermissionRequest):
                    for s in splitter.flush():
                        self._speak(s, cancel)
                    allowed = self._ask_permission(ev, cancel)
                    self.brain.answer_permission(ev, allowed)
                    if cancel.is_set():
                        return
                elif isinstance(ev, BrainError):
                    self._speak(f"Sorry, something went wrong. {ev.message}", cancel)
                    return
                elif isinstance(ev, TurnDone):
                    for s in splitter.flush():
                        self._speak(s, cancel)
                        spoke_anything = True
                    if not spoke_anything and ev.text:  # no streamed text arrived; use the final answer
                        fallback = SentenceSplitter()
                        for s in fallback.feed(ev.text) + fallback.flush():
                            self._speak(s, cancel)
                    return
        except Exception as exc:  # never let one bad turn kill JARVIS
            log.exception("Error while answering")
            self.logger.log("error", message=str(exc))
            self._speak("Sorry, I ran into a problem answering that.", cancel)

    def _ask_permission(self, req: PermissionRequest, cancel: threading.Event) -> bool:
        """Speak the question, then wait until the main loop hears yes or no."""
        pending = PendingPermission(req)
        question = permission_question(req.tool_name, req.input, req.description)
        self._speak(question, cancel)
        self._emit("permission", question=question, tool=tool_label(req.tool_name))
        self.pending = pending
        while not pending.done.wait(0.05):
            if cancel.is_set():
                return False
        self._emit("permission_done", allowed=pending.allowed)
        if pending.allowed:
            self._speak("Very good.", cancel)
        else:
            self._speak("Understood, I won't do that.", cancel)
        return pending.allowed

    # ----------------------------------------------------------------- timing
    def on_voice_timing(self, info: dict) -> None:
        """Called by the Speaker; notes when the first sentence of this answer started playing."""
        if info.get("stage") == "started_speaking" and "first_text_at" in self.timing:
            self.timing.setdefault("voice_at", self.clock())

    def _report_timing(self) -> None:
        t, self.timing = self.timing, {}
        if "heard_at" not in t or "first_text_at" not in t:
            return
        after_transcribe = t["heard_at"] + t["transcribe_s"]
        parts = {
            "you_spoke_s": t.get("seconds"),
            "transcribe_s": t["transcribe_s"],
            "claude_s": t["first_text_at"] - after_transcribe,
            "voice_s": (t["voice_at"] - t["first_text_at"]) if "voice_at" in t else None,
        }
        behind_s = self._max_backlog * 0.08
        steps, self._listen_step_s, self._max_backlog = self._listen_step_s, [], 0
        slowest_ms = max(steps) * 1000 if steps else 0.0
        rounded = {k: round(v, 2) for k, v in parts.items() if v is not None}
        self.logger.log("turn_timing", **rounded, ended_by=t.get("ended_by"),
                        voice_level=t.get("voice_level"), background_level=t.get("background_level"),
                        room_level=t.get("room_level"), behind_s=round(behind_s, 2),
                        slowest_listen_step_ms=round(slowest_ms, 1))
        self._emit("timing", **rounded, ended_by=t.get("ended_by"), behind_s=round(behind_s, 2))
        if not self.cfg.show_timings:
            return
        line = "(timing: "
        if t.get("seconds") is not None:
            why = "after a pause" if t.get("ended_by") == "silence" else "at the time limit"
            line += f"recorded {t['seconds']:.1f}s, stopped {why}; "
        line += f"understood your words in {parts['transcribe_s']:.1f}s"
        line += f", Claude's first words {parts['claude_s']:.1f}s"
        if parts["voice_s"] is not None:
            line += f", voice started {parts['voice_s']:.1f}s after the text"
        if t.get("ended_by") == "length limit":
            line += " - background noise kept me listening"
        if behind_s >= 0.5:
            line += f" - note: I was running {behind_s:.1f}s behind the microphone"
        self.ui(line + ")")

    def wait_for_response(self, timeout: float = 10.0) -> None:
        if self._worker:
            self._worker.join(timeout)


# ----------------------------------------------------------------------- start
def fatal(msg: str) -> int:
    """A problem JARVIS can't recover from. JARVIS.app shows the message in a dialog."""
    say_to_user(msg)
    print(f"JARVIS_FATAL={msg}", flush=True)
    if HUD_PUBLISH:
        HUD_PUBLISH("fatal", {"text": msg})
    return 1


def build_and_run(cfg: Config, prepare_only: bool = False, hud: Any = None) -> int:  # pragma: no cover - Mac
    from .stt import detect_chip
    from .tts import Speaker

    if shutil.which(cfg.claude_command) is None:
        return fatal("I can't find Claude Code (the 'claude' program). Install it, sign in, then run ./setup.sh again.")

    chip = detect_chip()
    say_to_user(f"Starting JARVIS on an {'Apple Silicon' if chip == 'apple' else 'Intel'} Mac.")
    if prepare_only:
        say_to_user("Loading models. The very first time this downloads a few gigabytes, so please be patient...")
        return _load_everything(cfg, chip, prepare_only=True)[0]
    say_to_user("Loading models (about 20 seconds)...")
    # Keep the window tidy: technical warnings printed while loading go to logs/jarvis.log.
    with stderr_to_file(cfg.log_path / "jarvis.log"):
        status, wake, vad, transcriber, player, engines = _load_everything(cfg, chip, prepare_only=False)
    if status:
        return status

    setup_logging(cfg.log_path)          # make sure no library has redirected JARVIS's own log
    timing_log = TurnLogger(cfg.log_path)
    speaker = Speaker(engines, player, on_timing=lambda d: timing_log.log("voice_timing", **d))
    if hud is not None:
        backend = getattr(player, "_backend", None)
        if hasattr(backend, "on_level"):
            backend.on_level = lambda level: hud.publish("level", {"out": round(level, 4)})
        hud.publish("config", config_summary(cfg, engines, transcriber))
    return _run(cfg, wake, vad, transcriber, speaker, hud)


def config_summary(cfg: Config, engines: list, transcriber: Any) -> dict:
    """What the HUD shows in its systems panel."""
    import json as _json

    from .config import REPO_ROOT
    from .stt import mac_memory_gb
    from .wake import hotkey_label

    try:
        servers = _json.loads((REPO_ROOT / ".mcp.json").read_text()).get("mcpServers", {})
        settings = _json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
        tools = [n for n in servers if n not in settings.get("disabledMcpjsonServers", [])]
    except Exception:
        tools = []
    voice = engines[0]
    return {
        "model": cfg.model, "voice": voice.name, "voice_detail": getattr(voice, "voice", "") or "",
        "voice_device": getattr(voice, "device", ""), "speech_model": getattr(transcriber, "model", ""),
        "hotkey": hotkey_label(cfg.hotkey), "tools": tools, "memory_gb": mac_memory_gb(),
        "follow_up_seconds": cfg.follow_up_seconds, "confirm_timeout_seconds": cfg.confirm_timeout_seconds,
    }


def _load_everything(cfg: Config, chip: str, prepare_only: bool):  # pragma: no cover - needs a Mac
    """Load (and with prepare_only, download) every model. Returns (status, wake, vad, stt, player, engines)."""
    from .audio import Player, load_silero_vad
    from .stt import load_transcriber
    from .tts import load_engines
    from .wake import WakeWordDetector, download_wake_model, load_wake_model

    boot_progress(1, 4, "Wake word")
    if prepare_only:
        download_wake_model()
    wake = WakeWordDetector(load_wake_model(), threshold=cfg.wake_threshold)

    boot_progress(2, 4, "Speech detector")
    vad = load_silero_vad()

    boot_progress(3, 4, "Speech recognition")
    transcriber = load_transcriber(cfg.whisper_model_apple, cfg.whisper_model_intel, chip)
    model_name = getattr(transcriber, "model", None)
    if chip == "apple" and model_name and model_name != cfg.whisper_model_apple:
        say_to_user(f"  (Couldn't load {cfg.whisper_model_apple}; using {model_name} instead.)")

    boot_progress(4, 4, "Voice")
    if prepare_only and "piper" in cfg.tts_engines:
        from piper.download_voices import download_voice
        cfg.piper_dir.mkdir(parents=True, exist_ok=True)
        download_voice(cfg.piper_voice, cfg.piper_dir)
    player = Player()
    engines = load_engines(cfg.tts_engines, cfg, notify=say_to_user)
    if not engines:
        return fatal("None of the voices could start. Run ./setup.sh again."), None, None, None, None, None
    where = {"mps": " on the graphics chip", "cpu": " on the processor"}.get(getattr(engines[0], "device", ""), "")
    say_to_user(f"  Voice in use: {engines[0].name}{where}")
    _warm_up_listening(transcriber, engines, chip)
    if prepare_only:
        say_to_user("All models are downloaded and ready.")
    return 0, wake, vad, transcriber, player, engines


SLOW_TRANSCRIPTION_S = 3.0
WARM_UP_SENTENCE = "Hello there, how are you doing today?"


def _warm_up_listening(transcriber: Any, engines: list, chip: str) -> float | None:  # pragma: no cover - Mac
    """Run speech recognition once on a real spoken sentence (made with JARVIS's own voice).

    Warming up on silence leaves most of the one-time setup to your first real request; a real
    sentence gets it all done at startup, and tells us how fast recognition is on this Mac."""
    from .audio import resample
    from .stt import mac_memory_gb, swap_used_gb, timed_transcription

    voice = next((e for e in engines if hasattr(e, "synthesize")), None)
    if voice is None:
        return None
    chunks = list(voice.synthesize(WARM_UP_SENTENCE))
    audio = np.concatenate([resample(a, sr) for a, sr in chunks]) if chunks else np.zeros(16000, np.float32)
    text, seconds = timed_transcription(transcriber, audio)
    log.info("Warm-up transcription took %.2fs: %r", seconds, text)
    say_to_user(f"  Speech recognition test: understood a sample sentence in {seconds:.1f}s")
    memory, swapped = mac_memory_gb(), swap_used_gb()
    if memory:
        line = f"  This Mac has {memory:.0f} GB of memory"
        if swapped and swapped >= 0.5:
            line += (f"; {swapped:.1f} GB has overflowed to the disk, which slows everything down."
                     " Quitting other apps (especially web browsers) helps")
        say_to_user(line + ".")
    if seconds > SLOW_TRANSCRIPTION_S:
        say_to_user("  Speech recognition is slower than it should be, so replies will be slow."
                    " Quitting other apps usually helps.")
    return seconds


def _run(cfg: Config, wake: Any, vad: Any, transcriber: Any, speaker: Any,
         hud: Any = None) -> int:  # pragma: no cover - needs a Mac
    from .audio import Microphone
    from .brain import ClaudeBrain
    from .wake import PushToTalk, hotkey_label

    logger = TurnLogger(cfg.log_path)
    brain = ClaudeBrain(cfg, logger)
    brain.start()
    endpointer = Endpointer(vad, silence_ms=cfg.end_of_speech_silence_ms,
                            start_timeout_s=cfg.listen_timeout_seconds, max_seconds=cfg.max_request_seconds)
    hotkey = PushToTalk(cfg.hotkey)
    if not hotkey.start():
        say_to_user("(The push-to-talk shortcut isn't available - see the README. Voice still works.)")

    mic = Microphone()
    try:
        mic.start()
    except Exception as exc:
        log.error("Microphone failed: %s", exc)
        who = "JARVIS" if hud is not None else "Terminal"
        return fatal(f"I can't use the microphone. Allow microphone access for {who} in "
                     "System Settings > Privacy & Security > Microphone, then start me again.")

    from . import settings as settings_mod

    jarvis = Jarvis(cfg, wake, endpointer, transcriber, speaker, brain, logger, hotkey,
                    events=hud.publish if hud is not None else None,
                    commands=hud.next_command if hud is not None else None,
                    save_settings=settings_mod.save)
    if hud is not None:
        hud.publish("settings", {"values": settings_mod.current(cfg), "options": settings_mod.options()})
        jarvis.emit_toggles()
        _start_telemetry(hud, brain, mic)
    voice_log = speaker.on_timing

    def on_timing(info: dict) -> None:
        if voice_log:
            voice_log(info)
        jarvis.on_voice_timing(info)
    speaker.on_timing = on_timing
    speaker.say("At your service.")
    say_to_user(f"\nJARVIS is ready. Say \"hey Jarvis\" or press {hotkey_label(cfg.hotkey)}. "
                + ("Quit with Command-Q." if hud is not None else "Press Control-C to quit.") + "\n")
    if hud is not None:
        hud.publish("boot", {"step": 4, "total": 4, "label": "Ready", "ready": True})
    try:
        for frame in mic.frames():
            jarvis.note_backlog(mic.backlog())
            jarvis.step(frame)
            if jarvis.state == IDLE and mic.backlog() > 12:     # more than ~1 s behind real time
                dropped = mic.drop_backlog()
                log.warning("Fell %.1f s behind the microphone; skipped ahead", dropped * 0.08)
                logger.log("mic_backlog_dropped", frames=dropped)
    except KeyboardInterrupt:
        say_to_user("\nGoodbye.")
    finally:
        mic.stop()
        hotkey.stop()
        speaker.close()
        brain.stop()
    return 0


def telemetry_snapshot(brain: Any, mic: Any, started: float, rate: Any) -> dict:
    """One reading of how the Mac and JARVIS are doing, for the HUD's diagnostics panel."""
    import os

    from .hud import sysinfo
    from .stt import swap_used_gb

    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    return {
        "uptime_s": round(time.monotonic() - started), "load": load, "cores": os.cpu_count(),
        "cpu": sysinfo.cpu_percent(), "memory": sysinfo.memory(), "swap_gb": swap_used_gb(),
        "battery": sysinfo.battery(), "net": rate.update(sysinfo.network_bytes()), "disk": sysinfo.disk(),
        "claude_online": bool(brain.alive()), "behind_s": round(mic.backlog() * 0.08, 1),
    }


def _start_telemetry(hud: Any, brain: Any, mic: Any, every_s: float = 2.0) -> None:  # pragma: no cover - Mac
    """Every few seconds, tell the HUD how the Mac and JARVIS are doing."""
    from .hud.sysinfo import NetworkRate

    started, rate = time.monotonic(), NetworkRate()

    def loop() -> None:
        while True:
            try:
                hud.publish("telemetry", telemetry_snapshot(brain, mic, started, rate))
            except Exception:
                log.debug("telemetry failed", exc_info=True)
            time.sleep(every_s)

    threading.Thread(target=loop, name="hud-telemetry", daemon=True).start()


def hide_from_dock() -> None:  # pragma: no cover - macOS only
    """The engine runs inside JARVIS.app; make sure it never shows a second icon in the Dock."""
    if sys.platform != "darwin":
        return
    try:
        from AppKit import NSApplication   # part of PyObjC, installed with pynput

        NSApplication.sharedApplication().setActivationPolicy_(2)   # 2 = "prohibited": no Dock icon
    except Exception:
        log.debug("couldn't hide from the Dock", exc_info=True)


def start_hud(open_browser: bool = False):  # pragma: no cover - exercised on a Mac
    """Start the HUD web server and route status lines to it. Returns the event bus."""
    import signal
    import webbrowser

    from .hud.bus import EventBus
    from .hud.server import HudServer

    global HUD_PUBLISH
    hide_from_dock()
    bus = EventBus()
    server = HudServer(bus).start()
    HUD_PUBLISH = bus.publish
    print(f"JARVIS_HUD_URL={server.url}", flush=True)     # JARVIS.app reads this line

    def quit_quietly(signum, frame):                     # JARVIS.app was closed
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, quit_quietly)
    if open_browser:
        webbrowser.open(server.url)
    return bus


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS voice assistant")
    parser.add_argument("--prepare", action="store_true", help="download and load all models, then exit")
    parser.add_argument("--verbose", action="store_true", help="write extra detail to logs/jarvis.log")
    parser.add_argument("--app", action="store_true", help="run with the HUD (used by JARVIS.app)")
    parser.add_argument("--browser", action="store_true", help="with --app: also open the HUD in a web browser")
    args = parser.parse_args(argv)
    hud = start_hud(open_browser=args.browser) if args.app else None
    try:
        cfg = load_config()
    except ConfigError as exc:
        return fatal(f"There's a problem in config.yaml: {exc}")
    setup_logging(cfg.log_path, args.verbose)
    try:
        return build_and_run(cfg, prepare_only=args.prepare, hud=hud)
    except KeyboardInterrupt:
        say_to_user("Goodbye.")
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
