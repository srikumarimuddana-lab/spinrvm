"""N23 / ADMIN-OPS-001 wiring: the per-admin daily cap blocks admin wallet
credit/debit and dispute refunds BEFORE any money moves.

Wallet endpoints go through HTTP; the audit_logs daily-sum read runs through the
real get_rows path against the conftest ``mock_supabase_client``. The dispute
handler is called directly, as test_dispute_refund_cents.py does (over HTTP,
PUT /api/admin/disputes/{id}/resolve is served by routes/admin/support.py,
which is registered first and moves no money).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

WALLET_MOD = "backend.routes.admin.wallet"
CAPS_MOD = "backend.services.admin_money_caps"

USER = {"id": "user-1", "role": "rider"}
WALLET = {"id": "wallet-1", "user_id": "user-1", "balance": "500.00", "currency": "CAD", "is_active": True}
# Already moved today by admin_1: $60 credit + $30 debit = $90.
TODAY_ROWS = [
    {"action": "wallet_credit", "details": {"amount": "60.00"}},
    {"action": "wallet_debit", "details": {"amount": "30.00"}},
]


@pytest.fixture
def admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def audit_rows(mock_supabase_client):
    table = mock_supabase_client.table.return_value
    table.range.return_value = table
    table.execute.side_effect = lambda: MagicMock(data=list(TODAY_ROWS))
    return table


def _cap_settings(**values):
    return patch(f"{CAPS_MOD}.get_app_settings", AsyncMock(return_value=dict(values)))


def _wallet_call(test_client, path, amount, *, apply_delta):
    with (
        patch(f"{WALLET_MOD}.db_supabase.get_user_by_id", AsyncMock(return_value=dict(USER))),
        patch(f"{WALLET_MOD}.db_supabase.wallet_apply_delta", apply_delta),
        patch(f"{WALLET_MOD}.db_supabase.insert_one", AsyncMock(return_value={"id": "audit-1"})),
        patch(f"{WALLET_MOD}.get_or_create_wallet", AsyncMock(return_value=dict(WALLET))),
        patch(f"{WALLET_MOD}.send_push_notification", AsyncMock()),
        patch(f"{CAPS_MOD}.log_admin_action", AsyncMock()),
    ):
        return test_client.post(path, json={"user_id": "user-1", "amount": amount, "reason": "cap test"})


@pytest.mark.parametrize("path", ["/api/admin/wallet/credit", "/api/admin/wallet/debit"])
def test_wallet_over_cap_is_403_and_never_touches_wallet(test_client, admin_override, audit_rows, path):
    apply_delta = AsyncMock()
    with _cap_settings(admin_money_daily_cap_per_admin="100.00"):
        resp = _wallet_call(test_client, path, "10.01", apply_delta=apply_delta)
    assert resp.status_code == 403, resp.text
    assert "cap" in resp.json()["detail"].lower()
    apply_delta.assert_not_awaited()
    audit_rows.eq.assert_any_call("actor_id", "admin_1")


@pytest.mark.parametrize("path", ["/api/admin/wallet/credit", "/api/admin/wallet/debit"])
def test_wallet_under_cap_is_allowed(test_client, admin_override, audit_rows, path):
    apply_delta = AsyncMock(return_value={"transaction_id": "txn-1", "balance_after": "490.00"})
    with _cap_settings(admin_money_daily_cap_per_admin="100.00"):
        resp = _wallet_call(test_client, path, "10.00", apply_delta=apply_delta)
    assert resp.status_code == 200, resp.text
    apply_delta.assert_awaited_once()


def test_wallet_null_cap_is_unchanged_behaviour(test_client, admin_override, mock_supabase_client):
    apply_delta = AsyncMock(return_value={"transaction_id": "txn-1", "balance_after": "510.00"})
    with _cap_settings(admin_money_daily_cap_per_admin=None, admin_money_alert_threshold=None):
        resp = _wallet_call(test_client, "/api/admin/wallet/credit", "9999.00", apply_delta=apply_delta)
    assert resp.status_code == 200, resp.text
    apply_delta.assert_awaited_once()
    assert all(c.args != ("audit_logs",) for c in mock_supabase_client.table.call_args_list)


def test_wallet_sum_failure_is_503_and_never_touches_wallet(test_client, admin_override):
    apply_delta = AsyncMock()
    with (
        _cap_settings(admin_money_daily_cap_per_admin="100.00"),
        patch(f"{CAPS_MOD}.db_supabase.get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        resp = _wallet_call(test_client, "/api/admin/wallet/credit", "1.00", apply_delta=apply_delta)
    assert resp.status_code == 503, resp.text
    apply_delta.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dispute refund (routes/disputes.py admin_resolve_dispute)
# ---------------------------------------------------------------------------

_DISPUTE = {"id": "disp_1", "ride_id": "ride_1", "user_id": "user_1", "status": "open", "original_fare": 50.00}
_ADMIN = {"id": "admin_1", "role": "admin"}


async def _resolve(refund_amount: str, *, cap, refund_create):
    from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "disputes":
            return [dict(_DISPUTE)]
        assert table == "audit_logs" and filters["actor_id"] == "admin_1"
        return list(TODAY_ROWS)

    req = ResolveDisputeRequest(resolution="partial_refund", refund_amount=Decimal(refund_amount))
    with (
        patch("backend.routes.disputes.db_supabase.get_rows", fake_get_rows),
        patch(
            "backend.routes.disputes.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_1", "rider_id": "user_1", "stripe_charge_id": "pi_123"}),
        ),
        patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()) as update_one,
        patch("backend.routes.disputes.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test_x"})),
        patch("backend.routes.disputes.log_admin_action", AsyncMock()),
        patch("backend.routes.disputes.send_push_notification", AsyncMock()),
        patch(f"{CAPS_MOD}.log_admin_action", AsyncMock()),
        _cap_settings(admin_money_daily_cap_per_admin=cap),
        patch("stripe.Refund.create", refund_create),
    ):
        result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
    return result, update_one


@pytest.mark.anyio
async def test_dispute_refund_over_cap_is_403_and_no_stripe_call():
    refund_create = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await _resolve("10.01", cap="100.00", refund_create=refund_create)
    assert exc.value.status_code == 403
    refund_create.assert_not_called()


@pytest.mark.anyio
async def test_dispute_refund_under_cap_is_allowed():
    refund_create = MagicMock(return_value=MagicMock(status="succeeded", id="re_1"))
    result, update_one = await _resolve("10.00", cap="100.00", refund_create=refund_create)
    assert result["success"] is True
    refund_create.assert_called_once()
    update_one.assert_awaited_once()
