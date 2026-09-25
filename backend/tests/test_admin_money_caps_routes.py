"""N23 / ADMIN-OPS-001 wiring: the per-admin daily cap blocks admin wallet
credit/debit and dispute refunds BEFORE any money moves.

Wallet endpoints go through HTTP; the audit_logs daily-sum read runs through the
real get_rows path against the conftest ``mock_supabase_client``. The dispute
handler is called directly, as test_dispute_refund_cents.py does; the HTTP
route-resolution test lives in test_admin_support_routes.py. Dispute refunds
only move money when admin_dispute_refunds_enabled is on.
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


async def _resolve(refund_amount: str, *, cap, refund_create, flag=True, resolution="partial_refund"):
    from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

    async def fake_get_rows(table, filters=None, **kwargs):
        if table == "disputes":
            return [dict(_DISPUTE)]
        assert table == "audit_logs" and filters["actor_id"] == "admin_1"
        return list(TODAY_ROWS)

    settings = {"stripe_secret_key": "sk_test_x", "admin_dispute_refunds_enabled": flag}
    req = ResolveDisputeRequest(resolution=resolution, refund_amount=Decimal(refund_amount))
    with (
        patch("backend.routes.disputes.db_supabase.get_rows", fake_get_rows),
        patch(
            "backend.routes.disputes.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_1", "rider_id": "user_1", "stripe_charge_id": "pi_123"}),
        ),
        patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()) as update_one,
        patch("backend.routes.disputes.get_app_settings", AsyncMock(return_value=settings)),
        patch("backend.routes.disputes.log_admin_action", AsyncMock()) as audit,
        patch("backend.routes.disputes.send_push_notification", AsyncMock()) as push,
        patch(f"{CAPS_MOD}.log_admin_action", AsyncMock()),
        _cap_settings(admin_money_daily_cap_per_admin=cap),
        patch("stripe.Refund.create", refund_create),
    ):
        result = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
    return result, update_one, audit, push


@pytest.mark.anyio
async def test_dispute_refund_over_cap_is_403_and_no_stripe_call():
    refund_create = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await _resolve("10.01", cap="100.00", refund_create=refund_create)
    assert exc.value.status_code == 403
    refund_create.assert_not_called()


@pytest.mark.anyio
async def test_dispute_refund_flag_on_under_cap_calls_stripe():
    refund_create = MagicMock(return_value=MagicMock(status="succeeded", id="re_1"))
    result, update_one, audit, push = await _resolve("10.00", cap="100.00", refund_create=refund_create)
    assert result["success"] is True
    assert result["refund_issued"] is True
    refund_create.assert_called_once()
    update_one.assert_awaited_once()
    assert audit.await_args.args[4]["refund_issued"] is True
    assert "has been issued" in push.await_args.args[2]


@pytest.mark.anyio
@pytest.mark.parametrize(("status", "issued"), [("pending", True), ("failed", False), ("canceled", False)])
async def test_dispute_refund_issued_only_for_succeeded_or_pending(status, issued):
    refund_create = MagicMock(return_value=MagicMock(status=status, id="re_1"))
    result, update_one, audit, push = await _resolve("10.00", cap="100.00", refund_create=refund_create)
    refund_create.assert_called_once()
    assert result["refund_issued"] is issued
    assert audit.await_args.args[4]["refund_issued"] is issued  # what the cap sums on
    assert ("has been issued" in push.await_args.args[2]) is issued
    assert update_one.await_args.args[2]["refund_amount"] == (Decimal("10.00") if issued else 0)
    assert ("message" in result) is (not issued)


@pytest.mark.anyio
@pytest.mark.parametrize("resolution", ["approved", "partial_refund"])
async def test_dispute_refund_flag_off_records_resolution_without_refund(resolution):
    refund_create = MagicMock()
    # cap=1.00 would block a flag-on refund; flag off must not even reach the cap.
    result, update_one, audit, push = await _resolve(
        "10.00", cap="1.00", refund_create=refund_create, flag=False, resolution=resolution
    )
    refund_create.assert_not_called()
    assert result["refund_issued"] is False
    assert "no refund was issued" in result["message"].lower()
    assert result["refund"] == {
        "status": "not_issued",
        "reason": "admin_dispute_refunds_disabled",
        "approved_amount": "10.00",
    }
    update_one.assert_awaited_once()
    updates = update_one.await_args.args[2]
    assert updates["status"] == "resolved"
    assert updates["refund_amount"] == 0  # not summed into total_refunded
    details = audit.await_args.args[4]
    assert details["refund_issued"] is False
    assert "has been issued" not in push.await_args.args[2]


@pytest.mark.anyio
async def test_dispute_flag_read_failure_is_503_and_no_refund():
    from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

    refund_create = MagicMock()
    with (
        patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dict(_DISPUTE)])),
        patch(
            "backend.routes.disputes.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_1", "rider_id": "user_1", "stripe_charge_id": "pi_123"}),
        ),
        patch("backend.routes.disputes.db_supabase.update_one", AsyncMock()) as update_one,
        patch("backend.routes.disputes.get_app_settings", AsyncMock(side_effect=RuntimeError("db down"))),
        patch("stripe.Refund.create", refund_create),
    ):
        with pytest.raises(HTTPException) as exc:
            await admin_resolve_dispute(
                dispute_id="disp_1",
                req=ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("5")),
                current_admin=dict(_ADMIN),
            )
    assert exc.value.status_code == 503
    refund_create.assert_not_called()
    update_one.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("flag", [True, False])
async def test_dispute_rejected_is_identical_with_flag_on_or_off(flag):
    refund_create = MagicMock()
    result, update_one, audit, _ = await _resolve(
        "10.00", cap="1.00", refund_create=refund_create, flag=flag, resolution="rejected"
    )
    refund_create.assert_not_called()
    assert result["refund_issued"] is False and "message" not in result
    assert result["refund"] is None
    assert update_one.await_args.args[2]["status"] == "rejected"
    assert audit.await_args.args[4]["refund_issued"] is False


@pytest.mark.anyio
async def test_concurrent_resolve_loser_gets_409_and_no_second_audit_row():
    """Two resolves both read the dispute as open (the race). The status write
    is a compare-and-set, so only the first lands; the second gets 409, writes
    no dispute_resolved audit row (the cap can't double count), and its Stripe
    call is a replay under the same idempotency key, not a second refund."""
    from backend.routes.disputes import ResolveDisputeRequest, admin_resolve_dispute

    row = dict(_DISPUTE)  # the "database" row

    async def cas_update(table, filters, update):
        assert filters["id"] == row["id"]
        if row["status"] in filters["status"]["$nin"]:
            return None  # 0 rows matched
        row.update(update)
        return dict(row)

    refund_create = MagicMock(return_value=MagicMock(status="succeeded", id="re_1"))
    settings = {"stripe_secret_key": "sk_test_x", "admin_dispute_refunds_enabled": True}
    with (
        patch("backend.routes.disputes.db_supabase.get_rows", AsyncMock(return_value=[dict(_DISPUTE)])),
        patch(
            "backend.routes.disputes.db_supabase.get_ride",
            AsyncMock(return_value={"id": "ride_1", "rider_id": "user_1", "stripe_charge_id": "pi_123"}),
        ),
        patch("backend.routes.disputes.db_supabase.update_one", cas_update),
        patch("backend.routes.disputes.get_app_settings", AsyncMock(return_value=settings)),
        patch("backend.routes.disputes.log_admin_action", AsyncMock()) as audit,
        patch("backend.routes.disputes.send_push_notification", AsyncMock()) as push,
        patch(f"{CAPS_MOD}.log_admin_action", AsyncMock()),
        _cap_settings(admin_money_daily_cap_per_admin=None),
        patch("stripe.Refund.create", refund_create),
    ):
        req = ResolveDisputeRequest(resolution="approved", refund_amount=Decimal("10.00"))
        first = await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin=dict(_ADMIN))
        with pytest.raises(HTTPException) as exc:
            await admin_resolve_dispute(dispute_id="disp_1", req=req, current_admin={"id": "admin_2", "role": "admin"})

    assert first["refund_issued"] is True
    assert exc.value.status_code == 409
    assert row["resolved_by"] == "admin_1"
    assert audit.await_count == 1  # only the winner's dispute_resolved row
    assert push.await_count == 1
    keys = {c.kwargs["idempotency_key"] for c in refund_create.call_args_list}
    amounts = {c.kwargs["amount"] for c in refund_create.call_args_list}
    assert keys == {"refund-dispute-disp_1"} and amounts == {1000}  # replay, not a second refund
