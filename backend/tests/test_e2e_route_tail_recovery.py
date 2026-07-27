"""E2E acceptance test for the production symptom: route ends before dropoff.

Scenario (synthetic Regina trace):
  1. Mid-trip GPS batches are persisted while the ride is in_progress.
  2. The ride completes while the final stretch is still stranded in the
     driver's SQLite outbox (dead network at the dropoff).
  3. The tail — captured before completion, delivered after — uploads late.
  4. The route finalizer re-runs.

Expected end state:
  - the late tail is accepted and re-queues finalization;
  - route_quality shows full coverage and no missing tail;
  - rides.actual_distance_km is recomputed within tolerance of ground truth;
  - fare columns are byte-identical (never touched by the recompute);
  - an append-only ride_distance_recomputes audit row exists.
"""

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.geo_utils import calculate_distance
from backend.utils.breadcrumbs import persist_trip_location_batch
from backend.utils.route_finalizer import finalize_route

RIDE_ID = "ride_regina_1"
SESSION = "6fe8dc5c-3448-46a1-aa7c-d081ce7f1d9f"
STARTED = datetime(2026, 7, 19, 10, 0, 0, tzinfo=timezone.utc)
COMPLETED = STARTED + timedelta(minutes=10)

# 120 points at 5s cadence heading north from downtown Regina —
# ~55.6 m per step (~40 km/h), no gap ever exceeding the 60s split bound.
POINT_COUNT = 120
TAIL_START_INDEX = 96  # the last 2 minutes are stranded until after completion


def _coord(i):
    return 50.4452 + i * 0.0005, -104.6189


def _point(i, **over):
    lat, lng = _coord(i)
    point = {
        "sequence_number": i + 1,
        "captured_at": (STARTED + timedelta(seconds=5 * i)).isoformat(),
        "lat": lat,
        "lng": lng,
        "accuracy": 7.5,
        "speed": 11.1,
    }
    point.update(over)
    return point


def _ground_truth_km(n_points):
    total = 0.0
    for i in range(1, n_points):
        a, b = _coord(i - 1), _coord(i)
        total += calculate_distance(a[0], a[1], b[0], b[1])
    return round(total, 2)


class FakeDb:
    """Minimal in-memory Supabase stand-in for the tables this flow touches."""

    def __init__(self):
        self.rides = {}
        self.ride_routes = {}
        self.history = []
        self.audit = []

    def _table(self, name):
        return {
            "rides": list(self.rides.values()),
            "ride_routes": list(self.ride_routes.values()),
            "driver_location_history": self.history,
            "ride_distance_recomputes": self.audit,
            # get_app_settings() (settings_loader.py) queries this table on
            # every cache miss -- reconstruct_completed_route now calls it.
            "settings": [],
        }[name]

    @staticmethod
    def _matches(row, filters):
        return all(row.get(key) == value for key, value in filters.items())

    async def get_rows(self, table, query, *, order=None, limit=None, offset=0, **_):
        rows = [row for row in self._table(table) if self._matches(row, query)]
        if order:
            rows.sort(key=lambda row: str(row.get(order, "")))
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def get_ride(self, ride_id, **_):
        return self.rides.get(ride_id)

    async def update_one(self, table, filters, update, upsert=False, **_):
        for row in self._table(table):
            if self._matches(row, filters):
                row.update(update)
                return row
        return None

    async def insert_one(self, table, doc):
        self._table(table).append(dict(doc))
        return dict(doc)

    async def insert_many_ignore_conflicts(self, table, rows, *, on_conflict):
        conflict_keys = on_conflict.split(",")
        existing = {tuple(str(r.get(k)) for k in conflict_keys) for r in self._table(table)}
        inserted = []
        for row in rows:
            serialized = {
                key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()
            }
            identity = tuple(str(serialized.get(k)) for k in conflict_keys)
            if identity in existing:
                continue
            existing.add(identity)
            self._table(table).append(serialized)
            inserted.append(serialized)
        return inserted


def _ride(status, **over):
    ride = {
        "id": RIDE_ID,
        "status": status,
        "driver_accepted_at": (STARTED - timedelta(minutes=6)).isoformat(),
        "driver_arrived_at": (STARTED - timedelta(minutes=2)).isoformat(),
        "ride_started_at": STARTED.isoformat(),
        "pickup_lat": 50.4452,
        "pickup_lng": -104.6189,
        "dropoff_lat": _coord(POINT_COUNT - 1)[0],
        "dropoff_lng": _coord(POINT_COUNT - 1)[1],
        "planned_distance_km": 3.0,
        "distance_km": 3.0,
        "total_fare": "23.91",
        "distance_fare": "10.50",
        "time_fare": "4.20",
        "driver_earnings": "23.91",
        "ride_metrics": {},
    }
    ride.update(over)
    return ride


def _patch_db(stack: ExitStack, fake: FakeDb) -> None:
    # All backend modules share the db_supabase module; patch its functions on
    # every consumer identity that may be loaded in this environment.
    for prefix in ("backend.utils", "utils"):
        for module in ("breadcrumbs", "route_finalizer", "trip_distance"):
            for fn in (
                "get_rows",
                "get_ride",
                "update_one",
                "insert_one",
                "insert_many_ignore_conflicts",
            ):
                try:
                    stack.enter_context(patch(f"{prefix}.{module}.db_supabase.{fn}", getattr(fake, fn)))
                except (ModuleNotFoundError, AttributeError):
                    continue


def _patch_providers(stack: ExitStack) -> None:
    # Road matching and road-snap providers are external — keep the test on
    # pure haversine/observed geometry.
    matched = AsyncMock(return_value={"segments": [], "failures": [], "provider": "test", "distance_km": None})
    for prefix in ("backend.utils", "utils"):
        try:
            stack.enter_context(patch(f"{prefix}.route_finalizer.compute_segmented_road_route", matched))
        except (ModuleNotFoundError, AttributeError):
            pass
        try:
            stack.enter_context(
                patch(
                    f"{prefix}.route_distance.compute_road_route",
                    AsyncMock(side_effect=RuntimeError("provider offline")),
                )
            )
        except (ModuleNotFoundError, AttributeError):
            pass
        try:
            stack.enter_context(patch(f"{prefix}.route_finalizer._publish_finalized_snapshot", AsyncMock()))
        except (ModuleNotFoundError, AttributeError):
            pass
    for name in ("backend.settings_loader.get_app_settings", "settings_loader.get_app_settings"):
        try:
            stack.enter_context(patch(name, AsyncMock(return_value={"fare_lock_enabled": False})))
        except (ModuleNotFoundError, AttributeError):
            pass
    for name in ("backend.utils.breadcrumbs.redis_get", "utils.breadcrumbs.redis_get"):
        try:
            stack.enter_context(patch(name, AsyncMock(return_value=None)))
        except (ModuleNotFoundError, AttributeError):
            pass
    for name in ("backend.utils.breadcrumbs.redis_set", "utils.breadcrumbs.redis_set"):
        try:
            stack.enter_context(patch(name, AsyncMock(return_value=True)))
        except (ModuleNotFoundError, AttributeError):
            pass


@pytest.mark.anyio
async def test_stranded_tail_recovers_full_route_and_distance_without_touching_fare():
    fake = FakeDb()
    truncated_truth = _ground_truth_km(TAIL_START_INDEX)
    full_truth = _ground_truth_km(POINT_COUNT)
    assert full_truth - truncated_truth > 1.0, "fixture must make the truncation visible"

    with ExitStack() as stack:
        _patch_db(stack, fake)
        _patch_providers(stack)

        # 1. Mid-trip: everything except the tail uploads normally.
        fake.rides[RIDE_ID] = _ride("in_progress")
        mid = await persist_trip_location_batch(
            "drv_1",
            RIDE_ID,
            SESSION,
            [_point(i) for i in range(TAIL_START_INDEX)],
            active_ride=fake.rides[RIDE_ID],
        )
        assert mid.ack.accepted_count == TAIL_START_INDEX

        # 2. Completion happens with the tail stranded: settlement measured
        #    only the truncated trail (what complete_ride would have written).
        completion_lat, completion_lng = _coord(POINT_COUNT - 1)
        completion_point = {
            "lat": completion_lat,
            "lng": completion_lng,
            "captured_at": COMPLETED.isoformat(),
            "accuracy": 8.0,
        }
        fake.rides[RIDE_ID].update(
            status="completed",
            ride_completed_at=COMPLETED.isoformat(),
            actual_distance_km=truncated_truth,
            phase_distances={"trip_in_progress": truncated_truth},
            gps_points_count=TAIL_START_INDEX,
        )
        fake.ride_routes[RIDE_ID] = {
            "ride_id": RIDE_ID,
            "route_schema_version": 2,
            "route_revision": 1,
            "processing_status": "complete",
            "completion_point": completion_point,
            "processing_claimed_at": None,
            "snapshot_attempts": 0,
        }

        # 3. The tail (captured pre-completion) is delivered post-completion,
        #    including the completion fix at exactly ride_completed_at.
        tail_points = [_point(i) for i in range(TAIL_START_INDEX, POINT_COUNT)]
        tail_points.append(
            _point(
                POINT_COUNT,
                sequence_number=POINT_COUNT + 1,
                captured_at=COMPLETED.isoformat(),
                lat=completion_lat,
                lng=completion_lng,
                is_completion_fix=True,
                source="completion",
            )
        )
        late = await persist_trip_location_batch(
            "drv_1",
            RIDE_ID,
            SESSION,
            tail_points,
            active_ride=fake.rides[RIDE_ID],
        )
        assert late.ack.accepted_count == len(tail_points)
        assert fake.ride_routes[RIDE_ID]["processing_status"] == "pending", "late tail must re-queue finalization"

        # 4. The finalizer worker claims and runs.
        claimed_at = datetime.now(timezone.utc)
        fake.ride_routes[RIDE_ID].update(processing_status="processing", processing_claimed_at=claimed_at)
        result = await finalize_route(RIDE_ID)

    # Route quality: full coverage, no missing tail, finalized as complete.
    quality = fake.ride_routes[RIDE_ID]["route_quality"]
    assert result["processing_status"] == "complete"
    assert quality["missing_tail"] is False
    assert quality["coverage_ratio"] >= 0.98
    assert quality["max_gap_seconds"] <= 60

    # Measured distance recomputed within 5% of ground truth.
    ride = fake.rides[RIDE_ID]
    assert abs(ride["actual_distance_km"] - full_truth) / full_truth < 0.05
    assert ride["phase_distances"]["trip_in_progress"] > truncated_truth

    # Fare columns byte-identical — the recompute is stats-only.
    assert ride["total_fare"] == "23.91"
    assert ride["distance_fare"] == "10.50"
    assert ride["time_fare"] == "4.20"
    assert ride["driver_earnings"] == "23.91"

    # Append-only audit trail records the correction.
    assert len(fake.audit) == 1
    audit = fake.audit[0]
    assert audit["ride_id"] == RIDE_ID
    assert audit["previous_actual_distance_km"] == truncated_truth
    assert abs(audit["new_actual_distance_km"] - full_truth) / full_truth < 0.05
    # "late_tail_refinalization" only applies to the no-reconstruction,
    # GPS-measured-only recompute path (distance_basis="gps_measured");
    # this test exercises route reconstruction, which resolves distance via
    # resolve_measured_distance_km and tags the trigger "route_reconstruction"
    # (utils/route_finalizer.py's _DISTANCE_RECOMPUTE_TRIGGER_BY_BASIS).
    assert audit["trigger"] == "route_reconstruction"
