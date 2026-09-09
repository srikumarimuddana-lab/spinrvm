"""Demand tile rasteriser — the render pipeline. Needs numpy and Pillow.

Split from test_demand_tiles.py on purpose: a module-level importorskip here
would otherwise take the stdlib geometry/LUT tests down with it.
"""

from __future__ import annotations

import io
import math

import pytest

from services.demand_tiles import (
    COLOR_LUT_SIZE,
    MAX_ALPHA,
    MAX_ZOOM,
    TILE_SIZE,
    TileRequestError,
    TileSnapshot,
    build_color_lut,
    kernel_px,
    lnglat_to_world_px,
    meters_per_pixel,
    tile_center_latlng,
    tile_local_cells,
    tile_origin_px,
)

pytestmark = pytest.mark.unit

np = pytest.importorskip("numpy", reason="rasteriser needs numpy")
Image = pytest.importorskip("PIL.Image", reason="rasteriser needs Pillow")

from services.demand_tiles import blank_tile_png, render_demand_tile  # noqa: E402

SK_LAT, SK_LNG = 52.1332, -106.6700
RAMP = ("#FFE3E0", "#FFB3AC", "#FF7A6E", "#FF3B30", "#B71C1C")


def _tile_for(lat: float, lng: float, z: int) -> tuple[int, int]:
    wx, wy = lnglat_to_world_px(lat, lng, z)
    return int(wx // TILE_SIZE), int(wy // TILE_SIZE)


def _decode(png: bytes):
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def _reference_rgba(snap: TileSnapshot, z: int, x: int, y: int):
    """Independent pure-Python implementation of the whole pipeline.

    Deliberately written the slow, obvious way — nested loops, no vectorisation
    — so it can disagree with the numpy path if that path has a broadcasting or
    dtype bug. A test that shares the implementation proves nothing.
    """
    lat, _ = tile_center_latlng(z, x, y)
    sigma, support = kernel_px(snap.sigma_m, snap.support_m, meters_per_pixel(z, lat))
    local = tile_local_cells(snap.cells, z=z, x=x, y=y, support_px=support)
    lut = build_color_lut(snap.palette, size=COLOR_LUT_SIZE, max_alpha=snap.max_alpha)
    out = [[(0, 0, 0, 0)] * TILE_SIZE for _ in range(TILE_SIZE)]
    for row in range(TILE_SIZE):
        for col in range(TILE_SIZE):
            total = 0.0
            for px, py, w in local:
                d2 = (col + 0.5 - px) ** 2 + (row + 0.5 - py) ** 2
                if d2 <= support * support:
                    total += w * math.exp(-d2 / (2.0 * sigma * sigma))
            norm = min(1.0, max(0.0, total / snap.scale))
            out[row][col] = lut[round(norm * (COLOR_LUT_SIZE - 1))]
    return out


class TestRenderDemandTile:
    def _snap(self, cells, **over):
        base = dict(cells=cells, scale=5.0, palette=RAMP)
        base.update(over)
        return TileSnapshot(**base)

    def test_produces_a_256px_rgba_png(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        img = _decode(render_demand_tile(self._snap(((SK_LAT, SK_LNG, 4.0),)), z=z, x=x, y=y))
        assert img.shape == (TILE_SIZE, TILE_SIZE, 4)

    def test_empty_coverage_is_transparent_not_an_error(self):
        # A quiet suburb, or every cell suppressed for privacy, is a normal
        # outcome and must render as clean map.
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        img = _decode(render_demand_tile(self._snap(()), z=z, x=x, y=y))
        assert img[:, :, 3].max() == 0

    def test_a_far_away_cell_renders_nothing_here(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        snap = self._snap(((SK_LAT + 4.0, SK_LNG + 4.0, 50.0),))
        assert _decode(render_demand_tile(snap, z=z, x=x, y=y))[:, :, 3].max() == 0

    def test_matches_the_independent_reference_implementation(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        snap = self._snap(((SK_LAT, SK_LNG, 4.0), (SK_LAT + 0.004, SK_LNG + 0.006, 2.0)))
        got = _decode(render_demand_tile(snap, z=z, x=x, y=y))
        want = _reference_rgba(snap, z, x, y)
        # float32 accumulation vs float64 can land either side of a rounding
        # boundary; one 8-bit step of slack, no more.
        for row in range(0, TILE_SIZE, 16):
            for col in range(0, TILE_SIZE, 16):
                assert tuple(int(v) for v in got[row][col]) == pytest.approx(want[row][col], abs=1), (
                    f"pixel ({row},{col})"
                )

    def test_never_exceeds_the_alpha_ceiling(self):
        # Overlapping cells must not saturate to flat opaque red — clipping is
        # what keeps structure visible inside a dense cluster.
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        piled = tuple((SK_LAT, SK_LNG, 100.0) for _ in range(20))
        img = _decode(render_demand_tile(self._snap(piled), z=z, x=x, y=y))
        assert img[:, :, 3].max() <= round(255 * MAX_ALPHA)

    def test_is_deterministic_so_bytes_can_be_cached(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        snap = self._snap(((SK_LAT, SK_LNG, 4.0),))
        assert render_demand_tile(snap, z=z, x=x, y=y) == render_demand_tile(snap, z=z, x=x, y=y)

    def test_scale_is_absolute_not_relative_to_what_is_in_view(self):
        # The mobile renderers normalise by the strongest cell in the viewport,
        # so panning changes what a colour means and a lone weak cell renders
        # darkest. A distant, much stronger cell must not restyle this tile.
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        alone = self._snap(((SK_LAT, SK_LNG, 4.0),))
        with_far_giant = self._snap(((SK_LAT, SK_LNG, 4.0), (SK_LAT + 4.0, SK_LNG, 900.0)))
        assert render_demand_tile(alone, z=z, x=x, y=y) == render_demand_tile(with_far_giant, z=z, x=x, y=y)

    def test_density_is_continuous_across_a_tile_seam(self):
        # Render neighbours and compare the touching columns. A dropped halo
        # shows up here as a step at the boundary.
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        world = float(TILE_SIZE * (1 << z))
        ox, _ = tile_origin_px(z, x, y)
        # Put a cell right on the shared edge between tile x and tile x+1.
        edge_lng = (ox + TILE_SIZE) / world * 360.0 - 180.0
        snap = self._snap(((SK_LAT, edge_lng, 6.0),))
        left = _decode(render_demand_tile(snap, z=z, x=x, y=y))
        right = _decode(render_demand_tile(snap, z=z, x=x + 1, y=y))
        assert left[:, -1, 3].max() > 0, "cell on the seam left no heat on the left tile"
        assert right[:, 0, 3].max() > 0, "cell on the seam left no heat on the right tile"
        # Adjacent world pixels, so the two columns must nearly agree.
        assert int(abs(int(left[:, -1, 3].max()) - int(right[:, 0, 3].max()))) <= 2

    def test_rejects_a_bad_address_before_rendering(self):
        with pytest.raises(TileRequestError):
            render_demand_tile(self._snap(()), z=MAX_ZOOM + 1, x=0, y=0)


def test_blank_tile_is_a_transparent_256px_png():
    img = _decode(blank_tile_png())
    assert img.shape == (TILE_SIZE, TILE_SIZE, 4)
    assert img[:, :, 3].max() == 0
