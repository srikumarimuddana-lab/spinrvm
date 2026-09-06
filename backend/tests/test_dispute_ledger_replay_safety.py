"""The dispute-close ledger append must be replay-safe (audit N4).

`record_dispute_close_events` looped Stripe's `balance_transactions` and called
`ledger_service.record_event` unconditionally, and `record_event` minted a fresh
`uuid4` per call. `financial_events` has no unique constraint other than its
`id` primary key, and stuck events are deliberately re-run through
`_dispatch_stripe_event` by the admin replay endpoint
(`routes/admin/stripe_events.py`) — so a crash after the ledger write booked the
same -$42.50 chargeback and -$15.00 dispute fee **twice**.

Fixed by deriving the row's primary key from the Stripe balance-transaction id,
which is globally unique and stable across redeliveries. The replay then hits a
duplicate-key, which `_insert_with_retry` already treats as success.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio

_DISPUTE_ID = "dp_1"
_RIDE_ID = "ride-dsp-1"
_USER_ID = "user-dsp-1"


def _bts():
    """A real dispute close: the chargeback and its separate fee row."""
    return [
        {"id": "txn_charge_1", "amount": -4250, "type": "adjustment", "fee": 0, "currency": "cad"},
        {"id": "txn_fee_1", "amount": -1500, "type": "adjustment", "fee": 1500, "currency": "cad"},
    ]


async def _record(balance_transactions, *, user_id=_USER_ID):
    from backend.services import payment_service

    rec = AsyncMock(return_value="evt-id")
    with patch("backend.services.ledger_service.record_event", rec):
        await payment_service.record_dispute_close_events(
            _DISPUTE_ID,
            user_id,
            _RIDE_ID,
            balance_transactions,
            dispute_status="lost",
        )
    return rec


class TestDeterministicLedgerId:
    async def test_each_balance_transaction_gets_a_distinct_dedupe_key(self):
        rec = await _record(_bts())
        keys = [c.kwargs["dedupe_key"] for c in rec.await_args_list]
        assert keys == [
            f"stripe_dispute|{_DISPUTE_ID}|txn_charge_1",
            f"stripe_dispute|{_DISPUTE_ID}|txn_fee_1",
        ]
        # The chargeback and its fee are two real, separate money movements —
        # they must NOT collapse onto one row.
        assert len(set(keys)) == 2

    async def test_a_replay_produces_the_same_keys(self):
        """The whole point: re-running the handler books the same ids, so the
        second insert is a no-op duplicate-key rather than a second chargeback."""
        first = [c.kwargs["dedupe_key"] for c in (await _record(_bts())).await_args_list]
        second = [c.kwargs["dedupe_key"] for c in (await _record(_bts())).await_args_list]
        assert first == second

    async def test_derived_id_is_stable_and_distinct_per_key(self):
        from backend.services.ledger_service import derive_event_id

        a = derive_event_id(f"stripe_dispute|{_DISPUTE_ID}|txn_charge_1")
        b = derive_event_id(f"stripe_dispute|{_DISPUTE_ID}|txn_fee_1")
        assert a == derive_event_id(f"stripe_dispute|{_DISPUTE_ID}|txn_charge_1")
        assert a != b
        # Must be a real UUID — financial_events.id is a uuid column.
        import uuid as _uuid

        assert str(_uuid.UUID(a)) == a

    async def test_different_disputes_do_not_collide(self):
        from backend.services.ledger_service import derive_event_id

        assert derive_event_id("stripe_dispute|dp_1|txn_1") != derive_event_id("stripe_dispute|dp_2|txn_1")

    async def test_balance_transaction_without_an_id_falls_back_and_logs(self):
        """Should not happen (Stripe always sets one), but must not collapse
        every such row onto a single shared key."""
        with patch("backend.services.payment_service.logger.error") as log:
            rec = await _record([{"id": None, "amount": -100, "type": "adjustment"}])
        assert rec.await_args.kwargs["dedupe_key"] is None
        log.assert_called_once()


class TestExistingBehaviourPreserved:
    async def test_zero_amount_transactions_are_still_skipped(self):
        rec = await _record([{"id": "txn_zero", "amount": 0}, *_bts()])
        assert rec.await_count == 2

    async def test_still_skips_entirely_without_a_user_id(self):
        """financial_events.user_id is NOT NULL REFERENCES users(id)."""
        rec = await _record(_bts(), user_id="")
        rec.assert_not_awaited()

    async def test_metadata_is_unchanged(self):
        rec = await _record(_bts())
        meta = rec.await_args_list[0].kwargs["metadata"]
        assert meta["stripe_dispute_id"] == _DISPUTE_ID
        assert meta["balance_transaction_id"] == "txn_charge_1"
        assert meta["dispute_status"] == "lost"


class TestRecordEventDefaultUnchanged:
    async def test_without_a_dedupe_key_ids_stay_random(self):
        """Callers that were not updated must keep their previous behaviour —
        a fresh id per call, not a collision."""
        from backend.services import ledger_service

        seen = []

        async def _capture(table, row, what=""):
            seen.append(row["id"])
            return True

        with patch.object(ledger_service, "_insert_with_retry", _capture):
            for _ in range(2):
                await ledger_service.record_event(
                    event_type="stripe_charge",
                    user_id=_USER_ID,
                    ride_id=_RIDE_ID,
                    delta_cents=100,
                    ref="pi_1",
                )
        assert seen[0] != seen[1]
