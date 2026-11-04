"""Settings you can change from the JARVIS window, and saving them back to config.yaml.

Only the settings listed here can be changed from the window, and every value is checked first,
so the window can never write anything unexpected into config.yaml.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config

MODELS = [("haiku", "Haiku · fastest"), ("sonnet", "Sonnet · balanced"), ("opus", "Opus · most capable")]

# Kokoro voices (hexgrad/Kokoro-82M). The first letter is the accent: b = British, a = American.
KOKORO_VOICES = [
    ("bm_george", "George · British"), ("bm_fable", "Fable · British"),
    ("bm_lewis", "Lewis · British"), ("bm_daniel", "Daniel · British"),
    ("bf_emma", "Emma · British"), ("bf_isabella", "Isabella · British"),
    ("bf_alice", "Alice · British"), ("bf_lily", "Lily · British"),
    ("am_michael", "Michael · American"), ("am_adam", "Adam · American"),
    ("am_onyx", "Onyx · American"), ("am_eric", "Eric · American"),
    ("af_heart", "Heart · American"), ("af_bella", "Bella · American"),
    ("af_nicole", "Nicole · American"), ("af_sarah", "Sarah · American"),
]

RANGES = {
    "speech_speed": (0.7, 1.5),
    "wake_threshold": (0.2, 0.9),
    "follow_up_seconds": (0.0, 20.0),
}
BOOLEANS = {"show_timings"}
CHOICES = {
    "model": {m for m, _ in MODELS},
    "kokoro_voice": {v for v, _ in KOKORO_VOICES},
}
EDITABLE = set(RANGES) | BOOLEANS | set(CHOICES)


def sanitize(raw: Any) -> dict[str, Any]:
    """Keep only known settings with valid values. Numbers are clamped into their allowed range."""
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if key in CHOICES and isinstance(value, str) and value in CHOICES[key]:
            clean[key] = value
        elif key in BOOLEANS and isinstance(value, bool):
            clean[key] = value
        elif key in RANGES and isinstance(value, (int, float)) and not isinstance(value, bool):
            low, high = RANGES[key]
            if value == value:                      # not NaN
                clean[key] = round(min(high, max(low, float(value))), 2)
    return clean


def current(cfg: Config) -> dict[str, Any]:
    return {key: getattr(cfg, key) for key in sorted(EDITABLE)}


def options() -> dict[str, Any]:
    return {
        "models": [{"value": v, "label": label} for v, label in MODELS],
        "voices": [{"value": v, "label": label} for v, label in KOKORO_VOICES],
        "ranges": {k: list(v) for k, v in RANGES.items()},
    }


def _yaml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def save(values: dict[str, Any], path: Path | str = DEFAULT_CONFIG_PATH) -> None:
    """Write the given settings into config.yaml, keeping every comment and every other line.

    Raises ConfigError (and leaves the file untouched) if the result wouldn't load."""
    values = sanitize(values)
    if not values:
        return
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    for key, value in values.items():
        line = f"{key}: {_yaml_value(value)}"
        pattern = re.compile(rf"^{re.escape(key)}\s*:.*$", re.MULTILINE)
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            text = text.rstrip("\n") + ("\n" if text else "") + line + "\n"
    # Write to a temporary file, check it loads, then swap it in.
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        load_config(tmp)
        os.replace(tmp, path)
    except (ConfigError, OSError):
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
