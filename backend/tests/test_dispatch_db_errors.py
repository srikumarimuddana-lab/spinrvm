"""Dispatch must never swallow DB errors as "no drivers".

Regression tests for the finding: a transient Supabase failure during driver
matching was indistinguishable from an empty candidate pool, so the ride
drifted to the stuck-ride sweeper's auto-cancel instead of being retried
(CLAUDE.md: never soften DB/dispatch errors).

Covers:
  - repositories.driver_repo.match_and_claim_driver re-raises the typed
    DatabaseError instead of returning None (None strictly = no driver)
  - match_driver_to_ride schedules a _dispatch_retry when the candidate
    get_rows fetch raises, instead of letting the chain die
  - _dispatch_retry re-arms itself after a failed attempt (bounded by the
    attempt cap)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.utils.error_handling import DatabaseError

_RIDE = {
    "id": "ride_dbfail_1",
    "rider_id": "rider_dbfail_1",
    "vehicle_type_id": "economy",
    "pickup_lat": 52.13,
    "pickup_lng": -106.67,
    "dropoff_lat": 52.14,
    "dropoff_lng": -106.65,
    "status": "searching",
    "requires_wav": False,
}


@pytest.mark.asyncio
async def test_match_and_claim_driver_reraises_db_error():
    """RPC failure must propagate, not masquerade as 'no eligible driver'."""
    from backend.repositories import driver_repo

    def _boom(_fn):
        raise DatabaseError(details={"original": "connection reset"})

    with (
        patch.object(driver_repo, "supabase", MagicMock()),
        patch.object(driver_repo, "run_sync", AsyncMock(side_effect=_boom)),
    ):
        with pytest.raises(DatabaseError):
            await driver_repo.match_and_claim_driver("economy", 52.13, -106.67, 10.0)


@pytest.mark.asyncio
async def test_match_and_claim_driver_returns_none_only_for_no_driver():
    from backend.repositories import driver_repo

    with (
        patch.object(driver_repo, "supabase", MagicMock()),
        patch.object(driver_repo, "run_sync", AsyncMock(return_value=None)),
    ):
        assert await driver_repo.match_and_claim_driver("economy", 52.13, -106.67, 10.0) is None


@pytest.mark.asyncio
async def test_candidate_fetch_failure_schedules_retry():
    """A Supabase blip on the drivers fetch must re-arm dispatch, not end it."""
    from backend.routes import rides as rides_mod

    spawn_mock = MagicMock()
    update_ride_mock = AsyncMock()

    with (
        patch("backend.routes.rides.db_supabase.get_ride", AsyncMock(return_value=_RIDE)),
        patch(
            "backend.routes.rides.db_supabase.get_rows",
            AsyncMock(side_effect=DatabaseError(details={"original": "H2 GOAWAY"})),
        ),
        patch("backend.routes.rides.db_supabase.update_ride", update_ride_mock),
        patch(
            "backend.routes.rides.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 4.0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides.get_app_settings", AsyncMock(return_value={})),
        patch("backend.routes.rides.spawn", spawn_mock),
    ):
        await rides_mod.match_driver_to_ride(ride_id=_RIDE["id"])

    assert spawn_mock.called, "Expected a _dispatch_retry to be scheduled on DB failure"
    # The ride must not be touched — no cancel, no state change from this path.
    update_ride_mock.assert_not_called()
    for call in spawn_mock.call_args_list:
        call.args[0].close()  # silence un-awaited coroutine warnings


@pytest.mark.asyncio
async def test_dispatch_retry_rearms_after_failed_attempt():
    """An exception inside a retry attempt must schedule the next attempt."""
    from backend.routes import rides as rides_mod

    spawn_mock = MagicMock()

    with (
        patch("backend.routes.rides.asyncio.sleep", AsyncMock()),
        patch(
            "backend.routes.rides.db_supabase.get_ride",
            AsyncMock(side_effect=DatabaseError(details={"original": "connection reset"})),
        ),
        patch("backend.routes.rides.spawn", spawn_mock),
    ):
        await rides_mod._dispatch_retry(_RIDE["id"], delay=0, attempt=3)

    assert spawn_mock.called, "Expected the retry chain to re-arm itself"
    for call in spawn_mock.call_args_list:
        call.args[0].close()


@pytest.mark.asyncio
async def test_dispatch_retry_respects_attempt_cap():
    """The re-arm must not defeat the attempt cap — past it, the chain stops."""
    from backend.routes import rides as rides_mod

    spawn_mock = MagicMock()
    get_ride_mock = AsyncMock()

    with (
        patch("backend.routes.rides.asyncio.sleep", AsyncMock()),
        patch("backend.routes.rides.db_supabase.get_ride", get_ride_mock),
        patch("backend.routes.rides.spawn", spawn_mock),
    ):
        await rides_mod._dispatch_retry(_RIDE["id"], delay=0, attempt=rides_mod._MAX_DISPATCH_ATTEMPTS + 1)

    get_ride_mock.assert_not_called()
    spawn_mock.assert_not_called()
