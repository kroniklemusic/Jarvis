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

from jarvis.stt import SPARE_MEMORY_BYTES, MlxMemory, MlxWhisper, mac_memory_gb, timed_transcription  # noqa: E402


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


class FakeMx:
    """Stands in for mlx.core's memory controls."""

    def __init__(self, ceiling=5_000_000_000, fail=False):
        self.calls, self.ceiling, self.fail = [], ceiling, fail

    def _check(self):
        if self.fail:
            raise RuntimeError("no Metal")

    def set_cache_limit(self, n):
        self._check()
        self.calls.append(("cache", n))

    def clear_cache(self):
        self._check()
        self.calls.append(("clear",))

    def device_info(self):
        return {"max_recommended_working_set_size": self.ceiling} if self.ceiling else {}

    def set_wired_limit(self, n):
        self._check()
        self.calls.append(("wired", n))

    def get_active_memory(self):
        return 600 * 1024 ** 2

    def get_cache_memory(self):
        return 100 * 1024 ** 2


class FakeMemory(MlxMemory):
    def __init__(self, core, model_bytes=500_000_000):
        super().__init__(core)
        self._bytes = model_bytes

    def model_bytes(self):
        return self._bytes


def test_speech_model_is_kept_in_memory_with_a_small_reserve():
    mx = FakeMx()
    stt = MlxWhisper("m", mlx_module=FakeMlxWhisper(), memory=FakeMemory(mx))
    assert mx.calls[0] == ("cache", SPARE_MEMORY_BYTES)             # set before the model loads
    assert ("wired", 500_000_000 + SPARE_MEMORY_BYTES) in mx.calls     # model plus its reserve
    assert stt.resident_bytes == 500_000_000 + SPARE_MEMORY_BYTES
    stt.transcribe(np.zeros(1600, dtype=np.float32))
    assert stt.last_memory == {"in_use_gb": 0.59, "spare_gb": 0.1}


def test_resident_memory_never_takes_more_than_half_of_what_macos_allows():
    mx = FakeMx(ceiling=600_000_000)
    stt = MlxWhisper("m", mlx_module=FakeMlxWhisper(), memory=FakeMemory(mx, model_bytes=1_500_000_000))
    assert stt.resident_bytes == 300_000_000


def test_memory_tuning_is_skipped_when_unavailable():
    for memory in (FakeMemory(FakeMx(ceiling=0)), FakeMemory(FakeMx(fail=True)), None):
        stt = MlxWhisper("m", mlx_module=FakeMlxWhisper(), memory=memory)
        assert stt.resident_bytes == 0
        assert stt.transcribe(np.zeros(1600, dtype=np.float32)) == "How are you doing today?"
