"""Load and check settings from config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

ALLOWED_TTS = ("kokoro", "piper", "say")


class ConfigError(ValueError):
    """A setting in config.yaml is missing or wrong. The message is plain English."""


@dataclass
class Config:
    model: str = "sonnet"
    tts_engines: list[str] = field(default_factory=lambda: ["kokoro", "piper", "say"])
    kokoro_voice: str = "bm_george"
    piper_voice: str = "en_GB-alan-medium"
    say_voice: str = "Daniel"
    speech_speed: float = 1.0
    auto_fast_voice: bool = True
    kokoro_device: str = "cpu"
    wake_threshold: float = 0.5
    hotkey: str = "<ctrl>+<alt>+j"
    follow_up_seconds: float = 8.0
    end_of_speech_silence_ms: int = 700
    listen_timeout_seconds: float = 6.0
    max_request_seconds: float = 20.0
    confirm_timeout_seconds: float = 15.0
    whisper_model_apple: str = "mlx-community/whisper-large-v3-turbo"
    whisper_model_intel: str = "small"
    claude_command: str = "claude"
    claude_timeout_seconds: float = 300.0
    log_dir: str = "logs"
    chime: bool = True
    show_timings: bool = True
    echo_guard_seconds: float = 0.8   # wait this long after JARVIS stops talking before listening

    @property
    def log_path(self) -> Path:
        p = Path(self.log_dir)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def piper_dir(self) -> Path:
        return REPO_ROOT / "models" / "piper"

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ConfigError("'model' must be a name such as sonnet, opus or haiku.")
        if not self.tts_engines:
            raise ConfigError("'tts_engines' needs at least one voice engine, e.g. [kokoro, piper, say].")
        for e in self.tts_engines:
            if e not in ALLOWED_TTS:
                raise ConfigError(f"'{e}' is not a voice engine I know. Use kokoro, piper or say.")
        if not 0.0 < float(self.wake_threshold) < 1.0:
            raise ConfigError("'wake_threshold' must be a number between 0 and 1, for example 0.5.")
        if float(self.follow_up_seconds) < 0:
            raise ConfigError("'follow_up_seconds' can't be negative. Use 0 to turn follow-ups off.")
        if not 100 <= int(self.end_of_speech_silence_ms) <= 5000:
            raise ConfigError("'end_of_speech_silence_ms' should be between 100 and 5000.")
        if self.kokoro_device not in ("auto", "cpu", "mps"):
            raise ConfigError("'kokoro_device' must be auto, cpu or mps.")
        if not 0.5 <= float(self.speech_speed) <= 2.0:
            raise ConfigError("'speech_speed' should be between 0.5 and 2.0.")
        if float(self.listen_timeout_seconds) <= 0 or float(self.max_request_seconds) <= 0:
            raise ConfigError("Listening times must be more than zero seconds.")


def load_config(path: Path | str | None = None) -> Config:
    """Read config.yaml. Missing settings use the defaults above."""
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: dict[str, Any] = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(
                f"I couldn't read {path.name}. A line is probably mis-typed (check spaces and colons). Details: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path.name} should be a list of 'setting: value' lines.")
    known = {f.name: f for f in fields(Config)}
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ConfigError(f"{path.name} has settings I don't recognise: {', '.join(unknown)}. Check the spelling.")
    cfg = Config(**data)
    cfg.validate()
    return cfg
