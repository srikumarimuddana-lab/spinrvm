"""Retention-purge column-drift regression checks (migration 256).

History: purge_pii_retention() has now been broken three separate times by the
same bug — a step keyed on a timestamp column that exists in a
`CREATE TABLE IF NOT EXISTS` somewhere in the tree but NOT on the live table:

    * Step C  — driver_location_history.recorded_at   (fixed by migration 187)
    * Step F  — stripe_events.created_at              (introduced by migration 67,
                                                       carried by 117/129/141/143/
                                                       187/216/228, fixed here)
    * Step D  — ride_messages.created_at              (08/50 shape vs migration
                                                       98's authoritative
                                                       `timestamp`, fixed here)

Any one of these aborts the whole function with SQLSTATE 42703, and because the
RPC runs in a single transaction, *every* step rolls back — the purge silently
enforces nothing while the loop logs a DB error once a day.

CI has no Postgres, so these checks pin the contract textually:
  1. Steps D and F resolve their column from pg_catalog and RAISE if neither
     candidate exists (never a silent skip).
  2. The re-fork kept every prior step, result key, and the security lockdown.
  3. `TestNoCutoffColumnDrift` is the generic guard: every remaining
     hard-coded `WHERE <col> <op> v_started_at` cutoff in the authoritative
     definition is checked against the columns the migrations actually declare
     for that table. A future re-fork that reintroduces a phantom column fails
     here instead of in production.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"

# The authoritative purge_pii_retention definition. Update BOTH this constant
# and the prior-definition constant below when a new migration re-forks it.
CURRENT_MIGRATION = "256_retention_purge_column_drift_fix.sql"
PRIOR_MIGRATION = "228_retention_purge_price_searches.sql"

SQL_CURRENT = (MIGRATIONS / CURRENT_MIGRATION).read_text()
SQL_PRIOR = (MIGRATIONS / PRIOR_MIGRATION).read_text()


def _function_body(sql: str) -> str:
    """The plpgsql body of purge_pii_retention, without the file's header
    comments — so a column name mentioned only in prose can't satisfy a check."""
    start = sql.index("CREATE OR REPLACE FUNCTION purge_pii_retention")
    end = sql.index("$$;", start)
    return sql[start:end]


BODY_CURRENT = _function_body(SQL_CURRENT)


class TestStepFStripeEvents:
    """stripe_events has never had a created_at column — migration 22 created it
    with received_at, and migration 50 added idx_stripe_events_received_at for
    exactly this step."""

    def test_no_hardcoded_created_at_cutoff(self):
        assert "FROM stripe_events\n        WHERE created_at" not in BODY_CURRENT
        assert "DELETE FROM stripe_events\n        WHERE created_at" not in BODY_CURRENT

    def test_column_resolved_from_catalog_with_received_at_preferred(self):
        assert "'public.stripe_events'::regclass" in BODY_CURRENT
        assert "a.attname IN ('received_at', 'created_at')" in BODY_CURRENT
        assert "CASE a.attname WHEN 'received_at' THEN 1 ELSE 2 END" in BODY_CURRENT

    def test_raises_when_no_candidate_column_exists(self):
        # Loud failure, not a silently skipped retention step.
        assert "IF v_stripe_ts_col IS NULL THEN" in BODY_CURRENT
        assert re.search(
            r"RAISE EXCEPTION\s*\n?\s*'purge_pii_retention Step F: stripe_events has neither",
            BODY_CURRENT,
        )

    def test_delete_and_dry_run_branches_both_use_the_resolved_column(self):
        assert "format('DELETE FROM stripe_events WHERE %I < $1', v_stripe_ts_col)" in BODY_CURRENT
        assert "format('SELECT COUNT(*) FROM stripe_events WHERE %I < $1', v_stripe_ts_col)" in BODY_CURRENT


class TestStepDRideMessages:
    """The live ride_messages is migration 98's shape (`timestamp`), which is
    what routes/rides/chat.py writes and orders by; migrations 08 and 50 declare
    a competing `created_at` shape. Both CREATEs are IF NOT EXISTS, so the step
    must not assume either one."""

    def test_no_hardcoded_created_at_cutoff(self):
        assert "FROM ride_messages\n        WHERE created_at" not in BODY_CURRENT
        assert "DELETE FROM ride_messages\n        WHERE created_at" not in BODY_CURRENT

    def test_column_resolved_from_catalog_with_timestamp_preferred(self):
        assert "'public.ride_messages'::regclass" in BODY_CURRENT
        assert "a.attname IN ('timestamp', 'created_at')" in BODY_CURRENT
        assert "CASE a.attname WHEN 'timestamp' THEN 1 ELSE 2 END" in BODY_CURRENT

    def test_raises_when_no_candidate_column_exists(self):
        assert "IF v_msgs_ts_col IS NULL THEN" in BODY_CURRENT
        assert re.search(
            r"RAISE EXCEPTION\s*\n?\s*'purge_pii_retention Step D: ride_messages has neither",
            BODY_CURRENT,
        )

    def test_delete_and_dry_run_branches_both_use_the_resolved_column(self):
        assert "format('DELETE FROM ride_messages WHERE %I < $1', v_msgs_ts_col)" in BODY_CURRENT
        assert "format('SELECT COUNT(*) FROM ride_messages WHERE %I < $1', v_msgs_ts_col)" in BODY_CURRENT

    def test_chat_window_still_90_days(self):
        assert re.search(r"c_chat_age\s+INTERVAL := INTERVAL '90 days'", BODY_CURRENT)


class TestResolvedColumnsAreObservable:
    """Drift must be visible on every run, not inferred from a stack trace once
    it breaks — the resolved names go into the result JSONB, which the wrapper
    logs and Step's audit INSERT persists."""

    def test_result_reports_resolved_columns(self):
        assert "'retention_ts_columns'" in BODY_CURRENT
        assert "'ride_messages',  v_msgs_ts_col" in BODY_CURRENT
        assert "'stripe_events',  v_stripe_ts_col" in BODY_CURRENT

    def test_python_wrapper_logs_them(self):
        src = (Path(__file__).resolve().parents[1] / "utils" / "retention_purge.py").read_text()
        assert 'data.get("retention_ts_columns")' in src
        assert "ts_cols=%s" in src


class TestReForkIntegrity:
    def test_every_prior_step_survives(self):
        for step in "ABCDEFGHIJKL":
            assert f"Step {step}" in BODY_CURRENT, f"Step {step} missing from the re-fork"

    def test_every_prior_result_key_survives(self):
        for key in re.findall(r"'(\w+)',\s+v_\w+", _function_body(SQL_PRIOR)):
            assert f"'{key}'" in BODY_CURRENT, f"result key {key} dropped in the re-fork"

    def test_retention_windows_unchanged(self):
        for const in re.findall(r"(c_\w+)\s+INTERVAL := (INTERVAL '[^']+')", _function_body(SQL_PRIOR)):
            name, value = const
            assert re.search(rf"{name}\s+INTERVAL := {re.escape(value)}", BODY_CURRENT), (
                f"{name} window changed in the re-fork"
            )

    def test_security_definer_and_grants_intact(self):
        assert "SECURITY DEFINER" in SQL_CURRENT
        assert "SET search_path = public, pg_temp" in SQL_CURRENT
        assert "REVOKE EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) FROM PUBLIC, anon, authenticated" in SQL_CURRENT
        assert "GRANT  EXECUTE ON FUNCTION purge_pii_retention(BOOLEAN) TO service_role" in SQL_CURRENT

    def test_audit_insert_preserved(self):
        assert "'pii_retention_purge'" in BODY_CURRENT
        assert "'system:retention_purge'" in BODY_CURRENT

    def test_dynamic_sql_interpolates_identifiers_only_from_the_catalog(self):
        # %I over pg_catalog-sourced names; the cutoff is bound via USING, never
        # concatenated. p_dry_run stays the only caller-supplied input.
        for stmt in re.findall(r"format\('([^']+)'", BODY_CURRENT):
            assert "%s" not in stmt, f"unsafe %s interpolation in dynamic SQL: {stmt}"
        assert "USING v_started_at - c_chat_age" in BODY_CURRENT
        assert "USING v_started_at - c_stripe_event_age" in BODY_CURRENT

    def test_no_concurrently_in_a_dollar_quoted_migration(self):
        # The runner switches to autocommit and naively splits on ';' when it
        # sees CONCURRENTLY, which would shred this $$-quoted body.
        assert "CONCURRENTLY" not in SQL_CURRENT.upper()


# ── Generic guard: no cutoff may reference a column the tree never declares ───


def _declared_columns() -> tuple[dict[str, set[str]], set[str]]:
    """(table -> every column any migration declares for it, set of tables the
    tree actually CREATEs).

    Only tables in the second set can be validated: several of the oldest
    tables (`rides`, `payouts`, `bank_accounts`, `saved_addresses`,
    `support_tickets`) pre-date migration control — they were created ad hoc in
    the Supabase dashboard, so the tree knows only the columns later ALTERs
    added and cannot prove a column absent."""
    columns: dict[str, set[str]] = defaultdict(set)
    created: set[str] = set()

    for path in MIGRATIONS.glob("*.sql"):
        sql = path.read_text()

        for match in re.finditer(
            r"CREATE TABLE (?:IF NOT EXISTS )?(?:public\.)?(\w+)\s*\((.*?)\n\);",
            sql,
            re.DOTALL | re.IGNORECASE,
        ):
            table, body = match.group(1).lower(), match.group(2)
            created.add(table)
            for line in body.splitlines():
                line = line.strip()
                if not line or line.startswith("--"):
                    continue
                # Skip table-level constraints; take the leading identifier.
                if re.match(r"(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT|EXCLUDE)\b", line, re.I):
                    continue
                name = re.match(r"(\w+)", line)
                if name:
                    columns[table].add(name.group(1).lower())

        for match in re.finditer(
            r"ALTER TABLE (?:ONLY )?(?:public\.)?(\w+)(.*?);",
            sql,
            re.DOTALL | re.IGNORECASE,
        ):
            table, body = match.group(1).lower(), match.group(2)
            for col in re.findall(r"ADD COLUMN (?:IF NOT EXISTS )?(\w+)", body, re.IGNORECASE):
                columns[table].add(col.lower())

    return columns, created


def _cutoff_references(body: str) -> set[tuple[str, str]]:
    """Every (table, column) pair compared against v_started_at in the purge
    body. Statements are split on ';' first so a table can never be paired with
    a cutoff from a neighbouring statement."""
    refs: set[tuple[str, str]] = set()

    for stmt in body.split(";"):
        # Table introductions, with optional alias: FROM x a / UPDATE x / DELETE FROM x
        sources: list[tuple[int, str, str | None]] = []
        for m in re.finditer(
            r"\b(?:DELETE\s+FROM|UPDATE|FROM|JOIN)\s+(?:public\.)?(\w+)(?:\s+(?!WHERE|SET|USING|WHEN|INTO|ON)(\w+))?",
            stmt,
            re.IGNORECASE,
        ):
            sources.append((m.start(), m.group(1).lower(), (m.group(2) or "").lower() or None))

        aliases = {alias: table for _, table, alias in sources if alias}

        for m in re.finditer(r"(?:(\w+)\.)?(\w+)\s*(?:<=|<|>=|>)\s*v_started_at", stmt):
            qualifier, column = (m.group(1) or "").lower(), m.group(2).lower()

            if qualifier:
                table = aliases.get(qualifier, qualifier)
            else:
                prior = [t for pos, t, _ in sources if pos < m.start()]
                if not prior:
                    continue
                table = prior[-1]

            refs.add((table, column))

    return refs


class TestNoCutoffColumnDrift:
    """The guard that would have caught migrations 67, 117, 129, 141, 143, 187,
    216 and 228 shipping a purge keyed on stripe_events.created_at."""

    def test_extraction_finds_the_known_cutoffs(self):
        # Sanity-check the parser itself, so a silently-empty result set can't
        # make the real assertion below vacuously pass.
        refs = _cutoff_references(BODY_CURRENT)
        assert ("rides", "created_at") in refs
        assert ("driver_location_history", "received_at") in refs
        assert ("surge_pricing", "created_at") in refs
        assert len(refs) >= 8

    @staticmethod
    def _undeclared(body: str) -> set[tuple[str, str]]:
        columns, created = _declared_columns()
        return {
            (table, column)
            for table, column in _cutoff_references(body)
            if table in created and column not in columns[table]
        }

    def test_the_guard_covers_the_tables_that_have_drifted(self):
        # stripe_events and ride_messages must be inside the checkable set —
        # otherwise the assertion below is vacuous for exactly the two tables
        # this migration fixes.
        _, created = _declared_columns()
        assert {"stripe_events", "ride_messages", "driver_location_history"} <= created

    def test_every_hardcoded_cutoff_column_is_declared_somewhere(self):
        unknown = sorted(f"{t}.{c}" for t, c in self._undeclared(BODY_CURRENT))
        assert not unknown, (
            "purge_pii_retention keys a retention cutoff on column(s) no migration declares: "
            f"{unknown}. This aborts the whole purge with SQLSTATE 42703 and rolls back every "
            "step. Either fix the column name or resolve it from pg_catalog like Steps D and F."
        )

    def test_prior_definition_still_fails_this_guard(self):
        # Pins the bug this migration fixes: 228 keyed Step F on a phantom
        # column. If this ever passes, the check above has stopped working.
        assert ("stripe_events", "created_at") in self._undeclared(_function_body(SQL_PRIOR))
