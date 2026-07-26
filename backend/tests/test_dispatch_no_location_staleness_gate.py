"""Regression guard: dispatch must NOT gate on `drivers.updated_at`.

An earlier fix added a 120s "location freshness" check to
``_is_dispatchable_driver``. It was wrong in both directions and was reverted:

  * False *stale*: a stationary driver produces no GPS fixes at all (the
    foreground watcher uses ``distanceInterval: 30``m when idle, the background
    task 50m), so ``update_driver_location`` never runs and ``updated_at``
    freezes. A driver parked in an airport queue — the most available cohort
    there is — would silently drop out of dispatch after two minutes.
  * False *fresh*: ``updated_at`` is also stamped by go-online/go-offline and
    by ride acceptance, so a GPS-dead driver who just toggled status reads as
    fresh.

Liveness is enforced upstream in ``find_candidate_drivers`` via the Redis
presence key (refreshed on every heartbeat pong *and* every location ping).
These tests pin that ``_is_dispatchable_driver`` stays a pure structural check.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


class TestIsDispatchableDriverIgnoresUpdatedAt:
    def test_stale_updated_at_is_still_dispatchable(self):
        """The parked-driver case. This is the regression that matters."""
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
        }
        assert _is_dispatchable_driver(driver) is True

    def test_fresh_updated_at_is_dispatchable(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {
            "user_id": "u1",
            "lat": 50.45,
            "lng": -104.6,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        assert _is_dispatchable_driver(driver) is True

    def test_missing_updated_at_is_dispatchable(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        assert _is_dispatchable_driver({"user_id": "u1", "lat": 50.45, "lng": -104.6}) is True

    def test_malformed_updated_at_is_dispatchable(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        driver = {"user_id": "u1", "lat": 50.45, "lng": -104.6, "updated_at": "not-a-date"}
        assert _is_dispatchable_driver(driver) is True

    def test_no_location_stale_constant_is_reintroduced(self):
        """Fails loudly if someone re-adds the retired staleness gate."""
        import backend.services.dispatch_service as ds

        assert not hasattr(ds, "_LOCATION_STALE_SECONDS"), (
            "dispatch must not gate on a DB location-freshness timestamp — "
            "Redis presence is the liveness signal (see module docstring)"
        )


class TestIsDispatchableDriverStructuralChecks:
    """The checks that DO belong here are unchanged."""

    def test_missing_user_id_rejected(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        assert _is_dispatchable_driver({"lat": 50.45, "lng": -104.6}) is False

    def test_missing_lat_rejected(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        assert _is_dispatchable_driver({"user_id": "u1", "lng": -104.6}) is False

    def test_missing_lng_rejected(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        assert _is_dispatchable_driver({"user_id": "u1", "lat": 50.45}) is False

    def test_complete_row_accepted(self):
        from backend.services.dispatch_service import _is_dispatchable_driver

        assert _is_dispatchable_driver({"user_id": "u1", "lat": 50.45, "lng": -104.6}) is True
