#!/usr/bin/env python3
"""DEPRECATED shim — forwards to scripts/run_migrations.py.

Why this file no longer implements anything
-------------------------------------------
The repo had TWO migration runners writing to the same `schema_migrations`
table with two incompatible shapes:

  * `00_schema_migrations_table.sql` → (version TEXT PK, applied_at)
    ...which this script used: `SELECT version` / `INSERT (version)`.
  * `24_schema_migrations.sql`       → (filename PK, checksum, applied_at, applied_by)
    ...which `run_migrations.py` uses.

Both are `CREATE TABLE IF NOT EXISTS`, so whichever ran first won. In
production the `filename/checksum` shape won — meaning every query this script
made failed. And it failed *quietly*: `get_applied_versions()` caught the
error, logged a warning, and returned an empty set, i.e. "nothing has been
applied." So this runner would have re-applied all 391 migrations on every
invocation, and never recorded a single one. The tracking table was found
empty on a live database with 391 files on disk.

Beyond the column mismatch, `run_migrations.py` is simply the better runner and
the one the table was designed for:

  * wraps each migration + its provenance row in ONE transaction, so a
    half-applied migration can't be recorded as done
  * records a SHA-256 checksum and refuses to re-run a file whose contents
    changed since it was applied (migrations are append-only)
  * bootstraps the tracking table itself

Rather than fix a second, weaker implementation into existence — and give the
repo two writers of one table again — this is now a thin forwarder. Every
documented command keeps working (CLAUDE.md, docs/runbooks/*, and muscle
memory all say `migrate.py`), and there is exactly one implementation.

Prefer calling `scripts/run_migrations.py` directly in new docs and scripts.
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent / "run_migrations.py"


def main() -> int:
    if not _RUNNER.exists():
        print(
            f"error: expected the real runner at {_RUNNER}, but it is missing.\n"
            "migrate.py is a forwarding shim and has no implementation of its own.",
            file=sys.stderr,
        )
        return 1

    print(
        f"note: migrate.py is a shim; running scripts/run_migrations.py {' '.join(sys.argv[1:])}".rstrip(),
        file=sys.stderr,
    )

    # runpy rather than subprocess so the child sees this process's environment
    # (SUPABASE_URL / PG_CONNECTION_STRING / …) and its exit code and tracebacks
    # surface unchanged. sys.argv[0] is rewritten so --help prints the real
    # runner's usage, not this shim's.
    sys.argv[0] = str(_RUNNER)
    try:
        runpy.run_path(str(_RUNNER), run_name="__main__")
    except SystemExit as exc:  # the runner calls raise SystemExit(main())
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
