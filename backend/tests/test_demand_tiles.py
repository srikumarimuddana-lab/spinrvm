"""Demand tile rasteriser — projection, kernel, halo selection, colour table.

Stdlib only, deliberately. These cover where the real bugs live — tile seams,
absolute scale, the alpha ceiling — so they run even in an environment without
numpy. The render pipeline itself is exercised in test_demand_tiles_render.py,
which needs numpy and Pillow; keeping the split means a missing optional dep
cannot silently skip this file too.
"""

from __future__ import annotations

import math

import pytest

from services.demand_tiles import (
    COLOR_LUT_SIZE,
    MAX_ALPHA,
    MAX_CELLS,
    MAX_ZOOM,
    MIN_SIGMA_PX,
    TILE_SIZE,
    TileRequestError,
    TileSnapshot,
    build_color_lut,
    kernel_px,
    kernel_taper,
    kernel_value,
    lnglat_to_world_px,
    meters_per_pixel,
    tile_center_latlng,
    tile_local_cells,
    tile_origin_px,
    validate_tile,
)

pytestmark = pytest.mark.unit

# Downtown Saskatoon — a real Spinr service area, used as a stable fixture.
SK_LAT, SK_LNG = 52.1332, -106.6700
RAMP = ("#FFE3E0", "#FFB3AC", "#FF7A6E", "#FF3B30", "#B71C1C")


def _tile_for(lat: float, lng: float, z: int) -> tuple[int, int]:
    wx, wy = lnglat_to_world_px(lat, lng, z)
    return int(wx // TILE_SIZE), int(wy // TILE_SIZE)


# --------------------------------------------------------------------------
# Tile address validation
# --------------------------------------------------------------------------


class TestValidateTile:
    def test_accepts_a_real_address(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        validate_tile(z, x, y)

    @pytest.mark.parametrize("z", [-1, MAX_ZOOM + 1, 999])
    def test_rejects_zoom_outside_the_hard_ceiling(self, z):
        # Must reject BEFORE any 2**z runs — an unbounded z turns the world-size
        # arithmetic into a huge int and hangs rather than erroring.
        with pytest.raises(TileRequestError):
            validate_tile(z, 0, 0)

    @pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (1 << 10, 0), (0, 1 << 10)])
    def test_rejects_coordinates_outside_the_zoom_grid(self, x, y):
        with pytest.raises(TileRequestError):
            validate_tile(10, x, y)

    @pytest.mark.parametrize("bad", [1.0, "3", None, True])
    def test_rejects_non_integers_including_bool(self, bad):
        # bool is an int subclass; True would silently mean tile 1.
        with pytest.raises(TileRequestError):
            validate_tile(bad, 0, 0)


# --------------------------------------------------------------------------
# Web Mercator
# --------------------------------------------------------------------------


class TestProjection:
    def test_origin_maps_to_the_centre_of_the_world_tile(self):
        assert lnglat_to_world_px(0.0, 0.0, 0) == pytest.approx((128.0, 128.0))

    def test_corners_map_to_the_world_bounds(self):
        assert lnglat_to_world_px(85.05112878, -180.0, 0) == pytest.approx((0.0, 0.0), abs=1e-6)
        assert lnglat_to_world_px(-85.05112878, 180.0, 0) == pytest.approx((256.0, 256.0), abs=1e-6)

    def test_world_doubles_each_zoom(self):
        assert lnglat_to_world_px(0.0, 0.0, 1) == pytest.approx((256.0, 256.0))
        assert lnglat_to_world_px(0.0, 0.0, 2) == pytest.approx((512.0, 512.0))

    def test_clamps_the_poles_instead_of_diverging(self):
        # Mercator y goes infinite at +/-90; an unclamped value would produce
        # inf and poison every downstream pixel index.
        for lat in (90.0, -90.0, 1e6, -1e6):
            x, y = lnglat_to_world_px(lat, 0.0, 5)
            assert math.isfinite(x) and math.isfinite(y)

    @pytest.mark.parametrize("z,x,y", [(0, 0, 0), (10, 200, 350), (16, 12345, 22222)])
    def test_tile_centre_round_trips_to_the_tile_middle(self, z, x, y):
        lat, lng = tile_center_latlng(z, x, y)
        wx, wy = lnglat_to_world_px(lat, lng, z)
        ox, oy = tile_origin_px(z, x, y)
        assert (wx - ox, wy - oy) == pytest.approx((TILE_SIZE / 2, TILE_SIZE / 2), abs=1e-6)


class TestMetersPerPixel:
    def test_halves_with_each_zoom_step(self):
        assert meters_per_pixel(11, 0.0) == pytest.approx(meters_per_pixel(10, 0.0) / 2)

    def test_shrinks_with_latitude(self):
        # Ignoring the cosine would make every blob ~1.6x too wide in
        # Saskatchewan, where cos(52.13 deg) is about 0.614.
        ratio = meters_per_pixel(13, SK_LAT) / meters_per_pixel(13, 0.0)
        assert ratio == pytest.approx(math.cos(math.radians(SK_LAT)), rel=1e-9)


# --------------------------------------------------------------------------
# Kernel sizing
# --------------------------------------------------------------------------


class TestKernelPx:
    def test_uses_metres_where_the_raster_can_resolve_them(self):
        mpp = meters_per_pixel(13, SK_LAT)
        sigma, support = kernel_px(180.0, 540.0, mpp)
        assert sigma == pytest.approx(180.0 / mpp)
        assert support == pytest.approx(540.0 / mpp)

    def test_floors_the_kernel_at_low_zoom(self):
        # At z=10 near 52N a pixel covers ~94 m, so a 180 m sigma is under 2 px
        # — below what the raster can resolve, and it aliases into specks.
        mpp = meters_per_pixel(10, SK_LAT)
        assert 180.0 / mpp < MIN_SIGMA_PX
        sigma, _ = kernel_px(180.0, 540.0, mpp)
        assert sigma == pytest.approx(MIN_SIGMA_PX)

    def test_preserves_the_support_ratio_when_the_floor_binds(self):
        # If support did not scale with the lifted sigma, the 3-sigma cut would
        # slice into the visible kernel and put a hard edge back.
        sigma, support = kernel_px(180.0, 540.0, meters_per_pixel(10, SK_LAT))
        assert support / sigma == pytest.approx(3.0)

    @pytest.mark.parametrize("mpp", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_a_nonsense_resolution(self, mpp):
        with pytest.raises(TileRequestError):
            kernel_px(180.0, 540.0, mpp)


# --------------------------------------------------------------------------
# Halo selection — the seam guard
# --------------------------------------------------------------------------


class TestTileLocalCells:
    def test_keeps_a_cell_inside_the_tile_and_places_it_correctly(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        got = tile_local_cells([(SK_LAT, SK_LNG, 4.0)], z=z, x=x, y=y, support_px=40.0)
        assert len(got) == 1
        px, py, w = got[0]
        assert 0 <= px <= TILE_SIZE and 0 <= py <= TILE_SIZE
        assert w == 4.0

    def test_keeps_a_cell_just_outside_so_seams_stay_continuous(self):
        # This is the whole reason the halo exists: selecting only cells whose
        # centre is inside would drop every kernel straddling the boundary, and
        # the missing half-blobs draw as grid lines where tiles meet.
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        ox, _oy = tile_origin_px(z, x, y)
        # A point 20 px left of the tile's left edge, at the same latitude.
        world = float(TILE_SIZE * (1 << z))
        lng_left = (ox - 20.0) / world * 360.0 - 180.0
        got = tile_local_cells([(SK_LAT, lng_left, 1.0)], z=z, x=x, y=y, support_px=40.0)
        assert len(got) == 1
        assert got[0][0] < 0  # outside the tile, deliberately kept

    def test_drops_a_cell_beyond_the_halo(self):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        far = tile_local_cells([(SK_LAT, SK_LNG + 5.0, 1.0)], z=z, x=x, y=y, support_px=40.0)
        assert far == []

    @pytest.mark.parametrize(
        "cell",
        [
            (float("nan"), -106.67, 1.0),
            (52.13, float("inf"), 1.0),
            (52.13, -106.67, float("nan")),
        ],
    )
    def test_drops_non_finite_cells_before_they_reach_the_accumulator(self, cell):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        assert tile_local_cells([cell], z=z, x=x, y=y, support_px=40.0) == []

    @pytest.mark.parametrize("weight", [0.0, -1.0])
    def test_drops_non_positive_weights(self, weight):
        z = 13
        x, y = _tile_for(SK_LAT, SK_LNG, z)
        assert tile_local_cells([(SK_LAT, SK_LNG, weight)], z=z, x=x, y=y, support_px=40.0) == []

    def test_rejects_a_negative_halo(self):
        with pytest.raises(TileRequestError):
            tile_local_cells([], z=13, x=0, y=0, support_px=-1.0)


# --------------------------------------------------------------------------
# Colour lookup table
# --------------------------------------------------------------------------


class TestColorLut:
    def test_has_one_entry_per_density_step(self):
        assert len(build_color_lut(RAMP)) == COLOR_LUT_SIZE

    def test_starts_fully_transparent(self):
        # Without this the lowest density paints solid ramp[0] and the field
        # terminates at a visible disc edge instead of fading into bare map.
        assert build_color_lut(RAMP)[0][3] == 0

    def test_tops_out_at_the_alpha_ceiling(self):
        assert build_color_lut(RAMP)[-1][3] == round(255 * MAX_ALPHA)

    def test_alpha_never_decreases(self):
        alphas = [entry[3] for entry in build_color_lut(RAMP)]
        assert all(b >= a for a, b in zip(alphas, alphas[1:]))

    def test_never_exceeds_the_ceiling_anywhere(self):
        ceiling = round(255 * MAX_ALPHA)
        assert all(e[3] <= ceiling for e in build_color_lut(RAMP))

    def test_ends_on_the_ramp_endpoints(self):
        lut = build_color_lut(RAMP)
        assert lut[0][:3] == (255, 227, 224)  # #FFE3E0
        assert lut[-1][:3] == (183, 28, 28)  # #B71C1C

    def test_interpolates_hue_instead_of_banding(self):
        # A table that merely repeated each ramp stop would show 5 hard bands.
        distinct = {e[:3] for e in build_color_lut(RAMP)}
        assert len(distinct) > len(RAMP) * 10

    def test_supports_a_single_colour_ramp(self):
        lut = build_color_lut(("#FF3B30",))
        assert {e[:3] for e in lut} == {(255, 59, 48)}
        assert lut[0][3] == 0 and lut[-1][3] == round(255 * MAX_ALPHA)

    def test_respects_a_custom_ceiling(self):
        assert build_color_lut(RAMP, max_alpha=0.5)[-1][3] == round(255 * 0.5)

    @pytest.mark.parametrize(
        "kwargs,ramp",
        [
            ({}, ()),
            ({"size": 1}, RAMP),
            ({"max_alpha": 0.0}, RAMP),
            ({"max_alpha": 1.5}, RAMP),
        ],
    )
    def test_rejects_invalid_configuration(self, kwargs, ramp):
        with pytest.raises(ValueError):
            build_color_lut(ramp, **kwargs)

    def test_rejects_a_malformed_hex_colour(self):
        with pytest.raises(ValueError):
            build_color_lut(("#abc",))


# --------------------------------------------------------------------------
# Snapshot contract
# --------------------------------------------------------------------------


class TestTileSnapshot:
    def _snap(self, **over):
        base = dict(cells=((SK_LAT, SK_LNG, 3.0),), scale=10.0, palette=RAMP)
        base.update(over)
        return TileSnapshot(**base)

    def test_accepts_a_well_formed_snapshot(self):
        assert self._snap().scale == 10.0

    @pytest.mark.parametrize("scale", [0.0, -1.0, float("nan"), float("inf")])
    def test_rejects_a_scale_that_cannot_normalise(self, scale):
        with pytest.raises(ValueError):
            self._snap(scale=scale)

    def test_rejects_more_cells_than_the_cap(self):
        # A larger aggregate must be coarsened privacy-safely, never truncated:
        # dropping an arbitrary subset silently understates demand somewhere.
        with pytest.raises(ValueError):
            self._snap(cells=tuple((52.0, -106.0, 1.0) for _ in range(MAX_CELLS + 1)))

    def test_rejects_support_narrower_than_sigma(self):
        with pytest.raises(ValueError):
            self._snap(sigma_m=180.0, support_m=90.0)

    @pytest.mark.parametrize("alpha", [0.0, -0.1, 1.1])
    def test_rejects_an_out_of_range_alpha_ceiling(self, alpha):
        with pytest.raises(ValueError):
            self._snap(max_alpha=alpha)

    def test_rejects_an_empty_palette(self):
        with pytest.raises(ValueError):
            self._snap(palette=())


# --------------------------------------------------------------------------
# Kernel taper — the hard-disk-edge guard
# --------------------------------------------------------------------------


class TestKernelTaper:
    SIGMA = 15.34  # z=13 at Saskatoon
    SUPPORT = 3 * SIGMA

    def test_peaks_at_one_in_the_centre(self):
        assert kernel_value(0.0, self.SIGMA, self.SUPPORT) == pytest.approx(1.0)

    def test_reaches_exactly_zero_at_the_support_boundary(self):
        # A bare truncation leaves ~1.11% of peak here. Scaled by weight/scale
        # that residue becomes a visible cliff, so it must be zero by
        # construction rather than by hoping the numbers stay small.
        assert kernel_value(self.SUPPORT, self.SIGMA, self.SUPPORT) == 0.0

    def test_decays_monotonically(self):
        prev = kernel_value(0.0, self.SIGMA, self.SUPPORT)
        for step in range(1, 60):
            cur = kernel_value(step * self.SUPPORT / 60, self.SIGMA, self.SUPPORT)
            assert cur <= prev
            prev = cur

    def test_approaches_the_boundary_continuously(self):
        # The defect this guards: at weight 500 / scale 5 the untapered kernel
        # went from the alpha ceiling straight to 0 across one pixel.
        near = kernel_value(self.SUPPORT * 0.999, self.SIGMA, self.SUPPORT)
        assert near < 1e-3, f"residue at the edge is still {near}"

    def _alpha_profile(self, weight=500.0, scale=5.0):
        """Alpha along a 1-pixel-spaced radius, at the reported fixture."""
        out = []
        for d in range(0, int(self.SUPPORT) + 4):
            k = kernel_value(float(d), self.SIGMA, self.SUPPORT)
            out.append(round(255 * MAX_ALPHA * min(1.0, max(0.0, weight * k / scale))))
        return out

    def test_dense_demand_falls_off_over_several_pixels_not_one(self):
        """The reported defect, measured on the pixel grid it actually renders on.

        Untapered at weight 500 / scale 5 the profile ran [82, 82, 82, 82, 0] —
        the ceiling straight to nothing between adjacent pixels, with no
        intermediate value at all. Tapered it runs [82, 71, 43, 20, 0].
        """
        profile = self._alpha_profile()
        ceiling = round(255 * MAX_ALPHA)
        between = [a for a in profile if 0 < a < ceiling]
        assert len(between) >= 3, f"edge still collapses in one step: {profile[-8:]}"

    def test_no_adjacent_pixel_jumps_by_half_the_alpha_range(self):
        profile = self._alpha_profile()
        worst = max(abs(b - a) for a, b in zip(profile, profile[1:]))
        # Untapered this was a full-ceiling jump (82). It is now ~28. It is not
        # smaller because at 100x the published scale the field saturates and
        # the whole visible gradient compresses into the rim — that is a scale
        # calibration matter, not a kernel one. What must never return is the
        # discontinuity.
        assert worst < round(255 * MAX_ALPHA) // 2, f"adjacent jump of {worst}"

    def test_is_zero_beyond_support(self):
        assert kernel_value(self.SUPPORT * 1.5, self.SIGMA, self.SUPPORT) == 0.0

    def test_taper_constant_is_the_untruncated_value_at_the_edge(self):
        assert kernel_taper(self.SIGMA, self.SUPPORT) == pytest.approx(math.exp(-4.5), rel=1e-9)

    @pytest.mark.parametrize("sigma,support", [(0.0, 10.0), (10.0, 0.0), (-1.0, 10.0)])
    def test_degenerate_geometry_yields_no_taper_instead_of_dividing_by_zero(self, sigma, support):
        assert kernel_taper(sigma, support) == 0.0
