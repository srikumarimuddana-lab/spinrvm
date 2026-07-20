"""RC4 revalidation: Python vs Postgres phase-distance parity.

Two independent implementations measure per-phase GPS distance:
  - utils/trip_distance.compute_trip_distances (settlement + finalizer), and
  - compute_driver_phase_distances (migration 54_gps_daily_rollup_fn.sql,
    used by the admin daily rollup in routes/admin/maintenance.py).

These tests run the SAME synthetic trace through both and assert the
per-phase totals agree on clean data, and explicitly pin the one known
divergence (the SQL rollup applies no anomaly/spike filter).

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

_DSN = os.environ.get(
    "SPINR_PG_TEST_DSN",
    "host=127.0.0.1 dbname=spinr_parity_test user=spinr_test password=spinr_test",
)
_MIGRATION = os.path.join(os.path.dirname(__file__), "..", "migrations", "54_gps_daily_rollup_fn.sql")

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
        with open(_MIGRATION) as migration:
            sql = migration.read()
        # SECURITY DEFINER needs ownership rights the test role lacks for the
        # index; the function body is what parity cares about.
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
            "SELECT navigating_km, trip_km, point_count FROM compute_driver_phase_distances(%s, %s, %s)",
            (driver_id, DAY_START, DAY_END),
        )
        navigating_km, trip_km, point_count = cursor.fetchone()
    return float(navigating_km), float(trip_km), int(point_count)


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


def test_clean_trace_per_phase_totals_agree(pg):
    driver_id = str(uuid4())
    rows = _trace(driver_id)
    _insert(pg, rows)

    navigating_sql, trip_sql, point_count = _sql_phases(pg, driver_id)
    python = _python_phases(rows)

    assert point_count == len(rows)
    # Same haversine, same current-point phase attribution: the transition
    # segment (nav point 19 -> trip point 20) bills to trip_in_progress in
    # both implementations. Tolerance covers Python's round(v, 3) only.
    assert python.phase_distances["navigating_to_pickup"] == pytest.approx(navigating_sql, abs=5e-4)
    assert python.phase_distances["trip_in_progress"] == pytest.approx(trip_sql, abs=5e-4)
    assert python.actual_distance_km == pytest.approx(trip_sql, abs=0.02)


def test_known_divergence_sql_rollup_has_no_spike_filter(pg):
    """Pin the documented difference: the admin daily rollup sums a teleport
    spike that settlement's anomaly filter rejects. If this ever starts
    failing because the SQL function gained a filter, update the admin
    rollup docs and the settlement comparison guidance together."""
    driver_id = str(uuid4())
    rows = _trace(driver_id, spike=True)
    _insert(pg, rows)

    _, trip_sql, _ = _sql_phases(pg, driver_id)
    python = _python_phases(rows)

    # Python rejects both segments touching the spike (> MAX_SEG_KM).
    assert python.rejected_segments == 2
    assert python.phase_distances["trip_in_progress"] < 5.0
    # SQL sums the full teleport round trip (~230 km on a ~4 km trip).
    assert trip_sql > 200.0
