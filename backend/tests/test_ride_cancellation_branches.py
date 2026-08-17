"""Targeted branch coverage for routes/rides/cancellation.py.

Complements the existing happy-path cancellation tests in
test_coverage_rides.py (searching / driver_arrived-fee), test_e2e_cancellation.py,
and test_p2_scheduled_rides.py. Covers guard clauses and fail-open paths those
don't reach: the request-body JSON parse fallback, the atomic-claim-lost 409,
the pre-auth-release fail-open, partial wallet fee collection, the driver
cancellation-fee payout call, the outer fee-computation fail-open, the
attribution-column write fallback, the post-write verification fail-open (both
the re-read exception and the silent-no-op 500), the batch pending-offer
cleanup loop, and the scheduled-ride cancel's not-found/attribution-fallback/
claim-lost branches.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_RIDER_ID = "rider-cxl-1"
_DRIVER_ID = "drv-cxl-1"
_RIDE_ID = "ride-cxl-1"
_USER = {"id": _RIDER_ID}


def _ride(status="searching", **kw):
    base = {
        "id": _RIDE_ID,
        "rider_id": _RIDER_ID,
        "driver_id": None,
        "status": status,
        "payment_method": "card",
        "payment_intent_id": None,
        "auth_status": None,
        "service_area_id": None,
        "is_scheduled": False,
    }
    base.update(kw)
    return base


def _starlette_request(method="POST", path="/rides/x/cancel", body: bytes = b""):
    from starlette.requests import Request as SR

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"content-length", str(len(body)).encode())],
        "query_string": b"",
        "root_path": "",
        "client": ("127.0.0.1", 9999),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return SR(scope, receive)


def _base_patches(mock_db, mock_supabase, mock_manager, cancelled_ride):
    mock_supabase.get_app_settings = AsyncMock(return_value={})
    mock_supabase.update_ride = AsyncMock(return_value=None)
    mock_supabase.get_ride = AsyncMock(return_value=cancelled_ride)
    mock_supabase.set_driver_available = AsyncMock(return_value=None)
    mock_manager.send_personal_message = AsyncMock()
    mock_manager.broadcast_ride_status = AsyncMock()
    mock_manager.broadcast_to_admins = AsyncMock()
    mock_supabase.run_sync = AsyncMock(return_value=MagicMock(data=[]))


async def test_body_reason_json_parse_failure_falls_back_to_query_reason():
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching")
    cancelled = _ride(status="cancelled")
    # A request with declared content-length but a receive() that raises —
    # request.json() blows up, exercising the except-and-fall-back-to-"" path.
    req = _starlette_request(body=b"not json but has length")

    async def _broken_receive():
        raise RuntimeError("body read failed")

    req._receive = _broken_receive

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="query-reason", current_user=_USER)

    assert result["success"] is True
    # Falls back to the ?reason= query param when the body can't be parsed.
    mock_supabase.update_ride.assert_awaited()
    _, attribution_payload = mock_supabase.update_ride.await_args_list[0].args
    assert attribution_payload["cancellation_reason"] == "query-reason"


async def test_cancel_claim_lost_to_race_returns_409():
    from fastapi import HTTPException

    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching")
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        # Atomic claim finds zero rows -- ride left the pre-trip states
        # between the read and the write (driver started the trip).
        mock_supabase.update_one = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert exc_info.value.status_code == 409


async def test_preauth_release_failure_is_non_fatal():
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching", payment_intent_id="pi_123", auth_status="authorized")
    cancelled = _ride(status="cancelled", payment_intent_id="pi_123", auth_status="released")
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch(
            "backend.routes.rides.cancellation._deps.cancel_authorization",
            AsyncMock(side_effect=RuntimeError("stripe down")),
        ),
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    # Still non-fatal — that part of this test's purpose is unchanged. A Stripe
    # failure is logged loudly for reconciliation but must not block the cancel.
    assert result["success"] is True

    # ...but the ride must NOT be marked auth_status="released".
    #
    # This assertion used to expect "released", reasoning that the hold had been
    # "attempted-released". That was wrong, and actively harmful: the release
    # FAILED, so the authorization is still open on the rider's card. Both
    # orphaned_hold_reconciler and card_hold_release select on OPEN_AUTH_STATES
    # ("authorized"/"fare_only"), so writing "released" removes the ride from the
    # only sweepers that could ever find that hold — stranding the rider's funds
    # until Stripe's ~7-day expiry, which is precisely what those sweepers exist
    # to prevent. auth_status is a record of what happened, not of what we tried.
    _, attribution_payload = mock_supabase.update_ride.await_args_list[0].args
    assert attribution_payload.get("auth_status") != "released"


async def test_preauth_release_success_marks_auth_released():
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching", payment_intent_id="pi_123", auth_status="fare_only")
    cancelled = _ride(status="cancelled")
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch(
            "backend.routes.rides.cancellation._deps.cancel_authorization",
            AsyncMock(return_value=True),
        ),
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert result["success"] is True
    _, attribution_payload = mock_supabase.update_ride.await_args_list[0].args
    assert attribution_payload["auth_status"] == "released"


async def test_wallet_fee_partial_collection_is_logged_not_fatal():
    from backend.routes.rides.cancellation import cancel_ride_rider

    arrived = _ride(status="driver_arrived", driver_id=_DRIVER_ID, payment_method="wallet")
    cancelled = _ride(status="cancelled", driver_id=_DRIVER_ID)
    wallet = {"id": "wallet-1", "balance": 1.0}
    driver = {"id": _DRIVER_ID, "user_id": "drv-user-1"}

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch(
            "backend.routes.rides.cancellation._deps.calculate_cancellation_fee",
            return_value=(Decimal("3.00"), Decimal("2.00")),
        ),
        patch("backend.routes.rides.cancellation._deps.pay_driver_cancellation_fee", AsyncMock()) as mock_pay_driver,
        patch("backend.routes.rides.cancellation._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(side_effect=[arrived, wallet])
        mock_supabase.find_one = AsyncMock(return_value=wallet)
        mock_supabase.get_driver_by_id = AsyncMock(return_value=driver)
        # Only $1 of the $5 total fee was actually collectable from the wallet.
        mock_supabase.wallet_apply_delta = AsyncMock(return_value={"applied_delta": "-1.00"})
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)

        result = await cancel_ride_rider(request=req_arg(), ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert result["success"] is True
    # pay_driver_cancellation_fee still called with the full driver share.
    mock_pay_driver.assert_awaited_once()


def req_arg():
    return _starlette_request()


async def test_fee_computation_failure_still_releases_driver():
    from backend.routes.rides.cancellation import cancel_ride_rider

    arrived = _ride(status="driver_arrived", driver_id=_DRIVER_ID)
    cancelled = _ride(status="cancelled", driver_id=_DRIVER_ID)
    driver = {"id": _DRIVER_ID, "user_id": "drv-user-1"}
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch(
            "backend.routes.rides.cancellation._deps.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
        patch("backend.routes.rides.cancellation._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=arrived)
        mock_supabase.update_ride = AsyncMock(return_value=None)
        mock_supabase.get_ride = AsyncMock(return_value=cancelled)
        mock_supabase.set_driver_available = AsyncMock(return_value=None)
        mock_supabase.get_driver_by_id = AsyncMock(return_value=driver)
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()
        mock_manager.broadcast_to_admins = AsyncMock()
        mock_supabase.run_sync = AsyncMock(return_value=MagicMock(data=[]))
        mock_supabase.update_one = AsyncMock(return_value=cancelled)

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert result["success"] is True
    # Fee computation blew up (charged_admin/driver default to 0) but the
    # driver must still be released and notified.
    mock_supabase.set_driver_available.assert_awaited_once_with(_DRIVER_ID, True)


async def test_attribution_write_falls_back_to_minimal_on_missing_column():
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching")
    cancelled = _ride(status="cancelled")
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.update_ride = AsyncMock(
            side_effect=[RuntimeError("column cancelled_by does not exist (PGRST204)"), None]
        )

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert result["success"] is True
    assert mock_supabase.update_ride.await_count == 2


async def test_verify_reread_exception_still_flags_silent_no_op():
    from fastapi import HTTPException

    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching")
    cancelled = _ride(status="cancelled")
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.update_ride = AsyncMock(return_value=None)
        # The post-write verification re-read blows up -- verify_ride stays
        # None, which must still trip the "did not persist" 500, not crash
        # the request with an unhandled exception.
        mock_supabase.get_ride = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(HTTPException) as exc_info:
            await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert exc_info.value.status_code == 500


async def test_batch_pending_offers_cancelled_and_drivers_released():
    from backend.routes.rides.cancellation import cancel_ride_rider

    # Batch dispatch: no driver_id on the ride, offers live in ride_offers.
    searching = _ride(status="searching", driver_id=None)
    cancelled = _ride(status="cancelled", driver_id=None)
    offer_driver = {"id": "offer-drv-1", "user_id": "offer-user-1"}
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.set_driver_available = AsyncMock(return_value=None)
        mock_supabase.get_driver_by_id = AsyncMock(return_value=offer_driver)
        mock_supabase.run_sync = AsyncMock(
            side_effect=[MagicMock(data=[{"driver_id": "offer-drv-1"}]), MagicMock(data=[])]
        )

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert result["success"] is True
    mock_supabase.set_driver_available.assert_awaited_once_with("offer-drv-1", True)


async def test_batch_pending_offer_driver_also_gets_push_not_just_ws():
    """N5 follow-up (ACTION_ITEMS.md): the batch-dispatch pending-offers path
    had the same WS-only gap the assigned-driver case was fixed for -- a
    driver with a pending offer on this ride, backgrounded when the rider
    cancels, never learned the ride vanished. Mirrors
    test_driver_arrived_also_pushes_driver_not_just_ws's patch/assert shape
    (test_e2e_cancellation.py): patch send_push_notification directly and
    let the real spawn() run the fire-and-forget task, instead of stubbing
    spawn to close() the coroutine like the sibling tests in this file do.
    """
    import asyncio

    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching", driver_id=None)
    cancelled = _ride(status="cancelled", driver_id=None)
    offer_driver = {"id": "offer-drv-1", "user_id": "offer-user-1"}
    req = _starlette_request()

    push_mock = AsyncMock()
    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.send_push_notification", push_mock),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.set_driver_available = AsyncMock(return_value=None)
        mock_supabase.get_driver_by_id = AsyncMock(return_value=offer_driver)
        mock_supabase.run_sync = AsyncMock(
            side_effect=[MagicMock(data=[{"driver_id": "offer-drv-1"}]), MagicMock(data=[])]
        )

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)
        # The push is fired via spawn() (fire-and-forget) -- yield once so
        # the scheduled task actually runs before asserting on it.
        await asyncio.sleep(0)

    assert result["success"] is True
    push_mock.assert_awaited_once()
    call = push_mock.await_args
    assert call.args[0] == "offer-user-1"
    assert call.kwargs.get("priority") == "dispatch"
    assert call.kwargs.get("target_app") == "driver"
    assert call.kwargs.get("data", {}).get("type") == "ride_cancelled"


async def test_batch_offer_cleanup_failure_is_non_fatal():
    from backend.routes.rides.cancellation import cancel_ride_rider

    searching = _ride(status="searching", driver_id=None)
    cancelled = _ride(status="cancelled", driver_id=None)
    req = _starlette_request()

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_db.find_one = AsyncMock(return_value=searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        mock_supabase.update_one = AsyncMock(return_value=cancelled)
        mock_supabase.run_sync = AsyncMock(side_effect=RuntimeError("ride_offers query failed"))

        result = await cancel_ride_rider(request=req, ride_id=_RIDE_ID, reason="", current_user=_USER)

    assert result["success"] is True


# ── cancel_scheduled_ride ────────────────────────────────────────────────────


async def test_cancel_scheduled_attribution_write_falls_back_on_missing_column():
    from backend.routes.rides.cancellation import cancel_scheduled_ride

    scheduled = _ride(status="scheduled", is_scheduled=True)
    req = _starlette_request(path="/rides/scheduled/x")

    with (
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
    ):
        mock_supabase.get_rows = AsyncMock(return_value=[scheduled])
        mock_supabase.update_one = AsyncMock(
            side_effect=[RuntimeError("column cancellation_type does not exist (PGRST204)"), scheduled]
        )
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()

        result = await cancel_scheduled_ride(ride_id=_RIDE_ID, request=req, current_user=_USER)

    assert result["success"] is True
    assert mock_supabase.update_one.await_count == 2


async def test_cancel_scheduled_claim_lost_falls_through_to_live_cancel_path():
    from backend.routes.rides.cancellation import cancel_scheduled_ride

    scheduled = _ride(status="scheduled", is_scheduled=True)
    now_searching = _ride(status="searching", is_scheduled=True)
    cancelled = _ride(status="cancelled", is_scheduled=True)
    req = _starlette_request(path="/rides/scheduled/x")

    with (
        patch("backend.routes.rides.cancellation._deps.db") as mock_db,
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.spawn", side_effect=lambda coro: coro.close()),
    ):
        mock_supabase.get_rows = AsyncMock(return_value=[scheduled])
        # Zero rows: the dispatch loop won the race between the read and claim.
        mock_supabase.update_one = AsyncMock(side_effect=[None, cancelled])
        mock_db.find_one = AsyncMock(return_value=now_searching)
        _base_patches(mock_db, mock_supabase, mock_manager, cancelled)
        # _base_patches sets a fixed get_ride return; override AFTER it so
        # the fall-through re-read (now live + searching) wins, and the
        # nested cancel_ride_rider's own verification re-read gets "cancelled".
        mock_supabase.get_ride = AsyncMock(side_effect=[now_searching, cancelled])

        result = await cancel_scheduled_ride(ride_id=_RIDE_ID, request=req, current_user=_USER)

    assert result["success"] is True


async def test_cancel_scheduled_charges_notice_window_fee_when_enabled():
    """Finding #01: a pre-dispatch scheduled cancel inside the notice window,
    with the flag on, must charge the rider's card via charge_ancillary_fee."""
    from datetime import datetime, timedelta, timezone

    from backend.routes.rides.cancellation import cancel_scheduled_ride

    soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    scheduled = _ride(status="scheduled", is_scheduled=True, scheduled_time=soon, payment_method="card")
    req = _starlette_request(path="/rides/scheduled/x")

    charge_mock = AsyncMock(return_value=MagicMock(status="succeeded", payment_intent_id="pi_notice_1"))
    with (
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch(
            "backend.routes.rides.cancellation._deps.get_app_settings",
            AsyncMock(
                return_value={
                    "scheduled_ride_notice_window_fee_enabled": True,
                    "scheduled_ride_notice_window_minutes": 60,
                    "scheduled_ride_notice_window_fee_amount": "3.00",
                }
            ),
        ),
        patch("backend.routes.rides.cancellation._deps.charge_ancillary_fee", charge_mock),
        patch(
            "backend.routes.rides.cancellation._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"stripe_customer_id": "cus_notice", "default_payment_method": "pm_notice"}),
        ),
        patch(
            "backend.routes.rides.cancellation._deps.record_ledger_event",
            AsyncMock(return_value="evt_notice_1"),
        ) as ledger_mock,
    ):
        mock_supabase.get_rows = AsyncMock(return_value=[scheduled])
        mock_supabase.update_one = AsyncMock(return_value=scheduled)
        mock_supabase.insert_one = AsyncMock()
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()

        result = await cancel_scheduled_ride(ride_id=_RIDE_ID, request=req, current_user=_USER)

    assert result["success"] is True
    charge_mock.assert_awaited_once()
    assert charge_mock.await_args.kwargs["amount"] == Decimal("3.00")
    assert charge_mock.await_args.kwargs["fee_type"] == "scheduled_cancel_notice_fee"
    # The succeeded charge must leave a ledger trail via the durable writer
    # (previously this write escaped the test's mocks into the real
    # ledger binding — now asserted explicitly).
    ledger_mock.assert_awaited_once()
    lk = ledger_mock.call_args.kwargs
    assert lk["event_type"] == "stripe_charge"
    assert lk["delta_cents"] == 300
    assert lk["ref"] == "pi_notice_1"
    assert lk["metadata"] == {"source": "scheduled_cancel_notice_fee"}


async def test_cancel_scheduled_no_fee_charge_attempted_when_flag_disabled():
    """Default (flag off): the cancel succeeds and charge_ancillary_fee is
    never called -- confirms the new code path is a true no-op when the
    feature isn't explicitly enabled."""
    from datetime import datetime, timedelta, timezone

    from backend.routes.rides.cancellation import cancel_scheduled_ride

    soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    scheduled = _ride(status="scheduled", is_scheduled=True, scheduled_time=soon, payment_method="card")
    req = _starlette_request(path="/rides/scheduled/x")

    charge_mock = AsyncMock()
    with (
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch("backend.routes.rides.cancellation._deps.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.cancellation._deps.charge_ancillary_fee", charge_mock),
    ):
        mock_supabase.get_rows = AsyncMock(return_value=[scheduled])
        mock_supabase.update_one = AsyncMock(return_value=scheduled)
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()

        result = await cancel_scheduled_ride(ride_id=_RIDE_ID, request=req, current_user=_USER)

    assert result["success"] is True
    charge_mock.assert_not_awaited()


async def test_cancel_scheduled_fee_charge_failure_does_not_block_cancellation():
    """A fee-charge exception must never undo an already-persisted
    cancellation -- the cancel succeeds regardless of the fee outcome."""
    from datetime import datetime, timedelta, timezone

    from backend.routes.rides.cancellation import cancel_scheduled_ride

    soon = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    scheduled = _ride(status="scheduled", is_scheduled=True, scheduled_time=soon, payment_method="card")
    req = _starlette_request(path="/rides/scheduled/x")

    with (
        patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase,
        patch("backend.routes.rides.cancellation._deps.manager") as mock_manager,
        patch(
            "backend.routes.rides.cancellation._deps.get_app_settings",
            AsyncMock(side_effect=RuntimeError("settings db down")),
        ),
    ):
        mock_supabase.get_rows = AsyncMock(return_value=[scheduled])
        mock_supabase.update_one = AsyncMock(return_value=scheduled)
        mock_manager.send_personal_message = AsyncMock()
        mock_manager.broadcast_ride_status = AsyncMock()

        # Must not raise -- the cancellation itself already succeeded.
        result = await cancel_scheduled_ride(ride_id=_RIDE_ID, request=req, current_user=_USER)

    assert result["success"] is True


async def test_cancel_scheduled_claim_lost_ride_now_terminal_raises_400():
    from backend.routes.rides.cancellation import cancel_scheduled_ride
    from backend.utils.error_handling import SpinrException

    scheduled = _ride(status="scheduled", is_scheduled=True)
    completed = _ride(status="completed", is_scheduled=True)
    req = _starlette_request(path="/rides/scheduled/x")

    with patch("backend.routes.rides.cancellation._deps.db_supabase") as mock_supabase:
        mock_supabase.get_rows = AsyncMock(return_value=[scheduled])
        mock_supabase.update_one = AsyncMock(return_value=None)
        # Someone else completed the ride in the race window.
        mock_supabase.get_ride = AsyncMock(return_value=completed)

        with pytest.raises(SpinrException) as exc_info:
            await cancel_scheduled_ride(ride_id=_RIDE_ID, request=req, current_user=_USER)

    assert exc_info.value.status_code == 400
