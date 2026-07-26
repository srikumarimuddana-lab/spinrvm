"""Tests for the GPS-blind driver location-freshness gate in dispatch (P1).

Verifies that _is_dispatchable_driver rejects drivers whose location is
stale (updated_at older than _LOCATION_STALE_SECONDS).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class TestIsDispatchableDriverFreshness:
    def test_fresh_location_is_dispatchable(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        }
        assert _is_dispatchable_driver(driver) is True

    def test_stale_location_is_not_dispatchable(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat(),
        }
        assert _is_dispatchable_driver(driver) is False

    def test_exactly_at_threshold_is_dispatchable(self):
        from backend.services.dispatch_service import _LOCATION_STALE_SECONDS, _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=_LOCATION_STALE_SECONDS - 2)).isoformat(),
        }
        assert _is_dispatchable_driver(driver) is True

    def test_just_over_threshold_is_not_dispatchable(self):
        from backend.services.dispatch_service import _LOCATION_STALE_SECONDS, _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": (datetime.now(timezone.utc) - timedelta(seconds=_LOCATION_STALE_SECONDS + 1)).isoformat(),
        }
        assert _is_dispatchable_driver(driver) is False

    def test_missing_updated_at_is_dispatchable(self):
        """Graceful degradation: if updated_at is absent (legacy row), don't
        block — the presence check is the fallback."""
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {"user_id": "u1", "lat": 50.45, "lng": -104.6}
        assert _is_dispatchable_driver(driver) is True

    def test_missing_user_id_still_rejected(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {"lat": 50.45, "lng": -104.6, "updated_at": datetime.now(timezone.utc).isoformat()}
        assert _is_dispatchable_driver(driver) is False

    def test_missing_lat_lng_still_rejected(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {"user_id": "u1", "updated_at": datetime.now(timezone.utc).isoformat()}
        assert _is_dispatchable_driver(driver) is False

    def test_updated_at_with_z_suffix(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        fresh = datetime.now(timezone.utc) - timedelta(seconds=10)
        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": fresh.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        }
        assert _is_dispatchable_driver(driver) is True

    def test_updated_at_as_datetime_object(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": datetime.now(timezone.utc) - timedelta(seconds=10),
        }
        assert _is_dispatchable_driver(driver) is True

    def test_invalid_updated_at_is_dispatchable(self):
        """Malformed timestamp should not crash dispatch — fail open."""
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": "not-a-date",
        }
        assert _is_dispatchable_driver(driver) is True
