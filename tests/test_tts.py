import threading

import numpy as np

from jarvis.audio import Player
from jarvis.tts import SayTTS, SentenceSplitter, Speaker, clean_for_speech
from tests.conftest import FakeEngine, wait_for


def test_clean_for_speech_strips_markdown():
    assert clean_for_speech("**Bold** and *italic*") == "Bold and italic"
    assert clean_for_speech("# Heading\n## Sub") == "Heading Sub"
    assert clean_for_speech("Run ```bash\nls -la\n``` now") == "Run ls -la now"
    assert clean_for_speech("Use `pip` here") == "Use pip here"
    assert clean_for_speech("- one\n- two") == "one two"
    assert clean_for_speech("See [the docs](https://x.y/z).") == "See the docs."
    assert clean_for_speech("my_file_name stays") == "my_file_name stays"


def test_splitter_emits_each_sentence_as_soon_as_it_ends():
    sp = SentenceSplitter()
    assert sp.feed("Hello there. How are") == ["Hello there."]
    assert sp.feed(" you today?") == []          # no following space yet: might continue
    assert sp.feed(" I'm fine") == ["How are you today?"]
    assert sp.flush() == ["I'm fine"]
    assert sp.flush() == []


def test_splitter_handles_abbreviations_numbers_and_newlines():
    sp = SentenceSplitter()
    out = sp.feed("Mr. Smith paid 3.50 pounds. Done!\nNext line ")
    assert out == ["Mr. Smith paid 3.50 pounds.", "Done!"]
    assert sp.flush() == ["Next line"]


def test_splitter_strips_markdown_from_sentences():
    sp = SentenceSplitter()
    assert sp.feed("I'll remember **pineapple**.\n\nI couldn't run `touch x`. ") == [
        "I'll remember pineapple.", "I couldn't run touch x."]


def test_speaker_speaks_in_order():
    e = FakeEngine()
    s = Speaker([e])
    for t in ["One.", "Two.", "Three."]:
        s.say(t)
    assert s.wait(2)
    assert e.spoken == ["One.", "Two.", "Three."]
    assert not s.is_speaking()


def test_speaker_falls_back_to_next_engine():
    broken, backup = FakeEngine("kokoro", fail=True), FakeEngine("say")
    s = Speaker([broken, backup])
    s.say("Hello.")
    s.say("Again.")
    assert s.wait(2)
    assert backup.spoken == ["Hello.", "Again."]
    assert s.engine is backup


def test_speaker_interrupt_stops_and_clears_queue():
    e = FakeEngine(seconds=0.5)
    s = Speaker([e])
    s.say("A long sentence.")
    s.say("Another one.")
    assert wait_for(s.is_speaking)
    s.interrupt()
    assert s.wait(2)
    assert e.spoken == []


def test_player_stop_reports_early_finish():
    started = threading.Event()

    def backend(samples, sr, stop):
        started.set()
        stop.wait(2)

    p = Player(backend)
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("ok", p.play(np.zeros(10), 24000)))
    t.start()
    started.wait(1)
    p.stop()
    t.join(2)
    assert result["ok"] is False


class FakeProc:
    def __init__(self):
        self.terminated = threading.Event()

    def wait(self):
        self.terminated.wait(2)
        return -15

    def poll(self):
        return None if not self.terminated.is_set() else -15

    def terminate(self):
        self.terminated.set()


def test_say_engine_uses_voice_and_can_be_stopped():
    calls = []
    proc = FakeProc()

    def runner(text, voice, rate):
        calls.append((text, voice, rate))
        return proc

    say = SayTTS("Daniel", 1.0, runner=runner)
    t = threading.Thread(target=say.speak, args=("Good evening.",))
    t.start()
    assert wait_for(lambda: calls)
    say.stop()
    t.join(2)
    assert calls == [("Good evening.", "Daniel", 180)]
    assert proc.terminated.is_set()


# ---------------------------------------------------------------- latency pipeline
import time  # noqa: E402

from jarvis.config import Config  # noqa: E402
from jarvis.tts import prefer_fast_engine  # noqa: E402


class FakeSynthEngine:
    """Takes `build_s` seconds to build each sentence's audio; each sentence is `audio_s` long."""

    def __init__(self, name="kokoro", build_s=0.0, audio_s=0.1, fail=False):
        self.name, self.build_s, self.audio_s, self.fail = name, build_s, audio_s, fail
        self.built: list[str] = []

    def synthesize(self, text):
        if self.fail:
            raise RuntimeError("broken")
        time.sleep(self.build_s)
        self.built.append(text)
        yield np.full(int(1000 * self.audio_s), 0.1, dtype=np.float32), 1000

    def stop(self):
        pass


class RecordingPlayer(Player):
    """Pretends to play audio in real time and records when each clip started."""

    def __init__(self):
        self.started: list[float] = []

        def backend(samples, sr, stop):
            self.started.append(time.monotonic())
            stop.wait(len(samples) / sr)
        super().__init__(backend)


def test_next_sentence_is_built_while_current_one_plays():
    engine = FakeSynthEngine(build_s=0.2, audio_s=0.3)
    player = RecordingPlayer()
    s = Speaker([engine], player)
    t0 = time.monotonic()
    for t in ["One.", "Two.", "Three."]:
        s.say(t)
    assert s.wait(5)
    total = time.monotonic() - t0
    # Serial would be 3 x (0.2 + 0.3) = 1.5 s; pipelined is about 0.2 + 3 x 0.3 = 1.1 s.
    assert total < 1.35, total
    gaps = [b - a for a, b in zip(player.started, player.started[1:])]
    assert all(g < 0.4 for g in gaps), gaps          # no build pause between sentences
    assert engine.built == ["One.", "Two.", "Three."]


def test_synth_engine_falls_back_to_say():
    broken, backup = FakeSynthEngine(fail=True), FakeEngine("say")
    s = Speaker([broken, backup], RecordingPlayer())
    s.say("Hello.")
    assert s.wait(2)
    assert backup.spoken == ["Hello."] and s.engine is backup


def test_interrupt_stops_pipelined_audio():
    engine = FakeSynthEngine(build_s=0.0, audio_s=2.0)
    player = RecordingPlayer()
    s = Speaker([engine], player)
    s.say("Long one.")
    s.say("Another.")
    assert wait_for(lambda: player.started)
    t0 = time.monotonic()
    s.interrupt()
    assert not s.is_speaking()
    time.sleep(0.2)
    assert len(player.started) == 1                    # the second sentence never started
    assert time.monotonic() - t0 < 1.0


def test_long_first_sentence_starts_at_first_comma():
    sp = SentenceSplitter()
    assert sp.feed("Of course, ") == []                # too short to split off
    assert sp.feed("I'll look that up for you right now, and then ") == [
        "Of course, I'll look that up for you right now,"]
    assert sp.feed("tell you what I find. Done. ") == ["and then tell you what I find.", "Done."]


def test_only_the_first_sentence_is_split_early():
    sp = SentenceSplitter()
    assert sp.feed("Hello there. ") == ["Hello there."]
    assert sp.feed("Here is a much longer second sentence, with a comma ") == []


class Named:
    def __init__(self, name):
        self.name = name

    def synthesize(self, text):
        yield np.zeros(10), 10

    def stop(self):
        pass


def test_slow_kokoro_hands_over_to_piper():
    k, p, s = Named("kokoro"), Named("piper"), FakeEngine("say")
    notes = []
    assert prefer_fast_engine([k, p, s], Config(), notes.append, measure=lambda e: 1.4) == [p, k, s]
    assert "slow on this Mac" in notes[0]
    assert prefer_fast_engine([k, p, s], Config(), measure=lambda e: 0.2) == [k, p, s]
    assert prefer_fast_engine([k, p, s], Config(auto_fast_voice=False), measure=lambda e: 1.4) == [k, p, s]
    assert prefer_fast_engine([k, s], Config(), measure=lambda e: 1.4) == [k, s]   # no piper to switch to


# ------------------------------------------------------------ Kokoro device choice
import subprocess  # noqa: E402

from jarvis.tts import audio_is_sound, kokoro_devices, load_kokoro, performance_cores  # noqa: E402


class FakeKokoro:
    def __init__(self, device, audio=None, fail=False):
        self.name, self.device, self.fail = "kokoro", device, fail
        self.audio = np.full(100, 0.2, dtype=np.float32) if audio is None else audio
        self.measured_ratio = None

    def synthesize(self, text):
        if self.fail:
            raise RuntimeError("MPS op not supported")
        yield self.audio, 24000

    def stop(self):
        pass


def test_kokoro_devices_to_try():
    assert kokoro_devices("auto", "apple", lambda: True) == ["mps", "cpu"]
    assert kokoro_devices("auto", "apple", lambda: False) == ["cpu"]
    assert kokoro_devices("auto", "intel", lambda: True) == ["cpu"]
    assert kokoro_devices("cpu", "apple", lambda: True) == ["cpu"]
    assert kokoro_devices("mps", "apple", lambda: True) == ["mps"]


def test_kokoro_picks_the_faster_device():
    speeds = {"mps": 0.08, "cpu": 0.6}
    k = load_kokoro(Config(kokoro_device="auto"), "apple", make=FakeKokoro, mps_available=lambda: True,
                    measure=lambda e: speeds[e.device])
    assert k.device == "mps" and k.measured_ratio == 0.08
    speeds = {"mps": 0.9, "cpu": 0.3}
    k = load_kokoro(Config(kokoro_device="auto"), "apple", make=FakeKokoro, mps_available=lambda: True,
                    measure=lambda e: speeds[e.device])
    assert k.device == "cpu"


def test_kokoro_skips_a_device_that_fails_or_gives_bad_audio():
    def make(device):
        if device == "mps":
            return FakeKokoro(device, audio=np.full(100, np.nan, dtype=np.float32))
        return FakeKokoro(device)
    k = load_kokoro(Config(kokoro_device="auto"), "apple", make=make, mps_available=lambda: True, measure=lambda e: 0.4)
    assert k.device == "cpu"
    k = load_kokoro(Config(kokoro_device="auto"), "apple", make=lambda d: FakeKokoro(d, fail=(d == "mps")),
                    mps_available=lambda: True, measure=lambda e: 0.4)
    assert k.device == "cpu"


def test_kokoro_raises_if_nothing_works():
    import pytest
    with pytest.raises(RuntimeError):
        load_kokoro(Config(), "intel", make=lambda d: FakeKokoro(d, fail=True), measure=lambda e: 0.1)


def test_audio_is_sound():
    assert audio_is_sound([(np.full(10, 0.1), 24000)])
    assert not audio_is_sound([])
    assert not audio_is_sound([(np.zeros(10), 24000)])
    assert not audio_is_sound([(np.array([0.1, np.inf]), 24000)])


def test_performance_cores():
    ok = lambda *a, **k: subprocess.CompletedProcess(a, 0, "4\n", "")  # noqa: E731
    assert performance_cores(ok) == 4
    bad = lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "unknown oid")  # noqa: E731
    assert performance_cores(bad) is None

    def missing(*a, **k):
        raise FileNotFoundError("sysctl")
    assert performance_cores(missing) is None


def test_slow_kokoro_check_reuses_startup_measurement():
    k, p = FakeKokoro("cpu"), Named("piper")
    k.measured_ratio = 0.9
    calls = []
    assert prefer_fast_engine([k, p], Config(), measure=lambda e: calls.append(e) or 0.1) == [p, k]
    assert calls == []


def test_a_voice_that_calls_sys_exit_does_not_kill_jarvis(monkeypatch):
    import jarvis.tts as tts

    def exits(*a, **k):
        raise SystemExit(1)       # what spaCy does when its download fails
    monkeypatch.setattr(tts, "load_kokoro", exits)
    engines = tts.load_engines(["kokoro", "say"], Config(), chip="apple")
    assert [e.name for e in engines] == ["say"]


def test_speed_test_uses_several_new_sentences():
    from jarvis.tts import SPEED_TEST_TEXTS, speed_ratio

    seen = []

    class Engine:
        def synthesize(self, text):
            seen.append(text)
            yield np.zeros(1000), 1000

    ticks = iter([0.0, 0.5])
    ratio = speed_ratio(Engine(), clock=lambda: next(ticks))
    assert seen == list(SPEED_TEST_TEXTS) and len(set(seen)) >= 2
    assert ratio == 0.5 / len(SPEED_TEST_TEXTS)


def test_default_voice_device_is_the_processor():
    assert Config().kokoro_device == "cpu"


def test_speaker_reports_timings_for_each_sentence():
    events = []
    s = Speaker([FakeSynthEngine(build_s=0.05, audio_s=0.05)], RecordingPlayer(), on_timing=events.append)
    s.say("First.")
    s.say("Second.")
    assert s.wait(3)
    stages = [(e["stage"], e["text"]) for e in events]
    assert stages.count(("audio_ready", "First.")) == 1 and ("started_speaking", "First.") in stages
    assert ("started_speaking", "Second.") in stages
    ready = next(e for e in events if e["stage"] == "audio_ready")
    assert ready["engine"] == "kokoro" and ready["seconds"] >= 0.05


def test_a_broken_timing_callback_never_stops_speech():
    def broken(_):
        raise ValueError("oops")
    engine = FakeSynthEngine()
    s = Speaker([engine], RecordingPlayer(), on_timing=broken)
    s.say("Still spoken.")
    assert s.wait(2) and engine.built == ["Still spoken."]


def test_quiet_for_counts_from_when_the_sound_stopped():
    s = Speaker([FakeSynthEngine(audio_s=0.2)], RecordingPlayer())
    assert s.quiet_for() > 1000                          # never spoken yet
    s.say("Hello.")
    assert wait_for(s.is_speaking) and s.quiet_for() == 0.0
    assert s.wait(2)
    first = s.quiet_for()
    time.sleep(0.15)
    assert 0.0 <= first < 0.15 and s.quiet_for() >= 0.15
