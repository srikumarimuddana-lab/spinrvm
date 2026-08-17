"""
Unit tests for the booking-time card pre-authorization helper
backend/routes/rides.py::_preauthorize_ride_card.

The helper places a manual-capture hold at booking and decides, per Stripe
outcome, whether to:
  - persist the hold (authorized / fare_only),
  - block the booking with 402 (genuine decline), or
  - degrade to "no hold, proceed" (SCA, ops error, unconfigured, no card).

Hold sizing is fare-lock dependent, which is what most of these tests pin:
  - fare_lock_enabled TRUE (production default, migration 248) -> buffer is
    ZERO, so the hold equals the quoted fare. A $5 ride holds $5, not $15.
  - fare_lock_enabled FALSE -> settlement could exceed the quote, so a
    proportional buffer comes back (25% of fare, floored at $2, capped at $10).

The buffer also gates the insufficient_funds retry: retrying "at a lower amount"
is only meaningful when the hold was larger than the fare in the first place.

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
    """Patch routes.rides._deps.authorize_ride with a sequence of ChargeOutcomes
    (one per call — the hold, then the optional fare-only retry)."""
    return patch(
        "backend.routes.rides._deps.authorize_ride",
        AsyncMock(side_effect=list(returns)),
    )


def _patch_fare_lock(enabled: bool):
    """Pin app_settings.fare_lock_enabled, which decides the hold buffer.

    Every hold-sizing test sets this explicitly rather than relying on a default:
    the two branches produce different hold amounts, and a test that silently
    drifted onto the wrong branch would still "pass" while asserting the wrong
    number.
    """
    return patch(
        "backend.routes.rides._deps.get_app_settings",
        AsyncMock(return_value={"fare_lock_enabled": enabled}),
    )


@pytest.mark.asyncio
class TestPreauthorizeRideCard:
    async def test_authorized_holds_exact_fare_under_fare_lock(self):
        """The headline behaviour: hold == quoted fare, no tip headroom.

        A rider quoted $25 sees a $25 pending charge. The old flat $10 buffer
        made that $35, which reads on a bank feed as being overcharged.
        """
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(True):
            with _patch_authorize(_outcome(status="authorized", payment_intent_id="pi_hold")) as auth:
                result = await _preauthorize_ride_card(**_BASE)

        assert result.fields == {
            "payment_intent_id": "pi_hold",
            "authorized_amount": 25.0,  # fare exactly — no buffer
            "auth_status": "authorized",
            "auth_incrementable": False,
        }
        assert result.requires_action is False
        assert auth.call_args.kwargs["amount"] == Decimal("25.00")

    async def test_small_fare_is_not_dwarfed_by_the_hold(self):
        """Regression for the complaint that started this: $5 ride, $15 hold."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(True):
            with _patch_authorize(_outcome(status="authorized", payment_intent_id="pi_small")) as auth:
                await _preauthorize_ride_card(**{**_BASE, "grand_total": Decimal("5.00")})

        assert auth.call_args.kwargs["amount"] == Decimal("5.00")

    async def test_increment_capability_is_persisted(self):
        """Settlement runs in a later request, so the capability must be stored."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(True):
            with _patch_authorize(
                _outcome(
                    status="authorized",
                    payment_intent_id="pi_inc",
                    incremental_authorization_supported=True,
                )
            ):
                result = await _preauthorize_ride_card(**_BASE)

        assert result.fields["auth_incrementable"] is True

    async def test_insufficient_funds_does_not_retry_under_fare_lock(self):
        """With a zero buffer there is no lower amount to retry at.

        Re-authorizing the identical amount would just decline again, so the
        booking blocks on the first decline instead of burning a second call.
        """
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(True):
            with _patch_authorize(
                _outcome(status="declined", decline_code="insufficient_funds"),
            ) as auth:
                with pytest.raises(HTTPException) as ei:
                    await _preauthorize_ride_card(**_BASE)

        assert ei.value.status_code == 402
        assert auth.call_count == 1

    async def test_insufficient_funds_falls_back_to_fare_only_when_unlocked(self):
        """The retry survives, but only on the branch where it means something.

        Fare unlocked -> 25% of $25 = $6.25 buffer -> $31.25 hold. If that trips
        a thin balance, retry at the bare fare so a rider who can afford the ride
        still rides.
        """
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(False):
            with _patch_authorize(
                _outcome(status="declined", decline_code="insufficient_funds"),
                _outcome(status="authorized", payment_intent_id="pi_fare_only"),
            ) as auth:
                result = await _preauthorize_ride_card(**_BASE)

        assert result.fields == {
            "payment_intent_id": "pi_fare_only",
            "authorized_amount": 25.0,  # fare only, no buffer
            "auth_status": "fare_only",
            "auth_incrementable": False,
        }
        assert auth.call_count == 2
        assert auth.call_args_list[0].kwargs["amount"] == Decimal("31.25")
        assert auth.call_args_list[1].kwargs["amount"] == Decimal("25.00")

    async def test_unlocked_buffer_is_floored_for_tiny_fares(self):
        """25% of $4 is $1, below the $2 floor — the floor wins."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(False):
            with _patch_authorize(_outcome(status="authorized", payment_intent_id="pi_tiny")) as auth:
                await _preauthorize_ride_card(**{**_BASE, "grand_total": Decimal("4.00")})

        assert auth.call_args.kwargs["amount"] == Decimal("6.00")

    async def test_unlocked_buffer_is_capped_for_large_fares(self):
        """25% of $200 is $50, above the $10 cap — the cap wins."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_fare_lock(False):
            with _patch_authorize(_outcome(status="authorized", payment_intent_id="pi_big")) as auth:
                await _preauthorize_ride_card(**{**_BASE, "grand_total": Decimal("200.00")})

        assert auth.call_args.kwargs["amount"] == Decimal("210.00")

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

    async def test_fare_only_retry_requires_action_surfaces_client_secret(self):
        """The buffered hold declined for insufficient_funds, and the
        fare-only retry needs SCA -- surface it (interactive booking)."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(
            _outcome(status="declined", decline_code="insufficient_funds"),
            _outcome(status="requires_action", client_secret="cs2", payment_intent_id="pi_fare_sca"),
        ):
            result = await _preauthorize_ride_card(**_BASE)
        assert result.requires_action is True
        assert result.client_secret == "cs2"
        assert result.payment_intent_id == "pi_fare_sca"

    async def test_fare_only_retry_requires_action_degrades_when_not_blocking(self):
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(
            _outcome(status="declined", decline_code="insufficient_funds"),
            _outcome(status="requires_action", client_secret="cs2"),
        ):
            result = await _preauthorize_ride_card(**_BASE, block_on_decline=False)
        assert result.requires_action is False
        assert result.fields == {}

    async def test_fare_only_retry_ops_failure_degrades_to_no_hold(self):
        """The fare-only retry hits an ops error (not a genuine decline) --
        degrade rather than block the booking."""
        from backend.routes.rides import _preauthorize_ride_card

        with _patch_authorize(
            _outcome(status="declined", decline_code="insufficient_funds"),
            _outcome(status="failed", error_message="rate_limit"),
        ):
            result = await _preauthorize_ride_card(**_BASE)
        assert result.fields == {}
        assert result.requires_action is False


def _patch_verify(outcome, *, existing_rows=None):
    """Patch verify_authorization and the PI-reuse lookup (db_supabase.get_rows)."""
    return [
        patch("backend.routes.rides._deps.verify_authorization", AsyncMock(return_value=outcome)),
        patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(return_value=existing_rows or [])),
    ]


_ATTACH_KW = dict(
    ride_id="r",
    rider_id="rider_book_1",
    payment_intent_id="pi_sca",
    stripe_customer_id="cus_book",
    min_amount=Decimal("25.00"),
)


@pytest.mark.asyncio
class TestAttachPreauthorizedHold:
    async def test_authorized_attaches_amount_from_stripe(self):
        from contextlib import ExitStack

        from backend.routes.rides import _attach_preauthorized_hold

        out = _outcome(status="authorized", payment_intent_id="pi_sca", charged_amount=Decimal("35.00"))
        patches = _patch_verify(out)
        with ExitStack() as st:
            verify = st.enter_context(patches[0])
            st.enter_context(patches[1])
            fields = await _attach_preauthorized_hold(**_ATTACH_KW)

        assert fields == {
            "payment_intent_id": "pi_sca",
            "authorized_amount": 35.0,
            "auth_status": "authorized",
        }
        # ownership + amount checks are threaded to verify_authorization
        assert verify.call_args.kwargs["expected_customer_id"] == "cus_book"
        assert verify.call_args.kwargs["min_amount_cents"] == 2500

    async def test_same_ride_reattach_is_allowed(self):
        """Idempotent retry: a PI already on THIS ride re-attaches, not blocks."""
        from contextlib import ExitStack

        from backend.routes.rides import _attach_preauthorized_hold

        out = _outcome(status="authorized", payment_intent_id="pi_sca", charged_amount=Decimal("35.00"))
        with ExitStack() as st:
            for p in _patch_verify(out, existing_rows=[{"id": _ATTACH_KW["ride_id"]}]):
                st.enter_context(p)
            fields = await _attach_preauthorized_hold(**_ATTACH_KW)
        assert fields["auth_status"] == "authorized"

    async def test_no_stripe_customer_blocks(self):
        """SECURITY: without a customer to verify ownership against, reject (fail closed)."""
        from contextlib import ExitStack

        from backend.routes.rides import _attach_preauthorized_hold

        out = _outcome(status="authorized", payment_intent_id="pi_sca", charged_amount=Decimal("35.00"))
        with ExitStack() as st:
            for p in _patch_verify(out):
                st.enter_context(p)
            with pytest.raises(HTTPException) as ei:
                await _attach_preauthorized_hold(**{**_ATTACH_KW, "stripe_customer_id": None})
        assert ei.value.status_code == 402

    async def test_declined_raises_402(self):
        from contextlib import ExitStack

        from backend.routes.rides import _attach_preauthorized_hold

        with ExitStack() as st:
            for p in _patch_verify(_outcome(status="declined")):
                st.enter_context(p)
            with pytest.raises(HTTPException) as ei:
                await _attach_preauthorized_hold(**_ATTACH_KW)
        assert ei.value.status_code == 402
        assert ei.value.detail["code"] == "CARD_DECLINED"

    async def test_failed_degrades_to_no_hold(self):
        from contextlib import ExitStack

        from backend.routes.rides import _attach_preauthorized_hold

        with ExitStack() as st:
            for p in _patch_verify(_outcome(status="failed", error_message="ops")):
                st.enter_context(p)
            fields = await _attach_preauthorized_hold(**_ATTACH_KW)
        assert fields == {}

    async def test_pi_reuse_lookup_failure_fails_open_to_stripe_check(self):
        """A degraded reuse-lookup query must not block attachment -- ownership
        and amount are still enforced against Stripe itself below it."""
        from backend.routes.rides import _attach_preauthorized_hold

        out = _outcome(status="authorized", payment_intent_id="pi_sca", charged_amount=Decimal("35.00"))
        with (
            patch("backend.routes.rides._deps.verify_authorization", AsyncMock(return_value=out)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_rows",
                AsyncMock(side_effect=RuntimeError("db unavailable")),
            ),
        ):
            fields = await _attach_preauthorized_hold(**_ATTACH_KW)
        assert fields["auth_status"] == "authorized"

    async def test_pi_already_attached_to_other_ride_blocks(self):
        """SECURITY: a PI already on a DIFFERENT ride can't be re-attached."""
        from contextlib import ExitStack

        from backend.routes.rides import _attach_preauthorized_hold

        out = _outcome(status="authorized", payment_intent_id="pi_sca", charged_amount=Decimal("35.00"))
        with ExitStack() as st:
            for p in _patch_verify(out, existing_rows=[{"id": "some_other_ride"}]):
                st.enter_context(p)
            with pytest.raises(HTTPException) as ei:
                await _attach_preauthorized_hold(**_ATTACH_KW)
        assert ei.value.status_code == 402
