"""The main loop: wake word -> listen -> transcribe -> Claude -> speak -> follow-up."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import threading
from typing import Any, Callable

import numpy as np

from .audio import Endpointer, play_chime
from .brain import BrainError, PermissionRequest, TextDelta, ToolCall, TurnDone
from .config import Config, ConfigError, load_config
from .logs import TurnLogger, setup_logging
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
                 chime: Callable[[bool], None] = play_chime, ui: Callable[[str], None] = say_to_user):
        self.cfg, self.wake, self.endpointer = cfg, wake, endpointer
        self.transcriber, self.speaker, self.brain = transcriber, speaker, brain
        self.logger, self.hotkey, self.chime, self.ui = logger, hotkey, chime, ui
        self.state = IDLE
        self.follow_up = False
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self.pending: PendingPermission | None = None
        self._speak_lock = threading.Lock()   # makes "cancel" and "queue a sentence" atomic

    # ------------------------------------------------------------- transitions
    def _hotkey_pressed(self) -> bool:
        return bool(self.hotkey and self.hotkey.consume())

    def _go_idle(self) -> None:
        self.state, self.follow_up = IDLE, False
        self.wake.reset()

    def _go_listening(self, timeout_s: float, follow_up: bool = False) -> None:
        self.state, self.follow_up = LISTENING, follow_up
        self.endpointer.reset(timeout_s)
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

    # -------------------------------------------------------------------- step
    def step(self, frame: np.ndarray) -> None:
        """Process one 80 ms microphone frame."""
        if self.state == IDLE:
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
            if p and not p.listening and not p.done.is_set() and not self.speaker.is_speaking():
                p.listening = True
                self._go_listening(self.cfg.confirm_timeout_seconds)
                self.ui("Waiting for you to say \"yes, proceed\" or \"no\"...")
                return
            worker_done = self._worker is None or not self._worker.is_alive()
            if worker_done and not self.speaker.is_speaking():
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
        raw = self.transcriber.transcribe(audio)
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

    def wait_for_response(self, timeout: float = 10.0) -> None:
        if self._worker:
            self._worker.join(timeout)


# ----------------------------------------------------------------------- start
def build_and_run(cfg: Config, prepare_only: bool = False) -> int:  # pragma: no cover - needs a Mac
    from .audio import Microphone, Player, load_silero_vad
    from .brain import ClaudeBrain
    from .stt import detect_chip, load_transcriber
    from .tts import Speaker, load_engines
    from .wake import PushToTalk, WakeWordDetector, download_wake_model, load_wake_model

    if shutil.which(cfg.claude_command) is None:
        say_to_user("I can't find Claude Code (the 'claude' program). Install it, sign in, then run ./setup.sh again.")
        return 1

    chip = detect_chip()
    say_to_user(f"Starting JARVIS on an {'Apple Silicon' if chip == 'apple' else 'Intel'} Mac.")
    say_to_user("Loading models. The very first time this downloads a few gigabytes, so please be patient...")

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
        return 1
    where = {"mps": " on the graphics chip", "cpu": " on the processor"}.get(getattr(engines[0], "device", ""), "")
    say_to_user(f"  Voice in use: {engines[0].name}{where}")

    if prepare_only:
        say_to_user("All models are downloaded and ready.")
        return 0

    speaker = Speaker(engines, player)
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
    speaker.say("At your service.")
    say_to_user(f"\nJARVIS is ready. Say \"hey Jarvis\" or press {cfg.hotkey}. Press Control-C to quit.\n")
    try:
        for frame in mic.frames():
            jarvis.step(frame)
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
