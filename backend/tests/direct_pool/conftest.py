"""Fixtures for real-Postgres tests in backend/tests/direct_pool/.

Why this directory is special
------------------------------
C50 Phase 1 (docs/audit/2026-09-02-pgbouncer-direct-pool-migration-plan.md,
T11). Same rationale as `backend/tests/rls/conftest.py`: every other test in
`backend/tests/` uses the mocked `mock_supabase_client` fixture, which
cannot exercise real Postgres wire-protocol behavior (real transactions,
real constraint enforcement, real Supavisor-style connection handling).
Phase 2 (T12/T13, out of scope here) will need exactly that for the direct
dispatch pool's SQL. This directory is the harness Phase 2's tests will run
against — nothing in Phase 1 itself calls it (backend/repositories/
dispatch_pool.py has no real callers yet).

Pattern, copied from `backend/tests/rls/conftest.py`
-------------------------------------------------------
1. Connect to a real, disposable Postgres via `TEST_DATABASE_URL` (falling
   back to `DATABASE_URL`), with CREATE DATABASE rights.
2. Create a uniquely-named scratch *database* for the test session (dropped
   at the end).
3. Apply the ACTUAL shipped SQL this repo tracks: `backend/supabase_schema.sql`
   (users/drivers/rides table defs, extracted verbatim) plus the specific
   migrations that shape `ride_offers` and `driver_insurance_periods` --
   the tables the plan doc names as relevant to the dispatch claim path.
4. Self-skips cleanly (not a failure) when TEST_DATABASE_URL/DATABASE_URL
   isn't set, or when psycopg2 isn't installed.

Migration set -- VERIFIED against the actual files in this repo, not
trusted from the plan doc's reference list (which itself flags the numbers
as "may have shifted")
--------------------------------------------------------------------------
The plan doc's Work Plan section (T11) cites migrations 77, 80, 131, 143,
224, 253, 354. Verifying each against `backend/migrations/` at
implementation time found:

  * 77_match_and_claim_driver.sql / 80_fix_match_and_claim_driver_type.sql
    -- EXCLUDED. Both define `match_and_claim_driver()`, a `FOR UPDATE SKIP
    LOCKED` SQL function the plan doc's own corrections table (#13) already
    flags as "dead code, zero callers" -- nothing in this repo calls it.
    Verified two independent reasons it cannot apply to this fixture (or to
    a real postgres:15 CI service, which is NOT the `postgis/postgis`
    image): (a) both reference `drivers.current_lat` / `current_lng`, but
    the actual `drivers` table (backend/supabase_schema.sql) has no such
    columns -- only `lat` / `lng`; (b) both call `ST_SetSRID`/`ST_MakePoint`/
    `::geography`, which requires the `postgis` extension, unavailable on
    vanilla `postgres:15` (`CREATE EXTENSION postgis` errors "extension...
    not available" -- confirmed against a live postgres:15 container while
    building this fixture). Applying either file as-is fails outright.
  * 131_ride_offers_preempted_status.sql, both
    143_ride_offers_cancelled_status.sql /
    143_ride_offers_one_accepted_index.sql (two files share the 131/143
    prefix in this repo -- verified via `ls backend/migrations | sort -V`),
    224_ride_offers_expires_at.sql -- all confirmed present and apply
    cleanly against `ride_offers` (created by 100_batch_dispatch.sql, which
    the plan doc's reference list omitted -- added here since none of
    131/143/224 apply without the base table existing first).
  * 253_insurance_period_transition_rpc.sql -- confirmed present, applies
    cleanly (SECURITY DEFINER RPC over driver_insurance_periods, created by
    64_driver_insurance_periods.sql -- also added here for the same reason
    as 100 above).
  * 354_revoke_public_execute_on_security_definer_fns.sql -- confirmed
    present. Applies cleanly; it is a sweep with no fixed table list (see
    its own header comment -- "THIS MIGRATION DOES NOT CHANGE PRODUCTION --
    it is expected to be a no-op there" on a database where the grants were
    already correct, which is exactly this fixture's freshly-built state).

Net set actually applied here: supabase_schema.sql (users/drivers/rides,
extracted verbatim) + 100 (ride_offers, drivers.acceptance_rate,
service_areas dispatch config) + 131 + 143 (x2) + 224 + 64
(driver_insurance_periods) + 253 + 354, in that dependency order.

Running these tests
--------------------
Needs a real reachable Postgres with CREATE DATABASE rights. Point
`TEST_DATABASE_URL` (or `DATABASE_URL`) at it:

    export TEST_DATABASE_URL="<your connection string>"
    cd backend
    pytest tests/direct_pool -c /dev/null --confcutdir=tests/direct_pool

`-c /dev/null --confcutdir=tests/direct_pool` stops pytest from also loading
`backend/tests/conftest.py` (and its coverage gate / mocked-Supabase stack),
same as `tests/rls`. Without a reachable Postgres, every test here is
skipped (not failed, not faked) with a clear skip-reason string.

Windows / git-bash note: `-c /dev/null` does not resolve the same way in a
git-bash shell as it does in CI (ubuntu-latest) or a native Linux/macOS
shell -- pytest ends up with an unexpected rootdir and the run misbehaves.
If reproducing locally on Windows, pass `-c <path-to-an-actual-empty-file>`
instead (e.g. an empty `.ini` you create next to this conftest); CI itself
is unaffected since it runs on ubuntu-latest.

Conftest-level skip caveat (found while building this fixture)
-----------------------------------------------------------------
A bare module-level `pytestmark = pytest.mark.skipif(...)` placed in THIS
file (which `tests/rls/conftest.py` also does) does NOT apply to sibling
test modules -- pytest only honors `pytestmark` in a module actually
collected as a test file, not a conftest.py that merely sits next to one.
Confirmed by direct repro: with TEST_DATABASE_URL/DATABASE_URL unset, tests
errored with a raw `psycopg2.OperationalError` instead of skipping. This
file uses `pytest_collection_modifyitems` instead, which is the correct
hook for a conftest-level skip. `tests/rls/conftest.py` has the same latent
bug and was not fixed here (out of scope, pre-existing, untouched by C50).
"""

from __future__ import annotations

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

_DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

_SKIP_REASON = (
    "direct_pool real-Postgres tests require psycopg2 and a real Postgres reachable "
    "via TEST_DATABASE_URL (or DATABASE_URL) -- see backend/tests/direct_pool/conftest.py "
    "docstring. Skipped, not faked: a mocked Supabase client cannot exercise real "
    "Postgres transaction/connection behavior."
)
_SKIP_CONDITION = psycopg2 is None or not _DSN


def pytest_collection_modifyitems(config, items):
    """Skip every test collected under this directory when the pre-flight
    condition isn't met.

    A bare module-level `pytestmark = pytest.mark.skipif(...)` in a
    conftest.py (the pattern this file was first written with, matching
    tests/rls/conftest.py) does NOT apply to sibling test modules --
    pytest only honors `pytestmark` at the level of the module actually
    collected as a test file, not a conftest.py that merely sits alongside
    it. Confirmed by direct repro while building this fixture: with
    TEST_DATABASE_URL/DATABASE_URL unset, tests errored with a raw
    `psycopg2.OperationalError` instead of skipping. This hook is the
    correct, documented way to apply a conftest-level skip to every test
    under the directory. tests/rls/conftest.py has the same latent bug
    (its `pytestmark` also silently does nothing) -- out of scope to fix
    here since tests/rls is pre-existing and untouched by this task, but
    worth flagging in the PR description.
    """
    if not _SKIP_CONDITION:
        return
    skip_marker = pytest.mark.skip(reason=_SKIP_REASON)
    for item in items:
        item.add_marker(skip_marker)


# Migrations applied, in dependency order. See the module docstring for why
# 77/80 are excluded and why 100/64 were added beyond the plan doc's list.
# 401 added for C50 Phase 2 (T14) -- the dispatch_claim_batch RPC itself,
# the actual object under test in test_claim_batch.py. 12 and 157 added
# alongside it: dispatch_claim_batch's SQL body references drivers.status
# and drivers.availability_claimed_at, and NEITHER column exists in the
# base CREATE TABLE this fixture extracts from supabase_schema.sql -- they
# are both later ALTER TABLE ADD COLUMN migrations (12_driver_lifecycle_
# status.sql for `status`, 157_driver_availability_claimed_at.sql for
# `availability_claimed_at`). Without them, every dispatch_claim_batch call
# below would fail with "column drivers.status does not exist" rather than
# testing the RPC at all.
_MIGRATION_FILES = (
    "100_batch_dispatch.sql",  # ride_offers table + drivers.acceptance_rate
    "131_ride_offers_preempted_status.sql",
    "143_ride_offers_cancelled_status.sql",
    "143_ride_offers_one_accepted_index.sql",
    "224_ride_offers_expires_at.sql",
    "64_driver_insurance_periods.sql",
    "253_insurance_period_transition_rpc.sql",
    "354_revoke_public_execute_on_security_definer_fns.sql",
    "12_driver_lifecycle_status.sql",
    "157_driver_availability_claimed_at.sql",
    "402_dispatch_claim_batch.sql",
)

# 100_batch_dispatch.sql and 64_driver_insurance_periods.sql both define RLS
# policies calling auth.uid() -- Supabase's auth schema, which ships outside
# this repo's migrations. Reproduced here the same way tests/rls/conftest.py
# does (same published Supabase definition), otherwise applying either
# migration raises "schema auth does not exist" against a vanilla Postgres.
_AUTH_SHIM_SQL = """
CREATE SCHEMA IF NOT EXISTS auth;

CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
      SELECT (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')::uuid
    $$;
"""

# 354_revoke_public_execute_on_security_definer_fns.sql's sweep queries
# has_function_privilege('anon', ...) / ('authenticated', ...) -- those
# roles are Supabase-managed, outside this repo's migrations, same as the
# auth schema above. Same role names/shapes as tests/rls/conftest.py's
# _ROLE_SETUP_SQL (minus BYPASSRLS on service_role, irrelevant here since
# this fixture does no RLS-as-a-specific-role testing).
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


def _extract_create_table(sql_text: str, table_name: str) -> str:
    """Pull one `CREATE TABLE IF NOT EXISTS <table_name> ( ... );` block out
    of supabase_schema.sql by tracking paren depth -- same technique as
    tests/rls/conftest.py, so we test the exact DDL this repo ships rather
    than a copy that can drift out of sync."""
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


def _dsn_with_dbname(dsn: str, dbname: str) -> str:
    import urllib.parse as _u

    parts = _u.urlsplit(dsn)
    return _u.urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", parts.query, parts.fragment))


def _apply_migration_sql(cur, sql: str) -> None:
    """Execute one migration file's SQL, statement-by-statement.

    Reuses backend/scripts/run_migrations.py's `_split_sql_statements` (the
    same CONCURRENTLY-safe lexical splitter the real migration runner uses)
    rather than a second implementation of the same parsing -- per repo
    convention. Required because 143_ride_offers_one_accepted_index.sql
    contains `CREATE UNIQUE INDEX CONCURRENTLY`, which Postgres refuses
    inside a transaction block; sending the whole file as one multi-
    statement query (psycopg2's simple query protocol implicitly wraps it
    in one) fails with "CREATE INDEX CONCURRENTLY cannot run inside a
    transaction block" even with `conn.autocommit = True` on the
    connection, because autocommit only affects transaction boundaries
    BETWEEN separate `execute()` calls, not within a single multi-statement
    string.
    """
    try:
        from backend.scripts.run_migrations import _split_sql_statements
    except ImportError:  # pragma: no cover - import style varies by entrypoint
        import sys as _sys

        _sys.path.insert(0, str(_BACKEND_DIR.parent))
        from backend.scripts.run_migrations import _split_sql_statements

    for statement in _split_sql_statements(sql):
        cur.execute(statement)


@pytest.fixture(scope="session")
def pg_test_dbname() -> str:
    return f"direct_pool_test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="session")
def pg_conn(pg_test_dbname):
    """Session-scoped admin connection to a throwaway *database*. Builds
    users/drivers/rides (extracted verbatim from supabase_schema.sql) plus
    ride_offers and driver_insurance_periods (and their supporting
    migrations) once; drops the database at the end of the session."""
    bootstrap = psycopg2.connect(_DSN)
    bootstrap.autocommit = True
    with bootstrap.cursor() as bcur:
        bcur.execute(f"CREATE DATABASE {pg_test_dbname}")
    bootstrap.close()

    conn = psycopg2.connect(_dsn_with_dbname(_DSN, pg_test_dbname))
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(_AUTH_SHIM_SQL)
    cur.execute(_ROLE_SETUP_SQL)

    # users/drivers/rides -- extracted verbatim, same technique as tests/rls.
    schema_sql = (_BACKEND_DIR / "supabase_schema.sql").read_text()
    for table in ("users", "drivers", "rides"):
        cur.execute(_extract_create_table(schema_sql, table))

    # service_areas is referenced by 100_batch_dispatch.sql's ALTER TABLE --
    # not itself under test, so a minimal stub is enough (same convention as
    # tests/rls/conftest.py's _STUB_TABLES_SQL).
    cur.execute("CREATE TABLE service_areas (id text primary key)")

    migrations_dir = _BACKEND_DIR / "migrations"
    for fname in _MIGRATION_FILES:
        sql = (migrations_dir / fname).read_text()
        _apply_migration_sql(cur, sql)

    yield conn

    conn.close()

    bootstrap = psycopg2.connect(_DSN)
    bootstrap.autocommit = True
    with bootstrap.cursor() as bcur:
        bcur.execute(f"DROP DATABASE IF EXISTS {pg_test_dbname}")
    bootstrap.close()


@pytest.fixture()
def pg_cur(pg_conn):
    """Function-scoped cursor: truncates the tables under test before each
    test for isolation."""
    cur = pg_conn.cursor()
    for table in ("ride_offers", "driver_insurance_periods", "rides", "drivers", "users"):
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
    yield cur
