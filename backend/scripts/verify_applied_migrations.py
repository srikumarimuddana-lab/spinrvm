#!/usr/bin/env python3
"""Decide which migration files are ALREADY applied, by inspecting the schema.

Why this exists
---------------
`public.schema_migrations` was found empty on a live database with 391
migration files on disk. Something had clearly been applied — the app runs —
but nothing was recorded, so neither runner could tell what was pending.

Backfilling that table is a one-way decision with real consequences:

  * Mark a migration applied that ISN'T  → it never runs. Its schema change is
    missing forever, and nothing will ever flag it.
  * Mark one NOT applied that IS         → it re-runs. Usually harmless (most
    files here are IF NOT EXISTS) but not universally.

"The app works, so everything must be applied" is NOT sound reasoning, and this
codebase has a concrete counter-example: migration 313 added 24 settings columns
whose absence broke nothing visible, because the settings that needed them only
500 when an admin actually changes one. A missing migration can sit silent for
months.

So this script does not assume. It parses each migration for the schema objects
it creates and checks whether each exists live. A file counts as applied only
when EVERY object it declares is present.

What it can and cannot see
--------------------------
Detected: CREATE TABLE, ALTER TABLE ... ADD COLUMN, CREATE INDEX,
CREATE FUNCTION, CREATE VIEW.

NOT detected: data-only migrations (INSERT/UPDATE/backfills), DROP statements,
policy/grant/trigger changes, constraint-only changes, and anything inside a
DO $$ block. Those files come back UNKNOWN rather than being guessed at — they
are reported for a human to decide, never silently backfilled.

Usage:
    python scripts/verify_applied_migrations.py --schema schema.json
    python scripts/verify_applied_migrations.py --schema schema.json --emit-sql
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _strip_sql_comments(sql: str) -> str:
    """Remove -- line comments and /* */ blocks.

    Migration headers here routinely contain rollback instructions with DROP /
    CREATE statements. Parsing those as real DDL would invent objects the file
    never creates and mark it unapplied forever.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def _unquote(ident: str) -> str:
    ident = ident.strip().strip(";").strip()
    if ident.startswith("public."):
        ident = ident[len("public.") :]
    return ident.strip('"').strip().lower()


def declared_objects(sql: str) -> Dict[str, Set[str]]:
    """Extract the objects a migration creates."""
    code = _strip_sql_comments(sql)
    objects: Dict[str, Set[str]] = {
        "tables": set(),
        "columns": set(),
        "indexes": set(),
        "functions": set(),
        "views": set(),
    }

    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\".]+)", code, re.IGNORECASE):
        objects["tables"].add(_unquote(m.group(1)))

    for m in re.finditer(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?([\w\".]+)",
        code,
        re.IGNORECASE,
    ):
        objects["indexes"].add(_unquote(m.group(1)))

    for m in re.finditer(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([\w\".]+)\s*\(", code, re.IGNORECASE):
        objects["functions"].add(_unquote(m.group(1)))

    for m in re.finditer(
        r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\".]+)", code, re.IGNORECASE
    ):
        objects["views"].add(_unquote(m.group(1)))

    # ALTER TABLE <t> ... ADD COLUMN [IF NOT EXISTS] <c>, ADD COLUMN <c2>, ...
    # One statement can add many columns, so scan each ALTER's body.
    for stmt in re.split(r";", code):
        alter = re.search(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w\".]+)", stmt, re.IGNORECASE)
        if not alter:
            continue
        table = _unquote(alter.group(1))
        for col in re.finditer(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\"]+)", stmt, re.IGNORECASE):
            objects["columns"].add(f"{table}.{_unquote(col.group(1))}")

    return objects


def classify(objects: Dict[str, Set[str]], live: Dict[str, Set[str]]) -> Tuple[str, List[str]]:
    """APPLIED / MISSING / UNKNOWN plus the specific objects not found."""
    total = sum(len(v) for v in objects.values())
    if total == 0:
        # Data-only, DROP-only, policy-only, or DO-block-only. Not guessable.
        return "UNKNOWN", []

    missing: List[str] = []
    for kind, names in objects.items():
        for name in names:
            if name not in live[kind]:
                missing.append(f"{kind[:-1]}:{name}")

    return ("APPLIED", []) if not missing else ("MISSING", sorted(missing))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True, help="JSON schema inventory")
    ap.add_argument("--emit-sql", action="store_true", help="Print backfill SQL for APPLIED files")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    raw = json.loads(Path(args.schema).read_text())
    live = {
        k: {str(x).lower() for x in (raw.get(k) or [])} for k in ("tables", "columns", "indexes", "functions", "views")
    }

    results: Dict[str, List[Tuple[str, List[str]]]] = {"APPLIED": [], "MISSING": [], "UNKNOWN": []}
    checksums: Dict[str, str] = {}

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8", errors="ignore")
        # MUST match run_migrations.py::_checksum exactly — raw bytes, not
        # decoded-then-re-encoded text. `errors="ignore"` above can silently
        # drop a byte, and a checksum computed that way would make the runner
        # treat every backfilled file as edited-since-applied and refuse to run.
        checksums[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        verdict, missing = classify(declared_objects(sql), live)
        results[verdict].append((path.name, missing))

    total = sum(len(v) for v in results.values())
    print(f"{total} migration files\n")
    for verdict in ("APPLIED", "MISSING", "UNKNOWN"):
        print(f"  {verdict:8} {len(results[verdict])}")

    if results["MISSING"]:
        print("\nMISSING — declared objects not found in the live schema.")
        print("These are the ones that matter: do NOT backfill them without a decision.\n")
        for name, missing in results["MISSING"]:
            shown = ", ".join(missing[:4]) + (" …" if len(missing) > 4 else "")
            print(f"  {name}\n      {shown}")

    if args.verbose and results["UNKNOWN"]:
        print("\nUNKNOWN — no detectable schema object (data-only, DROP-only, policy-only):")
        for name, _ in results["UNKNOWN"]:
            print(f"  {name}")

    if args.emit_sql:
        print("\n-- Backfill: only files whose every declared object exists live.")
        print("INSERT INTO public.schema_migrations (filename, checksum, applied_by) VALUES")
        rows = [f"  ('{n}', '{checksums[n]}', 'backfill-verified')" for n, _ in results["APPLIED"]]
        print(",\n".join(rows))
        print("ON CONFLICT (filename) DO NOTHING;")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
