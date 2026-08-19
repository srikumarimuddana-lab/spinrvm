"""RC4 revalidation: Python vs Postgres phase-distance parity.

Two independent implementations measure per-phase GPS distance:
  - utils/trip_distance.compute_trip_distances (settlement + finalizer), and
  - compute_driver_phase_distances v2 (migration 336_phase_distances_fn_v2.sql,
    used by the daily rollup — routes/admin/maintenance.py and the scheduled
    driver_daily_rollup loop).

These tests run the SAME synthetic trace through both and assert per-phase
totals AND seconds agree on clean data, and that both now reject the same
anomalous segments (v1's known no-spike-filter divergence was closed by
migration 336, which mirrors trip_distance.py's 5 km / 300 s / 150 km/h caps).

Integration-marked: requires a reachable Postgres (SPINR_PG_TEST_DSN, or the
local spinr_parity_test database); skipped otherwise.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from backend.utils.trip_distance import compute_trip_distances

pytestmark = pytest.mark.integration

_DSN = (
    os.environ.get("SPINR_PG_TEST_DSN")
    or os.environ.get("DATABASE_URL")
    or os.environ.get("PG_CONNECTION_STRING")
    or "host=127.0.0.1 dbname=spinr_parity_test user=spinr_test password=spinr_test"
)
# Applied in production order: v1 defines the function + index, v2 (336)
# drops and recreates it with the anomaly filter and per-phase seconds.
_MIGRATIONS = [
    os.path.join(os.path.dirname(__file__), "..", "migrations", "54_gps_daily_rollup_fn.sql"),
    os.path.join(os.path.dirname(__file__), "..", "migrations", "336_phase_distances_fn_v2.sql"),
]

DAY_START = datetime(2026, 7, 19, 0, 0, 0, tzinfo=timezone.utc)
DAY_END = DAY_START + timedelta(days=1)
T0 = DAY_START + timedelta(hours=10)


@pytest.fixture(scope="module")
def pg():
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        connection = psycopg2.connect(_DSN)
    except Exception as exc:  # pragma: no cover — environment-dependent
        pytest.skip(f"integration Postgres not available: {exc}")
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS driver_location_history CASCADE")
        cursor.execute(
            """
            CREATE TABLE driver_location_history (
                driver_id uuid,
                lat double precision,
                lng double precision,
                "timestamp" timestamptz,
                tracking_phase text
            )
            """
        )
        for path in _MIGRATIONS:
            with open(path) as migration:
                sql = migration.read()
            # SECURITY DEFINER needs ownership rights the test role lacks for
            # the index; the function body is what parity cares about.
            cursor.execute(sql.replace("SECURITY DEFINER", ""))
    yield connection
    connection.close()


def _trace(driver_id, *, spike=False):
    """Nav (20 points) then trip (60 points), 5s cadence, ~55.6m per step."""
    rows = []
    for i in range(80):
        phase = "navigating_to_pickup" if i < 20 else "trip_in_progress"
        lat = 50.4452 + i * 0.0005
        lng = -104.6189
        if spike and i == 50:
            lat = 51.5  # ~117 km tower-handoff teleport
        rows.append(
            {
                "driver_id": driver_id,
                "lat": lat,
                "lng": lng,
                "timestamp": (T0 + timedelta(seconds=5 * i)).isoformat(),
                "tracking_phase": phase,
            }
        )
    return rows


def _sql_phases(pg, driver_id):
    with pg.cursor() as cursor:
        cursor.execute(
            "SELECT navigating_km, trip_km, point_count, navigating_seconds,"
            " trip_seconds, rejected_segments"
            " FROM compute_driver_phase_distances(%s, %s, %s)",
            (driver_id, DAY_START, DAY_END),
        )
        nav_km, trip_km, points, nav_s, trip_s, rejected = cursor.fetchone()
    return {
        "navigating_km": float(nav_km),
        "trip_km": float(trip_km),
        "point_count": int(points),
        "navigating_seconds": int(nav_s),
        "trip_seconds": int(trip_s),
        "rejected_segments": int(rejected),
    }


def _insert(pg, rows):
    with pg.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO driver_location_history (driver_id, lat, lng, "timestamp", tracking_phase)
            VALUES (%(driver_id)s, %(lat)s, %(lng)s, %(timestamp)s, %(tracking_phase)s)
            """,
            rows,
        )


def _python_phases(rows):
    breadcrumbs = [
        {"lat": r["lat"], "lng": r["lng"], "timestamp": r["timestamp"], "tracking_phase": r["tracking_phase"]}
        for r in rows
    ]
    with patch(
        "backend.utils.route_distance.compute_road_route",
        AsyncMock(side_effect=RuntimeError("provider offline")),
    ):
        result = asyncio.run(compute_trip_distances(breadcrumbs, ride_id="parity", planned_distance=0.0))
    return result


def test_clean_trace_per_phase_totals_and_seconds_agree(pg):
    driver_id = str(uuid4())
    rows = _trace(driver_id)
    _insert(pg, rows)

    sql = _sql_phases(pg, driver_id)
    python = _python_phases(rows)

    assert sql["point_count"] == len(rows)
    assert sql["rejected_segments"] == 0
    # Same haversine, same current-point phase attribution: the transition
    # segment (nav point 19 -> trip point 20) bills to trip_in_progress in
    # both implementations. Tolerance covers Python's round(v, 3) only.
    assert python.phase_distances["navigating_to_pickup"] == pytest.approx(sql["navigating_km"], abs=5e-4)
    assert python.phase_distances["trip_in_progress"] == pytest.approx(sql["trip_km"], abs=5e-4)
    assert python.actual_distance_km == pytest.approx(sql["trip_km"], abs=0.02)
    # v2 seconds mirror trip_distance.py's accepted-gap sums exactly.
    assert python.phase_durations["navigating_to_pickup"] == sql["navigating_seconds"]
    assert python.phase_durations["trip_in_progress"] == sql["trip_seconds"]


def test_spike_filter_parity_sql_rejects_same_segments_as_settlement(pg):
    """v1's documented divergence (SQL summed a ~230 km teleport round trip
    that settlement rejected) was closed by migration 336: both
    implementations now reject the two segments touching the spike and
    report near-identical trip_km."""
    driver_id = str(uuid4())
    rows = _trace(driver_id, spike=True)
    _insert(pg, rows)

    sql = _sql_phases(pg, driver_id)
    python = _python_phases(rows)

    # Python rejects both segments touching the spike (> MAX_SEG_KM).
    assert python.rejected_segments == 2
    assert sql["rejected_segments"] == 2
    assert python.phase_distances["trip_in_progress"] < 5.0
    assert sql["trip_km"] < 5.0
    assert python.phase_distances["trip_in_progress"] == pytest.approx(sql["trip_km"], abs=5e-4)
