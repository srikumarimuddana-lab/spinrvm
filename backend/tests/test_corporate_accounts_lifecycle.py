# backend/tests/test_corporate_accounts_lifecycle.py
"""Coverage for corporate_accounts.py's get/update/delete/status-transition
handlers — mostly error paths, audit-log-failure isolation, and the
suspend/close side effects (wallet freeze, pre-pickup ride cancellation,
wallet wind-down, reactivation auto-topup review) that weren't previously
exercised.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests._factories import corporate_account_row

_ROUTE = "routes.corporate_accounts."


# ── get_corporate_account ───────────────────────────────────────────────


def test_get_account_not_found_404(test_client, admin_override):
    with patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)):
        resp = test_client.get("/api/admin/corporate-accounts/c_missing")
    assert resp.status_code == 404


def test_get_account_db_error_is_500(test_client, admin_override):
    with patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(side_effect=RuntimeError("boom"))):
        resp = test_client.get("/api/admin/corporate-accounts/c1")
    assert resp.status_code == 500


# ── update_corporate_account ────────────────────────────────────────────


def test_update_account_not_found_404(test_client, admin_override):
    with patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)):
        resp = test_client.put("/api/admin/corporate-accounts/c_missing", json={"name": "New"})
    assert resp.status_code == 404


def test_update_account_normalizes_contact_phone():
    existing = corporate_account_row("active", id="c1")
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch(
            _ROUTE + "db_update_corporate_account",
            AsyncMock(return_value={**existing, "contact_phone": "+13065551234"}),
        ) as m_upd,
        patch(_ROUTE + "log_admin_action", AsyncMock()),
    ):
        import asyncio

        from routes.corporate_accounts import CorporateAccountUpdate, update_corporate_account

        result = asyncio.run(
            update_corporate_account("c1", CorporateAccountUpdate(contact_phone="306-555-1234"), {"id": "admin1"})
        )
    assert result["contact_phone"] == "+13065551234"
    assert m_upd.call_args.args[1]["contact_phone"] == "+13065551234"


def test_update_account_db_error_is_500(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "db_update_corporate_account", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        resp = test_client.put("/api/admin/corporate-accounts/c1", json={"name": "New Name"})
    assert resp.status_code == 500


def test_update_account_audit_log_failure_does_not_fail_request(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    updated = {**existing, "name": "New Name"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "db_update_corporate_account", AsyncMock(return_value=updated)),
        patch(_ROUTE + "log_admin_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.put("/api/admin/corporate-accounts/c1", json={"name": "New Name"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "New Name"


# ── delete_corporate_account ────────────────────────────────────────────


def test_delete_account_not_found_404(test_client, admin_override):
    with patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)):
        resp = test_client.delete("/api/admin/corporate-accounts/c_missing")
    assert resp.status_code == 404


def test_delete_account_success_204(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "db_delete_corporate_account", AsyncMock(return_value=None)),
        patch(_ROUTE + "log_admin_action", AsyncMock()) as m_log,
    ):
        resp = test_client.delete("/api/admin/corporate-accounts/c1")
    assert resp.status_code == 204
    m_log.assert_awaited_once()


def test_delete_account_db_error_is_500(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "db_delete_corporate_account", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        resp = test_client.delete("/api/admin/corporate-accounts/c1")
    assert resp.status_code == 500


def test_delete_account_audit_log_failure_does_not_fail_request(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch(_ROUTE + "db_delete_corporate_account", AsyncMock(return_value=None)),
        patch(_ROUTE + "log_admin_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.delete("/api/admin/corporate-accounts/c1")
    assert resp.status_code == 204


# ── change_company_status ───────────────────────────────────────────────


def test_change_status_company_not_found_404(test_client, admin_override):
    with patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=None)):
        resp = test_client.post("/api/admin/corporate-accounts/c_missing/status", json={"status": "suspended"})
    assert resp.status_code == 404


def test_change_status_closed_account_cannot_reopen_409(test_client, admin_override):
    existing = corporate_account_row("closed", id="c1")
    with patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)):
        resp = test_client.post("/api/admin/corporate-accounts/c1/status", json={"status": "active"})
    assert resp.status_code == 409


def test_change_status_row_disappears_mid_transition_404(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=None)),
    ):
        resp = test_client.post("/api/admin/corporate-accounts/c1/status", json={"status": "suspended"})
    assert resp.status_code == 404


def test_change_status_suspend_freezes_auto_topup_and_cancels_rides(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    suspended = {**existing, "status": "suspended"}
    wallet = {"id": "w1", "auto_topup_enabled": True}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=suspended)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=wallet)),
        patch(_ROUTE + "update_corporate_wallet_config", AsyncMock()) as m_freeze,
        patch(
            _ROUTE + "get_app_settings", AsyncMock(return_value={"corporate_suspend_cancels_pre_pickup_rides": True})
        ),
        patch(_ROUTE + "cancel_pre_pickup_rides_for_company", AsyncMock(return_value=3)) as m_cancel,
        patch(_ROUTE + "log_admin_action", AsyncMock()),
    ):
        resp = test_client.post("/api/admin/corporate-accounts/c1/status", json={"status": "suspended"})
    assert resp.status_code == 200, resp.text
    m_freeze.assert_awaited_once_with(wallet_id="w1", patch={"auto_topup_enabled": False})
    m_cancel.assert_awaited_once_with("c1")


def test_change_status_suspend_ride_cancellation_failure_is_logged_not_raised(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    suspended = {**existing, "status": "suspended"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=suspended)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
        patch(
            _ROUTE + "get_app_settings", AsyncMock(return_value={"corporate_suspend_cancels_pre_pickup_rides": True})
        ),
        patch(_ROUTE + "cancel_pre_pickup_rides_for_company", AsyncMock(side_effect=RuntimeError("dispatch down"))),
        patch(_ROUTE + "log_admin_action", AsyncMock()),
    ):
        resp = test_client.post("/api/admin/corporate-accounts/c1/status", json={"status": "suspended"})
    assert resp.status_code == 200, resp.text


def test_change_status_close_refunds_wallet_when_enabled(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    closed = {**existing, "status": "closed", "stripe_customer_id": "cus_1"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=closed)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
        patch(
            _ROUTE + "get_app_settings",
            AsyncMock(
                return_value={
                    "corporate_suspend_cancels_pre_pickup_rides": False,
                    "corporate_close_refunds_wallet_balance": True,
                }
            ),
        ),
        patch(
            _ROUTE + "refund_wallet_balance_on_close",
            AsyncMock(return_value={"stripe_error": None, "unrefundable_amount": "0.00"}),
        ) as m_refund,
        patch(_ROUTE + "log_admin_action", AsyncMock()),
    ):
        resp = test_client.post("/api/admin/corporate-accounts/c1/status", json={"status": "closed"})
    assert resp.status_code == 200, resp.text
    m_refund.assert_awaited_once()


def test_change_status_close_winddown_incomplete_is_logged():
    """unrefundable_amount != '0.00' must still return 200 but log an error —
    the endpoint doesn't fail the request over an incomplete wind-down."""
    import asyncio

    from routes.corporate_accounts import CompanyStatusTransition, change_company_status

    existing = corporate_account_row("active", id="c1")
    closed = {**existing, "status": "closed"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=closed)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
        patch(
            _ROUTE + "get_app_settings",
            AsyncMock(
                return_value={
                    "corporate_suspend_cancels_pre_pickup_rides": False,
                    "corporate_close_refunds_wallet_balance": True,
                }
            ),
        ),
        patch(
            _ROUTE + "refund_wallet_balance_on_close",
            AsyncMock(return_value={"stripe_error": "card_declined", "unrefundable_amount": "12.00"}),
        ),
        patch(_ROUTE + "log_admin_action", AsyncMock()) as m_log,
    ):
        result = asyncio.run(change_company_status("c1", CompanyStatusTransition(status="closed"), {"id": "admin1"}))
    assert result["status"] == "closed"
    details = m_log.call_args.kwargs["details"]
    assert details["wallet_winddown"]["stripe_error"] == "card_declined"


def test_change_status_close_winddown_exception_is_swallowed():
    import asyncio

    from routes.corporate_accounts import CompanyStatusTransition, change_company_status

    existing = corporate_account_row("active", id="c1")
    closed = {**existing, "status": "closed"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=closed)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
        patch(
            _ROUTE + "get_app_settings",
            AsyncMock(
                return_value={
                    "corporate_suspend_cancels_pre_pickup_rides": False,
                    "corporate_close_refunds_wallet_balance": True,
                }
            ),
        ),
        patch(_ROUTE + "refund_wallet_balance_on_close", AsyncMock(side_effect=RuntimeError("stripe down"))),
        patch(_ROUTE + "log_admin_action", AsyncMock()) as m_log,
    ):
        result = asyncio.run(change_company_status("c1", CompanyStatusTransition(status="closed"), {"id": "admin1"}))
    assert result["status"] == "closed"
    details = m_log.call_args.kwargs["details"]
    assert details["wallet_winddown"] == {"skipped_reason": "unhandled_exception"}


def test_change_status_reactivation_flags_auto_topup_needs_review():
    import asyncio

    from routes.corporate_accounts import CompanyStatusTransition, change_company_status

    existing = corporate_account_row("suspended", id="c1")
    activated = {**existing, "status": "active"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=activated)),
        patch(
            _ROUTE + "get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value={})),
        patch(_ROUTE + "log_admin_action", AsyncMock()) as m_log,
    ):
        asyncio.run(change_company_status("c1", CompanyStatusTransition(status="active"), {"id": "admin1"}))
    details = m_log.call_args.kwargs["details"]
    assert details["auto_topup_needs_review"] is True


def test_change_status_reactivation_wallet_check_failure_is_swallowed():
    import asyncio

    from routes.corporate_accounts import CompanyStatusTransition, change_company_status

    existing = corporate_account_row("suspended", id="c1")
    activated = {**existing, "status": "active"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=activated)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(side_effect=RuntimeError("db down"))),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value={})),
        patch(_ROUTE + "log_admin_action", AsyncMock()) as m_log,
    ):
        result = asyncio.run(change_company_status("c1", CompanyStatusTransition(status="active"), {"id": "admin1"}))
    assert result["status"] == "active"
    details = m_log.call_args.kwargs["details"]
    assert details["auto_topup_needs_review"] is False


def test_change_status_audit_log_failure_does_not_fail_request(test_client, admin_override):
    existing = corporate_account_row("active", id="c1")
    suspended = {**existing, "status": "suspended"}
    with (
        patch(_ROUTE + "get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("db_supabase.update_corporate_account_status", AsyncMock(return_value=suspended)),
        patch(_ROUTE + "get_corporate_wallet_by_company", AsyncMock(return_value=None)),
        patch(_ROUTE + "get_app_settings", AsyncMock(return_value={})),
        patch(_ROUTE + "log_admin_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.post("/api/admin/corporate-accounts/c1/status", json={"status": "suspended"})
    assert resp.status_code == 200


# ── admin_view_kyb_document ──────────────────────────────────────────────


def test_kyb_view_account_not_found_404(test_client, admin_override):
    with patch(_ROUTE + "get_rows", AsyncMock(return_value=[])):
        resp = test_client.get("/api/admin/corporate-accounts/c1/kyb/view")
    assert resp.status_code == 404


def test_kyb_view_no_document_on_file_404(test_client, admin_override):
    with patch(_ROUTE + "get_rows", AsyncMock(return_value=[{"kyb_document_url": ""}])):
        resp = test_client.get("/api/admin/corporate-accounts/c1/kyb/view")
    assert resp.status_code == 404


def test_kyb_view_storage_client_not_configured_503(test_client, admin_override):
    with (
        patch(_ROUTE + "get_rows", AsyncMock(return_value=[{"kyb_document_url": "kyb/c1/doc.pdf"}])),
        patch("supabase_client.supabase", None),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/kyb/view")
    assert resp.status_code == 503


def test_kyb_view_storage_download_failure_502(test_client, admin_override):
    fake_supabase = MagicMock()
    fake_supabase.storage.from_.return_value.download.side_effect = Exception("not found in bucket")
    with (
        patch(_ROUTE + "get_rows", AsyncMock(return_value=[{"kyb_document_url": "kyb/c1/doc.pdf"}])),
        patch("supabase_client.supabase", fake_supabase),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/kyb/view")
    assert resp.status_code == 502


def test_kyb_view_streams_document_success(test_client, admin_override):
    fake_supabase = MagicMock()
    fake_supabase.storage.from_.return_value.download.return_value = b"%PDF-1.4 fake"
    with (
        patch(_ROUTE + "get_rows", AsyncMock(return_value=[{"kyb_document_url": "kyb/c1/doc.pdf"}])),
        patch("supabase_client.supabase", fake_supabase),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/kyb/view")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fake"


# ── create_corporate_account ────────────────────────────────────────────


def test_create_account_normalizes_contact_phone(test_client, admin_override):
    created = corporate_account_row("pending_verification", id="c1")
    with (
        patch(_ROUTE + "insert_corporate_account", AsyncMock(return_value=created)) as m_insert,
        patch(_ROUTE + "log_admin_action", AsyncMock()),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts",
            json={"name": "Acme", "contact_phone": "306-555-1234"},
        )
    assert resp.status_code == 201, resp.text
    assert m_insert.call_args.args[0]["contact_phone"] == "+13065551234"


def test_create_account_insert_failure_is_500(test_client, admin_override):
    with patch(_ROUTE + "insert_corporate_account", AsyncMock(side_effect=RuntimeError("db down"))):
        resp = test_client.post("/api/admin/corporate-accounts", json={"name": "Acme"})
    assert resp.status_code == 500


def test_create_account_audit_log_failure_does_not_fail_request(test_client, admin_override):
    created = corporate_account_row("pending_verification", id="c1")
    with (
        patch(_ROUTE + "insert_corporate_account", AsyncMock(return_value=created)),
        patch(_ROUTE + "log_admin_action", AsyncMock(side_effect=Exception("audit down"))),
    ):
        resp = test_client.post("/api/admin/corporate-accounts", json={"name": "Acme"})
    assert resp.status_code == 201
