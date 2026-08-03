# backend/tests/test_corporate_member_offboarding_service_coverage.py
"""Coverage top-up for corporate_member_offboarding_service.py (79% -> target
~100%). Companion to test_corporate_member_offboarding_service.py, which
already covers the happy path, the atomic-claim race skip, the `scheduled`
inclusion fix (Finding #16), and best-effort per-ride isolation. This file
targets the remaining gaps:

  * lines 19-21  -- the *successful* half of the dual try/except import at
    module top -- only executes when the module is imported via its fully
    qualified ``backend.`` package path (relative imports resolve). The
    existing test file imports it bare (``services.corporate_member_offboarding_service``),
    which makes ``from .. import db_supabase`` raise "attempted relative
    import beyond top-level package" immediately (package ``services`` has
    no parent), so lines 19-21 never even get *attempted* under that import
    style -- only the except block (lines 23-26) runs.
  * lines 131-132 -- push-notification failure is swallowed (best-effort).
  * lines 135-142 -- the guest_booking branch: success and failure paths.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 4): a bug
found while reading the source — the driver/rider WebSocket sends were not
wrapped in try/except (unlike the push/SMS notifies right after them), so a
WS blip after a successful DB cancel made the caller under-count the
cancellation and log a misleading "failed to cancel" message. Both WS
sends are now wrapped, matching the existing push/SMS pattern — see
test_ws_notify_failure_still_counts_despite_committed_cancellation below.
"""

import importlib
import sys
from unittest.mock import AsyncMock, patch

import pytest

from backend.tests._factories import driver_row, ride_row
from services import corporate_member_offboarding_service as svc


@pytest.mark.unit
def test_qualified_import_path_covers_the_relative_import_try_block():
    """Import the module via its fully-qualified ``backend.`` path so that
    the relative imports on lines 18-21 (``from .. import db_supabase`` etc.)
    actually resolve and succeed, rather than raising "attempted relative
    import beyond top-level package" the way the bare ``services.*`` import
    style used elsewhere in this file does. This exercises lines 19-21,
    which are otherwise never reached by the rest of the suite.

    ``importlib.reload`` is used (rather than a bare ``import``) so the
    module-level try/except body actually re-executes even if some other
    test already warmed ``backend.services.corporate_member_offboarding_service``
    into ``sys.modules`` earlier in the run.
    """
    mod_name = "backend.services.corporate_member_offboarding_service"
    qualified = importlib.import_module(mod_name)
    reloaded = importlib.reload(qualified)

    assert reloaded is sys.modules[mod_name]
    # Sanity: the qualified module is a fully independent object from the
    # bare-imported ``svc`` used by the rest of this suite, but exposes the
    # same public API.
    assert callable(reloaded.cancel_pre_pickup_rides_for_member)
    assert reloaded._PRE_PICKUP_STATUSES == svc._PRE_PICKUP_STATUSES


@pytest.mark.unit
@pytest.mark.anyio
async def test_push_notification_failure_is_swallowed_and_ride_still_counts():
    """Lines 131-132: send_push_notification raising must not stop the ride
    from being counted as cancelled -- it's a best-effort side channel, the
    WS message is the primary rider notification."""
    ride = ride_row(status="driver_assigned", driver_id=None, rider_id="rider-1", corporate_member_id="m1")

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.manager, "send_personal_message", AsyncMock()) as mock_ws,
        patch.object(svc, "send_push_notification", AsyncMock(side_effect=RuntimeError("fcm down"))) as mock_push,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 1
    mock_ws.assert_awaited_once()
    mock_push.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_guest_booking_success_notifies_guest_by_sms():
    """Lines 135-140: guest_booking rides also get an SMS notify via the
    guest_notification_service (dual-imported inline, bare-style resolves
    here since svc itself loaded bare)."""
    ride = ride_row(
        status="searching",
        driver_id=None,
        rider_id="rider-1",
        corporate_member_id="m1",
        guest_booking=True,
    )

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
        patch(
            "services.guest_notification_service.notify_guest_cancelled",
            AsyncMock(),
            create=True,
        ) as mock_guest_notify,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 1
    mock_guest_notify.assert_awaited_once()
    # Passed a plain dict copy of the ride, not the original object.
    (passed_ride,), _ = mock_guest_notify.await_args
    assert passed_ride["id"] == ride["id"]
    assert passed_ride is not ride


@pytest.mark.unit
@pytest.mark.anyio
async def test_guest_booking_sms_failure_is_swallowed_and_ride_still_counts():
    """Lines 141-142: a guest SMS notify failure must not stop the ride from
    counting as cancelled -- same best-effort contract as the push branch."""
    ride = ride_row(
        status="searching",
        driver_id=None,
        rider_id="rider-1",
        corporate_member_id="m1",
        guest_booking=True,
    )

    with (
        patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=[ride])),
        patch.object(svc.db_supabase, "update_one", AsyncMock(return_value={"id": ride["id"]})),
        patch.object(svc.manager, "send_personal_message", AsyncMock()),
        patch.object(svc, "send_push_notification", AsyncMock()),
        patch(
            "services.guest_notification_service.notify_guest_cancelled",
            AsyncMock(side_effect=RuntimeError("sms provider down")),
            create=True,
        ) as mock_guest_notify,
    ):
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    assert cancelled == 1
    mock_guest_notify.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.anyio
async def test_ws_notify_failure_still_counts_despite_committed_cancellation():
    """Fixed (2026-08-03): both `manager.send_personal_message` calls
    (driver and rider) are now wrapped in their own try/except, matching
    the existing push/SMS pattern. A transient WS failure no longer aborts
    the rest of `_cancel_one_ride` — the ride is still counted as
    cancelled and the rider still gets their push notification."""
    ride = ride_row(status="driver_assigned", driver_id="d1", rider_id="rider-1", corporate_member_id="m1")
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
        cancelled = await svc.cancel_pre_pickup_rides_for_member("c1", "m1")

    # The DB write to `cancelled` and the driver release both happened...
    mock_update.assert_awaited_once()
    write_payload = mock_update.await_args.args[2]
    assert write_payload["status"] == "cancelled"
    mock_set_avail.assert_awaited_once_with("d1", True)
    mock_period.assert_awaited_once_with("d1", 1)
    # ...and now the WS blip no longer masks that: the ride is still
    # counted, and the rider still gets their push notification.
    assert cancelled == 1
    mock_push.assert_awaited_once()
