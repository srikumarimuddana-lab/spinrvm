"""C50 Phase 2 (T14) — parity test between the PostgREST claim path (flag
off) and the direct-pool claim path (flag on) for
``_match_driver_to_ride_attempt``.

Uses the mocked ``mock_supabase_client``-style fixtures (same style as
``test_dispatch_match_attempt_branches.py``) — this file does NOT exercise
real Postgres. The real-Postgres side of T12/T13's SQL (the actual
``dispatch_claim_batch`` RPC body, race behavior, and the
``driver_insurance_periods`` rows it writes) is covered separately in
``backend/tests/direct_pool/test_claim_batch.py``.

What "parity" means here, precisely
-----------------------------------
Both branches of ``_match_driver_to_ride_attempt``'s claim block
(``dispatch_direct_pool_enabled`` True vs False) are driven through an
IDENTICAL fixed-seed scenario (same ride, same 3 ranked candidate drivers
in the same order, same claim outcomes: driver 1 succeeds, driver 2 loses
the claim race, driver 3 succeeds, ``max_offers=2``) and asserted to
produce:

  1. The identical claimed-driver id set AND order.
  2. Identical ``ride_offers`` row payloads (built via the same
     ``_build_offer_rows`` helper on both sides — the PostgREST path calls
     it directly; the direct-pool path's SQL insert is asserted separately
     in ``migrations/402_dispatch_claim_batch.sql`` and
     ``tests/direct_pool/test_claim_batch.py`` to build the SAME columns,
     so this test constructs the equivalent structure from the mocked RPC
     response and diffs it against the PostgREST path's actual insert
     payload).
  3. The identical claimed-driver set as the set that would receive an
     insurance-period-2 write: the PostgREST path's actual
     ``record_period_transition`` call args vs. the direct-pool mocked
     RPC's ``claimed=True`` driver set (the RPC's SQL body is what
     performs this write for real on the direct-pool path — verified in
     migration 402 and the real-Postgres suite, not re-verified here).
  4. Identical ``spinr_dispatch_offer_sent_total`` increment count.

Also asserts the two paths are mutually exclusive at the call-site level:
flag off never calls ``dispatch_pool.claim_batch``; flag on never calls
``claim_driver_atomic``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio

_FIXED_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
_RIDE_ID = "ride-parity-1"
_OFFER_TIMEOUT = 15


def _make_ride():
    return {
        "id": _RIDE_ID,
        "rider_id": "rider-1",
        "vehicle_type_id": "vt-std",
        # None sidesteps the subscription/quota/cascade blocks entirely,
        # same technique test_dispatch_match_attempt_branches.py uses --
        # this test targets the claim/offer/insurance block, not those
        # upstream gates (already covered elsewhere).
        "service_area_id": None,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.60,
        "requires_wav": False,
        "status": "searching",
        "driver_earnings": 12.50,
    }


def _make_driver(driver_id, **overrides):
    d = {
        "id": driver_id,
        "user_id": f"user-{driver_id}",
        "vehicle_type_id": "vt-std",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "lat": 52.14,
        "lng": -106.68,
        "average_rating": 4.8,
    }
    d.update(overrides)
    return d


@pytest.fixture
def scenario():
    """Fixed-seed scenario: 3 candidates, ranked in id order, driver 2
    loses the claim race (or fails revalidation), max_offers=2."""
    d1 = _make_driver("drv-1")
    d2 = _make_driver("drv-2")
    d3 = _make_driver("drv-3")
    return {
        "candidates": [d1, d2, d3],
        "eta_by_id": {"drv-1": 120, "drv-2": 240, "drv-3": 360},
        "claimed_ids_expected": ["drv-1", "drv-3"],
    }


async def _run_postgrest_path(scenario, mock_db):
    """Flag OFF: drives _match_driver_to_ride_attempt through the existing
    PostgREST claim/offer/insurance block. Returns captured artifacts."""
    from backend.routes.rides.matching import _match_driver_to_ride_attempt

    ride = _make_ride()
    d1, d2, d3 = scenario["candidates"]

    mock_db.get_rows = AsyncMock(return_value=scenario["candidates"])
    mock_db.find_one = AsyncMock(return_value=None)
    # d2 loses the race -> claim_driver_atomic returns falsy, matching
    # migration 402's "0 rows / NOT FOUND -> skip" semantics.
    mock_db.claim_driver_atomic = AsyncMock(side_effect=[d1, None, d3])
    mock_db.set_driver_available = AsyncMock()
    mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Rider", "rating": 4.9})

    captured_insert = MagicMock()
    fake_supabase = MagicMock()
    fake_supabase.table.return_value.insert = captured_insert
    captured_insert.return_value.execute = MagicMock(return_value=MagicMock(data=[]))
    mock_db.supabase = fake_supabase
    # run_sync's argument is a real callable in both the ride_offers-insert
    # call (a lambda) and the quest-progress call (a bound `.execute`
    # method reference) -- invoke it directly so the insert() call above
    # actually captures its argument, same as production.
    mock_db.run_sync = AsyncMock(side_effect=lambda fn: fn())

    record_period_calls = []

    async def _record_period(driver_id, period, ride_id=None):
        record_period_calls.append((driver_id, period, ride_id))

    with (
        patch("backend.routes.rides.matching._deps.db_supabase", mock_db),
        patch(
            "backend.routes.rides.matching._deps.get_app_settings",
            AsyncMock(return_value={"dispatch_direct_pool_enabled": False}),
        ),
        patch("backend.routes.rides.matching._deps.record_period_transition", _record_period),
        patch("backend.routes.rides.matching.match_ride_incentives", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching.incentive_display_payload", return_value=([], 0.0)),
        patch("backend.routes.rides.matching.get_service_area_polygon", return_value=None),
        patch("backend.routes.rides.matching._deps.manager") as mock_manager,
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 2, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, float(i + 1)) for i, d in enumerate(drivers)],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None, None, None])),
        patch("backend.routes.rides.matching.datetime") as mock_datetime,
        patch("backend.routes.rides.matching._metric_inc") as mock_metric_inc,
    ):
        mock_manager.send_personal_message = AsyncMock()
        mock_datetime.now.return_value = _FIXED_NOW

        await _match_driver_to_ride_attempt(_RIDE_ID, ride=ride)

    offer_rows = captured_insert.call_args[0][0] if captured_insert.call_args else None

    return {
        "claim_call_order": [c.args[0] for c in mock_db.claim_driver_atomic.await_args_list],
        "offer_rows": offer_rows,
        "insurance_calls": record_period_calls,
        "metric_inc_calls": mock_metric_inc.call_args_list,
    }


async def _run_direct_pool_path(scenario, mock_db, rpc_result=None, pool_open=True):
    """Flag ON: drives the same attempt through the mocked
    dispatch_pool.claim_batch RPC wrapper."""
    from backend.routes.rides.matching import _build_offer_rows, _match_driver_to_ride_attempt

    ride = _make_ride()
    d1, d2, d3 = scenario["candidates"]

    mock_db.get_rows = AsyncMock(return_value=scenario["candidates"])
    mock_db.find_one = AsyncMock(return_value=None)
    mock_db.invalidate_driver_cache = AsyncMock()
    mock_db.get_user_by_id = AsyncMock(return_value={"first_name": "Rider", "rating": 4.9})
    # claim_driver_atomic must NEVER be called on this path -- assert via
    # a bare AsyncMock with no side_effect/return_value configured so any
    # accidental call is still harmless but visible to the "never called"
    # assertion in the test body.
    mock_db.claim_driver_atomic = AsyncMock()
    mock_db.set_driver_available = AsyncMock()
    mock_db.run_sync = AsyncMock(side_effect=lambda fn: fn())
    mock_db.supabase = MagicMock()

    # The RPC's return shape per migration 402: one row per ATTEMPTED
    # driver (claimed True/False), matching the header's documented
    # rationale (Python needs the full attempted set to invalidate cache
    # for each one).
    if rpc_result is None:
        rpc_result = [
            {
                "driver_id": "drv-1",
                "claimed": True,
                "driver_row": d1,
                "ride_offer_id": "offer-uuid-1",
                "insurance_written": True,
            },
            {
                "driver_id": "drv-2",
                "claimed": False,
                "driver_row": None,
                "ride_offer_id": None,
                "insurance_written": None,
            },
            {
                "driver_id": "drv-3",
                "claimed": True,
                "driver_row": d3,
                "ride_offer_id": "offer-uuid-2",
                "insurance_written": True,
            },
        ]
    mock_claim_batch = AsyncMock(return_value=rpc_result)
    # The PostgREST claim must be untouched on this path too (the pool-closed
    # fallback test flips pool_open to prove the opposite).
    mock_db.claim_driver_atomic = AsyncMock(side_effect=[d1, None, d3])
    record_period_calls = []

    async def _record_period(driver_id, period, ride_id=None):
        record_period_calls.append((driver_id, period, ride_id))

    with (
        patch("backend.routes.rides.matching._deps.db_supabase", mock_db),
        patch(
            "backend.routes.rides.matching._deps.get_app_settings",
            AsyncMock(return_value={"dispatch_direct_pool_enabled": True}),
        ),
        # Review fix (2026-09-03): the gate now checks the pool is actually
        # open before routing to it; the parity harness never opens a real
        # pool, so report it open explicitly.
        patch("backend.routes.rides.matching._dispatch_pool.is_open", return_value=pool_open),
        patch("backend.routes.rides.matching._dispatch_pool.claim_batch", mock_claim_batch),
        patch("backend.routes.rides.matching._deps.record_period_transition", _record_period),
        patch.object(
            __import__("backend.routes.rides.matching", fromlist=["logger"]).logger, "error"
        ) as mock_log_error,
        patch("backend.routes.rides.matching.match_ride_incentives", AsyncMock(return_value=[])),
        patch("backend.routes.rides.matching.incentive_display_payload", return_value=([], 0.0)),
        patch("backend.routes.rides.matching.get_service_area_polygon", return_value=None),
        patch("backend.routes.rides.matching._deps.manager") as mock_manager,
        patch("backend.routes.rides.matching._deps.send_push_notification", AsyncMock()),
        patch("backend.routes.rides.matching._deps.spawn", side_effect=lambda coro: coro.close()),
        patch(
            "backend.routes.rides.matching._shared.dispatch.resolve_matching_config",
            AsyncMock(return_value=("nearest", 0, 10.0, 2, False)),
        ),
        patch(
            "backend.routes.rides.matching._deps.filter_and_rank_drivers",
            side_effect=lambda ride, drivers, *a, **kw: [(d, float(i + 1)) for i, d in enumerate(drivers)],
        ),
        patch(
            "backend.utils.driver_presence.present_driver_ids_checked",
            AsyncMock(return_value=(set(), False)),
        ),
        patch("backend.utils.redis_client.redis_mget", AsyncMock(return_value=[None, None, None])),
        patch("backend.routes.rides.matching.datetime") as mock_datetime,
        patch("backend.routes.rides.matching._metric_inc") as mock_metric_inc,
    ):
        mock_manager.send_personal_message = AsyncMock()
        mock_datetime.now.return_value = _FIXED_NOW

        await _match_driver_to_ride_attempt(_RIDE_ID, ride=ride)

    claim_batch_call = mock_claim_batch.await_args
    claimed_rows = [r for r in rpc_result if r["claimed"]]
    equivalent_offer_rows = None
    if claim_batch_call is not None:
        # Reconstruct what the RPC's SQL insert produced FROM THE ARGUMENTS
        # matching.py actually passed (review fix, 2026-09-03: this used to
        # be rebuilt from _FIXED_NOW, i.e. _build_offer_rows(X) compared to
        # _build_offer_rows(X)). The offered_at/expires_at the RPC persists
        # are args[4]/args[5]; eta_seconds per driver is args[2] aligned to
        # args[1].
        _, arg_ids, arg_etas, _max, arg_offered_at, arg_expires_at = claim_batch_call.args
        eta_by_arg = dict(zip(arg_ids, arg_etas))
        equivalent_offer_rows = _build_offer_rows(
            [(r["driver_row"], eta_by_arg[r["driver_id"]]) for r in claimed_rows],
            _RIDE_ID,
            arg_offered_at.isoformat(),
            arg_expires_at.isoformat(),
        )

    return {
        "claim_driver_atomic_called": mock_db.claim_driver_atomic.await_count > 0,
        "claim_driver_atomic_order": [c.args[0] for c in mock_db.claim_driver_atomic.await_args_list],
        "claim_batch_call_args": claim_batch_call,
        "claimed_ids_in_order": [r["driver_id"] for r in claimed_rows],
        "equivalent_offer_rows": equivalent_offer_rows,
        "metric_inc_calls": mock_metric_inc.call_args_list,
        "invalidate_calls": mock_db.invalidate_driver_cache.await_args_list,
        "insurance_calls": record_period_calls,
        "log_error_calls": mock_log_error.call_args_list,
    }


async def test_postgrest_path_claim_order_and_skip(scenario):
    """Sanity check on the PostgREST branch in isolation before comparing:
    driver 2's lost race is a genuine skip, not silently absorbed."""
    mock_db = MagicMock()
    result = await _run_postgrest_path(scenario, mock_db)

    assert result["claim_call_order"] == ["drv-1", "drv-2", "drv-3"]
    assert [row["driver_id"] for row in result["offer_rows"]] == scenario["claimed_ids_expected"]
    assert [c[0] for c in result["insurance_calls"]] == scenario["claimed_ids_expected"]
    assert all(c[1] == 2 and c[2] == _RIDE_ID for c in result["insurance_calls"])


async def test_direct_pool_path_never_calls_claim_driver_atomic(scenario):
    mock_db = MagicMock()
    result = await _run_direct_pool_path(scenario, mock_db)

    assert result["claim_driver_atomic_called"] is False
    assert result["claimed_ids_in_order"] == scenario["claimed_ids_expected"]


async def test_postgrest_path_never_calls_claim_batch(scenario):
    mock_db = MagicMock()
    with patch("backend.routes.rides.matching._dispatch_pool.claim_batch", AsyncMock()) as mock_claim_batch:
        await _run_postgrest_path(scenario, mock_db)
    mock_claim_batch.assert_not_called()


async def test_claimed_driver_set_and_order_are_identical_across_paths(scenario):
    """Assertion 1 from the module docstring."""
    postgrest_result = await _run_postgrest_path(scenario, MagicMock())
    direct_result = await _run_direct_pool_path(scenario, MagicMock())

    postgrest_claimed_order = [row["driver_id"] for row in postgrest_result["offer_rows"]]

    assert postgrest_claimed_order == direct_result["claimed_ids_in_order"] == scenario["claimed_ids_expected"]


async def test_ride_offers_rows_are_identical_across_paths(scenario):
    """Assertion 2 from the module docstring."""
    postgrest_result = await _run_postgrest_path(scenario, MagicMock())
    direct_result = await _run_direct_pool_path(scenario, MagicMock())

    assert postgrest_result["offer_rows"] == direct_result["equivalent_offer_rows"]
    # Spot-check the shape hasn't silently drifted from _build_offer_rows'
    # documented columns (matching.py:132-150).
    for row in postgrest_result["offer_rows"]:
        assert set(row.keys()) == {"ride_id", "driver_id", "status", "eta_seconds", "offered_at", "expires_at"}
        assert row["status"] == "pending"
        assert row["ride_id"] == _RIDE_ID


async def test_insurance_write_target_set_is_identical_across_paths(scenario):
    """Assertion 3 from the module docstring.

    The PostgREST path's ACTUAL record_period_transition call args are the
    ground truth for "who gets an insurance-period-2 write". The
    direct-pool path performs this write inside migration 402's SQL, not
    in Python -- so parity here means the driver ids the RPC reports as
    claimed=True are exactly the ids the PostgREST path separately, really
    called record_period_transition for.
    """
    postgrest_result = await _run_postgrest_path(scenario, MagicMock())
    direct_result = await _run_direct_pool_path(scenario, MagicMock())

    postgrest_insurance_ids = [c[0] for c in postgrest_result["insurance_calls"]]
    assert postgrest_insurance_ids == direct_result["claimed_ids_in_order"] == scenario["claimed_ids_expected"]


async def test_offer_sent_metric_count_is_identical_across_paths(scenario):
    """Assertion 4 from the module docstring: both paths increment
    spinr_dispatch_offer_sent_total by the same claimed count (2), and
    each also emits the new spinr_dispatch_claim_path_total with the
    correct path label."""
    postgrest_result = await _run_postgrest_path(scenario, MagicMock())
    direct_result = await _run_direct_pool_path(scenario, MagicMock())

    def _find_call(calls, name):
        return [c for c in calls if c.args and c.args[0] == name]

    pg_offer_sent = _find_call(postgrest_result["metric_inc_calls"], "spinr_dispatch_offer_sent_total")
    direct_offer_sent = _find_call(direct_result["metric_inc_calls"], "spinr_dispatch_offer_sent_total")
    assert len(pg_offer_sent) == 1 and len(direct_offer_sent) == 1
    assert pg_offer_sent[0].kwargs.get("by") == direct_offer_sent[0].kwargs.get("by") == 2

    pg_path = _find_call(postgrest_result["metric_inc_calls"], "spinr_dispatch_claim_path_total")
    direct_path = _find_call(direct_result["metric_inc_calls"], "spinr_dispatch_claim_path_total")
    assert len(pg_path) == 1 and pg_path[0].kwargs.get("labels") == {"path": "postgrest"}
    assert len(direct_path) == 1 and direct_path[0].kwargs.get("labels") == {"path": "direct"}


async def test_direct_pool_rpc_receives_ranked_ids_aligned_etas_and_the_offer_window(scenario):
    """Review fix (2026-09-03): the RPC's positional contract was captured
    but never asserted, so a swapped argument or a misaligned eta list would
    have passed every parity test."""
    direct_result = await _run_direct_pool_path(scenario, MagicMock())

    args = direct_result["claim_batch_call_args"].args
    assert args[0] == _RIDE_ID
    assert args[1] == ["drv-1", "drv-2", "drv-3"]
    assert args[2] == [scenario["eta_by_id"][d] for d in args[1]]  # index-aligned
    assert args[3] == 2  # max_offers from resolve_matching_config
    assert args[4] == _FIXED_NOW
    assert args[5] == _FIXED_NOW + timedelta(seconds=_OFFER_TIMEOUT)


async def test_flag_on_with_pool_closed_falls_back_to_postgrest_loudly(scenario):
    """Review fix (2026-09-03): flag on + pool never opened (DSN unset, flag
    flipped on against a running process, non-production open failure)
    must NOT raise on every attempt — that stalled 100% of rides. It logs at
    ERROR, counts the mismatch, and takes the PostgREST claim path."""
    direct_result = await _run_direct_pool_path(scenario, MagicMock(), pool_open=False)

    assert direct_result["claim_batch_call_args"] is None
    assert direct_result["claim_driver_atomic_order"] == ["drv-1", "drv-2", "drv-3"]
    assert [c[0] for c in direct_result["insurance_calls"]] == scenario["claimed_ids_expected"]

    path_calls = [
        c for c in direct_result["metric_inc_calls"] if c.args and c.args[0] == "spinr_dispatch_claim_path_total"
    ]
    assert [c.kwargs.get("labels") for c in path_calls] == [
        {"path": "postgrest_pool_unavailable"},
        {"path": "postgrest"},
    ]
    assert any("direct pool is not open" in str(c.args[0]) for c in direct_result["log_error_calls"])


async def test_direct_pool_insurance_write_failure_is_logged_and_counted(scenario):
    """Review fix (2026-09-03): a failed Period-2 write inside the RPC used
    to be a Postgres-side RAISE WARNING only. The flag-on path must emit the
    same ERROR log + metric the PostgREST path emits."""
    d1, d2, d3 = scenario["candidates"]
    rpc_result = [
        {"driver_id": "drv-1", "claimed": True, "driver_row": d1, "ride_offer_id": "o1", "insurance_written": False},
        {"driver_id": "drv-2", "claimed": False, "driver_row": None, "ride_offer_id": None, "insurance_written": None},
        {"driver_id": "drv-3", "claimed": True, "driver_row": d3, "ride_offer_id": "o2", "insurance_written": True},
    ]
    direct_result = await _run_direct_pool_path(scenario, MagicMock(), rpc_result=rpc_result)

    assert direct_result["claimed_ids_in_order"] == ["drv-1", "drv-3"]
    failed = [
        c
        for c in direct_result["metric_inc_calls"]
        if c.args and c.args[0] == "spinr_insurance_period_write_failed_total"
    ]
    assert len(failed) == 1
    assert failed[0].args[1] == {"reason": "direct_pool", "period": "2"}
    assert any("Period 2" in str(c.args[0]) and "drv-1" in str(c.args[0]) for c in direct_result["log_error_calls"])


async def test_direct_pool_cache_invalidation_is_bounded_and_carries_user_id(scenario):
    """Review fix (2026-09-03): pre-claim invalidation is bounded to the
    first max_offers candidates (not the whole ranked list), the post-call
    loop covers the exact attempted set the RPC reports, and both evict the
    by-user key."""
    direct_result = await _run_direct_pool_path(scenario, MagicMock())

    calls = [(c.kwargs.get("driver_id"), c.kwargs.get("user_id")) for c in direct_result["invalidate_calls"]]
    # pre-claim: max_offers=2 -> drv-1, drv-2; post-call: all three attempted.
    assert sorted(calls[:2]) == [("drv-1", "user-drv-1"), ("drv-2", "user-drv-2")]
    assert sorted(calls[2:]) == [("drv-1", "user-drv-1"), ("drv-2", "user-drv-2"), ("drv-3", "user-drv-3")]
