# backend/tests/test_corporate_repo_guards.py
"""Repository-level guards from clean-sheet audit CORP-001 / CORP-003 (ROADMAP N20).

- ``update_corporate_account_status(expected_status=...)`` is a compare-and-set:
  the UPDATE also filters on the status the caller read, so a concurrent
  request that already moved the row matches zero rows and gets ``None``.
- ``update_corporate_wallet_config`` only accepts wallet-config columns;
  anything else (``balance`` above all) raises before any DB call.

``repositories.corporate_repo.supabase`` is the ``mock_supabase_client``
fixture (patched by conftest's autouse ``patch_external_dependencies``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from repositories import corporate_repo

pytestmark = pytest.mark.unit


def _resp(data):
    r = MagicMock()
    r.data = data
    return r


def _wire_update_chain(mock_supabase_client, data):
    table = mock_supabase_client.table.return_value
    table.update.return_value = table
    table.execute = MagicMock(return_value=_resp(data))
    return table


@pytest.mark.anyio
async def test_status_update_without_expected_status_is_unconditional(mock_supabase_client):
    """KYB re-upload (corporate_company_kyb.py) calls without expected_status —
    its behaviour must not change."""
    table = _wire_update_chain(mock_supabase_client, [{"id": "c1", "status": "pending_verification"}])

    row = await corporate_repo.update_corporate_account_status("c1", "pending_verification")

    assert row == {"id": "c1", "status": "pending_verification"}
    assert table.eq.call_args_list == [call("id", "c1")]


@pytest.mark.anyio
async def test_status_update_with_expected_status_filters_on_it(mock_supabase_client):
    table = _wire_update_chain(mock_supabase_client, [{"id": "c1", "status": "closed"}])

    row = await corporate_repo.update_corporate_account_status("c1", "closed", expected_status="active")

    assert row == {"id": "c1", "status": "closed"}
    table.update.assert_called_with({"status": "closed"})
    assert table.eq.call_args_list == [call("id", "c1"), call("status", "active")]


@pytest.mark.anyio
async def test_status_update_cas_losing_path_returns_none(mock_supabase_client):
    """0 rows matched (another request already moved the status) → None, not a row."""
    _wire_update_chain(mock_supabase_client, [])

    row = await corporate_repo.update_corporate_account_status("c1", "closed", expected_status="active")

    assert row is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "patch_body",
    [
        {"balance": 999},
        {"auto_topup_enabled": False, "balance": 0},
        {"low_balance_notified_at": None},
        {"company_id": "c2"},
    ],
)
async def test_wallet_config_patch_rejects_non_config_columns(mock_supabase_client, patch_body):
    table = _wire_update_chain(mock_supabase_client, [{"id": "w1"}])

    with pytest.raises(ValueError, match="not wallet config"):
        await corporate_repo.update_corporate_wallet_config(wallet_id="w1", patch=patch_body)

    table.update.assert_not_called()


@pytest.mark.anyio
async def test_wallet_config_patch_accepts_every_allowed_column(mock_supabase_client):
    """Exactly the fields WalletConfigPatch (routes/corporate_wallet.py) can emit,
    plus the close-flow freeze literal — both existing callers keep working."""
    body = {
        "auto_topup_enabled": True,
        "auto_topup_threshold": 100.0,
        "auto_topup_amount": 500.0,
        "auto_topup_daily_cap": 5000.0,
    }
    table = _wire_update_chain(mock_supabase_client, [{"id": "w1", **body}])

    row = await corporate_repo.update_corporate_wallet_config(wallet_id="w1", patch=body)

    assert row == {"id": "w1", **body}
    table.update.assert_called_with(body)


def test_wallet_config_allow_list_matches_route_schema():
    """If WalletConfigPatch grows a field, the repo allow-list must be updated
    deliberately — this fails loudly instead of the route 500-ing at runtime."""
    from routes.corporate_wallet import WalletConfigPatch

    assert set(WalletConfigPatch.model_fields) <= corporate_repo._WALLET_CONFIG_PATCHABLE_COLUMNS
    assert "balance" not in corporate_repo._WALLET_CONFIG_PATCHABLE_COLUMNS
