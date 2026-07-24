"""Regression tests for findings 2 and 3 (WS-6): admin wallet credit/debit must
be atomic.

Both endpoints previously read wallet.balance, computed the new value in Python,
and wrote it back filtered on {"id": wallet_id} only — no compare-and-swap, no
row lock. A concurrent mutation between the read and the write was silently
overwritten (lost update), and the debit's sufficiency check was evaluated
against a balance that could already be stale.

Both now go through wallet_apply_delta, which locks the row, dedups, and writes
balance + ledger together.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_SRC = (pathlib.Path(__file__).resolve().parents[1] / "routes" / "admin" / "wallet.py").read_text()


def _body(fn_name: str) -> str:
    """Source of one endpoint function, up to the next @router decorator."""
    start = _SRC.index(f"async def {fn_name}(")
    nxt = _SRC.find("@router.", start)
    return _SRC[start : nxt if nxt != -1 else len(_SRC)]


def test_credit_uses_atomic_rpc_not_read_modify_write():
    body = _body("admin_credit_wallet")
    assert "wallet_apply_delta(" in body, "admin credit must go through the locked RPC"
    assert 'update_one(\n        "wallets"' not in body
    assert '"wallets",' not in body, "no direct wallets table write may remain"


def test_debit_uses_atomic_rpc_not_read_modify_write():
    body = _body("admin_debit_wallet")
    assert "wallet_apply_delta(" in body, "admin debit must go through the locked RPC"
    assert '"wallets",' not in body, "no direct wallets table write may remain"


def test_credit_is_not_floor_checked():
    """A wallet already below zero must still be creditable — floor-checking a
    credit would reject the very top-up that repairs it."""
    body = _body("admin_credit_wallet")
    assert "floor=None" in body


def test_debit_enforces_floor_and_maps_breach_to_400():
    """The in-Python sufficiency check races; the RPC floor is authoritative and
    must surface as the same 400 rather than a 500."""
    body = _body("admin_debit_wallet")
    assert 'floor=Decimal("0")' in body
    assert "wallet_below_floor" in body
    assert "status_code=400" in body


def test_both_pass_idempotency_key_as_reference():
    """The RPC dedups on (wallet_id, reference_id, type), so a retried admin
    action with the same key cannot apply twice even if the pre-check races."""
    for fn in ("admin_credit_wallet", "admin_debit_wallet"):
        assert "reference_id=req.idempotency_key" in _body(fn), fn


def test_balance_reported_comes_from_the_rpc():
    """Returning a locally-computed balance would report a value that never
    existed if another writer interleaved; use what the locked write returned."""
    for fn in ("admin_credit_wallet", "admin_debit_wallet"):
        assert 'txn["balance_after"]' in _body(fn), fn
