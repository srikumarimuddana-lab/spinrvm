# backend/tests/test_corporate_admin_routes.py
from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests._factories import corporate_account_row


def test_list_filters_by_status(test_client, admin_override):
    rows = [corporate_account_row("pending_verification", name="A")]
    with patch(
        "db_supabase.list_corporate_accounts_filtered",
        AsyncMock(return_value=rows),
    ):
        resp = test_client.get(
            "/api/admin/corporate-accounts?status=pending_verification",
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending_verification"


def test_status_filter_validates_enum(test_client, admin_override):
    resp = test_client.get(
        "/api/admin/corporate-accounts?status=bogus",
    )
    assert resp.status_code == 422


def test_get_single_account_includes_status_and_size_tier(test_client, admin_override):
    # Regression: the admin-dashboard detail page renders
    # `company.status.replace(...)` and `company.size_tier.replace(...)`
    # with no null-guard, so a response missing either field crashes the
    # page for every corporate account.
    row = corporate_account_row("active", size_tier="enterprise", id="c1")
    with patch(
        "routes.corporate_accounts.get_corporate_account_by_id",
        AsyncMock(return_value=row),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "active"
    assert data["size_tier"] == "enterprise"


def test_create_sanitizes_name_without_corrupting_it(test_client, admin_override):
    # Regression: sanitize_string() returns (is_valid, sanitized_str), but
    # the handler assigned the raw tuple to account.name/contact_name
    # instead of destructuring it, so every created account stored
    # "(True, 'Acme Corp')" as its name.
    created = corporate_account_row("pending_verification", id="c1", name="Acme Corp")
    mock_insert = AsyncMock(return_value=created)
    with patch("routes.corporate_accounts.insert_corporate_account", mock_insert):
        resp = test_client.post(
            "/api/admin/corporate-accounts",
            json={"name": "Acme Corp", "contact_name": "Jane Doe"},
        )
    assert resp.status_code == 201, resp.text
    sent = mock_insert.call_args[0][0]
    assert sent["name"] == "Acme Corp"
    assert sent["contact_name"] == "Jane Doe"


def test_update_sanitizes_name_without_corrupting_it(test_client, admin_override):
    existing = corporate_account_row("active", id="c1", name="Old Name")
    updated = corporate_account_row("active", id="c1", name="New Name")
    mock_update = AsyncMock(return_value=updated)
    with (
        patch(
            "routes.corporate_accounts.get_corporate_account_by_id",
            AsyncMock(return_value=existing),
        ),
        patch("routes.corporate_accounts.db_update_corporate_account", mock_update),
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1",
            json={"name": "New Name", "contact_name": "New Contact"},
        )
    assert resp.status_code == 200, resp.text
    sent = mock_update.call_args[0][1]
    assert sent["name"] == "New Name"
    assert sent["contact_name"] == "New Contact"


# ── M1.4: rich create schema + owner bootstrap ───────────────────────────────


def test_create_accepts_rich_b2b_fields(test_client, admin_override):
    created = corporate_account_row("pending_verification", id="c1", name="Acme Corp")
    mock_insert = AsyncMock(return_value=created)
    with patch("routes.corporate_accounts.insert_corporate_account", mock_insert):
        resp = test_client.post(
            "/api/admin/corporate-accounts",
            json={
                "name": "Acme Corp",
                "legal_name": "Acme Corporation Inc.",
                "business_number": "12-345 6789 rt0001",
                "tax_region": "sk",
                "size_tier": "mid_market",
                "industry": "Automotive",
            },
        )
    assert resp.status_code == 201, resp.text
    sent = mock_insert.call_args[0][0]
    assert sent["business_number"] == "123456789RT0001"  # canonicalized
    assert sent["tax_region"] == "SK"
    assert sent["size_tier"] == "mid_market"
    assert "owner_email" not in sent  # never a column


def test_create_with_owner_email_bootstraps_owner(test_client, admin_override):
    created = corporate_account_row("pending_verification", id="c7", name="Acme Corp")
    member = {"id": "m7", "role": "owner", "status": "invited"}
    mock_boot = AsyncMock(return_value=(member, "app://join?token=t0k"))
    with (
        patch("routes.corporate_accounts.insert_corporate_account", AsyncMock(return_value=created)),
        patch("routes.corporate_accounts.bootstrap_owner", mock_boot),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts",
            json={"name": "Acme Corp", "owner_email": "Owner@Acme.com"},
        )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["owner_invite_url"] == "app://join?token=t0k"
    assert data["owner_member_id"] == "m7"
    assert data["owner_bootstrap_error"] is False
    kwargs = mock_boot.await_args.kwargs
    assert kwargs["company_id"] == "c7"
    assert kwargs["email"] == "owner@acme.com"  # normalized


def test_create_owner_bootstrap_failure_is_partial_success(test_client, admin_override):
    created = corporate_account_row("pending_verification", id="c8", name="Acme Corp")
    with (
        patch("routes.corporate_accounts.insert_corporate_account", AsyncMock(return_value=created)),
        patch(
            "routes.corporate_accounts.bootstrap_owner",
            AsyncMock(side_effect=RuntimeError("db down")),
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts",
            json={"name": "Acme Corp", "owner_email": "owner@acme.com"},
        )
    assert resp.status_code == 201  # company exists; error surfaced, not hidden
    assert resp.json()["owner_bootstrap_error"] is True


def test_create_rejects_invalid_business_number(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts",
        json={"name": "Acme Corp", "business_number": "12345"},
    )
    assert resp.status_code == 422


def test_update_persists_rich_b2b_fields(test_client, admin_override):
    # Regression (M1.6): the route-local CorporateAccountUpdate was thin, so
    # legal_name/business_number/tax_region sent by the admin detail page were
    # silently DROPPED (pydantic extra='ignore') and never persisted.
    existing = corporate_account_row("active", id="c1")
    updated = corporate_account_row("active", id="c1", legal_name="Acme Corporation Inc.")
    mock_update = AsyncMock(return_value=updated)
    with (
        patch("routes.corporate_accounts.get_corporate_account_by_id", AsyncMock(return_value=existing)),
        patch("routes.corporate_accounts.db_update_corporate_account", mock_update),
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1",
            json={
                "legal_name": "Acme Corporation Inc.",
                "business_number": "123456789rt0001",
                "tax_region": "sk",
                "size_tier": "enterprise",
                "industry": "Automotive",
            },
        )
    assert resp.status_code == 200, resp.text
    sent = mock_update.call_args[0][1]
    assert sent["legal_name"] == "Acme Corporation Inc."
    assert sent["business_number"] == "123456789RT0001"
    assert sent["tax_region"] == "SK"
    assert sent["size_tier"] == "enterprise"
    assert sent["industry"] == "Automotive"


# ── M2.3: staff KYB confirm + decision emails + view fallback ────────────────


def test_kyb_document_confirm_persists_path(test_client, admin_override):
    updated = corporate_account_row("pending_verification", id="c1")
    updated["kyb_submitted_at"] = "2026-07-10T00:00:00Z"
    with (
        patch("db_supabase.kyb_object_exists", AsyncMock(return_value=True)),
        patch("db_supabase.set_kyb_document", AsyncMock(return_value=updated)) as m_set,
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-document",
            json={"path": "kyb/c1/doc.pdf"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["submitted_at"] == "2026-07-10T00:00:00Z"
    m_set.assert_awaited_once_with(company_id="c1", path="kyb/c1/doc.pdf")


def test_kyb_document_confirm_rejects_foreign_prefix(test_client, admin_override):
    resp = test_client.post(
        "/api/admin/corporate-accounts/c1/kyb-document",
        json={"path": "kyb/OTHER/doc.pdf"},
    )
    assert resp.status_code == 400


def test_kyb_document_confirm_requires_uploaded_object(test_client, admin_override):
    with patch("db_supabase.kyb_object_exists", AsyncMock(return_value=False)):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-document",
            json={"path": "kyb/c1/ghost.pdf"},
        )
    assert resp.status_code == 400


def _kyb_review_mocks(approved_row):
    return (
        patch("db_supabase.record_kyb_decision", AsyncMock(return_value=approved_row)),
        patch("routes.corporate_accounts.ensure_corporate_wallet", AsyncMock(return_value={})),
        patch("routes.corporate_accounts.get_app_settings", AsyncMock(return_value={})),
        patch("utils.email_provider.send_transactional_email", AsyncMock(return_value=True)),
    )


def test_kyb_review_approve_sends_decision_email(test_client, admin_override):
    row = corporate_account_row("active", id="c1", contact_email="owner@acme.com")
    p_dec, p_wallet, p_settings, p_mail = _kyb_review_mocks(row)
    with p_dec, p_wallet, p_settings, p_mail as m_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200, resp.text
    kwargs = m_mail.await_args.kwargs
    assert kwargs["to"] == "owner@acme.com"
    assert "approved" in kwargs["subject"].lower()


def test_kyb_review_reject_email_includes_note(test_client, admin_override):
    row = corporate_account_row("suspended", id="c1", contact_email="owner@acme.com")
    p_dec, p_wallet, p_settings, p_mail = _kyb_review_mocks(row)
    with p_dec, p_wallet, p_settings, p_mail as m_mail:
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": False, "note": "BN mismatch"},
        )
    assert resp.status_code == 200, resp.text
    kwargs = m_mail.await_args.kwargs
    assert "BN mismatch" in kwargs["text"]


def test_kyb_review_email_failure_does_not_fail_review(test_client, admin_override):
    row = corporate_account_row("active", id="c1", contact_email="owner@acme.com")
    with (
        patch("db_supabase.record_kyb_decision", AsyncMock(return_value=row)),
        patch("routes.corporate_accounts.ensure_corporate_wallet", AsyncMock(return_value={})),
        patch("routes.corporate_accounts.get_app_settings", AsyncMock(return_value={})),
        patch(
            "utils.email_provider.send_transactional_email",
            AsyncMock(side_effect=RuntimeError("ses down")),
        ),
    ):
        resp = test_client.post(
            "/api/admin/corporate-accounts/c1/kyb-review",
            json={"approve": True},
        )
    assert resp.status_code == 200  # decision recorded; email is best-effort


def test_kyb_view_accepts_raw_storage_key(test_client, admin_override):
    # set_kyb_document stores raw keys (kyb/{id}/{uuid}.ext); the view
    # endpoint must resolve them (legacy full URLs keep working via regex).
    account = corporate_account_row("pending_verification", id="c1")
    account["kyb_document_url"] = "kyb/c1/doc.pdf"
    fake_storage = MagicMock()
    fake_storage.storage.from_.return_value.download.return_value = b"%PDF-1.4"
    with (
        patch("routes.corporate_accounts.get_rows", AsyncMock(return_value=[account])),
        patch("supabase_client.supabase", fake_storage),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/kyb/view")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.4"
    fake_storage.storage.from_.return_value.download.assert_called_once_with("kyb/c1/doc.pdf")
