from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest


def scheduled(**extra):
    # `now` must be computed fresh on every call, not once at module import:
    # pytest imports every test module up front before running any test body,
    # and this file sits ~744th of 1500+ files in collection order, so a
    # module-level `NOW` constant goes stale by several minutes of real
    # wall-clock time by the time these tests actually execute against the
    # production code's own live `datetime.now()` calls -- see
    # docs/change-log/2026-09-20-scheduled-timing-guards-frozen-now.md.
    now = datetime.now(timezone.utc)
    return {
        "id": "ride-1",
        "rider_id": "rider-1",
        "driver_id": "driver-1",
        "is_scheduled": True,
        "status": "searching",
        "scheduled_time": (now + timedelta(minutes=5)).isoformat(),
        "ride_requested_at": (now - timedelta(minutes=10)).isoformat(),
        **extra,
    }


@pytest.mark.anyio
async def test_scheduled_retry_continues_after_normal_attempt_limit():
    from backend.routes.rides import matching as m

    with (
        patch.object(m.asyncio, "sleep", AsyncMock()),
        patch.object(m._deps.db_supabase, "get_ride", AsyncMock(return_value=scheduled())),
        patch.object(m, "match_driver_to_ride", AsyncMock()) as match,
    ):
        await m._dispatch_retry("ride-1", attempt=40)
    match.assert_awaited_once()


@pytest.mark.anyio
async def test_timeout_waits_until_pickup_grace_and_rechecks():
    from backend.routes.rides import matching as m

    with (
        patch.object(m.asyncio, "sleep", AsyncMock()) as sleep,
        patch.object(
            m._deps.db_supabase, "get_ride", AsyncMock(side_effect=[scheduled(), scheduled(status="driver_accepted")])
        ),
        patch.object(m._deps.db_supabase, "update_one", AsyncMock()) as claim,
    ):
        await m.ride_search_timeout("ride-1")
    assert sleep.await_count == 2 and sleep.await_args.args[0] > 500
    claim.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("won", [False, True])
async def test_timeout_claim_precedes_hold_release(won):
    from backend.routes.rides import matching as m
    from backend.utils import card_hold_release

    row = scheduled(
        scheduled_time=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        auth_status="authorized",
        payment_intent_id="pi_test",
    )
    with (
        patch.object(m.asyncio, "sleep", AsyncMock()),
        patch.object(m._deps.db_supabase, "get_ride", AsyncMock(return_value=row)),
        patch.object(m._deps.db_supabase, "update_one", AsyncMock(return_value=row if won else None)) as claim,
        patch.object(m._deps.db_supabase, "update_ride", AsyncMock()) as update,
        patch.object(card_hold_release, "release_open_hold", AsyncMock()) as release,
        patch.object(m._deps, "cancel_authorization", AsyncMock()) as old_release,
    ):
        await m.ride_search_timeout("ride-1")
    assert claim.await_args.args[1]["status"] == "searching"
    assert release.await_count == int(won)
    update.assert_not_awaited()
    old_release.assert_not_awaited()
