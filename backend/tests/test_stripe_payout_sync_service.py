"""Unit tests for services/stripe_payout_sync_service.py.

Supabase is faked in-memory on BOTH the sync service and
driver_import_service (whose _select_in it reuses). Stripe's Transfer.list is
patched with a fake auto-paging list. db_supabase.get_rows is patched for the
T4A year-sum helper.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import driver_import_service as driver_svc
from backend.services import stripe_payout_sync_service as svc


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeUniqueViolation(Exception):
    code = "23505"


# Supabase's PostgREST default db-max-rows. Kept low-ish here only as a
# constant; the pagination test uses a driver count above it.
_POSTGREST_MAX_ROWS = 1000


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None
        self._order = None
        self._range = None

    def select(self, *_a, **_k):
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def limit(self, _n):
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, start, end):
        # PostgREST .range() bounds are INCLUSIVE on both ends.
        self._range = (start, end)
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._insert is not None:
            existing_ids = {r.get("id") for r in rows}
            for r in self._insert:
                if r.get("id") in existing_ids:
                    raise _FakeUniqueViolation('duplicate key value violates unique constraint "payouts_pkey"')
            rows.extend(dict(r) for r in self._insert)
            return _FakeExecute(list(self._insert))
        out = list(rows)
        for op, col, val in self._filters:
            if op == "eq":
                out = [r for r in out if r.get(col) == val]
            elif op == "in":
                allowed = {str(v) for v in val}
                out = [r for r in out if str(r.get(col)) in allowed]
        if self._order:
            col, desc = self._order
            out.sort(key=lambda r: r.get(col) or "", reverse=desc)
        if self._range:
            start, end = self._range
            out = out[start : end + 1]
        elif self._insert is None:
            # An unbounded PostgREST select is capped at db-max-rows (1000 on
            # Supabase) with NO truncation signal — model that, so a caller
            # that forgets to page is caught by the tests instead of silently
            # dropping rows in production.
            out = out[:_POSTGREST_MAX_ROWS]
        return _FakeExecute(out)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeQuery(name, self.store)


class _FakeTransferList:
    def __init__(self, transfers):
        self._transfers = transfers

    def auto_paging_iter(self):
        return iter(self._transfers)


def _transfer(tr_id, amount, *, reversed_cents=0, currency="cad", created=1735689600):
    # created default: 2025-01-01T00:00:00Z
    return {
        "id": tr_id,
        "amount": amount,
        "amount_reversed": reversed_cents,
        "currency": currency,
        "created": created,
    }


@pytest.fixture
def store(monkeypatch):
    store = {
        "drivers": [{"id": "drv_1", "stripe_account_id": "acct_A1"}],
        "payouts": [],
    }
    fake = _FakeSupabase(store)
    monkeypatch.setattr(svc, "supabase", fake)
    monkeypatch.setattr(driver_svc, "supabase", fake)
    return store


def _patch_transfers(by_account):
    """Patch stripe.Transfer.list to serve canned transfers per destination."""

    def _list(**params):
        return _FakeTransferList(by_account.get(params.get("destination"), []))

    return patch("stripe.Transfer.list", side_effect=_list)


@pytest.mark.anyio
async def test_plan_builds_rows_for_untracked_transfers(store):
    with _patch_transfers({"acct_A1": [_transfer("tr_1", 2999), _transfer("tr_2", 10000)]}):
        plan = await svc.build_plan("sk_test_x", "batch1")

    assert plan.errors == []
    assert len(plan.payouts_to_insert) == 2
    row = next(r for r in plan.payouts_to_insert if r["stripe_transfer_id"] == "tr_1")
    assert row["id"] == "stripe-sync-tr_1"
    assert row["driver_id"] == "drv_1"
    assert row["amount"] == 29.99  # 2999 cents — Decimal conversion, no float drift
    assert row["status"] == "completed"
    assert row["payout_type"] == "stripe_sync"
    assert row["created_at"].startswith("2025-01-01T00:00:00")
    assert plan.stats["payouts_planned"] == 2
    assert plan.stats["sum_planned"] == 129.99
    assert plan.stats["sum_by_year"] == {"2025": 129.99}


@pytest.mark.anyio
async def test_plan_skips_transfers_already_tracked(store):
    # tr_1 tracked via stripe_transfer_id (instant/modern), tr_2 via
    # stripe_payout_id (older standard payouts stored the transfer id there),
    # tr_3 via a previous sync run's deterministic row id.
    store["payouts"] = [
        {"id": "p1", "stripe_transfer_id": "tr_1", "stripe_payout_id": None},
        {"id": "p2", "stripe_transfer_id": None, "stripe_payout_id": "tr_2"},
        {"id": "stripe-sync-tr_3", "stripe_transfer_id": "tr_3", "stripe_payout_id": None},
    ]
    with _patch_transfers(
        {
            "acct_A1": [
                _transfer("tr_1", 1000),
                _transfer("tr_2", 1000),
                _transfer("tr_3", 1000),
                _transfer("tr_4", 1000),
            ]
        }
    ):
        plan = await svc.build_plan("sk_test_x", "batch1")

    assert plan.errors == []
    assert [r["stripe_transfer_id"] for r in plan.payouts_to_insert] == ["tr_4"]
    assert plan.stats["already_tracked"] == 3


@pytest.mark.anyio
async def test_reversals_skipped_or_netted(store):
    with _patch_transfers(
        {"acct_A1": [_transfer("tr_full", 1000, reversed_cents=1000), _transfer("tr_part", 1000, reversed_cents=250)]}
    ):
        plan = await svc.build_plan("sk_test_x", "batch1")

    assert plan.errors == []
    assert len(plan.payouts_to_insert) == 1
    assert plan.payouts_to_insert[0]["amount"] == 7.50
    assert plan.stats["fully_reversed_skipped"] == 1
    assert plan.stats["partial_reversals"] == 1
    assert any(w.field == "partial_reversal" for w in plan.warnings)


@pytest.mark.anyio
async def test_non_cad_currency_is_an_error(store):
    with _patch_transfers({"acct_A1": [_transfer("tr_usd", 1000, currency="usd")]}):
        plan = await svc.build_plan("sk_test_x", "batch1")

    assert any(e.field == "currency" for e in plan.errors)
    assert plan.payouts_to_insert == []
    with pytest.raises(RuntimeError):
        svc.commit_plan(plan)


@pytest.mark.anyio
async def test_stripe_list_failure_blocks_commit(store):
    import stripe

    with patch("stripe.Transfer.list", side_effect=stripe.error.APIError("boom")):
        plan = await svc.build_plan("sk_test_x", "batch1")

    assert any(e.field == "stripe_list_failed" and e.row_ref == "drv_1" for e in plan.errors)
    with pytest.raises(RuntimeError):
        svc.commit_plan(plan)


@pytest.mark.anyio
async def test_missing_secret_errors(store):
    plan = await svc.build_plan("", "batch1")
    assert any(e.field == "stripe_not_configured" for e in plan.errors)


@pytest.mark.anyio
async def test_driver_ids_scope_limits_targets(store):
    store["drivers"].append({"id": "drv_2", "stripe_account_id": "acct_B2"})
    calls = []

    def _list(**params):
        calls.append(params.get("destination"))
        return _FakeTransferList([])

    with patch("stripe.Transfer.list", side_effect=_list):
        plan = await svc.build_plan("sk_test_x", "batch1", driver_ids=["drv_2"])

    assert calls == ["acct_B2"]
    assert plan.stats["drivers_scanned"] == 1
    # No history in the window → surfaced so the operator can chase scenario-B
    # drivers whose old account holds the transfers.
    assert any(w.field == "no_transfers" and w.row_ref == "drv_2" for w in plan.warnings)


@pytest.mark.anyio
async def test_year_window_passed_to_stripe(store):
    seen = {}

    def _list(**params):
        seen.update(params)
        return _FakeTransferList([])

    with patch("stripe.Transfer.list", side_effect=_list):
        await svc.build_plan("sk_test_x", "batch1", created_gte=1704067200, created_lte=1735689599)

    assert seen["created"] == {"gte": 1704067200, "lte": 1735689599}


@pytest.mark.anyio
async def test_commit_inserts_and_rerun_converges(store):
    with _patch_transfers({"acct_A1": [_transfer("tr_1", 2999)]}):
        plan = await svc.build_plan("sk_test_x", "batch1")
    result = svc.commit_plan(plan)
    assert result == {"inserted": 1, "skipped_existing": 0}
    assert store["payouts"][0]["id"] == "stripe-sync-tr_1"

    # A racing/duplicate commit of the same plan hits the PK and converges.
    result2 = svc.commit_plan(plan)
    assert result2 == {"inserted": 0, "skipped_existing": 1}
    assert len(store["payouts"]) == 1

    # A re-VALIDATE after commit sees the row as tracked — nothing re-planned.
    with _patch_transfers({"acct_A1": [_transfer("tr_1", 2999)]}):
        plan2 = await svc.build_plan("sk_test_x", "batch2")
    assert plan2.payouts_to_insert == []
    assert plan2.stats["already_tracked"] == 1


@pytest.mark.anyio
async def test_all_drivers_are_paged_past_the_postgrest_row_cap(store):
    """An unbounded PostgREST select silently stops at db-max-rows (1000).
    Every mapped driver must still be scanned — a dropped driver would leave
    their payout history unsynced and under-report their T4A."""
    store["drivers"] = [
        {"id": f"drv_{i:05d}", "stripe_account_id": f"acct_{i:05d}"} for i in range(_POSTGREST_MAX_ROWS + 250)
    ]
    seen: list[str] = []

    def _list(**params):
        seen.append(params.get("destination"))
        return _FakeTransferList([])

    with patch("stripe.Transfer.list", side_effect=_list):
        plan = await svc.build_plan("sk_test_x", "batch1")

    assert plan.stats["drivers_scanned"] == _POSTGREST_MAX_ROWS + 250
    assert len(set(seen)) == _POSTGREST_MAX_ROWS + 250


@pytest.mark.anyio
async def test_driver_synced_earnings_for_year_sums_decimal():
    rows = [{"amount": 10.10}, {"amount": 20.25}, {"amount": "5.00"}]
    with patch.object(svc, "db_supabase") as db:
        db.get_rows = AsyncMock(return_value=rows)
        total = await svc.driver_synced_earnings_for_year("drv_1", 2025)

    assert total == Decimal("35.35")
    filters = db.get_rows.call_args.args[1]
    assert filters["payout_type"] == "stripe_sync"
    assert filters["created_at"]["$gte"].startswith("2025-01-01")
    assert filters["created_at"]["$lt"].startswith("2026-01-01")
