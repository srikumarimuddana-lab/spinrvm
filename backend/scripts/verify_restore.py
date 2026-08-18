"""Read-only verification tool for a restored Supabase branch (ACTION_ITEMS.md E7).

Run this by hand, against the connection string of a Supabase PITR *branch*
restore (see docs/runbooks/pitr-restore.md, Option A step 2 "Verify branch
data"), to turn "eyeball a row count" into a concrete pass/fail check and to
produce a wall-clock timing that feeds the runbook's RTO measurement.

This script is intentionally NOT wired into any route, background loop, or
CI job. It is an opt-in tool a human runs from a terminal, once a restore
branch already exists. It never runs a Supabase restore itself.

Usage:
    # Preferred: explicit, unambiguous restore-branch URL
    python -m backend.scripts.verify_restore \\
        --database-url "postgresql://postgres:***@db.<branch-ref>.supabase.co:5432/postgres"

    # Or via env var (name is deliberately NOT "DATABASE_URL" — see the
    # production guard below)
    RESTORE_BRANCH_DATABASE_URL="postgresql://...branch..." \\
        python -m backend.scripts.verify_restore

Exit code is non-zero if any check fails, so this composes into a drill
checklist or a future CI-adjacent script without extra parsing.

Read-only contract: every statement this script issues is a SELECT (plus a
session-level `SET ... READ ONLY`). It never executes INSERT / UPDATE /
DELETE / DDL against the target database.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

try:
    from backend.utils.money import to_decimal
except ImportError:
    try:
        from utils.money import to_decimal
    except ImportError:
        # Fallback so this script has no hard dependency on a specific
        # backend module layout surviving refactors — Decimal-only, never
        # float, per CLAUDE.md's money-arithmetic convention.
        def to_decimal(amount: Any) -> Decimal:
            if amount is None:
                return Decimal("0")
            if isinstance(amount, Decimal):
                return amount
            return Decimal(str(amount))


# Core tables this repo's own runbook + tests care about for a restore drill.
# Verified against backend/migrations/ (see CLAUDE.md migration conventions):
#   users                     - base table (rider/driver/admin identity)
#   drivers                   - base table
#   rides                     - base table; state machine lives in routes/rides/
#   payouts                   - 138_payouts_missing_columns.sql, 331_payouts_amount_numeric.sql
#   stripe_disputes           - 88_stripe_disputes.sql
#   driver_insurance_periods  - 64_driver_insurance_periods.sql (append-only, 7yr retention)
#   financial_events          - 58_financial_events.sql (append-only ledger)
CORE_TABLES: tuple[str, ...] = (
    "users",
    "drivers",
    "rides",
    "payouts",
    "stripe_disputes",
    "driver_insurance_periods",
    "financial_events",
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class VerificationReport:
    results: list[CheckResult] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def add(self, name: str, passed: bool, detail: str = "") -> None:
        self.results.append(CheckResult(name=name, passed=passed, detail=detail))

    @property
    def ok(self) -> bool:
        return all(r.passed for r in self.results)


class ProductionURLGuardError(RuntimeError):
    """Raised when the target URL is missing, or looks like it might be production."""


def _mask_url(url: str) -> str:
    """Mask credentials in a Postgres URL for safe printing."""
    if "@" not in url:
        return url
    scheme_and_creds, _, rest = url.partition("@")
    scheme, _, _creds = scheme_and_creds.partition("//")
    return f"{scheme}//***:***@{rest}"


def _normalize_for_comparison(url: str) -> str:
    """Normalize a Postgres URL for equality comparison only (not for use as a URL)."""
    return url.strip().rstrip("/")


def resolve_database_url(cli_url: str | None) -> str:
    """Resolve the restore-branch URL, refusing anything that looks like prod.

    Precedence: --database-url flag, then RESTORE_BRANCH_DATABASE_URL env var.
    We deliberately do NOT fall back to a bare DATABASE_URL env var — that
    name is reserved, throughout this repo, for the real production/staging
    connection string (see backend/scripts/run_migrations.py), and silently
    reading it here would be exactly the kind of "ran a restore-verification
    tool against prod by accident" failure this script exists to prevent.
    """
    url = cli_url or os.environ.get("RESTORE_BRANCH_DATABASE_URL")
    if not url:
        raise ProductionURLGuardError(
            "No restore-branch database URL given. Pass --database-url "
            "explicitly, or set RESTORE_BRANCH_DATABASE_URL. This script "
            "will NOT read a bare DATABASE_URL — that env var is reserved "
            "for the production/staging connection in this repo."
        )

    prod_url = os.environ.get("DATABASE_URL")
    if prod_url and _normalize_for_comparison(prod_url) == _normalize_for_comparison(url):
        raise ProductionURLGuardError(
            "REFUSING TO RUN: the resolved restore-branch URL is identical "
            "to this shell's DATABASE_URL. verify_restore.py must never be "
            "pointed at production. If this really is a restore branch, "
            "unset DATABASE_URL in this shell or double-check you copied "
            "the branch connection string (not the primary project's)."
        )

    return url


def _connect(url: str):
    """Open a psycopg connection and set the session read-only.

    Mirrors backend/scripts/run_migrations.py's psycopg v3 / v2 fallback so
    this script has the same install footprint as the migration runner it
    sits next to. The session is explicitly set read-only at the Postgres
    level as a second line of defense on top of "this script never emits a
    write statement" — belt and suspenders for a tool that will be run by
    hand against a database someone just spent time restoring.
    """
    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(url, autocommit=True)
    except ImportError:
        try:
            import psycopg2  # type: ignore

            conn = psycopg2.connect(url)
            conn.autocommit = True
        except ImportError:
            print(
                "ERROR: neither psycopg (v3) nor psycopg2 is installed. "
                "Install one of them: pip install 'psycopg[binary]'",
                file=sys.stderr,
            )
            sys.exit(2)

    with conn.cursor() as cur:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


def check_row_counts(conn, report: VerificationReport, tables: tuple[str, ...] = CORE_TABLES) -> dict[str, int]:
    """Report row counts for the core tables. Fails any table that errors or is empty."""
    counts: dict[str, int] = {}
    for table in tables:
        try:
            with conn.cursor() as cur:
                # Table names come only from the CORE_TABLES constant above,
                # never from user input, so this f-string is not a
                # SQL-injection risk.
                cur.execute(f"SELECT count(*) FROM {table}")  # noqa: S608
                (count,) = cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - surface loudly, then continue other checks
            report.add(f"row_count:{table}", False, f"query failed: {exc}")
            counts[table] = -1
            continue

        counts[table] = int(count)
        # An empty restored table is a real finding for a drill (either the
        # restore is incomplete or the timestamp predates any data) — flag
        # it as a failure rather than silently reporting "0 rows, moving on".
        report.add(
            f"row_count:{table}",
            count > 0,
            f"{count} rows" if count > 0 else "0 rows — restore may be incomplete or empty at this timestamp",
        )
    return counts


def find_sample_completed_ride(conn) -> str | None:
    """Pick one recent completed ride to walk through the lifecycle check."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM rides WHERE status = 'completed' ORDER BY created_at DESC LIMIT 1")
        row = cur.fetchone()
    return row[0] if row else None


def check_ride_lifecycle(conn, report: VerificationReport, ride_id: str | None) -> None:
    """Walk one completed ride's full lifecycle: ride row, insurance periods, ledger.

    This is a read-only sampling check, not exhaustive validation — it exists
    to catch a restore that has the ride row but is missing its related
    audit/ledger rows (e.g. a partial restore, or a foreign-key target that
    silently didn't come across).
    """
    if ride_id is None:
        report.add("ride_lifecycle:sample_found", False, "no completed ride found in rides table")
        return
    report.add("ride_lifecycle:sample_found", True, f"ride_id={ride_id}")

    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, status, driver_id, rider_id, created_at FROM rides WHERE id = %s",
            (ride_id,),
        )
        ride_row = cur.fetchone()
    report.add(
        "ride_lifecycle:ride_row",
        ride_row is not None and ride_row[1] == "completed",
        f"status={ride_row[1] if ride_row else 'MISSING'}",
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT period, started_at, ended_at FROM driver_insurance_periods WHERE ride_id = %s ORDER BY started_at",
            (ride_id,),
        )
        insurance_rows = cur.fetchall()
    # A completed ride should typically have at least one insurance-period
    # row tied to it (period 2 and/or 3, per CLAUDE.md's insurance-period
    # table). Zero rows for a completed ride is a real gap worth flagging in
    # a drill, but not necessarily a hard failure (a pre-migration-64 ride
    # legitimately has none) — report it, don't silently pass.
    report.add(
        "ride_lifecycle:insurance_periods",
        len(insurance_rows) > 0,
        (
            f"{len(insurance_rows)} driver_insurance_periods row(s) for this ride"
            if insurance_rows
            else "0 rows — may be a pre-migration-64 ride; verify manually if unexpected"
        ),
    )

    with conn.cursor() as cur:
        cur.execute(
            "SELECT event_type, delta_cents, created_at FROM financial_events WHERE ride_id = %s ORDER BY created_at",
            (ride_id,),
        )
        ledger_rows = cur.fetchall()
    report.add(
        "ride_lifecycle:financial_events",
        len(ledger_rows) > 0,
        (
            f"{len(ledger_rows)} financial_events row(s) for this ride"
            if ledger_rows
            else "0 rows — may be a pre-migration-58 ride; verify manually if unexpected"
        ),
    )

    # Any money amounts pulled from the ledger for display must go through
    # Decimal, never float (CLAUDE.md money-arithmetic rule). delta_cents is
    # an integer count of cents; convert defensively via to_decimal so any
    # future column added to this SELECT inherits the right handling.
    for _event_type, delta_cents, _created_at in ledger_rows:
        _ = to_decimal(delta_cents)  # exercised for type safety; not summed/displayed here


def run_verification(database_url: str, tables: tuple[str, ...] = CORE_TABLES) -> VerificationReport:
    report = VerificationReport()
    start = time.monotonic()

    conn = _connect(database_url)
    try:
        check_row_counts(conn, report, tables)
        ride_id = find_sample_completed_ride(conn)
        check_ride_lifecycle(conn, report, ride_id)
    finally:
        conn.close()

    report.elapsed_seconds = time.monotonic() - start
    return report


def print_report(report: VerificationReport, database_url: str) -> None:
    print(f"verify_restore.py — target: {_mask_url(database_url)}")
    print("-" * 72)
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.name}: {result.detail}")
    print("-" * 72)
    overall = "PASS" if report.ok else "FAIL"
    print(f"Overall: {overall}")
    print(f"Elapsed: {report.elapsed_seconds:.2f}s (record this in the drill's RTO measurement)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Connection string for the restored Supabase branch. If omitted, "
        "reads RESTORE_BRANCH_DATABASE_URL. Never reads a bare DATABASE_URL.",
    )
    args = parser.parse_args(argv)

    try:
        database_url = resolve_database_url(args.database_url)
    except ProductionURLGuardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    report = run_verification(database_url)
    print_report(report, database_url)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
