"""A migration containing CONCURRENTLY is split into statements by the runner.

`scripts/migrate.py::apply_migration` routes a file to
`_apply_migration_autocommit` when CONCURRENTLY appears in one of its actual
(comment-free) statements, which cannot use a transaction and so must execute
each statement individually.

Fixed (ACTION_ITEMS.md B0): the splitter used to be a naive `sql.split(";")`
with a leading-`--`-line strip per chunk, which had two failure modes — a
mid-line semicolon inside a prose comment split inside the comment and handed
the trailing prose to Postgres as SQL, and semicolons inside a `$$`-quoted
PL/pgSQL function body shredded the body into fragments. `_split_sql_statements`
(in `scripts/migrate.py`) is now a real lexical scan that tracks `--`/`/* */`
comments, `'...'` string literals (with `''` escaping), and
`$tag$...$tag$` dollar-quoted bodies, so top-level semicolons are the only
split points and comment text never leaks into the executable output. This
test now exercises that real function (not a reimplementation) against every
CONCURRENTLY-mentioning migration in the repo, and the previously-frozen
allowlist of known-broken files is empty — every one of them now splits
cleanly.
"""

from __future__ import annotations

import pathlib

import pytest

from backend.scripts.migrate import _split_sql_statements

_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "migrations"

# Every statement keyword the runner can legitimately be handed. A chunk
# starting with anything else is prose or a function-body fragment.
_SQL_STARTS = (
    "ALTER",
    "COMMENT",
    "CREATE",
    "DO",
    "DROP",
    "GRANT",
    "INSERT",
    "NOTIFY",
    "REVOKE",
    "SELECT",
    "SET",
    "UPDATE",
    "WITH",
)

# Kept empty deliberately (rather than deleted) so a regression that breaks
# the splitter again has somewhere obvious to record known-bad files instead
# of silently disabling this test file's coverage. Every migration in the
# repo splits cleanly with the current `_split_sql_statements` — do not add
# an entry here without first trying to fix the splitter itself; a real
# unsplittable migration should be reworded (drop the stray "CONCURRENTLY"
# from a comment, or restructure the function body) instead.
_KNOWN_UNSPLITTABLE: frozenset[str] = frozenset()


def _concurrently_migrations() -> list[pathlib.Path]:
    return sorted(p for p in _MIGRATIONS.glob("*.sql") if "CONCURRENTLY" in p.read_text().upper())


def _checked() -> list[pathlib.Path]:
    return [p for p in _concurrently_migrations() if p.name not in _KNOWN_UNSPLITTABLE]


def test_there_is_something_to_check():
    """Guard the guard — a glob that silently matched nothing, or an allowlist
    that swallowed every file, would make the parametrized cases vacuous."""
    assert _checked()


def test_allowlist_has_no_stale_entries():
    """Every frozen name must still exist and still contain CONCURRENTLY. A
    stale entry would silently exempt nothing — or worse, mask a file that was
    renamed rather than fixed."""
    names = {p.name for p in _concurrently_migrations()}
    assert _KNOWN_UNSPLITTABLE <= names, f"stale allowlist entries: {sorted(_KNOWN_UNSPLITTABLE - names)}"


@pytest.mark.parametrize("path", _checked(), ids=lambda p: p.name)
def test_no_prose_or_body_fragment_leaks_out(path: pathlib.Path):
    for stmt in _split_sql_statements(path.read_text()):
        first_word = stmt.split(None, 1)[0].upper().lstrip("(")
        assert first_word.startswith(_SQL_STARTS), (
            f"{path.name}: scripts/migrate.py would hand this to Postgres as a statement:\n\n"
            f"{stmt[:300]}\n\n"
            "The real splitter should never produce this — if it does, "
            "_split_sql_statements has a new lexical gap, not the migration."
        )


# ── Direct regression pins for the two originally-documented failure modes ──


def test_mid_line_semicolon_in_comment_does_not_split():
    sql = (
        "-- keep this hot table; a plain build blocks writes, so we use CONCURRENTLY\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_x ON drivers (id);\n"
    )
    stmts = _split_sql_statements(sql)
    assert stmts == ["CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_x ON drivers (id)"]


def test_dollar_quoted_function_body_is_not_shredded():
    sql = (
        "CREATE OR REPLACE FUNCTION example_fn() RETURNS void AS $$\n"
        "BEGIN\n"
        "    UPDATE drivers SET x = 1;\n"
        "    UPDATE drivers SET y = 2;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "-- CONCURRENTLY mentioned only in this rollback comment, not executable SQL\n"
    )
    stmts = _split_sql_statements(sql)
    assert len(stmts) == 1
    assert stmts[0].startswith("CREATE OR REPLACE FUNCTION")
    assert stmts[0].rstrip().endswith("LANGUAGE plpgsql")


def test_comment_only_concurrently_does_not_force_autocommit_routing():
    """A file whose only CONCURRENTLY mention is inside a dropped comment must
    route through the normal transactional path, not the no-transaction
    autocommit path — matching apply_migration's needs_autocommit check."""
    sql = (
        "-- Rollback: DROP INDEX CONCURRENTLY IF EXISTS idx_x;\n"
        "CREATE OR REPLACE FUNCTION example_fn() RETURNS void AS $$\n"
        "BEGIN\n"
        "    UPDATE drivers SET x = 1;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
    )
    stmts = _split_sql_statements(sql)
    needs_autocommit = any("CONCURRENTLY" in s.upper() for s in stmts)
    assert needs_autocommit is False
