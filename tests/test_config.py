import pytest

from jarvis.config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config


def test_repo_config_yaml_is_valid():
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert cfg.model == "sonnet"
    assert cfg.follow_up_seconds == 8
    assert cfg.end_of_speech_silence_ms == 700
    assert cfg.tts_engines == ["kokoro", "piper", "say"]


def test_missing_file_uses_defaults(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg == Config()


@pytest.mark.parametrize("text, words", [
    ("wake_threshold: 2", "between 0 and 1"),
    ("tts_engines: [espeak]", "voice engine"),
    ("modle: opus", "don't recognise"),
    ("model: [oops", "couldn't read"),
    ("speech_speed: 9", "speech_speed"),
])
def test_bad_settings_give_plain_english_errors(tmp_path, text, words):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    with pytest.raises(ConfigError, match=words):
        load_config(p)


def test_partial_config_overrides_only_given_values(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("model: opus\nkokoro_voice: bf_emma\n")
    cfg = load_config(p)
    assert (cfg.model, cfg.kokoro_voice, cfg.say_voice) == ("opus", "bf_emma", "Daniel")
