"""Speech-to-text with Whisper. mlx-whisper on Apple Silicon, faster-whisper on Intel."""

from __future__ import annotations

import logging
import platform
import re
from typing import Protocol

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


class MlxWhisper:
    def __init__(self, model: str):
        import mlx_whisper

        self._mlx = mlx_whisper
        self.model = model
        # Warm up once so the model is loaded (and downloaded if needed) at startup.
        self.transcribe(np.zeros(16_000, dtype=np.float32))

    def transcribe(self, audio: np.ndarray) -> str:
        result = self._mlx.transcribe(
            audio.astype(np.float32), path_or_hf_repo=self.model, language=LANGUAGE,
            condition_on_previous_text=False, verbose=None,
        )
        return str(result.get("text", "")).strip()


class FasterWhisper:
    def __init__(self, model: str):
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model, device="cpu", compute_type="int8")

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _info = self._model.transcribe(
            audio.astype(np.float32), language=LANGUAGE, beam_size=1,
            condition_on_previous_text=False,
        )
        return "".join(s.text for s in segments).strip()


def load_transcriber(apple_model: str, intel_model: str, chip: str | None = None) -> Transcriber:
    chip = chip or detect_chip()
    if chip == "apple":
        log.info("Speech recognition: mlx-whisper (%s)", apple_model)
        return MlxWhisper(apple_model)
    log.info("Speech recognition: faster-whisper (%s, int8)", intel_model)
    return FasterWhisper(intel_model)
