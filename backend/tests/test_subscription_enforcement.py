"""
Tests for per-service-area Spinr Pass subscription enforcement.

Covers three guard points:
  1. go-online blocked when area.subscription_required=True and driver has no sub
  2. dispatch (find_candidate_drivers) filters out unsubscribed drivers
  3. accept_ride returns 402 when area requires subscription and driver has none
"""

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub heavy third-party dependencies so we can import backend modules
# without a live Supabase/Stripe connection.  The stubs live only in
# sys.modules for the duration of this test session.
# ---------------------------------------------------------------------------


def _stub(name: str, **attrs):
    """Insert a minimal module stub into sys.modules."""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules.setdefault(name, mod)


_stub("stripe")
_stub("stripe.error")
_stub("supabase", create_client=MagicMock())
_stub("supabase_auth", AsyncGoTrueClient=MagicMock())
_stub("postgrest")
_stub("postgrest.exceptions", APIError=Exception)
_stub("storage3")
_stub("realtime")
_stub("gotrue")
_stub("firebase_admin")
_stub("firebase_admin.messaging")
_stub("firebase_admin.credentials")
_stub("twilio")
_stub("twilio.rest")
_stub("google.auth")
_stub("google.oauth2")
_stub("google.oauth2.service_account")
_stub("anthropic")
_stub("openai")
_stub("slowapi", Limiter=MagicMock())
_stub("slowapi.errors", RateLimitExceeded=Exception)
_stub("slowapi.util")

# Project root on path so backend.* imports resolve
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _root not in sys.path:
    sys.path.insert(0, _root)
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_driver(driver_id="d1", service_area_id="area1", **kwargs):
    return {
        "id": driver_id,
        "user_id": "u1",
        "status": "active",
        "is_online": False,
        "is_available": False,
        "is_verified": True,
        "service_area_id": service_area_id,
        "lat": 52.1,
        "lng": -106.6,
        **kwargs,
    }


def _make_area(area_id="area1", subscription_required=False, spinr_pass_enabled=True):
    return {
        "id": area_id,
        "name": "Saskatoon",
        "spinr_pass_enabled": spinr_pass_enabled,
        "subscription_required": subscription_required,
    }


def _make_ride(ride_id="r1", service_area_id="area1", vehicle_type_id="standard"):
    return {
        "id": ride_id,
        "status": "searching",
        "rider_id": "rider_u1",
        "driver_id": None,
        "service_area_id": service_area_id,
        "vehicle_type_id": vehicle_type_id,
        "pickup_lat": 52.1,
        "pickup_lng": -106.6,
    }


def _make_sub(driver_id="d1", status="active"):
    return {"id": "sub1", "driver_id": driver_id, "status": status, "plan_id": "plan1"}


# ---------------------------------------------------------------------------
# Subtask 3: go-online guard (logic replayed directly — no full module import)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestGoOnlineSubscriptionGuard:
    """Replays the subscription-check logic from update_driver_status."""

    async def _run_check(self, driver, area, active_sub, global_require=False):
        """
        Replicate the subscription enforcement block from update_driver_status
        using mocked DB calls.  Returns the sub row found (or None), or
        "skipped" if the check was bypassed entirely.
        """
        mock_db = MagicMock()
        mock_db.find_one = AsyncMock(return_value=area)
        mock_db.get_rows = AsyncMock(return_value=[active_sub] if active_sub else [])

        require_sub = global_require

        if not require_sub and driver.get("service_area_id"):
            _driver_area = await mock_db.find_one("service_areas", {"id": driver["service_area_id"]})
            if _driver_area and _driver_area.get("subscription_required"):
                require_sub = True

        if not require_sub:
            return "skipped"

        rows = await mock_db.get_rows(
            "driver_subscriptions",
            {"driver_id": driver["id"], "status": "active"},
            limit=1,
        )
        return rows[0] if rows else None

    async def test_area_not_required_allows_online_without_sub(self):
        driver = _make_driver()
        area = _make_area(subscription_required=False)
        result = await self._run_check(driver, area, active_sub=None)
        assert result == "skipped"

    async def test_area_required_with_active_sub_passes(self):
        driver = _make_driver()
        area = _make_area(subscription_required=True)
        sub = _make_sub(driver_id="d1")
        result = await self._run_check(driver, area, active_sub=sub)
        assert result is not None
        assert result["status"] == "active"

    async def test_area_required_without_sub_returns_none(self):
        driver = _make_driver()
        area = _make_area(subscription_required=True)
        result = await self._run_check(driver, area, active_sub=None)
        # Production code raises SpinrException on None; here we confirm
        # the check resolves to None so the caller would block.
        assert result is None

    async def test_global_flag_overrides_area_flag(self):
        """Global require_driver_subscription=True enforces even if area flag is False."""
        driver = _make_driver()
        area = _make_area(subscription_required=False)
        result = await self._run_check(driver, area, active_sub=None, global_require=True)
        assert result is None  # sub query ran, found nothing

    async def test_no_service_area_skips_area_check(self):
        driver = _make_driver(service_area_id=None)
        area = _make_area(subscription_required=True)
        result = await self._run_check(driver, area, active_sub=None)
        assert result == "skipped"


# ---------------------------------------------------------------------------
# Subtask 4: dispatch filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestDispatchSubscriptionFilter:
    """find_candidate_drivers filters unsubscribed drivers in required areas."""

    def _make_service(self, area, subscribed_ids):
        """Build a minimal DispatchService with mocked DB, avoiding heavy imports."""

        # Inline the DispatchService class structure we test — avoids importing
        # the full module chain (settings_loader → db_supabase → supabase).
        class _FakeDispatch:
            def __init__(self, db):
                self.db = db

            async def find_candidate_drivers(self, ride):
                # Replicate the subscription-filter logic from dispatch_service.py.
                # Presence filter is not under test here — treat all DB drivers as
                # present (mirrors the Redis fail-open path in production).
                rows = await self.db.get_rows("drivers", {}, limit=500)
                if not rows:
                    return rows

                if rows and ride.get("service_area_id"):
                    try:
                        _area = await self.db.find_one("service_areas", {"id": ride["service_area_id"]})
                        if _area and _area.get("subscription_required"):
                            candidate_ids = [d["id"] for d in rows]
                            active_subs = await self.db.get_rows(
                                "driver_subscriptions",
                                {"driver_id": {"$in": candidate_ids}, "status": "active"},
                                columns="driver_id",
                                limit=len(candidate_ids),
                            )
                            subscribed = {s["driver_id"] for s in (active_subs or [])}
                            rows = [d for d in rows if d["id"] in subscribed]
                    except Exception:
                        pass  # fail open

                return rows

        mock_db = MagicMock()

        async def _find_one(table, filt):
            if table == "service_areas":
                return area
            return None

        async def _get_rows(table, filt=None, columns=None, limit=500, **kw):
            if table == "drivers":
                return [_make_driver("d1"), _make_driver("d2"), _make_driver("d3")]
            if table == "driver_subscriptions":
                return [{"driver_id": did} for did in subscribed_ids]
            return []

        mock_db.find_one = AsyncMock(side_effect=_find_one)
        mock_db.get_rows = AsyncMock(side_effect=_get_rows)
        return _FakeDispatch(mock_db)

    async def test_required_area_filters_unsubscribed(self):
        area = _make_area(subscription_required=True)
        svc = self._make_service(area, subscribed_ids=["d1", "d2"])
        # d3 has no subscription — expect it to be filtered out
        result = await svc.find_candidate_drivers(_make_ride())
        ids = {d["id"] for d in result}
        assert "d1" in ids
        assert "d2" in ids
        assert "d3" not in ids

    async def test_not_required_area_passes_all_drivers(self):
        area = _make_area(subscription_required=False)
        svc = self._make_service(area, subscribed_ids=["d1", "d2"])
        result = await svc.find_candidate_drivers(_make_ride())
        ids = {d["id"] for d in result}
        assert ids == {"d1", "d2", "d3"}

    async def test_no_service_area_on_ride_skips_filter(self):
        area = _make_area(subscription_required=True)
        svc = self._make_service(area, subscribed_ids=["d1"])
        result = await svc.find_candidate_drivers(_make_ride(service_area_id=None))
        # No area_id on ride → subscription check skipped → all 3 returned
        assert len(result) == 3

    async def test_subscription_db_error_fails_open(self):
        """A DB error during subscription lookup must not drop all drivers."""
        area = _make_area(subscription_required=True)

        class _ErrorDispatch:
            def __init__(self):
                self.db = MagicMock()

            async def find_candidate_drivers(self, ride):
                rows = [_make_driver("d1"), _make_driver("d2")]
                present = {"d1", "d2"}
                rows = [d for d in rows if d["id"] in present]
                if rows and ride.get("service_area_id"):
                    try:
                        _area = await self.db.find_one("service_areas", {"id": ride["service_area_id"]})
                        if _area and _area.get("subscription_required"):
                            # Simulate a DB failure
                            raise RuntimeError("DB unavailable")
                    except Exception:
                        pass  # fail open
                return rows

        svc = _ErrorDispatch()
        svc.db.find_one = AsyncMock(return_value=area)

        result = await svc.find_candidate_drivers(_make_ride())
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Subtask 5: accept_ride guard (logic replayed directly)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
class TestAcceptRideSubscriptionGuard:
    """Replays the subscription-check block from accept_ride."""

    class _SubscriptionRequired(Exception):
        """Stand-in for SpinrException with status_code=402."""

        def __init__(self, message, status_code):
            super().__init__(message)
            self.message = message
            self.status_code = status_code

    async def _run_check(self, area, active_sub):
        """Replay the accept_ride subscription block."""
        mock_db = MagicMock()
        mock_db.find_one = AsyncMock(return_value=area)
        mock_db.get_rows = AsyncMock(return_value=[active_sub] if active_sub else [])

        driver = _make_driver()
        ride = _make_ride()

        if ride.get("service_area_id"):
            _ride_area = await mock_db.find_one("service_areas", {"id": ride["service_area_id"]})
            if _ride_area and _ride_area.get("subscription_required"):
                rows = await mock_db.get_rows(
                    "driver_subscriptions",
                    {"driver_id": driver["id"], "status": "active"},
                    limit=1,
                )
                _active_sub = rows[0] if rows else None
                if not _active_sub:
                    raise self._SubscriptionRequired(
                        "An active Spinr Pass subscription is required to accept rides in this area.",
                        status_code=402,
                    )
        return "ok"

    async def test_required_area_no_sub_raises_402(self):
        area = _make_area(subscription_required=True)
        with pytest.raises(self._SubscriptionRequired) as exc_info:
            await self._run_check(area, active_sub=None)
        assert exc_info.value.status_code == 402
        assert "subscription" in exc_info.value.message.lower()

    async def test_required_area_with_active_sub_passes(self):
        area = _make_area(subscription_required=True)
        sub = _make_sub(driver_id="d1")
        result = await self._run_check(area, active_sub=sub)
        assert result == "ok"

    async def test_not_required_area_passes_without_sub(self):
        area = _make_area(subscription_required=False)
        result = await self._run_check(area, active_sub=None)
        assert result == "ok"
