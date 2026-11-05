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
    assert log_kinds(j) == ["wake", "transcribed", "user", "spoken", "assistant", "spoken", "turn_timing"]


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
    assert log_kinds(j) == ["wake", "transcribed", "dropped_transcript"]


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


# ------------------------------------------------------------------- HUD wiring
@pytest.fixture
def hud_jarvis(make_jarvis):
    from jarvis.hud.bus import EventBus

    def _make(*texts, **kw):
        bus = EventBus()
        j = make_jarvis(*texts, **kw)
        j.events, j.commands = bus.publish, bus.next_command
        j.bus = bus
        j.q, _ = bus.subscribe()
        return j
    return _make


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_hud_receives_state_changes_and_conversation(hud_jarvis):
    j = hud_jarvis("Read the note please.")
    j.step(frame())
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    events = drain(j.q)
    states = [e["data"]["state"] for e in events if e["kind"] == "state"]
    assert states[0] == "standby" and "listening" in states and states[-1] == "listening"
    assert "thinking" in states or "speaking" in states
    kinds = [e["kind"] for e in events]
    assert "heard" in kinds and "said" in kinds and "tool" in kinds and "level" in kinds and "timing" in kinds
    tool = next(e for e in events if e["kind"] == "tool")
    assert tool["data"] == {"name": "Read", "label": "Read"}


def test_talk_button_works_like_the_wake_word(hud_jarvis):
    j = hud_jarvis("hello")
    j.bus.command("talk")
    j.step(frame())
    assert j.state == LISTENING


def test_stop_button_cancels_and_goes_to_standby(hud_jarvis):
    j = hud_jarvis("Say hello.", engine_seconds=1.0)
    j.step(frame("wake"))
    speak_request(j)
    assert wait_for(j.speaker.is_speaking)
    j.bus.command("stop")
    j.step(frame())
    assert j.state == IDLE
    assert wait_for(lambda: not j.speaker.is_speaking(), 2) and j.engine.spoken == []
    assert "stopped_from_hud" in log_kinds(j)


def test_authorise_button_answers_a_permission_question(hud_jarvis):
    j = hud_jarvis("Please create the file.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.pending is not None and j.pending.listening)
    events = drain(j.q)
    perm = next(e for e in events if e["kind"] == "permission")
    assert "Shall I proceed" in perm["data"]["question"]
    assert any(e["kind"] == "state" and e["data"]["state"] == "confirm" for e in events)
    j.bus.command("yes")
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert "Very good." in j.engine.spoken
    assert any("finished with no errors" in s for s in j.engine.spoken)
    assert any(e["kind"] == "permission_done" and e["data"]["allowed"] for e in drain(j.q))


def test_deny_button(hud_jarvis):
    j = hud_jarvis("Please create the file.")
    j.step(frame("wake"))
    speak_request(j)
    assert pump(j, lambda: j.pending is not None)
    j.bus.command("no")
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert any("declined" in s for s in j.engine.spoken)


def test_tool_labels():
    from jarvis.loop import tool_label
    assert tool_label("mcp__duckduckgo__search") == "DuckDuckGo: search"
    assert tool_label("mcp__playwright__browser_click") == "Browser: click"
    assert tool_label("Bash") == "Bash"


# ------------------------------------------------------------ command bar & toggles
def test_typed_request_gets_a_full_answer(hud_jarvis):
    j = hud_jarvis()
    j.bus.command("ask", {"text": "say hello"})
    j.step(frame())
    assert j.state == RESPONDING
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert j.engine.spoken == ["Hello there.", "How are you today?"]
    events = drain(j.q)
    heard = next(e for e in events if e["kind"] == "heard")
    assert heard["data"] == {"text": "say hello", "typed": True}
    assert "typed" in log_kinds(j)


def test_typed_request_replaces_the_current_answer(hud_jarvis):
    j = hud_jarvis("Say hello.", engine_seconds=1.0)
    j.step(frame("wake"))
    speak_request(j)
    assert wait_for(j.speaker.is_speaking)
    j.engine.seconds = 0
    j.bus.command("ask", {"text": "read the note"})
    j.step(frame())
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert j.engine.spoken[-1] == "The colour is teal."
    assert "Hello there." not in j.engine.spoken


def test_muted_microphone_ignores_the_wake_word_until_talk_is_pressed(hud_jarvis):
    j = hud_jarvis("hello")
    j.bus.command("mute")
    j.step(frame())
    assert j.muted
    j.step(frame("wake"))
    assert j.state == IDLE
    assert any(e["kind"] == "toggles" and e["data"]["muted"] for e in drain(j.q))
    j.bus.command("talk")                       # pressing Talk turns the microphone back on
    j.step(frame())
    assert not j.muted and j.state == LISTENING


def test_muting_while_listening_stops_listening(hud_jarvis):
    j = hud_jarvis("hello")
    j.step(frame("wake"))
    assert j.state == LISTENING
    j.bus.command("mute")
    j.step(frame())
    assert j.state == IDLE


def test_voice_off_shows_answers_without_speaking(hud_jarvis):
    j = hud_jarvis()
    j.bus.command("voice_off")
    j.bus.command("ask", {"text": "say hello"})
    j.step(frame())
    assert pump(j, lambda: j.state == LISTENING and j.follow_up)
    assert j.engine.spoken == []
    said = [e["data"]["text"] for e in drain(j.q) if e["kind"] == "said"]
    assert said == ["Hello there.", "How are you today?"]


def test_new_conversation_button(hud_jarvis):
    j = hud_jarvis()
    calls = []
    j.brain.new_conversation = lambda: calls.append("new")
    j.bus.command("new_conversation")
    j.step(frame())
    assert calls == ["new"]
    assert any(e["kind"] == "cleared" for e in drain(j.q))


def test_settings_from_the_hud_apply_live_and_are_saved(hud_jarvis):
    j = hud_jarvis()
    voice_calls, saved = [], []

    class Tunable(FakeEngine):
        def set_voice(self, v):
            voice_calls.append(("voice", v))

        def set_speed(self, s):
            voice_calls.append(("speed", s))

    j.speaker.engines.insert(0, Tunable())
    j.save_settings = saved.append
    j.bus.command("settings", {"values": {"model": "opus", "kokoro_voice": "bf_emma",
                                          "speech_speed": 1.2, "wake_threshold": 0.7}})
    j.step(frame())
    assert j.cfg.model == "opus" and j.brain.cfg.model == "opus"
    assert j.wake.threshold == 0.7
    assert ("voice", "bf_emma") in voice_calls and ("speed", 1.2) in voice_calls
    assert saved == [{"model": "opus", "kokoro_voice": "bf_emma", "speech_speed": 1.2, "wake_threshold": 0.7}]
    ev = next(e for e in drain(j.q) if e["kind"] == "settings")
    assert ev["data"]["values"]["model"] == "opus" and ev["data"]["options"]["voices"]


def test_hud_is_told_when_transcription_starts(hud_jarvis):
    j = hud_jarvis("hello")
    j.step(frame("wake"))
    speak_request(j)
    states = [e["data"]["state"] for e in drain(j.q) if e["kind"] == "state"]
    assert "transcribing" in states
    assert states.index("transcribing") > states.index("listening")


class SlowTranscriber(FakeTranscriber):
    """Pretends each transcription takes `seconds`, by moving a fake clock forward."""

    def __init__(self, clock, seconds, *texts):
        super().__init__(*texts)
        self.clock, self.seconds = clock, seconds
        self.last_memory = {"in_use_gb": 0.6, "spare_gb": 0.1}

    def transcribe(self, audio):
        self.clock.now += self.seconds
        return super().transcribe(audio)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_slow_speech_recognition_is_explained_once(make_jarvis):
    j = make_jarvis()
    clock = FakeClock()
    j.clock = clock
    j.transcriber = SlowTranscriber(clock, 12.0, "Hello.", "Hello again.")
    j.swap_gb = lambda: 2.4
    notices = []
    j.events = lambda kind, data: notices.append(data["text"]) if kind == "notice" else None
    j.step(frame("wake"))
    speak_request(j)
    assert any("took 12 seconds" in n and "2.4 GB" in n and "Quitting other apps" in n for n in notices)
    entry = [json.loads(l) for l in j.logger.path.read_text().splitlines() if '"transcribed"' in l][0]
    assert entry["seconds"] == 12.0 and entry["swap_gb"] == 2.4 and entry["in_use_gb"] == 0.6
    # a second slow one soon after isn't announced again
    j._go_idle()
    j.step(frame("wake"))
    speak_request(j)
    assert sum("took 12 seconds" in n for n in notices) == 1


def test_quick_speech_recognition_is_only_logged(make_jarvis):
    j = make_jarvis()
    clock = FakeClock()
    j.clock = clock
    j.transcriber = SlowTranscriber(clock, 0.4, "Hello.")
    checked = []
    j.swap_gb = lambda: checked.append(1) or 0.0
    notices = []
    j.events = lambda kind, data: notices.append(data["text"]) if kind == "notice" else None
    j.step(frame("wake"))
    speak_request(j)
    assert not any("took" in n for n in notices)
    assert not checked                      # memory is only checked when something was slow
