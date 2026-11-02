"""Writes every turn and tool call to logs/ as one JSON object per line."""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class TurnLogger:
    def __init__(self, log_dir: Path):
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self.dir / f"conversation-{datetime.now():%Y-%m-%d}.jsonl"

    def log(self, kind: str, **data: Any) -> None:
        record = {"time": datetime.now().isoformat(timespec="seconds"), "ts": time.time(), "kind": kind, **data}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Technical details go to logs/jarvis.log; the screen only shows plain messages."""
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "jarvis.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
