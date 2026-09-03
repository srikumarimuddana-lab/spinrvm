"""Smoke test proving the direct_pool fixture (conftest.py) actually applies
the migrations it claims to and yields a usable connection -- not just that
it imports.

This is deliberately narrow: it proves the harness itself is sound (schema
present, tables queryable, self-skip works with no DATABASE_URL). It is NOT
a test of backend/repositories/dispatch_pool.py's SQL, because that module
has no real callers yet (Phase 2, T12/T13, out of scope for T11). Real
dispatch-pool-specific tests land in Phase 2 alongside T12's migration.
"""

from __future__ import annotations


def test_users_drivers_rides_tables_exist(pg_cur):
    for table in ("users", "drivers", "rides"):
        pg_cur.execute(f"SELECT count(*) FROM {table}")
        assert pg_cur.fetchone()[0] == 0


def test_ride_offers_table_and_status_constraint(pg_cur):
    """Confirms 100 (create) + 131/143 (widen status check) applied in the
    right order: 'preempted' and 'cancelled' must be accepted."""
    pg_cur.execute("INSERT INTO users (id, phone) VALUES ('u1', '+15550000001')")
    pg_cur.execute("INSERT INTO drivers (id, user_id, name, phone) VALUES ('d1', 'u1', 'Test Driver', '+15550000002')")
    pg_cur.execute(
        """
        INSERT INTO rides (id, rider_id, pickup_address, pickup_lat, pickup_lng,
                            dropoff_address, dropoff_lat, dropoff_lng)
        VALUES ('r1', 'u1', 'A', 52.13, -106.67, 'B', 52.15, -106.60)
        """
    )
    for status in ("pending", "accepted", "declined", "expired", "preempted", "cancelled"):
        pg_cur.execute(
            "INSERT INTO ride_offers (id, ride_id, driver_id, status) "
            "VALUES (gen_random_uuid(), 'r1', 'd1', %s) "
            "ON CONFLICT (ride_id, driver_id) DO UPDATE SET status = EXCLUDED.status",
            (status,),
        )
    pg_cur.execute("SELECT status FROM ride_offers WHERE ride_id = 'r1'")
    assert pg_cur.fetchone()[0] == "cancelled"


def test_ride_offers_expires_at_column_exists(pg_cur):
    """Confirms migration 224 applied (persisted per-offer expiry deadline)."""
    pg_cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'ride_offers' AND column_name = 'expires_at'"
    )
    assert pg_cur.fetchone() is not None


def test_driver_insurance_periods_transition_rpc(pg_cur):
    """Confirms 64 (table) + 253 (RPC) applied and the RPC actually works --
    the exact function Phase 2's dispatch_pool.claim_batch (T12/T13, not yet
    built) will call in the same transaction as a claim."""
    pg_cur.execute("INSERT INTO users (id, phone) VALUES ('u2', '+15550000003')")
    pg_cur.execute(
        "INSERT INTO drivers (id, user_id, name, phone) VALUES ('d2', 'u2', 'Test Driver 2', '+15550000004')"
    )
    pg_cur.execute("SELECT record_insurance_period_transition('d2', 1::smallint, NULL)")
    result = pg_cur.fetchone()[0]
    assert result["status"] == "ok"
    assert result["opened"] is True

    pg_cur.execute("SELECT period FROM driver_insurance_periods WHERE driver_id = 'd2' AND ended_at IS NULL")
    assert pg_cur.fetchone()[0] == 1


def test_migration_354_lockdown_applied_without_error(pg_cur):
    """354 is a no-op-on-a-clean-database sweep (see its own header
    comment); confirms it applied without raising and left
    record_insurance_period_transition callable by the service role path
    this fixture uses (the migration only revokes anon/authenticated, never
    the connecting role here)."""
    pg_cur.execute(
        "SELECT has_function_privilege(current_user, "
        "'record_insurance_period_transition(text, smallint, text)', 'EXECUTE')"
    )
    assert pg_cur.fetchone()[0] is True
