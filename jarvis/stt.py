"""Speech-to-text with Whisper. mlx-whisper on Apple Silicon, faster-whisper on Intel."""

from __future__ import annotations

import importlib
import logging
import platform
import re
import subprocess
import time
from typing import Any, Callable, Protocol

import numpy as np

log = logging.getLogger(__name__)

LANGUAGE = "en"

# Phrases Whisper commonly invents from silence or background noise.
HALLUCINATIONS = {
    "thank you for watching", "thanks for watching", "thank you for watching and see you next time",
    "thank you so much for watching", "thanks for watching and see you next time",
    "please subscribe", "like and subscribe", "dont forget to like and subscribe",
    "subscribe to my channel", "see you next time", "see you in the next video",
    "thank you", "thank you very much", "thanks", "bye", "bye bye", "you", "okay", "oh",
    "subtitles by the amara org community", "subtitles by", "transcribed by",
    "music", "applause", "silence", "laughter", "inaudible", "blank audio", "no speech",
}


def detect_chip(system: str | None = None, machine: str | None = None) -> str:
    """'apple' for Apple Silicon Macs, otherwise 'intel'."""
    system = system or platform.system()
    machine = machine or platform.machine()
    return "apple" if system == "Darwin" and machine == "arm64" else "intel"


def _normalise(text: str) -> str:
    text = re.sub(r"[\[\(].*?[\]\)]", " ", text.lower())   # [Music], (applause)
    text = re.sub(r"[^a-z0-9' ]+", " ", text).replace("'", "")
    return re.sub(r"\s+", " ", text).strip()


def clean_transcript(text: str | None) -> str | None:
    """Return the request text, or None if it's empty or a known Whisper hallucination."""
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    norm = _normalise(text)
    if not norm or len(norm) < 2:
        return None
    if norm in HALLUCINATIONS:
        return None
    # Repeated hallucinations such as "Thank you. Thank you. Thank you."
    parts = [p for p in re.split(r"[.!?]+", text.lower()) if p.strip()]
    if parts and all(_normalise(p) in HALLUCINATIONS for p in parts):
        return None
    return text


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> str: ...


# One attempt per request. Whisper's default retries up to six times with more randomness when it
# isn't confident, repeating all the work each time - far too slow for a voice assistant.
TEMPERATURE = 0.0

GB = 1024 ** 3
# After each request MLX keeps the memory it used in reserve for next time. By default that reserve
# may grow to several GB, which on an 8 GB Mac pushes everything else (the voice, Claude, your apps)
# out to the disk and makes the whole Mac slow. A small reserve is plenty for speech recognition.
SPARE_MEMORY_BYTES = 256 * 1024 ** 2


class MlxMemory:
    """Keeps speech recognition's memory use small, and its model in memory while JARVIS waits.

    Without this, macOS moves the idle model out to the disk on a Mac that's short of memory, and
    the next request has to wait while it's read back in (seconds, sometimes much longer)."""

    def __init__(self, core: Any):
        self.mx = core

    @classmethod
    def create(cls) -> "MlxMemory | None":
        try:
            import mlx.core as core
        except ImportError:
            return None
        return cls(core)

    def limit_spare(self) -> None:
        self.mx.set_cache_limit(SPARE_MEMORY_BYTES)

    def model_bytes(self) -> int:
        """Size of the speech model mlx-whisper has loaded, in bytes (0 if unknown)."""
        from mlx.utils import tree_flatten

        holder = importlib.import_module("mlx_whisper.transcribe").ModelHolder
        if holder.model is None:
            return 0
        return int(sum(v.nbytes for _, v in tree_flatten(holder.model.parameters())))

    def keep_resident(self, model_bytes: int) -> int:
        """Ask macOS to keep the model in memory (macOS 15 or later; older versions ignore it).

        Returns the number of bytes kept resident, or 0."""
        self.mx.clear_cache()
        ceiling = int(self.mx.device_info().get("max_recommended_working_set_size", 0) or 0)
        if not ceiling or model_bytes <= 0:
            return 0
        limit = min(model_bytes + SPARE_MEMORY_BYTES, ceiling // 2)
        self.mx.set_wired_limit(limit)
        return limit

    def stats(self) -> dict:
        return {"in_use_gb": round(self.mx.get_active_memory() / GB, 2),
                "spare_gb": round(self.mx.get_cache_memory() / GB, 2)}


class MlxWhisper:
    def __init__(self, model: str, mlx_module=None, memory: MlxMemory | None = None):
        if mlx_module is None:
            import mlx_whisper as mlx_module

        self._mlx = mlx_module
        self.model = model
        self.memory = memory if memory is not None else MlxMemory.create()
        self.resident_bytes = 0
        self.last_memory: dict = {}
        self._guard("limit spare memory", lambda: self.memory.limit_spare())
        # Warm up once so the model is loaded (and downloaded if needed) at startup.
        self.transcribe(np.zeros(16_000, dtype=np.float32))
        self.resident_bytes = self._guard(
            "keep the model in memory", lambda: self.memory.keep_resident(self.memory.model_bytes())) or 0
        if self.resident_bytes:
            log.info("Speech model kept in memory: %.2f GB", self.resident_bytes / GB)

    def _guard(self, what: str, action: Callable[[], Any]) -> Any:
        """Memory tuning only makes things faster; if it fails, carry on without it."""
        if self.memory is None:
            return None
        try:
            return action()
        except Exception as exc:
            log.warning("Speech recognition couldn't %s: %s", what, exc)
            return None

    def transcribe(self, audio: np.ndarray) -> str:
        result = self._mlx.transcribe(
            audio.astype(np.float32), path_or_hf_repo=self.model, language=LANGUAGE,
            condition_on_previous_text=False, verbose=None, temperature=TEMPERATURE,
        )
        self.last_memory = self._guard("measure memory", lambda: self.memory.stats()) or {}
        return str(result.get("text", "")).strip()


class FasterWhisper:
    def __init__(self, model: str):
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio.astype(np.float32), language=LANGUAGE, beam_size=1,
            condition_on_previous_text=False, temperature=TEMPERATURE,
        )
        return "".join(s.text for s in segments).strip()


def timed_transcription(transcriber: Transcriber, audio: np.ndarray,
                        clock: Callable[[], float] = time.monotonic) -> tuple[str, float]:
    """Transcribe and return (text, seconds taken)."""
    started = clock()
    text = transcriber.transcribe(audio)
    return text, clock() - started


def mac_memory_gb(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> float | None:
    """Installed memory (RAM) in GB, or None if it can't be read."""
    try:
        out = run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) / 1024 ** 3
    except Exception:
        return None


# Known-good model used if the configured Apple Silicon model can't be downloaded or loaded.
APPLE_FALLBACK_MODEL = "mlx-community/whisper-large-v3-turbo"


def load_transcriber(apple_model: str, intel_model: str, chip: str | None = None,
                     make_apple: Callable[[str], Transcriber] | None = None,
                     make_intel: Callable[[str], Transcriber] | None = None) -> Transcriber:
    chip = chip or detect_chip()
    if chip != "apple":
        log.info("Speech recognition: faster-whisper (%s, int8)", intel_model)
        return (make_intel or FasterWhisper)(intel_model)
    make_apple = make_apple or MlxWhisper
    candidates = [apple_model] + ([APPLE_FALLBACK_MODEL] if apple_model != APPLE_FALLBACK_MODEL else [])
    last_error: BaseException | None = None
    for model in candidates:
        try:
            transcriber = make_apple(model)
            log.info("Speech recognition: mlx-whisper (%s)", model)
            return transcriber
        except (Exception, SystemExit) as exc:
            log.warning("Speech recognition model %s could not load: %s", model, exc)
            last_error = exc
    raise RuntimeError(f"No speech recognition model could load ({last_error})")


def swap_used_gb(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> float | None:
    """How much memory macOS has moved to disk because it ran out (GB), or None if unknown."""
    try:
        out = run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"used\s*=\s*([\d.]+)([MG])", out)
        if not m:
            return None
        value = float(m.group(1))
        return value / 1024 if m.group(2) == "M" else value
    except Exception:
        return None
