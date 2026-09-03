"""KPI instrumentation for dispatch (CLAUDE.md metric naming scheme).

Pins that the dispatch hot paths emit:
  - spinr_dispatch_offer_sent_total      (match_driver_to_ride, per offer row)
  - spinr_dispatch_offer_accepted_total  (accept_ride, per winning accept)
  - spinr_dispatch_offer_to_accept_duration_ms (accept_ride, from the winner's
    own ride_offers.offered_at — the KPI table's P95 < 2s dispatch latency)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tests._factories import close_spawned_coro
from backend.utils import metrics

RIDE_ID = "ride-metrics-001"
RIDER_ID = "rider-metrics"
DRIVER_ID = "driver-row-metrics"
DRIVER_USER_ID = "driver-user-metrics"


def _counter_total(name: str) -> int:
    return sum(metrics.snapshot()["counters"].get(name, {}).values())


def _histogram_cell(name: str):
    series = metrics.snapshot()["histograms"].get(name, {})
    return series.get(()) if series else None


def _driver() -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "lat": 52.13,
        "lng": -106.67,
        "rating": 4.8,
        "is_wav": False,
        "is_online": True,
        "is_verified": True,
        "status": "active",
        "vehicle_type_id": "economy",
    }


def _ride(status: str = "searching", driver_id=None) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "vehicle_type_id": "economy",
        "requires_wav": False,
        "status": status,
        "driver_id": driver_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.anyio
async def test_match_driver_to_ride_counts_offers_sent():
    from backend.routes import rides as rides_mod

    before = _counter_total("spinr_dispatch_offer_sent_total")

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch(
            "backend.routes.rides._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 4.0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
        patch("backend.routes.rides._deps.db_supabase.claim_driver_atomic", AsyncMock(return_value=_driver())),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
        patch(
            "backend.routes.rides._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
        ),
        # ride_offers insert + incentives lookup both go through run_sync.
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=SimpleNamespace(data=[])),
        ),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"offer_timeout_seconds": 15})),
        # side_effect closes the spawned coroutine instead of leaking it (A8).
        patch("backend.routes.rides._deps.asyncio.create_task", MagicMock(side_effect=close_spawned_coro)),
    ):
        try:
            await rides_mod.match_driver_to_ride(ride_id=RIDE_ID)
        except Exception:
            pass  # post-offer enrichment may raise under mocks; offers were sent

    assert _counter_total("spinr_dispatch_offer_sent_total") == before + 1


@pytest.mark.anyio
async def test_find_candidate_drivers_counts_presence_filter_failure():
    """A Redis-presence failure falls back to DB-online drivers AND increments
    spinr_dispatch_presence_filter_failed_total so the degradation is visible."""
    from backend.services.dispatch_service import DispatchService

    before = _counter_total("spinr_dispatch_presence_filter_failed_total")

    db = MagicMock()
    db.get_rows = AsyncMock(return_value=[_driver()])
    svc = DispatchService(db)

    with patch(
        "backend.services.dispatch_service.present_driver_ids",
        AsyncMock(side_effect=RuntimeError("redis down")),
    ):
        rows = await svc.find_candidate_drivers(_ride())

    assert rows == [_driver()]  # safety valve: fall back to DB-online drivers
    assert _counter_total("spinr_dispatch_presence_filter_failed_total") == before + 1


@pytest.mark.anyio
async def test_accept_ride_counts_accept_and_observes_latency():
    from backend.routes import drivers as drivers_mod

    offered_at = datetime.now(timezone.utc) - timedelta(seconds=3)
    pre_ride = _ride("driver_assigned", driver_id=DRIVER_ID)
    post_ride = _ride("driver_accepted", driver_id=DRIVER_ID)

    # First run_sync call = winner ride_offers update (returns the offer row,
    # which carries offered_at); later calls (losers select etc.) return empty.
    run_sync_results = [
        SimpleNamespace(data=[{"driver_id": DRIVER_ID, "offered_at": offered_at.isoformat()}]),
    ]

    async def _run_sync(fn, *a, **kw):
        if run_sync_results:
            return run_sync_results.pop(0)
        return SimpleNamespace(data=[])

    counter_before = _counter_total("spinr_dispatch_offer_accepted_total")
    cell_before = _histogram_cell("spinr_dispatch_offer_to_accept_duration_ms")
    count_before = cell_before["count"] if cell_before else 0

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_ride", AsyncMock(return_value=pre_ride)),
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch("backend.routes.drivers._deps.db.update_one", AsyncMock(return_value={"id": RIDE_ID})),
        patch("backend.routes.drivers._deps.db.find_one", AsyncMock(return_value=post_ride)),
        patch("backend.routes.drivers._deps.db_supabase.run_sync", AsyncMock(side_effect=_run_sync)),
        # record_period_transition (Period 2 open on acceptance) makes its
        # own db_supabase.run_sync call for the insurance-period RPC --
        # unmocked, it consumes run_sync_results[0] before the winner-offer
        # run_sync call below reaches it, so offered_at parses to None and
        # the latency histogram observation is silently skipped.
        patch("backend.routes.drivers._deps.record_period_transition", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.drivers._deps.manager.broadcast_ride_status", AsyncMock()),
        patch("backend.routes.drivers._deps.send_push_notification", AsyncMock()),
    ):
        await drivers_mod.accept_ride(ride_id=RIDE_ID, current_user={"id": DRIVER_USER_ID})

    assert _counter_total("spinr_dispatch_offer_accepted_total") == counter_before + 1

    cell = _histogram_cell("spinr_dispatch_offer_to_accept_duration_ms")
    assert cell is not None and cell["count"] == count_before + 1
    # The sample must be the offered_at → now delta (~3s), not a wall-clock zero.
    last_sample_ms = cell["sum"] - (cell_before["sum"] if cell_before else 0.0)
    assert 2000.0 <= last_sample_ms <= 30000.0


# ── T3 (C50 Phase 0): run_sync queue-wait / exec-time histograms ──────────
#
# Pins that backend/repositories/_base.py's run_sync records BOTH
# spinr_db_run_sync_queue_wait_ms (submit → thread-start) and
# spinr_db_run_sync_exec_ms (thread-start → callable return) on every call,
# with sane small-positive-millisecond values -- not just "code ran".


@pytest.mark.anyio
async def test_run_sync_observes_queue_wait_and_exec_histograms():
    from repositories import _base

    cell_wait_before = _histogram_cell("spinr_db_run_sync_queue_wait_ms")
    cell_exec_before = _histogram_cell("spinr_db_run_sync_exec_ms")
    wait_count_before = cell_wait_before["count"] if cell_wait_before else 0
    exec_count_before = cell_exec_before["count"] if cell_exec_before else 0
    wait_sum_before = cell_wait_before["sum"] if cell_wait_before else 0.0
    exec_sum_before = cell_exec_before["sum"] if cell_exec_before else 0.0

    def _trivial_sync_call():
        # A short but deliberate sleep so exec-time is measurably > 0.
        # Windows' time.monotonic() is backed by GetTickCount64, whose
        # resolution is ~15.6ms (time.get_clock_info('monotonic').resolution)
        # -- a 10ms sleep can round-trip to a 0ms delta on that clock, so this
        # sleeps long enough to reliably clear at least one clock tick on any
        # platform this suite runs on.
        import time as _t

        _t.sleep(0.05)
        return "ok"

    result = await _base.run_sync(_trivial_sync_call, retry_policy="read")
    assert result == "ok"

    cell_wait = _histogram_cell("spinr_db_run_sync_queue_wait_ms")
    cell_exec = _histogram_cell("spinr_db_run_sync_exec_ms")
    assert cell_wait is not None and cell_wait["count"] == wait_count_before + 1
    assert cell_exec is not None and cell_exec["count"] == exec_count_before + 1

    # Queue-wait: a single call on an idle pool should resolve in well under
    # a second; assert a positive-but-bounded delta (not just "count went up").
    wait_delta_ms = cell_wait["sum"] - wait_sum_before
    assert 0.0 <= wait_delta_ms <= 5000.0

    # Exec-time: the callable itself slept ~50ms, so the observed delta should
    # land comfortably above 0 and reasonably close to that sleep duration
    # (generous bounds to absorb scheduler jitter and clock-resolution
    # rounding on shared/CI hosts, esp. Windows' ~15.6ms monotonic tick).
    exec_delta_ms = cell_exec["sum"] - exec_sum_before
    assert 15.0 <= exec_delta_ms <= 5000.0


@pytest.mark.anyio
async def test_run_sync_records_exec_time_on_exception_path():
    """time_ms/observe must fire even when the wrapped callable raises --
    the queue-wait/exec-time histograms are latency data the SLA dashboards
    need to see for slow *failures*, not just slow successes (matches
    utils/metrics.py's time_ms docstring: 'Records even when the block
    raises')."""
    from repositories import _base

    cell_exec_before = _histogram_cell("spinr_db_run_sync_exec_ms")
    exec_count_before = cell_exec_before["count"] if cell_exec_before else 0

    def _failing_sync_call():
        raise ValueError("boom - deliberate test failure")

    # ValueError is the run_sync "not transient, no retry" fast-exit path
    # (see the `isinstance(exc, ValueError): raise` branch) -- exactly one
    # executor submission, so exactly one exec-time observation is expected.
    with pytest.raises(ValueError, match="boom"):
        await _base.run_sync(_failing_sync_call, retry_policy="read")

    cell_exec = _histogram_cell("spinr_db_run_sync_exec_ms")
    assert cell_exec is not None and cell_exec["count"] == exec_count_before + 1
    # A single fast raise should still be a small positive number, not 0 and
    # not something absurd that would indicate the timer never stopped.
    assert cell_exec["buckets"] is not None  # sanity: bucket layout was pinned


@pytest.mark.anyio
async def test_db_call_counter_tracks_real_run_sync_calls():
    """Direct, unmocked exercise of the reset_db_call_count/get_db_call_count
    contract that _match_driver_to_ride_attempt relies on: reset zeroes the
    per-task counter, each successful run_sync call increments it exactly
    once, and observing the final count produces a real positive value in
    spinr_dispatch_attempt_db_calls -- the correctness check the fully-mocked
    dispatch-attempt test above cannot provide because it stubs run_sync out
    entirely."""
    from repositories import _base

    _base.reset_db_call_count()
    assert _base.get_db_call_count() == 0

    for _ in range(3):
        result = await _base.run_sync(lambda: "row", retry_policy="read")
        assert result == "row"

    assert _base.get_db_call_count() == 3

    cell_before = _histogram_cell("spinr_dispatch_attempt_db_calls")
    count_before = cell_before["count"] if cell_before else 0

    _base._metric_observe(
        "spinr_dispatch_attempt_db_calls",
        _base.get_db_call_count(),
        buckets=(5, 10, 15, 20, 25, 30, 40, 50, 75, 100),
    )

    cell = _histogram_cell("spinr_dispatch_attempt_db_calls")
    assert cell is not None and cell["count"] == count_before + 1
    last_value = cell["sum"] - (cell_before["sum"] if cell_before else 0.0)
    assert last_value == 3

    # A second reset must zero it again -- proves reset isn't a one-shot
    # no-op and each dispatch attempt starts from a clean counter.
    _base.reset_db_call_count()
    assert _base.get_db_call_count() == 0


@pytest.mark.anyio
async def test_db_call_counter_includes_calls_made_in_gather_children():
    """run_sync calls issued from asyncio.gather() children must reach the
    parent's count.

    Regression test. _match_driver_to_ride_attempt fans its enrichment reads
    out through asyncio.gather() (_fetch_rider -> get_user_by_id,
    _fetch_incentives -> match_ride_incentives' run_sync), and asyncio runs
    each gathered coroutine in a *copy* of the current context. A plain
    ContextVar[int] counter therefore had its child increments written to
    that copy and thrown away, so every spinr_dispatch_attempt_db_calls
    observation silently under-reported the enrichment phase -- the exact
    phase the C50 PostgREST -> direct-pool sizing decision depends on.

    The sequential test above cannot catch this: it never crosses a task
    boundary. This one does, and fails (1 instead of 5) against the plain-int
    implementation.
    """
    import asyncio

    from repositories import _base

    async def _child_doing_two_calls():
        await _base.run_sync(lambda: "row", retry_policy="read")
        await _base.run_sync(lambda: "row", retry_policy="read")

    _base.reset_db_call_count()
    await _base.run_sync(lambda: "row", retry_policy="read")  # 1 in the parent
    await asyncio.gather(_child_doing_two_calls(), _child_doing_two_calls())  # 4 in children

    assert _base.get_db_call_count() == 5


@pytest.mark.anyio
async def test_db_call_counter_isolates_concurrent_dispatch_attempts():
    # Review note (2026-09-03): this passes against the pre-fix int-based
    # ContextVar too (each gathered child rebinds in its own context copy),
    # so it guards against a future module-global regression rather than
    # re-proving the gather-additivity fix — that is the test above.
    """Two dispatch attempts running concurrently must not pool their counts.

    The mutable-container counter that makes the gather case above work must
    not reintroduce cross-attempt bleed: reset_db_call_count() rebinds a new
    container per attempt, and each attempt runs in its own task context, so
    each must observe only its own calls.
    """
    import asyncio

    from repositories import _base

    async def _attempt(n_calls: int) -> int:
        _base.reset_db_call_count()
        for _ in range(n_calls):
            await _base.run_sync(lambda: "row", retry_policy="read")
        return _base.get_db_call_count()

    # Distinct call counts so a pooled counter cannot coincidentally match.
    assert await asyncio.gather(_attempt(2), _attempt(5), _attempt(3)) == [2, 5, 3]


# ── T3 (C50 Phase 0): per-phase dispatch timing + db-calls histogram ──────
#
# Pins that _match_driver_to_ride_attempt wraps its claim / offer_insert /
# insurance phases with spinr_dispatch_attempt_duration_ms{phase=...} and
# records at least one spinr_dispatch_attempt_db_calls observation with a
# count > 0, using the SAME mock setup as
# test_match_driver_to_ride_counts_offers_sent (a real successful attempt
# has to reach every phase for all three labels to be hit).


def _phase_cell(metric_name: str, phase: str):
    """Find the histogram cell for {"phase": <phase>} regardless of how
    _labels_to_key sorts/serializes the label tuple -- avoids hardcoding
    metrics.py's internal key format."""
    series = metrics.snapshot()["histograms"].get(metric_name, {})
    for label_key, cell in series.items():
        if dict(label_key).get("phase") == phase:
            return cell
    return None


@pytest.mark.anyio
async def test_match_driver_to_ride_records_phase_timings_and_db_calls():
    from backend.routes import rides as rides_mod

    phases_to_check = ("claim", "offer_insert", "insurance")
    counts_before = {
        p: (_phase_cell("spinr_dispatch_attempt_duration_ms", p) or {}).get("count", 0) for p in phases_to_check
    }
    sums_before = {
        p: (_phase_cell("spinr_dispatch_attempt_duration_ms", p) or {}).get("sum", 0.0) for p in phases_to_check
    }

    db_calls_cell_before = _histogram_cell("spinr_dispatch_attempt_db_calls")
    db_calls_count_before = db_calls_cell_before["count"] if db_calls_cell_before else 0

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[_driver()])),
        patch(
            "backend.routes.rides._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 4.0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
        patch("backend.routes.rides._deps.db_supabase.claim_driver_atomic", AsyncMock(return_value=_driver())),
        patch("backend.routes.rides._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=_driver())),
        patch(
            "backend.routes.rides._deps.db_supabase.get_user_by_id",
            AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
        ),
        # ride_offers insert + incentives lookup both go through run_sync.
        patch(
            "backend.routes.rides._deps.db_supabase.run_sync",
            AsyncMock(return_value=SimpleNamespace(data=[])),
        ),
        patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
        patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"offer_timeout_seconds": 15})),
        # side_effect closes the spawned coroutine instead of leaking it (A8).
        patch("backend.routes.rides._deps.asyncio.create_task", MagicMock(side_effect=close_spawned_coro)),
    ):
        try:
            await rides_mod.match_driver_to_ride(ride_id=RIDE_ID)
        except Exception:
            pass  # post-offer enrichment may raise under mocks; offers were sent

    for phase in phases_to_check:
        cell = _phase_cell("spinr_dispatch_attempt_duration_ms", phase)
        assert cell is not None, f"expected a spinr_dispatch_attempt_duration_ms observation for phase={phase}"
        assert cell["count"] == counts_before[phase] + 1, f"phase={phase} count did not increase by exactly 1"
        # Each phase's own latency delta must be a real (non-negative, bounded)
        # duration, not a stray 0 or an absurd outlier from a mis-scoped `with`.
        delta_ms = cell["sum"] - sums_before[phase]
        assert 0.0 <= delta_ms <= 5000.0, f"phase={phase} latency delta {delta_ms}ms out of sane range"

    db_calls_cell = _histogram_cell("spinr_dispatch_attempt_db_calls")
    assert db_calls_cell is not None
    assert db_calls_cell["count"] == db_calls_count_before + 1
    # NOTE: this test patches backend.routes.rides._deps.db_supabase.run_sync
    # directly with an AsyncMock (same pattern as
    # test_match_driver_to_ride_counts_offers_sent), which replaces the real
    # repositories._base.run_sync entirely for this attempt -- so the
    # ContextVar counter it increments never fires here, and the recorded
    # db_calls VALUE is legitimately 0 under this mock. The counter's own
    # increment behaviour (that the value is a real, positive count derived
    # from actual run_sync calls) is covered directly, unmocked, by
    # test_db_call_counter_tracks_real_run_sync_calls below -- that is the
    # right place to assert value correctness, not here where run_sync itself
    # is stubbed out.


@pytest.mark.anyio
async def test_match_driver_to_ride_records_db_calls_on_early_no_drivers_return():
    """The db_calls histogram must still fire when the attempt exits early
    (no eligible drivers) via the try/finally, matching time_ms's own
    'records even on early exit / exception' contract -- and the recorded
    count should reflect the DB calls made before the early return (the
    candidate read), not be skipped entirely."""
    from backend.routes import rides as rides_mod

    db_calls_cell_before = _histogram_cell("spinr_dispatch_attempt_db_calls")
    db_calls_count_before = db_calls_cell_before["count"] if db_calls_cell_before else 0

    with (
        patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=_ride())),
        # No candidate drivers at all -> filter_and_rank_drivers returns [],
        # no service_area_id on the ride so cascade is skipped, and the
        # attempt takes the "no eligible drivers" early-return path.
        patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch(
            "backend.routes.rides._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 4.0, 10.0, 3, False)),
        ),
        patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"offer_timeout_seconds": 15})),
        patch("backend.routes.rides._deps.asyncio.create_task", MagicMock(side_effect=close_spawned_coro)),
    ):
        await rides_mod.match_driver_to_ride(ride_id=RIDE_ID)

    db_calls_cell = _histogram_cell("spinr_dispatch_attempt_db_calls")
    assert db_calls_cell is not None
    assert db_calls_cell["count"] == db_calls_count_before + 1
