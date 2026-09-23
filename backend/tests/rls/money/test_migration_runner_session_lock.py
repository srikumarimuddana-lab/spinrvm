"""Exercise migration-runner ownership with actual PostgreSQL sessions."""

import psycopg
from backend.scripts.run_migrations import _acquire_apply_lock


def test_apply_lock_is_session_owned_across_commit_and_released_on_close(pg_conn):
    first = psycopg.connect(pg_conn.dsn, autocommit=False)
    second = psycopg.connect(pg_conn.dsn, autocommit=False)
    try:
        assert _acquire_apply_lock(first) is True
        assert _acquire_apply_lock(second) is False

        # _acquire_apply_lock commits its SELECT transaction. The owner lock
        # must survive that commit because migration files commit separately.
        first.commit()
        assert _acquire_apply_lock(second) is False

        first.close()
        assert _acquire_apply_lock(second) is True
    finally:
        if not first.closed:
            first.close()
        if not second.closed:
            second.close()
