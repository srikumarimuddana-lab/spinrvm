"""Unit tests for backend/services/wallet_import_service.py.

Two layers, tested separately per this repo's own convention
(test_wallet_repo.py's docstring): build_plan()'s matching/parsing logic
against a fake table-query Supabase client (no real DB), and commit_plan()'s
per-row RPC-call behavior against a mocked .rpc() -- the RPC's own
locking/idempotency/floor semantics are already covered by
test_wallet_apply_delta_contract.py and test_wallet_repo.py, not re-tested
here.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from backend.services import wallet_import_service as svc

BATCH = "20260824000000"


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        if isinstance(rows, list):
            self.store.setdefault(self.table, []).extend(rows)
        else:
            self.store.setdefault(self.table, []).append(rows)
        return self

    def execute(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
        return _FakeExecute(rows)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _FakeQuery(name, self.store)


def _install_fake(monkeypatch, *, users=None, drivers=None, wallets=None):
    store = {
        "users": users if users is not None else [{"id": "user-1", "phone": "+13065551111"}],
        "drivers": drivers if drivers is not None else [{"id": "drv-1", "user_id": "user-2", "phone": "+13065552222"}],
        "wallets": wallets or [],
    }
    monkeypatch.setattr(svc, "supabase", _FakeSupabase(store))
    return store


def _customer(**overrides):
    row = {"_id": "cus-1", "phone": "3065551111"}
    row.update(overrides)
    return row


def _driver(**overrides):
    row = {"_id": "drv-legacy-1", "phone": "3065552222"}
    row.update(overrides)
    return row


def _wallet_row(**overrides):
    """A legacy rider top-up: $50 added, from_bank."""
    row = {
        "_id": "wal-1",
        "customer_id": "cus-1",
        "driver_id": "",
        "amount": "50.00",
        "type": "from_bank",
        "status": "add",
    }
    row.update(overrides)
    return row


def _plan(rows, customers=None, drivers=None):
    return svc.build_plan(
        rows,
        customers if customers is not None else [_customer()],
        drivers if drivers is not None else [_driver()],
        batch=BATCH,
    )


# --------------------------------------------------------------------------
# Column validation
# --------------------------------------------------------------------------


def test_empty_csv_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([])
    assert plan.errors
    assert "empty" in plan.errors[0].message


def test_missing_required_column_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    bad_row = _wallet_row()
    del bad_row["amount"]
    plan = _plan([bad_row])
    assert any(e.field == "amount" for e in plan.errors)
    assert plan.deltas_to_apply == []


# --------------------------------------------------------------------------
# Matching: rider vs driver ownership, unmatched accounts
# --------------------------------------------------------------------------


def test_rider_wallet_entry_matched_by_phone(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row()])
    assert not plan.errors
    (entry,) = plan.deltas_to_apply
    assert entry["user_id"] == "user-1"
    assert entry["type_"] == "top_up"
    assert entry["delta"] == Decimal("50.00")


def test_driver_wallet_entry_uses_drivers_user_id_not_drivers_id(monkeypatch):
    """Driver-owned wallet rows must resolve to drivers.user_id (the shared
    users.id wallets.user_id references), never drivers.id itself."""
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(customer_id="", driver_id="drv-legacy-1", type="from_driver_refer", amount="20.00")])
    (entry,) = plan.deltas_to_apply
    assert entry["user_id"] == "user-2"  # drivers.user_id, not "drv-1"
    assert entry["type_"] == "referral_reward"


def test_unmatched_account_is_skipped_not_fabricated(monkeypatch):
    _install_fake(monkeypatch, users=[], drivers=[])
    plan = _plan([_wallet_row()])
    assert plan.deltas_to_apply == []
    assert plan.stats["skipped_unmatched"] == 1
    assert any("no matching" in w.message for w in plan.warnings)


def test_row_with_no_customer_or_driver_id_is_skipped(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(customer_id="", driver_id="")])
    assert plan.deltas_to_apply == []
    assert plan.stats["skipped_unmatched"] == 1


# --------------------------------------------------------------------------
# Row-level validation: id, amount, type, status
# --------------------------------------------------------------------------


def test_missing_legacy_id_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(_id="")])
    assert any(e.field == "_id" for e in plan.errors)
    assert plan.deltas_to_apply == []


def test_duplicate_legacy_id_in_csv_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(), _wallet_row()])
    assert any("duplicate" in e.message for e in plan.errors)
    assert len(plan.deltas_to_apply) == 1


def test_zero_amount_is_skipped_not_errored(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(amount="0")])
    assert not plan.errors
    assert plan.deltas_to_apply == []
    assert plan.stats["skipped_zero_amount"] == 1


def test_unrecognized_legacy_type_is_an_error(monkeypatch):
    """Never guess at an unmapped legacy type -- see LEGACY_TYPE_TO_TXN_TYPE's
    own comment on why only these three are recognized."""
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(type="mystery_bucket")])
    assert any(e.field == "type" for e in plan.errors)
    assert plan.deltas_to_apply == []


def test_unrecognized_legacy_status_is_an_error(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(status="withdraw")])
    assert any(e.field == "status" for e in plan.errors)
    assert plan.deltas_to_apply == []


def test_deduct_status_produces_negative_delta(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(status="deduct", amount="15.00")])
    (entry,) = plan.deltas_to_apply
    assert entry["delta"] == Decimal("-15.00")
    assert plan.stats["sum_deduct"] == pytest.approx(15.00)
    assert plan.stats["sum_add"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Reference id / metadata (idempotency + audit trail)
# --------------------------------------------------------------------------


def test_reference_id_is_deterministic_from_legacy_id(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(_id="wal-42")])
    (entry,) = plan.deltas_to_apply
    assert entry["reference_id"] == "legacy-wallet-wal-42"


def test_metadata_carries_provenance_no_pii(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row()])
    (entry,) = plan.deltas_to_apply
    meta = entry["metadata"]
    assert meta["source"] == svc.IMPORT_SOURCE
    assert meta["batch"] == BATCH
    assert meta["old_wallet_entry_id"] == "wal-1"
    assert meta["old_legacy_type"] == "from_bank"
    assert meta["old_legacy_status"] == "add"
    blob = repr(meta)
    assert "3065551111" not in blob


def test_stats_reconcile_to_the_batch_total(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan(
        [
            _wallet_row(_id="wal-1", amount="50.00", status="add"),
            _wallet_row(
                _id="wal-2",
                customer_id="",
                driver_id="drv-legacy-1",
                type="from_driver_refer",
                amount="10.00",
                status="deduct",
            ),
        ]
    )
    assert plan.stats["target_rows"] == 2
    assert plan.stats["rider_rows"] == 1
    assert plan.stats["driver_rows"] == 1
    assert plan.stats["sum_add"] == pytest.approx(50.00)
    assert plan.stats["sum_deduct"] == pytest.approx(10.00)
    assert plan.stats["sum_net"] == pytest.approx(40.00)


# --------------------------------------------------------------------------
# commit_plan() -- per-row RPC call behavior
# --------------------------------------------------------------------------


def _rpc_supabase(store, rpc_return):
    """A fake supabase client whose .table() serves wallets (for
    get-or-create) and whose .rpc() returns a canned wallet_apply_delta row."""
    sb = _FakeSupabase(store)
    sb.rpc = MagicMock(return_value=MagicMock(execute=MagicMock(return_value=_FakeExecute(rpc_return))))
    return sb


def test_commit_refuses_when_plan_has_errors(monkeypatch):
    _install_fake(monkeypatch)
    plan = _plan([_wallet_row(amount="")])  # unparseable-but-present -> 0 -> skipped, not an error
    plan.errors.append(svc.ImportReportItem(1, "wal-x", "type", "forced error"))
    with pytest.raises(RuntimeError, match="refusing to commit"):
        svc.commit_plan(plan)


def test_commit_creates_wallet_if_missing_and_calls_rpc(monkeypatch):
    store = _install_fake(monkeypatch, wallets=[])
    plan = _plan([_wallet_row()])
    fake_sb = _rpc_supabase(
        store, [{"transaction_id": "t1", "balance_after": "50.00", "applied_delta": "50.00", "deduped": False}]
    )
    monkeypatch.setattr(svc, "supabase", fake_sb)

    results = svc.commit_plan(plan)

    assert len(store["wallets"]) == 1
    assert store["wallets"][0]["user_id"] == "user-1"
    assert results == [
        {
            "reference_id": "legacy-wallet-wal-1",
            "status": "applied",
            "transaction_id": "t1",
            "balance_after": "50.00",
            "applied_delta": "50.00",
        }
    ]
    rpc_args, _ = fake_sb.rpc.call_args
    assert rpc_args[0] == "wallet_apply_delta"
    params = rpc_args[1]
    assert params["p_wallet_id"] == store["wallets"][0]["id"]
    assert params["p_user_id"] == "user-1"
    assert params["p_type"] == "top_up"
    assert params["p_delta"] == "50.00"
    assert params["p_reference_id"] == "legacy-wallet-wal-1"


def test_commit_reuses_existing_wallet(monkeypatch):
    store = _install_fake(monkeypatch, wallets=[{"id": "existing-wallet", "user_id": "user-1"}])
    plan = _plan([_wallet_row()])
    fake_sb = _rpc_supabase(
        store, [{"transaction_id": "t1", "balance_after": "50.00", "applied_delta": "50.00", "deduped": False}]
    )
    monkeypatch.setattr(svc, "supabase", fake_sb)

    svc.commit_plan(plan)

    assert len(store["wallets"]) == 1  # no new wallet created
    rpc_args, _ = fake_sb.rpc.call_args
    assert rpc_args[1]["p_wallet_id"] == "existing-wallet"


def test_commit_reports_deduped_result(monkeypatch):
    """A re-run against the same legacy entry: the RPC's own idempotency
    returns deduped=True, and commit_plan must surface that rather than
    reporting it as a fresh apply."""
    store = _install_fake(monkeypatch, wallets=[{"id": "w1", "user_id": "user-1"}])
    plan = _plan([_wallet_row()])
    fake_sb = _rpc_supabase(
        store, [{"transaction_id": "t1", "balance_after": "50.00", "applied_delta": "50.00", "deduped": True}]
    )
    monkeypatch.setattr(svc, "supabase", fake_sb)

    (result,) = svc.commit_plan(plan)
    assert result["status"] == "deduped"


def test_commit_records_failure_without_aborting_other_rows(monkeypatch):
    """One row's RPC call raising (e.g. wallet_below_floor) must not stop the
    other independent rows in the same batch from being applied."""
    store = _install_fake(
        monkeypatch,
        users=[{"id": "user-1", "phone": "+13065551111"}, {"id": "user-3", "phone": "+13065553333"}],
        wallets=[{"id": "w1", "user_id": "user-1"}, {"id": "w3", "user_id": "user-3"}],
    )
    plan = _plan(
        [_wallet_row(_id="wal-1"), _wallet_row(_id="wal-2", customer_id="cus-3", amount="5.00")],
        customers=[_customer(), _customer(_id="cus-3", phone="3065553333")],
    )
    assert len(plan.deltas_to_apply) == 2

    calls = [
        MagicMock(execute=MagicMock(side_effect=RuntimeError("wallet_below_floor"))),
        MagicMock(
            execute=MagicMock(
                return_value=_FakeExecute(
                    [{"transaction_id": "t2", "balance_after": "5.00", "applied_delta": "5.00", "deduped": False}]
                )
            )
        ),
    ]
    fake_sb = _FakeSupabase(store)
    fake_sb.rpc = MagicMock(side_effect=calls)
    monkeypatch.setattr(svc, "supabase", fake_sb)

    results = svc.commit_plan(plan)
    assert [r["status"] for r in results] == ["failed", "applied"]
