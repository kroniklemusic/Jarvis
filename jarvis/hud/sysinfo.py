"""Live readings about this Mac for the HUD: processor, memory, battery, network and disk.

Uses only commands that come with macOS (ps, sysctl, vm_stat, pmset, netstat). Every function
returns None if a reading isn't available, so the HUD simply shows a dash.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Any, Callable

Runner = Callable[..., subprocess.CompletedProcess]


def _out(run: Runner, *cmd: str) -> str | None:
    try:
        result = run(list(cmd), capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return result.stdout if result.returncode == 0 else None


def cpu_percent(run: Runner = subprocess.run, cores: int | None = None) -> float | None:
    """How busy the processor is, 0-100 (every program's CPU added up, divided by the cores)."""
    out = _out(run, "ps", "-A", "-o", "%cpu=")
    if out is None:
        return None
    total = 0.0
    for value in out.split():
        try:
            total += float(value.replace(",", "."))
        except ValueError:
            continue
    cores = cores or os.cpu_count() or 1
    return round(min(100.0, total / cores), 1)


def memory(run: Runner = subprocess.run) -> dict[str, float] | None:
    """Memory in use (apps + system + compressed), like Activity Monitor's "Memory Used"."""
    size = _out(run, "sysctl", "-n", "hw.memsize")
    stats = _out(run, "vm_stat")
    if not size or not stats:
        return None
    try:
        total = int(size.strip())
    except ValueError:
        return None
    m = re.search(r"page size of (\d+) bytes", stats)
    page = int(m.group(1)) if m else 4096

    def pages(label: str) -> int:
        found = re.search(rf"^{re.escape(label)}:\s+(\d+)", stats, re.MULTILINE)
        return int(found.group(1)) if found else 0

    used = (pages("Pages active") + pages("Pages wired down") + pages("Pages occupied by compressor")) * page
    gb = 1024 ** 3
    return {"used_gb": round(used / gb, 1), "total_gb": round(total / gb, 1),
            "fraction": round(min(1.0, used / total), 3) if total else 0.0}


def battery(run: Runner = subprocess.run) -> dict[str, Any] | None:
    """Battery charge and whether it's charging. None on Macs without a battery."""
    out = _out(run, "pmset", "-g", "batt")
    if not out:
        return None
    m = re.search(r"(\d+)%;\s*([^;]+);", out)
    if not m:
        return None
    state = m.group(2).strip().lower()
    return {"percent": int(m.group(1)), "charging": state in ("charging", "charged", "finishing charge"),
            "on_power": "ac power" in out.lower()}


def network_bytes(run: Runner = subprocess.run) -> tuple[int, int] | None:
    """Total bytes received and sent on Wi-Fi/Ethernet since the Mac started."""
    out = _out(run, "netstat", "-ib", "-n")
    if not out:
        return None
    lines = out.strip().splitlines()
    if not lines:
        return None
    header = lines[0].split()
    try:
        i_in, i_out = header.index("Ibytes"), header.index("Obytes")
    except ValueError:
        return None
    seen: dict[str, tuple[int, int]] = {}
    for line in lines[1:]:
        cols = line.split()
        if not cols or not cols[0].startswith("en") or "<Link" not in line:
            continue
        # The link-level row has no address column, so count from the right-hand end.
        from_end_in, from_end_out = len(header) - i_in, len(header) - i_out
        try:
            seen[cols[0]] = (int(cols[-from_end_in]), int(cols[-from_end_out]))
        except (ValueError, IndexError):
            continue
    if not seen:
        return None
    return sum(v[0] for v in seen.values()), sum(v[1] for v in seen.values())


def disk(path: str = "/") -> dict[str, float] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    gb = 1000 ** 3     # Finder counts disk space in 1000s
    return {"free_gb": round(usage.free / gb, 1), "total_gb": round(usage.total / gb, 1),
            "fraction": round(usage.used / usage.total, 3) if usage.total else 0.0}


class NetworkRate:
    """Turns the byte counters into download/upload speeds (bytes per second)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self._last: tuple[float, int, int] | None = None

    def update(self, counters: tuple[int, int] | None) -> dict[str, float] | None:
        if counters is None:
            return None
        now = self.clock()
        last, self._last = self._last, (now, counters[0], counters[1])
        if last is None or now <= last[0]:
            return {"down_bps": 0.0, "up_bps": 0.0}
        dt = now - last[0]
        return {"down_bps": round(max(0, counters[0] - last[1]) / dt, 1),
                "up_bps": round(max(0, counters[1] - last[2]) / dt, 1)}
