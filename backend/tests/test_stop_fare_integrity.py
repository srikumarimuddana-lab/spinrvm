"""Regression tests for finding 13 (WS-10): mid-trip stop fare integrity.

Before this fix, _reestimate_fare_for_stops only recomputed distance/time/
total_fare but never called calculate_all_fees — so grand_total, tax_amount,
tax_breakdown, driver_earnings, admin_earnings, and fare_breakdown_snapshot
were all stale after a stop add/remove. The rider saw one price; settlement
charged a different (lower) amount.

Fix: _reestimate_fare_for_stops is now async, calls calculate_all_fees to
recompute all derived fields atomically, and refreshes the fare_breakdown_snapshot.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SHARED_SRC = (_ROOT / "routes" / "rides" / "_shared.py").read_text()
_STOPS_SRC = (_ROOT / "routes" / "rides" / "stops.py").read_text()


class TestReestimateFareIsAsync:
    def test_function_is_async(self):
        assert "async def _reestimate_fare_for_stops" in _SHARED_SRC


class TestReestimateFareRecomputesTaxAndFees:
    def test_calls_calculate_all_fees(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert "calculate_all_fees" in section

    def test_includes_grand_total(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"grand_total"' in section

    def test_includes_tax_amount(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"tax_amount"' in section

    def test_includes_tax_breakdown(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"tax_breakdown"' in section

    def test_includes_driver_earnings(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"driver_earnings"' in section

    def test_includes_admin_earnings(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"admin_earnings"' in section


class TestFareBreakdownSnapshotRefreshed:
    def test_includes_fare_breakdown_snapshot(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"fare_breakdown_snapshot"' in section

    def test_snapshot_has_lines_and_grand_total(self):
        idx = _SHARED_SRC.index("async def _reestimate_fare_for_stops")
        body = _SHARED_SRC[idx:]
        next_def = body.find("\ndef ", 10)
        if next_def == -1:
            next_def = body.find("\nasync def ", 10)
        section = body[:next_def] if next_def > 0 else body
        assert '"lines"' in section
        assert "_build_fare_breakdown" in section


class TestStopsHandlersAwait:
    def test_add_stop_awaits_reestimate(self):
        assert "await _reestimate_fare_for_stops" in _STOPS_SRC

    def test_remove_stop_awaits_reestimate(self):
        lines = _STOPS_SRC.split("\n")
        await_count = sum(1 for line in lines if "await _reestimate_fare_for_stops" in line)
        assert await_count >= 2, "Both add and remove handlers must await"


class TestWsPayloadIncludesGrandTotal:
    def test_driver_ws_includes_grand_total(self):
        assert '"grand_total"' in _STOPS_SRC
