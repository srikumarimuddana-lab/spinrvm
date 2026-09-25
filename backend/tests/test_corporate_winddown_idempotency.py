# backend/tests/test_corporate_winddown_idempotency.py
"""Wind-down ledger debit idempotency (clean-sheet audit CORP-001, ROADMAP N20).

Dry run against ``mock_supabase_client`` (CLAUDE.md release gate 4): the
real ``refund_wallet_balance_on_close`` → real ``apply_adjustment`` → real
``_apply`` → ``supabase.rpc("corporate_wallet_apply_delta", ...)`` path, with
the RPC replaced by a small in-memory model of migration 376's semantics
(wallet row lock, client-idempotency-key short-circuit, floor check) and
Stripe replaced by a model of Stripe's own idempotency (same idempotency key
→ same refund object). Only the DB and Stripe edges are faked.

Scenario (before this fix): wallet balance $250.00, two $50 top-ups. A close
refunds $100 via Stripe and debits the ledger $100. If the wind-down runs a
second time for the same close (concurrent double-submit before the CAS fix,
or any future re-entry), Stripe returns the same two refunds, but the ledger
debit had no idempotency key, so the wallet was debited another $100 →
$50.00, i.e. $200 debited for $100 actually refunded, on a terminal account.

After: the debit carries ``corp-close-{wallet}-{sha256(sorted refund ids)}``,
so the replay's RPC call returns the original ledger row (``deduped``) and
the balance stays at $150.00. A run that refunded a *different* set of
Stripe refunds gets a different key and is applied.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import uuid
from contextlib import ExitStack
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.tests._factories import corporate_account_row
from services import corporate_wallet_winddown_service as svc

pytestmark = pytest.mark.unit

_SETTINGS = {"stripe_secret_key": "sk_test_123"}


def _topup(id_, amount, pi):
    return {
        "id": id_,
        "wallet_id": "w1",
        "type": "topup",
        "amount": amount,
        "stripe_payment_intent_id": pi,
        "created_at": "2026-01-01T00:00:00Z",
    }


class _FakeWalletLedger:
    """In-memory model of corporate_wallet_apply_delta (migration 376)."""

    def __init__(self, balance: str):
        self.balance = Decimal(balance)
        self.rows = []
        self._lock = threading.Lock()  # stands in for SELECT ... FOR UPDATE

    def rpc(self, name, params):
        assert name == "corporate_wallet_apply_delta"
        builder = MagicMock()
        builder.execute = MagicMock(side_effect=lambda: self._apply(params))
        return builder

    def _apply(self, p):
        with self._lock:
            key = p.get("p_client_idempotency_key")
            if p["p_stripe_pi"] is None and p["p_ride_id"] is None and key is not None:
                for row in self.rows:
                    if row["client_idempotency_key"] == key:
                        return self._resp(row, deduped=True)
            new_balance = self.balance + Decimal(p["p_delta"])
            if p["p_scope"] == "master" and p["p_floor"] is not None and new_balance < Decimal(p["p_floor"]):
                raise RuntimeError(f"wallet_below_floor: new={new_balance} floor={p['p_floor']}")
            self.balance = new_balance
            row = {
                "id": str(uuid.uuid4()),
                "type": p["p_type"],
                "amount": Decimal(p["p_delta"]),
                "balance_after": new_balance,
                "client_idempotency_key": key,
            }
            self.rows.append(row)
            return self._resp(row, deduped=False)

    @staticmethod
    def _resp(row, *, deduped):
        r = MagicMock()
        r.data = [{"transaction_id": row["id"], "balance_after": str(row["balance_after"]), "deduped": deduped}]
        return r

    async def get_wallet(self, company_id):
        snapshot = {"id": "w1", "company_id": company_id, "balance": str(self.balance)}
        await asyncio.sleep(0)  # let a concurrent run read the same balance
        return snapshot


class _FakeStripeRefunds:
    """Same idempotency key → same refund object, like Stripe's own idempotency."""

    def __init__(self):
        self.by_key = {}
        self.calls = 0
        self._lock = threading.Lock()

    def create(self, **kwargs):
        with self._lock:
            self.calls += 1
            key = kwargs["idempotency_key"]
            if key not in self.by_key:
                self.by_key[key] = MagicMock(id=f"re_{len(self.by_key) + 1}")
            return self.by_key[key]


def _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, topups):
    wallet_service_mod = sys.modules[svc.apply_adjustment.__module__]
    mock_supabase_client.rpc = MagicMock(side_effect=ledger.rpc)
    stack.enter_context(patch.object(wallet_service_mod, "supabase", mock_supabase_client))
    stack.enter_context(
        patch.object(svc.db_supabase, "get_corporate_wallet_by_company", AsyncMock(side_effect=ledger.get_wallet))
    )
    stack.enter_context(patch.object(svc.db_supabase, "get_rows", AsyncMock(return_value=topups)))
    stack.enter_context(patch.object(svc, "get_app_settings", AsyncMock(return_value=_SETTINGS)))
    stack.enter_context(patch.object(svc.stripe.Refund, "create", side_effect=stripe_fake.create))


async def _winddown():
    return await svc.refund_wallet_balance_on_close(company_id="c1", stripe_customer_id="cus_1", actor_user_id="a1")


# ── key derivation ──────────────────────────────────────────────────────


def test_key_is_order_insensitive_and_set_sensitive():
    k = svc._winddown_idempotency_key
    assert k("w1", ["re_2", "re_1"]) == k("w1", ["re_1", "re_2"])
    assert k("w1", ["re_1", "re_2"]) != k("w1", ["re_1"])  # partial set
    assert k("w1", ["re_1", "re_2"]) != k("w1", ["re_1", "re_3"])  # different set
    assert k("w1", ["re_1"]) != k("w2", ["re_1"])  # the unique index is global
    assert k("w1", ["re_1"]).startswith("corp-close-w1-")
    # fixed length however many top-ups were refunded (unique btree index)
    assert len(k("w1", [f"re_{i}" for i in range(500)])) == len(k("w1", ["re_1"]))


# ── dry runs against the modelled RPC ───────────────────────────────────


@pytest.mark.anyio
async def test_debit_passes_idempotency_key_to_rpc(mock_supabase_client):
    ledger = _FakeWalletLedger("250.00")
    stripe_fake = _FakeStripeRefunds()
    topups = [_topup("t2", "50.00", "pi_2"), _topup("t1", "50.00", "pi_1")]
    with ExitStack() as stack:
        _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, topups)
        result = await _winddown()

    params = mock_supabase_client.rpc.call_args.args[1]
    assert params["p_client_idempotency_key"] == svc._winddown_idempotency_key("w1", ["re_1", "re_2"])
    assert params["p_delta"] == "-100.00"
    assert result["refunded_total"] == "100.00"
    assert result["ledger_debit_deduped"] is False
    assert ledger.balance == Decimal("150.00")


@pytest.mark.anyio
async def test_two_concurrent_winddowns_debit_the_ledger_exactly_once(mock_supabase_client):
    """Both runs read $250 before either debits (the pre-CAS race)."""
    ledger = _FakeWalletLedger("250.00")
    stripe_fake = _FakeStripeRefunds()
    topups = [_topup("t2", "50.00", "pi_2"), _topup("t1", "50.00", "pi_1")]
    with ExitStack() as stack:
        _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, topups)
        first, second = await asyncio.gather(_winddown(), _winddown())

    assert stripe_fake.calls == 4  # each run asked Stripe for both refunds...
    assert len(stripe_fake.by_key) == 2  # ...but Stripe only ever made two
    assert sorted(first["stripe_refund_ids"]) == sorted(second["stripe_refund_ids"])
    assert len(ledger.rows) == 1  # one ledger debit
    assert ledger.balance == Decimal("150.00")  # $100 debited for $100 refunded, not $200
    assert sorted([first["ledger_debit_deduped"], second["ledger_debit_deduped"]]) == [False, True]
    assert not first["ledger_write_failed"] and not second["ledger_write_failed"]


@pytest.mark.anyio
async def test_repeated_winddown_for_same_refunds_is_deduplicated(mock_supabase_client, caplog):
    ledger = _FakeWalletLedger("250.00")
    stripe_fake = _FakeStripeRefunds()
    topups = [_topup("t2", "50.00", "pi_2"), _topup("t1", "50.00", "pi_1")]
    with ExitStack() as stack:
        _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, topups)
        first = await _winddown()
        with caplog.at_level("WARNING"):
            second = await _winddown()

    assert len(ledger.rows) == 1
    assert ledger.balance == Decimal("150.00")
    assert first["ledger_debit_deduped"] is False
    assert second["ledger_debit_deduped"] is True
    assert any("already recorded by an earlier wind-down run" in r.getMessage() for r in caplog.records)


@pytest.mark.anyio
async def test_different_refund_set_is_not_deduplicated(mock_supabase_client):
    """A key built from the wallet id alone would wrongly swallow the second
    debit here; the refund-id-derived key must not."""
    ledger = _FakeWalletLedger("250.00")
    stripe_fake = _FakeStripeRefunds()
    with ExitStack() as stack:
        _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, [_topup("t1", "50.00", "pi_1")])
        first = await _winddown()
    with ExitStack() as stack:
        _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, [_topup("t3", "30.00", "pi_3")])
        second = await _winddown()

    assert first["stripe_refund_ids"] != second["stripe_refund_ids"]
    assert len(ledger.rows) == 2
    assert [r["amount"] for r in ledger.rows] == [Decimal("-50.00"), Decimal("-30.00")]
    assert ledger.balance == Decimal("170.00")
    assert first["ledger_debit_deduped"] is False and second["ledger_debit_deduped"] is False


# ── full close flow: route CAS + service key together ──────────────────


class _FakeCompanyRow:
    def __init__(self, status):
        self.row = corporate_account_row(status, id="c1", stripe_customer_id="cus_1")

    async def get(self, validated_id):
        snapshot = dict(self.row)
        await asyncio.sleep(0)
        return snapshot

    async def update(self, company_id, status, expected_status=None):
        if expected_status is not None and self.row["status"] != expected_status:
            return None
        self.row["status"] = status
        return dict(self.row)


@pytest.mark.anyio
async def test_two_concurrent_close_requests_produce_exactly_one_debit(mock_supabase_client):
    from routes.corporate_accounts import CompanyStatusTransition, change_company_status

    route = "routes.corporate_accounts."
    company = _FakeCompanyRow("active")
    ledger = _FakeWalletLedger("250.00")
    stripe_fake = _FakeStripeRefunds()
    topups = [_topup("t2", "50.00", "pi_2"), _topup("t1", "50.00", "pi_1")]

    async def _close():
        try:
            return await change_company_status("c1", CompanyStatusTransition(status="closed"), {"id": "a1"})
        except HTTPException as exc:
            return exc

    with ExitStack() as stack:
        _dry_run_patches(stack, mock_supabase_client, ledger, stripe_fake, topups)
        stack.enter_context(patch(route + "get_corporate_account_by_id", AsyncMock(side_effect=company.get)))
        stack.enter_context(patch("db_supabase.update_corporate_account_status", AsyncMock(side_effect=company.update)))
        stack.enter_context(patch(route + "get_corporate_wallet_by_company", AsyncMock(return_value=None)))
        stack.enter_context(
            patch(
                route + "get_app_settings",
                AsyncMock(
                    return_value={
                        "corporate_suspend_cancels_pre_pickup_rides": False,
                        "corporate_close_refunds_wallet_balance": True,
                    }
                ),
            )
        )
        stack.enter_context(patch(route + "cancel_subscription", AsyncMock(return_value={})))
        audit = stack.enter_context(patch(route + "log_admin_action", AsyncMock()))
        results = await asyncio.gather(_close(), _close())

    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, HTTPException) and r.status_code == 409 for r in results) == 1
    assert len(ledger.rows) == 1
    assert ledger.balance == Decimal("150.00")
    assert len(stripe_fake.by_key) == 2
    winddown = audit.call_args.kwargs["details"]["wallet_winddown"]
    assert winddown["refunded_total"] == "100.00"
    assert winddown["ledger_debit_deduped"] is False
