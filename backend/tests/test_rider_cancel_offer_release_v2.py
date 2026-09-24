"""Rider cancel releases v2 offers via resolve_driver_offer (T5-10)."""

from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests.test_ride_cancellation_branches import _RIDE_ID, _USER, _base_patches, _ride, _starlette_request

OFFER = "11111111-1111-4111-8111-111111111111"
CLAIM = "22222222-2222-4222-8222-222222222222"
V2_ROW = {"driver_id": "drv-v2", "id": OFFER, "claim_id": CLAIM, "online_epoch": 5, "status": "cancelled"}
LEGACY_ROW = {"driver_id": "drv-legacy", "id": "legacy-offer", "claim_id": None, "online_epoch": None}


async def _cancel_with_offers(rows, release_v2):
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching", driver_id=None)
    cancelled = _ride(status="cancelled", driver_id=None)
    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
        patch("backend.services.driver_offer_service.release_cancelled_offer_v2", release_v2),
        patch(
            "backend.routes.rides.cancellation._deps.release_batch_offer_driver_and_close_period", AsyncMock()
        ) as legacy_release,
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.get_driver_by_id = AsyncMock(return_value={"id": "x", "user_id": "u"})
        offer_query = mock_supabase.supabase.table.return_value.update.return_value
        offer_query.eq.return_value.eq.return_value.execute.return_value = MagicMock(data=rows)
        mock_supabase.run_sync = AsyncMock(side_effect=lambda fn: fn())
        result = await cancel_ride_rider(request=_starlette_request(), ride_id=_RIDE_ID, reason="", current_user=_USER)
    return result, legacy_release, mock_manager


async def test_v2_offer_released_through_rpc_and_legacy_offer_through_legacy_rpc():
    release_v2 = AsyncMock(return_value={"code": "OK", "released": True})
    result, legacy_release, manager = await _cancel_with_offers([V2_ROW, LEGACY_ROW], release_v2)
    assert result["success"] is True
    release_v2.assert_awaited_once_with(V2_ROW)
    legacy_release.assert_awaited_once_with("drv-legacy", ride_id=_RIDE_ID)
    cancelled_msgs = [
        c.args for c in manager.send_personal_message.await_args_list if c.args[0].get("type") == "ride_cancelled"
    ]
    assert len(cancelled_msgs) >= 2  # both offer drivers still told the ride is gone


async def test_v2_release_failure_still_notifies_driver_and_completes_cancel():
    release_v2 = AsyncMock(side_effect=RuntimeError("rpc down"))
    result, legacy_release, manager = await _cancel_with_offers([V2_ROW], release_v2)
    assert result["success"] is True
    legacy_release.assert_not_awaited()
    assert any(c.args[0].get("type") == "ride_cancelled" for c in manager.send_personal_message.await_args_list)
