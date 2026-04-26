"""GET /admin/corporate-accounts/{id}/wallet — balance + transaction history."""

from unittest.mock import AsyncMock, patch


def test_get_wallet_returns_balance_and_txns(test_client, admin_override):
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(
                return_value={
                    "id": "w1",
                    "company_id": "c1",
                    "balance": "500.00",
                    "currency": "CAD",
                    "auto_topup_enabled": False,
                    "auto_topup_threshold": None,
                    "auto_topup_amount": None,
                    "soft_negative_floor": "-50.00",
                }
            ),
        ),
        patch(
            "routes.corporate_wallet.list_wallet_transactions",
            AsyncMock(
                return_value=[
                    {
                        "id": "t1",
                        "type": "topup",
                        "amount": "500.00",
                        "balance_after": "500.00",
                        "scope": "master",
                        "created_at": "2026-04-16T00:00:00Z",
                    }
                ]
            ),
        ),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/wallet")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance"] == "500.00"
    assert body["currency"] == "CAD"
    assert body["auto_topup_enabled"] is False
    assert len(body["transactions"]) == 1
    assert body["transactions"][0]["type"] == "topup"


def test_get_wallet_404_when_missing(test_client, admin_override):
    with patch(
        "routes.corporate_wallet.get_corporate_wallet_by_company",
        AsyncMock(return_value=None),
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/wallet")
    assert resp.status_code == 404, resp.text


def test_get_wallet_caps_limit_at_200(test_client, admin_override):
    with (
        patch(
            "routes.corporate_wallet.get_corporate_wallet_by_company",
            AsyncMock(return_value={"id": "w1", "balance": "0.00"}),
        ),
        patch(
            "routes.corporate_wallet.list_wallet_transactions",
            AsyncMock(return_value=[]),
        ) as m_list,
    ):
        resp = test_client.get("/api/admin/corporate-accounts/c1/wallet?skip=0&limit=9999")

    assert resp.status_code == 200
    kwargs = m_list.call_args.kwargs
    assert kwargs["limit"] == 200
