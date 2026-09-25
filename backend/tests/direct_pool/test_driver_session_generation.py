"""Exercise driver-login revocation against the shipped SQL on disposable Postgres."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture()
def session_db(pg_cur):
    migrations = Path(__file__).resolve().parents[2] / "migrations"
    pg_cur.execute("CREATE TABLE IF NOT EXISTS admin_staff (id text PRIMARY KEY)")
    for name in ("25_refresh_tokens_and_token_version.sql", "441_refresh_token_revocation_reason.sql"):
        pg_cur.execute((migrations / name).read_text(encoding="utf-8"))
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id text")
    pg_cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_invalid_before timestamptz")
    pg_cur.execute("TRUNCATE refresh_tokens")
    pg_cur.execute("INSERT INTO users (id, phone) VALUES ('login-user', '+13060000001')")
    pg_cur.execute("UPDATE users SET token_version=4 WHERE id='login-user'")
    pg_cur.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, audience, expires_at) "
        "VALUES ('login-user', 'mobile', 'rider', now()+interval '1 day'), "
        "('login-user', 'admin', 'admin', now()+interval '1 day')"
    )
    try:
        from backend.scripts.run_migrations import _split_sql_statements
    except ImportError:  # pragma: no cover - import style varies by entrypoint
        import sys as _sys

        _sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from backend.scripts.run_migrations import _split_sql_statements

    for statement in _split_sql_statements(
        (migrations / "452_driver_session_generation.sql").read_text(encoding="utf-8")
    ):
        pg_cur.execute(statement)
    pg_cur.execute("UPDATE settings SET driver_single_session_enabled=false WHERE id='app_settings'")
    return pg_cur


def test_disabled_rollout_does_not_displace_sessions(session_db):
    session_db.execute("SELECT begin_driver_session('login-user', 'next-session')")
    assert session_db.fetchone()[0] == {"enabled": False}
    session_db.execute("SELECT token_version FROM users WHERE id='login-user'")
    assert session_db.fetchone()[0] == 4
    session_db.execute("SELECT revoked_at FROM refresh_tokens WHERE token_hash='mobile'")
    assert session_db.fetchone()[0] is None


def test_login_revokes_mobile_generation_without_touching_admin(session_db):
    session_db.execute("UPDATE settings SET driver_single_session_enabled=true WHERE id='app_settings'")
    session_db.execute("SELECT begin_driver_session('login-user', 'next-session')")
    result = session_db.fetchone()[0]
    assert result["token_version"] == 5
    session_db.execute("SELECT current_session_id, token_version FROM users WHERE id='login-user'")
    assert session_db.fetchone() == ("next-session", 5)
    session_db.execute(
        "SELECT token_version, revoked_at, revocation_reason FROM refresh_tokens WHERE token_hash='mobile'"
    )
    version, revoked_at, reason = session_db.fetchone()
    assert version == 4 and revoked_at is not None and reason == "session_superseded"
    session_db.execute("SELECT revoked_at FROM refresh_tokens WHERE token_hash='admin'")
    assert session_db.fetchone()[0] is None
    session_db.execute("SELECT begin_driver_session('login-user', 'later-session')")
    assert session_db.fetchone()[0]["token_version"] == 6


def test_login_rpc_is_not_executable_by_clients(session_db):
    session_db.execute("SELECT has_function_privilege('authenticated', 'begin_driver_session(text,text)', 'EXECUTE')")
    assert session_db.fetchone()[0] is False
    session_db.execute("SELECT has_function_privilege('service_role', 'begin_driver_session(text,text)', 'EXECUTE')")
    assert session_db.fetchone()[0] is True


def test_concurrent_driver_logins_get_distinct_generations(session_db, pg_dsn):
    import psycopg2

    session_db.execute("UPDATE settings SET driver_single_session_enabled=true WHERE id='app_settings'")

    def login(session_id):
        with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT begin_driver_session('login-user', %s)", (session_id,))
            return cur.fetchone()[0]["token_version"]

    with ThreadPoolExecutor(max_workers=2) as workers:
        versions = list(workers.map(login, ("phone-one", "phone-two")))
    assert sorted(versions) == [5, 6]
    session_db.execute("SELECT token_version, sessions_invalid_before FROM users WHERE id='login-user'")
    version, invalid_before = session_db.fetchone()
    assert version == 6 and invalid_before is not None
