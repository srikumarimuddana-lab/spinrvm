"""An inline FK column must be declared with the type of the PK it references.

Regression: migration 281 declared

    company_id TEXT NOT NULL REFERENCES public.corporate_accounts(id)

while `corporate_accounts.id` is UUID (migration 05). Postgres refuses to
build the constraint at all --

    42804: foreign key constraint "corporate_subscriptions_company_id_fkey"
    cannot be implemented
    DETAIL: Key columns "company_id" and "id" are of incompatible types:
    text and uuid.

-- so the whole CREATE TABLE, and with it the entire migration, failed on
every environment. Nothing caught it before an operator ran it against a
real database, because no test reads the SQL and no CI job applies the
migrations to a throwaway Postgres.

This is a static check over the migration text: it cannot catch every
mismatch (FKs added via a later ALTER TABLE, or a column whose type is
changed after creation, are out of scope), but it does catch the inline
`col TYPE ... REFERENCES table(id)` shape that every corporate/driver table
in this repo uses.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_migrations import _discover_migrations  # noqa: E402

pytestmark = pytest.mark.unit

# Only types we can compare unambiguously. Anything else (NUMERIC(10,2),
# TIMESTAMPTZ, …) is not a plausible FK target here and is skipped rather
# than guessed at.
_COMPARABLE = {"UUID", "TEXT", "BIGINT", "INTEGER", "INT", "SERIAL", "BIGSERIAL"}

# SERIAL/BIGSERIAL are integer columns with a sequence default; an FK to one
# is declared INTEGER/BIGINT, so normalize before comparing.
_NORMALIZE = {"SERIAL": "INTEGER", "BIGSERIAL": "BIGINT", "INT": "INTEGER"}

_PK_DECL = re.compile(
    r"^\s*id\s+(?P<type>\w+)\b[^,]*\bPRIMARY\s+KEY",
    re.IGNORECASE | re.MULTILINE,
)
_CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:public\.)?(?P<table>\w+)\s*\((?P<body>.*?)\n\s*\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_FK = re.compile(
    r"^\s*(?P<col>\w+)\s+(?P<type>\w+)\b[^,]*?\bREFERENCES\s+(?:public\.)?(?P<ref_table>\w+)\s*\(\s*(?P<ref_col>\w+)\s*\)",
    re.IGNORECASE | re.MULTILINE,
)


def _normalize(sql_type: str) -> str:
    upper = sql_type.upper()
    return _NORMALIZE.get(upper, upper)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _migration_sources() -> list[tuple[str, str]]:
    return [(p.name, _strip_comments(p.read_text())) for p in _discover_migrations()]


def _primary_key_types(sources: list[tuple[str, str]]) -> dict[str, str]:
    """Map table -> `id` column type, for tables with an unambiguous PK type.

    A table created more than once across migrations (05 and 08 both create
    `corporate_accounts`) is only usable if every definition agrees; a table
    with conflicting definitions is dropped from the map rather than making
    the test assert against an arbitrary one.
    """
    seen: dict[str, set[str]] = defaultdict(set)
    for _name, sql in sources:
        for table_match in _CREATE_TABLE.finditer(sql):
            pk = _PK_DECL.search(table_match.group("body"))
            if not pk:
                continue
            pk_type = _normalize(pk.group("type"))
            if pk_type in _COMPARABLE:
                seen[table_match.group("table").lower()].add(pk_type)
    return {table: next(iter(types)) for table, types in seen.items() if len(types) == 1}


def test_inline_foreign_keys_match_referenced_primary_key_type():
    sources = _migration_sources()
    pk_types = _primary_key_types(sources)
    # Sanity-check the parser itself: if this stops resolving, the test below
    # would pass vacuously.
    assert pk_types.get("corporate_accounts") == "UUID"

    mismatches = []
    for name, sql in sources:
        for fk in _INLINE_FK.finditer(sql):
            if fk.group("ref_col").lower() != "id":
                continue
            expected = pk_types.get(fk.group("ref_table").lower())
            actual = _normalize(fk.group("type"))
            if expected is None or actual not in _COMPARABLE:
                continue
            if actual != expected:
                mismatches.append(
                    f"{name}: {fk.group('col')} {actual} REFERENCES {fk.group('ref_table')}(id) which is {expected}"
                )

    assert not mismatches, "FK column type does not match referenced PK:\n" + "\n".join(mismatches)


def test_corporate_subscriptions_company_id_is_uuid():
    """The specific regression, asserted directly."""
    sql = next(sql for name, sql in _migration_sources() if name == "281_corporate_subscriptions.sql")
    decl = re.search(r"^\s*company_id\s+(\w+)", sql, re.IGNORECASE | re.MULTILINE)
    assert decl is not None
    assert decl.group(1).upper() == "UUID"
