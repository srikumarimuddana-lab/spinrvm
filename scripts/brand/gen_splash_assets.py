#!/usr/bin/env python3
"""Generate the Spinr brand-splash asset set for rider-app and driver-app.

The splash animation needs the real wordmark taken apart into layers, so the
bullseye "o" can fly in on its own and land *exactly* where it sits inside the
word. Everything here is derived from committed brand art -- nothing is
redrawn:

  * ``backend/static/branding/spinr_logo.png`` (768x312, the 2x wordmark)
      -> ``wordmark-letters.png``  charcoal letters, the "o" punched out
      -> ``mark.png``              square crop of the red bullseye "o"
  * ``driver-app/assets/images/icon.png`` (1024x1024, opaque white)
      -> ``descriptor-driver.png`` the word "Driver" keyed off white
  * generated analytically
      -> ``glow.png``              radial brand-red halo (alpha only)
      -> ``native-splash.png``     glow + 180deg-rotated mark, the frame the
                                   native splash shows before JS boots

Pure standard library (zlib + struct), same rule as ``gen_brand_assets.py``
next door -- these run in CI containers with no pip install.

Usage:
    python3 scripts/brand/gen_splash_assets.py            # write into both apps
    python3 scripts/brand/gen_splash_assets.py --out /tmp/x  # somewhere else

Re-running is idempotent: same inputs produce byte-identical PNGs.
"""
from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORDMARK_SRC = REPO / "backend/static/branding/spinr_logo.png"
DRIVER_ICON_SRC = REPO / "driver-app/assets/images/icon.png"

# --- Tunables ---------------------------------------------------------------
# Brand red for the halo. shared/theme lightColors.primary.
GLOW_RGB = (0xFF, 0x3B, 0x30)
# Peak alpha at the centre of the halo. Higher = hotter. Mirrors GLOW_PEAK in
# each app's constants/splash.ts -- change both together.
GLOW_PEAK = 0.48
# Falloff exponent; 2.2 gives a soft shoulder that reaches 0 exactly at the rim
# (so Android 12+'s circular icon mask can never clip a visible edge).
GLOW_FALLOFF = 2.2
GLOW_SIZE = 512

# Square crop around the "o", in source (768-wide) pixels. 240px covers the
# bullseye with ~7% margin and renders at 84dp => 2.9x, crisp on 3x screens.
CROP_PX = 240
# Native composite: the halo is 200dp wide and the mark 84dp inside it, so a
# 600px canvas puts the mark at 252px -- a 1.05x resample of the 240px crop.
NATIVE_SIZE = 600
NATIVE_GLOW_DP = 200
NATIVE_MARK_DP = 84

# A pixel counts as ink at all above this alpha; below it we treat it as empty
# rather than trying to classify near-invisible antialiasing.
ALPHA_FLOOR = 8
# Above this alpha we trust the pixel's own colour; between the two we look at
# the neighbourhood, because the source's palette bleeds red into the charcoal
# edges (and vice versa) on every antialiased boundary.
ALPHA_CONFIDENT = 128
NEIGHBOUR_MAX_RADIUS = 5


# --- PNG I/O ----------------------------------------------------------------
def _write_png(path: Path, w: int, h: int, rows) -> None:
    """Write 8-bit RGBA rows (list of list of (r,g,b,a)) as a PNG."""

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0 (None)
        for px in row:
            raw.extend(px)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def _read_png(path: Path):
    """Decode a non-interlaced PNG to (w, h, rows of (r,g,b,a)).

    Handles the colour types our brand art actually uses: 2 (RGB), 3 (palette
    + tRNS) and 6 (RGBA), all 8-bit. Anything else raises rather than guessing.
    """
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    pos, idat, palette, trns = 8, bytearray(), None, None
    width = height = depth = colour = interlace = 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        typ = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if typ == b"IHDR":
            width, height, depth, colour, _, _, interlace = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            palette = [tuple(body[i : i + 3]) for i in range(0, len(body), 3)]
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat += body
    if depth != 8 or interlace != 0 or colour not in (2, 3, 6):
        raise ValueError(f"{path}: unsupported PNG (depth={depth} colour={colour} interlace={interlace})")

    channels = {2: 3, 3: 1, 6: 4}[colour]
    stride = width * channels
    raw = zlib.decompress(bytes(idat))
    prev = bytearray(stride)
    rows = []
    at = 0
    for _ in range(height):
        filt = raw[at]
        at += 1
        line = bytearray(raw[at : at + stride])
        at += stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filt == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        out = []
        for x in range(width):
            if colour == 6:
                out.append(tuple(line[x * 4 : x * 4 + 4]))
            elif colour == 2:
                out.append((line[x * 3], line[x * 3 + 1], line[x * 3 + 2], 255))
            else:
                idx = line[x]
                rgb = palette[idx]
                alpha = trns[idx] if trns is not None and idx < len(trns) else 255
                out.append((rgb[0], rgb[1], rgb[2], alpha))
        rows.append(out)
        prev = line
    return width, height, rows


# --- Helpers ----------------------------------------------------------------
EMPTY = (0, 0, 0, 0)


def _blank(w: int, h: int):
    return [[EMPTY] * w for _ in range(h)]


def _is_red(px) -> bool:
    r, g, b, _ = px
    return r > 150 and r > g + 60 and r > b + 60


def _classify(w: int, h: int, rows):
    """Split every ink pixel into 'r' (the bullseye) or 'c' (the letters).

    Confident pixels vote by colour. Ambiguous edge pixels take the majority of
    the nearest ring of confident neighbours, which keeps the antialiased seam
    between the "p" and the "o" on the correct layer -- the two glyphs overlap
    in x, so no column-based split can separate them.
    """
    seed = [[None] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if rows[y][x][3] >= ALPHA_CONFIDENT:
                seed[y][x] = "r" if _is_red(rows[y][x]) else "c"

    final = [[None] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if rows[y][x][3] < ALPHA_FLOOR:
                continue
            if seed[y][x] is not None:
                final[y][x] = seed[y][x]
                continue
            label = "c"
            for radius in range(1, NEIGHBOUR_MAX_RADIUS + 1):
                reds = chars = 0
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        if max(abs(dx), abs(dy)) != radius:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            near = seed[ny][nx]
                            if near == "r":
                                reds += 1
                            elif near == "c":
                                chars += 1
                if reds or chars:
                    label = "r" if reds > chars else "c"
                    break
            final[y][x] = label
    return final


def _bbox(w: int, h: int, mask, want) -> tuple[int, int, int, int]:
    xs0, xs1, ys0, ys1 = w, -1, h, -1
    for y in range(h):
        for x in range(w):
            if mask[y][x] == want:
                xs0, xs1 = min(xs0, x), max(xs1, x)
                ys0, ys1 = min(ys0, y), max(ys1, y)
    return xs0, xs1, ys0, ys1


def _resample(rows, sw: int, sh: int, dw: int, dh: int):
    """Bilinear resample of straight-alpha RGBA. Only ever a small ratio here."""
    if (sw, sh) == (dw, dh):
        return [row[:] for row in rows]
    out = []
    for y in range(dh):
        sy = (y + 0.5) * sh / dh - 0.5
        y0 = max(0, min(sh - 1, int(math.floor(sy))))
        y1 = max(0, min(sh - 1, y0 + 1))
        fy = max(0.0, min(1.0, sy - y0))
        row = []
        for x in range(dw):
            sx = (x + 0.5) * sw / dw - 0.5
            x0 = max(0, min(sw - 1, int(math.floor(sx))))
            x1 = max(0, min(sw - 1, x0 + 1))
            fx = max(0.0, min(1.0, sx - x0))
            acc = [0.0, 0.0, 0.0, 0.0]
            for (px, py, weight) in (
                (x0, y0, (1 - fx) * (1 - fy)),
                (x1, y0, fx * (1 - fy)),
                (x0, y1, (1 - fx) * fy),
                (x1, y1, fx * fy),
            ):
                sample = rows[py][px]
                alpha = sample[3] / 255.0
                for i in range(3):
                    acc[i] += sample[i] * alpha * weight
                acc[3] += alpha * weight
            if acc[3] <= 0:
                row.append(EMPTY)
            else:
                row.append(
                    (
                        int(round(acc[0] / acc[3])),
                        int(round(acc[1] / acc[3])),
                        int(round(acc[2] / acc[3])),
                        int(round(acc[3] * 255)),
                    )
                )
        out.append(row)
    return out


def _over(dst, src):
    """Straight-alpha source-over compositing of one pixel."""
    sa = src[3] / 255.0
    da = dst[3] / 255.0
    oa = sa + da * (1 - sa)
    if oa <= 0:
        return EMPTY
    return tuple(
        [int(round((src[i] * sa + dst[i] * da * (1 - sa)) / oa)) for i in range(3)]
        + [int(round(oa * 255))]
    )


# --- Layer builders ---------------------------------------------------------
def build_glow(size: int = GLOW_SIZE):
    """Radial brand-red halo. Alpha reaches exactly 0 at the rim."""
    radius = size / 2.0
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            dist = math.hypot(x + 0.5 - radius, y + 0.5 - radius) / radius
            alpha = 0.0 if dist >= 1 else (1 - dist) ** GLOW_FALLOFF
            row.append((*GLOW_RGB, int(round(255 * alpha * GLOW_PEAK))))
        rows.append(row)
    return rows


def build_wordmark_layers():
    """Return (letters_rows, mark_rows, geometry) from the 2x wordmark."""
    w, h, rows = _read_png(WORDMARK_SRC)
    mask = _classify(w, h, rows)

    letters = _blank(w, h)
    for y in range(h):
        for x in range(w):
            if mask[y][x] == "c":
                letters[y][x] = rows[y][x]

    x0, x1, y0, y1 = _bbox(w, h, mask, "r")
    cx, cy = (x0 + x1 + 1) / 2.0, (y0 + y1 + 1) / 2.0
    left, top = int(round(cx - CROP_PX / 2)), int(round(cy - CROP_PX / 2))
    mark = _blank(CROP_PX, CROP_PX)
    for y in range(CROP_PX):
        for x in range(CROP_PX):
            sx, sy = left + x, top + y
            if 0 <= sx < w and 0 <= sy < h and mask[sy][sx] == "r":
                mark[y][x] = rows[sy][sx]

    geometry = {
        "wordmark_w": w,
        "wordmark_h": h,
        "o_center_x_frac": cx / w,
        "o_center_y_frac": cy / h,
        "o_diameter_frac": (x1 - x0 + 1) / w,
        "mark_ink_frac": (x1 - x0 + 1) / CROP_PX,
    }
    return letters, mark, geometry


def build_native_frame(mark_rows):
    """Glow + 180deg-rotated mark: the exact frame JS re-creates at t=0.

    Rotating by half a turn is a lossless point reflection, so the native image
    and the first JS frame are the same pixels -- the mark then unwinds those
    180 degrees on screen, which is why the handoff is invisible.
    """
    canvas = _resample(build_glow(), GLOW_SIZE, GLOW_SIZE, NATIVE_SIZE, NATIVE_SIZE)
    mark_px = int(round(NATIVE_SIZE * NATIVE_MARK_DP / NATIVE_GLOW_DP))
    scaled = _resample(mark_rows, CROP_PX, CROP_PX, mark_px, mark_px)
    rotated = [[scaled[mark_px - 1 - y][mark_px - 1 - x] for x in range(mark_px)] for y in range(mark_px)]
    offset = (NATIVE_SIZE - mark_px) // 2
    for y in range(mark_px):
        for x in range(mark_px):
            src = rotated[y][x]
            if src[3]:
                canvas[offset + y][offset + x] = _over(canvas[offset + y][offset + x], src)
    return canvas


def build_driver_descriptor():
    """Key the word "Driver" off the driver icon's opaque white background.

    The driver app's own ``spinr-logo.png`` is byte-identical to the rider's --
    the "Driver" descriptor only exists inside the app icon, so that is where
    it has to come from. Alpha is recovered from luminance, which keeps the
    glyph edges soft instead of stair-stepped.
    """
    w, h, rows = _read_png(DRIVER_ICON_SRC)

    def inked(px):
        return min(px[:3]) < 200

    bands, start = [], None
    for y in range(h + 1):
        has_ink = y < h and any(inked(rows[y][x]) for x in range(w))
        if has_ink and start is None:
            start = y
        elif not has_ink and start is not None:
            bands.append((start, y - 1))
            start = None
    if len(bands) < 2:
        raise ValueError("driver icon: expected a wordmark band and a 'Driver' band")

    # Last band is the descriptor; everything above it is the wordmark (its
    # i-dot sits in a band of its own, which is why we merge rather than index).
    d_top, d_bottom = bands[-1]
    w_top, w_bottom = bands[0][0], bands[-2][1]

    def span(top, bottom):
        xs = [x for y in range(top, bottom + 1) for x in range(w) if inked(rows[y][x])]
        return min(xs), max(xs)

    wx0, wx1 = span(w_top, w_bottom)
    dx0, dx1 = span(d_top, d_bottom)
    ink = min(
        (rows[y][x] for y in range(d_top, d_bottom + 1) for x in range(dx0, dx1 + 1)),
        key=lambda px: sum(px[:3]),
    )
    ink_lum = sum(ink[:3]) / 3.0

    margin = 8
    out = []
    for y in range(d_top - margin, d_bottom + margin + 1):
        row = []
        for x in range(dx0 - margin, dx1 + margin + 1):
            lum = sum(rows[y][x][:3]) / 3.0
            alpha = max(0.0, min(1.0, (255 - lum) / (255 - ink_lum)))
            row.append((ink[0], ink[1], ink[2], int(round(alpha * 255))))
        out.append(row)

    wordmark_w = wx1 - wx0 + 1
    geometry = {
        "descriptor_w_frac": (dx1 - dx0 + 1) / wordmark_w,
        "descriptor_h_frac": (d_bottom - d_top + 1) / wordmark_w,
        "descriptor_gap_frac": (d_top - w_bottom - 1) / wordmark_w,
        "descriptor_margin_frac": margin / (dx1 - dx0 + 1),
    }
    return out, geometry


# --- Entry point ------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        action="append",
        help="output directory (repeatable). Defaults to both apps' assets/images/splash/.",
    )
    args = parser.parse_args()

    targets = (
        [Path(p) for p in args.out]
        if args.out
        else [
            REPO / "rider-app/assets/images/splash",
            REPO / "driver-app/assets/images/splash",
        ]
    )

    letters, mark, geo = build_wordmark_layers()
    glow = build_glow()
    native = build_native_frame(mark)
    descriptor, dgeo = build_driver_descriptor()

    for out in targets:
        _write_png(out / "wordmark-letters.png", geo["wordmark_w"], geo["wordmark_h"], letters)
        _write_png(out / "mark.png", CROP_PX, CROP_PX, mark)
        _write_png(out / "glow.png", GLOW_SIZE, GLOW_SIZE, glow)
        _write_png(out / "native-splash.png", NATIVE_SIZE, NATIVE_SIZE, native)
        if out.name == "splash" and "driver-app" in str(out):
            _write_png(out / "descriptor-driver.png", len(descriptor[0]), len(descriptor), descriptor)
        print(f"wrote splash assets -> {out}")

    print("\nGeometry (cross-check against constants/splash.ts):")
    for key, value in {**geo, **dgeo}.items():
        print(f"  {key:24} {value:.4f}" if isinstance(value, float) else f"  {key:24} {value}")


if __name__ == "__main__":
    main()
