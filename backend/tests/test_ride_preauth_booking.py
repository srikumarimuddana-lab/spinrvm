"""
Unit tests for the booking-time card pre-authorization helper
backend/routes/rides.py::_preauthorize_ride_card.

The helper places a buffered manual-capture hold (grand_total +
RIDE_AUTH_BUFFER_CAD) at booking and decides, per Stripe outcome, whether to:
  - persist the hold (authorized / fare_only),
  - block the booking with 402 (genuine decline), or
  - degrade to "no hold, proceed" (SCA, ops error, unconfigured, no card).

`authorize_ride` is patched on the bound name in routes.rides so no Stripe is
touched. ChargeOutcome is the real dataclass.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

_BASE = dict(
    ride_id="ride_book_1",
    rider_id="rider_book_1",
    grand_total=Decimal("25.00"),
    stripe_customer_id="cus_book",
    payment_method_id="pm_book",
)


def _outcome(**kw):
    from backend.utils.stripe_charge import ChargeOutcome

    return ChargeOutcome(**kw)


def _patch_authorize(*returns):
    """Patch routes.rides.authorize_ride with a sequence of ChargeOutcomes
    (one per call — buffered hold, then optional fare-only retry)."""
    return patch(
        "backend.routes.rides.authorize_ride",
        AsyncMock(side_effect=list(returns)),
    )


@pytest.mark.asyncio
class TestPreauthorizeRideCard:
    async def test_authorized_persists_hold_with_buffer(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="authorized", payment_intent_id="pi_hold")) as auth:
            result = await _preauthorize_ride_card(**_BASE)

        assert result.fields == {
            "payment_intent_id": "pi_hold",
            "authorized_amount": 35.0,  # 25.00 fare + 10.00 buffer
            "auth_status": "authorized",
        }
        assert result.requires_action is False
        # Buffered amount actually requested
        assert auth.call_args.kwargs["amount"] == Decimal("35.00")

    async def test_insufficient_funds_falls_back_to_fare_only(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(
            _outcome(status="declined", decline_code="insufficient_funds"),
            _outcome(status="authorized", payment_intent_id="pi_fare_only"),
        ) as auth:
            result = await _preauthorize_ride_card(**_BASE)

        assert result.fields == {
            "payment_intent_id": "pi_fare_only",
            "authorized_amount": 25.0,  # fare only, no buffer
            "auth_status": "fare_only",
        }
        # Second call held the fare only
        assert auth.call_count == 2
        assert auth.call_args_list[1].kwargs["amount"] == Decimal("25.00")

    async def test_both_declined_blocks_with_402(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(
            _outcome(status="declined", decline_code="insufficient_funds"),
            _outcome(status="declined", decline_code="insufficient_funds"),
        ):
            with pytest.raises(HTTPException) as ei:
                await _preauthorize_ride_card(**_BASE)

        assert ei.value.status_code == 402
        assert ei.value.detail["code"] == "CARD_DECLINED"

    async def test_hard_decline_blocks_without_retry(self):
        """lost_card etc. — a smaller hold won't help, so block immediately
        (exactly one authorize call, no fare-only retry)."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="declined", decline_code="lost_card")) as auth:
            with pytest.raises(HTTPException) as ei:
                await _preauthorize_ride_card(**_BASE)

        assert ei.value.status_code == 402
        assert auth.call_count == 1  # no fallback retry on a hard decline

    async def test_requires_action_surfaces_client_secret_at_booking(self):
        """SCA / Apple Pay at booking: surface requires_action + client_secret so
        the app confirms on-device, then re-books with the PI."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="requires_action", client_secret="cs", payment_intent_id="pi_sca")):
            result = await _preauthorize_ride_card(**_BASE)
        assert result.requires_action is True
        assert result.client_secret == "cs"
        assert result.payment_intent_id == "pi_sca"
        assert result.fields == {}

    async def test_requires_action_degrades_when_not_blocking(self):
        """Scheduled dispatch can't drive an on-device sheet → degrade to no hold."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="requires_action", client_secret="cs")):
            result = await _preauthorize_ride_card(**_BASE, block_on_decline=False)
        assert result.requires_action is False
        assert result.fields == {}

    async def test_block_on_decline_false_hard_decline_returns_empty(self):
        """Scheduled dispatch (rider absent): a hard decline must NOT raise —
        it degrades to no hold so the scheduled ride is never stranded."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="declined", decline_code="lost_card")):
            result = await _preauthorize_ride_card(**_BASE, block_on_decline=False)
        assert result.fields == {}

    async def test_block_on_decline_false_both_declined_returns_empty(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(
            _outcome(status="declined", decline_code="insufficient_funds"),
            _outcome(status="declined", decline_code="insufficient_funds"),
        ):
            result = await _preauthorize_ride_card(**_BASE, block_on_decline=False)
        assert result.fields == {}

    async def test_ops_failure_degrades_to_no_hold(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="failed", error_message="rate_limit")):
            result = await _preauthorize_ride_card(**_BASE)
        assert result.fields == {}

    async def test_unconfigured_degrades_to_no_hold(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(_outcome(status="unconfigured")):
            result = await _preauthorize_ride_card(**_BASE)
        assert result.fields == {}

    async def test_no_card_on_file_skips_stripe_entirely(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize() as auth:  # would raise StopIteration if called
            result = await _preauthorize_ride_card(**{**_BASE, "payment_method_id": None})
        assert result.fields == {}
        auth.assert_not_called()


def _patch_verify(outcome):
    return patch("backend.routes.rides.verify_authorization", AsyncMock(return_value=outcome))


@pytest.mark.asyncio
class TestAttachPreauthorizedHold:
    async def test_authorized_attaches_amount_from_stripe(self):
        from backend.routes.rides import _attach_preauthorized_hold

        with _patch_verify(_outcome(status="authorized", payment_intent_id="pi_sca", charged_amount=Decimal("35.00"))):
            fields = await _attach_preauthorized_hold(ride_id="r", payment_intent_id="pi_sca")

        assert fields == {
            "payment_intent_id": "pi_sca",
            "authorized_amount": 35.0,
            "auth_status": "authorized",
        }

    async def test_declined_raises_402(self):
        from backend.routes.rides import _attach_preauthorized_hold

        with _patch_verify(_outcome(status="declined")):
            with pytest.raises(HTTPException) as ei:
                await _attach_preauthorized_hold(ride_id="r", payment_intent_id="pi_x")
        assert ei.value.status_code == 402
        assert ei.value.detail["code"] == "CARD_DECLINED"

    async def test_failed_degrades_to_no_hold(self):
        from backend.routes.rides import _attach_preauthorized_hold

        with _patch_verify(_outcome(status="failed", error_message="ops")):
            fields = await _attach_preauthorized_hold(ride_id="r", payment_intent_id="pi_x")
        assert fields == {}
