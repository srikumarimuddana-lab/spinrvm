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

What this tier proves -- and doesn't (ACTION_ITEMS.md C108)
-------------------------------------------------------------
These tests prove policy *logic*: given a claim set, is access correctly
granted or denied. They do not prove any of this is exercised by real
production traffic today. This app's entire end-user auth model is the
custom `JWT_SECRET`-signed scheme CLAUDE.md documents, verified by the
backend's own dependency functions -- no anon/publishable-key Supabase
client (`createClient(...)`) exists anywhere in shipped code, and this
app's `JWT_SECRET` is a wholly separate secret from whatever key Supabase's
PostgREST validates `authenticated`/`anon`-role requests against. No rider,
driver, or admin has ever obtained a real Supabase-issued session JWT, so
no `anon`/`authenticated`-role RLS policy in this schema is reachable by a
real request; the backend's exclusive use of the service-role key (which
bypasses RLS entirely) is the only path real traffic ever takes. That makes
this tier dormant-but-correct rather than actively enforcing anything today
-- still worth testing (it protects against a future direct-PostgREST path
and against RLS regressions if the auth model ever changes), but not a
claim that these policies are live-enforced against real users. See
ACTION_ITEMS.md C108 for the full finding and the corrected mechanism
(earlier drafts of this finding cited an empty `auth.users` table as the
reason; that was wrong on its own -- JWT signature validation doesn't
require a matching `auth.users` row -- though the no-live-traffic
conclusion holds on the grounds above).

Coverage scope (deliberately partial -- see ACTION_ITEMS.md C49 for the
running total, not this comment, which has already gone stale across
multiple rounds of additions)
-------------------------------------------------------------
This is incremental DB-role-level RLS coverage, not the whole ~127-207
policy-statement backlog. Started 2026-08-31 with five tables chosen for
consequence (`users`, `drivers`, `rides` from `backend/supabase_rls.sql`,
`financial_events` money ledger migrations 58/70/290, `driver_insurance_periods`
safety audit trail migration 64); extended since across several rounds
(`saved_addresses`, the transactional outbox, `lost_and_found`(_messages),
`referral_payouts`, `auto_payout_batches`, `complaints`, the migration-26
deny-all tables, the `corporate_*` money/PII tables (migrations 05/17/27/142)
plus `stripe_disputes`/`stripe_orphan_refunds` (88/254), `otp_records`/
`rider_email_verification_otp`/`emergency_contacts`/`safety_incidents`/
`safety_incident_photos`, the remaining four of the nine migration-27
corporate tables (`corporate_policies`, `corporate_allowed_domains`,
`ride_payment_sources`, `corporate_policy_evaluations`), `audit_logs`
(security audit trail, migrations 06/51/57) plus its two insurance-period
audit siblings `driver_insurance_period_corrections` (355) and
`driver_period_distances` (249), and -- this round -- the admin PII-export
audit trail: `data_transfer_export_jobs` (262), `compliance_export_events`
(263), and `admin_export_approval_requests` (268)). See each test file's own docstring
for what it covers, and ACTION_ITEMS.md C49 for the current fraction
covered.

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
import re
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


def _extract_section(sql_text: str, start_marker: str, end_marker: str) -> str:
    """Pull the text between two exact substrings out of a larger .sql file
    (inclusive of start_marker, exclusive of end_marker). Used for a
    multi-concern migration where only one section is in this harness's
    scope and the other sections touch tables the harness doesn't build
    (e.g. migration 142 also scrubs `disputes` PII and repairs `ride_offers`
    -- unrelated to the corporate-financial-tables section this pulls out),
    so the section is read out of the merged file rather than hand-copied."""
    start = sql_text.index(start_marker)
    end = sql_text.index(end_marker, start)
    return sql_text[start:end]


def _extract_policy(sql_text: str, marker: str) -> str:
    """Pull one `CREATE POLICY ...;` statement out of a larger .sql file by
    tracking paren depth from its first '(' to close, then reading to the
    next ';' -- same paren-balanced-then-semicolon technique as
    _extract_create_table, generalized since an individual CREATE POLICY
    statement isn't wrapped in one outer paren group the way a CREATE TABLE
    is. Used for migration 06, which creates audit_logs' two original
    policies interleaved with unrelated cloud_messages/push_tokens
    statements this harness doesn't build."""
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
    raise AssertionError(f"unbalanced parens extracting policy from {marker!r}")


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

# CREATE ... EXCEPTION duplicate_object THEN ALTER (not a bare NULL): these
# roles are cluster-level, not per-database, and CI's backend-test job runs
# 3 pytest sessions back-to-back against the SAME postgres:15 service
# container -- "Run backend tests" (mocked, but tests/test_phase_distance_parity.py
# connects to the same real DATABASE_URL and creates anon/authenticated/
# service_role with a bare `NOLOGIN`, no BYPASSRLS), then this repo's own
# tests/direct_pool, then this file. Whichever session's CREATE ROLE runs
# first wins the idempotent race; a later session's "EXCEPTION THEN NULL"
# used to silently accept whatever attributes the first creator left behind
# -- which meant `service_role` sometimes reached these tests WITHOUT
# BYPASSRLS, and every "service_role bypasses RLS" test failed with an
# empty result set or a false RLS-violation error instead of exercising the
# real production access path (found via the exact repro above: 5 CI-only
# failures across test_core_tables_rls.py/test_money_and_safety_rls.py/
# test_saved_addresses_rls.py that never reproduced locally on a *fresh*
# Postgres -- because a fresh Postgres never has the poisoned role). The
# ALTER on the exception path makes this file authoritative over these
# roles' attributes regardless of creation order.
_ROLE_SETUP_SQL = """
DO $$ BEGIN
    CREATE ROLE anon NOLOGIN NOINHERIT;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE anon NOLOGIN NOINHERIT;
END $$;

DO $$ BEGIN
    CREATE ROLE authenticated NOLOGIN NOINHERIT;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE authenticated NOLOGIN NOINHERIT;
END $$;

DO $$ BEGIN
    CREATE ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
EXCEPTION WHEN duplicate_object THEN
    ALTER ROLE service_role NOLOGIN NOINHERIT BYPASSRLS;
END $$;
"""

# Minimal stub tables for the other backend/supabase_rls.sql targets, so the
# file can be applied byte-for-byte (unmodified) instead of us hand-slicing
# just the users/drivers/rides sections out of it.
_STUB_TABLES_SQL = """
CREATE TABLE otp_records (id text primary key);
CREATE TABLE settings (id text primary key);
INSERT INTO settings (id) VALUES ('app_settings');
CREATE TABLE support_tickets (id text primary key, user_id text);
CREATE TABLE faqs (id text primary key);
CREATE TABLE vehicle_types (id text primary key);
CREATE TABLE fare_configs (id text primary key);
CREATE TABLE service_areas (id text primary key);
-- Minimal stubs so migrations 25 (refresh_tokens) and 314
-- (auto_payout_batches) can ALTER these tables verbatim -- neither
-- table's own schema is under this harness's test scope, only the
-- side-effect ADD COLUMN statements those migrations carry.
CREATE TABLE admin_staff (id text primary key);
CREATE TABLE payouts (id text primary key);
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

    # --- saved_addresses: real shipped table def (extracted verbatim, same
    # technique as users/drivers/rides above) + migration 378's owner-scoped
    # SELECT/INSERT/DELETE policies (ACTION_ITEMS.md B40), verbatim. ---
    cur.execute(_extract_create_table(schema_sql, "saved_addresses"))
    cur.execute("ALTER TABLE saved_addresses ENABLE ROW LEVEL SECURITY")
    cur.execute((migrations_dir / "378_saved_addresses_rls_policies.sql").read_text())

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

    # --- stripe_events / schema_migrations / refresh_tokens + the migration
    # 26 deny-all policies on those three: migrations 22, 24, 25, 26,
    # verbatim, in order. 25's token_version ALTERs target users (already
    # created above) and admin_staff (stubbed above). 26 also flips
    # ENABLE ROW LEVEL SECURITY on six unrelated tables via `ALTER TABLE IF
    # EXISTS`, which is a no-op here since none of those six are part of
    # this harness's scope -- safe to apply unmodified. ---
    for fname in (
        "22_stripe_events.sql",
        "24_schema_migrations.sql",
        "25_refresh_tokens_and_token_version.sql",
        "26_rls_coverage_gap.sql",
    ):
        cur.execute((migrations_dir / fname).read_text())

    # --- complaints: migration 68, with a harness-only patch (NOT a change
    # to the migration file itself). Migration 68 declares ride_id/
    # reporter_id/reported_id/resolved_by as UUID REFERENCES rides(id)/
    # users(id), but backend/supabase_schema.sql's *current* users.id/
    # rides.id are TEXT -- applying the file verbatim raises
    # psycopg2.errors.DatatypeMismatch ("cannot be implemented ... uuid and
    # text") on the very first FK. No later migration corrects this (grepped
    # every migration touching `complaints` -- 128/192/367 only add columns
    # elsewhere). This is real, previously-undocumented drift, not a
    # harness bug -- flagged as a new ACTION_ITEMS.md finding for a human
    # session with production access to resolve (check
    # information_schema.columns for complaints' actual live FK types; this
    # sandbox has none). Swapping UUID->text here only lets the *policies*
    # (which already compare via auth.uid()::text, unaffected by the
    # underlying column type) be exercised; it does not silently fix or
    # hide the drift, which stays flagged in the backlog regardless of what
    # this test harness does to work around it.
    _complaints_sql = (migrations_dir / "68_complaints_table.sql").read_text()
    _complaints_sql = re.sub(
        r"\b(ride_id|reporter_id|reported_id|resolved_by)(\s+)UUID\b",
        r"\1\2text",
        _complaints_sql,
    )
    cur.execute(_complaints_sql)

    # --- lost_and_found: migrations 69 (original create) then 69a (repair --
    # on a fresh DB with no legacy table, 69a's own CREATE TABLE IF NOT
    # EXISTS is a no-op and its rename/not-null DO blocks are guarded no-ops
    # too; only its ADD COLUMN/policy-recreate statements actually apply),
    # in that order -- same "apply the schema's evolution in migration
    # order" approach already used for financial_events above.
    #
    # Migration 69 has the *same* UUID-vs-TEXT drift as complaints (68,
    # patched above): id/ride_id/reporter_id are declared UUID REFERENCES
    # rides(id)/users(id), which are TEXT in the current schema. 69a's own
    # CREATE TABLE IF NOT EXISTS (a no-op here, since 69 already created the
    # table) uses TEXT for the same columns, confirming this repo's own
    # later migration already expected TEXT -- 69 was just never corrected.
    # Same harness-only patch as complaints, same reason: found here for a
    # second table, broadening the ACTION_ITEMS.md finding rather than
    # treating it as complaints-specific. ---
    _laf_sql = (migrations_dir / "69_lost_and_found_table.sql").read_text()
    _laf_sql = re.sub(r"\b(id|ride_id|reporter_id)(\s+)UUID\b", r"\1\2text", _laf_sql)
    cur.execute(_laf_sql)
    cur.execute((migrations_dir / "69a_lost_and_found_repair_schema.sql").read_text())

    # --- lost_and_found_messages (+ lost_and_found.reporter_type/status
    # extension): migration 115, verbatim. ---
    cur.execute((migrations_dir / "115_lost_and_found_chat.sql").read_text())

    # --- fix migration 115's lfm_select/lfm_insert driver-visibility bug
    # (found writing this harness's own lost_and_found_messages tests):
    # migration 412, verbatim. ---
    cur.execute((migrations_dir / "412_lost_and_found_messages_rls_driver_visibility_fix.sql").read_text())

    # --- referral_payouts: migration 171, verbatim. ---
    cur.execute((migrations_dir / "171_referral_payouts.sql").read_text())

    # --- auto_payout_batches (+ service_areas.instant_payout_enabled /
    # payouts.auto_retry_count side-effect ALTERs, against the stubs
    # above): migration 314, verbatim. ---
    cur.execute((migrations_dir / "314_auto_payout_and_instant_kill_switch.sql").read_text())

    # New tables created by the migrations above also need the same
    # baseline grant as the earlier batches (grants don't retroactively
    # apply to tables that didn't exist yet).
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO anon, authenticated, service_role"
    )
    # Re-apply 290's revoke again: this blanket grant runs after the first
    # re-application above too, so it would silently re-open the same hole
    # a second time without this repeated.
    cur.execute("REVOKE ALL ON financial_events FROM anon")
    cur.execute("REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON financial_events FROM authenticated")
    cur.execute("GRANT SELECT ON financial_events TO authenticated")

    # --- outbox_messages / settings.outbox_receipts_enabled / outbox_* RPCs:
    # migration 399, verbatim. Applied last (after the blanket ALL-TABLES
    # grant above) so its own `REVOKE ALL ... FROM anon, authenticated` has
    # the final say -- same reasoning as the financial_events re-revoke just
    # above, except 399 already carries its own REVOKE/GRANT statements, so
    # nothing extra needs re-applying here. (ACTION_ITEMS.md C57: this file
    # never picked up 399 when the outbox feature dark-launched, so every
    # test in test_transactional_outbox.py failed with
    # UndefinedColumn/UndefinedTable/"function ... does not exist".)
    cur.execute((migrations_dir / "399_transactional_outbox.sql").read_text())

    # --- corporate billing tables (ACTION_ITEMS.md C49 remaining scope):
    # migration 05 (corporate_accounts create), 17 (FK-guard + RLS enable +
    # its own admin-only FOR ALL policy, verbatim -- the FK ALTERs
    # type-guard-skip harmlessly here since users/rides ids are TEXT while
    # corporate_accounts.id is UUID, the same real drift already noted above
    # for complaints/lost_and_found), 27 (creates the 9 corporate money/PII
    # tables + their original FOR ALL admin policies, verbatim), then
    # migration 142's corporate-financial-tables section pulled out via
    # _extract_section() rather than hand-copied, since 142 is a
    # mixed-concern migration whose other sections (disputes PII scrub,
    # ride_offers CHECK widening, an accepted-offer repair) touch tables
    # outside this harness's scope. The extracted slice covers 142's §2
    # (drops the 9 FOR-ALL policies from 27, replaces them with SELECT-only
    # admin-read policies restricted to admin/super_admin + REVOKE/GRANT
    # write lockdown) and the three "Member read own ..." policies that
    # follow it in the same file section, verbatim.
    #
    # Note: corporate_accounts's OWN admin policy (migration 17) was never
    # touched by 142's fix -- it still checked `users.role = 'admin'` only,
    # excluding super_admin, unlike the 9 sibling tables. Migration 416 (PR
    # #5307) closed this the same day, applying 142's exact fix pattern to
    # this one table -- applied below, after the baseline grant block, for
    # the same reason 142's own fix is sequenced after it (see that block's
    # comment: granting then narrowing, not narrowing then re-granting). ---
    cur.execute((migrations_dir / "05_corporate_accounts.sql").read_text())
    cur.execute((migrations_dir / "17_corporate_accounts_fk.sql").read_text())
    cur.execute((migrations_dir / "27_corporate_b2b_v1.sql").read_text())

    # New tables need the same baseline grant as earlier batches -- granted
    # by name (like the stripe_disputes/stripe_orphan_refunds grant below),
    # NOT the repeated "ALL TABLES in schema" blanket used earlier in this
    # fixture. This block runs after migration 399's outbox lockdown (the
    # last of the earlier batches), and the blanket form would silently
    # re-open outbox_messages' REVOKE (and financial_events', and the nine
    # corporate tables' own REVOKE below) the same way it did for
    # financial_events twice above -- a real bug caught by tracing exactly
    # which REVOKEs precede this point before writing it this way. A
    # table-by-name grant can't touch a table it doesn't name, so nothing
    # needs re-revoking after it.
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON corporate_accounts, corporate_wallets, "
        "corporate_wallet_transactions, corporate_members, corporate_member_allowances, "
        "corporate_allowance_requests, corporate_policies, corporate_allowed_domains, "
        "ride_payment_sources, corporate_policy_evaluations "
        "TO anon, authenticated, service_role"
    )

    migration_142_sql = (migrations_dir / "142_fix_rls_financial_tables.sql").read_text()
    cur.execute(
        _extract_section(
            migration_142_sql,
            "-- 2. Corporate financial tables:",
            "-- 3. PIPEDA data minimization:",
        )
    )

    # Migration 416 (PR #5307): corporate_accounts's own admin-policy fix,
    # verbatim -- applies 142's exact pattern (SELECT-only admin/super_admin
    # policy + REVOKE/GRANT write lockdown) to this one table, which 142
    # itself never touched.
    cur.execute((migrations_dir / "416_corporate_accounts_rls_super_admin_fix.sql").read_text())

    # --- stripe_disputes (migration 88) / stripe_orphan_refunds (migration
    # 254): admin-only read tables, verbatim. Unlike every other
    # admin-role-check applied in this harness, these two policies check
    # current_setting('request.jwt.claims', true)::json->>'role' directly,
    # without the nullif(...,'')-guarded cast the auth.uid()/auth.role()
    # shim functions use above -- see test_stripe_admin_tables_rls.py's
    # module docstring for why this harness deliberately never exercises
    # either policy with a truly empty claims GUC. ---
    cur.execute((migrations_dir / "88_stripe_disputes.sql").read_text())
    cur.execute((migrations_dir / "254_stripe_orphan_refunds.sql").read_text())

    # These two new tables need the same baseline grant as every other batch
    # above -- granted by name rather than the repeated "ALL TABLES in
    # schema" blanket used earlier, specifically so it does NOT re-open the
    # financial_events / corporate_* / outbox_messages holes those already
    # closed (a table-by-name grant can't touch tables it doesn't name, so
    # no further re-revoke is needed after this one). Neither 88 nor 254
    # carries its own REVOKE.
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON stripe_disputes, stripe_orphan_refunds "
        "TO anon, authenticated, service_role"
    )

    # --- emergency_contacts (migration 120): owner-only SELECT/INSERT/DELETE,
    # no UPDATE policy at all and no admin override (this migration's own
    # comment says so; migration 378 explicitly mirrors that no-admin-override
    # shape for saved_addresses). Applied verbatim -- it also ALTERs `rides`
    # to add gps_anonymized_at, a harmless additive column on the table
    # already created above. ---
    cur.execute((migrations_dir / "120_ensure_emergency_contacts_and_gps_column.sql").read_text())

    # --- rider_email_verification_otp: migration 299 declares user_id UUID,
    # which errors against this schema's TEXT users.id (same drift class as
    # complaints/lost_and_found -- see their own comments above) and was
    # never actually applied in production for that reason. Migration 362 is
    # the real, corrected source: its DO block creates the table itself (with
    # user_id TEXT) when 299 never ran, so 299 is skipped entirely rather
    # than patched -- there is nothing to extract from it that 362 doesn't
    # already redo correctly. ---
    cur.execute((migrations_dir / "362_fix_rider_email_verification_otp_user_id_type.sql").read_text())

    # --- safety_incidents (migration 94): service_role bypass, admin/
    # super_admin SELECT+UPDATE via a `users.role` lookup, reporter-only
    # SELECT of their own reports, no INSERT/DELETE policy for anyone but
    # service_role. Migrations 280/315 only add nullable columns (a merge
    # pointer, an idempotency key) with no RLS effect and no CHECK
    # constraint this harness's tests need -- deliberately not applied, to
    # keep this section to the migration that actually defines the
    # policies. ---
    cur.execute((migrations_dir / "94_safety_incidents.sql").read_text())

    # --- safety_incident_photos (migration 340): service_role-only, zero
    # policy for anon/authenticated by design (evidence photos can name a
    # third party). Extracted up to its storage-bucket INSERT -- this
    # harness has no `storage` schema stub, and that INSERT is irrelevant to
    # the table's own RLS behavior under test. ---
    migration_340_sql = (migrations_dir / "340_safety_incident_photos.sql").read_text()
    cur.execute(
        _extract_section(
            migration_340_sql,
            "-- 340_safety_incident_photos.sql",
            "-- Private bucket.",
        )
    )

    # New tables need the same baseline grant as every other batch above --
    # granted by name so it doesn't re-open any earlier REVOKE (see the
    # comment on the corporate-tables grant above for why "ALL TABLES in
    # schema" isn't used past this point). otp_records needs no such grant
    # here: its stub table (_STUB_TABLES_SQL) already existed when the
    # earlier blanket "ALL TABLES in schema" grant ran, so it's covered.
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON emergency_contacts, "
        "rider_email_verification_otp, safety_incidents, safety_incident_photos "
        "TO anon, authenticated, service_role"
    )

    # --- audit_logs (ACTION_ITEMS.md C49): migration 06 creates the table +
    # its original two policies (interleaved with unrelated cloud_messages/
    # push_tokens statements this harness doesn't build -- pulled out via
    # _extract_create_table/_extract_policy rather than hand-copied), 51
    # locks it down (SELECT-only admin policy, append-only UPDATE trigger,
    # REVOKE/GRANT narrowing), 56 adds a flag-gated DELETE trigger so
    # purge_pii_retention()'s 7y retention step can still delete old rows,
    # 57 adds actor_id (needed by migration 399's outbox_redrive() INSERT,
    # previously covered by a stub table here -- see the removed
    # _STUB_TABLES_SQL comment history) plus a second, unconditional
    # UPDATE-OR-DELETE trigger -- applied in filename-sort order (51 < 56 <
    # 57), matching how run_migrations.py would actually apply them. Two
    # more `57_*.sql` files besides this one exist (duplicate numeric
    # prefix, expected per CLAUDE.md) and are deliberately not applied --
    # both only rewrite purge_pii_retention(), unrelated to audit_logs'
    # RLS/grant/trigger surface under test here.
    #
    # 56 and 57's triggers do NOT compose safely -- see
    # test_flag_gated_delete_is_still_blocked_by_migration_57_trigger below,
    # which reproduces (does not fix) a real, currently-live production bug:
    # ACTION_ITEMS.md C112. ---
    migration_06_sql = (migrations_dir / "06_cloud_messaging.sql").read_text()
    cur.execute(_extract_create_table(migration_06_sql, "audit_logs"))
    cur.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    cur.execute(_extract_policy(migration_06_sql, 'CREATE POLICY "Admin full access audit_logs"'))
    cur.execute(_extract_policy(migration_06_sql, 'CREATE POLICY "Service role bypass audit_logs"'))
    # Baseline grant before 51's REVOKE narrows it -- mirrors Supabase's own
    # default new-table grant (same reasoning as the blanket "ALL TABLES"
    # grant above), so 51's REVOKE has something real to revoke rather than
    # being a no-op against a table nothing was ever granted on.
    cur.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON audit_logs TO anon, authenticated, service_role")
    cur.execute((migrations_dir / "51_audit_logs_lockdown.sql").read_text())
    cur.execute((migrations_dir / "56_audit_logs_delete_lockdown.sql").read_text())
    cur.execute((migrations_dir / "57_audit_logs_schema_standardization.sql").read_text())

    # --- driver_insurance_period_corrections (migration 355) / -- 355 references
    # driver_insurance_periods(id) (migration 64, already applied above);
    # driver_period_distances (migration 249) -- both self-contained
    # regulatory-audit tables (append-only, owner-or-admin SELECT, no
    # INSERT/UPDATE/DELETE policy for anon/authenticated), applied verbatim
    # in full like driver_insurance_periods itself. ---
    cur.execute((migrations_dir / "355_driver_insurance_period_corrections.sql").read_text())
    cur.execute((migrations_dir / "249_driver_period_distances.sql").read_text())

    # Baseline grant for the two plain new tables, by name (same reasoning
    # as every other by-name grant above -- doesn't touch audit_logs, which
    # already got its own baseline grant before 51's REVOKE ran). Neither
    # table has an INSERT/UPDATE/DELETE policy for anon/authenticated, so
    # without this grant every write attempt from those roles would be
    # denied at the grant layer instead of ever reaching RLS -- same
    # baseline-grant-then-narrow shape used for every other table above.
    # (Postgres raises the identical SQLSTATE 42501 either way, so no test
    # here can distinguish which layer produced a given denial -- this
    # grant is about matching Supabase's own default table permissions,
    # not about producing an observably different error.)
    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON driver_insurance_period_corrections, "
        "driver_period_distances TO anon, authenticated, service_role"
    )

    # --- admin PII-export audit trail (ACTION_ITEMS.md C49): three tables
    # tied to the dual-approval/export-audit hardening already done at the
    # app layer this session (B1/W2a-c) -- this round adds the RLS/DB-
    # constraint backstop. `data_transfer_export_jobs` (262, + 264's
    # additive `reason` column) and `admin_export_approval_requests` (268)
    # are both service-role-only: zero policy for anon/authenticated at
    # all, RLS default-denies the rest. `compliance_export_events` (263) is
    # SELECT-admin/super_admin-only, INSERT/UPDATE/DELETE service-role-
    # only, with a tamper-evidence trigger -- 285's own DELETE-gating
    # section (extracted below, not its much larger purge_pii_retention()
    # redefinition, which reaches tables outside this harness's scope, e.g.
    # driver_location_history/ride_routes/price_searches) applied on top so
    # the retention-purge flag path can be exercised too. Applied in
    # filename-sort order (262 < 263 < 264 < 268 < 270 < 274 < 278),
    # matching how run_migrations.py would actually apply them.
    #
    # 270/274/278 drop each table's admin-identity FK to `users(id)` -- a
    # real, already-shipped fix for a bug class where a real admin caller's
    # id (admin_staff.id, or the "admin-001"/"break-glass" sentinel) can
    # never satisfy that FK (confirmed live in each migration's own header:
    # zero rows ever written to any of these three tables before its fix).
    # Applied here so this harness doesn't silently paper over that removed
    # constraint the way a synthetic seeded-users-row test double otherwise
    # could -- see test_admin_export_audit_rls.py's FK-regression tests. ---
    cur.execute((migrations_dir / "262_data_transfer_export_jobs.sql").read_text())
    cur.execute((migrations_dir / "263_compliance_export_events.sql").read_text())
    cur.execute((migrations_dir / "264_data_transfer_export_reason.sql").read_text())
    cur.execute((migrations_dir / "268_admin_export_approvals.sql").read_text())
    cur.execute((migrations_dir / "270_export_approvals_admin_id_no_fk.sql").read_text())
    cur.execute((migrations_dir / "274_data_transfer_export_jobs_admin_id_no_fk.sql").read_text())
    cur.execute((migrations_dir / "278_compliance_export_events_admin_id_no_fk.sql").read_text())

    migration_285_sql = (migrations_dir / "285_retention_purge_compliance_export_events.sql").read_text()
    cur.execute(
        _extract_section(
            migration_285_sql,
            "CREATE OR REPLACE FUNCTION _compliance_export_events_immutable()",
            "-- Trigger definition itself is unchanged",
        )
    )

    cur.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON data_transfer_export_jobs, "
        "admin_export_approval_requests, compliance_export_events "
        "TO anon, authenticated, service_role"
    )

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
    for table in (
        "rides",
        "drivers",
        "users",
        "financial_events",
        "driver_insurance_periods",
        "saved_addresses",
        "outbox_messages",
        "audit_logs",
        "complaints",
        "lost_and_found",
        "lost_and_found_messages",
        "referral_payouts",
        "auto_payout_batches",
        "refresh_tokens",
        "stripe_events",
        "corporate_accounts",
        "corporate_wallets",
        "corporate_wallet_transactions",
        "corporate_members",
        "corporate_member_allowances",
        "corporate_allowance_requests",
        "corporate_policies",
        "corporate_allowed_domains",
        "corporate_policy_evaluations",
        "ride_payment_sources",
        "stripe_disputes",
        "stripe_orphan_refunds",
        "otp_records",
        "emergency_contacts",
        "rider_email_verification_otp",
        "safety_incident_photos",
        "safety_incidents",
        "driver_insurance_period_corrections",
        "driver_period_distances",
        "data_transfer_export_jobs",
        "admin_export_approval_requests",
        "compliance_export_events",
    ):
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
    # settings isn't truncated (it's a single always-present config row, not
    # per-test data) -- but outbox_receipts_enabled must reset to its
    # documented default between tests, since _enable_producer() flips it to
    # TRUE and several tests (e.g. test_producer_flag_defaults_false) assert
    # the default.
    cur.execute("UPDATE settings SET outbox_receipts_enabled = FALSE WHERE id = 'app_settings'")
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
