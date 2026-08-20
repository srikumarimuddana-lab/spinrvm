"""Tests for insurance_period_reconstruction_verification.py -- Oct 30
checklist item #5(a): re-check migration 332's Period-2/Period-3
reconstruction using driverlocationlogs.csv's real phase-boundary
timestamps.

Mirrors test_legacy_vehicle_history_backfill.py's fake-supabase harness
(this module reads its own ``supabase_client`` parameter directly, not
through ``db_supabase``/``repositories``).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from backend.services import insurance_period_reconstruction_verification as svc

pytestmark = pytest.mark.unit


# ── fixtures ──────────────────────────────────────────────────────────


def _candidate(**overrides):
    row = dict(
        ride_id="ride-1",
        driver_id="spinr-driver-1",
        old_driver_id="mongo-driver-1",
        old_booking_id="mongo-booking-1",
        driver_arrived_at="2026-04-19 02:54:56.593+00",
        started_at="2026-04-19 02:55:16.905+00",
        ride_completed_at="2026-04-19 03:01:42.698+00",
        excluded_by_migration_332=False,
    )
    row.update(overrides)
    return svc.LegacyRideCandidate(**row)


def _span(phase, driver_id="mongo-driver-1", start_ms=None, end_ms=None):
    return svc.PhaseSpan(phase=phase, driver_id=driver_id, start_time_ms=start_ms, end_time_ms=end_ms)


def _ms(pg_timestamp: str) -> int:
    """Epoch ms for a Postgres-style timestamp string, computed the same
    way the service module parses proxy timestamps -- avoids hand-typed
    epoch constants drifting from the human-readable fixture strings."""
    from datetime import datetime

    v = pg_timestamp.replace(" ", "T", 1)
    return int(datetime.fromisoformat(v).timestamp() * 1000)


# ── enumerate_distinct_phases / stream_driverlocationlogs_phase_spans ──


def _write_csv(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "driverlocationlogs.csv"
    fieldnames = [
        "#",
        "_id",
        "__v",
        "created_at",
        "distance",
        "driver_id",
        "end_time",
        "phase",
        "start_time",
        "updated_at",
        "way_points",
        "ride_id",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            base = {k: "" for k in fieldnames}
            base.update(row)
            writer.writerow(base)
    return path


def test_enumerate_distinct_phases_counts_correctly(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            {"phase": "idle"},
            {"phase": "idle"},
            {"phase": "going_to_pickup"},
            {"phase": "on_ride"},
        ],
    )
    counts = svc.enumerate_distinct_phases(path)
    assert counts == {"idle": 2, "going_to_pickup": 1, "on_ride": 1}


def test_stream_phase_spans_filters_to_target_booking_ids_only(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            {
                "ride_id": "booking-1",
                "phase": "going_to_pickup",
                "driver_id": "d1",
                "start_time": "100",
                "end_time": "200",
            },
            {"ride_id": "booking-2", "phase": "on_ride", "driver_id": "d2", "start_time": "300", "end_time": "400"},
            {"ride_id": "", "phase": "idle", "driver_id": "d1", "start_time": "1", "end_time": "2"},
        ],
    )
    spans = svc.stream_driverlocationlogs_phase_spans(path, {"booking-1"})
    assert set(spans) == {"booking-1"}
    assert len(spans["booking-1"]) == 1
    assert spans["booking-1"][0].phase == "going_to_pickup"
    assert spans["booking-1"][0].start_time_ms == 100


def test_stream_phase_spans_never_returns_way_points(tmp_path):
    path = _write_csv(
        tmp_path,
        [
            {
                "ride_id": "booking-1",
                "phase": "on_ride",
                "driver_id": "d1",
                "start_time": "100",
                "end_time": "200",
                "way_points": '[{"lat": "50.1", "lng": "-104.1"}]',
            }
        ],
    )
    spans = svc.stream_driverlocationlogs_phase_spans(path, {"booking-1"})
    span = spans["booking-1"][0]
    assert not hasattr(span, "way_points")
    # PhaseSpan is a frozen dataclass with a fixed field set -- confirm no
    # lat/lng-shaped value leaked into any of its actual fields.
    for value in (span.phase, span.driver_id, span.start_time_ms, span.end_time_ms):
        assert "lat" not in str(value) and "lng" not in str(value)


def test_stream_phase_spans_handles_huge_field_without_pandas(tmp_path):
    """way_points can be very large (long routes); the streaming reader must
    not choke on it even though it never returns the value."""
    huge_way_points = '[{"lat": "50.0", "lng": "-104.0"}]' * 20000  # ~600KB
    path = _write_csv(
        tmp_path,
        [
            {
                "ride_id": "booking-1",
                "phase": "on_ride",
                "driver_id": "d1",
                "start_time": "100",
                "end_time": "200",
                "way_points": huge_way_points,
            }
        ],
    )
    spans = svc.stream_driverlocationlogs_phase_spans(path, {"booking-1"})
    assert len(spans["booking-1"]) == 1


# ── build_verification_plan ──────────────────────────────────────────


def test_excluded_by_migration_332_short_circuits():
    c = _candidate(ride_id="ride-x", excluded_by_migration_332=True)
    plan = svc.build_verification_plan([c], {})
    assert plan.results[0].status == "EXCLUDED_BY_MIGRATION_332"


def test_no_csv_data_when_booking_has_no_spans():
    c = _candidate()
    plan = svc.build_verification_plan([c], {})
    assert plan.results[0].status == "NO_CSV_DATA"


def test_unknown_phase_value_is_conservative_not_guessed():
    c = _candidate()
    spans = {"mongo-booking-1": [_span("some_new_phase_value")]}
    plan = svc.build_verification_plan([c], spans)
    assert plan.results[0].status == "UNKNOWN_PHASE_VALUE"
    assert plan.results[0].detail["unknown_phases"] == ["some_new_phase_value"]


def test_driver_id_mismatch_when_no_span_matches_old_driver_id():
    c = _candidate(old_driver_id="mongo-driver-1")
    spans = {"mongo-booking-1": [_span("going_to_pickup", driver_id="some-other-driver")]}
    plan = svc.build_verification_plan([c], spans)
    assert plan.results[0].status == "DRIVER_ID_MISMATCH"


def test_ambiguous_span_count_when_not_exactly_one_of_each_phase():
    c = _candidate()
    spans = {
        "mongo-booking-1": [
            _span("going_to_pickup", start_ms=1, end_ms=2),
            _span("going_to_pickup", start_ms=3, end_ms=4),
            _span("on_ride", start_ms=5, end_ms=6),
        ]
    }
    plan = svc.build_verification_plan([c], spans)
    assert plan.results[0].status == "AMBIGUOUS_SPAN_COUNT"
    assert plan.results[0].detail["n_going_to_pickup"] == 2


def test_confirmed_when_real_boundaries_match_proxy_within_tolerance():
    # driver_arrived_at == started_at (2026-04-19T02:55:16.905+00:00) minus
    # ~0s of "going to pickup" duration -- pick real timestamps that land
    # within the default 60s tolerance of every proxy boundary.
    c = _candidate(
        driver_arrived_at="2026-04-19 02:54:56.593+00",
        started_at="2026-04-19 02:55:16.905+00",
        ride_completed_at="2026-04-19 03:01:42.698+00",
    )
    arrived_ms = _ms("2026-04-19 02:54:56.593+00")
    started_ms = _ms("2026-04-19 02:55:16.905+00")
    completed_ms = _ms("2026-04-19 03:01:42.698+00")
    spans = {
        "mongo-booking-1": [
            _span("going_to_pickup", start_ms=arrived_ms, end_ms=started_ms),  # ~= driver_arrived_at..started_at
            _span("on_ride", start_ms=started_ms, end_ms=completed_ms),  # ~= started_at..ride_completed_at
        ]
    }
    plan = svc.build_verification_plan([c], spans)
    assert plan.results[0].status == "CONFIRMED"


def test_diverges_when_a_boundary_exceeds_tolerance():
    c = _candidate(
        driver_arrived_at="2026-04-19 02:54:56.593+00",
        started_at="2026-04-19 02:55:16.905+00",
        ride_completed_at="2026-04-19 03:01:42.698+00",
    )
    arrived_ms = _ms("2026-04-19 02:54:56.593+00")
    started_ms = _ms("2026-04-19 02:55:16.905+00")
    completed_ms = _ms("2026-04-19 03:01:42.698+00")
    # going_to_pickup starts ~10 minutes before driver_arrived_at -- the
    # real, median-scale divergence this task actually found.
    real_p2_start_ms = arrived_ms - 10 * 60 * 1000
    spans = {
        "mongo-booking-1": [
            _span("going_to_pickup", start_ms=real_p2_start_ms, end_ms=started_ms),
            _span("on_ride", start_ms=started_ms, end_ms=completed_ms),
        ]
    }
    plan = svc.build_verification_plan([c], spans)
    assert plan.results[0].status == "DIVERGES"
    assert abs(plan.results[0].detail["delta_seconds"]["p2_start_vs_driver_arrived_at"] + 600) < 1


def test_period_3_end_boundary_alone_can_trigger_diverges():
    c = _candidate()
    arrived_ms = _ms(c.driver_arrived_at)
    started_ms = _ms(c.started_at)
    completed_ms = _ms(c.ride_completed_at)
    spans = {
        "mongo-booking-1": [
            _span("going_to_pickup", start_ms=arrived_ms, end_ms=started_ms),
            # on_ride ends 5 minutes after ride_completed_at -- clean p2/p3
            # start match but a real p3-end divergence.
            _span("on_ride", start_ms=started_ms, end_ms=completed_ms + 5 * 60 * 1000),
        ]
    }
    plan = svc.build_verification_plan([c], spans)
    assert plan.results[0].status == "DIVERGES"


# ── apply_verification_plan always refuses ─────────────────────────────


def test_apply_verification_plan_always_raises():
    plan = svc.build_verification_plan([], {})
    with pytest.raises(RuntimeError, match="no apply path"):
        svc.apply_verification_plan(plan)


# ── fetch_migration_332_candidate_rides (fake supabase) ────────────────


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store

    def select(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._eq = (col, val)
        return self

    def execute(self):
        rows = list(self.store.get(self.table, []))
        eq = getattr(self, "_eq", None)
        if eq:
            rows = [r for r in rows if r.get(eq[0]) == eq[1]]
        return _FakeExecute(rows)


class _FakeSupabase:
    def __init__(self, rides=None, periods=None):
        self.store = {
            "rides": rides or [],
            "driver_insurance_periods": periods or [],
        }

    def table(self, name):
        return _FakeQuery(name, self.store)


def test_fetch_candidates_includes_reconstructed_and_excluded_rides():
    fake = _FakeSupabase(
        rides=[
            {
                "id": "covered-ride",
                "driver_id": "d1",
                "legacy_import_metadata": {
                    "source": svc.MIGRATION_332_SOURCE,
                    "old_driver_id": "od1",
                    "old_booking_id": "ob1",
                },
                "driver_arrived_at": "x",
                "started_at": "y",
                "ride_completed_at": "z",
            },
            {
                "id": "bda2a258-7987-4344-882e-ca202df17d43",  # one of the 4 excluded
                "driver_id": None,
                "legacy_import_metadata": {"source": svc.MIGRATION_332_SOURCE, "old_booking_id": "ob2"},
                "driver_arrived_at": None,
                "started_at": None,
                "ride_completed_at": None,
            },
            {
                "id": "not-covered-ride",
                "driver_id": "d3",
                "legacy_import_metadata": {"source": svc.MIGRATION_332_SOURCE, "old_booking_id": "ob3"},
                "driver_arrived_at": "x",
                "started_at": "y",
                "ride_completed_at": "z",
            },
            {
                "id": "non-legacy-ride",
                "driver_id": "d4",
                "legacy_import_metadata": {"source": "some_other_import"},
            },
        ],
        periods=[
            {"ride_id": "covered-ride", "is_reconstructed": True},
        ],
    )
    candidates = svc.fetch_migration_332_candidate_rides(fake)
    ids = {c.ride_id for c in candidates}
    assert ids == {"covered-ride", "bda2a258-7987-4344-882e-ca202df17d43"}
    covered = next(c for c in candidates if c.ride_id == "covered-ride")
    assert covered.excluded_by_migration_332 is False
    excluded = next(c for c in candidates if c.ride_id == "bda2a258-7987-4344-882e-ca202df17d43")
    assert excluded.excluded_by_migration_332 is True
