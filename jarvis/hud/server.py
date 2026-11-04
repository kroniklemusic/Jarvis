"""A tiny local web server that serves the HUD and streams live events to it.

It listens only on this Mac (127.0.0.1). The live event stream and the button endpoint require a
random token, so web pages in your browser can't read your conversation or press JARVIS's buttons.
"""

from __future__ import annotations

import hmac
import json
import logging
import mimetypes
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .bus import EventBus

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
KEEPALIVE_S = 15


class HudServer:
    def __init__(self, bus: EventBus, port: int = 0, static_dir: Path = STATIC_DIR):
        self.bus = bus
        self.token = secrets.token_urlsafe(24)
        self.static_dir = static_dir
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler_class())
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="hud-server", daemon=True)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?token={self.token}"

    def start(self) -> "HudServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _handler_class(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):     # keep the console quiet
                log.debug("hud: " + fmt, *args)

            # -- helpers
            def _authorised(self) -> bool:
                query = parse_qs(urlparse(self.path).query)
                given = (query.get("token") or [self.headers.get("X-Jarvis-Token", "")])[0]
                return hmac.compare_digest(given.encode(), server.token.encode())

            def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8") -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            # -- routes
            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/events":           # the live conversation: token required
                    if not self._authorised():
                        return self._send(403, b"Forbidden")
                    return self._events()
                # The page, its stylesheet and script are the same public files as in the repo.
                name = "index.html" if path in ("/", "") else path.lstrip("/")
                target = (server.static_dir / name).resolve()
                if server.static_dir.resolve() not in target.parents or not target.is_file():
                    return self._send(404, b"Not found")
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                return self._send(200, target.read_bytes(), ctype)

            def do_POST(self):
                if not self._authorised():
                    return self._send(403, b"Forbidden")
                if urlparse(self.path).path != "/command":
                    return self._send(404, b"Not found")
                length = int(self.headers.get("Content-Length") or 0)
                if length > 16384:
                    return self._send(413, b"Too large")
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = None
                if not isinstance(body, dict):
                    return self._send(400, b"Bad request")
                ok = server.bus.command(str(body.get("command", "")), body)
                return self._send(200 if ok else 400, b"ok" if ok else b"unknown command")

            def _events(self):
                q, snapshot = server.bus.subscribe()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "keep-alive")
                    self.end_headers()
                    for event in snapshot:
                        self._write_event(event)
                    while True:
                        try:
                            self._write_event(q.get(timeout=KEEPALIVE_S))
                        except queue.Empty:
                            self.wfile.write(b": keep-alive\n\n")
                            self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    server.bus.unsubscribe(q)
                    self.close_connection = True

            def _write_event(self, event: dict) -> None:
                self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()

        return Handler
