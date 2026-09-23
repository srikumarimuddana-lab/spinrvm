"""Real-Postgres regression test for update_live_driver_marker (2026-09-22 incident).

Migration 445 declared ``p_driver_id uuid`` and compared it to ``drivers.id``
(TEXT). Every call raised 42883 "operator does not exist: text = uuid" in
production (1,359 failures, 01:03-01:58 UTC) and the driver WebSocket closed on
each one. The existing tests mocked the RPC (test_location_marker_atomic.py) or
regex-checked the SQL (test_migration_447_text_ids.py), so neither could catch
a SQL type mismatch. This module runs the real shipped SQL against real
Postgres with production-shaped TEXT driver ids.

Runs in CI's "direct-pool real-Postgres tests" step (see .github/workflows/ci.yml);
self-skips locally without TEST_DATABASE_URL/DATABASE_URL (tests/direct_pool/conftest.py).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MIGRATIONS = _BACKEND_DIR / "migrations"

try:
    from backend.scripts.run_migrations import _split_sql_statements
except ImportError:  # pragma: no cover - import style varies by entrypoint
    sys.path.insert(0, str(_BACKEND_DIR.parent))
    from backend.scripts.run_migrations import _split_sql_statements

# drivers columns the RPC touches that are added by later migrations, not by
# supabase_schema.sql's base CREATE TABLE: heading (113), period1_* (250 -- not
# applied verbatim because it also ALTERs `settings`, outside this harness),
# updated_at (present in production; no migration in this repo adds it).
_DRIVER_COLUMNS_SQL = """
ALTER TABLE public.drivers
    ADD COLUMN IF NOT EXISTS heading double precision,
    ADD COLUMN IF NOT EXISTS period1_accum_km numeric(10, 3) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS period1_accum_since timestamptz,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz;
"""

_CALL = "SELECT public.update_live_driver_marker(%s, %s, %s::jsonb)"


@pytest.fixture(scope="module")
def marker_conn(pg_conn):
    cur = pg_conn.cursor()
    cur.execute(_DRIVER_COLUMNS_SQL)
    yield pg_conn
    cur.execute("DROP FUNCTION IF EXISTS public.update_live_driver_marker(text, timestamptz, jsonb)")
    cur.execute("DROP FUNCTION IF EXISTS public.update_live_driver_marker(uuid, timestamptz, jsonb)")


def _seed_driver(cur, driver_id: str) -> None:
    cur.execute("INSERT INTO users (id, phone) VALUES (%s, %s)", (f"u-{driver_id}", f"+1555{uuid.uuid4().int % 10**7}"))
    cur.execute(
        "INSERT INTO drivers (id, user_id, name, phone) VALUES (%s, %s, 'Test Driver', '+15550000000')",
        (driver_id, f"u-{driver_id}"),
    )


def _apply(cur, filename: str) -> None:
    # Same splitter the real migration runner (and conftest's harness) uses.
    for statement in _split_sql_statements((_MIGRATIONS / filename).read_text()):
        cur.execute(statement)


def test_445_uuid_signature_reproduces_production_42883(marker_conn, pg_cur):
    """Proves this harness would have caught the incident: 445 as shipped fails."""
    driver_id = str(uuid.uuid4())  # production driver ids are uuid-shaped TEXT
    _seed_driver(pg_cur, driver_id)
    _apply(pg_cur, "445_monotonic_live_driver_marker.sql")
    try:
        with pytest.raises(psycopg2.errors.UndefinedFunction, match="text = uuid"):
            # PostgREST binds p_driver_id with the function's declared type (uuid).
            pg_cur.execute(
                "SELECT public.update_live_driver_marker(%s::uuid, %s, %s::jsonb)",
                (driver_id, datetime.now(timezone.utc), json.dumps({"lat": 50.45, "lng": -104.61})),
            )
    finally:
        pg_cur.execute("DROP FUNCTION IF EXISTS public.update_live_driver_marker(uuid, timestamptz, jsonb)")


@pytest.fixture()
def fixed_cur(marker_conn, pg_cur):
    # Apply 445 first then 447, so 447's DROP of the uuid overload is exercised.
    _apply(pg_cur, "445_monotonic_live_driver_marker.sql")
    _apply(pg_cur, "447_fix_payment_ops_and_live_marker_text_ids.sql")
    return pg_cur


def test_447_leaves_only_text_overload_with_service_role_grant(fixed_cur):
    fixed_cur.execute(
        "SELECT pg_get_function_identity_arguments(p.oid), p.prosecdef, p.proconfig "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = 'update_live_driver_marker'"
    )
    rows = fixed_cur.fetchall()
    assert len(rows) == 1
    args, security_definer, config = rows[0]
    assert args == "p_driver_id text, p_captured_at timestamp with time zone, p_values jsonb"
    assert security_definer is True
    assert any(c.startswith("search_path=") and "public" in c for c in config)
    sig = "public.update_live_driver_marker(text, timestamptz, jsonb)"
    for role, expected in (("service_role", True), ("anon", False), ("authenticated", False)):
        fixed_cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
        assert fixed_cur.fetchone()[0] is expected, role


def test_447_is_idempotent_on_reapply(fixed_cur):
    """Production already has the text version; re-applying must be a no-op."""
    _apply(fixed_cur, "447_fix_payment_ops_and_live_marker_text_ids.sql")
    fixed_cur.execute("SELECT count(*) FROM pg_proc WHERE proname = 'update_live_driver_marker'")
    assert fixed_cur.fetchone()[0] == 1


@pytest.mark.parametrize("driver_id", [str(uuid.uuid4()), "legacy-driver-7"])
def test_text_id_fresh_write_accepted_and_stale_rejected(fixed_cur, driver_id):
    _seed_driver(fixed_cur, driver_id)
    now = datetime.now(timezone.utc)

    fixed_cur.execute(_CALL, (driver_id, now, json.dumps({"lat": 50.45, "lng": -104.61, "heading": 90})))
    assert fixed_cur.fetchone()[0] is True
    fixed_cur.execute("SELECT lat, lng, heading, location_captured_at FROM drivers WHERE id = %s", (driver_id,))
    lat, lng, heading, captured = fixed_cur.fetchone()
    assert (lat, lng, heading) == (50.45, -104.61, 90)
    assert captured == now

    # Older capture than the stored one: rejected, row unchanged (capture ordering).
    fixed_cur.execute(_CALL, (driver_id, now - timedelta(seconds=5), json.dumps({"lat": 1.0, "lng": 1.0})))
    assert fixed_cur.fetchone()[0] is False
    fixed_cur.execute("SELECT lat, lng FROM drivers WHERE id = %s", (driver_id,))
    assert fixed_cur.fetchone() == (50.45, -104.61)


def test_period1_accumulator_written_even_when_marker_rejected(fixed_cur):
    driver_id = str(uuid.uuid4())
    _seed_driver(fixed_cur, driver_id)
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)  # outside 60 s window
    fixed_cur.execute(_CALL, (driver_id, stale, json.dumps({"lat": 50.4, "lng": -104.6, "period1_accum_km": 3.25})))
    assert fixed_cur.fetchone()[0] is False
    fixed_cur.execute("SELECT period1_accum_km, lat FROM drivers WHERE id = %s", (driver_id,))
    km, lat = fixed_cur.fetchone()
    assert float(km) == 3.25
    assert lat == 0  # marker untouched


def test_unknown_driver_returns_false(fixed_cur):
    fixed_cur.execute(_CALL, ("no-such-driver", datetime.now(timezone.utc), json.dumps({"lat": 50.4, "lng": -104.6})))
    assert fixed_cur.fetchone()[0] is False
