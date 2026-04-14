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
3. For each pending file (lexicographically sorted), wraps the file's
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
import sys
from pathlib import Path
from typing import List, Tuple

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
TRACKING_TABLE_MIGRATION = "24_schema_migrations.sql"


def _checksum(path: Path) -> str:
    """SHA-256 of the file contents, hex-encoded."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _discover_migrations() -> List[Path]:
    """Return all .sql files in MIGRATIONS_DIR sorted lexicographically."""
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())
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


def _ensure_tracking_table(conn) -> None:
    """Apply 24_schema_migrations.sql if the tracking table doesn't exist.

    Chicken-and-egg bootstrap: we can't query schema_migrations to see
    whether it itself has been applied, so we fall back to information_schema.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'schema_migrations'"
        )
        exists = cur.fetchone() is not None

    if exists:
        return

    tracking_path = MIGRATIONS_DIR / TRACKING_TABLE_MIGRATION
    if not tracking_path.exists():
        raise RuntimeError(
            f"Tracking table migration {TRACKING_TABLE_MIGRATION} not found in {MIGRATIONS_DIR}"
        )

    print(f"[bootstrap] applying {TRACKING_TABLE_MIGRATION} to create schema_migrations table")
    sql = tracking_path.read_text()
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s) "
            "ON CONFLICT (filename) DO NOTHING",
            (TRACKING_TABLE_MIGRATION, _checksum(tracking_path)),
        )
    conn.commit()


def _fetch_applied(conn) -> dict[str, str]:
    """Return {filename: checksum} for every row already in schema_migrations."""
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM schema_migrations")
        return {row[0]: row[1] for row in cur.fetchall()}


def _classify(
    files: List[Path], applied: dict[str, str]
) -> Tuple[List[Path], List[Path], List[Tuple[Path, str, str]]]:
    """Split migrations into (pending, already_applied, drifted)."""
    pending: List[Path] = []
    already: List[Path] = []
    drifted: List[Tuple[Path, str, str]] = []

    for path in files:
        if path.name not in applied:
            pending.append(path)
            continue
        current = _checksum(path)
        recorded = applied[path.name]
        if current != recorded:
            drifted.append((path, recorded, current))
        else:
            already.append(path)

    return pending, already, drifted


def _apply_one(conn, path: Path) -> None:
    """Apply one migration file inside a transaction + record provenance."""
    sql = path.read_text()
    checksum = _checksum(path)
    print(f"[apply] {path.name}  ({len(sql):,} bytes, sha256={checksum[:12]}…)")
    with conn.cursor() as cur:
        cur.execute(sql)
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
            (path.name, checksum),
        )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be applied; don't run SQL.")
    parser.add_argument("--status", action="store_true", help="Print applied + pending, then exit.")
    args = parser.parse_args()

    files = _discover_migrations()

    conn = _connect()
    try:
        _ensure_tracking_table(conn)
        applied = _fetch_applied(conn)
        pending, already, drifted = _classify(files, applied)

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
            if args.status:
                return 0
            if args.dry_run:
                print("(dry-run — no changes made)")
                return 0

        if not pending:
            print("No pending migrations.")
            return 0

        for path in pending:
            _apply_one(conn, path)

        print(f"Applied {len(pending)} migration(s).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
