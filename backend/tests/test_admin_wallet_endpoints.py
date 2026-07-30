"""Endpoint-level coverage for backend/routes/admin/wallet.py.

test_admin_wallet_atomic.py already source-asserts that credit/debit go
through the locked wallet_apply_delta RPC. This file exercises the actual
HTTP handlers end-to-end (with db_supabase/db mocked) to cover: happy-path
get/credit/debit, the idempotency short-circuit, insufficient-balance,
suspended-wallet, missing-user, and the floor-breach -> 400 mapping.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

MOD = "backend.routes.admin.wallet"


@pytest.fixture
def admin_override():
    """wallet_router is mounted behind require_module("earnings"); a plain
    admin_override with no `modules` claim gets a 403 there, so this file uses
    its own super_admin override (mirrors test_admin_driver_import.py)."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


USER = {
    "id": "user-1",
    "first_name": "Jane",
    "last_name": "Doe",
    "phone": "+13065550100",
    "email": "jane@example.com",
}

WALLET = {"id": "wallet-1", "user_id": "user-1", "balance": "50.00", "currency": "CAD", "is_active": True}


def _patches(**overrides):
    """Common patch set for the admin wallet endpoints; override any target."""
    targets = {
        "get_user_by_id": AsyncMock(return_value=dict(USER)),
        "get_rows": AsyncMock(return_value=[]),
        "insert_one": AsyncMock(return_value={"id": "audit-1"}),
    }
    targets.update(overrides)
    return [patch(f"{MOD}.db_supabase.{name}", fn) for name, fn in targets.items()]


def _apply(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


def _wallet_txn(balance_after="60.00", txn_id="txn-1"):
    return {"id": txn_id, "balance_after": balance_after}


# ---------- GET /wallet/{user_id} ----------


def test_get_wallet_not_found(test_client, admin_override):
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=None)))
    try:
        resp = test_client.get("/api/admin/wallet/nope")
    finally:
        _stop(patches)
    assert resp.status_code == 404


def test_get_wallet_returns_balance_and_transactions(test_client, admin_override):
    txns = [
        {
            "id": "t1",
            "type": "admin_credit",
            "amount": "10.00",
            "balance_after": "60.00",
            "description": "Admin credit: goodwill",
            "reference_id": None,
            "metadata": {"admin_id": "admin_1"},
            "created_at": "2026-07-29T00:00:00Z",
        }
    ]
    patches = _apply(_patches(get_rows=AsyncMock(return_value=txns)))
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            resp = test_client.get("/api/admin/wallet/user-1")
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wallet"]["balance"] == "50.00"
    assert body["user"]["phone"] == "+13065550100"
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["amount"] == "10.00"


# ---------- POST /wallet/credit ----------


def test_credit_wallet_happy_path(test_client, admin_override):
    patches = _apply(
        _patches(
            get_rows=AsyncMock(return_value=[]),
            wallet_apply_delta=AsyncMock(return_value=_wallet_txn("60.00")),
        )
    )
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            resp = test_client.post(
                "/api/admin/wallet/credit",
                json={"user_id": "user-1", "amount": "10.00", "reason": "goodwill credit"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance"] == "60.00"
    assert body["transaction_id"] == "txn-1"
    assert "audit_log_id" in body


def test_credit_wallet_idempotent_replay_returns_existing(test_client, admin_override):
    existing = [{"id": "txn-orig", "balance_after": "60.00"}]
    patches = _apply(_patches(get_rows=AsyncMock(return_value=existing)))
    apply_delta = AsyncMock()
    with patch(f"{MOD}.db_supabase.wallet_apply_delta", apply_delta):
        try:
            resp = test_client.post(
                "/api/admin/wallet/credit",
                json={
                    "user_id": "user-1",
                    "amount": "10.00",
                    "reason": "goodwill credit",
                    "idempotency_key": "idem-1",
                },
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transaction_id"] == "txn-orig"
    apply_delta.assert_not_called()


def test_credit_wallet_user_not_found(test_client, admin_override):
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=None)))
    try:
        resp = test_client.post(
            "/api/admin/wallet/credit",
            json={"user_id": "ghost", "amount": "10.00", "reason": "goodwill credit"},
        )
    finally:
        _stop(patches)
    assert resp.status_code == 404


def test_credit_wallet_rejects_suspended_wallet(test_client, admin_override):
    suspended = {**WALLET, "is_active": False}
    patches = _apply(_patches(get_rows=AsyncMock(return_value=[])))
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=suspended)):
        try:
            resp = test_client.post(
                "/api/admin/wallet/credit",
                json={"user_id": "user-1", "amount": "10.00", "reason": "goodwill credit"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 403


def test_credit_wallet_rejects_amount_over_cap(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/wallet/credit",
        json={"user_id": "user-1", "amount": "10001", "reason": "too much money"},
    )
    assert resp.status_code == 422


def test_credit_wallet_rejects_short_reason(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/wallet/credit",
        json={"user_id": "user-1", "amount": "10.00", "reason": "x"},
    )
    assert resp.status_code == 422


def test_credit_wallet_passes_floor_none(test_client, admin_override):
    """A credit must never be floor-checked (WS-6) — assert the actual call args,
    not just the source text already covered by test_admin_wallet_atomic.py."""
    apply_delta = AsyncMock(return_value=_wallet_txn("60.00"))
    patches = _apply(_patches(get_rows=AsyncMock(return_value=[]), wallet_apply_delta=apply_delta))
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            resp = test_client.post(
                "/api/admin/wallet/credit",
                json={"user_id": "user-1", "amount": "10.00", "reason": "goodwill credit"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200
    _, kwargs = apply_delta.call_args
    assert kwargs["floor"] is None
    assert kwargs["delta"] == Decimal("10.00")


# ---------- POST /wallet/debit ----------


def test_debit_wallet_happy_path(test_client, admin_override):
    patches = _apply(
        _patches(
            get_rows=AsyncMock(return_value=[]),
            wallet_apply_delta=AsyncMock(return_value=_wallet_txn("40.00")),
        )
    )
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            resp = test_client.post(
                "/api/admin/wallet/debit",
                json={"user_id": "user-1", "amount": 10.0, "reason": "fraud clawback"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance"] == "40.00"


def test_debit_wallet_advisory_insufficient_balance(test_client, admin_override):
    """old_balance < debit trips the advisory pre-check before the RPC is called."""
    apply_delta = AsyncMock()
    patches = _apply(_patches(get_rows=AsyncMock(return_value=[]), wallet_apply_delta=apply_delta))
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            resp = test_client.post(
                "/api/admin/wallet/debit",
                json={"user_id": "user-1", "amount": 500.0, "reason": "overdraw attempt"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 400
    assert "Insufficient balance" in resp.json()["detail"]
    apply_delta.assert_not_called()


def test_debit_wallet_rpc_floor_breach_maps_to_400(test_client, admin_override):
    """The RPC's floor check is authoritative — a race that slips past the
    advisory check must still surface as 400, not 500."""

    class _FloorErr(Exception):
        details = {"original": "wallet_below_floor"}

    apply_delta = AsyncMock(side_effect=_FloorErr("wallet_below_floor"))
    patches = _apply(_patches(get_rows=AsyncMock(return_value=[]), wallet_apply_delta=apply_delta))
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            resp = test_client.post(
                "/api/admin/wallet/debit",
                json={"user_id": "user-1", "amount": 10.0, "reason": "raced debit"},
            )
        finally:
            _stop(patches)
    assert resp.status_code == 400
    assert "Insufficient balance" in resp.json()["detail"]


def test_debit_wallet_reraises_unrelated_rpc_error(test_client, admin_override):
    """A non-floor RPC failure must propagate loudly (not be swallowed as a 400
    or silently downgraded) — CLAUDE.md's "don't silently swallow errors" rule.
    TestClient re-raises unhandled exceptions rather than returning a 500."""

    apply_delta = AsyncMock(side_effect=RuntimeError("connection reset"))
    patches = _apply(_patches(get_rows=AsyncMock(return_value=[]), wallet_apply_delta=apply_delta))
    with patch(f"{MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))):
        try:
            with pytest.raises(RuntimeError, match="connection reset"):
                test_client.post(
                    "/api/admin/wallet/debit",
                    json={"user_id": "user-1", "amount": 10.0, "reason": "raced debit"},
                )
        finally:
            _stop(patches)


def test_debit_wallet_idempotent_replay_returns_existing(test_client, admin_override):
    existing = [{"id": "txn-orig", "balance_after": "40.00"}]
    patches = _apply(_patches(get_rows=AsyncMock(return_value=existing)))
    apply_delta = AsyncMock()
    with patch(f"{MOD}.db_supabase.wallet_apply_delta", apply_delta):
        try:
            resp = test_client.post(
                "/api/admin/wallet/debit",
                json={
                    "user_id": "user-1",
                    "amount": 10.0,
                    "reason": "fraud clawback",
                    "idempotency_key": "idem-2",
                },
            )
        finally:
            _stop(patches)
    assert resp.status_code == 200, resp.text
    assert resp.json()["transaction_id"] == "txn-orig"
    apply_delta.assert_not_called()


def test_debit_wallet_user_not_found(test_client, admin_override):
    patches = _apply(_patches(get_user_by_id=AsyncMock(return_value=None)))
    try:
        resp = test_client.post(
            "/api/admin/wallet/debit",
            json={"user_id": "ghost", "amount": 10.0, "reason": "fraud clawback"},
        )
    finally:
        _stop(patches)
    assert resp.status_code == 404


def test_wallet_endpoints_require_admin_auth(test_client):
    resp = test_client.get("/api/admin/wallet/user-1")
    assert resp.status_code in (401, 403)
