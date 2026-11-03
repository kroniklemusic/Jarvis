import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.audio import FRAME_SAMPLES  # noqa: E402
from jarvis.config import Config  # noqa: E402

FAKE_CLAUDE = Path(__file__).parent / "fake_claude.py"
WAKE_MARK = 1234   # a frame filled with this value means "hey Jarvis" was said (quiet enough to count as silence)


def frame(kind: str = "silence") -> np.ndarray:
    if kind == "speech":
        return np.full(FRAME_SAMPLES, 12000, dtype=np.int16)
    if kind == "wake":
        return np.full(FRAME_SAMPLES, WAKE_MARK, dtype=np.int16)
    return np.zeros(FRAME_SAMPLES, dtype=np.int16)


def fake_vad(chunk: np.ndarray) -> float:
    """Loud audio counts as speech."""
    return 1.0 if float(np.abs(chunk).mean()) > 0.1 else 0.0


class FakeWakeModel:
    def __init__(self):
        self.resets = 0

    def predict(self, f):
        return {"hey_jarvis": 0.99 if int(f[0]) == WAKE_MARK else 0.01}

    def reset(self):
        self.resets += 1


class FakeEngine:
    def __init__(self, name="fake", fail=False, seconds=0.0):
        self.name, self.fail, self.seconds = name, fail, seconds
        self.spoken: list[str] = []
        self._stop = threading.Event()

    def speak(self, text):
        if self.fail:
            raise RuntimeError("engine broken")
        self._stop.clear()
        end = time.monotonic() + self.seconds
        while time.monotonic() < end:
            if self._stop.is_set():
                return
            time.sleep(0.005)
        self.spoken.append(text)

    def stop(self):
        self._stop.set()


def fake_command_builder(cfg, resume=None):
    cmd = [sys.executable, str(FAKE_CLAUDE)]
    return cmd + (["--resume", resume] if resume else [])


@pytest.fixture
def cfg(tmp_path):
    return Config(log_dir=str(tmp_path / "logs"), claude_timeout_seconds=10, chime=False, echo_guard_seconds=0.05)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()
