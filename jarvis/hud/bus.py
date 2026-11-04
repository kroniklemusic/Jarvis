"""Event bus between JARVIS and the HUD.

JARVIS publishes small events ("state", "heard", "said", "level", ...). Every connected HUD gets
them live, and a HUD that connects late first receives a snapshot of what it missed.
"""

from __future__ import annotations

import collections
import queue
import threading
import time
from typing import Any

HISTORY_KINDS = {"heard", "said", "tool", "notice", "permission", "permission_done", "timing", "cleared"}
SNAPSHOT_KINDS = {"state", "telemetry", "config", "boot", "toggles", "settings"}
COMMANDS = {"talk", "stop", "yes", "no", "ask", "mute", "unmute", "voice_on", "voice_off",
            "new_conversation", "settings"}
MAX_TYPED_CHARS = 2000
LEVEL_INTERVAL_S = 1 / 20      # at most 20 audio-level updates per second per kind


class EventBus:
    def __init__(self, history: int = 120, clock=time.monotonic):
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []
        self._history: collections.deque[dict] = collections.deque(maxlen=history)
        self._latest: dict[str, dict] = {}
        self._last_level: dict[str, float] = {}
        self.clock = clock
        self.commands: queue.Queue[tuple[str, dict]] = queue.Queue()

    # -- publishing (called from any thread)
    def publish(self, kind: str, data: dict[str, Any] | None = None) -> None:
        event = {"kind": kind, "data": data or {}, "t": round(time.time(), 3)}
        if kind == "level":
            key = next(iter(event["data"]), "")
            now = self.clock()
            if now - self._last_level.get(key, -1.0) < LEVEL_INTERVAL_S:
                return
            self._last_level[key] = now
        with self._lock:
            if kind == "cleared":                 # a new conversation: forget the old one
                self._history.clear()
            if kind in HISTORY_KINDS:
                self._history.append(event)
            if kind in SNAPSHOT_KINDS:
                self._latest[kind] = event
            clients = list(self._clients)
        for q in clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass   # a stalled HUD must never slow JARVIS down

    # -- subscribing (the web server)
    def subscribe(self) -> tuple[queue.Queue, list[dict]]:
        """Returns a live queue and the snapshot to send first."""
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            snapshot = [self._latest[k] for k in ("config", "settings", "toggles", "boot", "telemetry", "state")
                        if k in self._latest]
            snapshot += list(self._history)
            self._clients.append(q)
        return q, snapshot

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)

    # -- commands from the HUD (buttons, keys, the command bar and settings)
    def command(self, name: str, payload: dict | None = None) -> bool:
        """Queue a command for JARVIS. Anything unknown or invalid is refused."""
        if name not in COMMANDS:
            return False
        payload = payload if isinstance(payload, dict) else {}
        if name == "ask":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                return False
            clean: dict = {"text": " ".join(text.split())[:MAX_TYPED_CHARS]}
        elif name == "settings":
            from ..settings import sanitize

            values = sanitize(payload.get("values"))
            if not values:
                return False
            clean = {"values": values}
        else:
            clean = {}
        self.commands.put((name, clean))
        return True

    def next_command(self) -> tuple[str, dict] | None:
        try:
            return self.commands.get_nowait()
        except queue.Empty:
            return None
