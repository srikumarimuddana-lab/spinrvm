"""Actual PostgreSQL refund/hold atomicity, replay and concurrency regressions."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
CALL = "SELECT * FROM public.apply_stripe_refund_cumulative(%s,%s,%s,%s,%s)"


@pytest.fixture(scope="module")
def refund_conn(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("""ALTER TABLE rides
            ADD COLUMN IF NOT EXISTS refund_amount numeric DEFAULT 0,
            ADD COLUMN IF NOT EXISTS grand_total numeric,
            ADD COLUMN IF NOT EXISTS tax_amount numeric DEFAULT 0,
            ADD COLUMN IF NOT EXISTS tax_breakdown jsonb,
            ADD COLUMN IF NOT EXISTS cancel_fee_payment_intent_id text;
            ALTER TABLE rides ALTER COLUMN total_fare TYPE numeric,
                ALTER COLUMN driver_earnings TYPE numeric;
            CREATE TABLE payouts (id text PRIMARY KEY, driver_id text REFERENCES drivers(id),
                amount numeric NOT NULL, status text, payout_type text, bank_name text,
                failure_reason text, created_at timestamptz DEFAULT now());
            ALTER TABLE settings ADD COLUMN IF NOT EXISTS driver_refund_holds_enabled boolean DEFAULT false;""")
        cur.execute((MIGRATIONS / "58_financial_events.sql").read_text(encoding="utf-8"))
        cur.execute((MIGRATIONS / "446_atomic_stripe_refund_cumulative.sql").read_text(encoding="utf-8"))
        fix = MIGRATIONS / "451_atomic_driver_refund_holds.sql"
        if fix.exists():
            from backend.scripts.run_migrations import _split_sql_statements

            for statement in _split_sql_statements(fix.read_text(encoding="utf-8")):
                cur.execute(statement)
    yield pg_conn
    with pg_conn.cursor() as cur:
        cur.execute("DROP TABLE payouts CASCADE; DROP TABLE financial_events CASCADE;")


@pytest.fixture
def refund_cur(refund_conn):
    cur = refund_conn.cursor()
    cur.execute("TRUNCATE financial_events, payouts, rides, drivers, users CASCADE")
    cur.execute("UPDATE settings SET driver_refund_holds_enabled=true WHERE id='app_settings'")
    cur.execute("INSERT INTO users(id,phone) VALUES('rider','rider'),('user-driver','driver')")
    cur.execute("INSERT INTO drivers(id,user_id,name,phone) VALUES('driver','user-driver','Driver','driver')")
    cur.execute("""INSERT INTO rides(id,rider_id,driver_id,pickup_address,pickup_lat,pickup_lng,
        dropoff_address,dropoff_lat,dropoff_lng,status,payment_intent_id,payment_status,
        driver_earnings,total_fare,grand_total,ride_completed_at)
        VALUES('ride','rider','driver','a',1,1,'b',2,2,'completed','pi','paid',20,25,27.75,now()-interval '2 days');
        INSERT INTO payouts(id,driver_id,amount,status,payout_type,created_at)
        VALUES('auto','driver',20,'completed','auto',now()-interval '1 day');""")
    yield cur
    cur.close()


def project(cur, cents):
    cur.execute(CALL, ("ride", "pi", cents, 2775, str(uuid4())))
    return cur.fetchone()


def held(cur):
    cur.execute("SELECT COALESCE(sum(amount),0) FROM payouts WHERE payout_type='clawback'")
    return cur.fetchone()[0]


def test_partial_refunds_and_replay_cannot_exceed_driver_earnings(refund_cur):
    project(refund_cur, 1500)
    project(refund_cur, 2775)
    project(refund_cur, 2775)
    assert held(refund_cur) == 20


def test_failed_hold_rolls_back_refund_and_retry_recovers(refund_cur):
    refund_cur.execute("ALTER TABLE payouts ADD CONSTRAINT reject_test_hold CHECK(payout_type <> 'clawback')")
    try:
        with pytest.raises(psycopg2.errors.CheckViolation):
            project(refund_cur, 500)
        refund_cur.execute("SELECT refund_amount FROM rides WHERE id='ride'")
        assert refund_cur.fetchone()[0] == 0
        refund_cur.execute("SELECT count(*) FROM financial_events")
        assert refund_cur.fetchone()[0] == 0
    finally:
        refund_cur.execute("ALTER TABLE payouts DROP CONSTRAINT reject_test_hold")
    project(refund_cur, 500)
    assert held(refund_cur) == 5


def test_old_absorbed_refund_is_not_reclassified_after_payout(refund_cur):
    refund_cur.execute("DELETE FROM payouts WHERE id='auto'")
    project(refund_cur, 500)
    refund_cur.execute(
        "INSERT INTO payouts(id,driver_id,amount,status,payout_type) VALUES('auto','driver',20,'completed','auto')"
    )
    project(refund_cur, 500)
    assert held(refund_cur) == 0
    project(refund_cur, 1000)
    assert held(refund_cur) == 5


@pytest.mark.parametrize("payout_type", ["standard", "instant"])
def test_completed_cash_payout_types_qualify_for_refund_hold(refund_cur, payout_type):
    refund_cur.execute("DELETE FROM payouts WHERE id='auto'")
    refund_cur.execute(
        "INSERT INTO payouts(id,driver_id,amount,status,payout_type,created_at) "
        "VALUES('cash-out','driver',20,'completed',%s,now()-interval '1 hour')",
        (payout_type,),
    )
    project(refund_cur, 500)
    assert held(refund_cur) == 5


def test_completed_refund_hold_does_not_itself_qualify_as_cash_paid(refund_cur):
    refund_cur.execute("DELETE FROM payouts WHERE id='auto'")
    refund_cur.execute(
        "INSERT INTO payouts(id,driver_id,amount,status,payout_type,created_at) "
        "VALUES('old-hold','driver',5,'completed','clawback',now()-interval '1 hour')"
    )
    project(refund_cur, 500)
    # held() sums every clawback row, so the seeded 5.00 hold is always counted.
    # The guarantee is that it did not qualify as cash paid: no new hold row was
    # written for this refund, and the refund itself still projected.
    assert held(refund_cur) == 5
    refund_cur.execute("SELECT id FROM payouts WHERE payout_type='clawback' ORDER BY id")
    assert refund_cur.fetchall() == [("old-hold",)]
    refund_cur.execute("SELECT refund_amount FROM rides WHERE id='ride'")
    assert refund_cur.fetchone()[0] == 5


def test_future_dated_payout_does_not_qualify_for_refund_hold(refund_cur):
    refund_cur.execute("DELETE FROM payouts WHERE id='auto'")
    refund_cur.execute(
        "INSERT INTO payouts(id,driver_id,amount,status,payout_type,created_at) "
        "VALUES('future','driver',20,'completed','instant',now()+interval '1 hour')"
    )
    project(refund_cur, 500)
    assert held(refund_cur) == 0


def test_default_off_does_not_deduct_driver_pay(refund_cur):
    refund_cur.execute("UPDATE settings SET driver_refund_holds_enabled=false")
    project(refund_cur, 500)
    assert held(refund_cur) == 0


def test_concurrent_partial_refunds_are_capped(refund_cur, pg_dsn):
    def run(cents):
        with psycopg2.connect(pg_dsn) as conn, conn.cursor() as cur:
            return project(cur, cents)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, [1500, 2775]))
    assert held(refund_cur) == 20


def test_flag_rollout_does_not_recover_old_absorbed_refunds(refund_cur):
    refund_cur.execute("UPDATE settings SET driver_refund_holds_enabled=false")
    project(refund_cur, 500)
    refund_cur.execute("UPDATE settings SET driver_refund_holds_enabled=true")
    project(refund_cur, 500)
    assert held(refund_cur) == 0
    project(refund_cur, 1000)
    assert held(refund_cur) == 5


@pytest.mark.parametrize("earnings", [0, -1])
def test_no_positive_earnings_never_creates_hold(refund_cur, earnings):
    refund_cur.execute("UPDATE rides SET driver_earnings=%s", (earnings,))
    project(refund_cur, 500)
    assert held(refund_cur) == 0


def test_known_legacy_hold_is_counted_and_unknown_hold_blocks(refund_cur):
    refund_cur.execute(
        "INSERT INTO payouts(id,driver_id,amount,status,payout_type,failure_reason) VALUES('old','driver',18,'completed','clawback','Rider refund hold for ride ride event evt_legacy')"
    )
    project(refund_cur, 500)
    assert held(refund_cur) == 20
    refund_cur.execute(
        "INSERT INTO payouts(id,driver_id,amount,status,payout_type) VALUES('unknown','driver',1,'completed','clawback')"
    )
    with pytest.raises(psycopg2.Error, match="Unattributed"):
        project(refund_cur, 1000)
    refund_cur.execute("SELECT refund_amount FROM rides WHERE id='ride'")
    assert refund_cur.fetchone()[0] == 5


def test_ledger_metadata_distinguishes_driver_and_platform_shares(refund_cur):
    project(refund_cur, 2775)
    refund_cur.execute("SELECT metadata FROM financial_events WHERE event_type='stripe_refund'")
    metadata = refund_cur.fetchone()[0]
    assert metadata["driver_refund_hold_cents"] == 2000
    assert metadata["platform_absorbed_cents"] == 775
    assert metadata["driver_earnings_retained"] == "0.00"
