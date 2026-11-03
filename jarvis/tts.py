"""Text-to-speech: Kokoro first, then Piper, then the macOS `say` command."""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol

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
    """Collects streamed text and hands back each sentence as soon as it is complete.

    To start talking sooner, a long FIRST sentence is split at its first comma (or similar)
    once at least `first_clause_words` words have arrived."""

    def __init__(self, first_clause_words: int = 4) -> None:
        self._buf = ""
        self._emitted = False
        self.first_clause_words = first_clause_words

    def feed(self, delta: str) -> list[str]:
        out = self._feed_sentences(delta)
        if not out and not self._emitted and self.first_clause_words:
            for m in re.finditer(r"[,;:\u2014\u2013](?=\s)", self._buf):
                if len(self._buf[:m.start()].split()) >= self.first_clause_words:
                    clause, self._buf = self._buf[:m.end()], self._buf[m.end():]
                    if clean_for_speech(clause):
                        out.append(clean_for_speech(clause))
                    break
        if out:
            self._emitted = True
        return out

    def _feed_sentences(self, delta: str) -> list[str]:
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
    """An engine has either synthesize(text) -> (audio, sample_rate) chunks, or speak(text) that plays itself."""
    name: str

    def stop(self) -> None: ...


class KokoroTTS:
    """Kokoro voice. Produces audio; the Speaker plays it."""
    name = "kokoro"
    SAMPLE_RATE = 24_000

    def __init__(self, voice: str, speed: float, device: str = "cpu"):
        from kokoro import KPipeline

        # lang_code 'b' = British English, matching the bm_/bf_ voices.
        lang = "b" if voice.startswith("b") else "a"
        self._pipeline = KPipeline(lang_code=lang, repo_id="hexgrad/Kokoro-82M", device=device)
        self.voice, self.speed, self.device = voice, speed, device
        self.measured_ratio: float | None = None
        for _ in self.synthesize("Ready."):  # warm-up: loads (and first time downloads) the voice
            pass

    def synthesize(self, text: str) -> Iterator[tuple[np.ndarray, int]]:
        for result in self._pipeline(text, voice=self.voice, speed=self.speed):
            if result.audio is not None:
                yield result.audio.detach().cpu().numpy(), self.SAMPLE_RATE

    def stop(self) -> None:
        pass


class PiperTTS:
    """Piper voice (fast, slightly more robotic). Produces audio; the Speaker plays it."""
    name = "piper"

    def __init__(self, voice: str, voice_dir: Path, speed: float):
        from piper import PiperVoice, SynthesisConfig

        model = Path(voice_dir) / f"{voice}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"Piper voice file not found: {model}. Run ./setup.sh again.")
        self._voice = PiperVoice.load(model)
        self._syn = SynthesisConfig(length_scale=1.0 / speed)

    def synthesize(self, text: str) -> Iterator[tuple[np.ndarray, int]]:
        for chunk in self._voice.synthesize(text, syn_config=self._syn):
            yield chunk.audio_float_array, chunk.sample_rate

    def stop(self) -> None:
        pass


def run_say(text: str, voice: str, rate_wpm: int) -> subprocess.Popen:
    """Start the macOS `say` command. Kept tiny so tests can replace it."""
    return subprocess.Popen(["say", "-v", voice, "-r", str(rate_wpm), text],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class SayTTS:
    """The Mac's built-in voice. It plays the sound itself, so it has speak() instead of synthesize()."""
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


# Different lengths on purpose: some devices (the Apple graphics chip) are only slow the first time
# they see a sentence of a new length, and real replies are always new lengths.
SPEED_TEST_TEXTS = ("Certainly. I'll take care of that for you right away.",
                    "The weather tomorrow looks mild, with light rain arriving in the late afternoon.")


def speed_ratio(engine: Any, clock: Callable[[], float] = time.monotonic) -> float:
    """Seconds of work per second of speech. 0.2 means 5x faster than real time; above 1 is too slow."""
    t0 = clock()
    seconds = sum(len(a) / sr for text in SPEED_TEST_TEXTS for a, sr in engine.synthesize(text))
    return (clock() - t0) / seconds if seconds else float("inf")


def performance_cores(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int | None:
    """Number of fast ("performance") cores on an Apple Silicon Mac, or None if unknown."""
    try:
        out = run(["sysctl", "-n", "hw.perflevel0.physicalcpu"], capture_output=True, text=True, timeout=5)
        n = int(out.stdout.strip())
        return n if n > 0 else None
    except Exception:
        return None


def audio_is_sound(chunks: list[tuple[np.ndarray, int]]) -> bool:
    """True if the audio is real sound (not empty, silent or full of invalid numbers)."""
    if not chunks:
        return False
    audio = np.concatenate([np.asarray(a, dtype=np.float32).ravel() for a, _ in chunks])
    return audio.size > 0 and bool(np.isfinite(audio).all()) and float(np.abs(audio).max()) > 1e-3


def kokoro_devices(setting: str, chip: str, mps_available: Callable[[], bool]) -> list[str]:
    """Which devices to try for Kokoro, in order."""
    if setting in ("cpu", "mps"):
        return [setting]
    return ["mps", "cpu"] if chip == "apple" and mps_available() else ["cpu"]


def load_kokoro(cfg, chip: str, make: Callable[[str], Any] | None = None,
                mps_available: Callable[[], bool] | None = None,
                measure: Callable[[Any], float] | None = None) -> Any:
    """Load Kokoro on the fastest device that produces correct audio on this Mac."""
    if make is None:
        import torch

        if chip == "apple":
            cores = performance_cores()
            if cores:
                torch.set_num_threads(cores)   # skip the slow efficiency cores
                log.info("Kokoro: using %d performance cores on the processor", cores)
        make = lambda device: KokoroTTS(cfg.kokoro_voice, cfg.speech_speed, device)  # noqa: E731
        mps_available = mps_available or (lambda: bool(torch.backends.mps.is_available()))
    measure = measure or speed_ratio
    best, best_ratio = None, float("inf")
    for device in kokoro_devices(getattr(cfg, "kokoro_device", "auto"), chip, mps_available or (lambda: False)):
        try:
            engine = make(device)
            if not audio_is_sound(list(engine.synthesize("Testing."))):
                raise RuntimeError("it produced silence or invalid audio")
            ratio = measure(engine)
        except (Exception, SystemExit) as exc:   # some libraries call sys.exit() when a download fails
            log.warning("Kokoro on %s didn't work: %s", device, exc)
            continue
        log.info("Kokoro on %s: %.2f s of work per second of speech", device, ratio)
        if ratio < best_ratio:
            best, best_ratio = engine, ratio
    if best is None:
        raise RuntimeError("Kokoro could not run on this Mac")
    best.measured_ratio = best_ratio
    log.info("Kokoro will run on %s", best.device)
    return best


def load_engines(names: list[str], cfg, notify: Callable[[str], None] | None = None,
                 chip: str | None = None) -> list[Any]:
    """Load each configured engine; skip (and log) any that fail to load.

    If cfg.auto_fast_voice is on and Kokoro turns out to be slow on this Mac, Piper is moved first
    so replies start quickly."""
    from .stt import detect_chip

    chip = chip or detect_chip()
    engines: list[Any] = []
    for name in names:
        try:
            if name == "kokoro":
                engines.append(load_kokoro(cfg, chip))
            elif name == "piper":
                engines.append(PiperTTS(cfg.piper_voice, cfg.piper_dir, cfg.speech_speed))
            elif name == "say":
                engines.append(SayTTS(cfg.say_voice, cfg.speech_speed))
            log.info("Voice engine ready: %s", name)
        except (Exception, SystemExit) as exc:   # SystemExit: spaCy exits if its download fails
            log.warning("Voice engine %s could not load: %s", name, exc)
    return prefer_fast_engine(engines, cfg, notify)


def prefer_fast_engine(engines: list[Any], cfg, notify: Callable[[str], None] | None = None,
                       measure: Callable[[Any], float] = speed_ratio) -> list[Any]:
    if not engines or not hasattr(engines[0], "synthesize"):
        return engines
    try:
        ratio = getattr(engines[0], "measured_ratio", None)
        if ratio is None:
            ratio = measure(engines[0])
    except Exception as exc:
        log.warning("Voice speed test failed: %s", exc)
        return engines
    log.info("Voice speed: %s needs %.2f s of work per second of speech", engines[0].name, ratio)
    if not getattr(cfg, "auto_fast_voice", True) or ratio <= SLOW_VOICE_RATIO:
        return engines
    faster = [e for e in engines[1:] if e.name == "piper"]
    if not faster:
        return engines
    first = engines[0]
    reordered = [faster[0], first] + [e for e in engines[1:] if e is not faster[0]]
    if notify:
        notify(f"  The {first.name} voice is slow on this Mac, so I'll use the faster piper voice for quick replies.\n"
               f"  (To keep {first.name} anyway, set auto_fast_voice: false in config.yaml.)")
    return reordered


SLOW_VOICE_RATIO = 0.5   # Kokoro taking more than half the speech length means a noticeable wait


# ---------------------------------------------------------------------- speaker
class Speaker:
    """Speaks sentences in order, without gaps, while Claude is still writing.

    Two background threads work as a pipeline:
      * the "voice builder" turns each queued sentence into audio, and
      * the "player" plays audio as soon as it is ready,
    so sentence 2 is being prepared while sentence 1 plays.

    say() queues a sentence and returns at once. interrupt() stops the sound and drops everything
    queued (barge-in). If an engine fails, the next engine in the list takes over.
    """

    def __init__(self, engines: list[Any], player: Player | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 on_timing: Callable[[dict], None] | None = None):
        if not engines:
            raise RuntimeError("No voice engine could start.")
        self.engines = list(engines)
        self.player = player or Player()
        self.clock = clock
        self.on_timing = on_timing          # receives {"stage", "seconds", "text", ...} for each sentence
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue()
        self._generation = 0
        self._pending = 0                  # sentences queued but not yet fully spoken
        self._last_sound_end = float("-inf")
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._build_loop, name="voice-builder", daemon=True),
                         threading.Thread(target=self._play_loop, name="voice-player", daemon=True)]
        for t in self._threads:
            t.start()

    @property
    def engine(self) -> Any:
        return self.engines[0]

    # -- public
    def say(self, text: str) -> None:
        text = clean_for_speech(text)
        if text:
            with self._lock:
                self._pending += 1
                self._text_q.put((self._generation, text, self.clock()))

    def interrupt(self) -> None:
        with self._lock:
            self._generation += 1
            self._pending = 0
            for q in (self._text_q, self._audio_q):
                try:
                    while True:
                        q.get_nowait()
                except queue.Empty:
                    pass
        self.player.stop()
        for e in self.engines:
            e.stop()

    def is_speaking(self) -> bool:
        return self._pending > 0

    def quiet_for(self) -> float:
        """Seconds since JARVIS last finished speaking (0 while speaking).

        The sound card still plays the last fraction of a second after the final write, and the room
        echoes it, so the microphone should wait a moment before listening again."""
        if self.is_speaking():
            return 0.0
        return self.clock() - self._last_sound_end

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until everything queued has been spoken."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.is_speaking():
            if deadline is not None and time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    def close(self) -> None:
        self.interrupt()
        self._text_q.put(None)
        self._audio_q.put(None)

    # -- internals
    def _timing(self, stage: str, seconds: float, text: str, **extra: Any) -> None:
        log.info("voice: %s %.2fs after queueing: %r %s", stage, seconds, text[:40], extra or "")
        if self.on_timing:
            try:
                self.on_timing({"stage": stage, "seconds": round(seconds, 3), "text": text[:80], **extra})
            except Exception:
                pass

    def _current(self, gen: int) -> bool:
        return gen == self._generation

    def _finish_sentence(self, gen: int) -> None:
        with self._lock:
            self._last_sound_end = self.clock()
            if self._current(gen) and self._pending > 0:
                self._pending -= 1

    def _next_engine(self, failed: Any, exc: Exception) -> bool:
        """Drop a broken engine. Returns False if none are left."""
        with self._lock:
            if failed not in self.engines:      # someone already switched away from it
                return bool(self.engines)
            if len(self.engines) == 1:
                log.error("Voice engine %s failed (%s) and there is no other voice to try.", failed.name, exc)
                return False
            if self.engines[0] is failed:
                self.engines.pop(0)
        log.warning("Voice engine %s failed (%s); switching to %s.", failed.name, exc, self.engines[0].name)
        return True

    def _build_loop(self) -> None:
        while True:
            item = self._text_q.get()
            if item is None:
                return
            gen, text, queued_at = item
            if not self._current(gen):
                continue
            while True:
                engine = self.engines[0]
                if not hasattr(engine, "synthesize"):          # e.g. macOS `say`: the player runs it
                    self._audio_q.put((gen, "speak", (engine, text), queued_at, text))
                    break
                produced = 0
                started = self.clock()
                try:
                    for samples, rate in engine.synthesize(text):
                        if not self._current(gen):
                            break
                        if produced == 0:
                            self._timing("audio_ready", self.clock() - queued_at, text, engine=engine.name)
                        produced += 1
                        self._audio_q.put((gen, "audio", (samples, rate), queued_at, text))
                    log.debug("voice: built %r in %.2fs", text[:40], self.clock() - started)
                    break
                except Exception as exc:
                    if produced or not self._current(gen) or not self._next_engine(engine, exc):
                        break
            self._audio_q.put((gen, "end", None, queued_at, text))

    def _play_loop(self) -> None:
        first_audio_logged: set[float] = set()
        while True:
            item = self._audio_q.get()
            if item is None:
                return
            gen, kind, payload, queued_at, text = item
            if not self._current(gen):
                continue
            if kind == "end":
                self._finish_sentence(gen)
                continue
            if queued_at not in first_audio_logged:
                first_audio_logged.add(queued_at)
                self._timing("started_speaking", self.clock() - queued_at, text)
                if len(first_audio_logged) > 1000:
                    first_audio_logged.clear()
            if kind == "audio":
                samples, rate = payload
                self.player.play(samples, rate)
            else:
                self._speak_directly(gen, *payload)

    def _speak_directly(self, gen: int, engine: Any, text: str) -> None:
        while self._current(gen):
            if engine not in self.engines:       # it broke after this sentence was queued
                engine = self.engines[0]
            try:
                if hasattr(engine, "synthesize"):
                    for samples, rate in engine.synthesize(text):
                        if not self._current(gen) or not self.player.play(samples, rate):
                            return
                else:
                    engine.speak(text)
                return
            except Exception as exc:
                if not self._current(gen) or not self._next_engine(engine, exc):
                    return
                engine = self.engines[0]
