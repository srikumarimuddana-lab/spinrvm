"""Tests for quote-vs-measured distance reconciliation.

The pure evaluate_reconciliation() is the core: per-ride outlier detection plus
the systematic-bias signal that would trip within a day on a platform-wide
undercharge like the haversine fare bug.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from utils.distance_reconciliation import (
    AGG_MIN_SAMPLES,
    _run_reconciliation_tick,
    evaluate_reconciliation,
)


def _ride(rid, quoted, measured, basis=None):
    ride = {"id": rid, "planned_distance_km": quoted, "actual_distance_km": measured}
    if basis is not None:
        ride["ride_metrics"] = {"phases": {"trip_in_progress": {"distance_basis": basis}}}
    return ride


class TestEvaluateReconciliation:
    def test_within_band_no_divergence(self):
        ratios, divergences, agg = evaluate_reconciliation([_ride("r1", 1.8, 1.9)])
        assert len(ratios) == 1
        assert divergences == []
        assert agg["count"] == 1

    def test_undercharge_outlier_flagged(self):
        # Measured 1.8 km vs quoted 0.7 km → ratio 2.57, well over the band.
        _ratios, divergences, _agg = evaluate_reconciliation([_ride("r1", 0.7, 1.8, basis="road_route")])
        assert len(divergences) == 1
        rid, details = divergences[0]
        assert rid == "r1"
        assert details["ratio"] == 2.571
        assert details["measured_distance_basis"] == "road_route"

    def test_overcharge_outlier_flagged(self):
        _ratios, divergences, _agg = evaluate_reconciliation([_ride("r1", 5.0, 1.0)])  # ratio 0.2
        assert len(divergences) == 1

    def test_unusable_distances_skipped(self):
        ratios, divergences, agg = evaluate_reconciliation(
            [_ride("r1", 0, 1.8), _ride("r2", 1.8, 0), _ride("r3", None, None)]
        )
        assert ratios == []
        assert divergences == []
        assert agg["count"] == 0
        assert agg["mean_ratio"] is None

    def test_systematic_bias_detected(self):
        # Enough samples all consistently under-quoted → biased aggregate.
        rides = [_ride(f"r{i}", 1.0, 2.5) for i in range(AGG_MIN_SAMPLES)]
        _ratios, _div, agg = evaluate_reconciliation(rides)
        assert agg["biased"] is True
        assert agg["mean_ratio"] == 2.5

    def test_no_bias_when_centered(self):
        rides = [_ride(f"r{i}", 1.0, 1.0) for i in range(AGG_MIN_SAMPLES)]
        _ratios, _div, agg = evaluate_reconciliation(rides)
        assert agg["biased"] is False

    def test_no_bias_below_min_samples(self):
        # Skewed but too few samples to call it systematic.
        rides = [_ride(f"r{i}", 1.0, 2.5) for i in range(AGG_MIN_SAMPLES - 1)]
        _ratios, _div, agg = evaluate_reconciliation(rides)
        assert agg["biased"] is False


def _run(coro):
    return asyncio.run(coro)


def test_tick_marks_batch_and_records_divergence():
    rides = [_ride("r1", 0.7, 1.8), _ride("r2", 1.8, 1.9)]
    with (
        patch("utils.distance_reconciliation.db_supabase.get_rows", AsyncMock(return_value=rides)),
        patch("utils.distance_reconciliation.db_supabase.update_one", AsyncMock(return_value={"id": "r1"})) as update,
        patch("utils.distance_reconciliation.record_integrity_event", AsyncMock(return_value=True)) as record,
    ):
        _run(_run_reconciliation_tick())

    # r1 is the outlier → one integrity event; both rides claimed in one update.
    assert record.await_count == 1
    filters = update.await_args[0][1]
    assert set(filters["id"]["$in"]) == {"r1", "r2"}
    assert "distance_reconciled_at" in update.await_args[0][2]


def test_tick_noop_on_empty_batch():
    with (
        patch("utils.distance_reconciliation.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch("utils.distance_reconciliation.db_supabase.update_one", AsyncMock()) as update,
    ):
        _run(_run_reconciliation_tick())
    update.assert_not_awaited()
