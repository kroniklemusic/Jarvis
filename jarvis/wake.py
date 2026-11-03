"""Wake word ("hey Jarvis") and the push-to-talk hotkey."""

from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

WAKE_MODEL_NAME = "hey_jarvis"


def download_wake_model() -> None:
    import openwakeword.utils

    openwakeword.utils.download_models([WAKE_MODEL_NAME])


def load_wake_model() -> Any:
    from openwakeword.model import Model

    return Model(wakeword_models=[WAKE_MODEL_NAME], inference_framework="onnx")


class WakeWordDetector:
    """Feeds 80 ms int16 frames to openWakeWord and reports "hey Jarvis"."""

    def __init__(self, model: Any, threshold: float = 0.5, cooldown_frames: int = 12):
        self.model = model
        self.threshold = threshold
        self.cooldown_frames = cooldown_frames  # ~1 s; stops one "hey Jarvis" firing twice
        self._cooldown = 0
        self.last_score = 0.0

    def process(self, frame: np.ndarray) -> bool:
        scores = self.model.predict(frame)
        self.last_score = max(scores.values()) if scores else 0.0
        if self._cooldown > 0:
            self._cooldown -= 1
            return False
        if self.last_score >= self.threshold:
            self.reset()
            self._cooldown = self.cooldown_frames
            return True
        return False

    def reset(self) -> None:
        """Forget recent audio (and any cooldown) so the next "hey Jarvis" is heard fresh."""
        self._cooldown = 0
        reset = getattr(self.model, "reset", None)
        if callable(reset):
            reset()


_KEY_NAMES = {"<ctrl>": "Control", "<alt>": "Option", "<cmd>": "Command", "<shift>": "Shift",
              "<space>": "Space", "<enter>": "Return", "<esc>": "Escape", "<tab>": "Tab"}


def hotkey_label(hotkey: str) -> str:
    """'<ctrl>+<alt>+j' -> 'Control + Option + J' (how the keys are labelled on a Mac)."""
    parts = []
    for part in hotkey.split("+"):
        part = part.strip()
        if part in _KEY_NAMES:
            parts.append(_KEY_NAMES[part])
        elif part.startswith("<") and part.endswith(">"):
            parts.append(part.strip("<>").upper() if len(part) <= 5 else part.strip("<>").title())
        else:
            parts.append(part.upper())
    return " + ".join(parts)


class PushToTalk:
    """A global keyboard shortcut that acts like saying "hey Jarvis"."""

    def __init__(self, hotkey: str):
        self.hotkey = hotkey
        self.pressed = threading.Event()
        self._listener = None

    def start(self) -> bool:
        """Returns False (and JARVIS carries on with voice only) if the hotkey can't be set up."""
        try:
            from pynput import keyboard

            self._listener = keyboard.GlobalHotKeys({self.hotkey: self.pressed.set})
            self._listener.start()
            return True
        except Exception as exc:  # missing Accessibility permission, bad hotkey text, etc.
            log.warning("Push-to-talk hotkey unavailable: %s", exc)
            return False

    def consume(self) -> bool:
        if self.pressed.is_set():
            self.pressed.clear()
            return True
        return False

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
