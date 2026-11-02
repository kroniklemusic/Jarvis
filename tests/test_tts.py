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
