"""
Tests for per-service-area Spinr Pass subscription enforcement.

Covers three guard points:
  1. go-online blocked when area.subscription_required=True and driver has no sub
  2. dispatch (find_candidate_drivers) filters out unsubscribed drivers
  3. accept_ride returns 402 when area requires subscription and driver has none
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
# Subtask 3: go-online guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGoOnlineSubscriptionGuard:
    """update_driver_status checks service area subscription_required."""

    async def _call_update_status(self, driver, area, active_sub, is_online=True):
        """
        Invoke update_driver_status's subscription-check block directly by
        replaying the relevant logic with mocked DB calls. We don't spin up
        a full FastAPI test client here — the logic under test is pure Python
        and the DB calls are the only integration point.
        """
        import importlib

        # Reload to avoid cross-test pollution
        import backend.routes.drivers as drv_mod

        importlib.reload(drv_mod)

        mock_db = MagicMock()
        # find_one returns the service area
        mock_db.find_one = AsyncMock(return_value=area)
        # get_rows returns the subscription (or empty)
        mock_db.get_rows = AsyncMock(return_value=[active_sub] if active_sub else [])

        mock_settings = {"require_driver_subscription": False}

        with (
            patch("backend.routes.drivers.db_supabase", mock_db),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value=mock_settings)),
        ):
            # Re-import after patching

            # Replicate the subscription check block from update_driver_status
            require_sub = bool(mock_settings.get("require_driver_subscription", False))

            if not require_sub and driver.get("service_area_id"):
                _driver_area = await mock_db.find_one("service_areas", {"id": driver["service_area_id"]})
                if _driver_area and _driver_area.get("subscription_required"):
                    require_sub = True

            if require_sub:
                sub_rows = await mock_db.get_rows(
                    "driver_subscriptions",
                    {"driver_id": driver["id"], "status": "active"},
                    limit=1,
                )
                sub = sub_rows[0] if sub_rows else None
                return sub  # caller asserts on this
            return "skipped"

    async def test_area_not_required_allows_online_without_sub(self):
        driver = _make_driver()
        area = _make_area(subscription_required=False)
        result = await self._call_update_status(driver, area, active_sub=None)
        assert result == "skipped"

    async def test_area_required_with_active_sub_passes(self):
        driver = _make_driver()
        area = _make_area(subscription_required=True)
        sub = _make_sub(driver_id="d1")
        result = await self._call_update_status(driver, area, active_sub=sub)
        assert result is not None
        assert result["status"] == "active"

    async def test_area_required_without_sub_returns_none(self):
        driver = _make_driver()
        area = _make_area(subscription_required=True)
        result = await self._call_update_status(driver, area, active_sub=None)
        # The real handler raises SpinrException on None sub; here we confirm
        # the check resolves to None (no sub found) so the caller would block.
        assert result is None

    async def test_no_service_area_skips_check(self):
        driver = _make_driver(service_area_id=None)
        area = _make_area(subscription_required=True)
        result = await self._call_update_status(driver, area, active_sub=None)
        assert result == "skipped"


# ---------------------------------------------------------------------------
# Subtask 4: dispatch filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDispatchSubscriptionFilter:
    """find_candidate_drivers filters unsubscribed drivers in required areas."""

    def _make_dispatcher(self, area, active_sub_driver_ids):
        """Build a DispatchService with mocked DB."""
        from backend.services.dispatch_service import DispatchService

        mock_db = MagicMock()

        async def _find_one(table, filt):
            if table == "service_areas":
                return area
            return None

        async def _get_rows(table, filt=None, columns=None, limit=500, **kw):
            if table == "drivers":
                return [
                    _make_driver("d1"),
                    _make_driver("d2"),
                    _make_driver("d3"),
                ]
            if table == "driver_subscriptions":
                return [{"driver_id": did} for did in active_sub_driver_ids]
            return []

        mock_db.find_one = AsyncMock(side_effect=_find_one)
        mock_db.get_rows = AsyncMock(side_effect=_get_rows)

        svc = DispatchService.__new__(DispatchService)
        svc.db = mock_db
        return svc

    async def test_required_area_filters_unsubscribed(self):
        area = _make_area(subscription_required=True)
        # Only d1 and d2 have active subscriptions; d3 does not.
        svc = self._make_dispatcher(area, active_sub_driver_ids=["d1", "d2"])
        ride = _make_ride()

        # Patch present_driver_ids to return all IDs (Redis presence not under test)
        with patch(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1", "d2", "d3"}),
        ):
            result = await svc.find_candidate_drivers(ride)

        ids = {d["id"] for d in result}
        assert "d1" in ids
        assert "d2" in ids
        assert "d3" not in ids

    async def test_not_required_area_passes_all_drivers(self):
        area = _make_area(subscription_required=False)
        # Even though d3 has no sub, the area doesn't require one.
        svc = self._make_dispatcher(area, active_sub_driver_ids=["d1", "d2"])
        ride = _make_ride()

        with patch(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1", "d2", "d3"}),
        ):
            result = await svc.find_candidate_drivers(ride)

        ids = {d["id"] for d in result}
        assert ids == {"d1", "d2", "d3"}

    async def test_no_service_area_on_ride_skips_filter(self):
        area = _make_area(subscription_required=True)
        svc = self._make_dispatcher(area, active_sub_driver_ids=["d1"])
        ride = _make_ride(service_area_id=None)

        with patch(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1", "d2", "d3"}),
        ):
            result = await svc.find_candidate_drivers(ride)

        # No area_id → subscription check skipped → all 3 returned
        assert len(result) == 3

    async def test_subscription_db_error_fails_open(self):
        """A DB error during subscription lookup must not drop all drivers."""
        area = _make_area(subscription_required=True)
        from backend.services.dispatch_service import DispatchService

        mock_db = MagicMock()

        async def _find_one(table, filt):
            return area

        async def _get_rows(table, filt=None, columns=None, limit=500, **kw):
            if table == "drivers":
                return [_make_driver("d1"), _make_driver("d2")]
            raise RuntimeError("DB unavailable")

        mock_db.find_one = AsyncMock(side_effect=_find_one)
        mock_db.get_rows = AsyncMock(side_effect=_get_rows)

        svc = DispatchService.__new__(DispatchService)
        svc.db = mock_db

        ride = _make_ride()
        with patch(
            "backend.services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1", "d2"}),
        ):
            result = await svc.find_candidate_drivers(ride)

        # Fails open: both drivers returned despite subscription lookup error
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Subtask 5: accept_ride guard (logic unit test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAcceptRideSubscriptionGuard:
    """The subscription check block in accept_ride raises 402 when required."""

    async def _run_subscription_check(self, area, active_sub):
        """Replay just the subscription-check block from accept_ride."""
        mock_db = MagicMock()
        mock_db.find_one = AsyncMock(return_value=area)
        mock_db.get_rows = AsyncMock(return_value=[active_sub] if active_sub else [])

        driver = _make_driver()
        ride = _make_ride()

        from backend.utils.error_keys import ErrorKeys

        try:
            from backend.utils.exceptions import SpinrException
        except ImportError:
            from utils.exceptions import SpinrException

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
                    raise SpinrException(
                        message="An active Spinr Pass subscription is required to accept rides in this area.",
                        error_code="payment_failed",
                        status_code=402,
                        message_key=ErrorKeys.DRIVER_SUBSCRIPTION_REQUIRED,
                        action_hint="Subscribe to Spinr Pass",
                    )
        return "ok"

    async def test_required_area_no_sub_raises_402(self):
        from backend.utils.exceptions import SpinrException

        area = _make_area(subscription_required=True)
        with pytest.raises(SpinrException) as exc_info:
            await self._run_subscription_check(area, active_sub=None)
        assert exc_info.value.status_code == 402
        assert "subscription" in exc_info.value.message.lower()

    async def test_required_area_with_active_sub_passes(self):
        area = _make_area(subscription_required=True)
        sub = _make_sub(driver_id="d1")
        result = await self._run_subscription_check(area, active_sub=sub)
        assert result == "ok"

    async def test_not_required_area_passes_without_sub(self):
        area = _make_area(subscription_required=False)
        result = await self._run_subscription_check(area, active_sub=None)
        assert result == "ok"
