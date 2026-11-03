"""Microphone input, speaker output and end-of-speech detection.

Everything that touches real hardware or macOS-only commands sits behind a small
function or class so the tests can swap it for a fake.
"""

from __future__ import annotations

import collections
import logging
import queue
import subprocess
import threading
from typing import Any, Callable, Iterator

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000        # Hz; what wake word, VAD and Whisper all expect
FRAME_SAMPLES = 1_280       # 80 ms per frame (openWakeWord's preferred size)
VAD_CHUNK = 512             # Silero VAD needs exactly 512 samples at 16 kHz (32 ms)
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE

CHIME_SOUND = "/System/Library/Sounds/Tink.aiff"


# --------------------------------------------------------------------------- mic
class Microphone:
    """Streams 16 kHz mono int16 frames from the default input device."""

    def __init__(self, frame_samples: int = FRAME_SAMPLES):
        self.frame_samples = frame_samples
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd  # imported here so tests never need PortAudio

        def callback(indata, frames, time_info, status):  # runs on the audio thread
            if status:
                log.debug("mic status: %s", status)
            try:
                self._q.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass  # we are badly behind; dropping a frame is better than stalling audio

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=self.frame_samples, callback=callback,
        )
        self._stream.start()

    def read(self, timeout: float = 1.0) -> np.ndarray | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def frames(self) -> Iterator[np.ndarray]:
        while True:
            f = self.read()
            if f is not None:
                yield f

    def backlog(self) -> int:
        """How many frames are waiting to be processed (each is 80 ms)."""
        return self._q.qsize()

    def drop_backlog(self, keep: int = 12) -> int:
        """Throw away old waiting frames so JARVIS catches up with real time, keeping the newest
        `keep` frames (~1 s) so a "hey Jarvis" you are in the middle of saying isn't lost."""
        frames: list[np.ndarray] = []
        try:
            while True:
                frames.append(self._q.get_nowait())
        except queue.Empty:
            pass
        kept = frames[-keep:] if keep > 0 else []
        for f in kept:
            try:
                self._q.put_nowait(f)
            except queue.Full:
                break
        return len(frames) - len(kept)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


# ----------------------------------------------------------------------- speaker
class Player:
    """Plays float32 audio and can be stopped instantly (for barge-in)."""

    def __init__(self, backend: Callable[[np.ndarray, int, threading.Event], None] | None = None):
        self._stop = threading.Event()
        self._backend = backend or SoundDeviceOutput()

    def play(self, samples: np.ndarray, sample_rate: int) -> bool:
        """Blocks until finished. Returns False if it was stopped early."""
        self._stop.clear()
        self._backend(np.asarray(samples, dtype=np.float32), sample_rate, self._stop)
        return not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()


class SoundDeviceOutput:
    """Keeps one speaker stream open between clips.

    Opening a new stream for every sentence adds a pause each time on macOS, so the stream stays
    open and is only reopened if the sample rate changes (e.g. switching from Kokoro to Piper)."""

    BLOCK_SECONDS = 0.1      # how much audio is handed over per write
    BUFFER_SECONDS = 0.25    # how much the sound card keeps queued, so other work never causes gaps

    def __init__(self, sd_module: Any = None):
        self._sd = sd_module
        self._stream = None
        self._rate: int | None = None

    def _open(self, rate: int):
        if self._sd is None:
            import sounddevice as sd
            self._sd = sd
        if self._stream is not None and self._rate == rate:
            return self._stream
        self.close()
        self._stream = self._sd.OutputStream(samplerate=rate, channels=1, dtype="float32",
                                             latency=self.BUFFER_SECONDS)
        self._rate = rate
        return self._stream

    def __call__(self, samples: np.ndarray, sample_rate: int, stop: threading.Event) -> None:
        out = self._open(sample_rate)
        if not out.active:
            out.start()
        block = max(1, int(sample_rate * self.BLOCK_SECONDS))
        for i in range(0, len(samples), block):
            if stop.is_set():
                out.abort()          # throw away anything still buffered so speech stops at once
                return
            out.write(samples[i:i + block].reshape(-1, 1))

    def close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
        self._stream, self._rate = None, None


def play_chime(enabled: bool = True) -> None:
    """Short 'I'm listening' sound using macOS afplay. Never raises."""
    if not enabled:
        return
    try:
        subprocess.Popen(["afplay", CHIME_SOUND], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# -------------------------------------------------------------------------- VAD
def load_silero_vad() -> Callable[[np.ndarray], float]:
    """Return a function: 512 float32 samples -> probability of speech (0..1)."""
    import torch
    from silero_vad import load_silero_vad as _load

    model = _load(onnx=True)

    def prob(chunk: np.ndarray) -> float:
        return float(model(torch.from_numpy(chunk), SAMPLE_RATE).item())

    prob.reset = model.reset_states  # type: ignore[attr-defined]
    return prob


def resample(audio: np.ndarray, rate_from: int, rate_to: int = SAMPLE_RATE) -> np.ndarray:
    """Simple linear resampling (good enough for a warm-up sample, not for listening to)."""
    audio = np.asarray(audio, dtype=np.float32).ravel()
    if rate_from == rate_to or audio.size == 0:
        return audio
    n_out = int(round(audio.size * rate_to / rate_from))
    x_old = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def int16_to_float(frame: np.ndarray) -> np.ndarray:
    return frame.astype(np.float32) / 32768.0


class Endpointer:
    """Collects one spoken request frame by frame and decides when it has ended.

    feed() returns:
      "waiting"  – no speech yet
      "speaking" – speech in progress
      "done"     – speech ended after enough silence (call audio() to get it)
      "timeout"  – nobody started talking in time

    Background sound (a fan, a TV, an echo) can fool the speech detector into thinking you are
    still talking. So while you speak it learns how loud your voice is, and any sound much quieter
    than that (below `quiet_ratio` of your level) counts as silence even if it sounds speech-like.
    """

    def __init__(self, vad_prob: Callable[[np.ndarray], float], silence_ms: int = 700,
                 start_timeout_s: float = 6.0, max_seconds: float = 30.0,
                 threshold: float = 0.5, preroll_ms: int = 300, quiet_ratio: float = 0.15,
                 room_margin: float = 2.5):
        self.vad_prob = vad_prob
        self.silence_samples = int(SAMPLE_RATE * silence_ms / 1000)
        self.start_timeout_samples = int(SAMPLE_RATE * start_timeout_s)
        self.max_samples = int(SAMPLE_RATE * max_seconds)
        self.threshold = threshold
        self.quiet_ratio = quiet_ratio
        self.room_margin = room_margin     # your voice must be this many times louder than the room
        self.noise_floor: float | None = None
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=max(1, int(preroll_ms / 1000 / FRAME_SECONDS)))
        self.stats: dict[str, Any] = {}
        self.reset(start_timeout_s)

    def reset(self, start_timeout_s: float | None = None, noise_floor: float | None = None) -> None:
        """Start a new request. noise_floor is the room's background loudness, if already known."""
        if start_timeout_s is not None:
            self.start_timeout_samples = int(SAMPLE_RATE * start_timeout_s)
        self.noise_floor = noise_floor
        self._waiting_levels: list[float] = []
        self._pending = np.zeros(0, dtype=np.float32)
        self._chunks: list[np.ndarray] = []
        self._preroll.clear()
        self._started = False
        self._silence = 0
        self._waited = 0
        self._total = 0
        self._voice_levels: list[float] = []
        self._quiet_levels: list[float] = []
        reset = getattr(self.vad_prob, "reset", None)
        if callable(reset):
            reset()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def room_level(self) -> float:
        """The room's background loudness: given at reset, or measured while waiting for you."""
        if self.noise_floor is not None:
            return self.noise_floor
        if len(self._waiting_levels) >= 5:
            return float(np.percentile(self._waiting_levels, 30))
        return 0.0

    @property
    def voice_level(self) -> float:
        """Typical loudness of your voice in this request (75th percentile of speech frames)."""
        return float(np.percentile(self._voice_levels, 75)) if self._voice_levels else 0.0

    def feed(self, frame: np.ndarray) -> str:
        f = int16_to_float(frame) if frame.dtype == np.int16 else frame.astype(np.float32)
        self._pending = np.concatenate([self._pending, f])
        vad_says_speech = False
        while len(self._pending) >= VAD_CHUNK:
            chunk, self._pending = self._pending[:VAD_CHUNK], self._pending[VAD_CHUNK:]
            if self.vad_prob(chunk) >= self.threshold:
                vad_says_speech = True
        level = float(np.sqrt(np.mean(f * f))) if len(f) else 0.0

        loud_enough = level >= self.room_margin * self.room_level
        if not self._started:
            self._preroll.append(f)
            self._waiting_levels.append(level)
            if vad_says_speech and loud_enough:
                self._started = True
                self._voice_levels.append(level)
                self._chunks.extend(self._preroll)
                self._total = sum(len(c) for c in self._chunks)
                return "speaking"
            self._waited += len(f)
            return "timeout" if self._waited >= self.start_timeout_samples else "waiting"

        self._chunks.append(f)
        self._total += len(f)
        too_quiet = level < self.quiet_ratio * self.voice_level or not loud_enough
        if vad_says_speech and not too_quiet:
            self._voice_levels.append(level)
            self._silence = 0
        else:
            self._quiet_levels.append(level)
            self._silence += len(f)
        ended_by = ("silence" if self._silence >= self.silence_samples
                    else "length limit" if self._total >= self.max_samples else None)
        if ended_by:
            self.stats = {
                "seconds": round(self._total / SAMPLE_RATE, 2), "ended_by": ended_by,
                "voice_level": round(self.voice_level, 4),
                "background_level": round(float(np.median(self._quiet_levels)), 4) if self._quiet_levels else 0.0,
                "room_level": round(self.room_level, 4),
            }
            return "done"
        return "speaking"

    def audio(self) -> np.ndarray:
        return np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
