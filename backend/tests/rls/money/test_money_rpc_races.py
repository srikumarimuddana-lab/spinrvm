"""Real-Postgres races against the shipped corporate wallet RPCs."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import psycopg2


def _seed_wallet(cur, balance="100.00"):
    company_id, wallet_id = str(uuid4()), str(uuid4())
    cur.execute("INSERT INTO corporate_accounts (id, name) VALUES (%s, 'PG money test')", (company_id,))
    cur.execute(
        "INSERT INTO corporate_wallets (id, company_id, balance) VALUES (%s, %s, %s)",
        (wallet_id, company_id, balance),
    )
    return wallet_id


def _apply_wallet_debit(dsn, barrier, wallet_id, ride_id):
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5000ms'")
            barrier.wait(timeout=5)
            cur.execute(
                """SELECT balance_after, deduped FROM corporate_wallet_apply_delta(
                       p_wallet_id => %s::uuid, p_scope => 'master', p_type => 'ride_debit',
                       p_delta => -20.00, p_ride_id => %s::uuid, p_floor => 0.00,
                       p_client_idempotency_key => NULL::text)""",
                (wallet_id, ride_id),
            )
            return cur.fetchone()
    finally:
        conn.close()


def test_concurrent_same_wallet_ride_debits_preserve_balance_and_ledger(money_pg_cur, pg_conn):
    wallet_id = _seed_wallet(money_pg_cur)
    dsn = pg_conn.dsn
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(
            pool.map(
                lambda _index: _apply_wallet_debit(dsn, barrier, wallet_id, str(uuid4())),
                range(2),
            )
        )

    assert set(rows) == {(Decimal("80.00"), False), (Decimal("60.00"), False)}
    money_pg_cur.execute("SELECT balance FROM corporate_wallets WHERE id = %s", (wallet_id,))
    assert money_pg_cur.fetchone()[0] == Decimal("60.00")
    money_pg_cur.execute(
        "SELECT count(*), sum(amount), array_agg(balance_after) "
        "FROM corporate_wallet_transactions WHERE wallet_id = %s AND type = 'ride_debit'",
        (wallet_id,),
    )
    count, total, balances = money_pg_cur.fetchone()
    assert (count, total) == (2, Decimal("-40.00"))
    assert set(balances) == {Decimal("80.00"), Decimal("60.00")}


def _apply_allowance_debit(dsn, barrier, wallet_id, allowance_id, member_id, ride_id):
    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET statement_timeout = '5000ms'")
            barrier.wait(timeout=5)
            try:
                cur.execute(
                    """SELECT master_balance_after, allowance_used_after, deduped
                       FROM corporate_allowance_apply_delta(
                           p_wallet_id => %s::uuid, p_allowance_id => %s::uuid,
                           p_member_id => %s::uuid, p_type => 'ride_debit',
                           p_amount => 60.00, p_ride_id => %s::uuid, p_floor => 0.00)""",
                    (wallet_id, allowance_id, member_id, ride_id),
                )
                return ("applied", cur.fetchone())
            except psycopg2.Error as exc:
                conn.rollback()
                if "allowance_cap_exceeded" not in str(exc):
                    raise
                return ("cap", None)
    finally:
        conn.close()


def _seed_member_allowance(cur, wallet_id, cap="100.00"):
    cur.execute("SELECT company_id FROM corporate_wallets WHERE id = %s", (wallet_id,))
    company_id = str(cur.fetchone()[0])
    member_id, allowance_id = str(uuid4()), str(uuid4())
    cur.execute(
        "INSERT INTO corporate_members (id, company_id, status) VALUES (%s, %s, 'active')",
        (member_id, company_id),
    )
    cur.execute(
        """INSERT INTO corporate_member_allowances (id, member_id, type, amount, used)
           VALUES (%s, %s, 'fixed_recurring', %s, 0.00)""",
        (allowance_id, member_id, cap),
    )
    return member_id, allowance_id


def test_concurrent_allowance_debits_enforce_cap_and_keep_paired_ledger(money_pg_cur, pg_conn):
    wallet_id = _seed_wallet(money_pg_cur)
    member_id, allowance_id = _seed_member_allowance(money_pg_cur, wallet_id)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _index: _apply_allowance_debit(
                    pg_conn.dsn, barrier, wallet_id, allowance_id, member_id, str(uuid4())
                ),
                range(2),
            )
        )

    assert [result[0] for result in results].count("applied") == 1
    assert [result[0] for result in results].count("cap") == 1
    money_pg_cur.execute("SELECT balance FROM corporate_wallets WHERE id = %s", (wallet_id,))
    assert money_pg_cur.fetchone()[0] == Decimal("40.00")
    money_pg_cur.execute("SELECT used FROM corporate_member_allowances WHERE id = %s", (allowance_id,))
    assert money_pg_cur.fetchone()[0] == Decimal("60.00")
    money_pg_cur.execute(
        "SELECT scope, amount, balance_after FROM corporate_wallet_transactions WHERE wallet_id = %s ORDER BY scope",
        (wallet_id,),
    )
    assert set(money_pg_cur.fetchall()) == {
        (f"member:{member_id}", Decimal("60.00"), Decimal("60.00")),
        ("master", Decimal("-60.00"), Decimal("40.00")),
    }


def test_concurrent_same_ride_payment_replay_dedupes_under_wallet_lock(money_pg_cur, pg_conn):
    wallet_id = _seed_wallet(money_pg_cur)
    ride_id = str(uuid4())
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(
            pool.map(
                lambda _index: _apply_wallet_debit(pg_conn.dsn, barrier, wallet_id, ride_id),
                range(2),
            )
        )

    assert sorted(deduped for _balance, deduped in rows) == [False, True]
    money_pg_cur.execute("SELECT balance FROM corporate_wallets WHERE id = %s", (wallet_id,))
    assert money_pg_cur.fetchone()[0] == Decimal("80.00")
    money_pg_cur.execute(
        "SELECT count(*), sum(amount) FROM corporate_wallet_transactions "
        "WHERE wallet_id = %s AND ride_id = %s AND type = 'ride_debit'",
        (wallet_id, ride_id),
    )
    assert money_pg_cur.fetchone() == (1, Decimal("-20.00"))
