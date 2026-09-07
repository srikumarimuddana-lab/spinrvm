"""F1 — charge.refunded compare-and-swap, idempotent ledger, replay recovery.

Pins the fix for PR #5085's validated F1 finding: the refund handler used to
advance rides.refund_amount and write a ledger row as two independent,
unsynchronized steps — a concurrent delivery for the same ride could
interleave with either step, and a crash between them silently dropped the
ledger row while the ride already showed the refund as applied. This file
covers what test_routes_webhooks_coverage.py's existing refund tests don't:
the compare-and-swap conflict path, a ledger-write failure after a
successful CAS, and the delta_cents<=0 replay-recovery branch that detects
and repairs exactly that dropped-ledger-row gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _event_obj(event_type: str, data_object: dict, event_id: str) -> MagicMock:
    raw = {"id": event_id, "type": event_type, "data": {"object": data_object}}
    obj = MagicMock()
    obj.get = lambda k, d=None: raw.get(k, d)
    obj.to_dict_recursive = lambda: raw
    return obj


def _settings_fn():
    async def f():
        return {"stripe_webhook_secret": "ws", "stripe_secret_key": "sk"}

    return f


def _mock_req():
    req = MagicMock()
    req.body = AsyncMock(return_value=b"payload")
    req.headers = {"stripe-signature": "sig"}
    return req


def _get_rows_for(ride: dict, financial_rows: list | None = None):
    async def _get_rows(table, *args, **kwargs):
        if table == "financial_events":
            return financial_rows or []
        return [ride]

    return _get_rows


class TestChargeRefundedCasConflict:
    @pytest.mark.anyio
    async def test_cas_conflict_raises_500_unclaims_and_does_not_write_ledger(self):
        """A concurrent charge.refunded delivery for the same ride already won
        the race and advanced refund_amount between our read and our write —
        update_one's filter (id + refund_amount==prev) matches 0 rows, so it
        returns None. This must never be treated as "ride not found": it's a
        genuine conflict, and the ledger must not be written for a ride state
        we never actually applied."""
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_cas1", "payment_intent": "pi_cas_1", "amount_refunded": 1000, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_cas_1")
        ride = {"id": "ride_cas1", "rider_id": "rider_1", "refund_amount": None}
        record_refund_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_for(ride))),
            # 0 rows matched: another event already advanced refund_amount.
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock(return_value=None)),
            patch("backend.routes.webhooks.unclaim_stripe_event", AsyncMock()) as unclaim_mock,
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wh.stripe_webhook(request=_mock_req())

        assert exc_info.value.status_code == 500
        unclaim_mock.assert_awaited_once_with("evt_cas_1")
        record_refund_mock.assert_not_awaited()


class TestChargeRefundedLedgerFailureAfterCas:
    @pytest.mark.anyio
    async def test_ledger_write_failure_raises_500_and_does_not_revert_ride(self):
        """The CAS succeeds (ride row advances) but the ledger write itself
        returns None (e.g. transient DB failure). The ride row must NOT be
        reverted — a revert would let a delayed invoice.paid re-settle an
        already-refunded ride — and the handler must still surface a 500 so
        Stripe retries (the redelivery then either re-runs this same path,
        since update_one is idempotent to retry via unclaim, or lands in the
        delta_cents<=0 recovery branch once refund_amount has caught up)."""
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_cas2", "payment_intent": "pi_cas_2", "amount_refunded": 2000, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_cas_2")
        ride = {"id": "ride_cas2", "rider_id": "rider_2", "refund_amount": None}
        update_mock = AsyncMock(return_value={"id": "ride_cas2"})  # CAS succeeds

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_for(ride))),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.routes.webhooks.unclaim_stripe_event", AsyncMock()) as unclaim_mock,
            # Ledger write fails.
            patch("backend.services.payment_service.record_refund_event", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wh.stripe_webhook(request=_mock_req())

        assert exc_info.value.status_code == 500
        unclaim_mock.assert_awaited_once_with("evt_cas_2")
        # Exactly one CAS write — no compensating revert of the ride row.
        update_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_dedupe_key_derived_from_cumulative_not_delta(self):
        """The dedupe key must identify the money movement (this event's
        asserted cumulative amount), not the delivery attempt — two
        sequential partial refunds ($10 then $10 more) must produce two
        different keys even though each has the same $10 delta."""
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_key1", "payment_intent": "pi_key_1", "amount_refunded": 2000, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_key_1")
        ride = {"id": "ride_key1", "rider_id": "rider_1", "refund_amount": "10.00"}
        record_refund_mock = AsyncMock(return_value="ledger-id-1")

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_for(ride))),
            patch(
                "backend.routes.webhooks.db_supabase.update_one",
                AsyncMock(return_value={"id": "ride_key1"}),
            ),
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
            patch("backend.routes.webhooks.send_push_notification", AsyncMock()),
        ):
            await wh.stripe_webhook(request=_mock_req())

        # Cumulative refunded_cents=2000, not the 1000 delta.
        assert record_refund_mock.await_args.kwargs["dedupe_key"] == "stripe_refund|pi_key_1|2000"
        assert record_refund_mock.await_args.kwargs["refund_cents"] == 1000


class TestChargeRefundedReplayRecovery:
    @pytest.mark.anyio
    async def test_recovers_missing_ledger_row_when_cumulative_unchanged(self):
        """The ride's refund_amount already shows this event's cumulative
        total (a prior delivery's CAS succeeded) but the ledger has nothing
        for this payment intent (its write was lost — the exact gap this fix
        closes). A redelivery with an unchanged cumulative must book the full
        missing amount rather than silently treating it as stale."""
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_rec1", "payment_intent": "pi_rec_1", "amount_refunded": 1500, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_rec_1")
        ride = {"id": "ride_rec1", "rider_id": "rider_1", "refund_amount": "15.00"}
        record_refund_mock = AsyncMock(return_value="ledger-id-recovered")
        update_mock = AsyncMock()

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()) as mark_mock,
            # No financial_events rows at all for this payment intent.
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_for(ride, []))),
            patch("backend.routes.webhooks.db_supabase.update_one", update_mock),
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
        ):
            result = await wh.stripe_webhook(request=_mock_req())

        assert result["received"] is True
        # Recovery never touches the ride row — only the ledger.
        update_mock.assert_not_awaited()
        record_refund_mock.assert_awaited_once()
        assert record_refund_mock.await_args.kwargs["refund_cents"] == 1500
        assert record_refund_mock.await_args.kwargs["dedupe_key"] == "stripe_refund|pi_rec_1|1500"
        mark_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_recovers_a_superseded_events_lost_ledger_row(self):
        """spinr-money-auditor finding: event A(1000) wins its CAS then its
        ledger write fails; before A's Stripe retry arrives, B(2000) and then
        C(3000) both succeed fully, advancing the ride's recorded cumulative
        past A's own asserted amount. A's retry now reads amount_refunded=1000
        against a ride recorded at 3000 — refunded_cents != previous_refunded_cents,
        so gating recovery on that equality (an earlier version of this fix
        did) would fall through and PERMANENTLY lose A's $10 ledger row, since
        Stripe payloads are immutable and that equality can never hold again.
        Recovery must instead key off the ride's own current cumulative
        (previous_refunded_cents) so any non-forward delivery — not just a
        redelivery of the exact event that created the gap — can catch and
        heal it."""
        import stripe

        from backend.routes import webhooks as wh

        # A's redelivery: cumulative 1000, but the ride is already at 3000
        # (B and C both landed first). Only B's and C's ledger rows exist
        # (-1000 each = 2000 total); A's -1000 was lost.
        charge = {"id": "ch_super_a", "payment_intent": "pi_super", "amount_refunded": 1000, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_super_a_retry")
        ride = {"id": "ride_super", "rider_id": "rider_1", "refund_amount": "30.00"}
        record_refund_mock = AsyncMock(return_value="ledger-id-recovered-super")
        financial_rows = [{"delta_cents": -1000}, {"delta_cents": -1000}]  # B + C, not A

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()) as mark_mock,
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(side_effect=_get_rows_for(ride, financial_rows)),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()) as update_mock,
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
        ):
            result = await wh.stripe_webhook(request=_mock_req())

        assert result["received"] is True
        update_mock.assert_not_awaited()
        record_refund_mock.assert_awaited_once()
        # Books the gap against the ride's own recorded total (3000 - 2000 =
        # 1000), not this event's own 1000-cent amount_refunded (which would
        # be the same number here by coincidence — the dedupe_key proves
        # which one actually drove the calculation).
        assert record_refund_mock.await_args.kwargs["refund_cents"] == 1000
        assert record_refund_mock.await_args.kwargs["dedupe_key"] == "stripe_refund|pi_super|3000"
        mark_mock.assert_awaited_once()

    @pytest.mark.anyio
    async def test_recovers_only_the_missing_portion_when_partially_booked(self):
        """A partial-failure gap can leave SOME but not all of the cumulative
        amount booked (e.g. a prior recovery attempt itself failed partway).
        Only the shortfall should be booked, not the full cumulative again."""
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_rec2", "payment_intent": "pi_rec_2", "amount_refunded": 1500, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_rec_2")
        ride = {"id": "ride_rec2", "rider_id": "rider_2", "refund_amount": "15.00"}
        record_refund_mock = AsyncMock(return_value="ledger-id-recovered-2")
        # 500 of the 1500 cents already booked.
        financial_rows = [{"delta_cents": -500}]

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch(
                "backend.routes.webhooks.db_supabase.get_rows",
                AsyncMock(side_effect=_get_rows_for(ride, financial_rows)),
            ),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()) as update_mock,
            patch("backend.services.payment_service.record_refund_event", record_refund_mock),
        ):
            result = await wh.stripe_webhook(request=_mock_req())

        assert result["received"] is True
        update_mock.assert_not_awaited()
        assert record_refund_mock.await_args.kwargs["refund_cents"] == 1000

    @pytest.mark.anyio
    async def test_recovery_ledger_failure_raises_500_and_unclaims(self):
        """If even the recovery write fails, this must surface a 500 (Stripe
        retries) rather than silently swallowing a second lost ledger row."""
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_rec3", "payment_intent": "pi_rec_3", "amount_refunded": 1500, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_rec_3")
        ride = {"id": "ride_rec3", "rider_id": "rider_3", "refund_amount": "15.00"}

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(side_effect=_get_rows_for(ride, []))),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()) as update_mock,
            patch("backend.routes.webhooks.unclaim_stripe_event", AsyncMock()) as unclaim_mock,
            patch("backend.services.payment_service.record_refund_event", AsyncMock(return_value=None)),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wh.stripe_webhook(request=_mock_req())

        assert exc_info.value.status_code == 500
        unclaim_mock.assert_awaited_once_with("evt_rec_3")
        update_mock.assert_not_awaited()


class TestChargeRefundedDedupeKeyCollision:
    """spinr-money-auditor finding: previous_refunded_cents is, by
    construction, always some event's own asserted cumulative — so the
    recovery path's dedupe key can be structurally identical to a
    still-in-flight event's own normal-path key. Exercises the REAL
    record_refund_event -> ledger_service.record_event path (not mocked) so
    a collision between two different amounts is caught by
    _verify_duplicate_matches rather than silently no-op'd, end to end
    through the webhook handler."""

    @pytest.mark.anyio
    async def test_recovery_collision_with_different_amount_fails_loudly_not_silently(self):
        import stripe

        from backend.routes import webhooks as wh

        charge = {"id": "ch_collide", "payment_intent": "pi_collide", "amount_refunded": 1000, "currency": "cad"}
        event_obj = _event_obj("charge.refunded", charge, "evt_collide")
        ride = {"id": "ride_collide", "rider_id": "rider_1", "refund_amount": "30.00"}
        # Recovery computes missing = 3000 - 2000(booked) = 1000, and derives
        # dedupe_key "stripe_refund|pi_collide|3000" — but a row ALREADY
        # exists under that exact id with a DIFFERENT delta_cents (-2000),
        # simulating a still-in-flight event that won the race with an
        # honestly different amount than what recovery computed.
        from backend.services.ledger_service import derive_event_id

        colliding_id = derive_event_id("stripe_refund|pi_collide|3000")

        async def get_rows(table, filters, *args, **kwargs):
            if table == "financial_events":
                if filters.get("id") == colliding_id:
                    return [{"id": colliding_id, "delta_cents": -2000}]
                return [{"delta_cents": -1000}, {"delta_cents": -1000}]  # 2000 already booked
            return [ride]

        async def insert_one(table, row):
            if table == "financial_events" and row.get("id") == colliding_id:
                raise Exception("duplicate key value violates unique constraint (23505)")
            return row

        with (
            patch("backend.routes.webhooks.get_app_settings", _settings_fn()),
            patch.object(stripe.Webhook, "construct_event", return_value=event_obj),
            patch("backend.routes.webhooks.claim_stripe_event", AsyncMock(return_value=True)),
            patch("backend.routes.webhooks.mark_stripe_event_processed", AsyncMock()),
            patch("backend.routes.webhooks.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.routes.webhooks.db_supabase.update_one", AsyncMock()) as update_mock,
            patch("backend.routes.webhooks.unclaim_stripe_event", AsyncMock()) as unclaim_mock,
            patch("backend.services.payment_service.db_supabase.insert_one", AsyncMock(side_effect=insert_one)),
            patch("backend.services.ledger_service.db_supabase.get_rows", AsyncMock(side_effect=get_rows)),
            patch("backend.services.ledger_service.escalate"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await wh.stripe_webhook(request=_mock_req())

        # Must surface as a real failure (500, Stripe retries) — NOT a
        # silent success that leaves the ledger under/over-booked.
        assert exc_info.value.status_code == 500
        unclaim_mock.assert_awaited_once_with("evt_collide")
        update_mock.assert_not_awaited()
