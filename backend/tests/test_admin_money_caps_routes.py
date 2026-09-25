"""N23 / ADMIN-OPS-001 wiring: the per-admin daily cap blocks admin wallet
credit/debit BEFORE any money moves.

Wallet endpoints go through HTTP; the audit_logs daily-sum read runs through the
real get_rows path against the conftest ``mock_supabase_client``. The dispute
refund path this cap also guarded was deleted 2026-09-25 (in-app disputes are
disabled; PUT /admin/disputes/{id}/resolve is a 410 stub pinned in
test_disputes_disabled.py), so its tests went with it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
