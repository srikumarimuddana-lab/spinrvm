"""The offer-expiry reaper must not punish the driver who won the ride (D1).

`process_expired_offer` treats its atomic `ride_offers` pending->expired claim
as the single gate for all side-effects. On the **single-offer** path that is
sound: the timeout and the accept both predicate on
`{status: driver_assigned, driver_id: <this driver>}` on the *ride*, so they are
mutually exclusive.

On the **batch-offer** path it is not. `accept_ride`
(`routes/drivers/ride_flow.py`) CASes the *ride* — `{status: searching,
driver_id: None}` -> `driver_accepted` — independently of the offer row, and
only flips the offer to "accepted" afterwards, purely for a metric. So a driver
can win the ride and still lose this offer claim, at which point the reaper ran:

  * `increment_miss_streak` + `update_acceptance_rate(accepted=False)` on the
    driver who *did* accept, and
  * either force-offline + `record_period_transition(driver, 0)` — a driver
    offline and in Period 0 **mid-trip**, a regulatory/insurance
    misclassification — or `set_driver_available(True)`, which never checks for
    an active ride and so violates nothing less than the availability
    invariant.

Fixed by re-reading the ride after winning the offer claim and standing down
when it has reached a post-acceptance state with this driver.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

_DRIVER = "drv-race-1"
_RIDE = "ride-race-1"


def _penalty_patches():
    """Every side-effect process_expired_offer runs on a genuine timeout."""
    return {
        "miss": patch("backend.utils.driver_presence.increment_miss_streak", AsyncMock(return_value=1)),
        "rate": patch("backend.repositories.driver_repo.update_acceptance_rate", AsyncMock()),
        "period": patch("backend.routes.rides._deps.record_period_transition", AsyncMock()),
        "avail": patch(
            "backend.routes.rides._deps.db_supabase.set_driver_available",
            AsyncMock(return_value={"is_available": True}),
        ),
        "clear": patch("backend.utils.driver_presence.clear_presence", AsyncMock()),
        "reset": patch("backend.utils.driver_presence.reset_miss_streak", AsyncMock()),
        "redis": patch("backend.utils.redis_client.redis_set", AsyncMock()),
        "driver": patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=None)),
    }


async def _run(ride_row, *, claim_wins=True, miss_threshold=3, run_sync=None):
    from backend.routes.rides import matching as m

    claim = MagicMock(data=[{"id": "off1"}] if claim_wins else [])
    run_sync = run_sync or AsyncMock(return_value=claim)
    mocks = {}
    import contextlib

    with contextlib.ExitStack() as es:
        es.enter_context(patch("backend.routes.rides._deps.db_supabase.run_sync", run_sync))
        es.enter_context(patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=ride_row)))
        mgr = es.enter_context(patch("backend.routes.rides._deps.manager"))
        mgr.send_personal_message = AsyncMock()
        for name, cm in _penalty_patches().items():
            mocks[name] = es.enter_context(cm)
        won = await m.process_expired_offer(_RIDE, _DRIVER, miss_threshold)
    mocks["run_sync"] = run_sync
    return won, mocks


class TestReaperStandsDownWhenTheDriverWonTheRide:
    @pytest.mark.parametrize("status", ["driver_accepted", "driver_arrived", "in_progress", "completed"])
    async def test_no_penalties_when_this_driver_holds_the_ride(self, status):
        won, m = await _run({"id": _RIDE, "status": status, "driver_id": _DRIVER})
        assert won is False
        m["miss"].assert_not_awaited()
        m["rate"].assert_not_awaited()

    async def test_never_offlines_a_driver_mid_trip(self):
        """The worst outcome: at the miss threshold the reaper force-offlines
        and writes Period 0 on a driver with a passenger aboard."""
        won, m = await _run(
            {"id": _RIDE, "status": "in_progress", "driver_id": _DRIVER},
            miss_threshold=1,  # any miss trips auto-offline
        )
        assert won is False
        m["period"].assert_not_awaited()
        m["avail"].assert_not_awaited()
        m["clear"].assert_not_awaited()

    async def test_never_marks_an_on_trip_driver_available(self):
        """set_driver_available(True) never checks for an active ride, so
        calling it here would break is_available's meaning outright."""
        _, m = await _run({"id": _RIDE, "status": "driver_accepted", "driver_id": _DRIVER})
        m["avail"].assert_not_awaited()

    async def test_offer_row_is_restored_to_accepted(self):
        """accept_ride's own pending->accepted flip matched zero rows because
        this reaper got there first, so the history would otherwise show
        'expired' for an offer that was accepted."""
        calls = []

        async def _run_sync(fn):
            calls.append(fn)
            return MagicMock(data=[{"id": "off1"}])

        won, m = await _run(
            {"id": _RIDE, "status": "driver_accepted", "driver_id": _DRIVER},
            run_sync=AsyncMock(side_effect=_run_sync),
        )
        assert won is False
        # Two run_sync calls: the pending->expired claim, then the restore.
        assert len(calls) == 2


class TestGenuineTimeoutsStillPenalised:
    async def test_searching_ride_still_penalises(self):
        """The batch-offer timeout itself: the ride went back to searching with
        no driver, so this really is a miss."""
        won, m = await _run({"id": _RIDE, "status": "searching", "driver_id": None})
        assert won is True
        m["miss"].assert_awaited_once()
        m["rate"].assert_awaited_once()

    async def test_single_offer_timeout_on_driver_assigned_still_penalises(self):
        """CRITICAL non-regression: on the single-offer path the ride sits in
        driver_assigned with driver_id set to THIS driver while its offer is
        pending. Expiring that offer is the legitimate timeout, so the guard
        must key on a post-acceptance status, never on driver_id alone."""
        won, m = await _run({"id": _RIDE, "status": "driver_assigned", "driver_id": _DRIVER})
        assert won is True
        m["miss"].assert_awaited_once()

    async def test_ride_taken_by_a_different_driver_still_penalises(self):
        """Another driver won the batch race — this driver genuinely missed."""
        won, m = await _run({"id": _RIDE, "status": "driver_accepted", "driver_id": "someone-else"})
        assert won is True
        m["miss"].assert_awaited_once()

    async def test_missing_ride_row_still_penalises(self):
        won, m = await _run(None)
        assert won is True
        m["miss"].assert_awaited_once()


class TestFailClosedOnReadFailure:
    async def test_unreadable_ride_skips_penalties(self):
        """A missed penalty is recoverable; offlining a driver mid-trip and
        mis-recording their insurance period is not."""
        import contextlib

        from backend.routes.rides import matching as m_mod

        mocks = {}
        with contextlib.ExitStack() as es:
            es.enter_context(
                patch(
                    "backend.routes.rides._deps.db_supabase.run_sync",
                    AsyncMock(return_value=MagicMock(data=[{"id": "off1"}])),
                )
            )
            es.enter_context(
                patch(
                    "backend.routes.rides._deps.db_supabase.get_ride",
                    AsyncMock(side_effect=RuntimeError("db down")),
                )
            )
            mgr = es.enter_context(patch("backend.routes.rides._deps.manager"))
            mgr.send_personal_message = AsyncMock()
            for name, cm in _penalty_patches().items():
                mocks[name] = es.enter_context(cm)
            won = await m_mod.process_expired_offer(_RIDE, _DRIVER, 3)

        assert won is False
        mocks["miss"].assert_not_awaited()
        mocks["period"].assert_not_awaited()
