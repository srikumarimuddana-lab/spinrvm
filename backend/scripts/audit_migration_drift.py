"""Read-only reconciliation of backend/migrations/*.sql against the live
`schema_migrations` tracking table.

Why this exists
----------------
ACTION_ITEMS.md C22 found `schema_migrations` on production tracking far
fewer files than exist in `backend/migrations/` (161/407 as of the
2026-08-17/18 sessions that individually verified and applied a handful of
high-risk gaps: migrations 321, 323, 324). The item explicitly says the
*broader* reconciliation — a full diff of every repo file against the live
table — is a separate, higher-stakes audit that "neither started" one of
two ways: (a) making `run_migrations.py`'s own dry-run path talk to the
live table, or (b) a manual file-by-file cross-check.

This script is path (a), built as a standalone tool rather than a change to
`run_migrations.py` itself (that file is being edited concurrently by
another task adding a skip-list feature — touching it here would create an
avoidable merge conflict). It reuses `run_migrations.py`'s own file
discovery and checksum logic (import, not copy) so the two never drift
apart on what "the file list" or "the checksum" means.

What this script is NOT
------------------------
- It never writes to `schema_migrations`, never runs migration SQL, and
  never calls anything in `run_migrations.py` beyond its pure
  discovery/checksum helpers. Confirm this yourself: grep this file for
  "INSERT", "UPDATE", "DELETE", "cur.execute" — none of substance appear
  (the one `cur.execute` below is a single read-only SELECT).
- It does not decide whether an untracked file is actually applied to the
  live schema (that needs the file-by-file cross-check path (b), or
  `verify_applied_migrations.py`'s schema-introspection approach, for
  each individual file — out of scope here, same as the ACTION_ITEMS.md
  item explicitly defers it).
- It does not resolve checksum mismatches; it flags them for a human to
  investigate (append-only-rule violation vs. tooling bug are both live
  possibilities and need a person to tell them apart).

Three buckets
-------------
1. tracked_match     — repo file present in schema_migrations, checksums equal.
2. tracked_mismatch  — repo file present in schema_migrations, checksums differ.
                        Red flag: either the append-only convention was
                        violated after this file was applied, or there's a
                        bug in how one of the two checksums got computed/stored.
3. untracked         — repo file has no schema_migrations row at all.

Getting the live tracked rows
------------------------------
Same DATABASE_URL/TEST_DATABASE_URL convention as
`backend/tests/rls/conftest.py`: reads `TEST_DATABASE_URL`, falling back to
`DATABASE_URL`, and skips (does not fail, does not fabricate) if neither is
set or the connection fails. `--tracked-json <path>` is an alternate input
path for when the live rows were fetched through some other read-only
channel (e.g. the Supabase MCP `execute_sql` tool in an agent session that
has no direct Postgres network path) — it expects a JSON array of
{"filename": ..., "checksum": ..., "applied_at": ..., "applied_by": ...}
objects, exactly the shape `SELECT filename, checksum, applied_at,
applied_by FROM schema_migrations` returns.

Usage:
    # Live DB, if DATABASE_URL/TEST_DATABASE_URL is set:
    python -m backend.scripts.audit_migration_drift

    # Or feed in rows fetched some other read-only way:
    python -m backend.scripts.audit_migration_drift --tracked-json rows.json

    # Machine-readable output:
    python -m backend.scripts.audit_migration_drift --tracked-json rows.json --format json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

try:
    from .run_migrations import _checksum as checksum_of
    from .run_migrations import _discover_migrations as discover_migration_files
except ImportError:  # pragma: no cover - dual import pattern per repo convention
    from run_migrations import _checksum as checksum_of
    from run_migrations import _discover_migrations as discover_migration_files

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@dataclass
class DriftReport:
    tracked_match: List[str] = field(default_factory=list)
    tracked_mismatch: List[Dict[str, str]] = field(default_factory=list)  # {filename, recorded, current}
    untracked: List[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.tracked_match) + len(self.tracked_mismatch) + len(self.untracked)

    def to_dict(self) -> dict:
        return {
            "total_repo_files": self.total,
            "tracked_match_count": len(self.tracked_match),
            "tracked_mismatch_count": len(self.tracked_mismatch),
            "untracked_count": len(self.untracked),
            "tracked_match": self.tracked_match,
            "tracked_mismatch": self.tracked_mismatch,
            "untracked": self.untracked,
        }


def build_report(repo_files: List[Path], tracked: Dict[str, str]) -> DriftReport:
    """Pure diff logic — no I/O. `tracked` is {filename: checksum}.

    Kept side-effect-free and separate from any DB/file access so it's
    trivially unit-testable with a hand-built `tracked` dict.
    """
    report = DriftReport()
    for path in repo_files:
        name = path.name
        current = checksum_of(path)
        if name not in tracked:
            report.untracked.append(name)
            continue
        recorded = tracked[name]
        if recorded == current:
            report.tracked_match.append(name)
        else:
            report.tracked_mismatch.append({"filename": name, "recorded": recorded, "current": current})
    return report


# ---------------------------------------------------------------------------
# Live-row sourcing (read-only)
# ---------------------------------------------------------------------------


def _fetch_tracked_from_db() -> Optional[Dict[str, str]]:
    """Read-only SELECT against schema_migrations via TEST_DATABASE_URL or
    DATABASE_URL (same convention as backend/tests/rls/conftest.py).

    Returns None (never raises) if no connection string is configured or the
    connection fails — the caller decides how to report that; this function
    never fabricates data. Only ever issues a single SELECT.
    """
    dsn = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None

    try:
        import psycopg  # type: ignore

        conn = psycopg.connect(dsn, autocommit=True)
    except ImportError:
        try:
            import psycopg2  # type: ignore

            conn = psycopg2.connect(dsn)
            conn.autocommit = True
        except ImportError:
            print(
                "WARNING: neither psycopg nor psycopg2 installed; cannot query live DB. Pass --tracked-json instead.",
                file=sys.stderr,
            )
            return None
    except Exception as exc:  # noqa: BLE001 - report, never mask, connection failures
        print(f"WARNING: could not connect to schema_migrations DB: {exc}", file=sys.stderr)
        return None

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT filename, checksum FROM schema_migrations")
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001 - report, never mask, query failures
        print(f"WARNING: SELECT against schema_migrations failed: {exc}", file=sys.stderr)
        return None
    finally:
        conn.close()


def _load_tracked_from_json(path: Path) -> Dict[str, str]:
    rows = json.loads(path.read_text())
    return {row["filename"]: row["checksum"] for row in rows}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_text_report(report: DriftReport) -> None:
    print(f"Repo migration files: {report.total}")
    print(f"  tracked, checksum matches:    {len(report.tracked_match)}")
    print(f"  tracked, checksum MISMATCH:   {len(report.tracked_mismatch)}")
    print(f"  untracked (no DB row at all): {len(report.untracked)}")

    if report.tracked_mismatch:
        print("\nCHECKSUM MISMATCHES (highest priority — investigate each):")
        for row in report.tracked_mismatch:
            print(f"  - {row['filename']}: recorded={row['recorded'][:12]}… current={row['current'][:12]}…")

    if report.untracked:
        print(f"\nUNTRACKED files ({len(report.untracked)}):")
        for name in report.untracked:
            print(f"  - {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tracked-json",
        type=Path,
        help="Path to a JSON array of {filename, checksum, ...} rows fetched via some other "
        "read-only channel (e.g. Supabase MCP execute_sql), used instead of a direct DB connection.",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    repo_files = discover_migration_files()

    if args.tracked_json:
        tracked = _load_tracked_from_json(args.tracked_json)
    else:
        tracked = _fetch_tracked_from_db()
        if tracked is None:
            print(
                "SKIPPED: no live schema_migrations data available — set TEST_DATABASE_URL or "
                "DATABASE_URL, or pass --tracked-json. Not fabricating a report.",
                file=sys.stderr,
            )
            return 0

    report = build_report(repo_files, tracked)

    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_text_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
