import pytest

from jarvis.stt import clean_transcript, detect_chip


def test_detect_chip():
    assert detect_chip("Darwin", "arm64") == "apple"
    assert detect_chip("Darwin", "x86_64") == "intel"
    assert detect_chip("Linux", "x86_64") == "intel"


@pytest.mark.parametrize("text", [
    None, "", "   ", ".", "...", " Thank you for watching!", "Thanks for watching.",
    "[Music]", "(applause)", "Thank you. Thank you. Thank you.", "you", " Please subscribe!",
    "Subtitles by the Amara.org community",
])
def test_drops_empty_and_hallucinated_transcripts(text):
    assert clean_transcript(text) is None


@pytest.mark.parametrize("text", [
    "What's the weather like tomorrow?",
    "Thank you for watching the kids last night, can you remind me to call Sam?",
    "yes, proceed",
    "no",
])
def test_keeps_real_requests(text):
    assert clean_transcript("  " + text + " ") == text


# ------------------------------------------------------------- speed safeguards
import subprocess  # noqa: E402

import numpy as np  # noqa: E402

from jarvis.stt import MlxWhisper, mac_memory_gb, timed_transcription  # noqa: E402


class FakeMlxWhisper:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return {"text": " How are you doing today? "}


def test_whisper_makes_a_single_attempt_per_request():
    fake = FakeMlxWhisper()
    stt = MlxWhisper("some/model", mlx_module=fake)
    assert stt.transcribe(np.zeros(1600, dtype=np.float32)) == "How are you doing today?"
    for call in fake.calls:          # warm-up and the real request
        assert call["temperature"] == 0.0 and call["language"] == "en"
        assert call["path_or_hf_repo"] == "some/model"


def test_timed_transcription():
    ticks = iter([10.0, 10.8])
    text, seconds = timed_transcription(MlxWhisper("m", mlx_module=FakeMlxWhisper()), np.zeros(10),
                                        clock=lambda: next(ticks))
    assert text == "How are you doing today?" and abs(seconds - 0.8) < 1e-9


def test_mac_memory_gb():
    ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, str(16 * 1024 ** 3) + "\n", "")  # noqa: E731
    assert mac_memory_gb(ok) == 16.0

    def missing(*a, **k):
        raise FileNotFoundError
    assert mac_memory_gb(missing) is None


def test_apple_uses_the_configured_model_and_falls_back_if_it_fails():
    from jarvis.stt import APPLE_FALLBACK_MODEL, load_transcriber

    made = []

    def make(model):
        made.append(model)
        if model == "missing/model":
            raise OSError("404 not found")
        return MlxWhisper(model, mlx_module=FakeMlxWhisper())

    t = load_transcriber("mlx-community/whisper-small-mlx", "small", "apple", make_apple=make)
    assert t.model == "mlx-community/whisper-small-mlx" and made == ["mlx-community/whisper-small-mlx"]
    t = load_transcriber("missing/model", "small", "apple", make_apple=make)
    assert t.model == APPLE_FALLBACK_MODEL


def test_intel_uses_faster_whisper_model():
    from jarvis.stt import load_transcriber

    t = load_transcriber("x", "small", "intel", make_intel=lambda m: ("faster", m))
    assert t == ("faster", "small")


def test_swap_used_gb():
    from jarvis.stt import swap_used_gb

    out = "total = 3072.00M  used = 1536.00M  free = 1536.00M  (encrypted)\n"
    assert swap_used_gb(lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")) == 1.5
    out = "total = 0.00M  used = 0.00M  free = 0.00M  (encrypted)\n"
    assert swap_used_gb(lambda *a, **k: subprocess.CompletedProcess(a, 0, out, "")) == 0.0


def test_default_apple_model_is_the_small_one():
    from jarvis.config import Config
    assert Config().whisper_model_apple == "mlx-community/whisper-small-mlx"
