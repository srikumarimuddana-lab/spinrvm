"""migrate.py autocommit path — comment-prefixed chunks must still execute.

Codex PR #1724 round 4 (P2): the autocommit runner split on semicolons and
skipped any chunk whose stripped text started with `--`. Every migration
opens with the conventional `--` header/rollback comment block, so the chunk
containing the leading comments PLUS the CREATE INDEX CONCURRENTLY was
silently thrown away — the index was never created and (when a COMMENT ON
followed) the migration failed. Pins the fixed behavior: leading comment
lines are stripped per chunk, the executable remainder runs, and
comment-only chunks are still skipped.

Run:
    pytest backend/tests/test_migrate_autocommit_chunks.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock

from backend.scripts.migrate import _apply_migration_autocommit, _split_sql_statements

MIGRATION_138_STYLE = """-- 999_example_concurrent_index.sql
-- Rationale comment block, several lines long.
-- Rollback:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_example;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_example
    ON drivers (went_online_at DESC)
    WHERE is_online = TRUE;

-- trailing comment-only chunk should still be skipped

COMMENT ON INDEX idx_example IS 'example';
"""


def _conn_capturing(executed: list):
    cur = MagicMock()
    cur.execute = MagicMock(side_effect=lambda sql, *a: executed.append(sql))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cur)
    cm.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cm)
    return conn


def test_comment_prefixed_create_index_chunk_executes():
    executed: list = []
    ok = _apply_migration_autocommit(
        _conn_capturing(executed), "999_example.sql", _split_sql_statements(MIGRATION_138_STYLE)
    )
    assert ok is True
    create_stmts = [s for s in executed if "CREATE INDEX CONCURRENTLY" in s]
    assert create_stmts, "the comment-prefixed CREATE INDEX chunk must execute, not be skipped"
    assert not create_stmts[0].lstrip().startswith("--"), "leading comment lines are stripped before execution"
    assert any("COMMENT ON INDEX" in s for s in executed)
    assert any("schema_migrations" in s for s in executed)


def test_comment_only_chunks_are_still_skipped():
    executed: list = []
    ok = _apply_migration_autocommit(
        _conn_capturing(executed),
        "998_comments_only.sql",
        _split_sql_statements(
            "-- only a comment, no statement\n-- another line;\n-- CONCURRENTLY mentioned to route here;"
        ),
    )
    assert ok is True
    assert all("schema_migrations" in s for s in executed), "nothing but the version INSERT should run"
