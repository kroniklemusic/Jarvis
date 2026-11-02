import json

import pytest

from jarvis.brain import PermissionRequest, TurnDone, build_command, parse_line, permission_answer
from jarvis.config import Config
from jarvis.permissions import describe_action, parse_answer, permission_question
from tests.test_brain import FIX, launches, make_brain


@pytest.mark.parametrize("text, expected", [
    ("Yes, proceed.", True), ("yes", True), ("Yeah go ahead", True), ("Proceed!", True), ("Do it.", True),
    ("No.", False), ("No, don't.", False), ("Stop", False), ("Cancel that", False),
    ("Yes. No wait, don't.", False),          # any "no" wins
    ("I don't know", False),
    ("What's the weather?", None), ("", None), (None, None), ("Banana", None),
])
def test_parse_answer(text, expected):
    assert parse_answer(text) is expected


@pytest.mark.parametrize("tool, inp, words", [
    ("Bash", {"command": "touch x", "description": "Create empty file"}, "run a command on your Mac to create empty file"),
    ("Bash", {"command": "ls -la"}, "run this command on your Mac: ls -la"),
    ("Write", {"file_path": "/Users/you/notes/todo.md"}, "create or overwrite the file todo.md"),
    ("Edit", {"file_path": "/a/b.txt"}, "change the file b.txt"),
    ("WebFetch", {"url": "https://www.example.com/page"}, "open the web page on example.com"),
    ("mcp__macos-automator__execute_script", {"script_content": "tell app \"Mail\""}, "run an AppleScript"),
    ("mcp__macos-automator__execute_script", {"kb_script_id": "safari_get_front_tab_url"}, "safari get front tab url automation"),
    ("mcp__playwright__browser_click", {"element": "Buy now button", "ref": "e5"}, "click Buy now button on the web page"),
    ("mcp__playwright__browser_type", {"element": "search box", "text": "cheap flights"}, 'type "cheap flights" into search box'),
    ("mcp__playwright__browser_run_code_unsafe", {}, "run code inside the web page"),
    ("mcp__google-workspace__send_gmail_message", {}, "use Google Workspace to send gmail message"),
    ("mcp__other__do_thing", {}, "use the other tool to do thing"),
])
def test_describe_action(tool, inp, words):
    assert words in describe_action(tool, inp)


def test_question_is_speakable():
    q = permission_question("Bash", {"command": "rm x", "description": "Delete the file x"})
    assert q == "I'd like to run a command on your Mac to delete the file x. Shall I proceed? Say yes, proceed, or no."
    assert "*" not in q and "`" not in q


def test_command_uses_stdio_permission_prompts_and_loads_project_mcp():
    cmd = build_command(Config())
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert "--strict-mcp-config" not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_parse_recorded_permission_request():
    evs = [e for l in (FIX / "permission_ask.jsonl").read_text().splitlines() for e in parse_line(l)]
    req = [e for e in evs if isinstance(e, PermissionRequest)][0]
    assert req.tool_name == "Bash" and req.input["command"] == "touch made_by_claude.txt"
    assert req.request_id == "req-0001"


def test_permission_answer_format():
    yes = json.loads(permission_answer("r1", True, {"command": "ls"}))
    assert yes == {"type": "control_response", "response": {"subtype": "success", "request_id": "r1",
                   "response": {"behavior": "allow", "updatedInput": {"command": "ls"}}}}
    no = json.loads(permission_answer("r1", False))
    assert no["response"]["response"]["behavior"] == "deny"


@pytest.mark.parametrize("allow, phrase", [(True, "It finished with no errors"), (False, "was declined")])
def test_brain_round_trip_permission(cfg, tmp_path, monkeypatch, allow, phrase):
    brain, record, logger = make_brain(cfg, tmp_path, monkeypatch)
    try:
        gen = brain.ask("please create the file")
        req = next(e for e in gen if isinstance(e, PermissionRequest))
        brain.answer_permission(req, allow)
        rest = list(gen)
        assert isinstance(rest[-1], TurnDone) and phrase in rest[-1].text
        sent = [r for r in launches(record) if "control_response" in r]
        assert sent[0]["control_response"]["response"]["request_id"] == "req-0001"
        kinds = [json.loads(l)["kind"] for l in logger.path.read_text().splitlines()]
        assert "permission_request" in kinds and "permission_answer" in kinds
    finally:
        brain.stop()


def test_abandoned_turn_denies_unanswered_permission(cfg, tmp_path, monkeypatch):
    brain, record, _ = make_brain(cfg, tmp_path, monkeypatch)
    try:
        gen = brain.ask("please create the file")
        next(e for e in gen if isinstance(e, PermissionRequest))
        gen.close()                                  # barge-in before answering
        evs = list(brain.ask("hello"))
        assert isinstance(evs[-1], TurnDone)
        sent = [r for r in launches(record) if "control_response" in r]
        assert sent[0]["control_response"]["response"]["response"]["behavior"] == "deny"
        assert len([r for r in launches(record) if "argv" in r]) == 1   # no restart needed
    finally:
        brain.stop()
