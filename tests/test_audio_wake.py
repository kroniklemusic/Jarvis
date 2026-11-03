from jarvis.audio import FRAME_SECONDS, Endpointer
from jarvis.wake import PushToTalk, WakeWordDetector
from tests.conftest import FakeWakeModel, fake_vad, frame


def frames_for(seconds):
    return int(round(seconds / FRAME_SECONDS))


def test_endpointer_ends_after_700ms_of_silence():
    ep = Endpointer(fake_vad, silence_ms=700, start_timeout_s=5)
    assert ep.feed(frame()) == "waiting"
    assert ep.feed(frame("speech")) == "speaking"
    for _ in range(10):
        assert ep.feed(frame("speech")) == "speaking"
    results = [ep.feed(frame()) for _ in range(frames_for(0.7))]
    assert results[-1] == "done" and "done" not in results[:-1]
    audio = ep.audio()
    # pre-roll (the silent frame before speech) + 11 speech + 9 silence frames
    assert len(audio) > 11 * 1280
    assert audio.dtype.name == "float32"


def test_short_pause_does_not_end_the_request():
    ep = Endpointer(fake_vad, silence_ms=700)
    ep.feed(frame("speech"))
    for _ in range(frames_for(0.4)):
        assert ep.feed(frame()) == "speaking"
    assert ep.feed(frame("speech")) == "speaking"


def test_endpointer_times_out_when_nobody_speaks():
    ep = Endpointer(fake_vad, start_timeout_s=1.0)
    results = [ep.feed(frame()) for _ in range(frames_for(1.0) + 1)]
    assert results[-1] == "timeout" and results[0] == "waiting"


def test_endpointer_caps_very_long_requests():
    ep = Endpointer(fake_vad, max_seconds=2.0)
    results = [ep.feed(frame("speech")) for _ in range(frames_for(2.0) + 2)]
    assert "done" in results


def test_endpointer_reset_changes_timeout():
    ep = Endpointer(fake_vad, start_timeout_s=10)
    ep.reset(0.16)
    assert ep.feed(frame()) == "waiting"
    assert ep.feed(frame()) == "timeout"


def test_wake_detector_fires_once_then_cools_down():
    model = FakeWakeModel()
    wd = WakeWordDetector(model, threshold=0.5, cooldown_frames=3)
    assert wd.process(frame()) is False
    assert wd.process(frame("wake")) is True
    assert model.resets == 1
    assert [wd.process(frame("wake")) for _ in range(3)] == [False] * 3
    assert wd.process(frame("wake")) is True


def test_wake_threshold_is_respected():
    wd = WakeWordDetector(FakeWakeModel(), threshold=0.995)
    assert wd.process(frame("wake")) is False


def test_push_to_talk_consume():
    p = PushToTalk("<ctrl>+<alt>+j")
    assert p.consume() is False
    p.pressed.set()
    assert p.consume() is True
    assert p.consume() is False


# ------------------------------------------------------------ speaker stream reuse
import threading  # noqa: E402

import numpy as np  # noqa: E402

from jarvis.audio import SoundDeviceOutput  # noqa: E402


class FakeStream:
    def __init__(self, samplerate, **kw):
        self.rate, self.active, self.written, self.aborts, self.closed = samplerate, False, 0, 0, False

    def start(self):
        self.active = True

    def write(self, data):
        assert self.active
        self.written += len(data)

    def abort(self):
        self.active = False
        self.aborts += 1

    def close(self):
        self.closed = True


class FakeSD:
    def __init__(self):
        self.opened = []

    def OutputStream(self, **kw):
        s = FakeStream(**kw)
        self.opened.append(s)
        return s


def test_speaker_stream_stays_open_between_clips():
    sd = FakeSD()
    out = SoundDeviceOutput(sd)
    stop = threading.Event()
    out(np.zeros(2400, dtype=np.float32), 24000, stop)
    out(np.zeros(2400, dtype=np.float32), 24000, stop)
    assert len(sd.opened) == 1 and sd.opened[0].written == 4800


def test_speaker_stream_reopens_for_a_new_sample_rate():
    sd = FakeSD()
    out = SoundDeviceOutput(sd)
    stop = threading.Event()
    out(np.zeros(100, dtype=np.float32), 24000, stop)
    out(np.zeros(100, dtype=np.float32), 22050, stop)
    assert [s.rate for s in sd.opened] == [24000, 22050] and sd.opened[0].closed


def test_stop_discards_buffered_audio_and_stream_restarts_next_time():
    sd = FakeSD()
    out = SoundDeviceOutput(sd)
    stop = threading.Event()
    stop.set()
    out(np.zeros(24000, dtype=np.float32), 24000, stop)
    s = sd.opened[0]
    assert s.aborts == 1 and s.written == 0 and not s.active
    stop.clear()
    out(np.zeros(1200, dtype=np.float32), 24000, stop)
    assert s.active and s.written == 1200 and len(sd.opened) == 1
