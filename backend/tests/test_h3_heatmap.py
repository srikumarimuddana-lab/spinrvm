"""H3 heatmap aggregation — k-anonymity floor and centroid payload."""

from __future__ import annotations

import pytest

from services.h3_heatmap import aggregate_rides

pytestmark = pytest.mark.unit

SK = (52.1332, -106.6700)


def test_below_k_floor_is_suppressed():
    rides = [{"pickup_lat": SK[0], "pickup_lng": SK[1], "created_at": "2026-09-01T12:00:00Z"}] * 2
    out = aggregate_rides(rides, lat_key="pickup_lat", lng_key="pickup_lng", k_floor=3)
    assert out["points"] == []
    assert out["stats"]["cells_suppressed"] == 1
    assert out["aggregated"] is True


def test_k_floor_cannot_drop_below_three():
    rides = [{"pickup_lat": SK[0], "pickup_lng": SK[1]}] * 3
    out = aggregate_rides(rides, lat_key="pickup_lat", lng_key="pickup_lng", k_floor=1)
    assert out["k_floor"] == 3
    assert len(out["points"]) == 1
    lat, lng, weight = out["points"][0]
    # Centroid, not the raw GPS we fed in.
    assert (lat, lng) != SK
    assert weight == 3


def test_invalid_coords_are_skipped_not_crashed():
    rides = [{"pickup_lat": 999, "pickup_lng": 0}, {"pickup_lat": None, "pickup_lng": 1}]
    out = aggregate_rides(rides, lat_key="pickup_lat", lng_key="pickup_lng", k_floor=3)
    assert out["points"] == []
    assert out["stats"]["skipped_invalid"] == 2
