"""Text-to-speech: Kokoro first, then Piper, then the macOS `say` command."""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
from pathlib import Path
from typing import Callable, Protocol

import numpy as np

from .audio import Player

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ text tidying
_FENCE = re.compile(r"```[A-Za-z0-9_+-]*")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^)]+)\)")
_BULLET = re.compile(r"^\s*(?:[-+]|\d+[.)])\s+", re.MULTILINE)


def clean_for_speech(text: str) -> str:
    """Remove markdown symbols a voice would read out literally."""
    text = _FENCE.sub(" ", text)          # ``` and ```python fences
    text = _LINK.sub(r"\1", text)         # [label](url) -> label
    text = _BULLET.sub("", text)          # list markers at line starts
    text = text.replace("`", "").replace("*", "").replace("#", "")
    text = re.sub(r"(?<!\w)_{1,2}|_{1,2}(?!\w)", "", text)  # _emphasis_ but not snake_case
    return re.sub(r"\s+", " ", text).strip()


_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "approx", "no", "jr", "sr"}


class SentenceSplitter:
    """Collects streamed text and hands back each sentence as soon as it is complete."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        start = 0
        for m in re.finditer(r"[.!?]+[\"')\]]*(?=\s)|\n+", self._buf):
            end = m.end()
            candidate = self._buf[start:end]
            if m.group().startswith((".",)) and self._is_abbreviation(candidate):
                continue
            if clean_for_speech(candidate):
                out.append(clean_for_speech(candidate))
            start = end
        self._buf = self._buf[start:]
        return out

    def flush(self) -> list[str]:
        rest, self._buf = clean_for_speech(self._buf), ""
        return [rest] if rest else []

    @staticmethod
    def _is_abbreviation(candidate: str) -> bool:
        words = candidate.rstrip(".").split()
        return bool(words) and words[-1].lower().strip("(\"'") in _ABBREVIATIONS


# ---------------------------------------------------------------------- engines
class TTSEngine(Protocol):
    name: str

    def speak(self, text: str) -> None: ...   # blocks until done or stopped
    def stop(self) -> None: ...


class KokoroTTS:
    name = "kokoro"
    SAMPLE_RATE = 24_000

    def __init__(self, voice: str, speed: float, player: Player):
        from kokoro import KPipeline

        # lang_code 'b' = British English, matching the bm_/bf_ voices.
        lang = "b" if voice.startswith("b") else "a"
        self._pipeline = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M")
        self.voice, self.speed, self.player = voice, speed, player
        self._stopped = threading.Event()
        for _ in self._pipeline("Ready.", voice=self.voice, speed=self.speed):  # warm-up / download voice
            pass

    def speak(self, text: str) -> None:
        self._stopped.clear()
        for result in self._pipeline(text, voice=self.voice, speed=self.speed):
            if self._stopped.is_set():
                return
            if result.audio is None:
                continue
            if not self.player.play(result.audio.detach().cpu().numpy(), self.SAMPLE_RATE):
                return

    def stop(self) -> None:
        self._stopped.set()
        self.player.stop()


class PiperTTS:
    name = "piper"

    def __init__(self, voice: str, voice_dir: Path, speed: float, player: Player):
        from piper import PiperVoice, SynthesisConfig

        model = Path(voice_dir) / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"Piper voice file not found: {model}. Run ./setup.sh again.")
        self._voice = PiperVoice.load(model)
        self._syn = SynthesisConfig(length_scale=1.0 / speed)
        self.player = player
        self._stopped = threading.Event()

    def speak(self, text: str) -> None:
        self._stopped.clear()
        for chunk in self._voice.synthesize(text, syn_config=self._syn):
            if self._stopped.is_set() or not self.player.play(chunk.audio_float_array, chunk.sample_rate):
                return

    def stop(self) -> None:
        self._stopped.set()
        self.player.stop()


def run_say(text: str, voice: str, rate_wpm: int) -> subprocess.Popen:
    """Start the macOS `say` command. Kept tiny so tests can replace it."""
    return subprocess.Popen(["say", "-v", voice, "-r", str(rate_wpm), text],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SayTTS:
    name = "say"

    def __init__(self, voice: str, speed: float, runner: Callable[[str, str, int], subprocess.Popen] = run_say):
        self.voice, self.rate = voice, int(180 * speed)
        self._runner = runner
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def speak(self, text: str) -> None:
        with self._lock:
            self._proc = self._runner(text, self.voice, self.rate)
        rc = self._proc.wait()
        if rc not in (0, -15, None) and rc > 0:
            raise RuntimeError(f"say exited with code {rc}")

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()


def load_engines(names: list[str], cfg, player: Player) -> list[TTSEngine]:
    """Load each configured engine; skip (and log) any that fail to load."""
    engines: list[TTSEngine] = []
    for name in names:
        try:
            if name == "kokoro":
                engines.append(KokoroTTS(cfg.kokoro_voice, cfg.speech_speed, player))
            elif name == "piper":
                engines.append(PiperTTS(cfg.piper_voice, cfg.piper_dir, cfg.speech_speed, player))
            elif name == "say":
                engines.append(SayTTS(cfg.say_voice, cfg.speech_speed))
            log.info("Voice engine ready: %s", name)
        except Exception as exc:
            log.warning("Voice engine %s could not load: %s", name, exc)
    return engines


# ---------------------------------------------------------------------- speaker
class Speaker:
    """Speaks sentences one after another on a background thread.

    say() queues a sentence and returns at once, so Claude can keep streaming.
    interrupt() stops the current sentence and drops the queue (barge-in).
    If an engine fails, the next engine in the list takes over.
    """

    def __init__(self, engines: list[TTSEngine]):
        if not engines:
            raise RuntimeError("No voice engine could start.")
        self.engines = list(engines)
        self._q: queue.Queue[tuple[int, str] | None] = queue.Queue()
        self._generation = 0
        self._busy = threading.Event()
        self._active = False           # True while an engine is actually speaking
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="speaker", daemon=True)
        self._thread.start()

    @property
    def engine(self) -> TTSEngine:
        return self.engines[0]

    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if text:
            with self._lock:
                self._busy.set()
                self._q.put((self._generation, text))

    def interrupt(self) -> None:
        with self._lock:
            self._generation += 1
            try:
                while True:
                    self._q.get_nowait()
            except queue.Empty:
                pass
            if not self._active:
                self._busy.clear()
        for e in self.engines:
            e.stop()

    def is_speaking(self) -> bool:
        return self._busy.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until everything queued has been spoken."""
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        while self._busy.is_set():
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    def close(self) -> None:
        self.interrupt()
        self._q.put(None)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            gen, text = item
            with self._lock:
                current = gen == self._generation
                self._active = current
            if current:
                self._speak_with_fallback(text, gen)
            with self._lock:
                self._active = False
                if self._q.empty():
                    self._busy.clear()

    def _speak_with_fallback(self, text: str, gen: int) -> None:
        while self.engines:
            engine = self.engines[0]
            try:
                engine.speak(text)
                return
            except Exception as exc:
                if gen != self._generation:
                    return
                log.warning("Voice engine %s failed (%s); switching to the next one.", engine.name, exc)
                if len(self.engines) == 1:
                    log.error("No voice engine left to speak: %r", text)
                    return
                self.engines.pop(0)
