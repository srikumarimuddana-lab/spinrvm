"""Unit tests for utils.h3_cells — no Redis, no DB."""

from __future__ import annotations

import pytest

from utils.h3_cells import (
    ALLOWED_RESOLUTIONS,
    MAX_DISK_CELLS,
    cell_for,
    cells_covering,
    cells_for_all_resolutions,
    centroid,
    disk,
    disk_cell_count,
    filter_ids_within_radius,
    haversine_km,
    k_ring_size,
    validate_latlng,
    validate_resolution,
)

pytestmark = pytest.mark.unit

# Downtown Saskatoon — a real Spinr pickup, used as a stable fixture.
SK_LAT, SK_LNG = 52.1332, -106.6700


def test_cell_for_is_stable_and_valid():
    cell = cell_for(SK_LAT, SK_LNG, 8)
    assert isinstance(cell, str)
    assert len(cell) >= 15
    # Same point, same res → same cell (the whole index depends on this).
    assert cell_for(SK_LAT, SK_LNG, 8) == cell


def test_nearby_points_share_or_neighbor_cells():
    a = cell_for(SK_LAT, SK_LNG, 8)
    b = cell_for(SK_LAT + 0.001, SK_LNG, 8)  # ~111 m north
    ring = set(disk(a, 1))
    assert b == a or b in ring


@pytest.mark.parametrize("res", sorted(ALLOWED_RESOLUTIONS))
def test_all_supported_resolutions_produce_cells(res):
    assert cell_for(SK_LAT, SK_LNG, res)


def test_reject_unsupported_resolution():
    with pytest.raises(ValueError, match="resolution"):
        validate_resolution(6)
    with pytest.raises(ValueError, match="resolution"):
        cell_for(SK_LAT, SK_LNG, 10)


@pytest.mark.parametrize(
    "lat,lng",
    [
        (91, 0),
        (-91, 0),
        (0, 181),
        (0, -181),
        (float("nan"), 0),
        (0, float("inf")),
    ],
)
def test_reject_invalid_coordinates(lat, lng):
    with pytest.raises(ValueError):
        validate_latlng(lat, lng)


def test_k_ring_covers_typical_search_within_disk_cap():
    k = k_ring_size(5.0, 8)
    cells = cells_covering(SK_LAT, SK_LNG, 5.0, 8)
    # 1 + 3k(k+1) hexes in a filled disk.
    assert len(cells) == disk_cell_count(k)
    assert len(cells) <= MAX_DISK_CELLS
    assert cell_for(SK_LAT, SK_LNG, 8) in cells


def test_cells_covering_rejects_oversized_disk_without_coords_in_error():
    """10 km at res 8 is ~1657 SUNION keys — must fail closed so matching failovers."""
    with pytest.raises(ValueError, match="h3_disk_too_large") as exc:
        cells_covering(SK_LAT, SK_LNG, 10.0, 8)
    msg = str(exc.value)
    assert str(SK_LAT) not in msg
    assert str(SK_LNG) not in msg
    assert disk_cell_count(k_ring_size(10.0, 8)) > MAX_DISK_CELLS


def test_query_resolution_drops_to_coarser_hex_before_raising():
    from utils.h3_cells import H3DiskTooLargeError, resolution_for_query

    assert resolution_for_query(10.0, 8) == 7
    with pytest.raises(H3DiskTooLargeError):
        resolution_for_query(100.0, 9)


def test_centroid_is_inside_original_cell():
    cell = cell_for(SK_LAT, SK_LNG, 9)
    clat, clng = centroid(cell)
    assert cell_for(clat, clng, 9) == cell


def test_haversine_zero_and_known_distance():
    assert haversine_km(SK_LAT, SK_LNG, SK_LAT, SK_LNG) == 0.0
    # ~1 degree of latitude is ~111 km.
    d = haversine_km(52.0, -106.0, 53.0, -106.0)
    assert 110 < d < 112


def test_filter_ids_within_radius_drops_far_drivers():
    ids = filter_ids_within_radius(
        [
            ("near", SK_LAT + 0.01, SK_LNG),  # ~1.1 km
            ("far", SK_LAT + 1.0, SK_LNG),  # ~111 km
        ],
        SK_LAT,
        SK_LNG,
        10.0,
    )
    assert ids == ["near"]


def test_cells_for_all_resolutions_has_7_8_9():
    cells = cells_for_all_resolutions(SK_LAT, SK_LNG)
    assert set(cells) == {7, 8, 9}
    # Parent containment: a finer cell's parent equals the coarser cell.
    import h3

    assert h3.cell_to_parent(cells[9], 8) == cells[8]
    assert h3.cell_to_parent(cells[8], 7) == cells[7]
