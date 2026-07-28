"""PUT /admin/corporate-accounts/{id}/wallet/config — auto-topup configuration."""

from unittest.mock import AsyncMock, patch


def test_update_autotopup_config(test_client, admin_override):
    updated = {
        "id": "w1",
        "company_id": "c1",
        "balance": "0.00",
        "auto_topup_enabled": True,
        "auto_topup_threshold": "100.00",
        "auto_topup_amount": "500.00",
        "auto_topup_daily_cap": "5000.00",
    }
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ),
        patch(
            "routes.corporate_wallet.update_corporate_wallet_config",
            AsyncMock(return_value=updated),
        ) as m_upd,
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={
                "auto_topup_enabled": True,
                "auto_topup_threshold": 100,
                "auto_topup_amount": 500,
                "auto_topup_daily_cap": 5000,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["auto_topup_enabled"] is True
    m_upd.assert_awaited_once()
    kwargs = m_upd.call_args.kwargs
    assert kwargs["wallet_id"] == "w1"
    assert kwargs["patch"]["auto_topup_enabled"] is True
    assert kwargs["patch"]["auto_topup_threshold"] == 100


def test_update_config_404_when_wallet_missing(test_client, admin_override):
    with patch(
        "routes.corporate_wallet.get_corporate_wallet_by_company",
        AsyncMock(return_value=None),
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={"auto_topup_enabled": False},
        )
    assert resp.status_code == 404, resp.text


def test_enabling_without_threshold_or_amount_rejected(test_client, admin_override):
    with patch(
        "routes.corporate_wallet.get_corporate_wallet_by_company",
        AsyncMock(
            return_value={
                "id": "w1",
                "auto_topup_enabled": False,
                "auto_topup_threshold": None,
                "auto_topup_amount": None,
            }
        ),
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={"auto_topup_enabled": True},
        )
    assert resp.status_code == 422, resp.text


def test_disabling_autotopup_is_allowed_without_other_fields(test_client, admin_override):
    updated = {"id": "w1", "auto_topup_enabled": False}
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(
                return_value={
                    "id": "w1",
                    "auto_topup_enabled": True,
                    "auto_topup_threshold": "100",
                    "auto_topup_amount": "500",
                }
            ),
        ),
        patch(
            "routes.corporate_wallet.update_corporate_wallet_config",
            AsyncMock(return_value=updated),
        ),
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={"auto_topup_enabled": False},
        )
    assert resp.status_code == 200, resp.text


def test_empty_body_returns_wallet_unchanged(test_client, admin_override):
    wallet = {"id": "w1", "auto_topup_enabled": False}
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value=wallet),
        ),
        patch("routes.corporate_wallet.update_corporate_wallet_config", AsyncMock()) as m_upd,
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={},
        )
    assert resp.status_code == 200
    m_upd.assert_not_awaited()


def test_invalid_amount_rejected_by_pydantic(test_client, admin_override):
    resp = test_client.put(
        "/api/admin/corporate-accounts/c1/wallet/config",
        json={"auto_topup_amount": -5},
    )
    assert resp.status_code == 422


def test_config_update_writes_audit_log(test_client, admin_override):
    """Corporate module lifecycle audit Finding 9: this endpoint controls
    whether the company can be auto-charged going forward — as security-
    relevant as any other admin action in this module, but never wrote to
    the audit trail."""
    updated = {"id": "w1", "auto_topup_enabled": True}
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "auto_topup_enabled": False}),
        ),
        patch(
            "routes.corporate_wallet.update_corporate_wallet_config",
            AsyncMock(return_value=updated),
        ),
        patch("routes.corporate_wallet.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={"auto_topup_enabled": True, "auto_topup_threshold": 100, "auto_topup_amount": 500},
        )
    assert resp.status_code == 200, resp.text
    mock_audit.assert_awaited_once()
    assert mock_audit.await_args.kwargs["details"]["patch"]["auto_topup_enabled"] is True


def test_empty_body_config_update_does_not_write_audit_log(test_client, admin_override):
    wallet = {"id": "w1", "auto_topup_enabled": False}
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value=wallet),
        ),
        patch("routes.corporate_wallet.update_corporate_wallet_config", AsyncMock()) as m_upd,
        patch("routes.corporate_wallet.log_admin_action", AsyncMock()) as mock_audit,
    ):
        resp = test_client.put(
            "/api/admin/corporate-accounts/c1/wallet/config",
            json={},
        )
    assert resp.status_code == 200
    m_upd.assert_not_awaited()
    mock_audit.assert_not_awaited()
