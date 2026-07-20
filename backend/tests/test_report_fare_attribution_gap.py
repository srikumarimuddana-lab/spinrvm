"""Tests for scripts/report_fare_attribution_gap.py — the read-only historical
fare-attribution gap quantifier. The dollar figures it emits drive a back-pay /
T4A decision, so the pure aggregation is unit-tested."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from report_fare_attribution_gap import compute_gap_report  # noqa: E402


def _ride(
    rid="r", driver="d", total="8.00", driver_e="3.90", admin="2.00", tip="0.00", completed="2026-06-15T12:00:00Z"
):
    return {
        "id": rid,
        "driver_id": driver,
        "total_fare": total,
        "driver_earnings": driver_e,
        "admin_earnings": admin,
        "tip_amount": tip,
        "ride_completed_at": completed,
    }


def test_flags_understated_ride_and_sums_gap():
    # 8.00 charged, attributed 3.90 + 2.00 = 5.90 → 2.10 unallocated.
    report = compute_gap_report([_ride()])
    assert report["affected_rides"] == 1
    assert report["total_unallocated"] == 2.10


def test_reconciled_ride_excluded():
    report = compute_gap_report([_ride(driver_e="6.00")])  # 6.00 + 2.00 = 8.00
    assert report["affected_rides"] == 0
    assert report["total_unallocated"] == 0.0


def test_tip_is_subtracted_before_comparing():
    # driver_earnings 7.00 includes a 1.00 tip → fare share 6.00, reconciles.
    report = compute_gap_report([_ride(driver_e="7.00", tip="1.00")])
    assert report["affected_rides"] == 0


def test_legacy_ride_without_earnings_excluded():
    ride = _ride()
    ride["driver_earnings"] = None
    ride["admin_earnings"] = None
    report = compute_gap_report([ride])
    assert report["affected_rides"] == 0


def test_groups_by_year_and_driver():
    rides = [
        _ride(rid="a", driver="d1", completed="2025-03-01T12:00:00Z"),  # gap 2.10
        _ride(rid="b", driver="d1", completed="2026-03-01T12:00:00Z"),  # gap 2.10
        _ride(rid="c", driver="d2", completed="2026-04-01T12:00:00Z"),  # gap 2.10
    ]
    report = compute_gap_report(rides)
    assert report["affected_rides"] == 3
    assert report["total_unallocated"] == 6.30
    assert report["by_year"]["2025"]["rides"] == 1
    assert report["by_year"]["2026"]["rides"] == 2
    assert report["by_year"]["2026"]["unallocated"] == 4.20
    assert report["by_driver"]["d1"]["rides"] == 2
    assert report["by_driver"]["d2"]["unallocated"] == 2.10
