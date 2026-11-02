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
from typing import Callable, Iterator

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
        self._backend = backend or _sounddevice_play

    def play(self, samples: np.ndarray, sample_rate: int) -> bool:
        """Blocks until finished. Returns False if it was stopped early."""
        self._stop.clear()
        self._backend(np.asarray(samples, dtype=np.float32), sample_rate, self._stop)
        return not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()


def _sounddevice_play(samples: np.ndarray, sample_rate: int, stop: threading.Event) -> None:
    import sounddevice as sd

    block = int(sample_rate * 0.05)
    with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32") as out:
        for i in range(0, len(samples), block):
            if stop.is_set():
                return
            out.write(samples[i:i + block].reshape(-1, 1))


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


def int16_to_float(frame: np.ndarray) -> np.ndarray:
    return frame.astype(np.float32) / 32768.0


class Endpointer:
    """Collects one spoken request frame by frame and decides when it has ended.

    feed() returns:
      "waiting"  – no speech yet
      "speaking" – speech in progress
      "done"     – speech ended after enough silence (call audio() to get it)
      "timeout"  – nobody started talking in time
    """

    def __init__(self, vad_prob: Callable[[np.ndarray], float], silence_ms: int = 700,
                 start_timeout_s: float = 6.0, max_seconds: float = 30.0,
                 threshold: float = 0.5, preroll_ms: int = 300):
        self.vad_prob = vad_prob
        self.silence_samples = int(SAMPLE_RATE * silence_ms / 1000)
        self.start_timeout_samples = int(SAMPLE_RATE * start_timeout_s)
        self.max_samples = int(SAMPLE_RATE * max_seconds)
        self.threshold = threshold
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=max(1, int(preroll_ms / 1000 / FRAME_SECONDS)))
        self.reset(start_timeout_s)

    def reset(self, start_timeout_s: float | None = None) -> None:
        if start_timeout_s is not None:
            self.start_timeout_samples = int(SAMPLE_RATE * start_timeout_s)
        self._pending = np.zeros(0, dtype=np.float32)
        self._chunks: list[np.ndarray] = []
        self._preroll.clear()
        self._started = False
        self._silence = 0
        self._waited = 0
        self._total = 0
        reset = getattr(self.vad_prob, "reset", None)
        if callable(reset):
            reset()

    @property
    def started(self) -> bool:
        return self._started

    def feed(self, frame: np.ndarray) -> str:
        f = int16_to_float(frame) if frame.dtype == np.int16 else frame.astype(np.float32)
        self._pending = np.concatenate([self._pending, f])
        speech_in_frame = False
        while len(self._pending) >= VAD_CHUNK:
            chunk, self._pending = self._pending[:VAD_CHUNK], self._pending[VAD_CHUNK:]
            if self.vad_prob(chunk) >= self.threshold:
                speech_in_frame = True

        if not self._started:
            self._preroll.append(f)
            if speech_in_frame:
                self._started = True
                self._chunks.extend(self._preroll)
                self._total = sum(len(c) for c in self._chunks)
                return "speaking"
            self._waited += len(f)
            return "timeout" if self._waited >= self.start_timeout_samples else "waiting"

        self._chunks.append(f)
        self._total += len(f)
        self._silence = 0 if speech_in_frame else self._silence + len(f)
        if self._silence >= self.silence_samples or self._total >= self.max_samples:
            return "done"
        return "speaking"

    def audio(self) -> np.ndarray:
        return np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
