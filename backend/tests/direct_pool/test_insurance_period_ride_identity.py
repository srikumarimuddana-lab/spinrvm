"""F4 contract: an insurance-period transition is idempotent on (period, ride
identity) -- not on `period` alone.

Background. Migration 253's no-op check compared only `period` (253:44), so a
driver already open on Period 2 for ride A who was then claimed for ride B got
{"status": "noop"} and ride B never opened its own Period 2 interval. The open
row kept ride A's ride_id -- and on 2026-09-13 ride A was a ride the rider had
already cancelled. Migration 421 adds a NULL-safe ride-identity comparison.
See docs/audit/2026-09-13-driver-app-mid-ride-process-death.md F4.

Why this file exists in direct_pool/ rather than as a bare .sql: pytest.ini
sets `python_files = test_*.py`, so a .sql file is collected by nothing and can
never fail a build. This suite already runs against a real postgres service in
ci.yml and its conftest applies 64 + 253 + 421, so the contract is exercised
against the real function body on every CI run.

These tests assert the *historical* row is preserved, not merely that a new row
appears. That distinction is the regulatory point: driver_insurance_periods is
append-only (CLAUDE.md), so a fix that reattributed or reclassified the prior
interval would satisfy "ride B has a Period 2" while corrupting the audit trail.
"""

from __future__ import annotations

from datetime import datetime

import pytest

# A fixed past timestamp for the historical interval. Using an explicit value
# rather than letting the RPC stamp started_at defeats a real blind spot: the
# function sets v_now := now(), which is TRANSACTION time, so a row inserted
# and closed inside one transaction has started_at == ended_at == v_now. An
# assertion of "started_at unchanged" written that way passes even against a
# function that rewrites started_at to v_now. Seeding a distinct value makes
# the assertion actually load-bearing.
_SEEDED_START = "2026-09-13 19:45:56+00"


@pytest.fixture()
def f4_driver(pg_cur):
    """One driver plus two distinct rides, for period-attribution tests."""
    pg_cur.execute("INSERT INTO users (id, phone) VALUES ('f4_u', '+15550100001')")
    pg_cur.execute(
        "INSERT INTO drivers (id, user_id, name, phone) VALUES ('f4_d', 'f4_u', 'F4 Driver', '+15550100002')"
    )
    for ride_id, lat in (("f4_ride_a", 52.13), ("f4_ride_b", 52.15)):
        pg_cur.execute(
            """
            INSERT INTO rides (id, rider_id, pickup_address, pickup_lat, pickup_lng,
                               dropoff_address, dropoff_lat, dropoff_lng)
            VALUES (%s, 'f4_u', 'A', %s, -106.67, 'B', 52.20, -106.60)
            """,
            (ride_id, lat),
        )
    return "f4_d"


def _open_row(pg_cur, driver_id):
    pg_cur.execute(
        "SELECT period, ride_id, started_at FROM driver_insurance_periods WHERE driver_id = %s AND ended_at IS NULL",
        (driver_id,),
    )
    return pg_cur.fetchone()


def _rows(pg_cur, driver_id):
    pg_cur.execute(
        "SELECT period, ride_id, started_at, ended_at FROM driver_insurance_periods "
        "WHERE driver_id = %s ORDER BY started_at, ended_at NULLS LAST",
        (driver_id,),
    )
    return pg_cur.fetchall()


def test_same_period_different_ride_opens_a_new_interval(pg_cur, f4_driver):
    """The F4 regression itself: P2/rideA -> P2/rideB must NOT no-op."""
    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, 'f4_ride_a')",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, 'f4_ride_b')",
        (f4_driver,),
    )
    result = pg_cur.fetchone()[0]

    # Against migration 253 this returns {"status": "noop", "opened": false}.
    assert result["status"] == "ok", "ride B must open its own Period 2 interval"
    assert result["opened"] is True
    assert result["closed"] == 1, "ride A's open interval must be closed exactly once"

    open_row = _open_row(pg_cur, f4_driver)
    assert open_row is not None
    assert open_row[0] == 2
    assert open_row[1] == "f4_ride_b"


def test_prior_interval_is_closed_not_reattributed_or_reclassified(pg_cur, f4_driver):
    """Append-only: closing ride A's interval may set ended_at and nothing else.

    Covers the three mutation classes a period-only assertion misses -- ride_id
    reattribution, `period` reclassification (the exact F4 misclassification
    class), and started_at being rewritten to the function's own v_now.
    """
    # Seed ride A's open interval directly with an explicit past started_at,
    # rather than via the RPC, so "started_at unchanged" is a real assertion
    # instead of a tautology against transaction-time now().
    #
    # It must be an INSERT, not an UPDATE: migration 64's
    # _driver_insurance_periods_immutable() trigger rejects any UPDATE that
    # does not set ended_at ("driver_insurance_periods UPDATE must set
    # ended_at to a non-NULL timestamp"). That trigger IS the append-only
    # guarantee this test exists to protect, so the test must not need a
    # carve-out from it.
    pg_cur.execute(
        "INSERT INTO driver_insurance_periods (driver_id, period, started_at, ride_id) "
        "VALUES (%s, 2::smallint, %s, 'f4_ride_a')",
        (f4_driver, _SEEDED_START),
    )

    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, 'f4_ride_b')",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    rows = _rows(pg_cur, f4_driver)
    assert len(rows) == 2, "close+open must append, never replace"

    historical = rows[0]
    assert historical[0] == 2, "prior interval's period must not be rewritten"
    assert historical[1] == "f4_ride_a", "prior interval must stay attributed to ride A"
    assert historical[2] == datetime.fromisoformat(_SEEDED_START), (
        "prior interval's started_at must be preserved, not restamped to now()"
    )
    assert historical[3] is not None, "prior interval must be closed"


def test_same_period_same_ride_still_no_ops(pg_cur, f4_driver):
    """421 must not break 253's real idempotency guarantee.

    ride_flow.py:403 calls this a second time after accept as a deliberate
    safety net; it must stay a no-op or every acceptance would append a row.
    """
    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, 'f4_ride_a')",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, 'f4_ride_a')",
        (f4_driver,),
    )
    result = pg_cur.fetchone()[0]
    assert result["status"] == "noop"
    assert result["opened"] is False
    assert len(_rows(pg_cur, f4_driver)) == 1, "a repeat call must not append"


def test_null_ride_identity_is_null_safe(pg_cur, f4_driver):
    """IS NOT DISTINCT FROM, not `=`.

    Plain `=` yields NULL for a NULL ride_id, which is falsy, so every repeated
    Period 1/0 tick would fall through to close+open and churn a row per call.
    The startup loops in core/lifespan.py drive exactly that path.
    """
    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 1::smallint, NULL)",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 1::smallint, NULL)",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "noop", "NULL == NULL must compare equal"
    assert len(_rows(pg_cur, f4_driver)) == 1


def test_ride_bearing_to_null_ride_transition_opens_new_interval(pg_cur, f4_driver):
    """P2/rideA -> P2/NULL differs in ride identity and must open a new row."""
    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, 'f4_ride_a')",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 2::smallint, NULL)",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    open_row = _open_row(pg_cur, f4_driver)
    assert open_row[1] is None


def test_already_closed_intervals_are_untouched_by_later_transitions(pg_cur, f4_driver):
    """A third transition must not disturb rows closed by the first two."""
    for period, ride in ((2, "f4_ride_a"), (3, "f4_ride_a")):
        pg_cur.execute(
            "SELECT record_insurance_period_transition(%s, %s::smallint, %s)",
            (f4_driver, period, ride),
        )
        assert pg_cur.fetchone()[0]["status"] == "ok"

    before = [r for r in _rows(pg_cur, f4_driver) if r[3] is not None]
    assert before, "expected at least one closed interval"

    pg_cur.execute(
        "SELECT record_insurance_period_transition(%s, 1::smallint, NULL)",
        (f4_driver,),
    )
    assert pg_cur.fetchone()[0]["status"] == "ok"

    after = [r for r in _rows(pg_cur, f4_driver) if r[3] is not None]
    assert after[: len(before)] == before, "closed intervals must be immutable"


def test_period_3_still_requires_a_ride_id(pg_cur, f4_driver):
    """421 must not weaken 253's Period 3 guard (CLAUDE.md: no P3 without a ride)."""
    with pytest.raises(Exception) as exc:
        pg_cur.execute(
            "SELECT record_insurance_period_transition(%s, 3::smallint, NULL)",
            (f4_driver,),
        )
    assert "ride_id is required" in str(exc.value)
