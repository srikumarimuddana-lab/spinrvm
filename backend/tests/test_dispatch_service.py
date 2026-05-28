"""Tests for dispatch_service.py — location freshness filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services.dispatch_service import filter_and_rank_drivers

_RIDE = {
    "pickup_lat": 52.13,
    "pickup_lng": -106.67,
    "dropoff_lat": 52.12,
    "dropoff_lng": -106.65,
    "vehicle_type_id": "economy",
}


def _driver(driver_id: str, lat: float = 52.131, lng: float = -106.671, location_updated_at=None, **kw):
    d = {
        "id": driver_id,
        "user_id": f"user_{driver_id}",
        "lat": lat,
        "lng": lng,
        "rating": 5.0,
        "location_updated_at": location_updated_at,
    }
    d.update(kw)
    return d


class TestStaleLocationFilter:
    def test_stale_driver_excluded(self):
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        drivers = [_driver("d1", location_updated_at=stale_ts)]
        result = filter_and_rank_drivers(_RIDE, drivers, "nearest", 4.0, 10.0)
        assert len(result) == 0

    def test_fresh_driver_included(self):
        fresh_ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        drivers = [_driver("d1", location_updated_at=fresh_ts)]
        result = filter_and_rank_drivers(_RIDE, drivers, "nearest", 4.0, 10.0)
        assert len(result) == 1
        assert result[0][0]["id"] == "d1"

    def test_missing_location_updated_at_excluded(self):
        drivers = [_driver("d1", location_updated_at=None)]
        result = filter_and_rank_drivers(_RIDE, drivers, "nearest", 4.0, 10.0)
        assert len(result) == 0
