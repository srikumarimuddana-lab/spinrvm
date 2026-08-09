"""Unit tests for services/stripe_connect_ledger_service.py.

The service pulls a driver's connected-account money movement — Stripe
``Payout`` (account → bank) and ``BalanceTransaction`` (the full signed
ledger) — into our own tables so the app can serve them without calling
Stripe.

The properties that matter most here are money-shaped:
  * amounts convert from Stripe's integer cents via Decimal, never float;
  * ledger amounts stay SIGNED, so a debit cannot read as income;
  * payouts UPDATE on re-sync (their status moves pending → paid), while
    the sync as a whole stays idempotent on the Stripe object id;
  * an unreachable SUPERSEDED account warns, but an unreachable CURRENT one
    is an error — a wrong key must not look like "nothing to sync".
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
import stripe

from backend.services import stripe_connect_ledger_service as svc

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

DRIVER_ID = "drv_led_1"
ACCT = "acct_current"
OLD_ACCT = "acct_superseded"


def _driver(**extra) -> dict:
    row = {"id": DRIVER_ID, "stripe_account_id": ACCT, "stripe_account_id_superseded": None}
    row.update(extra)
    return row


def _payout(pid="po_1", amount=25000, status="paid", **extra) -> dict:
    obj = {
        "id": pid,
        "amount": amount,
        "currency": "cad",
        "status": status,
        "method": "standard",
        "arrival_date": 1735689600,
        "created": 1735603200,
        "failure_code": None,
        "failure_message": None,
        "destination": "ba_123",
    }
    obj.update(extra)
    return obj


def _txn(tid="txn_1", amount=25000, fee=0, net=None, ttype="transfer", **extra) -> dict:
    obj = {
        "id": tid,
        "type": ttype,
        "amount": amount,
        "fee": fee,
        "net": amount - fee if net is None else net,
        "currency": "cad",
        "status": "available",
        "source": "tr_1",
        "description": None,
        "available_on": 1735689600,
        "created": 1735603200,
    }
    obj.update(extra)
    return obj


class _FakeList:
    """Mimics stripe's list().auto_paging_iter()."""

    def __init__(self, items):
        self._items = items

    def auto_paging_iter(self):
        return iter(self._items)


class _Harness:
    def __init__(self, drivers, *, payouts=None, txns=None, payout_error=None, txn_error=None):
        self.upserts: list[tuple[str, list[dict]]] = []
        self._payouts = payouts if payouts is not None else {}
        self._txns = txns if txns is not None else {}
        self._payout_error = payout_error
        self._txn_error = txn_error
        self.list_calls: list[tuple[str, str]] = []

        def _payout_list(**kw):
            acct = kw.get("stripe_account")
            self.list_calls.append(("payout", acct))
            if self._payout_error and acct in self._payout_error:
                raise self._payout_error[acct]
            return _FakeList(self._payouts.get(acct, []))

        def _txn_list(**kw):
            acct = kw.get("stripe_account")
            self.list_calls.append(("txn", acct))
            if self._txn_error and acct in self._txn_error:
                raise self._txn_error[acct]
            return _FakeList(self._txns.get(acct, []))

        async def _upsert(table, rows):
            if rows:
                self.upserts.append((table, rows))
            return len(rows)

        self._patches = [
            patch.object(svc, "_fetch_sync_targets", MagicMock(return_value=drivers)),
            patch.object(svc, "_upsert", side_effect=_upsert),
            patch("stripe.Payout.list", side_effect=_payout_list),
            patch("stripe.BalanceTransaction.list", side_effect=_txn_list),
        ]

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False

    def rows(self, table):
        return [r for t, rows in self.upserts if t == table for r in rows]


class TestPayoutSync:
    async def test_bank_payout_is_stored(self):
        with _Harness([_driver()], payouts={ACCT: [_payout()]}) as h:
            result = await svc.sync_connect_ledger("sk_test_x")

        assert result.payouts_upserted == 1
        row = h.rows(svc.PAYOUTS_TABLE)[0]
        assert row["id"] == "po_1"
        assert row["driver_id"] == DRIVER_ID
        assert row["stripe_account_id"] == ACCT
        # 25000 cents → exactly 250.00, via Decimal.
        assert Decimal(row["amount"]) == Decimal("250.00")
        assert row["status"] == "paid"
        assert row["created_at"].startswith("2024-12-31")

    async def test_failed_payout_keeps_its_reason(self):
        with _Harness(
            [_driver()],
            payouts={ACCT: [_payout(status="failed", failure_code="account_closed", failure_message="Bank closed")]},
        ) as h:
            await svc.sync_connect_ledger("sk_test_x")
        row = h.rows(svc.PAYOUTS_TABLE)[0]
        assert (row["status"], row["failure_code"]) == ("failed", "account_closed")

    async def test_bank_last4_only_when_stripe_expanded_it(self):
        """`destination` is an id string unless expanded — never guess a last4."""
        with _Harness([_driver()], payouts={ACCT: [_payout()]}) as h:
            await svc.sync_connect_ledger("sk_test_x")
        assert h.rows(svc.PAYOUTS_TABLE)[0]["bank_last4"] is None

        with _Harness([_driver()], payouts={ACCT: [_payout(destination={"id": "ba_1", "last4": "6789"})]}) as h2:
            await svc.sync_connect_ledger("sk_test_x")
        assert h2.rows(svc.PAYOUTS_TABLE)[0]["bank_last4"] == "6789"


class TestLedgerSync:
    async def test_ledger_entry_is_stored_with_fee_and_net(self):
        with _Harness([_driver()], txns={ACCT: [_txn(amount=10000, fee=250)]}) as h:
            result = await svc.sync_connect_ledger("sk_test_x")

        assert result.ledger_upserted == 1
        row = h.rows(svc.LEDGER_TABLE)[0]
        assert Decimal(row["amount"]) == Decimal("100.00")
        assert Decimal(row["fee"]) == Decimal("2.50")
        assert Decimal(row["net"]) == Decimal("97.50")
        assert row["source"] == "tr_1"

    async def test_debits_stay_negative(self):
        """Signed by design: a payout debit must never read as income."""
        with _Harness([_driver()], txns={ACCT: [_txn(tid="txn_out", amount=-25000, ttype="payout")]}) as h:
            await svc.sync_connect_ledger("sk_test_x")
        row = h.rows(svc.LEDGER_TABLE)[0]
        assert Decimal(row["amount"]) == Decimal("-250.00")
        assert row["type"] == "payout"

    async def test_unknown_stripe_type_is_stored_verbatim(self):
        """The type set is open — a new Stripe type must not break the sync."""
        with _Harness([_driver()], txns={ACCT: [_txn(ttype="some_future_type")]}) as h:
            await svc.sync_connect_ledger("sk_test_x")
        assert h.rows(svc.LEDGER_TABLE)[0]["type"] == "some_future_type"

    async def test_a_dollar_appears_as_both_legs(self):
        """Documents the double-count trap: the same money is a credit and a
        debit here, which is exactly why this table is never summed as income."""
        with _Harness(
            [_driver()],
            txns={ACCT: [_txn(tid="txn_in", amount=25000, ttype="transfer"), _txn(tid="txn_out", amount=-25000, ttype="payout")]},
        ) as h:
            await svc.sync_connect_ledger("sk_test_x")
        rows = h.rows(svc.LEDGER_TABLE)
        assert sum(Decimal(r["amount"]) for r in rows) == Decimal("0.00")


class TestAccountScope:
    async def test_superseded_account_is_read_too(self):
        driver = _driver(stripe_account_id=None, stripe_account_id_superseded=OLD_ACCT)
        with _Harness([driver], payouts={OLD_ACCT: [_payout()]}) as h:
            result = await svc.sync_connect_ledger("sk_test_x")
        assert result.payouts_upserted == 1
        assert h.rows(svc.PAYOUTS_TABLE)[0]["stripe_account_id"] == OLD_ACCT

    async def test_both_accounts_are_read(self):
        driver = _driver(stripe_account_id_superseded=OLD_ACCT)
        with _Harness(
            [driver],
            payouts={ACCT: [_payout("po_new")], OLD_ACCT: [_payout("po_old")]},
        ) as h:
            await svc.sync_connect_ledger("sk_test_x")
        assert sorted(r["id"] for r in h.rows(svc.PAYOUTS_TABLE)) == ["po_new", "po_old"]

    async def test_unreachable_superseded_account_warns(self):
        driver = _driver(stripe_account_id_superseded=OLD_ACCT)
        err = {OLD_ACCT: stripe.error.PermissionError(f"no access to {OLD_ACCT}")}
        with _Harness([driver], payouts={ACCT: [_payout()]}, payout_error=err) as h:
            result = await svc.sync_connect_ledger("sk_test_x")
        assert result.errors == []
        assert [w.field for w in result.warnings] == ["account_not_accessible"]
        # The reachable current account still synced.
        assert h.rows(svc.PAYOUTS_TABLE)[0]["stripe_account_id"] == ACCT

    async def test_unreachable_current_account_errors(self):
        err = {ACCT: stripe.error.PermissionError(f"no access to {ACCT}")}
        with _Harness([_driver()], payout_error=err):
            result = await svc.sync_connect_ledger("sk_test_x")
        assert [e.field for e in result.errors] == ["stripe_list_failed"]

    async def test_transient_error_on_superseded_account_still_errors(self):
        """Only INACCESSIBILITY is downgraded; a rate limit must still surface."""
        driver = _driver(stripe_account_id_superseded=OLD_ACCT)
        err = {OLD_ACCT: stripe.error.RateLimitError("slow down")}
        with _Harness([driver], payout_error=err):
            result = await svc.sync_connect_ledger("sk_test_x")
        assert [e.field for e in result.errors] == ["stripe_list_failed"]


class TestGuards:
    async def test_missing_secret_errors_without_touching_stripe(self):
        with _Harness([_driver()]) as h:
            result = await svc.sync_connect_ledger("")
        assert [e.field for e in result.errors] == ["stripe_not_configured"]
        assert h.list_calls == []

    async def test_driver_with_no_account_is_skipped(self):
        with _Harness([]) as h:
            result = await svc.sync_connect_ledger("sk_test_x")
        assert (result.drivers_synced, h.upserts) == (0, [])

    async def test_calls_are_connect_scoped(self):
        """These objects do not exist on the platform account — the
        stripe_account header is what makes the read possible at all."""
        with _Harness([_driver()], payouts={ACCT: [_payout()]}) as h:
            await svc.sync_connect_ledger("sk_test_x")
        assert set(h.list_calls) == {("payout", ACCT), ("txn", ACCT)}
