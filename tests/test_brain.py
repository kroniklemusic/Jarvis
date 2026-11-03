import json
from pathlib import Path

from jarvis.brain import (BrainError, ClaudeBrain, TextDelta, ToolCall, ToolResult, TurnDone,
                          build_command, clean_env, parse_line, user_message)
from jarvis.config import Config
from jarvis.logs import TurnLogger
from tests.conftest import fake_command_builder

FIX = Path(__file__).parent / "fixtures"


def events_from(name):
    return [e for line in (FIX / name).read_text().splitlines() for e in parse_line(line)]


def test_command_has_required_flags_and_never_skips_permissions():
    cmd = build_command(Config(model="opus"))
    assert cmd[:2] == ["claude", "-p"]
    for flag in ["--input-format", "--output-format", "--verbose", "--include-partial-messages"]:
        assert flag in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--model") + 1] == "opus"
    assert "--dangerously-skip-permissions" not in cmd
    assert "--resume" not in cmd
    assert build_command(Config(), resume="abc")[-2:] == ["--resume", "abc"]


def test_clean_env_removes_every_anthropic_variable():
    env = {"PATH": "/bin", "HOME": "/Users/x", "ANTHROPIC_API_KEY": "sk-x",
           "ANTHROPIC_BASE_URL": "http://x", "anthropic_model": "y"}
    assert clean_env(env) == {"PATH": "/bin", "HOME": "/Users/x"}


def test_user_message_format():
    assert json.loads(user_message("hi")) == {"type": "user", "message": {"role": "user", "content": "hi"}}


def test_parse_recorded_text_reply():
    evs = events_from("hello.jsonl")
    text = "".join(e.text for e in evs if isinstance(e, TextDelta))
    assert text == "Hello there. How are you today?"
    assert isinstance(evs[-1], TurnDone) and evs[-1].text == text
    assert evs[0] == {"session_id": "00000000-0000-4000-8000-000000000001", "model": "claude-sonnet-5-5"}


def test_parse_recorded_tool_use():
    evs = events_from("tool_use.jsonl")
    calls = [e for e in evs if isinstance(e, ToolCall)]
    results = [e for e in evs if isinstance(e, ToolResult)]
    assert [c.name for c in calls] == ["Read"]
    assert calls[0].input["file_path"].endswith("note.txt")
    assert "teal" in results[0].content
    assert "".join(e.text for e in evs if isinstance(e, TextDelta)) == "The colour is teal."


def test_parse_ignores_junk():
    assert parse_line("") == []
    assert parse_line("not json") == []
    assert parse_line('{"type":"rate_limit_event"}') == []


def make_brain(cfg, tmp_path, monkeypatch, **kw):
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(record))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-reach-claude")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "nope")
    logger = TurnLogger(cfg.log_path)
    brain = ClaudeBrain(cfg, logger, command_builder=fake_command_builder, **kw)
    return brain, record, logger


def launches(record):
    return [json.loads(l) for l in record.read_text().splitlines()]


def test_brain_streams_answer_and_strips_anthropic_env(cfg, tmp_path, monkeypatch):
    brain, record, _ = make_brain(cfg, tmp_path, monkeypatch)
    try:
        evs = list(brain.ask("say hello"))
        assert "".join(e.text for e in evs if isinstance(e, TextDelta)) == "Hello there. How are you today?"
        assert isinstance(evs[-1], TurnDone)
        assert brain.session_id == "00000000-0000-4000-8000-000000000001"
        # Same process handles the second turn.
        evs2 = list(brain.ask("and again"))
        assert isinstance(evs2[-1], TurnDone)
        runs = launches(record)
        assert len(runs) == 1
        assert runs[0]["anthropic_vars"] == []
    finally:
        brain.stop()


def test_brain_logs_turns_and_tool_calls(cfg, tmp_path, monkeypatch):
    brain, _, logger = make_brain(cfg, tmp_path, monkeypatch)
    try:
        list(brain.ask("read the note"))
    finally:
        brain.stop()
    kinds = [json.loads(l)["kind"] for l in logger.path.read_text().splitlines()]
    assert kinds == ["user", "tool_call", "tool_result", "assistant"]


def test_brain_restarts_with_resume_after_crash(cfg, tmp_path, monkeypatch):
    brain, record, _ = make_brain(cfg, tmp_path, monkeypatch)
    try:
        list(brain.ask("hello"))
        evs = list(brain.ask("please die"))
        assert isinstance(evs[-1], BrainError)
        evs = list(brain.ask("hello again"))
        assert isinstance(evs[-1], TurnDone)
        runs = launches(record)
        assert len(runs) == 2
        assert runs[1]["argv"][-2:] == ["--resume", "00000000-0000-4000-8000-000000000001"]
    finally:
        brain.stop()


def test_abandoned_turn_that_never_ends_triggers_resume(cfg, tmp_path, monkeypatch):
    brain, record, _ = make_brain(cfg, tmp_path, monkeypatch, drain_grace_s=0.3)
    try:
        list(brain.ask("hello"))
        gen = brain.ask("hang forever")
        assert isinstance(next(gen), TextDelta)
        gen.close()                      # barge-in: the loop stops reading this answer
        evs = list(brain.ask("new question"))
        assert isinstance(evs[-1], TurnDone)
        assert "Hello there" in evs[-1].text
        assert launches(record)[-1]["argv"][-2:] == ["--resume", "00000000-0000-4000-8000-000000000001"]
    finally:
        brain.stop()


def test_abandoned_turn_that_finishes_is_drained_without_restart(cfg, tmp_path, monkeypatch):
    brain, record, _ = make_brain(cfg, tmp_path, monkeypatch)
    try:
        gen = brain.ask("hello")
        next(gen)
        gen.close()
        evs = list(brain.ask("read the note"))
        assert any(isinstance(e, ToolCall) for e in evs)
        assert len(launches(record)) == 1
    finally:
        brain.stop()


def test_timeout_gives_error_and_restarts(cfg, tmp_path, monkeypatch):
    cfg.claude_timeout_seconds = 0.5
    brain, record, _ = make_brain(cfg, tmp_path, monkeypatch)
    try:
        evs = list(brain.ask("hang"))
        assert isinstance(evs[-1], BrainError)
        assert not brain.alive()
        cfg.claude_timeout_seconds = 10
        evs = list(brain.ask("hello"))
        assert isinstance(evs[-1], TurnDone)
        assert launches(record)[-1]["argv"][-2:] == ["--resume", "00000000-0000-4000-8000-000000000001"]
    finally:
        brain.stop()


def test_missing_claude_gives_plain_message(cfg):
    brain = ClaudeBrain(cfg, command_builder=lambda c, r: ["definitely-not-a-real-claude-binary"])
    try:
        brain.start()
    except RuntimeError as exc:
        assert "can't find the 'claude' program" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_memory_is_handed_to_claude_at_startup(tmp_path):
    from jarvis.brain import memory_prompt

    facts = tmp_path / "facts.md"
    facts.write_text("# Facts\n- Sister is called Anna (2026-11-03)\n")
    cmd = build_command(Config(), memory_file=facts)
    prompt = cmd[cmd.index("--append-system-prompt") + 1]
    assert "Sister is called Anna" in prompt and "don't need to open it" in prompt
    assert build_command(Config(), resume="abc", memory_file=facts)[-2:] == ["--resume", "abc"]
    assert "--append-system-prompt" not in build_command(Config(), memory_file=tmp_path / "missing.md")
    facts.write_text("x" * 9000 + "NEWEST")
    assert memory_prompt(facts).endswith("NEWEST")      # very long files keep the newest facts
