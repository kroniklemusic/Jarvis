import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from jarvis.hud.bus import EventBus
from jarvis.hud.server import HudServer


# ------------------------------------------------------------------- event bus
def test_bus_sends_live_events_and_a_snapshot_to_late_subscribers():
    bus = EventBus()
    bus.publish("config", {"model": "sonnet"})
    bus.publish("state", {"state": "listening"})
    bus.publish("heard", {"text": "hello"})
    bus.publish("state", {"state": "thinking"})
    q, snapshot = bus.subscribe()
    kinds = [(e["kind"], e["data"].get("state") or e["data"].get("text") or e["data"].get("model")) for e in snapshot]
    assert ("config", "sonnet") in kinds and ("state", "thinking") in kinds and ("heard", "hello") in kinds
    assert ("state", "listening") not in kinds            # only the latest state is replayed
    bus.publish("said", {"text": "Hi."})
    assert q.get(timeout=1)["data"]["text"] == "Hi."
    bus.unsubscribe(q)
    assert bus.client_count == 0


def test_audio_levels_are_throttled():
    ticks = iter([0.0, 0.01, 0.02, 0.2])
    bus = EventBus(clock=lambda: next(ticks))
    q, _ = bus.subscribe()
    for v in (0.1, 0.2, 0.3, 0.4):
        bus.publish("level", {"mic": v})
    got = []
    while not q.empty():
        got.append(q.get_nowait()["data"]["mic"])
    assert got == [0.1, 0.4]


def test_only_known_commands_are_accepted():
    bus = EventBus()
    assert bus.command("talk") and bus.command("yes")
    assert not bus.command("rm -rf /")
    assert [bus.next_command(), bus.next_command(), bus.next_command()] == [("talk", {}), ("yes", {}), None]


def test_typed_requests_are_cleaned_and_limited():
    bus = EventBus()
    assert not bus.command("ask", {"text": "   "})
    assert not bus.command("ask", {"text": 42})
    assert bus.command("ask", {"text": "  What's   the\nweather? ", "extra": "ignored"})
    assert bus.next_command() == ("ask", {"text": "What's the weather?"})
    assert bus.command("ask", {"text": "x" * 5000})
    assert len(bus.next_command()[1]["text"]) == 2000


def test_settings_commands_only_carry_valid_settings():
    bus = EventBus()
    assert not bus.command("settings", {"values": {"claude_command": "rm"}})     # not editable
    assert not bus.command("settings", {"values": {"model": "gpt"}})             # not a choice
    assert bus.command("settings", {"values": {"model": "opus", "wake_threshold": 5, "x": 1}})
    assert bus.next_command() == ("settings", {"values": {"model": "opus", "wake_threshold": 0.9}})


def test_new_conversation_clears_history_for_late_viewers():
    bus = EventBus()
    bus.publish("heard", {"text": "old"})
    bus.publish("cleared", {})
    bus.publish("heard", {"text": "new"})
    _, snapshot = bus.subscribe()
    texts = [e["data"].get("text") for e in snapshot if e["kind"] == "heard"]
    assert texts == ["new"]


# ------------------------------------------------------------------- web server
@pytest.fixture
def server():
    bus = EventBus()
    s = HudServer(bus).start()
    yield s
    s.stop()


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_conversation_stream_and_buttons_require_the_token(server):
    for path in ("/events", "/events?token=wrong"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(f"http://127.0.0.1:{server.port}{path}")
        assert e.value.code == 403
    req = urllib.request.Request(f"http://127.0.0.1:{server.port}/command", data=b'{"command":"talk"}',
                                 method="POST")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403
    assert server.bus.next_command() is None


def test_page_assets_load_without_the_token(server):
    """The browser fetches hud.css and hud.js without the token; they must still load."""
    for name in ("hud.css", "hud.js"):
        status, _, _ = get(f"http://127.0.0.1:{server.port}/{name}")
        assert status == 200


def test_3d_modules_are_served_as_javascript(server):
    """Browsers refuse to run ES modules served with the wrong type."""
    for name in ("scene.js", "art.js", "reactor2d.js", "vendor/three/three.module.min.js",
                 "vendor/three/three.core.min.js"):
        status, ctype, body = get(f"http://127.0.0.1:{server.port}/{name}")
        assert status == 200 and "javascript" in ctype, name
        assert len(body) > 100


def test_server_serves_the_hud_files(server):
    status, ctype, body = get(server.url)
    assert status == 200 and ctype.startswith("text/html") and b"J.A.R.V.I.S." in body
    status, ctype, body = get(f"http://127.0.0.1:{server.port}/hud.js?token={server.token}")
    assert status == 200 and "javascript" in ctype and b"EventSource" in body
    assert server.url.startswith("http://127.0.0.1:")


def test_server_blocks_files_outside_the_hud_folder(server):
    for path in ("/../server.py", "/..%2Fserver.py", "/%2e%2e/bus.py", "/nope.txt"):
        with pytest.raises(urllib.error.HTTPError) as e:
            get(f"http://127.0.0.1:{server.port}{path}?token={server.token}")
        assert e.value.code == 404


def test_buttons_post_commands(server):
    def post(body, token=server.token):
        req = urllib.request.Request(f"http://127.0.0.1:{server.port}/command?token={token}",
                                     data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    assert post({"command": "stop"}) == 200
    assert server.bus.next_command() == ("stop", {})
    assert post({"command": "ask", "text": "Hello there"}) == 200
    assert server.bus.next_command() == ("ask", {"text": "Hello there"})
    with pytest.raises(urllib.error.HTTPError) as e:
        post({"command": "format disk"})
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        post({"command": "talk"}, token="wrong")
    assert e.value.code == 403
    assert server.bus.next_command() is None


def test_event_stream_replays_snapshot_then_streams(server):
    server.bus.publish("state", {"state": "standby"})
    url = f"http://127.0.0.1:{server.port}/events?token={server.token}"
    received = []

    def reader():
        with urllib.request.urlopen(url, timeout=5) as r:
            assert r.headers.get("Content-Type") == "text/event-stream"
            for raw in r:
                line = raw.decode().strip()
                if line.startswith("data: "):
                    received.append(json.loads(line[6:]))
                    if len(received) == 2:
                        return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    deadline = time.monotonic() + 5
    while server.bus.client_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    server.bus.publish("said", {"text": "Good evening."})
    t.join(5)
    assert [e["kind"] for e in received] == ["state", "said"]
    assert received[1]["data"]["text"] == "Good evening."
