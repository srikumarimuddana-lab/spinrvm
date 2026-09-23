"""Opt-in PostgreSQL setup for corporate money and webhook DB-boundary tests.

The parent RLS fixture supplies the disposable DB, roles, auth shim, and the
real migrations 22/05/27. Apply only the later RPC definitions needed here so
other RLS tests keep their existing fixture state.
"""

from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"


@pytest.fixture(scope="session")
def money_rpc_schema(pg_conn):
    """Install the shipped wallet tables and current corporate RPC bodies."""
    with pg_conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY)")
        for name in (
            "19_wallet.sql",
            "214_corporate_actor_user_id_text.sql",
            "297_corporate_rpc_ride_idempotency.sql",
            "319_late_tip_debit_types.sql",
            "376_corporate_wallet_adjust_idempotency.sql",
        ):
            cur.execute((_MIGRATIONS / name).read_text())
    return pg_conn


@pytest.fixture
def money_pg_cur(pg_conn, money_rpc_schema):
    """Reset only the rows these opt-in tests own."""
    with pg_conn.cursor() as cur:
        cur.execute("RESET ROLE")
        cur.execute(
            "TRUNCATE corporate_wallet_transactions, corporate_member_allowances, "
            "corporate_members, corporate_wallets, corporate_accounts, stripe_events CASCADE"
        )
    cur = pg_conn.cursor()
    yield cur
    cur.close()
