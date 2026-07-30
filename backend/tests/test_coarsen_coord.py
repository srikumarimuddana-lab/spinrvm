"""Grid-snapping for driver coordinates shown to unassigned riders.

Launch-gate hard no-go: "Any client can enumerate precise driver locations
without an assigned ride." These tests pin the properties that make coarsening
actually protective rather than cosmetic — chiefly that it is deterministic
(jitter averages back to the truth under repeated sampling) and that the cell is
roughly square in metres at Saskatchewan latitudes.
"""

import math

import pytest

from utils.pii import coarsen_coord

# Saskatoon downtown.
LAT = 52.1332
LNG = -106.6700


def _metres_between(lat1, lng1, lat2, lng2):
    """Equirectangular approximation — fine at these distances."""
    dlat = (lat2 - lat1) * 111_320.0
    dlng = (lng2 - lng1) * 111_320.0 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dlat, dlng)


def test_returns_none_for_missing_or_invalid_input():
    assert coarsen_coord(None, None) is None
    assert coarsen_coord(LAT, None) is None
    assert coarsen_coord("nope", LNG) is None
    assert coarsen_coord(91.0, LNG) is None, "latitude out of range"
    assert coarsen_coord(LAT, 181.0) is None, "longitude out of range"


def test_zero_zero_is_treated_as_no_gps_not_as_a_location():
    """(0, 0) is the registration default. Callers skip it; this must not
    silently turn it into a plausible-looking coarse point."""
    assert coarsen_coord(0.0, 0.0) is None


def test_is_deterministic():
    """The single most important property.

    Random jitter would (a) look like the car is moving and (b) leak the true
    position to anyone who samples repeatedly and averages. Same input must give
    the same output, every time.
    """
    first = coarsen_coord(LAT, LNG, cell_m=500)
    for _ in range(50):
        assert coarsen_coord(LAT, LNG, cell_m=500) == first


def test_output_is_within_half_a_cell_of_the_truth():
    """Snapping to cell centre bounds the error at half the cell diagonal."""
    cell = 500
    got = coarsen_coord(LAT, LNG, cell_m=cell)
    assert got is not None
    error = _metres_between(LAT, LNG, got[0], got[1])
    max_error = (cell * math.sqrt(2)) / 2
    assert error <= max_error, f"{error:.0f}m exceeds half-diagonal {max_error:.0f}m"


def test_resolution_is_destroyed_over_a_small_area():
    """The anti-enumeration property, stated correctly.

    An earlier version of this test asserted that two points 50m apart always
    collapse to one cell. That is false for *any* grid — a specific pair can
    straddle a boundary, and LAT here happens to sit ~5m from one. The property
    that actually matters is aggregate: many distinct real positions over a small
    area must collapse to very few reported positions, so a caller polling the
    endpoint cannot resolve individual vehicles.
    """
    step_m = 50
    span_m = 400  # a 400m box, sampled every 50m → 81 distinct real positions
    seen = set()
    n = 0
    steps = range(0, span_m + 1, step_m)
    for d_north in steps:
        for d_east in steps:
            lat = LAT + d_north / 111_320.0
            lng = LNG + d_east / (111_320.0 * math.cos(math.radians(LAT)))
            seen.add(coarsen_coord(lat, lng, cell_m=500))
            n += 1

    assert n == 81
    # A 400m box can overlap at most 2x2 cells of 500m.
    assert len(seen) <= 4, f"{n} real positions resolved to {len(seen)} reported cells: {seen}"
    assert len(seen) < n / 10, "coarsening barely reduced resolution"


def test_distant_points_do_not_collapse():
    """Over-coarsening is its own failure — a map where every car sits on one pin
    is useless. 5km apart must stay distinguishable at a 500m cell."""
    a = coarsen_coord(LAT, LNG, cell_m=500)
    b = coarsen_coord(LAT + 5000 / 111_320.0, LNG, cell_m=500)
    assert a != b


def test_moving_north_does_not_change_the_reported_longitude():
    """Regression: the longitude step must be derived from the SNAPPED latitude.

    The first implementation computed ``cos(lat)`` from the raw latitude, so
    ``lng_step`` varied continuously as a driver moved north and the snapped
    longitude drifted even though the true longitude never changed. Two
    consequences, both bad: the output was not actually a grid (a 10km northward
    walk produced a new "cell" every 25m sample), and the reported longitude
    encoded fine-grained latitude — leaking back the precision this function
    exists to remove.
    """
    fixed_lng = LNG
    by_band = {}
    for d_north_m in range(0, 5000, 25):
        lat = LAT + d_north_m / 111_320.0
        got = coarsen_coord(lat, fixed_lng, cell_m=500)
        assert got is not None
        by_band.setdefault(got[0], set()).add(got[1])

    # Within one latitude band, a constant true longitude must map to exactly one
    # reported longitude.
    for band_lat, lngs in by_band.items():
        assert len(lngs) == 1, f"latitude band {band_lat} reported {len(lngs)} longitudes for one input: {lngs}"

    # And the whole 5km walk must produce ~10 bands, not ~200.
    assert 8 <= len(by_band) <= 12, f"5km north produced {len(by_band)} bands, expected ~10"


def test_cell_is_roughly_square_in_metres_not_in_degrees():
    """The reason this snaps on a cos(lat)-scaled grid instead of rounding decimals.

    At 52°N a longitude degree is only ~61% of a latitude degree, so per-axis
    decimal rounding yields a visibly oblong cell. Counting how many distinct
    cells a fixed-length walk crosses is independent of where in a cell the walk
    starts — unlike measuring the distance to the first boundary, which an earlier
    version of this test did and which is just the (uniform random) offset of the
    start point.
    """
    cell = 500
    walk_m = 10_000

    def _cells_crossed(d_north, d_east):
        seen = set()
        for moved in range(0, walk_m, 25):
            lat = LAT + (d_north * moved) / 111_320.0
            lng = LNG + (d_east * moved) / (111_320.0 * math.cos(math.radians(LAT)))
            seen.add(coarsen_coord(lat, lng, cell_m=cell))
        return len(seen)

    north = _cells_crossed(1, 0)
    east = _cells_crossed(0, 1)
    expected = walk_m / cell  # ~20 cells in each direction

    assert 0.7 <= north / east <= 1.4, f"cell is not roughly square: {north} north vs {east} east"
    for label, count in (("north", north), ("east", east)):
        assert 0.7 <= count / expected <= 1.4, f"{label} crossed {count} cells, expected ~{expected:.0f}"


@pytest.mark.parametrize("cell", [100, 250, 500, 1000, 2000])
def test_larger_cells_are_never_more_precise(cell):
    """Monotonicity: raising cell_m must not tighten the error."""
    got = coarsen_coord(LAT, LNG, cell_m=cell)
    assert got is not None
    error = _metres_between(LAT, LNG, got[0], got[1])
    assert error <= (cell * math.sqrt(2)) / 2


def test_cell_m_zero_is_an_explicit_exact_passthrough():
    """The assigned-ride path needs exact coordinates. That is an authorization
    decision made by the caller, so the helper honours it explicitly rather than
    the caller reaching around the helper."""
    assert coarsen_coord(LAT, LNG, cell_m=0) == (LAT, LNG)
    assert coarsen_coord(LAT, LNG, cell_m=-1) == (LAT, LNG)


def test_does_not_blow_up_at_the_poles():
    """cos(lat) → 0 would make the longitude step infinite."""
    for lat in (89.999, -89.999, 90.0, -90.0):
        got = coarsen_coord(lat, 10.0, cell_m=500)
        assert got is not None, f"failed at lat={lat}"
        assert -90.0 <= got[0] <= 90.0
        assert -180.0 <= got[1] <= 180.0


def test_southern_and_western_hemispheres_snap_consistently():
    """floor() on negative values is a classic off-by-one source; assert the
    error bound holds there too rather than only in the NE quadrant."""
    for lat, lng in ((-33.86, 151.21), (52.13, -106.67), (-23.55, -46.63), (35.68, 139.69)):
        got = coarsen_coord(lat, lng, cell_m=500)
        assert got is not None
        error = _metres_between(lat, lng, got[0], got[1])
        assert error <= (500 * math.sqrt(2)) / 2, f"error {error:.0f}m at ({lat}, {lng})"
