"""Admin per-payment subscription-invoice routes (TestClient).

Covers the admin PDF download (binary Response, audit-logged, 404 on missing)
and the admin email-resend (reuses the driver email helper, audit-logged,
502 on delivery failure). The PDF renderer (fpdf) and the email sender are
mocked — these tests assert the route wiring, auth, and audit, not rendering.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

_ADMIN = {
    "id": "admin-1",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": ["dashboard", "earnings"],
}
_PAYMENT = {"id": "pay_1", "driver_id": "drv_1", "amount": "23.65"}


@pytest.fixture
def admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: dict(_ADMIN)
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


# ── download ────────────────────────────────────────────────────────────────


def test_admin_download_invoice_returns_pdf(admin_client):
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value=_PAYMENT)),
        patch(
            "utils.subscription_invoice.build_subscription_invoice_pdf",
            new=AsyncMock(return_value=(b"%PDF-1.4 admin", "SpinrPass_Invoice_SPX-PAY_1.pdf")),
        ),
        patch("routes.admin.subscriptions.log_admin_action", new=AsyncMock(return_value="aud-1")) as log,
    ):
        resp = admin_client.get("/api/admin/subscription/payments/pay_1/invoice.pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"%PDF-1.4 admin"
    assert "attachment" in resp.headers.get("content-disposition", "")
    log.assert_awaited_once()
    assert log.call_args[0][1] == "download_subscription_invoice"


def test_admin_download_invoice_404_when_missing(admin_client):
    with patch("backend.db_supabase.find_one", new=AsyncMock(return_value=None)):
        resp = admin_client.get("/api/admin/subscription/payments/nope/invoice.pdf")
    assert resp.status_code == 404


# ── resend ──────────────────────────────────────────────────────────────────


def test_admin_resend_invoice_emails_and_audits(admin_client):
    kwargs = {"driver_id": "drv_1", "plan_name": "Spinr Pass", "total": "23.65"}
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value=_PAYMENT)),
        patch("utils.subscription_invoice.build_invoice_email_kwargs", new=AsyncMock(return_value=kwargs)),
        patch("utils.redis_client.redis_set_nx", new=AsyncMock(return_value=True)),
        patch("routes.drivers.subscriptions._send_subscription_invoice_email", new=AsyncMock(return_value=True)) as send,
        patch("routes.admin.subscriptions.log_admin_action", new=AsyncMock(return_value="aud-1")) as log,
    ):
        resp = admin_client.post("/api/admin/subscription/payments/pay_1/resend-invoice")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    send.assert_awaited_once_with(**kwargs)
    log.assert_awaited_once()
    assert log.call_args[0][1] == "resend_subscription_invoice"


def test_admin_resend_invoice_502_on_delivery_failure(admin_client):
    kwargs = {"driver_id": "drv_1"}
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value=_PAYMENT)),
        patch("utils.subscription_invoice.build_invoice_email_kwargs", new=AsyncMock(return_value=kwargs)),
        patch("utils.redis_client.redis_set_nx", new=AsyncMock(return_value=True)),
        patch("utils.redis_client.redis_delete", new=AsyncMock()) as rdel,
        patch("routes.drivers.subscriptions._send_subscription_invoice_email", new=AsyncMock(return_value=False)),
        patch("routes.admin.subscriptions.log_admin_action", new=AsyncMock(return_value="aud-1")),
    ):
        resp = admin_client.post("/api/admin/subscription/payments/pay_1/resend-invoice")
    assert resp.status_code == 502
    # Delivery failed → cooldown is cleared so a genuine retry isn't blocked.
    rdel.assert_awaited_once()


def test_admin_resend_invoice_429_on_cooldown(admin_client):
    # A second resend within the cooldown window (redis_set_nx -> False) is rejected
    # before any email is sent.
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value=_PAYMENT)),
        patch(
            "utils.subscription_invoice.build_invoice_email_kwargs", new=AsyncMock(return_value={"driver_id": "drv_1"})
        ),
        patch("utils.redis_client.redis_set_nx", new=AsyncMock(return_value=False)),
        patch("routes.drivers.subscriptions._send_subscription_invoice_email", new=AsyncMock(return_value=True)) as send,
    ):
        resp = admin_client.post("/api/admin/subscription/payments/pay_1/resend-invoice")
    assert resp.status_code == 429
    send.assert_not_awaited()


def test_admin_resend_invoice_503_on_redis_error(admin_client):
    """2026-08-11 P1 fix: redis_set_nx now raises on a real Redis error
    instead of silently falling back per-replica. This cooldown exists
    specifically to stop a stuck/looping admin action from email-bombing a
    driver -- must fail CLOSED (503) rather than silently proceeding as if
    the anti-spam guard didn't matter."""
    with (
        patch("backend.db_supabase.find_one", new=AsyncMock(return_value=_PAYMENT)),
        patch(
            "utils.subscription_invoice.build_invoice_email_kwargs", new=AsyncMock(return_value={"driver_id": "drv_1"})
        ),
        patch("utils.redis_client.redis_set_nx", new=AsyncMock(side_effect=ConnectionError("redis down"))),
        patch("routes.drivers.subscriptions._send_subscription_invoice_email", new=AsyncMock(return_value=True)) as send,
    ):
        resp = admin_client.post("/api/admin/subscription/payments/pay_1/resend-invoice")
    assert resp.status_code == 503
    send.assert_not_awaited()


# ── auth (denied path) ──────────────────────────────────────────────────────


def test_invoice_routes_require_admin_auth(test_client):
    # No admin token → both routes must be gated (401/403), never reachable.
    dl = test_client.get("/api/admin/subscription/payments/pay_1/invoice.pdf")
    rs = test_client.post("/api/admin/subscription/payments/pay_1/resend-invoice")
    assert dl.status_code in (401, 403)
    assert rs.status_code in (401, 403)
