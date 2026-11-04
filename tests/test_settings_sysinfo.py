import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis import settings
from jarvis.config import ConfigError, load_config
from jarvis.hud import sysinfo

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------- settings
def test_sanitize_keeps_only_valid_editable_settings():
    raw = {"model": "opus", "kokoro_voice": "bf_emma", "speech_speed": 9, "wake_threshold": "0.5",
           "show_timings": False, "follow_up_seconds": -3, "claude_command": "something-else", "model2": "x"}
    assert settings.sanitize(raw) == {"model": "opus", "kokoro_voice": "bf_emma", "speech_speed": 1.5,
                                      "show_timings": False, "follow_up_seconds": 0.0}
    assert settings.sanitize({"model": "gpt-5"}) == {}
    assert settings.sanitize({"show_timings": 1}) == {}          # must be a real true/false
    assert settings.sanitize("not a dict") == {}
    assert settings.sanitize({"speech_speed": float("nan")}) == {}


def test_save_changes_values_and_keeps_comments(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.yaml", cfg_file)
    before = cfg_file.read_text()
    settings.save({"model": "opus", "speech_speed": 1.15, "show_timings": False}, cfg_file)
    after = cfg_file.read_text()
    assert "model: opus" in after and "speech_speed: 1.15" in after and "show_timings: false" in after
    comments = [line for line in before.splitlines() if line.startswith("#")]
    assert all(c in after for c in comments)                      # every note is kept
    cfg = load_config(cfg_file)
    assert (cfg.model, cfg.speech_speed, cfg.show_timings) == ("opus", 1.15, False)
    assert len(after.splitlines()) == len(before.splitlines())   # changed in place, nothing added


def test_save_adds_missing_settings_and_never_breaks_the_file(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("# my settings\nmodel: sonnet\n")
    settings.save({"wake_threshold": 0.4}, cfg_file)
    assert load_config(cfg_file).wake_threshold == 0.4
    original = cfg_file.read_text()

    def broken(path):
        raise ConfigError("bad")
    monkeypatch.setattr(settings, "load_config", broken)
    with pytest.raises(ConfigError):
        settings.save({"model": "opus"}, cfg_file)
    assert cfg_file.read_text() == original                      # untouched after a failed check
    assert [p.name for p in tmp_path.iterdir()] == ["config.yaml"]  # no temporary files left


def test_options_and_current():
    opts = settings.options()
    assert {m["value"] for m in opts["models"]} == {"haiku", "sonnet", "opus"}
    assert any(v["value"] == "bm_george" for v in opts["voices"])
    cur = settings.current(load_config(ROOT / "config.yaml"))
    assert set(cur) == settings.EDITABLE


# -------------------------------------------------------------------- sysinfo
def runner(outputs):
    def run(cmd, **kw):
        out = outputs.get(cmd[0])
        if out is None:
            raise FileNotFoundError(cmd[0])
        return subprocess.CompletedProcess(cmd, 0, out, "")
    return run


VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12345.
Pages active:                            200000.
Pages inactive:                          190000.
Pages wired down:                         80000.
Pages stored in compressor:              300000.
Pages occupied by compressor:            100000.
"""

NETSTAT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                        123456     0   45678901   123456     0   45678901     0
en0        1500  <Link#6>    aa:bb:cc:dd:ee:ff  9876543     0 12345678901  5432109     0  987654321     0
en0        1500  192.168.1     192.168.1.20     9876543     - 12345678901  5432109     -  987654321     -
en1        1500  <Link#7>    11:22:33:44:55:66      100     0       5000       50     0       2000     0
"""


def test_cpu_percent():
    assert sysinfo.cpu_percent(runner({"ps": "  0.0\n 12.5\n  3.1\n"}), cores=4) == 3.9
    assert sysinfo.cpu_percent(runner({"ps": "400\n"}), cores=2) == 100.0
    assert sysinfo.cpu_percent(runner({})) is None


def test_memory():
    m = sysinfo.memory(runner({"sysctl": "8589934592\n", "vm_stat": VM_STAT}))
    assert m == {"used_gb": 5.8, "total_gb": 8.0, "fraction": 0.725}
    assert sysinfo.memory(runner({"sysctl": "8589934592\n"})) is None


def test_battery():
    laptop = ("Now drawing from 'Battery Power'\n"
              " -InternalBattery-0 (id=123)\t85%; discharging; 4:20 remaining present: true\n")
    assert sysinfo.battery(runner({"pmset": laptop})) == {"percent": 85, "charging": False, "on_power": False}
    charging = ("Now drawing from 'AC Power'\n"
                " -InternalBattery-0 (id=123)\t40%; charging; 1:10 remaining present: true\n")
    assert sysinfo.battery(runner({"pmset": charging})) == {"percent": 40, "charging": True, "on_power": True}
    assert sysinfo.battery(runner({"pmset": "Now drawing from 'AC Power'\n"})) is None   # desktop Mac


def test_network_bytes_and_rate():
    assert sysinfo.network_bytes(runner({"netstat": NETSTAT})) == (12345678901 + 5000, 987654321 + 2000)
    assert sysinfo.network_bytes(runner({"netstat": "garbage"})) is None
    ticks = iter([0.0, 2.0])
    rate = sysinfo.NetworkRate(clock=lambda: next(ticks))
    assert rate.update((1000, 500)) == {"down_bps": 0.0, "up_bps": 0.0}
    assert rate.update((5000, 1500)) == {"down_bps": 2000.0, "up_bps": 500.0}
    assert rate.update(None) is None


def test_disk():
    d = sysinfo.disk("/")
    assert d["total_gb"] > 0 and 0 <= d["fraction"] <= 1
    assert sysinfo.disk("/definitely/not/here") is None
