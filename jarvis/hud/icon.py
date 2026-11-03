"""Draws the JARVIS app icon (an arc reactor) as a PNG, using only numpy.

    python -m jarvis.hud.icon OUTPUT.png [SIZE]

The build script turns it into the .icns file macOS uses for the app.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

CYAN = np.array([0.27, 0.89, 1.0])
WHITE = np.array([1.0, 1.0, 1.0])


def _band(d: np.ndarray, half_width: float, aa: float) -> np.ndarray:
    """1 inside a band |d| < half_width, with a soft anti-aliased edge."""
    return np.clip((half_width - np.abs(d)) / aa + 0.5, 0.0, 1.0)


def render(size: int = 1024) -> np.ndarray:
    """Returns an RGBA float image (size x size x 4) with values 0..1."""
    s = size
    aa = 1.2 / s * 2                                  # one pixel, in the -1..1 coordinate space
    ys, xs = np.mgrid[0:s, 0:s].astype(np.float64)
    x = (xs + 0.5) / s * 2 - 1
    y = (ys + 0.5) / s * 2 - 1
    r = np.hypot(x, y)
    th = np.arctan2(y, x)

    # macOS icon shape: a rounded square ("superellipse") filling ~80% of the canvas
    body = 0.805
    n = 5.0
    se = (np.abs(x / body) ** n + np.abs(y / body) ** n) ** (1 / n)
    alpha = np.clip((1 - se) / (aa / body) + 0.5, 0, 1)

    # background: deep navy fading to black, with a faint grid
    bg = np.stack([0.008 + 0.03 * (1 - r), 0.035 + 0.09 * np.clip(1 - r, 0, 1), 0.06 + 0.12 * np.clip(1 - r, 0, 1)], -1)
    grid = (_band((x * 9) % 1 - 0.5, 0.015, 0.02) + _band((y * 9) % 1 - 0.5, 0.015, 0.02)).clip(0, 1)
    rgb = bg + grid[..., None] * 0.035 * CYAN * np.clip(1 - r, 0, 1)[..., None]

    def add(mask: np.ndarray, color: np.ndarray, strength: float = 1.0) -> None:
        nonlocal rgb
        rgb = rgb + mask[..., None] * color * strength

    def ring(radius: float, width: float, glow: float, strength: float = 1.0) -> np.ndarray:
        d = r - radius
        core = _band(d, width / 2, aa)
        halo = np.exp(-(d / glow) ** 2) * 0.32
        return (core + halo) * strength

    # outer ticks + rings
    ticks = _band(((th / (2 * np.pi) * 60) % 1) - 0.5, 0.11, 0.08) * _band(r - 0.70, 0.03, aa)
    add(ticks, CYAN, 0.55)
    add(ring(0.665, 0.012, 0.02, 0.8), CYAN)
    # segmented ring (5 arcs)
    seg = _band(((th / (2 * np.pi) * 5 + 0.1) % 1) - 0.5, 0.36, 0.01)
    add(ring(0.585, 0.035, 0.04) * seg, CYAN)
    # reactor coils (10 blocks)
    coils = _band(((th / (2 * np.pi) * 10) % 1) - 0.5, 0.38, 0.02)
    add(_band(r - 0.43, 0.06, aa) * coils, CYAN, 0.55)
    add(ring(0.495, 0.008, 0.015, 0.7), CYAN)
    add(ring(0.365, 0.008, 0.015, 0.7), CYAN)
    # glowing core
    glow = np.exp(-(r / 0.30) ** 2)
    add(glow, CYAN, 0.6)
    add(np.exp(-(r / 0.11) ** 2), WHITE, 0.9)
    add(ring(0.27, 0.014, 0.03, 0.9), CYAN)
    # triangle (Mark VI reactor)
    k = np.sqrt(3)
    tri_r = 0.17
    px, py = x, -y
    d1 = py + tri_r / 2                               # flat bottom edge
    d2 = (k * px - py + tri_r) / 2
    d3 = (-k * px - py + tri_r) / 2
    sd = -np.minimum(np.minimum(d1, d2), d3)          # signed distance (negative inside)
    add(_band(sd, 0.011, aa), WHITE, 0.85)

    rgb = 1 - np.exp(-rgb * 1.6)                     # soft tone-mapping keeps highlights from clipping
    return np.concatenate([np.clip(rgb, 0, 1), alpha[..., None]], -1)


def write_png(path: Path, rgba: np.ndarray) -> None:
    img = (np.clip(rgba, 0, 1) * 255 + 0.5).astype(np.uint8)
    h, w = img.shape[:2]
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python -m jarvis.hud.icon OUTPUT.png [SIZE]")
        return 2
    size = int(argv[1]) if len(argv) > 1 else 1024
    write_png(Path(argv[0]), render(size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
