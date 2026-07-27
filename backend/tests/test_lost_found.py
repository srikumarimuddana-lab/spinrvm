"""Coverage for routes/rides/lost_found.py — rider lost-item reports.

Part of ACTION_ITEMS.md A1 (per-module coverage floors): lost_found.py was
at 25% coverage, the worst-covered file in the routes/rides/ package.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_lost_found_1"
OTHER_RIDER_ID = "rider_lost_found_2"
DRIVER_ID = "driver_lost_found_1"
DRIVER_USER_ID = "driver_user_lost_found_1"
RIDE_ID = "ride_lost_found_001"


def _ride(status: str = "completed", driver_id: str | None = DRIVER_ID, **extra) -> dict:
    row = {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "driver_id": driver_id,
        "status": status,
    }
    row.update(extra)
    return row


def _driver(user_id: str | None = DRIVER_USER_ID) -> dict:
    return {"id": DRIVER_ID, "user_id": user_id, "name": "Test Driver"}


def _unwrapped():
    from backend.routes.rides import lost_found as lost_found_mod

    fn = getattr(
        lost_found_mod.rider_report_lost_item,
        "__wrapped__",
        lost_found_mod.rider_report_lost_item,
    )
    return lost_found_mod, fn


class _Req:
    item_description = "Blue backpack left on the back seat"
    item_category = "bag"


@pytest.mark.anyio
class TestRiderReportLostItem:
    async def test_ride_not_found_returns_404(self):
        mod, fn = _unwrapped()
        with patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=None)):
            with pytest.raises(mod.HTTPException) as exc:
                await fn(
                    ride_id=RIDE_ID,
                    req=_Req(),
                    request=MagicMock(),
                    current_user={"id": RIDER_ID},
                )
        assert exc.value.status_code == 404

    async def test_wrong_rider_returns_403(self):
        mod, fn = _unwrapped()
        ride = _ride()
        with patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(mod.HTTPException) as exc:
                await fn(
                    ride_id=RIDE_ID,
                    req=_Req(),
                    request=MagicMock(),
                    current_user={"id": OTHER_RIDER_ID},
                )
        assert exc.value.status_code == 403

    async def test_non_completed_ride_returns_400(self):
        mod, fn = _unwrapped()
        ride = _ride(status="in_progress")
        with patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(mod.HTTPException) as exc:
                await fn(
                    ride_id=RIDE_ID,
                    req=_Req(),
                    request=MagicMock(),
                    current_user={"id": RIDER_ID},
                )
        assert exc.value.status_code == 400
        assert "completed" in exc.value.detail.lower()

    async def test_no_driver_assigned_returns_400(self):
        mod, fn = _unwrapped()
        ride = _ride(driver_id=None)
        with patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)):
            with pytest.raises(mod.HTTPException) as exc:
                await fn(
                    ride_id=RIDE_ID,
                    req=_Req(),
                    request=MagicMock(),
                    current_user={"id": RIDER_ID},
                )
        assert exc.value.status_code == 400
        assert "driver" in exc.value.detail.lower()

    async def test_invalid_category_falls_back_to_other(self):
        mod, fn = _unwrapped()
        ride = _ride()
        created = {"id": "item_1"}

        class BadCategoryReq:
            item_description = "A hat"
            item_category = "not-a-real-category"

        with (
            patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(mod._deps.db_supabase, "create_lost_and_found", AsyncMock(return_value=created)) as create_mock,
            patch.object(mod._deps.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
        ):
            result = await fn(
                ride_id=RIDE_ID,
                req=BadCategoryReq(),
                request=MagicMock(),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert create_mock.call_args[0][0]["item_category"] == "other"

    async def test_valid_category_is_preserved(self):
        mod, fn = _unwrapped()
        ride = _ride()
        created = {"id": "item_1"}

        with (
            patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(mod._deps.db_supabase, "create_lost_and_found", AsyncMock(return_value=created)) as create_mock,
            patch.object(mod._deps.db_supabase, "get_driver_by_id", AsyncMock(return_value=None)),
        ):
            result = await fn(
                ride_id=RIDE_ID,
                req=_Req(),
                request=MagicMock(),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert create_mock.call_args[0][0]["item_category"] == "bag"

    async def test_notifies_driver_and_marks_notified(self):
        mod, fn = _unwrapped()
        ride = _ride()
        created = {"id": "item_1"}
        driver = _driver()
        driver_user = {"id": DRIVER_USER_ID}

        with (
            patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(mod._deps.db_supabase, "create_lost_and_found", AsyncMock(return_value=created)),
            patch.object(mod._deps.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
            patch.object(mod._deps.db_supabase, "get_user_by_id", AsyncMock(return_value=driver_user)),
            patch.object(mod._deps, "send_push_notification", AsyncMock()) as push_mock,
            patch.object(mod._deps.db_supabase, "update_lost_and_found", AsyncMock()) as update_mock,
        ):
            result = await fn(
                ride_id=RIDE_ID,
                req=_Req(),
                request=MagicMock(),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        push_mock.assert_awaited_once()
        assert push_mock.call_args[0][0] == DRIVER_USER_ID
        update_mock.assert_awaited_once()
        assert update_mock.call_args[0][0] == "item_1"
        assert update_mock.call_args[0][1]["status"] == "driver_notified"

    async def test_driver_without_user_id_skips_notification(self):
        mod, fn = _unwrapped()
        ride = _ride()
        created = {"id": "item_1"}
        driver = _driver(user_id=None)

        with (
            patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(mod._deps.db_supabase, "create_lost_and_found", AsyncMock(return_value=created)),
            patch.object(mod._deps.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
            patch.object(mod._deps, "send_push_notification", AsyncMock()) as push_mock,
            patch.object(mod._deps.db_supabase, "update_lost_and_found", AsyncMock()) as update_mock,
        ):
            result = await fn(
                ride_id=RIDE_ID,
                req=_Req(),
                request=MagicMock(),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        push_mock.assert_not_awaited()
        update_mock.assert_not_awaited()

    async def test_missing_driver_user_row_skips_notification(self):
        mod, fn = _unwrapped()
        ride = _ride()
        created = {"id": "item_1"}
        driver = _driver()

        with (
            patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(mod._deps.db_supabase, "create_lost_and_found", AsyncMock(return_value=created)),
            patch.object(mod._deps.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
            patch.object(mod._deps.db_supabase, "get_user_by_id", AsyncMock(return_value=None)),
            patch.object(mod._deps, "send_push_notification", AsyncMock()) as push_mock,
        ):
            result = await fn(
                ride_id=RIDE_ID,
                req=_Req(),
                request=MagicMock(),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        push_mock.assert_not_awaited()

    async def test_notification_failure_is_caught_and_report_still_succeeds(self):
        """CLAUDE.md's fail-closed error-handling rule applies to core state
        transitions, not to a best-effort side notification — this endpoint's
        own try/except intentionally treats notification failure as
        non-fatal, since the lost-item report itself already succeeded."""
        mod, fn = _unwrapped()
        ride = _ride()
        created = {"id": "item_1"}
        driver = _driver()

        with (
            patch.object(mod._deps.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(mod._deps.db_supabase, "create_lost_and_found", AsyncMock(return_value=created)),
            patch.object(
                mod._deps.db_supabase,
                "get_driver_by_id",
                AsyncMock(side_effect=RuntimeError("db unavailable")),
            ),
        ):
            result = await fn(
                ride_id=RIDE_ID,
                req=_Req(),
                request=MagicMock(),
                current_user={"id": RIDER_ID},
            )

        assert result["success"] is True
        assert result["item"] == created
