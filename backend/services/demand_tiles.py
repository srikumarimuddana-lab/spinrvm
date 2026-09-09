"""Rasterise privacy-suppressed demand aggregates into transparent XYZ tiles.

This is the piece that lets every driver surface show the *same* heat. Android
already runs a real density spreader natively (Google's ``HeatmapTileProvider``);
iOS runs Apple Maps and Android Auto runs its own renderer, and neither has one.
Stacking translucent circles per cell cannot substitute, because the two
operations are not the same:

    kernel density:     d(p) = sum_i w_i * k(|p - c_i|)      linear, then scaled
    alpha compositing:  a(p) = 1 - prod_i (1 - a_i)          saturating

Under compositing, two cells of weight 1 and one cell of weight 2 never look
alike; under a real kernel they are identical. So the summing has to happen once,
here, and ship as pixels.

Pipeline, in the order that matters:

    1. spread     every cell through a Gaussian kernel (sigma in METRES, so the
                  footprint is geographic and stays put as the client zooms)
    2. sum        overlapping kernels add into one density field
    3. normalise  against the snapshot's ABSOLUTE scale, never the tile's own
                  maximum -- a per-tile maximum would make the same colour mean
                  different things on adjacent tiles and while panning
    4. colourise  one lookup per pixel in a table carrying alpha as well as hue,
                  anchored at fully transparent so the field fades to bare map

Only aggregates that already cleared the k-anonymity floor may be passed in.
Nothing here re-checks that; suppression happens upstream, before smoothing,
because blurring below-threshold cells would launder them back into the output.

Layout note: the geometry, selection and colour-table helpers are deliberately
stdlib-only and independently exported. They hold the logic where the real bugs
live -- projection, tile seams, scale, alpha ceiling -- so they stay testable
without numpy present. numpy and Pillow are used only for the accumulate and
encode step at the bottom.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

# ---------------------------------------------------------------------------
# Contract constants
# ---------------------------------------------------------------------------

TILE_SIZE = 256

# Native zoom band we actually rasterise. Below MIN the view is too coarse for
# the detail to mean anything; above MAX the client scales these same tiles
# rather than us pretending to hold finer source data than we do.
MIN_NATIVE_ZOOM = 10
MAX_NATIVE_ZOOM = 16

# Hard ceiling used to validate `z` BEFORE anything computes 2**z. Without this
# a hostile or buggy z turns the world-size arithmetic into an enormous integer.
MAX_ZOOM = 22

EARTH_CIRCUMFERENCE_M = 40_075_016.686
# Metres per pixel at zoom 0 on the equator for 256 px tiles.
_MPP_ZOOM_0 = EARTH_CIRCUMFERENCE_M / TILE_SIZE

# Kernel geometry, in metres so it is a real-world footprint rather than a
# screen-space blur. Support is 3 sigma: beyond that a Gaussian contributes
# under 1.2% of its peak, which is below one step of an 8-bit alpha channel.
KERNEL_SIGMA_M = 180.0
KERNEL_SUPPORT_M = 3.0 * KERNEL_SIGMA_M

# Floor on the kernel in screen pixels.
#
# A metre-denominated kernel keeps the heat geographically fixed, which is what
# we want -- a busy block stays the same size on the ground as the driver zooms.
# But at the bottom of the native band it stops being renderable: at z=10 near
# 52 deg N one pixel covers ~94 m, so the 180 m sigma spans under 2 px. That is
# below what the raster can resolve, and it renders as aliased specks rather
# than a field.
#
# So the metre kernel governs wherever it is actually resolvable, and below that
# the pixel floor takes over. This is not a fudge: at that zoom the display
# genuinely cannot carry 180 m of detail, and over-smoothing is the honest
# rendering of data you cannot resolve. The support:sigma ratio is preserved
# when the floor binds, so the 3-sigma cut still holds.
MIN_SIGMA_PX = 6.0

# Ceiling on the alpha any pixel may reach. From the design spec in the driver
# heatmap plan. Reading the Uber reference screenshot suggests the real target
# sits a little higher (~0.42); this stays at the documented value because the
# more translucent choice is the safe one -- the base map, its street labels and
# its route lines all have to stay readable through the overlay.
MAX_ALPHA = 0.32

# Upper bound on cells in one snapshot. A larger aggregate must be coarsened
# (privacy-safely, recorded in the geometry version), never randomly truncated:
# dropping an arbitrary subset silently understates demand somewhere.
MAX_CELLS = 2000

COLOR_LUT_SIZE = 256


class TileRequestError(ValueError):
    """Tile coordinates outside what this service will rasterise."""


# ---------------------------------------------------------------------------
# Web Mercator geometry (stdlib only)
# ---------------------------------------------------------------------------


def validate_tile(z: int, x: int, y: int) -> None:
    """Reject a tile address before any ``2**z`` arithmetic runs."""
    for name, value in (("z", z), ("x", x), ("y", y)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TileRequestError(f"{name} must be an int, got {type(value).__name__}")
    if z < 0 or z > MAX_ZOOM:
        raise TileRequestError(f"z out of range: {z} (0..{MAX_ZOOM})")
    span = 1 << z
    if not (0 <= x < span):
        raise TileRequestError(f"x out of range for z={z}: {x} (0..{span - 1})")
    if not (0 <= y < span):
        raise TileRequestError(f"y out of range for z={z}: {y} (0..{span - 1})")


def meters_per_pixel(zoom: int, latitude: float) -> float:
    """Ground resolution of one pixel, which shrinks with cos(latitude).

    Used to turn the metre-denominated kernel into pixels. Saskatchewan sits
    near 52 deg N, where a pixel covers roughly 62% of its equatorial ground
    distance -- ignoring the cosine would make every blob ~1.6x too wide here.
    """
    return _MPP_ZOOM_0 * math.cos(math.radians(latitude)) / (1 << zoom)


def lnglat_to_world_px(lat: float, lng: float, zoom: int) -> Tuple[float, float]:
    """Project to absolute world pixels at ``zoom``.

    Latitude is clamped to the Web Mercator limit; the projection diverges at
    the poles and an unclamped value would produce an infinite y.
    """
    world = float(TILE_SIZE * (1 << zoom))
    x = (lng + 180.0) / 360.0 * world
    clamped = max(-85.05112878, min(85.05112878, lat))
    sin_lat = math.sin(math.radians(clamped))
    y = (0.5 - math.log((1.0 + sin_lat) / (1.0 - sin_lat)) / (4.0 * math.pi)) * world
    return x, y


def tile_origin_px(z: int, x: int, y: int) -> Tuple[float, float]:
    """Top-left corner of a tile, in absolute world pixels."""
    return float(x * TILE_SIZE), float(y * TILE_SIZE)


def tile_center_latlng(z: int, x: int, y: int) -> Tuple[float, float]:
    """Centre of a tile, for picking the latitude that scales the kernel."""
    world = float(TILE_SIZE * (1 << z))
    cx = (x + 0.5) * TILE_SIZE
    cy = (y + 0.5) * TILE_SIZE
    lng = cx / world * 360.0 - 180.0
    n = math.pi - 2.0 * math.pi * cy / world
    lat = math.degrees(math.atan(math.sinh(n)))
    return lat, lng


def kernel_px(sigma_m: float, support_m: float, mpp: float) -> Tuple[float, float]:
    """Kernel sigma and support in pixels, with the low-zoom floor applied.

    Returned together because they must stay consistent: if the floor lifts
    sigma, support has to scale by the same factor or the 3-sigma cut would
    slice into the visible part of the kernel and re-introduce a hard edge.
    """
    if mpp <= 0 or not math.isfinite(mpp):
        raise TileRequestError(f"mpp must be finite and positive, got {mpp}")
    if sigma_m <= 0 or not math.isfinite(sigma_m):
        raise TileRequestError(f"sigma_m must be finite and positive, got {sigma_m}")
    ratio = support_m / sigma_m
    sigma = max(sigma_m / mpp, MIN_SIGMA_PX)
    return sigma, sigma * ratio


def tile_local_cells(
    cells: Iterable[Tuple[float, float, float]],
    *,
    z: int,
    x: int,
    y: int,
    support_px: float,
) -> List[Tuple[float, float, float]]:
    """Cells that can touch this tile, in tile-local pixel coordinates.

    The halo is what keeps tile edges seamless. Selecting only cells whose
    centre lands *inside* the tile would drop every kernel straddling the
    boundary, and the missing half-blobs show up as visible grid lines exactly
    where two tiles meet. So the window is widened by the kernel's support and
    the returned coordinates are allowed to fall outside 0..TILE_SIZE; the
    accumulator clips their contribution, which is not the same as discarding it.

    Non-finite and non-positive-weight cells are dropped here rather than left
    to poison the accumulator with NaN.
    """
    if support_px < 0:
        raise TileRequestError("support_px must be non-negative")
    ox, oy = tile_origin_px(z, x, y)
    lo = -support_px
    hi = TILE_SIZE + support_px
    out: List[Tuple[float, float, float]] = []
    for lat, lng, weight in cells:
        if not (math.isfinite(lat) and math.isfinite(lng) and math.isfinite(weight)):
            continue
        if weight <= 0.0:
            continue
        wx, wy = lnglat_to_world_px(lat, lng, z)
        px = wx - ox
        py = wy - oy
        if lo <= px <= hi and lo <= py <= hi:
            out.append((px, py, float(weight)))
    return out


# ---------------------------------------------------------------------------
# Colour lookup table (stdlib only)
# ---------------------------------------------------------------------------


def _hex_to_rgb(value: str) -> Tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"expected #RRGGBB, got {value!r}")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def build_color_lut(
    ramp: Sequence[str],
    *,
    size: int = COLOR_LUT_SIZE,
    max_alpha: float = MAX_ALPHA,
) -> List[Tuple[int, int, int, int]]:
    """Premultiply-free RGBA table indexed by normalised density.

    Alpha is part of this table, not applied afterwards, and it starts at zero.
    That is the difference between a field that trails off into bare map and one
    that ends at a visible disc edge: if the lowest entry were opaque, every
    pixel the kernel touched at all would be painted solid.

    Hue interpolates across the ramp so the table is smooth at 8-bit precision
    rather than banding into the ramp's handful of stops.
    """
    if size < 2:
        raise ValueError("LUT needs at least 2 entries")
    if not ramp:
        raise ValueError("ramp must not be empty")
    if not (0.0 < max_alpha <= 1.0):
        raise ValueError(f"max_alpha must be in (0, 1], got {max_alpha}")

    stops = [_hex_to_rgb(c) for c in ramp]
    last = len(stops) - 1
    table: List[Tuple[int, int, int, int]] = []
    for i in range(size):
        t = i / (size - 1)
        if last == 0:
            r, g, b = stops[0]
        else:
            pos = t * last
            lo = min(int(pos), last - 1)
            frac = pos - lo
            r0, g0, b0 = stops[lo]
            r1, g1, b1 = stops[lo + 1]
            r = round(r0 + (r1 - r0) * frac)
            g = round(g0 + (g1 - g0) * frac)
            b = round(b0 + (b1 - b0) * frac)
        table.append((r, g, b, round(255 * max_alpha * t)))
    return table


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TileSnapshot:
    """One immutable, already-suppressed aggregate ready to rasterise.

    ``scale`` is the absolute density mapping to the top of the ramp. It belongs
    to the snapshot, is published with a version, and is identical for every
    tile -- that is the whole point. Normalising per tile (or per viewport, as
    the current mobile renderers do) makes a lone weak cell render as darkest
    and changes what a colour means as the driver pans.
    """

    cells: Tuple[Tuple[float, float, float], ...]
    scale: float
    palette: Tuple[str, ...]
    sigma_m: float = KERNEL_SIGMA_M
    support_m: float = KERNEL_SUPPORT_M
    max_alpha: float = MAX_ALPHA
    scale_version: str = "v1"

    def __post_init__(self) -> None:
        if self.scale <= 0 or not math.isfinite(self.scale):
            raise ValueError(f"scale must be finite and positive, got {self.scale}")
        if len(self.cells) > MAX_CELLS:
            raise ValueError(
                f"{len(self.cells)} cells exceeds MAX_CELLS={MAX_CELLS}; coarsen "
                "the aggregate rather than truncating it"
            )
        if self.sigma_m <= 0 or not math.isfinite(self.sigma_m):
            raise ValueError(f"sigma_m must be finite and positive, got {self.sigma_m}")
        if self.support_m < self.sigma_m:
            raise ValueError("support_m must be at least sigma_m")
        if not (0.0 < self.max_alpha <= 1.0):
            raise ValueError(f"max_alpha must be in (0, 1], got {self.max_alpha}")
        if not self.palette:
            raise ValueError("palette must not be empty")


def blank_tile_png() -> bytes:
    """A fully transparent tile.

    An empty result is a normal outcome -- a quiet suburb, or every cell in view
    suppressed for privacy -- and must render as clean map, never as an error.
    """
    from PIL import Image  # local: keeps the stdlib helpers importable without Pillow

    buf = io.BytesIO()
    Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def render_demand_tile(snapshot: TileSnapshot, *, z: int, x: int, y: int) -> bytes:
    """Rasterise one 256x256 RGBA PNG tile from a suppressed aggregate.

    Deterministic: the same snapshot and address always produce the same pixels,
    which is what lets the bytes be cached under an immutable key.
    """
    import numpy as np
    from PIL import Image

    validate_tile(z, x, y)

    lat, _lng = tile_center_latlng(z, x, y)
    mpp = meters_per_pixel(z, lat)
    if mpp <= 0:
        return blank_tile_png()

    sigma_px, support_px = kernel_px(snapshot.sigma_m, snapshot.support_m, mpp)
    local = tile_local_cells(snapshot.cells, z=z, x=x, y=y, support_px=support_px)
    if not local:
        return blank_tile_png()

    acc = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32)
    two_sigma_sq = 2.0 * sigma_px * sigma_px
    support_sq = support_px * support_px

    for px, py, weight in local:
        # Only the patch this kernel can reach, clipped to the tile. A cell in
        # the halo contributes its overlapping sliver and nothing more.
        x0 = max(0, int(math.floor(px - support_px)))
        x1 = min(TILE_SIZE, int(math.ceil(px + support_px)) + 1)
        y0 = max(0, int(math.floor(py - support_px)))
        y1 = min(TILE_SIZE, int(math.ceil(py + support_px)) + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        dx = (np.arange(x0, x1, dtype=np.float32) + 0.5) - px
        dy = (np.arange(y0, y1, dtype=np.float32) + 0.5) - py
        d2 = dy[:, None] ** 2 + dx[None, :] ** 2
        patch = np.exp(-d2 / two_sigma_sq, dtype=np.float32)
        patch *= d2 <= support_sq  # hard cut at support, so no faint infinite tail
        acc[y0:y1, x0:x1] += weight * patch

    # Absolute scale, then clip. Clipping is what stops a dense cluster
    # saturating to flat opaque red with no structure left in it.
    norm = np.clip(acc / np.float32(snapshot.scale), 0.0, 1.0)
    lut = np.array(
        build_color_lut(snapshot.palette, size=COLOR_LUT_SIZE, max_alpha=snapshot.max_alpha),
        dtype=np.uint8,
    )
    idx = np.rint(norm * (COLOR_LUT_SIZE - 1)).astype(np.intp)
    rgba = lut[idx]

    buf = io.BytesIO()
    Image.fromarray(rgba, mode="RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
