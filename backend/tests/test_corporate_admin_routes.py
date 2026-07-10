# backend/tests/test_corporate_admin_routes.py
from unittest.mock import AsyncMock, patch

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
