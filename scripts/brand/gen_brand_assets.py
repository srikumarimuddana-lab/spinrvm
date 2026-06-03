#!/usr/bin/env python3
"""Generate Spinr brand raster assets (app icon, adaptive icon, favicon, splash mark).

Pure standard-library only (zlib + struct) so it runs anywhere with no pip install.
The Spinr mark is concentric red rings + a center dot (a stylised "spinr" record /
radar). It is pure geometry, so we render it with analytic anti-aliasing in a single
pass — crisp at any resolution.

Usage:
    python3 scripts/brand/gen_brand_assets.py [out_dir]

Re-run after tweaking RED / proportions to regenerate every size.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

# --- Brand tokens -----------------------------------------------------------
RED = (0xE8, 0x47, 0x3C)      # spinr coral-red (sampled from the wordmark mark)
INK = (0x2D, 0x30, 0x38)      # charcoal (wordmark / i-dot)
WHITE = (0xFF, 0xFF, 0xFF)

# Ring geometry as fractions of the mark radius R.
# (radius_fraction, stroke_half_fraction) outer -> inner.
RINGS = [
    (1.00, 0.115),
    (0.62, 0.115),
    (0.26, 0.130),  # innermost slightly thicker
]
CENTER_DOT = 0.0          # filled center handled by innermost ring's radius<half check
IDOT_RADIUS = 0.150       # the "i" tittle above the mark
IDOT_GAP = 0.32           # gap between top ring and the i-dot (fraction of R)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _smoothstep(e0: float, e1: float, x: float) -> float:
    if e0 == e1:
        return 0.0 if x < e0 else 1.0
    t = _clamp((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def _write_png(path: Path, w: int, h: int, rgba: bytearray) -> None:
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)  # filter type 0 (None)
        raw.extend(rgba[y * stride : (y + 1) * stride])
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _over(dst: tuple[float, float, float, float], src_rgb, src_a):
    """Source-over compositing. dst/src premultiplied-free, alpha in [0,1]."""
    da = dst[3]
    oa = src_a + da * (1.0 - src_a)
    if oa <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    out = []
    for i in range(3):
        c = (src_rgb[i] / 255.0) * src_a + (dst[i] / 255.0) * da * (1.0 - src_a)
        out.append((c / oa) * 255.0)
    return (out[0], out[1], out[2], oa)


# --- Spiral geometry (the "spinr" record/swirl) -----------------------------
TURNS = 4.0           # tightly-wound revolutions (vinyl-record look)
INNER_FRAC = 0.0      # spiral runs all the way in (center filled by CORE dot)
BAND_FRAC = 0.27      # stroke half-width as a fraction of the turn spacing
CORE_DOT = 0.12       # solid red center dot, fraction of mark radius R


def _atan2_pos(y: float, x: float) -> float:
    import math

    a = math.atan2(y, x)
    return a + 2.0 * math.pi if a < 0 else a


def render_mark(size: int, *, bg, pad: float, with_idot: bool = False) -> bytearray:
    """Render the Spinr spiral mark centered in a size×size RGBA buffer.

    Uses the radial-distance form of an Archimedean spiral (r = b·θ) so the
    whole mark rasterises in a single O(pixels) pass with analytic AA — crisp,
    fast, and a faithful swirl rather than a bullseye.

    bg:  (r,g,b,a) background (a=0/None for transparent).
    pad: fraction of half-size reserved as empty margin around the mark.
    """
    import math

    if bg is None:
        bg = (0, 0, 0, 0.0)
    bg_rgba = (bg[0], bg[1], bg[2], bg[3] / 255.0 if bg[3] > 1 else bg[3])

    cx = cy = (size - 1) / 2.0
    R = (size / 2.0) * (1.0 - pad)
    aa = max(0.9, size / 600.0)  # AA softness in px, scales with resolution

    theta1 = 2.0 * math.pi * TURNS          # outer angle
    theta0 = 2.0 * math.pi * INNER_FRAC     # inner (start) angle
    b = R / theta1                          # spiral growth: r = b·θ
    spacing = 2.0 * math.pi * b             # radial gap between successive turns
    hw = BAND_FRAC * spacing                # stroke half-width

    # endpoints (for rounded caps)
    in_x, in_y = cx + b * theta0 * math.cos(theta0), cy + b * theta0 * math.sin(theta0)
    out_x, out_y = cx + b * theta1 * math.cos(theta1), cy + b * theta1 * math.sin(theta1)

    buf = bytearray(size * size * 4)
    two_pi = 2.0 * math.pi
    for y in range(size):
        dy = y - cy
        row = y * size * 4
        for x in range(size):
            dx = x - cx
            rho = math.sqrt(dx * dx + dy * dy)

            cov = 0.0
            if rho <= R + hw + aa:
                phi = _atan2_pos(dy, dx)
                # nearest spiral arm at this angle: theta = phi + 2*pi*k
                k = round((rho / b - phi) / two_pi)
                theta = phi + two_pi * k
                if theta < theta0:
                    d = math.sqrt((x - in_x) ** 2 + (y - in_y) ** 2)   # inner cap
                elif theta > theta1:
                    d = math.sqrt((x - out_x) ** 2 + (y - out_y) ** 2)  # outer cap
                else:
                    d = abs(rho - b * theta)                            # radial distance
                cov = 1.0 - _smoothstep(hw - aa, hw + aa, d)

            # solid red center dot (the spiral's eye)
            core = CORE_DOT * R
            ccov = 1.0 - _smoothstep(core - aa, core + aa, rho)
            if ccov > cov:
                cov = ccov

            px = list(bg_rgba)
            if cov > 0.0:
                px = list(_over(tuple(px), RED, cov))

            # optional "i" tittle above the mark (charcoal) — used by the wordmark lockup
            if with_idot:
                idot_r = IDOT_RADIUS * R
                idot_cy = cy - (R + IDOT_GAP * R)
                dd = math.sqrt((x - cx) ** 2 + (y - idot_cy) ** 2)
                icov = 1.0 - _smoothstep(idot_r - aa, idot_r + aa, dd)
                if icov > 0.0:
                    px = list(_over(tuple(px), INK, icov))

            r, g, bl, a = px
            o = row + x * 4
            buf[o] = int(_clamp(r, 0, 255))
            buf[o + 1] = int(_clamp(g, 0, 255))
            buf[o + 2] = int(_clamp(bl, 0, 255))
            buf[o + 3] = int(_clamp(a * 255.0, 0, 255))
    return buf


def spiral_svg(view: int = 100, pad: float = 0.12) -> str:
    """Emit the Spinr spiral mark as a scalable SVG (same geometry as the PNGs).

    Ideal for web favicons (crisp at every size, tiny file). Next.js App Router
    auto-uses an `icon.svg` placed in the app directory.
    """
    import math

    cx = cy = view / 2.0
    R = (view / 2.0) * (1.0 - pad)
    theta1 = 2.0 * math.pi * TURNS
    b = R / theta1
    hw = BAND_FRAC * (2.0 * math.pi * b)
    red = "#%02X%02X%02X" % RED

    n = 480
    pts = []
    for i in range(n + 1):
        th = theta1 * i / n
        r = b * th
        pts.append((cx + r * math.cos(th), cy + r * math.sin(th)))
    d = "M " + " L ".join(f"{x:.2f} {y:.2f}" for x, y in pts)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {view} {view}" fill="none">'
        f'<path d="{d}" stroke="{red}" stroke-width="{2.0 * hw:.2f}" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{CORE_DOT * R:.2f}" fill="{red}"/>'
        f"</svg>\n"
    )


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/brand")
    out.mkdir(parents=True, exist_ok=True)

    jobs = [
        # name, size, bg, pad, with_idot
        ("icon.png", 1024, (*WHITE, 255), 0.18, False),           # iOS/store icon (opaque)
        ("adaptive-icon.png", 1024, None, 0.30, False),           # Android fg (transparent, safe zone)
        ("favicon.png", 196, (*WHITE, 255), 0.14, False),         # web favicon
        ("splash-mark.png", 512, None, 0.26, False),              # native splash (transparent on white)
    ]
    for name, size, bg, pad, with_idot in jobs:
        buf = render_mark(size, bg=bg, pad=pad, with_idot=with_idot)
        _write_png(out / name, size, size, buf)
        print(f"wrote {out / name} ({size}x{size})")

    (out / "mark.svg").write_text(spiral_svg(), encoding="utf-8")
    print(f"wrote {out / 'mark.svg'} (vector)")


if __name__ == "__main__":
    main()
