"""
Fixtures for real-Postgres, DB-role-level RLS policy tests.

Why this directory is special
------------------------------
Every other test in `backend/tests/` uses the `mock_supabase_client` fixture
(see `backend/tests/conftest.py`) -- a mocked Python object standing in for
`supabase-py`. That's fine for exercising *our* code's call sites, but it
cannot exercise Postgres Row-Level Security at all: RLS is enforced by the
Postgres engine itself when a query runs as a specific role, and a mock
client never issues a real role-scoped query. Per CLAUDE.md's Testing
Conventions gap (audit finding N18 / ranked blocker #29,
docs/audit/2026-08-18-full-fleet-whole-app-audit.md), zero tests in this
repo exercised an RLS policy from a real `anon`/`authenticated` role before
this directory was added. See docs/change-log/2026-08-31-rls-role-level-test-coverage.md.

What these fixtures do
-----------------------
1. Connect to a real, disposable Postgres (via `TEST_DATABASE_URL`, falling
   back to `DATABASE_URL`) with permission to CREATE DATABASE / CREATE ROLE.
2. Create a uniquely-named scratch *database* for the test session (dropped
   at the end), so `backend/supabase_rls.sql`'s `public.`-qualified policies
   apply unmodified against a real `public` schema, matching production,
   without touching any other database on the target Postgres.
3. Recreate the `anon` / `authenticated` / `service_role` roles and the
   `auth.uid()` / `auth.role()` / `auth.jwt()` helper functions Supabase
   normally provides (these live outside this repo's migrations -- Supabase
   manages them -- so we reproduce Supabase's own published definitions,
   which read the same `request.jwt.claims` GUC PostgREST sets per request).
4. Apply the ACTUAL shipped SQL this repo tracks -- `backend/supabase_schema.sql`
   (users/drivers/rides table defs, extracted verbatim) and
   `backend/supabase_rls.sql` (applied byte-for-byte, unmodified) -- plus a
   handful of `backend/migrations/*.sql` files for money/safety tables. If
   any of those files' policy syntax changes, these tests read the changed
   file directly, so drift shows up as a real test failure, not a stale copy.
5. Expose `as_role(cur, role, claims)` so a test can flip the current
   Postgres session to `anon`/`authenticated`/`service_role` with a given
   JWT claim set, matching Supabase's own RLS-testing convention
   (`SET ROLE` + `request.jwt.claims`), and run a query as that role.

Coverage scope (deliberately partial -- see the change log)
-------------------------------------------------------------
This is the start of DB-role-level RLS coverage, not the whole 207-ish
policy-statement backlog. Five tables, chosen for consequence: `users`,
`drivers`, `rides` (core consumer-facing tables, sourced from
`backend/supabase_rls.sql`), `financial_events` (7-year money ledger,
migrations 58/70/290), and `driver_insurance_periods` (SGI-regulated safety
audit trail, migration 64).

Running these tests
--------------------
Needs a real reachable Postgres with CREATE DATABASE / CREATE ROLE rights.
Point `TEST_DATABASE_URL` (or `DATABASE_URL`) at it -- a standard libpq
connection string (scheme, user, password, host, port, db name), e.g.
"postgresql://postgres:PASSWORD@127.0.0.1:5432/postgres":

    export TEST_DATABASE_URL="<your connection string>"
    cd backend
    pytest tests/rls -c /dev/null --confcutdir=tests/rls

`-c /dev/null --confcutdir=tests/rls` is required: it stops pytest from also
loading `backend/tests/conftest.py` (and hence `pytest.ini`'s coverage gate
and the full mocked-Supabase/FastAPI app stack), which these tests don't use
and shouldn't depend on. Without a reachable Postgres, every test here is
skipped (not failed, not faked).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

try:
    import psycopg2
    import psycopg2.extensions
except ImportError:  # pragma: no cover - environment without psycopg2 installed
    psycopg2 = None

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_ROOT = _BACKEND_DIR.parent

_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    psycopg2 is None or not _DSN,
    reason=(
        "RLS role-level tests require psycopg2 and a real Postgres reachable via "
        "TEST_DATABASE_URL (or DATABASE_URL) -- see backend/tests/rls/conftest.py "
        "docstring. Skipped, not faked: a mocked Supabase client cannot exercise RLS."
    ),
)

# ---------------------------------------------------------------------------
# SQL extraction helpers -- read the real shipped files, don't hand-copy them
# ---------------------------------------------------------------------------


def _extract_create_table(sql_text: str, table_name: str) -> str:
    """Pull one `CREATE TABLE IF NOT EXISTS <table_name> ( ... );` block out
    of a larger .sql file by tracking paren depth, so we test the exact DDL
    this repo ships rather than a copy that can drift out of sync."""
    marker = f"CREATE TABLE IF NOT EXISTS {table_name} ("
    start = sql_text.index(marker)
    depth = 0
    i = sql_text.index("(", start)
    for j in range(i, len(sql_text)):
        ch = sql_text[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = sql_text.index(";", j) + 1
                return sql_text[start:end]
    raise AssertionError(f"unbalanced parens extracting {table_name} from {marker!r}")


_AUTH_SHIM_SQL = """
CREATE SCHEMA IF NOT EXISTS auth;

-- Reproduces Supabase's own published auth.uid()/auth.role()/auth.jwt()
-- definitions (these ship with every Supabase project, outside this repo's
-- migrations, so there's nothing in backend/migrations/ to read verbatim
-- here) -- both read the same request.jwt.claims GUC PostgREST sets per
-- request, which is what SET ROLE + set_config() below emulates.
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
      SELECT (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
    $$;

CREATE OR REPLACE FUNCTION auth.role() RETURNS text
    LANGUAGE sql STABLE
    AS $$
      SELECT nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'role'
    $$;

CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb
    LANGUAGE sql STABLE
    AS $$
      SELECT coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb
    $$;
"""

_ROLE_SETUP_SQL = """
DO $$ BEGIN
    CREATE ROLE anon NOLOGIN NOINHERIT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""

# Minimal stub tables for the other backend/supabase_rls.sql targets, so the
# file can be applied byte-for-byte (unmodified) instead of us hand-slicing
# just the users/drivers/rides sections out of it.
_STUB_TABLES_SQL = """
CREATE TABLE otp_records (id text primary key);
CREATE TABLE settings (id text primary key);
CREATE TABLE support_tickets (id text primary key, user_id text);
CREATE TABLE faqs (id text primary key);
CREATE TABLE vehicle_types (id text primary key);
CREATE TABLE fare_configs (id text primary key);
CREATE TABLE service_areas (id text primary key);
"""


def _dsn_with_dbname(dsn: str, dbname: str) -> str:
    """Swap the database name in a postgres:// DSN, keeping host/user/etc."""
    import urllib.parse as _u

    parts = _u.urlsplit(dsn)
    return _u.urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


@pytest.fixture(scope="session")
def pg_test_dbname() -> str:
    return f"rls_test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def pg_conn(pg_test_dbname):
    """Session-scoped admin connection to a throwaway *database* (not just a
    schema) so `backend/supabase_rls.sql`'s `public.`-qualified policies
    apply unmodified against a real `public` schema, matching production.
    Builds roles, the auth shim, and the real shipped table/policy SQL once;
    drops the database at the end of the session."""
    bootstrap = psycopg2.connect(_DSN)
    bootstrap.autocommit = True
    with bootstrap.cursor() as bcur:
        bcur.execute(f"CREATE DATABASE {pg_test_dbname}")
    bootstrap.close()

    conn = psycopg2.connect(_dsn_with_dbname(_DSN, pg_test_dbname))
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(_ROLE_SETUP_SQL)
    cur.execute(_AUTH_SHIM_SQL)
    cur.execute("GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role")
    cur.execute("GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role")

    # --- users / drivers / rides: extracted verbatim from the real shipped
    # schema file, then backend/supabase_rls.sql applied unmodified. ---
    schema_sql = (_BACKEND_DIR / "supabase_schema.sql").read_text()
    for table in ("users", "drivers", "rides"):
        cur.execute(_extract_create_table(schema_sql, table))

    cur.execute(_STUB_TABLES_SQL)

    # Mirror Supabase's own default: new public-schema tables get broad
    # anon/authenticated table-level grants out of the box; RLS is what
    # narrows access from there (see migration 290's comment on exactly
    # this point). Without this, our test would under-state what a real
    # Supabase project's anon/authenticated roles can attempt.
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role"
    )

    rls_sql = (_REPO_ROOT / "backend" / "supabase_rls.sql").read_text()
    cur.execute(rls_sql)

    # --- financial_events: migrations 58 (create+policies), 70 (select
    # policy fix), 290 (grant lockdown) applied in order, verbatim. ---
    migrations_dir = _BACKEND_DIR / "migrations"
    for fname in (
        "58_financial_events.sql",
        "70_fix_financial_events_rls.sql",
        "290_financial_events_grant_lockdown.sql",
    ):
        sql = (migrations_dir / fname).read_text()
        # These migrations end with `NOTIFY pgrst, 'reload schema';`, a
        # PostgREST-specific instruction that's a harmless no-op on plain
        # Postgres (nothing is LISTENing) -- left in place, not stripped.
        cur.execute(sql)

    # --- driver_insurance_periods: migration 64, verbatim. ---
    cur.execute((migrations_dir / "64_driver_insurance_periods.sql").read_text())

    # New tables created by the migrations above also need the same
    # baseline grant as the earlier batch (grants don't retroactively apply
    # to tables that didn't exist yet).
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role"
    )
    # Re-apply 290's revoke: the blanket grant above would otherwise
    # re-open the exact hole migration 290 closed, since it runs after.
    cur.execute("REVOKE ALL ON financial_events FROM anon")
    cur.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON financial_events FROM authenticated")
    cur.execute("GRANT SELECT ON financial_events TO authenticated")

    yield conn

    cur.execute("RESET ROLE")
    conn.close()

    bootstrap = psycopg2.connect(_DSN)
    bootstrap.autocommit = True
    with bootstrap.cursor() as bcur:
        bcur.execute(f"DROP DATABASE IF EXISTS {pg_test_dbname}")
    bootstrap.close()


@pytest.fixture()
def pg_cur(pg_conn):
    """Function-scoped cursor: truncates the tables under test before each
    test for isolation, and always resets to the admin role afterward."""
    cur = pg_conn.cursor()
    cur.execute("RESET ROLE")
    for table in ("rides", "drivers", "users", "financial_events", "driver_insurance_periods"):
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
    yield cur
    cur.execute("RESET ROLE")
    cur.execute("SELECT set_config('request.jwt.claims', '', false)")


def as_role(cur, role: str | None, claims: dict | None = None):
    """Switch the current session to `role` (anon / authenticated /
    service_role / None for the admin/service connection) with the given
    JWT claim set, matching Supabase's PostgREST convention. Returns the
    same cursor for chaining."""
    cur.execute("RESET ROLE")
    cur.execute(
        "SELECT set_config('request.jwt.claims', %s, false)",
        (json.dumps(claims) if claims else "",),
    )
    if role:
        cur.execute(f"SET ROLE {role}")
    return cur
