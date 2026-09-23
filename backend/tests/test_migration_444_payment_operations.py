from pathlib import Path


def test_migration_444_locks_payment_operations_to_backend_and_is_additive():
    sql = Path(__file__).parents[1].joinpath("migrations/444_ride_payment_operations.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS public.ride_payment_operations" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "TO service_role" in sql
    assert "CREATE POLICY" not in sql
    assert "ADD COLUMN IF NOT EXISTS refund_status" in sql
    assert "ADD COLUMN IF NOT EXISTS scheduled_notice_fee_status" in sql
    assert "next_attempt_at" in sql
    assert "ON DELETE RESTRICT" in sql
