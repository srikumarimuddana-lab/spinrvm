"""Tests for insurance_period_gps_correction.py -- the write path for
`driver_insurance_period_corrections` (migration 355, ACTION_ITEMS.md B34),
built on top of insurance_period_reconstruction_verification.py's
already-tested classification.

Mirrors test_insurance_period_reconstruction_verification.py's fake-supabase
harness conventions (this module reads its own `supabase_client` parameter
directly, not through `db_supabase`/`repositories`), extended with `.in_()`
support since this module's fetch functions use it (unlike the verification
module's `.eq()`-only queries).
"""

from __future__ import annotations

import pytest

from backend.services import insurance_period_gps_correction as corr
from backend.services.insurance_period_reconstruction_verification import RideVerification, VerificationPlan

pytestmark = pytest.mark.unit


# ── fake supabase (extends the verification test's harness with .in_()) ──


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._eq = None
        self._in = None

    def select(self, *_a, **_k):
        return self

    def insert(self, rows):
        self._insert_rows = rows
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def in_(self, col, values):
        self._in = (col, set(values))
        return self

    def execute(self):
        if hasattr(self, "_insert_rows"):
            inserted = list(self._insert_rows)
            self.store.setdefault(self.table, [])
            self.store[self.table].extend(inserted)
            return _FakeExecute(inserted)
        rows = list(self.store.get(self.table, []))
        if self._eq:
            rows = [r for r in rows if r.get(self._eq[0]) == self._eq[1]]
        if self._in:
            col, values = self._in
            rows = [r for r in rows if r.get(col) in values]
        return _FakeExecute(rows)


class _FakeSupabase:
    def __init__(self, periods=None, corrections=None):
        self.store = {
            "driver_insurance_periods": periods or [],
            "driver_insurance_period_corrections": corrections or [],
        }
        self.insert_calls: list[tuple[str, list]] = []

    def table(self, name):
        q = _FakeQuery(name, self.store)
        orig_execute = q.execute

        def tracking_execute():
            result = orig_execute()
            if hasattr(q, "_insert_rows"):
                self.insert_calls.append((name, q._insert_rows))
            return result

        q.execute = tracking_execute
        return q


# ── fetch_period_2_rows_for_rides ──────────────────────────────────────


def test_fetch_period_2_rows_empty_ride_ids_short_circuits():
    fake = _FakeSupabase()
    assert corr.fetch_period_2_rows_for_rides(fake, []) == {}


def test_fetch_period_2_rows_filters_to_period_2_only():
    fake = _FakeSupabase(
        periods=[
            {"id": "p2-row", "ride_id": "ride-1", "period": 2, "ended_at": "2026-04-19T02:55:16.905+00:00"},
            {"id": "p3-row", "ride_id": "ride-1", "period": 3, "ended_at": "2026-04-19T03:01:42.698+00:00"},
            {"id": "other-ride-p2", "ride_id": "ride-2", "period": 2, "ended_at": None},
        ]
    )
    out = corr.fetch_period_2_rows_for_rides(fake, ["ride-1"])
    assert out == {"ride-1": {"id": "p2-row", "ended_at": "2026-04-19T02:55:16.905+00:00"}}


# ── fetch_existing_correction_period_ids ───────────────────────────────


def test_fetch_existing_corrections_empty_period_ids_short_circuits():
    fake = _FakeSupabase()
    assert corr.fetch_existing_correction_period_ids(fake, []) == set()


def test_fetch_existing_corrections_returns_set_of_original_period_ids():
    fake = _FakeSupabase(corrections=[{"original_period_id": "p2-row-1"}, {"original_period_id": "p2-row-2"}])
    out = corr.fetch_existing_correction_period_ids(fake, ["p2-row-1", "p2-row-2", "p2-row-3"])
    assert out == {"p2-row-1", "p2-row-2"}


# ── build_correction_plan ──────────────────────────────────────────────


def _diverges_result(ride_id="ride-1", real_period2=("2026-04-19T02:45:00+00:00", "2026-04-19T02:55:16.905+00:00")):
    return RideVerification(
        ride_id,
        "DIVERGES",
        {"real_period2": list(real_period2), "delta_seconds": {"p2_start_vs_driver_arrived_at": -600.0}},
    )


def test_only_diverges_rides_are_considered_everything_else_skipped_by_status():
    plan = VerificationPlan(
        results=[
            RideVerification("ride-confirmed", "CONFIRMED"),
            RideVerification("ride-no-csv", "NO_CSV_DATA"),
            RideVerification("ride-ambiguous", "AMBIGUOUS_SPAN_COUNT"),
            RideVerification("ride-excluded", "EXCLUDED_BY_MIGRATION_332"),
        ]
    )
    out = corr.build_correction_plan(plan, {}, set(), operator_user_id="admin-1")
    assert out.to_insert == []
    assert out.skipped == {
        "CONFIRMED": 1,
        "NO_CSV_DATA": 1,
        "AMBIGUOUS_SPAN_COUNT": 1,
        "EXCLUDED_BY_MIGRATION_332": 1,
    }


def test_diverges_ride_with_period_2_row_produces_a_correction_record():
    plan = VerificationPlan(results=[_diverges_result()])
    period_rows = {"ride-1": {"id": "p2-row-1", "ended_at": "2026-04-19T02:55:16.905+00:00"}}
    out = corr.build_correction_plan(plan, period_rows, set(), operator_user_id="admin-1", reason="test reason")
    assert len(out.to_insert) == 1
    rec = out.to_insert[0]
    assert rec.ride_id == "ride-1"
    assert rec.original_period_id == "p2-row-1"
    assert rec.corrected_started_at == "2026-04-19T02:45:00+00:00"
    assert rec.corrected_ended_at == "2026-04-19T02:55:16.905+00:00"
    assert rec.reason == "test reason"
    assert rec.corrected_by == "admin-1"


def test_diverges_ride_uses_default_reason_when_none_given():
    plan = VerificationPlan(results=[_diverges_result()])
    period_rows = {"ride-1": {"id": "p2-row-1", "ended_at": None}}
    out = corr.build_correction_plan(plan, period_rows, set(), operator_user_id="admin-1")
    assert out.to_insert[0].reason == corr.DEFAULT_REASON


def test_diverges_ride_missing_period_2_row_is_skipped_not_fabricated():
    plan = VerificationPlan(results=[_diverges_result(ride_id="ride-missing")])
    out = corr.build_correction_plan(plan, {}, set(), operator_user_id="admin-1")
    assert out.to_insert == []
    assert out.skipped == {"DIVERGES_BUT_NO_PERIOD_2_ROW": 1}


def test_diverges_ride_already_corrected_is_skipped():
    plan = VerificationPlan(results=[_diverges_result()])
    period_rows = {"ride-1": {"id": "p2-row-1", "ended_at": None}}
    out = corr.build_correction_plan(plan, period_rows, {"p2-row-1"}, operator_user_id="admin-1")
    assert out.to_insert == []
    assert out.skipped == {"ALREADY_CORRECTED": 1}


def test_diverges_ride_missing_real_period2_detail_is_skipped_defensively():
    # Contract-violation defense: build_correction_plan must not assume
    # build_verification_plan's internal shape holds forever without
    # checking -- a DIVERGES result missing real_period2 must be skipped,
    # never crash or fabricate a boundary.
    plan = VerificationPlan(results=[RideVerification("ride-1", "DIVERGES", {})])
    period_rows = {"ride-1": {"id": "p2-row-1", "ended_at": None}}
    out = corr.build_correction_plan(plan, period_rows, set(), operator_user_id="admin-1")
    assert out.to_insert == []
    assert out.skipped == {"DIVERGES_BUT_NO_REAL_BOUNDARY": 1}


def test_multiple_diverges_rides_each_get_their_own_record():
    plan = VerificationPlan(
        results=[
            _diverges_result(ride_id="ride-1"),
            _diverges_result(ride_id="ride-2", real_period2=("2026-04-20T10:00:00+00:00", "2026-04-20T10:10:00+00:00")),
            RideVerification("ride-3", "CONFIRMED"),
        ]
    )
    period_rows = {
        "ride-1": {"id": "p2-row-1", "ended_at": None},
        "ride-2": {"id": "p2-row-2", "ended_at": None},
    }
    out = corr.build_correction_plan(plan, period_rows, set(), operator_user_id="admin-1")
    assert {rec.ride_id for rec in out.to_insert} == {"ride-1", "ride-2"}
    assert out.skipped == {"CONFIRMED": 1}


# ── commit_correction_plan ──────────────────────────────────────────────


def test_commit_empty_plan_is_a_no_op():
    fake = _FakeSupabase()
    plan = corr.CorrectionPlan()
    result = corr.commit_correction_plan(fake, plan)
    assert result == {"inserted": 0}
    assert fake.insert_calls == []


def test_commit_plan_inserts_every_record_in_one_batch():
    fake = _FakeSupabase()
    plan = corr.CorrectionPlan(
        to_insert=[
            corr.CorrectionRecord(
                ride_id="ride-1",
                original_period_id="p2-row-1",
                corrected_started_at="2026-04-19T02:45:00+00:00",
                corrected_ended_at="2026-04-19T02:55:16.905+00:00",
                reason="test",
                corrected_by="admin-1",
            ),
            corr.CorrectionRecord(
                ride_id="ride-2",
                original_period_id="p2-row-2",
                corrected_started_at="2026-04-20T10:00:00+00:00",
                corrected_ended_at=None,
                reason="test",
                corrected_by="admin-1",
            ),
        ]
    )
    result = corr.commit_correction_plan(fake, plan)
    assert result == {"inserted": 2}
    assert len(fake.insert_calls) == 1
    table_name, rows = fake.insert_calls[0]
    assert table_name == "driver_insurance_period_corrections"
    assert len(rows) == 2
    assert rows[0] == {
        "original_period_id": "p2-row-1",
        "corrected_started_at": "2026-04-19T02:45:00+00:00",
        "corrected_ended_at": "2026-04-19T02:55:16.905+00:00",
        "reason": "test",
        "corrected_by": "admin-1",
    }
