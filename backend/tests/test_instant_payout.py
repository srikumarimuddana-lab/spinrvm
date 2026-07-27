"""Tests for instant-payout (Stripe Instant Pay) in routes/drivers.py.

Covers the fee math and the endpoint plumbing — Stripe is mocked.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request as StarletteRequest


def _req() -> StarletteRequest:
    """A real (header-less) request so @idempotent_endpoint opts out cleanly.
    A MagicMock here makes request.headers.get() return a Mock, which the
    decorator then sha256-hashes → TypeError. Empty headers → no Idempotency-Key
    → the decorator runs the handler normally."""
    return StarletteRequest(
        {"type": "http", "method": "POST", "path": "/drivers/payouts/instant", "query_string": b"", "headers": []}
    )


# ── Fee math ─────────────────────────────────────────────────────────────


class TestComputeInstantPayoutFee:
    def test_min_floor_applies_for_small_amounts(self):
        from backend.routes.drivers import INSTANT_PAYOUT_MIN_FEE, compute_instant_payout_fee

        # 1.5% of $5 = $0.075 — well below the $0.50 floor.
        assert compute_instant_payout_fee(Decimal("5.00")) == INSTANT_PAYOUT_MIN_FEE

    def test_percentage_applies_in_band(self):
        from backend.routes.drivers import compute_instant_payout_fee

        # 1.5% of $100 = $1.50, within floor/ceiling.
        assert compute_instant_payout_fee(Decimal("100.00")) == Decimal("1.50")

    def test_max_ceiling_applies_for_large_amounts(self):
        from backend.routes.drivers import INSTANT_PAYOUT_MAX_FEE, compute_instant_payout_fee

        # 1.5% of $5000 = $75 — capped at $15.
        assert compute_instant_payout_fee(Decimal("5000.00")) == INSTANT_PAYOUT_MAX_FEE

    def test_rounds_to_two_decimals(self):
        from backend.routes.drivers import compute_instant_payout_fee

        # 1.5% of $42.33 = $0.63495 → rounds to $0.63 (still above $0.50 floor)
        result = compute_instant_payout_fee(Decimal("42.33"))
        assert result == Decimal("0.63")


# ── Endpoint plumbing ────────────────────────────────────────────────────


USER_ID = "user_instant"
DRIVER_ID = "driver_instant"


def _driver(**extra):
    return {
        "id": DRIVER_ID,
        "user_id": USER_ID,
        "stripe_account_id": "acct_TEST",
        # GST/HST registration is a hard precondition for payout (CRA rideshare
        # rule). Default to a valid BN so eligibility tests reach the Stripe path;
        # override with gst_bn=None to exercise the block.
        "gst_bn": "123456789RT0001",
        # SIN-on-file is the other CRA precondition (enforced right after GST).
        # Default true so eligibility tests reach the Stripe path; override false
        # to exercise the SIN block.
        "stripe_id_number_provided": True,
        **extra,
    }


def _bank_account():
    return {
        "driver_id": DRIVER_ID,
        "bank_name": "Test Bank",
        "account_last4": "1234",
    }


class TestRequestInstantPayout:
    def _balance(self, payable: str = "200.00"):
        return {
            "payable_balance": payable,
            "pending_payouts": "0.00",
            "earned_today": "0.00",
        }

    def test_rejects_when_no_stripe_connect_account(self):
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver(stripe_account_id=None)]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=self._balance())),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req,
                        request=_req(),
                        current_user={"id": USER_ID},
                    )
                )
        assert exc.value.status_code == 400
        assert "Stripe Connect" in exc.value.detail

    def test_rejects_when_gst_not_registered(self):
        # CRA rideshare rule: no GST/HST Business Number on file → hard block,
        # before the Stripe eligibility / balance checks.
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver(gst_bn=None)]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        req = drv.InstantPayoutRequest(amount=Decimal("50.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=self._balance())),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req,
                        request=_req(),
                        current_user={"id": USER_ID},
                    )
                )
        assert exc.value.status_code == 422
        assert "gst" in exc.value.detail.lower()

    def test_rejects_when_insufficient_funds(self):
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        req = drv.InstantPayoutRequest(amount=Decimal("500.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch(
                "backend.routes.drivers.earnings.get_driver_balance",
                AsyncMock(return_value=self._balance(payable="100.00")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req,
                        request=_req(),
                        current_user={"id": USER_ID},
                    )
                )
        assert exc.value.status_code == 400
        assert "Insufficient" in exc.value.detail

    def test_happy_path_stores_fee_and_net_amount(self):
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        inserted: dict = {}
        updates: list = []

        async def fake_insert(_table, payload):
            inserted.update(payload)
            return payload

        async def fake_update(_table, _filters, fields):
            updates.append(dict(fields))
            return None

        req = drv.InstantPayoutRequest(amount=Decimal("100.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock(side_effect=fake_insert)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update)),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=self._balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_abc"}),
            ),
            patch("backend.routes.drivers._deps.stripe.Transfer.create", MagicMock(return_value=MagicMock(id="tr_x"))),
            patch(
                "backend.routes.drivers._deps.stripe.Payout.create",
                MagicMock(return_value=MagicMock(id="po_INSTANT")),
            ),
        ):
            result = asyncio.run(
                drv.request_instant_payout(
                    req=req,
                    request=_req(),
                    current_user={"id": USER_ID},
                )
            )

        assert result["success"] is True
        # WS-7 reserve-then-transfer (migration 250): the INSERT reserves the
        # row with status="reserved" and no Stripe IDs yet, BEFORE any Stripe
        # call, so a concurrent instant-payout request is blocked by the
        # partial unique index rather than racing past a balance check.
        assert inserted["amount"] == Decimal("100.00")
        assert inserted["fee"] == Decimal("1.50")
        assert inserted["net_amount"] == Decimal("98.50")
        assert inserted["payout_type"] == "instant"
        assert inserted["status"] == "reserved"
        assert inserted["stripe_transfer_id"] is None
        assert inserted["stripe_payout_id"] is None
        # First UPDATE (post-transfer): reserved -> transfer_completed,
        # carrying the Stripe transfer id.
        assert len(updates) >= 2, "Expected an UPDATE after the transfer and another after the payout step"
        transfer_update = updates[0]
        assert transfer_update["status"] == "transfer_completed"
        assert transfer_update["stripe_transfer_id"] == "tr_x"
        # Final UPDATE marks it completed with the payout id.
        final = updates[-1]
        assert final["status"] == "completed"
        assert final["stripe_payout_id"] == "po_INSTANT"

    def test_payout_step_failure_reverses_transfer_and_flags_row(self):
        """Transfer succeeds, Payout fails → reversal succeeds.

        Row should end in status='reversed', requires_manual_review=False.
        """
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        inserted: dict = {}
        updates: list = []

        async def fake_insert(_table, payload):
            inserted.update(payload)
            return payload

        async def fake_update(_table, _filters, fields):
            updates.append(dict(fields))
            return None

        req = drv.InstantPayoutRequest(amount=Decimal("100.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock(side_effect=fake_insert)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update)),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=self._balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_abc"}),
            ),
            patch("backend.routes.drivers._deps.stripe.Transfer.create", MagicMock(return_value=MagicMock(id="tr_x"))),
            patch(
                "backend.routes.drivers._deps.stripe.Payout.create",
                MagicMock(side_effect=Exception("instant payout temporarily unavailable")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(return_value=MagicMock(id="trr_ok")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(
                    drv.request_instant_payout(
                        req=req,
                        request=_req(),
                        current_user={"id": USER_ID},
                    )
                )
        assert exc.value.status_code == 500
        # The row is reserved (no Stripe IDs yet) at INSERT time; the
        # transfer id is only persisted by the first UPDATE, after the
        # Stripe Transfer call succeeds.
        assert inserted["status"] == "reserved"
        assert inserted["stripe_transfer_id"] is None
        assert len(updates) >= 2, "Expected an UPDATE after the transfer and another after the payout failure"
        transfer_update = updates[0]
        assert transfer_update["status"] == "transfer_completed"
        assert transfer_update["stripe_transfer_id"] == "tr_x"
        # And the failure path flags the row.
        final = updates[-1]
        assert final["status"] == "reversed"
        assert final["requires_manual_review"] is False
        assert "instant payout temporarily unavailable" in (final.get("failure_reason") or "")

    def test_payout_and_reversal_both_fail_flags_stranded(self):
        """Transfer succeeds, Payout fails, reversal also fails.

        Row should end in status='stranded', requires_manual_review=True
        so the ops dashboard surfaces it.
        """
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        inserted: dict = {}
        updates: list = []

        async def fake_insert(_table, payload):
            inserted.update(payload)
            return payload

        async def fake_update(_table, _filters, fields):
            updates.append(dict(fields))
            return None

        req = drv.InstantPayoutRequest(amount=Decimal("100.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock(side_effect=fake_insert)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update)),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=self._balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_abc"}),
            ),
            patch("backend.routes.drivers._deps.stripe.Transfer.create", MagicMock(return_value=MagicMock(id="tr_x"))),
            patch(
                "backend.routes.drivers._deps.stripe.Payout.create",
                MagicMock(side_effect=Exception("bank network down")),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create_reversal",
                MagicMock(side_effect=Exception("transfer already settled")),
            ),
        ):
            with pytest.raises(HTTPException):
                asyncio.run(
                    drv.request_instant_payout(
                        req=req,
                        request=_req(),
                        current_user={"id": USER_ID},
                    )
                )

        assert updates, "Expected an UPDATE after the payout failure"
        final = updates[-1]
        assert final["status"] == "stranded"
        assert final["requires_manual_review"] is True

    def test_transfer_failure_does_not_persist_or_reverse(self):
        """Step-1 transfer fails. No money moved → no reversal needed, but
        WS-7's reserve-then-transfer (migration 250) means the row was
        already inserted (status='reserved') BEFORE the Stripe call, and is
        then marked 'failed' rather than reversed (there was nothing to
        reverse)."""
        from backend.routes import drivers as drv

        def get_rows_side_effect(table, filters=None, **kw):
            if table == "drivers":
                return [_driver()]
            if table == "bank_accounts":
                return [_bank_account()]
            return []

        insert_calls: list = []
        update_calls: list = []
        reversal_calls: list = []

        async def fake_insert(_table, payload):
            insert_calls.append(payload)
            return payload

        async def fake_update(_table, _filters, fields):
            update_calls.append(dict(fields))
            return None

        def fake_reversal(*args, **kw):
            reversal_calls.append((args, kw))
            return MagicMock(id="trr_should_not_happen")

        req = drv.InstantPayoutRequest(amount=Decimal("100.00"))

        with (
            patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=get_rows_side_effect)),
            patch("backend.routes.drivers._deps.db_supabase.insert_one", AsyncMock(side_effect=fake_insert)),
            patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=fake_update)),
            patch("backend.routes.drivers.earnings.get_driver_balance", AsyncMock(return_value=self._balance())),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"stripe_secret_key": "sk_test_abc"}),
            ),
            patch(
                "backend.routes.drivers._deps.stripe.Transfer.create",
                MagicMock(side_effect=Exception("connect account closed")),
            ),
            patch("backend.routes.drivers._deps.stripe.Transfer.create_reversal", MagicMock(side_effect=fake_reversal)),
        ):
            with pytest.raises(HTTPException):
                asyncio.run(
                    drv.request_instant_payout(
                        req=req,
                        request=_req(),
                        current_user={"id": USER_ID},
                    )
                )
        # Reserved before the Stripe call, per WS-7.
        assert len(insert_calls) == 1
        assert insert_calls[0]["status"] == "reserved"
        assert insert_calls[0]["stripe_transfer_id"] is None
        # No money moved → the row is marked failed, not reversed.
        assert update_calls, "Expected the reserved row to be marked failed"
        assert update_calls[-1]["status"] == "failed"
        assert reversal_calls == []


class TestInstantPayoutQuote:
    def test_returns_fee_and_net(self):
        from backend.routes import drivers as drv

        result = asyncio.run(
            drv.get_instant_payout_quote(
                amount=Decimal("50.00"),
                current_user={"id": USER_ID},
            )
        )
        assert result["amount"] == "50.00"
        # 1.5% of 50 = 0.75 (above 0.50 floor)
        assert result["fee"] == "0.75"
        assert result["net_amount"] == "49.25"
        assert result["payout_type"] == "instant"
