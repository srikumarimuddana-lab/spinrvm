"""Contract tests for wallet_apply_delta (migration 249, WS-6 / findings 2,3,8,10).

Four call sites mutated wallets.balance with a read-modify-write and an
id-only UPDATE filter, so any concurrent mutation between the read and the write
was silently lost. wallet_apply_delta replaces that with a locked, idempotent,
signed-delta RPC.

The SQL-level assertions below read the migration directly, so the invariants
that matter (lock BEFORE the idempotency check, floor enforcement, service-role
only) are enforced without needing a database — the same approach used by
test_allowance_rpc_sign_contract.py.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_MIGRATION = pathlib.Path(__file__).resolve().parents[1] / "migrations" / "249_wallet_apply_delta.sql"
_SQL = _MIGRATION.read_text()


def test_locks_wallet_row_before_idempotency_check():
    """The lock must come FIRST, otherwise two concurrent calls with the same
    reference can both miss the dedup and both apply the delta."""
    lock_at = _SQL.index("FOR UPDATE")
    dedup_at = _SQL.index("p_reference_id IS NOT NULL")
    assert lock_at < dedup_at, "idempotency check must happen INSIDE the row lock"


def test_writes_balance_and_ledger_together():
    """Balance UPDATE and the wallet_transactions INSERT are in the same
    function body (hence the same transaction) — never two round trips."""
    update_at = _SQL.index("UPDATE wallets")
    insert_at = _SQL.index("INSERT INTO wallet_transactions")
    assert update_at < insert_at
    # Both must precede the single RETURN NEXT that ends the non-deduped path.
    assert insert_at < _SQL.rindex("RETURN NEXT")


def test_floor_is_enforced_and_clamp_is_opt_in():
    assert "p_clamp_to_floor BOOLEAN DEFAULT FALSE" in _SQL, "clamping must be opt-in"
    assert "wallet_below_floor" in _SQL, "a floor breach must raise when not clamping"


def test_clamp_never_turns_a_debit_into_a_credit():
    """If the wallet is already at or below the floor there is nothing to take.
    Without the guard, `p_floor - v_current` would be POSITIVE and the fee path
    would credit the rider instead of charging them."""
    clamp_block = _SQL[_SQL.index("IF p_clamp_to_floor THEN") : _SQL.index("UPDATE wallets")]
    assert "v_delta := p_floor - v_current;" in clamp_block
    assert "IF v_delta > 0 THEN" in clamp_block and "v_delta := 0;" in clamp_block


def test_returns_applied_delta_not_requested_delta():
    """Clamping means the charged amount can differ from the requested amount;
    callers must be able to record what actually moved."""
    assert "applied_delta  NUMERIC" in _SQL
    assert "applied_delta  := v_delta;" in _SQL


def test_is_security_definer_with_pinned_search_path():
    assert "SECURITY DEFINER" in _SQL
    assert "SET search_path = public, pg_catalog" in _SQL


def test_execute_is_locked_down_to_service_role():
    """Supabase exposes every public function at /rest/v1/rpc/ — without the
    REVOKE any client could call this money function directly."""
    assert "REVOKE EXECUTE ON FUNCTION wallet_apply_delta" in _SQL
    assert "FROM PUBLIC, anon, authenticated" in _SQL
    assert "GRANT EXECUTE ON FUNCTION wallet_apply_delta" in _SQL
    assert "TO service_role" in _SQL
    revoke_at = _SQL.index("REVOKE EXECUTE ON FUNCTION wallet_apply_delta")
    grant_at = _SQL.index("GRANT EXECUTE ON FUNCTION wallet_apply_delta")
    assert revoke_at < grant_at, "REVOKE strips service_role's inherited grant; it must be re-granted after"


def test_zero_delta_is_rejected():
    assert "delta must be non-zero" in _SQL


def test_wrapper_passes_signed_delta_and_clamp_flag():
    """The Python wrapper must forward the signed delta and clamp flag verbatim."""
    repo = (pathlib.Path(__file__).resolve().parents[1] / "repositories" / "wallet_repo.py").read_text()
    assert "async def wallet_apply_delta(" in repo
    assert '"p_delta": str(delta)' in repo
    assert '"p_clamp_to_floor": clamp_to_floor' in repo
    assert 'supabase.rpc("wallet_apply_delta", params)' in repo
