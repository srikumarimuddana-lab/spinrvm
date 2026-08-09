"""Driver + admin endpoints for the connected-account ledger.

The driver-facing reads exist so a driver's bank payouts and full ledger come
from OUR tables, not from Stripe on every screen view — the history stays
available when Stripe is slow, survives their account being superseded, and
costs no API quota.

The contract these tests pin:
  * both endpoints are driver-scoped (never another driver's money);
  * ledger amounts stay SIGNED and are flagged as such, so a client cannot
    mistake the feed for an earnings total;
  * the admin sync is super_admin only and is audited with a non-null
    entity_id (audit_logs.entity_id is NOT NULL and the logger swallows its
    own failures, so a None there goes silently unaudited).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.routes.admin.stripe_connect_ledger import (
    ConnectLedgerSyncRequest,
    _year_window,
    sync_connect_ledger,
)
from backend.routes.drivers.payouts import get_bank_payout_history, get_connect_ledger
from backend.services.stripe_connect_ledger_service import ConnectLedgerSyncResult

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

USER_ID = "user_led_1"
DRIVER_ID = "drv_led_1"
SUPER = {"id": "adm_1", "role": "super_admin"}
PLAIN = {"id": "adm_2", "role": "admin"}


def _patch_rows(rows, driver=True):
    """Patch the shared db binding: first call resolves the driver, second the rows."""
    seq = [[{"id": DRIVER_ID}] if driver else [], rows]

    async def _get_rows(table, filters=None, **kw):
        return seq.pop(0) if seq else []

    return patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows)


class TestBankPayoutHistory:
    async def test_returns_the_drivers_bank_payouts(self):
        row = {
            "id": "po_1",
            "amount": "250.00",
            "currency": "cad",
            "status": "paid",
            "method": "standard",
            "arrival_date": "2025-01-02T00:00:00+00:00",
            "failure_code": None,
            "failure_message": None,
            "bank_last4": "6789",
            "created_at": "2025-01-01T00:00:00+00:00",
        }
        with _patch_rows([row]):
            resp = await get_bank_payout_history(limit=20, offset=0, current_user={"id": USER_ID})

        assert resp["success"] is True
        payout = resp["bank_payouts"][0]
        assert payout["id"] == "po_1"
        assert payout["amount"] == "250.00"
        assert payout["status"] == "paid"
        # The two fields a driver actually asks about when money is late.
        assert payout["arrival_date"] == "2025-01-02T00:00:00+00:00"
        assert "failure_message" in payout

    async def test_failure_reason_is_surfaced(self):
        row = {
            "id": "po_2",
            "amount": "100.00",
            "status": "failed",
            "failure_code": "account_closed",
            "failure_message": "The bank account has been closed",
            "created_at": "2025-02-01T00:00:00+00:00",
        }
        with _patch_rows([row]):
            resp = await get_bank_payout_history(limit=20, offset=0, current_user={"id": USER_ID})
        assert resp["bank_payouts"][0]["failure_code"] == "account_closed"

    async def test_unknown_driver_404s(self):
        with _patch_rows([], driver=False):
            with pytest.raises(HTTPException) as ei:
                await get_bank_payout_history(limit=20, offset=0, current_user={"id": USER_ID})
        assert ei.value.status_code == 404

    async def test_query_is_scoped_to_this_driver(self):
        seen: list = []

        async def _get_rows(table, filters=None, **kw):
            seen.append((table, filters))
            return [{"id": DRIVER_ID}] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows):
            await get_bank_payout_history(limit=20, offset=0, current_user={"id": USER_ID})

        table, filters = seen[-1]
        assert table == "driver_stripe_payouts"
        assert filters == {"driver_id": DRIVER_ID}


class TestConnectLedger:
    async def test_entries_are_flagged_signed(self):
        """The client must render a statement, not sum it as earnings."""
        rows = [
            {
                "id": "txn_in",
                "type": "transfer",
                "amount": "250.00",
                "fee": "0.00",
                "net": "250.00",
                "created_at": "2025-01-01T00:00:00+00:00",
            },
            {
                "id": "txn_out",
                "type": "payout",
                "amount": "-250.00",
                "fee": "0.00",
                "net": "-250.00",
                "created_at": "2025-01-02T00:00:00+00:00",
            },
        ]
        with _patch_rows(rows):
            resp = await get_connect_ledger(limit=50, offset=0, entry_type=None, current_user={"id": USER_ID})

        assert resp["signed_amounts"] is True
        amounts = [e["amount"] for e in resp["entries"]]
        assert amounts == ["250.00", "-250.00"]

    async def test_type_filter_is_applied(self):
        seen: list = []

        async def _get_rows(table, filters=None, **kw):
            seen.append((table, filters))
            return [{"id": DRIVER_ID}] if table == "drivers" else []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", side_effect=_get_rows):
            await get_connect_ledger(limit=50, offset=0, entry_type="payout", current_user={"id": USER_ID})

        table, filters = seen[-1]
        assert table == "driver_stripe_ledger"
        assert filters == {"driver_id": DRIVER_ID, "type": "payout"}

    async def test_unknown_driver_404s(self):
        with _patch_rows([], driver=False):
            with pytest.raises(HTTPException) as ei:
                await get_connect_ledger(limit=50, offset=0, entry_type=None, current_user={"id": USER_ID})
        assert ei.value.status_code == 404


class TestAdminSync:
    async def test_requires_super_admin(self):
        with pytest.raises(HTTPException) as ei:
            await sync_connect_ledger(ConnectLedgerSyncRequest(), admin=PLAIN)
        assert ei.value.status_code == 403

    async def test_unconfigured_stripe_is_503(self):
        with patch(
            "backend.routes.admin.stripe_connect_ledger.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": ""}),
        ):
            with pytest.raises(HTTPException) as ei:
                await sync_connect_ledger(ConnectLedgerSyncRequest(), admin=SUPER)
        assert ei.value.status_code == 503

    async def test_reports_counts_and_audits_with_a_non_null_entity_id(self):
        result = ConnectLedgerSyncResult(payouts_upserted=3, ledger_upserted=9, drivers_synced=2, accounts_read=2)
        audit = AsyncMock()
        with (
            patch(
                "backend.routes.admin.stripe_connect_ledger.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
            ),
            patch(
                "backend.routes.admin.stripe_connect_ledger.ledger_svc.sync_connect_ledger",
                AsyncMock(return_value=result),
            ),
            patch("backend.routes.admin.stripe_connect_ledger.log_admin_action", audit),
        ):
            resp = await sync_connect_ledger(ConnectLedgerSyncRequest(year=2025), admin=SUPER)

        assert (resp["payouts_upserted"], resp["ledger_upserted"]) == (3, 9)
        # audit_logs.entity_id is NOT NULL; a None here would be swallowed and
        # the sync would run unaudited.
        assert audit.await_args.args[3] == "year:2025"

    async def test_year_window_bounds_a_calendar_year_utc(self):
        gte, lte = _year_window(2025)
        assert (gte, lte) == (1735689600, 1767225599)

    def test_no_year_means_full_history(self):
        assert _year_window(None) == (None, None)

    def test_unknown_fields_are_rejected(self):
        with pytest.raises(ValueError):
            ConnectLedgerSyncRequest(driver_idz=["x"])
