#!/usr/bin/env python3
"""Stand-in for the `claude` program used by the tests.

Reads stream-json user messages on stdin and answers each one by replaying a
recorded fixture from tests/fixtures/. Keywords in the message pick the fixture:
  "note"   -> tool_use.jsonl          (Claude reads a file, then answers)
  "touch"  -> permission_denied.jsonl (a tool call refused for lack of permission)
  "create the file" -> ask permission (control_request), wait for JARVIS's answer on
              stdin, then replay the recorded "yes" or "no" continuation
  "die"    -> exit immediately with code 3 (simulates a crash)
  "hang"   -> print a little text, then never finish the turn
  anything else -> hello.jsonl
If FAKE_CLAUDE_RECORD is set, the arguments and any ANTHROPIC_* variable names
are appended to that file so tests can check how the process was started.
"""

import json
import os
import sys
import time
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
DELAY = float(os.environ.get("FAKE_CLAUDE_DELAY", "0"))

record = os.environ.get("FAKE_CLAUDE_RECORD")
if record:
    with open(record, "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:],
                            "anthropic_vars": sorted(k for k in os.environ if k.upper().startswith("ANTHROPIC_"))}) + "\n")


def emit(line: str) -> None:
    sys.stdout.write(line.rstrip("\n") + "\n")
    sys.stdout.flush()
    if DELAY:
        time.sleep(DELAY)


for raw in iter(sys.stdin.readline, ""):
    if not raw.strip():
        continue
    text = json.loads(raw)["message"]["content"]
    if "die" in text:
        sys.exit(3)
    if "hang" in text:
        emit((FIXTURES / "hello.jsonl").read_text().splitlines()[0])   # the session "init" line
        emit(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": "Let me think about that for a very long time. "}}}))
        while True:
            time.sleep(1)
    if "create the file" in text:
        for line in (FIXTURES / "permission_ask.jsonl").read_text().splitlines():
            emit(line)
        answer = json.loads(sys.stdin.readline())
        if record:
            with open(record, "a") as f:
                f.write(json.dumps({"control_response": answer}) + "\n")
        allowed = answer["response"]["response"]["behavior"] == "allow"
        for line in (FIXTURES / f"permission_after_{'yes' if allowed else 'no'}.jsonl").read_text().splitlines():
            emit(line)
        continue
    name = "tool_use" if "note" in text else "permission_denied" if "touch" in text else "hello"
    for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines():
        emit(line)
