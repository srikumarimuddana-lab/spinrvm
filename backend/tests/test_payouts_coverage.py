"""Coverage-closure tests for routes/drivers/payouts.py (A1c Sub-tier A).

Scoped to close the branches NOT already pinned by:
  - test_p2_payout_t4a.py (standard request_payout no-Stripe happy path,
    insufficient funds, no bank account, GST gates, driver-not-found,
    get_payout_history)
  - test_instant_payout.py (instant payout fee math, GST/SIN gates, no-Connect
    guard, insufficient funds, happy path, payout-step failure + reversal
    success/failure, transfer-step failure)
  - test_drivers_extended.py (Stripe hosted/embedded onboarding happy paths,
    stripe-sync happy paths, bank-account read happy paths, SIN gate)
  - test_payout_toctou.py (static source-text assertions on the reserve-
    then-transfer ordering — no runtime coverage)

This file focuses on:
  - request_payout: the WITH-Stripe transfer branch (success, transfer
    failure, reserve-insert conflict/error, terminal-write failure with and
    without a Stripe reversal)
  - request_instant_payout: fee-exceeds-amount guard, no-stripe-secret 503,
    reserve-insert conflict/error, the transfer_completed persist-failure
    branch, and the "both the primary write AND the compensating write fail"
    double-failure branches
  - get_bank_account / save_bank_account / delete_bank_account gaps
  - _ensure_stripe_account: new-account creation + persist-failure branch
  - onboard_stripe / stripe_sync_status / stripe_account_session: not-found
    and exception-handling branches

Patch-target conventions (see routes/drivers/_deps.py + CLAUDE.md, matching
test_subscriptions_coverage.py / test_instant_payout.py):
  - `db_supabase` is a *module reference* shared by every importer, so
    `patch("backend.db_supabase.<fn>")` affects both `db_supabase.<fn>(...)`
    call sites in payouts.py.
  - `stripe` is the real third-party module imported once in _deps.py and
    re-bound into payouts.py's own namespace; patching
    `backend.routes.drivers._deps.stripe.<Resource>.<method>` (the
    convention used by test_instant_payout.py) reaches the same object
    payouts.py calls.
  - `earnings.get_driver_balance` is called via `from . import earnings` (a
    submodule reference), so patch `backend.routes.drivers.earnings.get_driver_balance`.
  - `@idempotent_endpoint` needs a real Starlette Request with no headers so
    it no-ops (a MagicMock's `.headers.get()` returns a Mock, which the
    decorator then tries to sha256-hash -> TypeError).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest

USER_ID = "user_payouts_cov"
DRIVER_ID = "driver_payouts_cov"


def _req(path: str = "/drivers/payouts") -> StarletteRequest:
    return StarletteRequest({"type": "http", "method": "POST", "path": path, "query_string": b"", "headers": []})


def _driver(**extra) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": USER_ID,
        "stripe_account_id": None,
        "gst_bn": "123456789RT0001",
        "stripe_id_number_provided": True,
        **extra,
    }


def _bank_account() -> dict:
    return {"id": "bank-1", "driver_id": DRIVER_ID, "bank_name": "Test Bank", "account_last4": "1234"}


def _balance(payable: str = "500.00") -> dict:
    return {"payable_balance": payable}


# ============================================================
# get_bank_account
# ============================================================


class TestGetBankAccountGaps:
    def test_driver_not_found_raises_404(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_bank_account(current_user={"id": "ghost"}))
        assert exc.value.status_code == 404

    def test_no_bank_row_but_stripe_onboarded_returns_placeholder(self):
        from backend.routes import drivers as drv

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver(stripe_account_onboarded=True)]
            if table == "bank_accounts":
                return []
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = asyncio.run(drv.get_bank_account(current_user={"id": USER_ID}))

        assert result["has_bank_account"] is True
        assert result["bank_account"]["bank_name"] == "Stripe Connect"

    def test_no_bank_row_no_stripe_returns_false(self):
        from backend.routes import drivers as drv

        def get_rows(table, filters=None, **kw):
            if table == "drivers":
                return [_driver(stripe_account_onboarded=False)]
            return []

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows)):
            result = asyncio.run(drv.get_bank_account(current_user={"id": USER_ID}))

        assert result == {"has_bank_account": False, "bank_account": None}


# ============================================================
# _ensure_stripe_account (via onboard_stripe, since it's only ever called
# internally)
# ============================================================


class TestEnsureStripeAccountCreation:
    def test_creates_account_and_persists_id(self):
        from backend.routes import drivers as drv

        update_mock = AsyncMock()
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id=None)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.update_one", update_mock),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
                create=True,
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Account.create",
                MagicMock(return_value=MagicMock(id="acct_NEW")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.AccountLink.create",
                MagicMock(return_value=MagicMock(url="https://connect.stripe.com/setup/new")),
            ) as account_link_create,
        ):
            result = asyncio.run(drv.onboard_stripe(current_user={"id": USER_ID}))

        assert result["mock"] is False
        # The Stripe mode is stamped alongside the id so a later test→live key
        # rotation is detectable without a Stripe round-trip (migration 286).
        # The mocked Account has no real `livemode` bool, so the stamp falls
        # back to the key's mode — sk_test_x → "test".
        update_mock.assert_awaited_once_with(
            "drivers",
            {"id": DRIVER_ID},
            {"stripe_account_id": "acct_NEW", "stripe_account_id_mode": "test"},
        )
        assert account_link_create.call_args.kwargs["account"] == "acct_NEW"

    def test_persist_failure_raises_502_and_is_preserved_by_onboard(self):
        """update_one failing after Account.create must never strand the
        driver on an unpersisted account id -> 502, and onboard_stripe's
        `except HTTPException: raise` must pass it through untouched
        (not remapped to a generic 500)."""
        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id=None)]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=Exception("db write failed")),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
                create=True,
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Account.create",
                MagicMock(return_value=MagicMock(id="acct_NEW")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.onboard_stripe(current_user={"id": USER_ID}))

        assert exc.value.status_code == 502


# ============================================================
# onboard_stripe: not-found + exception branches
# ============================================================


class TestOnboardStripeGaps:
    def test_driver_or_user_not_found_404(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.onboard_stripe(current_user={"id": "ghost"}))
        assert exc.value.status_code == 404

    def test_no_stripe_secret_returns_mock_url(self):
        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id="acct_1")]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={}), create=True),
        ):
            result = asyncio.run(drv.onboard_stripe(current_user={"id": USER_ID}))

        assert result == {"url": "https://spinr-demo-onboard.com", "mock": True}

    def test_generic_exception_returns_500(self):
        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id="acct_1")]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
                create=True,
            ),
            patch(
                "backend.routes.drivers._deps.stripe.AccountLink.create",
                MagicMock(side_effect=Exception("stripe unreachable")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.onboard_stripe(current_user={"id": USER_ID}))

        assert exc.value.status_code == 500
        assert "internal error" in exc.value.detail.lower()


# ============================================================
# stripe_sync_status: not-found branch
# ============================================================


class TestStripeSyncStatusGaps:
    def test_driver_not_found_404(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.stripe_sync_status(current_user={"id": "ghost"}))
        assert exc.value.status_code == 404


# ============================================================
# stripe_account_session: not-found + exception branches
# ============================================================


class TestStripeAccountSessionGaps:
    def test_driver_or_user_not_found_404(self):
        from backend.routes import drivers as drv

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.drivers._deps.db_supabase.get_user_by_id", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.stripe_account_session(current_user={"id": "ghost"}))
        assert exc.value.status_code == 404

    def test_generic_exception_returns_502(self):
        from backend.routes import drivers as drv

        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver(stripe_account_id="acct_1")]),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"id": USER_ID, "email": "drv@example.com"}),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_x"}),
                create=True,
            ),
            patch(
                "backend.routes.drivers._deps.stripe.AccountSession.create",
                MagicMock(side_effect=Exception("stripe down")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.stripe_account_session(current_user={"id": USER_ID}))

        assert exc.value.status_code == 502


# ============================================================
# save_bank_account / delete_bank_account (previously fully untested)
# ============================================================


class TestSaveBankAccount:
    def test_driver_not_found_404(self):
        from backend.routes import drivers as drv

        req = drv.BankAccountCreate(
            bank_name="Test Bank",
            institution_number="1",
            transit_number="12345",
            account_number="00012345",
            account_holder_name="Sam Driver",
        )
        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.save_bank_account(req, current_user={"id": "ghost"}))
        assert exc.value.status_code == 404

    def test_success_replaces_existing_and_serializes(self):
        from backend.routes import drivers as drv

        req = drv.BankAccountCreate(
            bank_name="Test Bank",
            institution_number="1",  # zfill(3) -> "001"
            transit_number="12345",
            account_number="000098765432",
            account_holder_name="Sam Driver",
            account_type="savings",
        )
        delete_mock = AsyncMock()
        insert_mock = AsyncMock()
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver()]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.delete_many", delete_mock),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", insert_mock),
        ):
            result = asyncio.run(drv.save_bank_account(req, current_user={"id": USER_ID}))

        assert result["success"] is True
        delete_mock.assert_awaited_once_with("bank_accounts", {"driver_id": DRIVER_ID})
        insert_mock.assert_awaited_once()
        inserted_row = insert_mock.await_args.args[1]
        assert inserted_row["driver_id"] == DRIVER_ID
        assert inserted_row["routing_number"] == "0001" + "12345"
        assert inserted_row["account_last4"] == "5432"
        assert inserted_row["currency"] == "cad"
        assert inserted_row["country"] == "CA"
        assert inserted_row["is_verified"] is False
        assert "account_number" not in inserted_row
        assert result["bank_account"]["account_last4"] == "5432"

    def test_short_account_number_uses_whole_value_as_last4(self):
        from backend.routes import drivers as drv

        req = drv.BankAccountCreate(
            bank_name="Test Bank",
            institution_number="001",
            transit_number="00012",
            account_number="12",  # < 4 digits
            account_holder_name="Sam Driver",
        )
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver()]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.delete_many", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()) as insert_mock,
        ):
            result = asyncio.run(drv.save_bank_account(req, current_user={"id": USER_ID}))

        assert result["bank_account"]["account_last4"] == "12"
        assert insert_mock.await_args.args[1]["account_last4"] == "12"


class TestDeleteBankAccount:
    def test_driver_not_found_404(self):
        from backend.routes import drivers as drv

        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.delete_bank_account(current_user={"id": "ghost"}))
        assert exc.value.status_code == 404

    def test_success_deletes_bank_accounts_for_driver(self):
        from backend.routes import drivers as drv

        delete_mock = AsyncMock()
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(return_value=[_driver()]),
            ),
            patch("backend.routes.drivers._deps.db_supabase.delete_many", delete_mock),
        ):
            result = asyncio.run(drv.delete_bank_account(current_user={"id": USER_ID}))

        assert result == {"success": True}
        delete_mock.assert_awaited_once_with("bank_accounts", {"driver_id": DRIVER_ID})


# ============================================================
# _request_payout_legacy: the WITH-Stripe branch (untested by
# test_p2_payout_t4a.py, which only exercises the no-Stripe-key "pending"
# fallback). POST /payouts itself is now a 410 stub (weekly auto-payouts);
# the original logic is preserved at _request_payout_legacy behind
# _STANDARD_CASHOUT_DISABLED for rollback — these tests pin that preserved
# path, so the fixture below runs them with the flag off.
# ============================================================


class TestRequestPayoutStripeBranch:
    @pytest.fixture(autouse=True)
    def _enable_legacy_cashout(self):
        with patch("backend.routes.drivers.payouts._STANDARD_CASHOUT_DISABLED", False):
            yield

    def _get_rows(self, driver, account=None):
        def side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            if table == "bank_accounts":
                return [account] if account else []
            return []

        return side_effect

    def test_stripe_transfer_success_marks_completed(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock()) as update_mock,
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_std_1")),
            ),
        ):
            result = asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert result["success"] is True
        assert result["payout"]["status"] == drv.RideStatus.COMPLETED
        assert result["payout"]["stripe_payout_id"] == "tr_std_1"
        terminal_call = update_mock.await_args_list[-1]
        assert terminal_call.args[2]["status"] == drv.RideStatus.COMPLETED
        assert terminal_call.args[2]["stripe_payout_id"] == "tr_std_1"

    def test_stripe_transfer_failure_marks_row_failed(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock()) as update_mock,
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(side_effect=Exception("card issuer declined")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 500
        update_mock.assert_awaited_once()
        assert update_mock.await_args.args[2]["status"] == "failed"
        assert "card issuer declined" in update_mock.await_args.args[2]["failure_reason"]

    def test_stripe_transfer_failure_and_mark_failed_also_raises_is_swallowed(self):
        """Both the Stripe transfer AND the follow-up 'mark as failed' write
        fail. The inner except must swallow the second failure (log only) and
        still surface the original transfer failure as a clean 500 — not an
        unhandled exception."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=Exception("db unreachable")),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(side_effect=Exception("card issuer declined")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 500

    def test_reserve_insert_duplicate_returns_409(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.insert_one",
                AsyncMock(side_effect=Exception("duplicate key value violates unique constraint")),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 409
        assert "already in progress" in exc.value.detail.lower()

    def test_reserve_insert_generic_failure_returns_500(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.insert_one",
                AsyncMock(side_effect=Exception("network timeout")),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 500

    def test_terminal_write_failure_with_stripe_reverses_successfully(self):
        """Stripe transfer succeeds; the terminal DB write fails. Must
        attempt (and here, succeed at) reversing the transfer, mark the row
        'reversed', and surface a clean 500 to the caller."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=[Exception("terminal write failed"), None]),
            ) as update_mock,
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_std_2")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(return_value=MagicMock(id="trr_ok")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 500
        assert update_mock.await_count == 2
        status_call = update_mock.await_args_list[-1]
        assert status_call.args[2]["status"] == "reversed"
        assert status_call.args[2]["requires_manual_review"] is False

    def test_terminal_write_failure_with_stripe_reversal_also_fails_marks_stranded(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=[Exception("terminal write failed"), None]),
            ) as update_mock,
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_std_3")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(side_effect=Exception("reversal window closed")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 500
        status_call = update_mock.await_args_list[-1]
        assert status_call.args[2]["status"] == "stranded"
        assert status_call.args[2]["requires_manual_review"] is True

    def test_terminal_write_failure_without_stripe_skips_reversal(self):
        """No Stripe account linked (pending/no-stripe path) -> stripe_payout_id
        stays None, so a terminal-write failure must raise straight through
        without attempting a transfer reversal (nothing was ever moved)."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id=None)
        req = drv.PayoutRequest(amount=Decimal("75.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=Exception("terminal write failed")),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
            patch("backend.routes.drivers._deps.stripe.Transfer.create_reversal") as reversal_mock,
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv._request_payout_legacy(req=req, request=_req(), current_user={"id": USER_ID}))

        assert exc.value.status_code == 500
        reversal_mock.assert_not_called()


# ============================================================
# request_instant_payout: gaps not covered by test_instant_payout.py
# ============================================================


class TestRequestInstantPayoutGaps:
    def _get_rows(self, driver, account=None):
        def side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [driver]
            if table == "bank_accounts":
                return [account] if account else []
            return []

        return side_effect

    def test_driver_not_found_404(self):
        from backend.routes import drivers as drv

        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": "ghost"}
                    )
                )
        assert exc.value.status_code == 404

    def test_fee_exceeds_amount_returns_400(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("5.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch(
                "backend.routes.drivers.payouts.compute_instant_payout_fee",
                MagicMock(return_value=Decimal("10.00")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 400
        assert "fee exceeds" in exc.value.detail.lower()

    def test_no_stripe_secret_returns_503(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch("backend.settings_loader.get_app_settings", AsyncMock(return_value={})),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 503

    def test_reserve_insert_duplicate_returns_409(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.insert_one",
                AsyncMock(side_effect=Exception("duplicate key value violates unique constraint '23505'")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 409

    def test_reserve_insert_generic_failure_returns_500(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch(
                "backend.routes.drivers._deps.db_supabase.insert_one",
                AsyncMock(side_effect=Exception("network timeout")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 500

    def test_transfer_failure_and_mark_failed_write_also_fails_is_swallowed(self):
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=Exception("db also down")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(side_effect=Exception("connect account restricted")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 500

    def test_persist_transfer_completed_failure_reverses_successfully(self):
        """Transfer succeeds; the reserved->transfer_completed write fails
        before the Payout step even runs. Must reverse the transfer and mark
        the row 'reversed'."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=[Exception("persist failed"), None]),
            ) as update_mock,
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_ip_1")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(return_value=MagicMock(id="trr_ok")),
            ),
            patch("backend.routes.drivers._deps.stripe.Payout.create") as payout_create,
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )

        assert exc.value.status_code == 500
        payout_create.assert_not_called()  # never reached step 2
        status_call = update_mock.await_args_list[-1]
        assert status_call.args[2]["status"] == "reversed"

    def test_persist_transfer_completed_failure_reversal_also_fails_and_status_write_fails(self):
        """Triple failure: the transfer_completed persist fails, the
        compensating reversal fails, AND the follow-up status write (marking
        the row 'stranded') also fails. Must not raise an unhandled
        exception -- degrades to the loud STRANDED log line and still
        surfaces the original 500 to the caller."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=Exception("db entirely down")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_ip_2")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(side_effect=Exception("reversal also failed")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 500

    def test_payout_step_failure_flag_update_also_fails_is_swallowed(self):
        """Payout.create fails, reversal succeeds, but the follow-up write
        flagging the row also fails -> must be swallowed (logged), not
        raised, and the original 500 from the payout failure still surfaces."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=[None, Exception("flag write failed")]),
            ) as update_mock,
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_ip_3")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Payout.create",
                MagicMock(side_effect=Exception("insufficient connect balance")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(return_value=MagicMock(id="trr_ok")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                    )
                )
        assert exc.value.status_code == 500
        assert update_mock.await_count == 2

    def test_final_completed_write_failure_is_swallowed_money_already_disbursed(self):
        """Step 3 (mark row completed) fails after the driver already has
        the money -- must not unwind or raise; the endpoint still returns
        success to the caller."""
        from backend.routes import drivers as drv

        driver = _driver(stripe_account_id="acct_1")
        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))
        with (
            patch(
                "backend.routes.drivers._deps.db_supabase.get_rows",
                AsyncMock(side_effect=self._get_rows(driver, _bank_account())),
            ),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=_balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test"}),
            ),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock()),
            patch(
                "backend.routes.drivers._deps.db_supabase.update_one",
                AsyncMock(side_effect=[None, Exception("final flip failed")]),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(return_value=MagicMock(id="tr_ip_4")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Payout.create",
                MagicMock(return_value=MagicMock(id="po_ip_4")),
            ),
        ):
            result = asyncio.run(
                drv.request_instant_payout(
                    req=req, request=_req("/drivers/payouts/instant"), current_user={"id": USER_ID}
                )
            )

        assert result["success"] is True
        assert result["payout"]["status"] == drv.RideStatus.COMPLETED
        assert result["payout"]["stripe_payout_id"] == "po_ip_4"
