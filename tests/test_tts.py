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
