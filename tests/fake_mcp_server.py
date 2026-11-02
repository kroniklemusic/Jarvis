#!/usr/bin/env python3
"""A tiny MCP server for tests: answers 'initialize', or misbehaves on request."""
import json
import sys

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "crash":
    sys.exit(2)
for line in sys.stdin:
    msg = json.loads(line)
    if mode == "silent":
        continue
    if msg.get("method") == "initialize":
        print("some log noise that is not JSON", flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "serverInfo": {"name": "fake-server", "version": "9.9"}}}), flush=True)
