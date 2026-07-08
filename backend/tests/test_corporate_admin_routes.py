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
