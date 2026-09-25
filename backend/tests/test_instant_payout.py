"""Instant payout is retired: Spinr pays drivers weekly only (owner decision
2026-09-25). POST /drivers/payouts/instant and GET /drivers/payouts/instant/quote
stay mounted so old or scripted clients get a clear 410, and they must answer
before any database or Stripe call.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

GONE_DETAIL = "Instant payouts are not offered. Earnings are paid automatically every week."
USER = {"id": "user_instant", "role": "driver"}


def _patch_money_and_db(stack: ExitStack) -> dict:
    """Patch every DB helper and Stripe call the old handler used, so a test
    can assert none of them ran."""
    mocks = {}
    for name in ("get_rows", "find_one", "insert_one", "update_one"):
        mocks[name] = stack.enter_context(patch(f"backend.routes.drivers._deps.db_supabase.{name}", AsyncMock()))
    mocks["transfer"] = stack.enter_context(patch("backend.routes.drivers._deps.stripe.Transfer.create", MagicMock()))
    mocks["reversal"] = stack.enter_context(
        patch("backend.routes.drivers._deps.stripe.Transfer.create_reversal", MagicMock())
    )
    mocks["payout"] = stack.enter_context(patch("backend.routes.drivers._deps.stripe.Payout.create", MagicMock()))
    mocks["balance"] = stack.enter_context(patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock()))
    mocks["settings"] = stack.enter_context(patch("settings_loader.get_app_settings", AsyncMock()))
    return mocks


def _assert_nothing_called(mocks: dict) -> None:
    for name, mock in mocks.items():
        assert not mock.called, f"{name} must not be called by a retired instant-payout route"


class TestHandlersReturn410:
    def test_request_instant_payout_is_gone(self):
        from backend.routes import drivers as drv

        with ExitStack() as stack:
            mocks = _patch_money_and_db(stack)
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.request_instant_payout(current_user=USER))
        assert exc.value.status_code == 410
        assert exc.value.detail == GONE_DETAIL
        _assert_nothing_called(mocks)

    def test_quote_is_gone(self):
        from backend.routes import drivers as drv

        with ExitStack() as stack:
            mocks = _patch_money_and_db(stack)
            with pytest.raises(HTTPException) as exc:
                asyncio.run(drv.get_instant_payout_quote(current_user=USER))
        assert exc.value.status_code == 410
        assert exc.value.detail == GONE_DETAIL
        _assert_nothing_called(mocks)


class TestHttpLayer:
    """Through a real router: an old client's body/query must still get the
    410, not a 422 from a removed request model."""

    @pytest.fixture
    def client(self):
        from backend.routes.drivers import payouts

        app = FastAPI()
        app.include_router(payouts.router, prefix="/drivers")
        app.dependency_overrides[payouts.get_current_user] = lambda: USER
        return TestClient(app)

    def test_post_with_old_body_gets_410(self, client):
        with ExitStack() as stack:
            mocks = _patch_money_and_db(stack)
            res = client.post("/drivers/payouts/instant", json={"amount": "50.00"})
        assert res.status_code == 410
        assert res.json()["detail"] == GONE_DETAIL
        _assert_nothing_called(mocks)

    def test_post_without_body_gets_410(self, client):
        res = client.post("/drivers/payouts/instant")
        assert res.status_code == 410

    @pytest.mark.parametrize("query", ["?amount=50.00", "", "?amount=1"])
    def test_quote_gets_410_for_any_query(self, client, query):
        with ExitStack() as stack:
            mocks = _patch_money_and_db(stack)
            res = client.get(f"/drivers/payouts/instant/quote{query}")
        assert res.status_code == 410
        assert res.json()["detail"] == GONE_DETAIL
        _assert_nothing_called(mocks)

    def test_unauthenticated_caller_is_still_rejected_by_auth(self):
        from backend.routes.drivers import payouts

        app = FastAPI()
        app.include_router(payouts.router, prefix="/drivers")
        res = TestClient(app).post("/drivers/payouts/instant")
        assert res.status_code in (401, 403)


class TestHelpersRemoved:
    @pytest.mark.parametrize(
        "name",
        [
            "INSTANT_PAYOUT_FEE_PCT",
            "INSTANT_PAYOUT_MIN_FEE",
            "INSTANT_PAYOUT_MAX_FEE",
            "compute_instant_payout_fee",
            "InstantPayoutRequest",
            "_require_instant_payout_enabled",
            "_require_within_instant_payout_daily_cap",
            "_instant_cap_day_start_utc",
        ],
    )
    def test_instant_helper_is_gone(self, name):
        from backend.routes.drivers import payouts

        assert not hasattr(payouts, name)

    def test_transfer_reversal_kept_for_legacy_standard_path(self):
        from backend.routes.drivers import payouts

        assert callable(payouts._attempt_transfer_reversal)
