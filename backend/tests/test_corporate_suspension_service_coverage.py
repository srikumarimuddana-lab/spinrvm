# backend/tests/test_corporate_suspension_service_coverage.py
"""Coverage top-up for corporate_suspension_service.py.

Companion to test_corporate_suspension_service.py, which already covers the
happy path, the no-driver / scheduled-ride variants, the pre-trip race
guard, and the pre-pickup status filter itself. This file targets the
remaining uncovered branches (per coverage report: lines 128-129, 132-139):

  * push-notification failure to the rider must be swallowed and logged,
    not allowed to abort the rest of the per-ride cleanup (line 128-129)
  * guest_booking rides additionally fire an SMS via
    guest_notification_service.notify_guest_cancelled (lines 132-137)
  * a failure in that guest SMS path must also be swallowed and logged,
    not propagate up to cancel_pre_pickup_rides_for_company's per-ride
    try/except in a way that would mis-flag the ride as "not cancelled"
    (lines 138-139)

Lines 18-20 are the module's `except ImportError:` fallback bare-import
branch (only exercised when the module is loaded outside the `backend.`
package, e.g. via `python -m backend.server`'s bare sys.path setup) — not
reachable from a `from services import corporate_suspension_service`-style
test import and intentionally left uncovered, consistent with how the
sibling coverage file for corporate_member_offboarding_service treats the
equivalent lines.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 4):
`_cancel_one_ride` previously wrapped the rider push notification in
try/except but NOT the driver/rider WebSocket sends — a WS blip there
propagated out of `_cancel_one_ride`, was caught by the outer per-ride
try/except in `cancel_pre_pickup_rides_for_company`, and the ride was
logged as "failed to cancel" / not counted in `cancelled_count`, even
though the DB row was already durably flipped to `cancelled`. Both WS
sends are now wrapped in their own try/except, matching the existing
push/SMS pattern. See
`test_ws_send_failure_after_db_cancel_still_counts_and_flips_ride` below.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.tests._factories import driver_row, ride_row
from services import corporate_suspension_service as svc


@pytest.mark.unit
@pytest.mark.anyio
async def test_rider_push_notification_failure_is_swallowed_and_logged():
    """Line 128-129: send_push_notification raising must not stop the ride
    from being counted as cancelled, and must not propagate."""
    ride = ride_row(status="searching", driver_id=None, corporate_account_id="c1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.manager, "send_personal_message", AsyncMock()) as mock_ws,
        patch.object(svc, "send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down"))) as mock_push,
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()) as mock_guest,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_push.assert_awaited_once()
    # The rider WS message still went out before the push attempt.
    mock_ws.assert_awaited_once()
    # Not a guest booking -> guest SMS path is not touched.
    mock_guest.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_guest_booking_ride_sends_guest_cancelled_sms():
    """Lines 132-137: guest_booking=True triggers the lazy-imported
    notify_guest_cancelled with the full ride dict."""
    ride = ride_row(
        status="driver_assigned",
        driver_id="d1",
        corporate_account_id="c1",
        guest_booking=True,
    )
    driver = driver_row(id="d1", user_id="driver-user-1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()),
        patch.object(svc.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(svc, "record_period_transition", AsyncMock()),
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()) as mock_guest,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_guest.assert_awaited_once()
    (sent_ride,), _kwargs = mock_guest.await_args
    assert sent_ride["id"] == ride["id"]
    assert sent_ride["guest_booking"] is True


@pytest.mark.unit
@pytest.mark.anyio
async def test_guest_booking_false_skips_guest_notification():
    """guest_booking absent/False must not touch notify_guest_cancelled at
    all -- guards against the lazy import itself blowing up on a normal
    (non-guest) corporate ride."""
    ride = ride_row(status="searching", driver_id=None, corporate_account_id="c1", guest_booking=False)

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
        patch("backend.services.guest_notification_service.notify_guest_cancelled", AsyncMock()) as mock_guest,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_guest.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.anyio
async def test_guest_sms_notify_failure_is_swallowed_and_logged():
    """Lines 138-139: notify_guest_cancelled raising must not stop the ride
    from being counted as cancelled, and must not propagate to the caller's
    outer try/except (which would otherwise mis-log this as a cancel
    failure even though the DB row was already flipped)."""
    ride = ride_row(status="searching", driver_id=None, corporate_account_id="c1", guest_booking=True)

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
        patch(
            "backend.services.guest_notification_service.notify_guest_cancelled",
            AsyncMock(side_effect=RuntimeError("twilio down")),
        ) as mock_guest,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 1
    mock_guest.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_ws_send_failure_after_db_cancel_still_counts_and_flips_ride():
    """Fixed (2026-08-03): the WS sends to the driver and rider are now
    each wrapped in their own try/except, matching the existing push/SMS
    pattern. A WS blip no longer aborts the rest of `_cancel_one_ride` —
    the ride is still counted as cancelled and the rider still gets their
    push notification."""
    ride = ride_row(status="driver_assigned", driver_id="d1", corporate_account_id="c1")
    driver = driver_row(id="d1", user_id="driver-user-1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})) as mock_update,
        patch.object(svc.db_supabase, "set_driver_available", AsyncMock()) as mock_set_avail,
        patch.object(svc.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(svc, "record_period_transition", AsyncMock()) as mock_period,
        patch.object(svc.manager, "send_personal_message", AsyncMock(side_effect=RuntimeError("ws down"))),
        patch.object(svc, "send_push_notification", AsyncMock()) as mock_push,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    # The DB write and driver-side rollback both already happened...
    mock_update.assert_awaited_once()
    mock_set_avail.assert_awaited_once_with("d1", True)
    mock_period.assert_awaited_once_with("d1", 1)
    # ...and now the WS blip on the (uninvolved) send no longer masks that:
    # the ride is still counted, and the rider still gets their push.
    assert cancelled == 1
    mock_push.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_empty_candidates_returns_zero_without_touching_downstream():
    """get_rows returning an empty list (or None) must short-circuit to
    zero without calling update_one at all."""
    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=None)),
        patch.object(svc.db_supabase, "update_one", AsyncMock()) as mock_update,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_company("c1")

    assert cancelled == 0
    mock_update.assert_not_awaited()
