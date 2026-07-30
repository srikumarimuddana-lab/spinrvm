"""The orphaned-hold reconciler releases money riders should never have had held.

Two populations it exists for: the historical backlog from before the stuck-ride
sweeper released holds at all, and ongoing release failures, which nothing else
retries.

The tests that matter most here are the *negative* ones. This loop cancels Stripe
authorizations autonomously on a 15-minute cadence, so the expensive failure is not
"it missed one" — it is "it released a hold that was owed to a driver." A completed
ride's open hold belongs to utils/preauth_capture, which CAPTURES it; on a
0%-commission product, wrongly releasing that means the driver eats the fare.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import ride_row

_OLD = "2026-07-01T12:00:00+00:00"


def _orphan(**over):
    """A cancelled ride still carrying an open booking-time hold."""
    base = dict(
        id="ride-orphan-1",
        rider_id="rider-1",
        status="cancelled",
        auth_status="authorized",
        payment_intent_id="pi_orphan_1",
        authorized_amount="27.50",
        grand_total="22.50",
        updated_at=_OLD,
    )
    base.update(over)
    return ride_row(**base)


def _stack(rows, *, cancel=None, update_one=None, metric=None):
    """Patch the reconciler's collaborators. ExitStack because the set is dynamic."""
    cancel = cancel if cancel is not None else AsyncMock(return_value=True)
    update_one = update_one if update_one is not None else AsyncMock(return_value={"id": "x"})
    metric = metric if metric is not None else MagicMock()
    stack = ExitStack()
    for cm in (
        patch("backend.db_supabase.get_rows", AsyncMock(return_value=rows)),
        patch("backend.db_supabase.update_one", update_one),
        patch("backend.utils.card_hold_release.cancel_authorization", cancel),
        patch("backend.utils.card_hold_release._metric_inc", MagicMock()),
        patch("backend.utils.orphaned_hold_reconciler._metric_inc", metric),
    ):
        stack.enter_context(cm)
    return stack, cancel, update_one, metric


# ── it finds and releases the backlog ───────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_releases_an_orphaned_hold_on_a_cancelled_ride():
    stack, cancel, update_one, _ = _stack([_orphan()])
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert summary["found"] == 1
    assert summary["released"] == 1
    cancel.assert_awaited_once()
    assert cancel.await_args.kwargs["payment_intent_id"] == "pi_orphan_1"


@pytest.mark.anyio
@pytest.mark.unit
async def test_claim_is_a_compare_and_swap_on_updated_at():
    """Two replicas must not both release. migration 156 pins auth_status with a
    CHECK constraint, so there is no intermediate claim state — updated_at is the
    version column instead."""
    stack, _, update_one, _ = _stack([_orphan()])
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        await reconcile_tick()

    claim = update_one.await_args_list[0]
    filters = claim.args[1]
    assert filters["updated_at"] == _OLD, "claim must assert the observed version"
    assert filters["auth_status"] == {"$in": ["authorized", "fare_only"]}


@pytest.mark.anyio
@pytest.mark.unit
async def test_losing_the_claim_race_skips_without_touching_stripe():
    """update_one returning None means another replica won."""
    stack, cancel, _, _ = _stack([_orphan()], update_one=AsyncMock(return_value=None))
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert summary["not_claimed"] == 1
    assert summary["released"] == 0
    cancel.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_a_ride_with_no_updated_at_is_skipped_not_released_unguarded():
    stack, cancel, _, _ = _stack([_orphan(updated_at=None)])
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert summary["not_claimed"] == 1
    cancel.assert_not_awaited()


# ── what it must NEVER touch ────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_a_completed_ride_is_never_selected():
    """The expensive mistake. A completed ride's open hold is owed to the driver and
    belongs to utils/preauth_capture, which CAPTURES it. Releasing it refunds a fare
    on a ride that actually happened, and on a 0%-commission product the driver eats
    that loss directly."""
    stack, cancel, _, _ = _stack([])
    with stack:
        from backend.utils.orphaned_hold_reconciler import find_orphaned_holds, reconcile_tick

        await find_orphaned_holds()
        # Assert on the query itself, since the fixture returns [] regardless.
        import backend.db_supabase as db

        status_filter = db.get_rows.await_args.args[1]["status"]
        assert status_filter == {"$in": ["cancelled"]}, (
            f"reconciler must only consider terminal non-billable states, got {status_filter}"
        )

        summary = await reconcile_tick()
    assert summary["found"] == 0
    cancel.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_only_open_auth_states_are_queried():
    stack, _, _, _ = _stack([])
    with stack:
        from backend.utils.orphaned_hold_reconciler import find_orphaned_holds

        await find_orphaned_holds()
        import backend.db_supabase as db

        assert db.get_rows.await_args.args[1]["auth_status"] == {"$in": ["authorized", "fare_only"]}


@pytest.mark.anyio
@pytest.mark.unit
@pytest.mark.parametrize(
    "over",
    [
        {"payment_intent_id": None},  # no intent recorded
        {"auth_status": "captured"},  # would reverse money the rider owes
        {"auth_status": "released"},  # already done
        {"auth_status": None},  # wallet / corporate ride
    ],
)
async def test_rows_without_a_releasable_hold_are_filtered_out(over):
    """Belt-and-braces: even if the query returned such a row, has_open_hold must
    reject it before any Stripe call."""
    stack, cancel, _, _ = _stack([_orphan(**over)])
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert summary["found"] == 0
    cancel.assert_not_awaited()


# ── dry run ─────────────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_dry_run_touches_neither_stripe_nor_the_database():
    """How an operator sizes the backlog before moving real money."""
    stack, cancel, update_one, _ = _stack([_orphan(), _orphan(id="r2", payment_intent_id="pi_2")])
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick(dry_run=True)

    assert summary["found"] == 2
    assert summary["released"] == 0
    assert summary["dry_run"] is True
    cancel.assert_not_awaited()
    update_one.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.unit
async def test_dry_run_logs_no_rider_pii(caplog):
    """PIPEDA: IDs and amounts only — no names, phones, emails or coordinates."""
    import logging

    ride = _orphan()
    ride["rider_phone"] = "+13065551234"
    ride["rider_name"] = "Jane Doe"

    with caplog.at_level(logging.INFO):
        stack, _, _, _ = _stack([ride])
        with stack:
            from backend.utils.orphaned_hold_reconciler import reconcile_tick

            await reconcile_tick(dry_run=True)

    text = caplog.text
    assert "+13065551234" not in text
    assert "Jane Doe" not in text
    assert "ride-orphan-1" in text, "the ride id must be logged — it is how this gets triaged"


# ── failure handling ────────────────────────────────────────────────


@pytest.mark.anyio
@pytest.mark.unit
async def test_a_failed_release_is_counted_and_does_not_stop_the_batch():
    """One rider's Stripe failure must not strand the next rider's funds."""
    rides = [
        _orphan(id="r1", payment_intent_id="pi_1"),
        _orphan(id="r2", payment_intent_id="pi_2"),
        _orphan(id="r3", payment_intent_id="pi_3"),
    ]
    seen: list[str] = []

    async def _cancel(*, ride_id, payment_intent_id):
        seen.append(payment_intent_id)
        return payment_intent_id != "pi_2"

    stack, _, _, _ = _stack(rides, cancel=_cancel)
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert seen == ["pi_1", "pi_2", "pi_3"], f"batch stopped early: {seen}"
    assert summary["released"] == 2
    assert summary["failed"] == 1


@pytest.mark.anyio
@pytest.mark.unit
async def test_a_raising_stripe_call_does_not_abort_the_tick():
    stack, _, _, _ = _stack([_orphan()], cancel=AsyncMock(side_effect=RuntimeError("stripe down")))
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()  # must not raise

    assert summary["failed"] == 1
    assert summary["released"] == 0


@pytest.mark.anyio
@pytest.mark.unit
async def test_a_failing_candidate_query_reports_instead_of_raising():
    stack = ExitStack()
    stack.enter_context(patch("backend.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))))
    stack.enter_context(patch("backend.utils.orphaned_hold_reconciler._metric_inc", MagicMock()))
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert "error" in summary
    assert summary["found"] == 0


@pytest.mark.anyio
@pytest.mark.unit
async def test_empty_backlog_is_a_cheap_no_op():
    stack, cancel, update_one, metric = _stack([])
    with stack:
        from backend.utils.orphaned_hold_reconciler import reconcile_tick

        summary = await reconcile_tick()

    assert summary == {
        "found": 0,
        "released": 0,
        "skipped": 0,
        "not_claimed": 0,
        "failed": 0,
        "dry_run": False,
    }
    cancel.assert_not_awaited()
    update_one.assert_not_awaited()


# ── wiring ──────────────────────────────────────────────────────────


def test_the_loop_is_registered_in_lifespan():
    """Behavioural tests call reconcile_tick directly, so deleting the _spawn in
    lifespan.py would leave the backlog undrained with every test still green — the
    same wiring gap T1c and T2 both had."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parent.parent / "core" / "lifespan.py").read_text()
    assert "orphaned_hold_reconciler_loop" in src, "the reconciler loop is never spawned"
    assert "_spawn(\"orphaned_hold_reconciler" in src


def test_the_batch_is_bounded():
    """An unbounded scan would hold the leader lock for minutes on a hot table."""
    from backend.utils.orphaned_hold_reconciler import _BATCH_LIMIT

    assert 0 < _BATCH_LIMIT <= 200
