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


def say_to_user(msg: str) -> None:
    """Plain-English status line in the Terminal window."""
    print(msg, flush=True)


class Jarvis:
    def __init__(self, cfg: Config, wake: Any, endpointer: Endpointer, transcriber: Any,
                 speaker: Any, brain: Any, logger: TurnLogger, hotkey: Any = None,
                 chime: Callable[[bool], None] = play_chime, ui: Callable[[str], None] = say_to_user,
                 clock: Callable[[], float] = time.monotonic):
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

    # ------------------------------------------------------------- transitions
    def _hotkey_pressed(self) -> bool:
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
        if self.state == LISTENING:
            started = self.clock()
            self._step(frame)
            self._listen_step_s.append(self.clock() - started)
        else:
            self._step(frame)

    def _step(self, frame: np.ndarray) -> None:
        if self.state == IDLE:
            f = frame.astype(np.float32) / 32768.0 if frame.dtype == np.int16 else frame
            self._room.append(float(np.sqrt(np.mean(f * f))) if len(f) else 0.0)
            woke = self.wake.process(frame)
            if woke or self._hotkey_pressed():
                self.logger.log("wake", source="wake_word" if woke else "hotkey")
                self._go_listening(self.cfg.listen_timeout_seconds)
            return

        if self.state == RESPONDING:
            woke = self.wake.process(frame)
            if woke or self._hotkey_pressed():
                self.barge_in()
                return
            p = self.pending
            if p and not p.listening and not p.done.is_set() and self._speaker_quiet():
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
            heard = clean_transcript(self.transcriber.transcribe(self.endpointer.audio()))
            self.ui(f"You: {heard or '(unclear)'}")
            answer = parse_answer(heard)
            self.logger.log("permission_reply", text=heard, understood=answer)
        self.state = RESPONDING
        p.listening = False
        if answer is None:
            p.attempts += 1
            if result == "done" and p.attempts < 2:
                self.speaker.say(REPROMPT)      # ask once more, then listen again
                return
            self.speaker.say("I'll take that as a no.")
            answer = False
        self.pending = None
        p.resolve(answer)

    def _handle_utterance(self, audio: np.ndarray) -> None:
        started = self.clock()
        raw = self.transcriber.transcribe(audio)
        self.timing = {"heard_at": started, "transcribe_s": self.clock() - started,
                       **getattr(self.endpointer, "stats", {})}
        text = clean_transcript(raw)
        if text is None:
            self.logger.log("dropped_transcript", text=raw)
            self._go_idle()
            self.ui("Say \"hey Jarvis\" when you need me.")
            return
        self.ui(f"You: {text}")
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
            self.speaker.say(sentence)
            self.timing.setdefault("first_text_at", self.clock())
        self.ui(f"JARVIS: {sentence}")
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
        self._speak(permission_question(req.tool_name, req.input, req.description), cancel)
        self.pending = pending
        while not pending.done.wait(0.05):
            if cancel.is_set():
                return False
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
def build_and_run(cfg: Config, prepare_only: bool = False) -> int:  # pragma: no cover - needs a Mac
    from .stt import detect_chip
    from .tts import Speaker

    if shutil.which(cfg.claude_command) is None:
        say_to_user("I can't find Claude Code (the 'claude' program). Install it, sign in, then run ./setup.sh again.")
        return 1

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
    return _run(cfg, wake, vad, transcriber, speaker)


def _load_everything(cfg: Config, chip: str, prepare_only: bool):  # pragma: no cover - needs a Mac
    """Load (and with prepare_only, download) every model. Returns (status, wake, vad, stt, player, engines)."""
    from .audio import Player, load_silero_vad
    from .stt import load_transcriber
    from .tts import load_engines
    from .wake import WakeWordDetector, download_wake_model, load_wake_model

    say_to_user("  1/4  Wake word")
    if prepare_only:
        download_wake_model()
    wake = WakeWordDetector(load_wake_model(), threshold=cfg.wake_threshold)

    say_to_user("  2/4  Speech detector")
    vad = load_silero_vad()

    say_to_user("  3/4  Speech recognition")
    transcriber = load_transcriber(cfg.whisper_model_apple, cfg.whisper_model_intel, chip)

    say_to_user("  4/4  Voice")
    if prepare_only and "piper" in cfg.tts_engines:
        from piper.download_voices import download_voice
        cfg.piper_dir.mkdir(parents=True, exist_ok=True)
        download_voice(cfg.piper_voice, cfg.piper_dir)
    player = Player()
    engines = load_engines(cfg.tts_engines, cfg, notify=say_to_user)
    if not engines:
        say_to_user("None of the voices could start. Run ./setup.sh again.")
        return 1, None, None, None, None, None
    where = {"mps": " on the graphics chip", "cpu": " on the processor"}.get(getattr(engines[0], "device", ""), "")
    say_to_user(f"  Voice in use: {engines[0].name}{where}")
    if prepare_only:
        say_to_user("All models are downloaded and ready.")
    return 0, wake, vad, transcriber, player, engines


def _run(cfg: Config, wake: Any, vad: Any, transcriber: Any, speaker: Any) -> int:  # pragma: no cover - needs a Mac
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
        say_to_user("I can't use the microphone. Allow microphone access for Terminal in "
                    "System Settings > Privacy & Security > Microphone, then start me again.")
        log.error("Microphone failed: %s", exc)
        return 1

    jarvis = Jarvis(cfg, wake, endpointer, transcriber, speaker, brain, logger, hotkey)
    voice_log = speaker.on_timing

    def on_timing(info: dict) -> None:
        if voice_log:
            voice_log(info)
        jarvis.on_voice_timing(info)
    speaker.on_timing = on_timing
    speaker.say("At your service.")
    say_to_user(f"\nJARVIS is ready. Say \"hey Jarvis\" or press {hotkey_label(cfg.hotkey)}. "
                "Press Control-C to quit.\n")
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


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin wrapper
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS voice assistant")
    parser.add_argument("--prepare", action="store_true", help="download and load all models, then exit")
    parser.add_argument("--verbose", action="store_true", help="write extra detail to logs/jarvis.log")
    args = parser.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as exc:
        say_to_user(f"There's a problem in config.yaml: {exc}")
        return 1
    setup_logging(cfg.log_path, args.verbose)
    return build_and_run(cfg, prepare_only=args.prepare)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
