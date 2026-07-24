"""Regression tests for findings 8 and 10 (WS-6): rider fee debits must be atomic.

The no-show fee (routes/drivers/ride_cancel.py) and the cancellation fee
(routes/rides/cancellation.py) both read the rider's balance, computed
max(balance - fee, 0) in Python, then wrote it back filtered on {"id": wallet_id}
only — plus a separate wallet_transactions insert. A wallet top-up webhook or
another ride's fee landing between the read and the write was silently lost, and
the two writes could diverge if the second failed.

Both now go through wallet_apply_delta with clamp_to_floor=True, which preserves
the charge-only-what-they-have behaviour inside the row lock and writes balance +
ledger together.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_NOSHOW = (_ROOT / "routes" / "drivers" / "ride_cancel.py").read_text()
_CANCEL = (_ROOT / "routes" / "rides" / "cancellation.py").read_text()

_SITES = {"no-show fee": _NOSHOW, "cancellation fee": _CANCEL}


@pytest.mark.parametrize("name", sorted(_SITES))
def test_fee_debit_uses_atomic_rpc(name):
    src = _SITES[name]
    assert "wallet_apply_delta(" in src, f"{name} must debit via the locked RPC"


@pytest.mark.parametrize("name", sorted(_SITES))
def test_fee_debit_no_longer_writes_wallets_table_directly(name):
    """The read-modify-write is what lost concurrent updates; no direct balance
    write may survive. (find_one on wallets is a read and is still fine.)"""
    src = _SITES[name]
    assert 'update_one(\n                            "wallets"' not in src
    assert 'insert_one(\n                            "wallet_transactions"' not in src, (
        f"{name} must not write the ledger separately from the balance"
    )
    assert '"wallet_transactions",' not in src, f"{name} still inserts a ledger row outside the RPC"


@pytest.mark.parametrize("name", sorted(_SITES))
def test_fee_debit_clamps_instead_of_failing(name):
    """A rider with less than the full fee is charged what they have — the
    original max(balance - fee, 0) semantics — rather than the debit erroring."""
    src = _SITES[name]
    assert "clamp_to_floor=True" in src, f"{name} must clamp, not reject"
    assert 'floor=Decimal("0")' in src


@pytest.mark.parametrize("name", sorted(_SITES))
def test_fee_debit_is_idempotent_on_ride_id(name):
    """reference_id=ride_id means a replayed cancellation/no-show dedups in the
    RPC instead of charging the rider twice."""
    src = _SITES[name]
    assert "reference_id=ride_id" in src, f"{name} must key idempotency on the ride"


@pytest.mark.parametrize("name", sorted(_SITES))
def test_partial_collection_is_surfaced(name):
    """When the wallet could not cover the whole fee the shortfall is logged —
    the driver is still paid the full driver share, so the gap must be visible."""
    src = _SITES[name]
    assert "applied_delta" in src, f"{name} must read what was actually taken"
    assert "partial" in src.lower()
