"""Apply backend/migrations/*.sql in deterministic order.

Replaces the previous "paste this into the Supabase SQL Editor" workflow
described in README_MIGRATION.md. See backend/migrations/README.md for
the naming convention.

Usage:
    # Dry-run — show which migrations would be applied
    python -m backend.scripts.run_migrations --dry-run

    # Apply pending migrations
    python -m backend.scripts.run_migrations

    # Show applied vs pending
    python -m backend.scripts.run_migrations --status

How it works
------------
1. Requires DATABASE_URL (direct Postgres connection, typically the
   Supabase pooler URL — `…pooler.supabase.com:6543` with the service
   role password). We intentionally do NOT go through the Supabase
   REST client here because multi-statement DDL + transactional guards
   need a raw psycopg session.
2. Reads `backend/migrations/24_schema_migrations.sql` first if the
   tracking table is missing (bootstrap case), then consults
   `schema_migrations` to decide what's pending.
3. For each pending file (numeric-prefix order; see migration_sort_key), wraps the file's
   contents in a single transaction with an INSERT into
   schema_migrations at the end, so either the whole migration +
   provenance row commits together, or neither does.
4. Refuses to apply a file whose checksum differs from an already-
   recorded row. Migrations are append-only; edit-in-place is how
   you get divergent schemas across environments.

This script is intentionally small. Full Alembic adoption is tracked
as P1 work item 0.6 in the audit roadmap.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
TRACKING_TABLE_MIGRATION = "24_schema_migrations.sql"

# Files that must NEVER be applied even though they are real, merged files in
# backend/migrations/ — applying them as written would be a security
# regression relative to the live schema. See ACTION_ITEMS.md G2 ("116
# migration files merged to `main` had never been applied to the live
# database", "Still open" item 1) for the full investigation each reason
# below paraphrases.
#
# These are permanent, not "applied later with caveats": per the append-only
# migration rule we never edit or delete the files, and this runner must
# never execute them against any environment (fresh staging DB, disaster-
# recovery replay, a naive full re-run) even though they sort into the
# normal pending range by filename.
#
# To add a future entry: put the filename (exact, as it sits in
# backend/migrations/) as the key and a one-line reason (why it must never
# run, referencing the ACTION_ITEMS.md item that found it) as the value.
# Don't remove an entry without the same level of investigation that added
# it — and once a file is genuinely cleared, prefer superseding it with a
# corrective migration and removing its entry here in the same change (see
# backend/migrations/CLAUDE.md for the append-only convention this respects).
NEVER_APPLY: dict[str, str] = {
    "70_fix_financial_events_rls.sql": (
        "Would replace the live financial_events_select RLS policy (which correctly "
        "re-reads users.role from the table) with one that trusts the JWT's role claim "
        "directly -- exactly what CLAUDE.md's JWT trust model section says never to do "
        "for non-admin tokens. See ACTION_ITEMS.md G2, 'Still open' item 1."
    ),
    "78_fix_pii_function_search_path.sql": (
        "Would strip 'vault' from encrypt_driver_pii/decrypt_driver_pii's search_path, "
        "breaking the vault-based PII flow migration 138 already fixed them to use. "
        "See ACTION_ITEMS.md G2, 'Still open' item 1."
    ),
    "137_fix_pii_encrypt_pgsodium_perms.sql": (
        "Ambiguous change to encrypt_driver_pii/decrypt_driver_pii's search_path and "
        "owner; live state doesn't cleanly match either the pre- or post-138 target. "
        "Needs a human security review before ever running, not an automated apply. "
        "See ACTION_ITEMS.md G2, 'Still open' item 1."
    ),
    "26_rls_coverage_gap.sql": (
        "RLS is already enabled on every table it targets; only the two named "
        "*_deny_all policies' presence on the live schema is unconfirmed. Low risk "
        "either way -- left alone pending confirmation rather than blindly re-applied. "
        "See ACTION_ITEMS.md G2, 'Still open' item 1."
    ),
    "444_ride_payment_operations.sql": (
        "Declares ride_payment_operations.ride_id uuid REFERENCES rides(id), but "
        "rides.id is TEXT, so CREATE TABLE fails (42804) on a fresh database. "
        "Superseded by 447_fix_payment_ops_and_live_marker_text_ids.sql."
    ),
    "445_monotonic_live_driver_marker.sql": (
        "Declares update_live_driver_marker(p_driver_id uuid, ...) against TEXT "
        "drivers.id: every call fails (text = uuid), and re-running it after 447 "
        "would add a second overload (PGRST203). Superseded by "
        "447_fix_payment_ops_and_live_marker_text_ids.sql."
    ),
}

# Matches a dollar-quote tag opener: `$$` or `$tag$` (tag must start with a
# letter/underscore, so positional parameters like `$1` never match).
_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$")


def _split_sql_statements(sql: str) -> list[str]:
    """Split a SQL script into individual, comment-free, executable statements.

    Ported from the (now-deleted) scripts/migrate.py -- ACTION_ITEMS.md A39
    found migrate.py's schema_migrations shape doesn't match what's actually
    live in production, but its CONCURRENTLY-safe splitter (ACTION_ITEMS.md
    B0) was real, tested, and solved a gap this runner never had a fix for:
    without it, this runner's single-transaction `_apply_one` would fail on
    any migration containing CREATE/DROP INDEX CONCURRENTLY (Postgres refuses
    those inside a transaction block).

    A lexical scan (not a naive `str.split(";")`) tracks state so top-level
    semicolons are the only split points -- ones inside `--` line comments,
    `/* */` block comments, `'...'` string literals (with `''` escaping), and
    `$tag$...$tag$` dollar-quoted bodies (PL/pgSQL function bodies) are never
    treated as statement boundaries. Comment text is dropped from the output
    entirely, so the result is directly executable and safe to scan for
    keywords like CONCURRENTLY without re-parsing comments out again.
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    state = "normal"  # normal | string | dollar
    dollar_tag = None

    while i < n:
        ch = sql[i]
        if state == "normal":
            if sql[i : i + 2] == "--":
                j = sql.find("\n", i)
                i = n if j == -1 else j + 1
                continue
            if sql[i : i + 2] == "/*":
                j = sql.find("*/", i + 2)
                i = n if j == -1 else j + 2
                continue
            if ch == "'":
                buf.append(ch)
                state = "string"
                i += 1
                continue
            m = _DOLLAR_TAG_RE.match(sql, i)
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                state = "dollar"
                i += len(dollar_tag)
                continue
            if ch == ";":
                statements.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
        elif state == "string":
            if sql[i : i + 2] == "''":
                buf.append("''")
                i += 2
                continue
            buf.append(ch)
            if ch == "'":
                state = "normal"
            i += 1
        else:  # state == "dollar"
            if sql[i : i + len(dollar_tag)] == dollar_tag:
                buf.append(dollar_tag)
                i += len(dollar_tag)
                state = "normal"
                dollar_tag = None
                continue
            buf.append(ch)
            i += 1

    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)

    return _merge_explicit_transaction_blocks([s.strip() for s in statements if s.strip()])


# Transaction-control statements that can open/close an explicit BEGIN...COMMIT
# wrapper. Matched against a whole, already-split statement (case-insensitive)
# -- never a substring -- so a PL/pgSQL `BEGIN`/`END` inside a dollar-quoted
# function body (which the state machine above never splits out on its own)
# can't accidentally match.
_TXN_OPEN = {"BEGIN", "BEGIN TRANSACTION", "BEGIN WORK", "START TRANSACTION"}
_TXN_CLOSE = {"COMMIT", "COMMIT TRANSACTION", "COMMIT WORK", "END"}


def _merge_explicit_transaction_blocks(statements: list[str]) -> list[str]:
    """Fold an explicit top-level `BEGIN; ...; COMMIT;` wrapper into one statement.

    Lexical gap this closes: a migration may wrap only *part* of itself (e.g.
    a `SET LOCAL lock_timeout` scoped over a couple of `ALTER TABLE`
    statements) in an explicit transaction, while a later `CREATE/DROP INDEX
    CONCURRENTLY` statement is deliberately left outside it, since
    CONCURRENTLY can never run inside a transaction block. `_apply_one`
    detects that CONCURRENTLY and routes the *whole file* through
    `_apply_one_autocommit`, which executes each split statement in its own
    `cur.execute()` call. Left unmerged, the literal "BEGIN" and "COMMIT"
    statements would (a) be handed to Postgres as bare, meaningless
    statements the test suite (rightly) treats as a splitter bug, and worse,
    (b) silently discard the migration's own protection: a `SET LOCAL`
    executed as its own standalone autocommit call loses scope the instant
    that call returns, so the `ALTER TABLE` statements after it would no
    longer be bounded by the lock_timeout the migration author wrote them to
    have.

    The fix folds everything between the BEGIN and its matching COMMIT into
    one statement, joined with the original `;` separators, and drops the
    BEGIN/COMMIT keywords themselves. Postgres already treats a multi-command
    query string sent in one call as an implicit single transaction unless
    the string itself contains explicit BEGIN/COMMIT -- see the "Multiple
    Statements in a Simple Query" behavior in the Postgres protocol docs --
    so sending the interior as one `cur.execute()` call reproduces exactly
    the atomicity the migration asked for, without ever hitting Postgres with
    a bare "BEGIN"/"COMMIT". A CONCURRENTLY statement placed *outside* this
    span (before BEGIN or after COMMIT, as it must be) is untouched and keeps
    running as its own individual autocommit statement.
    """
    merged: list[str] = []
    i, n = 0, len(statements)
    while i < n:
        if statements[i].strip().upper() in _TXN_OPEN:
            j = i + 1
            inner: list[str] = []
            while j < n and statements[j].strip().upper() not in _TXN_CLOSE:
                inner.append(statements[j])
                j += 1
            if j < n:  # found a matching close; fold the span
                if inner:
                    merged.append("; ".join(inner))
                i = j + 1
                continue
            # No matching COMMIT/END found (malformed/truncated transaction) --
            # leave the BEGIN as-is rather than silently dropping it; the
            # existing keyword-allowlist test will flag it loudly instead of
            # this function guessing at intent.
        merged.append(statements[i])
        i += 1
    return merged


def _checksum(path: Path) -> str:
    """SHA-256 of the file contents, hex-encoded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_sort_key(name: str) -> Tuple[int, str]:
    """Order migrations by their numeric prefix, full filename as tiebreak.

    Plain lexicographic sort mis-orders unpadded prefixes ("157_…" before
    "15_…", "224_…" before "48_…"), which breaks a fresh-environment run the
    moment a later migration ALTERs a table a two-digit migration created.
    Files with no numeric prefix sort last, lexicographically.
    """
    digits = ""
    for ch in name:
        if not ch.isdigit():
            break
        digits += ch
    return (int(digits) if digits else sys.maxsize, name)


def _discover_migrations() -> List[Path]:
    """Return all .sql files in MIGRATIONS_DIR in numeric-prefix order."""
    files = sorted(
        (p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file()),
        key=lambda p: migration_sort_key(p.name),
    )
    if not files:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")
    return files


def _connect():
    """Open a psycopg connection from DATABASE_URL.

    psycopg v3 is preferred; fall back to psycopg2 if only v2 is
    installed. The caller gets a connection with autocommit OFF so each
    migration runs in its own transaction.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        print(
            "ERROR: DATABASE_URL is not set. Export the Postgres URL "
            "(e.g. the Supabase pooler URL with the service role password) "
            "before running migrations.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        import psycopg  # type: ignore

        return psycopg.connect(url, autocommit=False)
    except ImportError:
        try:
            import psycopg2  # type: ignore

            conn = psycopg2.connect(url)
            conn.autocommit = False
            return conn
        except ImportError:
            print(
                "ERROR: neither psycopg (v3) nor psycopg2 is installed. "
                "Install one of them: pip install 'psycopg[binary]'",
                file=sys.stderr,
            )
            sys.exit(2)


def _ensure_tracking_table(conn, *, allow_create: bool = True) -> bool:
    """Apply 24_schema_migrations.sql if the tracking table doesn't exist.

    Chicken-and-egg bootstrap: we can't query schema_migrations to see
    whether it itself has been applied, so we fall back to information_schema.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'schema_migrations'"
        )
        exists = cur.fetchone() is not None

    if exists:
        return True
    if not allow_create:
        return False

    tracking_path = MIGRATIONS_DIR / TRACKING_TABLE_MIGRATION
    if not tracking_path.exists():
        raise RuntimeError(f"Tracking table migration {TRACKING_TABLE_MIGRATION} not found in {MIGRATIONS_DIR}")

    print(f"[bootstrap] applying {TRACKING_TABLE_MIGRATION} to create schema_migrations table")
    sql = tracking_path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s) ON CONFLICT (filename) DO NOTHING",
            (TRACKING_TABLE_MIGRATION, _checksum(tracking_path)),
        )
    conn.commit()
    return True


def _fetch_applied(conn) -> dict[str, str]:
    """Return {filename: checksum} for every row already in schema_migrations."""
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        return {row[0]: row[1] for row in cur.fetchall()}


def _classify(
    files: List[Path], applied: dict[str, str]
) -> Tuple[List[Path], List[Path], List[Tuple[Path, str, str]], List[Tuple[Path, str]]]:
    """Split migrations into (pending, already_applied, drifted, skipped).

    A file in NEVER_APPLY is classified as `skipped` before it ever gets a
    chance to land in `pending` or `already` -- it has its own status, never
    conflated with either. If it is *also* already tracked as applied in
    schema_migrations, that's a contradiction (someone/something ran it
    outside this runner despite the skip-list) and must be surfaced loudly,
    not silently absorbed into either bucket.
    """
    pending: List[Path] = []
    already: List[Path] = []
    drifted: List[Tuple[Path, str, str]] = []
    skipped: List[Tuple[Path, str]] = []

    for path in files:
        reason = NEVER_APPLY.get(path.name)
        if reason is not None:
            skipped.append((path, reason))
            if path.name in applied:
                print(
                    f"CONTRADICTION: {path.name} is in the NEVER_APPLY skip-list "
                    f"(reason: {reason}) but is ALSO recorded as applied in "
                    "schema_migrations. It was applied through some other path "
                    "(direct SQL editor, another tool, manual psql) despite the "
                    "skip-list -- this needs human investigation of the live schema, "
                    "not silent acceptance either way.",
                    file=sys.stderr,
                )
            continue
        if path.name not in applied:
            pending.append(path)
            continue
        current = _checksum(path)
        recorded = applied[path.name]
        if current != recorded:
            drifted.append((path, recorded, current))
        else:
            already.append(path)

    return pending, already, drifted, skipped


def _apply_one(conn, path: Path) -> None:
    """Apply one migration file + record provenance.

    Migrations containing CREATE/DROP INDEX CONCURRENTLY (or any other
    statement that cannot run inside a transaction block) must execute
    outside a transaction -- Postgres rejects them otherwise. Those are
    detected from the comment-free, split statements (never raw text, so a
    CONCURRENTLY mention inside a rollback comment doesn't misroute a file
    that contains no actual concurrent index) and routed through the
    autocommit path; everything else runs in the normal single-transaction
    path unchanged.
    """
    sql = path.read_text()
    checksum = _checksum(path)
    print(f"[apply] {path.name}  ({len(sql):,} bytes, sha256={checksum[:12]}…)")

    statements = _split_sql_statements(sql)
    needs_autocommit = any("CONCURRENTLY" in stmt.upper() for stmt in statements)

    if needs_autocommit:
        _apply_one_autocommit(conn, path.name, checksum, statements)
        return

    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
            (path.name, checksum),
        )
    conn.commit()


def _apply_one_autocommit(conn, filename: str, checksum: str, statements: List[str]) -> None:
    """Apply a migration containing CONCURRENTLY -- no transaction wrapper.

    Each already-split, comment-free statement executes individually under
    autocommit; the schema_migrations row is inserted last, in the same
    autocommit mode, so a crash partway through still leaves whatever
    already-committed statements ran (the same partial-apply risk this
    runner's normal transactional path doesn't have -- inherent to
    CONCURRENTLY itself, not something this function can avoid).
    """
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            cur.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                (filename, checksum),
            )
    finally:
        conn.autocommit = False


def _apply_pending(conn, pending: List[Path]) -> int:
    """Apply each pending migration in order; report cleanly on a mid-batch failure.

    Each file already commits independently (see `_apply_one` /
    `_apply_one_autocommit`), so a failure partway through a batch never
    needs a rollback of earlier files -- but letting the exception propagate
    unhandled left the operator with a raw traceback and no summary of what
    actually landed vs. what's still pending. Catch it here instead: report
    the applied/failed/remaining split explicitly (never silently swallow --
    the original exception is still printed), then exit non-zero so CI/an
    operator can't mistake a partial batch for success.
    """
    applied_now: List[Path] = []
    for path in pending:
        try:
            _apply_one(conn, path)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: any DB/SQL error must be reported, not masked
            remaining = pending[len(applied_now) + 1 :]
            print(f"ERROR: failed to apply {path.name}: {exc}", file=sys.stderr)
            print(
                f"Applied {len(applied_now)} migration(s) before the failure: "
                + (", ".join(p.name for p in applied_now) or "(none)"),
                file=sys.stderr,
            )
            print(
                f"Not applied ({1 + len(remaining)} total, including the failed file): "
                + ", ".join([path.name] + [p.name for p in remaining]),
                file=sys.stderr,
            )
            return 1
        applied_now.append(path)

    print(f"Applied {len(applied_now)} migration(s).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be applied; don't run SQL.")
    parser.add_argument("--status", action="store_true", help="Print applied + pending, then exit.")
    args = parser.parse_args()

    files = _discover_migrations()

    conn = _connect()
    try:
        tracking_exists = _ensure_tracking_table(conn, allow_create=not (args.status or args.dry_run))
        applied = _fetch_applied(conn) if tracking_exists else {}
        pending, already, drifted, skipped = _classify(files, applied)

        if drifted:
            print("ERROR: the following migrations have been modified after being applied:", file=sys.stderr)
            for path, recorded, current in drifted:
                print(f"  - {path.name}: recorded={recorded[:12]}… current={current[:12]}…", file=sys.stderr)
            print(
                "Migrations are append-only. Create a new migration to amend the schema "
                "instead of editing an applied one.",
                file=sys.stderr,
            )
            return 1

        if args.status or args.dry_run:
            print(f"Applied: {len(already)}")
            for p in already:
                print(f"  ✓ {p.name}")
            print(f"Pending: {len(pending)}")
            for p in pending:
                print(f"  … {p.name}")
            print(f"PERMANENTLY SKIPPED (security regression risk): {len(skipped)}")
            for p, reason in skipped:
                print(f"  ⛔ {p.name}: {reason}")
            if args.status:
                return 0
            if args.dry_run:
                print("(dry-run — no changes made; PERMANENTLY SKIPPED files above will never be applied)")
                return 0

        if not pending:
            print("No pending migrations.")
            return 0

        return _apply_pending(conn, pending)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
