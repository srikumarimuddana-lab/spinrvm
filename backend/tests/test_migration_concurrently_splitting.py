"""Migration runner's SQL splitter must be comment/dollar-quote aware.

`scripts/migrate.py::apply_migration` used to route any file whose text
contained the string "CONCURRENTLY" — including in a comment — to
`_apply_migration_autocommit`, which cannot use a transaction and did a naive
`sql.split(";")`, executing each chunk after stripping its *leading* `--`
lines. That splitter had two failure modes:

1. **A mid-line semicolon in a prose comment.** A line-terminal one is harmless
   (the whole next chunk is still comments, strips to empty, is skipped) — but
   a mid-line one split inside the comment, and the rest of that line became
   the first line of the next chunk. It was not a comment, so the leading-
   comment strip loop stopped and the runner handed prose to Postgres as SQL.
2. **A `$$`-quoted function body.** Every semicolon inside `BEGIN … END` was a
   split point, so the body was shredded into fragments.
3. Detection itself was a raw substring search over the whole file, so a
   `CONCURRENTLY` mention inside a comment (e.g. rollback instructions) routed
   a migration with no actual concurrent-index statement into the fragile
   autocommit path at all.

Fixed in `scripts/migrate.py` via `split_sql_statements` (a comment/literal
-aware tokenizer, see `_tokenize_sql`) and `_statement_needs_autocommit`
(checks parsed statements, not raw file text). This test exercises the real
implementation — imported directly, not reimplemented — against every
migration file in `backend/migrations/`, with no allowlist: the property must
hold for all of them, including the 34 migrations that used to be exempted in
`_KNOWN_UNSPLITTABLE` (removed — see ACTION_ITEMS.md B0).
"""

from __future__ import annotations

import pathlib

import pytest

from backend.scripts.migrate import (
    _statement_needs_autocommit,
    _strip_leading_comments,
    split_sql_statements,
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parent.parent / "migrations"

# Every statement keyword the runner can legitimately be handed. A chunk
# starting with anything else is prose or a function-body fragment.
_SQL_STARTS = (
    "ALTER",
    "BEGIN",
    "COMMENT",
    "COMMIT",
    "CREATE",
    "DELETE",
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


def _all_migrations() -> list[pathlib.Path]:
    return sorted(_MIGRATIONS.glob("*.sql"))


def _concurrently_migrations() -> list[pathlib.Path]:
    """Migrations that actually contain a CONCURRENTLY statement (parsed, not
    raw substring search — a comment mentioning it does not count)."""
    out = []
    for p in _all_migrations():
        sql = p.read_text()
        if any(_statement_needs_autocommit(stmt) for stmt in split_sql_statements(sql)):
            out.append(p)
    return out


def test_there_is_something_to_check():
    """Guard the guard — a glob that silently matched nothing would make the
    parametrized cases vacuous."""
    assert _all_migrations()
    assert _concurrently_migrations()


@pytest.mark.parametrize("path", _all_migrations(), ids=lambda p: p.name)
def test_no_prose_or_body_fragment_leaks_out(path: pathlib.Path):
    """For every migration (not just ones the runner routes to autocommit),
    every top-level statement the splitter produces — after stripping leading
    comment lines, exactly as `_apply_migration_autocommit` does — must start
    with a real SQL keyword. Anything else means the splitter mis-cut the file
    and would hand Postgres a comment fragment or a broken statement."""
    sql = path.read_text()
    for stmt in split_sql_statements(sql):
        executable = _strip_leading_comments(stmt)
        if not executable:
            continue
        first_word = executable.split(None, 1)[0].upper().lstrip("(")
        assert first_word.startswith(_SQL_STARTS), (
            f"{path.name}: scripts/migrate.py would hand this to Postgres as a statement:\n\n"
            f"{executable[:300]}\n\n"
            "Usually a semicolon in the middle of a comment line, or a $$-quoted "
            "body that was shredded — both should be impossible with the "
            "comment/literal-aware splitter; if this fires, the splitter regressed."
        )


def test_mid_comment_semicolon_does_not_leak_into_next_statement():
    sql = (
        "-- Rationale: this speeds up the hot table; a plain build blocks "
        "writes so we run it concurrently, safe to remove anytime if the "
        "planner regresses.\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_example ON drivers (id);\n"
    )
    statements = split_sql_statements(sql)
    assert len(statements) == 1
    executable = _strip_leading_comments(statements[0])
    assert executable.startswith("CREATE INDEX CONCURRENTLY")
    assert "safe to remove" not in executable


def test_dollar_quoted_function_body_with_internal_semicolons_stays_intact():
    sql = (
        "-- rollback: DROP FUNCTION wallet_apply_credit(uuid, numeric);\n"
        "CREATE OR REPLACE FUNCTION wallet_apply_credit(p_wallet_id uuid, p_amount numeric)\n"
        "RETURNS void AS $$\n"
        "BEGIN\n"
        "    UPDATE wallets SET balance = balance + p_amount WHERE id = p_wallet_id;\n"
        "    INSERT INTO wallet_txns (wallet_id, amount) VALUES (p_wallet_id, p_amount);\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
    )
    statements = split_sql_statements(sql)
    assert len(statements) == 1
    executable = _strip_leading_comments(statements[0])
    assert executable.startswith("CREATE OR REPLACE FUNCTION")
    assert "UPDATE wallets SET balance" in executable
    assert "INSERT INTO wallet_txns" in executable
    assert executable.rstrip().endswith("$$ LANGUAGE plpgsql")
    # A comment mentioning a keyword like CONCURRENTLY nowhere in this body —
    # confirm detection doesn't misfire on ordinary function migrations.
    assert not _statement_needs_autocommit(statements[0])


def test_named_dollar_tag_function_body_with_semicolons_stays_intact():
    sql = (
        "CREATE FUNCTION corp_apply_delta() RETURNS trigger AS $func$\n"
        "BEGIN\n"
        "    IF NEW.amount < 0 THEN RAISE EXCEPTION 'negative; not allowed'; END IF;\n"
        "    RETURN NEW;\n"
        "END;\n"
        "$func$ LANGUAGE plpgsql;\n"
    )
    statements = split_sql_statements(sql)
    assert len(statements) == 1
    assert "RAISE EXCEPTION" in statements[0]
    assert "RETURN NEW" in statements[0]


def test_real_concurrently_index_migration_is_detected_and_splits_cleanly():
    sql = (
        "-- 999_drivers_hot_index.sql\n"
        "-- Rollback:\n"
        "--   DROP INDEX CONCURRENTLY IF EXISTS idx_drivers_hot;\n"
        "\n"
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_drivers_hot\n"
        "    ON drivers (went_online_at DESC)\n"
        "    WHERE is_online = TRUE;\n"
        "\n"
        "COMMENT ON INDEX idx_drivers_hot IS 'hot-path index';\n"
    )
    statements = split_sql_statements(sql)
    executables = [_strip_leading_comments(s) for s in statements]
    executables = [e for e in executables if e]
    assert len(executables) == 2
    assert executables[0].startswith("CREATE INDEX CONCURRENTLY")
    assert executables[1].startswith("COMMENT ON INDEX")
    assert any(_statement_needs_autocommit(s) for s in statements)


def test_concurrently_mentioned_only_in_a_comment_does_not_trigger_autocommit():
    """A migration whose only mention of CONCURRENTLY is in a rollback-plan
    comment (no actual CREATE/DROP INDEX ... CONCURRENTLY statement) must not
    be routed to the non-transactional autocommit path."""
    sql = (
        "-- Rollback: re-run the index build non-concurrently if this fails: "
        "DROP INDEX CONCURRENTLY IF EXISTS idx_foo; then CREATE INDEX idx_foo ...\n"
        "ALTER TABLE drivers ADD COLUMN foo text;\n"
    )
    statements = split_sql_statements(sql)
    assert not any(_statement_needs_autocommit(s) for s in statements)


def test_single_quoted_string_with_semicolon_is_not_a_split_point():
    sql = "INSERT INTO app_settings (key, value) VALUES ('note', 'a; b; c');\nSELECT 1;\n"
    statements = split_sql_statements(sql)
    assert len(statements) == 2
    assert statements[0].startswith("INSERT INTO app_settings")
    assert "a; b; c" in statements[0]
