"""Turning Claude's permission requests into a spoken question, and your spoken answer into yes/no."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlparse

QUESTION_SUFFIX = "Shall I proceed? Say yes, proceed, or no."
REPROMPT = "Sorry, I didn't catch that. Please say yes, proceed, or no."

_NO = re.compile(r"\b(no|nope|nah|don'?t|do not|stop|cancel|deny|negative|abort|never|wait|hold on)\b")
_YES = re.compile(r"\b(yes|yeah|yep|yup|proceed|go ahead|do it|confirm(ed)?|affirmative|approved?|sure|ok go)\b")


def parse_answer(text: str | None) -> bool | None:
    """True for yes, False for no, None if unclear. Any hint of "no" wins, to be safe."""
    if not text:
        return None
    t = re.sub(r"[^a-z' ]+", " ", text.lower())
    if _NO.search(t):
        return False
    if _YES.search(t):
        return True
    return None


def _short(text: Any, limit: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def _words(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip()


def _site(url: str) -> str:
    host = urlparse(str(url)).netloc or str(url)
    return host.removeprefix("www.") or "a website"


def describe_action(tool: str, inp: dict[str, Any], description: str = "") -> str:
    """A short, speakable description of what Claude wants to do."""
    inp = inp or {}
    if tool == "Bash":
        what = inp.get("description") or description
        if what:
            return f"run a command on your Mac to {_short(what).rstrip('.').lower()}"
        return f"run this command on your Mac: {_short(inp.get('command'), 60)}"
    if tool in ("Write", "Edit", "NotebookEdit", "MultiEdit"):
        name = os.path.basename(str(inp.get("file_path") or inp.get("notebook_path") or "a file"))
        return f"{'create or overwrite' if tool == 'Write' else 'change'} the file {name}"
    if tool == "WebSearch":
        return f"search the web for {_short(inp.get('query'), 60)}"
    if tool == "WebFetch":
        return f"open the web page on {_site(inp.get('url', ''))}"
    if tool.startswith("mcp__macos-automator__"):
        if inp.get("kb_script_id"):
            return f"run the {_words(str(inp['kb_script_id']))} automation on your Mac"
        return "run an AppleScript that controls apps on your Mac"
    if tool.startswith("mcp__playwright__"):
        action = tool.rsplit("__", 1)[-1].removeprefix("browser_")
        element = _short(inp.get("element"), 50) or "something"
        if action == "click":
            return f"click {element} on the web page"
        if action == "type":
            return f"type \"{_short(inp.get('text'), 50)}\" into {element} on the web page"
        if action == "fill_form":
            return "fill in a form on the web page"
        if action == "press_key":
            return f"press the {inp.get('key', '')} key on the web page"
        if action == "select_option":
            return f"choose an option in {element} on the web page"
        if action == "file_upload":
            return "upload a file to the web page"
        if action in ("evaluate", "run_code_unsafe"):
            return "run code inside the web page"
        if action == "handle_dialog":
            return "answer a pop-up on the web page"
        return f"{_words(action)} on the web page"
    if tool.startswith("mcp__google-workspace__"):
        return f"use Google Workspace to {_words(tool.rsplit('__', 1)[-1])}"
    if tool.startswith("mcp__"):
        _, server, name = (tool.split("__", 2) + ["", ""])[:3]
        return f"use the {_words(server)} tool to {_words(name)}"
    return f"use the {_words(tool)} tool"


def permission_question(tool: str, inp: dict[str, Any], description: str = "") -> str:
    return f"I'd like to {describe_action(tool, inp, description)}. {QUESTION_SUFFIX}"
