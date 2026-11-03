import json
import time

import numpy as np

import pytest

from jarvis.audio import Endpointer
from jarvis.brain import ClaudeBrain
from jarvis.logs import TurnLogger
from jarvis.loop import IDLE, LISTENING, RESPONDING, Jarvis
from jarvis.tts import Speaker
from jarvis.wake import WakeWordDetector
from tests.conftest import FakeEngine, FakeWakeModel, fake_command_builder, fake_vad, frame, wait_for


class FakeTranscriber:
    def __init__(self, *texts):
        self.texts = list(texts)
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        assert len(audio) > 0
        return self.texts.pop(0)


class FakeHotkey:
    def __init__(self):
        self.pending = False

    def consume(self):
        p, self.pending = self.pending, False
        return p


@pytest.fixture
def make_jarvis(cfg, monkeypatch):
    made = []

    def _make(*texts, engine_seconds=0.0):
        monkeypatch.delenv("FAKE_CLAUDE_RECORD", raising=False)
        logger = TurnLogger(cfg.log_path)
        engine = FakeEngine(seconds=engine_seconds)
        speaker = Speaker([engine])
        brain = ClaudeBrain(cfg, logger, command_builder=fake_command_builder, drain_grace_s=0.5)
        ep = Endpointer(fake_vad, silence_ms=cfg.end_of_speech_silence_ms,
                        start_timeout_s=cfg.listen_timeout_seconds)
        hotkey = FakeHotkey()
        ui = []
        j = Jarvis(cfg, WakeWordDetector(FakeWakeModel(), cfg.wake_threshold), ep, FakeTranscriber(*texts),
                   speaker, brain, logger, hotkey=hotkey, chime=lambda on: None, ui=ui.append)
        j.engine, j.ui_lines, j.hotkey_fake = engine, ui, hotkey
        made.append(j)
        return j

    yield _make
    for j in made:
        j._cancel.set()
        j.speaker.close()
        j.brain.stop()


def speak_request(j, speech_frames=8):
    for _ in range(speech_frames):
        j.step(frame("speech"))
    for _ in range(10):          # 800 ms of silence ends the request
        j.step(frame())


def pump(j, until, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j.step(frame())
        if until():
            return True
        time.sleep(0.01)
    return False


def log_kinds(j):
    return [json.loads(l)["kind"] for l in j.logger.path.read_text().splitlines()]


def test_full_turn_wake_listen_answer_follow_up(make_jarvis):
    j = make_jarvis("Say hello to me.")
    j.step(frame())
    assert j.state == IDLE
    j.step(frame("wake"))
    assert j.state == LISTENING
    speak_request(j)
    assert j.state == RESPONDING
    assert pump(j, lambda: j.state == LISTENING)
    assert j.follow_up is True
    assert j.engine.spoken == ["Hello there.", "How are you today?"]
    assert "You: Say hello to me." in j.ui_lines
    # the last sentence has no trailing space, so it is spoken when the turn result arrives
    assert log_kinds(j) == ["wake", "user", "spoken", "assistant", "spoken", "turn_timing"]


def test_follow_up_needs_no_wake_word_then_times_out(make_jarvis, cfg):
    cfg.follow_up_seconds = 0.4
    j = make_jarvis("Hello.", "Read the note please.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.state == LISTENING)
    speak_request(j)                              # follow-up, no wake word
    assert j.state == RESPONDING
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert j.engine.spoken[-1] == "The colour is teal."
    assert "(using Read)" in j.ui_lines
    assert pump(j, lambda: j.state == IDLE)       # nobody spoke within the follow-up window


def test_hallucinated_transcript_is_dropped(make_jarvis):
    j = make_jarvis("Thank you for watching!")
    j.step(frame("wake"))
    speak_request(j)
    assert j.state == IDLE
    assert j.engine.spoken == []
    assert log_kinds(j) == ["wake", "dropped_transcript"]


def test_silence_after_wake_returns_to_idle(make_jarvis, cfg):
    cfg.listen_timeout_seconds = 0.5
    j = make_jarvis()
    j.step(frame("wake"))
    for _ in range(8):
        j.step(frame())
    assert j.state == IDLE
    assert "I didn't hear anything." in j.ui_lines


def test_hotkey_works_like_wake_word(make_jarvis):
    j = make_jarvis("hello")
    j.hotkey_fake.pending = True
    j.step(frame())
    assert j.state == LISTENING


def test_barge_in_stops_speech_and_listens_again(make_jarvis):
    j = make_jarvis("Say hello.", "Read the note.", engine_seconds=1.0)
    j.step(frame("wake"))
    speak_request(j)
    assert wait_for(j.speaker.is_speaking)
    j.step(frame("wake"))                      # "hey Jarvis" while JARVIS is talking
    assert j.state == LISTENING and not j.follow_up
    assert wait_for(lambda: not j.speaker.is_speaking(), 2)
    assert j.engine.spoken == []               # the interrupted sentence was cut off
    assert "barge_in" in log_kinds(j)
    j.engine.seconds = 0
    speak_request(j)
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert j.engine.spoken == ["The colour is teal."]


def test_spoken_yes_proceed_allows_the_action(make_jarvis):
    j = make_jarvis("Please create the file.", "Yes, proceed.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.pending is not None and j.pending.listening)
    assert j.engine.spoken[0].startswith("I'd like to run a command on your Mac to create empty file.")
    speak_request(j)                                  # user says "yes, proceed"
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert "Very good." in j.engine.spoken
    assert any("finished with no errors" in s for s in j.engine.spoken)
    kinds = log_kinds(j)
    assert kinds.index("permission_request") < kinds.index("permission_reply") < kinds.index("permission_answer")


def test_spoken_no_denies_the_action(make_jarvis):
    j = make_jarvis("Please create the file.", "No.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.pending is not None and j.pending.listening)
    speak_request(j)
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert "Understood, I won't do that." in j.engine.spoken
    assert any("declined" in s for s in j.engine.spoken)


def test_unclear_answer_asks_again_then_silence_means_no(make_jarvis, cfg):
    cfg.confirm_timeout_seconds = 0.5
    j = make_jarvis("Please create the file.", "Banana.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.pending is not None and j.pending.listening)
    speak_request(j)                                  # unclear reply
    assert pump(j, lambda: any("didn't catch that" in s for s in j.engine.spoken))
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)   # then silence -> treated as no
    assert "I'll take that as a no." in j.engine.spoken
    assert any("declined" in s for s in j.engine.spoken)


def test_wake_word_during_permission_question_denies_it(make_jarvis):
    j = make_jarvis("Please create the file.", "hello", engine_seconds=0.5)
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.pending is not None, timeout=5)
    j.step(frame("wake"))                            # barge-in while the question is being spoken
    assert j.state == LISTENING and j.pending is None
    j.engine.seconds = 0
    speak_request(j)                                  # new request "hello" still works
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert "Hello there." in j.engine.spoken


def test_timing_summary_after_each_answer(make_jarvis):
    j = make_jarvis("Say hello.")
    j.step(frame("wake"))
    speak_request(j)
    j.on_voice_timing({"stage": "started_speaking"})
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    lines = [l for l in j.ui_lines if l.startswith("(timing:")]
    assert len(lines) == 1 and "understood your words" in lines[0] and "Claude's first words" in lines[0]
    assert "turn_timing" in log_kinds(j)


def test_timing_summary_can_be_turned_off(make_jarvis, cfg):
    cfg.show_timings = False
    j = make_jarvis("Say hello.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert not [l for l in j.ui_lines if l.startswith("(timing:")]
    assert "turn_timing" in log_kinds(j)


def test_jarvis_waits_for_its_own_voice_to_die_away_before_listening(make_jarvis, cfg):
    cfg.echo_guard_seconds = 0.6
    j = make_jarvis("Say hello.")
    j.step(frame("wake"))
    speak_request(j)
    assert wait_for(lambda: j.speaker.is_speaking() or j.engine.spoken, 3)
    assert j.speaker.wait(3)
    j.step(frame())
    assert j.state == RESPONDING                         # sound only just stopped: not listening yet
    assert pump(j, lambda: j.state == LISTENING and j.follow_up, timeout=3)
    assert j.speaker.quiet_for() >= 0.6


def test_room_loudness_measured_while_idle_is_used_for_the_next_request(make_jarvis):
    j = make_jarvis("Say hello.")
    room = np.full(1280, int(32767 * 0.05), dtype=np.int16)
    for _ in range(20):
        j.step(room)
    j.step(frame("wake"))
    assert j.state == LISTENING
    assert abs(j.endpointer.noise_floor - 0.05) < 0.005


def test_timing_line_explains_why_listening_stopped_and_lag(make_jarvis):
    j = make_jarvis("Say hello.")
    j.step(frame("wake"))
    j.note_backlog(25)                                   # 2 s behind the microphone
    speak_request(j)
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    line = next(l for l in j.ui_lines if l.startswith("(timing:"))
    assert "stopped after a pause" in line and "recorded" in line
    assert "running 2.0s behind the microphone" in line
