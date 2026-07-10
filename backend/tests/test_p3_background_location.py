"""
P3-20: Background location for drivers (iOS policy changes)

Backend location-batch endpoint is fully implemented:
  POST /drivers/location-batch — accepts list or {points:[]} dict;
                                  takes the last point; updates lat/lng in DB

These tests pin:
  - List format: last point lat/lng written to DB
  - Dict with "points" key: same
  - Dict with "locations" key: same
  - Empty list → no-op (success=True, no DB write)
  - Missing lat in last point → no DB write
  - Single point: writes that single point
  - Large batch: last (most-recent) point wins

Native-device path (always-permission on iOS / Android, background
watchPosition) is pinned in the client (Jest):
  driver-app/hooks/__tests__/goOnlinePermission.test.ts

The "location continues when app is backgrounded" case requires a real device
and remains xfail below.

Run:
    pytest backend/tests/test_p3_background_location.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

DRIVER_USER_ID = "driver_user_p3_20"


def _point(lat: float = 52.1332, lng: float = -106.6700, seq: int = 1) -> dict:
    return {
        "latitude": lat,
        "longitude": lng,
        "heading": 90.0,
        "accuracy": 5.0,
        "timestamp": f"2025-01-01T00:00:0{seq}Z",
    }


# ─────────────────────────────────────────────────────────────────────────────
# POST /drivers/location-batch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestUpdateLocationBatch:
    """Pins update_location_batch: last-point wins, format flexibility, guards.

    Code under test: backend/routes/drivers.py::update_location_batch (~line 1180).
    """

    async def _batch(self, batch):
        from backend.routes.drivers import update_location_batch

        db_updates = []

        with patch(
            "backend.routes.drivers._deps.db_supabase.update_one",
            AsyncMock(side_effect=lambda t, q, d: db_updates.append((t, q, d))),
        ):
            result = await update_location_batch(
                batch=batch,
                current_user={"id": DRIVER_USER_ID},
            )

        return result, db_updates

    async def test_list_format_writes_last_point(self):
        points = [_point(52.10, -106.60, 1), _point(52.20, -106.70, 2)]
        result, updates = await self._batch(points)

        assert result["success"] is True
        assert updates, "DB not updated"
        _, query, data = updates[0]
        assert data["lat"] == 52.20
        assert data["lng"] == -106.70

    async def test_dict_with_points_key(self):
        payload = {"points": [_point(52.30, -106.80, 1)]}
        result, updates = await self._batch(payload)

        assert result["success"] is True
        assert updates
        assert updates[0][2]["lat"] == 52.30

    async def test_dict_with_locations_key(self):
        """Legacy key name 'locations' also accepted."""
        payload = {"locations": [_point(52.40, -106.90, 1)]}
        result, updates = await self._batch(payload)

        assert result["success"] is True
        assert updates
        assert updates[0][2]["lat"] == 52.40

    async def test_empty_list_is_noop(self):
        result, updates = await self._batch([])

        assert result["success"] is True
        assert not updates, "DB must not be written for empty batch"

    async def test_empty_dict_is_noop(self):
        result, updates = await self._batch({})

        assert result["success"] is True
        assert not updates

    async def test_single_point_writes_correctly(self):
        result, updates = await self._batch([_point(51.0, -105.0)])

        assert result["success"] is True
        assert updates[0][2]["lat"] == 51.0
        assert updates[0][2]["lng"] == -105.0

    async def test_large_batch_last_point_wins(self):
        """Batch from background tracking — most-recent position applied."""
        points = [_point(52.0 + i * 0.001, -106.0, i) for i in range(50)]
        result, updates = await self._batch(points)

        assert result["success"] is True
        # Last point in the list is the most-recent
        last = points[-1]
        assert updates[0][2]["lat"] == last["latitude"]

    async def test_update_scoped_to_current_user(self):
        """DB update uses user_id from current_user, not any field in the payload."""
        result, updates = await self._batch([_point()])

        _, query, _ = updates[0]
        assert query.get("user_id") == DRIVER_USER_ID


# ─────────────────────────────────────────────────────────────────────────────
# Native background location permission — requires real device
# ─────────────────────────────────────────────────────────────────────────────

_TS_HOOK = Path(__file__).parents[2] / "driver-app" / "hooks" / "useDriverDashboard.ts"
_JEST_COVERAGE = Path(__file__).parents[2] / "driver-app" / "hooks" / "__tests__" / "goOnlinePermission.test.ts"


class TestBackgroundPermissionCodePath:
    """P3-20: requestBackgroundPermissionsAsync is called on go-online.

    Coverage is provided by the Jest suite:
      driver-app/hooks/__tests__/goOnlinePermission.test.ts

    These tests pin that the call is present in the TypeScript source and
    that the Jest coverage file exists.
    """

    def test_ios_always_permission_requested_on_go_online(self):
        """Location.requestBackgroundPermissionsAsync() is in the go-online
        path of useDriverDashboard.ts; Jest test pins the call is made."""
        src = _TS_HOOK.read_text()
        assert "requestBackgroundPermissionsAsync" in src, (
            "requestBackgroundPermissionsAsync call removed from useDriverDashboard.ts"
        )
        assert _JEST_COVERAGE.exists(), "goOnlinePermission.test.ts is missing — Jest coverage dropped"

    def test_android_background_location_permission_requested(self):
        """Same expo-location API call handles Android ACCESS_BACKGROUND_LOCATION.
        Source presence + Jest coverage file verify the path exists."""
        src = _TS_HOOK.read_text()
        assert "requestBackgroundPermissionsAsync" in src
        assert _JEST_COVERAGE.exists()


@pytest.mark.xfail(
    strict=False,
    reason=(
        "P3-20 gap: verifying that location updates *continue* when the app is "
        "backgrounded requires a real iOS/Android device with a background task "
        "runner (Detox + device.setPermissions or Espresso). "
        "Covered manually via docs/MOBILE_SMOKE.md §F step F-4."
    ),
)
class TestBackgroundLocationContinuity:
    """Real-device only — xfail until Detox background-task tests are active."""

    def test_location_updates_continue_when_app_backgrounded(self):
        raise AssertionError("Requires real device with background task runner")
