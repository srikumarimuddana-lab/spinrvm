"""Endpoint tests for the admin legacy-wallet-import routes.

The service layer (services/wallet_import_service.py) talks to Supabase, so we
patch its module-level ``supabase`` with an in-memory fake that also
implements a faithful-enough ``wallet_apply_delta`` RPC (lock/dedup/floor),
matching this repo's existing per-file fake-client convention (see
test_admin_booking_import.py's own docstring). log_admin_action is stubbed to
avoid a real audit write.

The money-planning contract itself is covered in test_wallet_import_service.py;
these tests cover the HTTP layer: three-file upload handling, the caps, the
super-admin boundary, refuse-without-4xx on a dirty plan, that validate never
writes, and end-to-end commit through the fake RPC (including dedup on a
re-send).
"""

import csv
import io
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def staff_admin_override():
    """A non-super_admin who has somehow passed the router gate."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin_2",
        "role": "admin",
        "modules": ["rides", "users", "drivers"],
    }
    yield
    app.dependency_overrides.pop(get_admin_user, None)


# --- fixtures mirroring the (inferred) legacy export shape -----------------

WALLET_ROW = {
    "_id": "wal-1",
    "customer_id": "cus-1",
    "driver_id": "",
    "amount": "50.00",
    "type": "from_bank",
    "status": "add",
}
CUSTOMER = {"_id": "cus-1", "phone": "3065551111"}
DRIVER = {"_id": "drv-legacy-1", "phone": "3065552222"}


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue().encode()


# --- in-memory Supabase fake, with a real-enough wallet_apply_delta RPC ----


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, vals):
        allowed = set(vals)
        self._filters.append((col, allowed))
        return self

    def limit(self, _n):
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _Result(list(self._insert))
        rows = list(self.store.get(self.table, []))
        for col, val in self._filters:
            if isinstance(val, set):
                rows = [r for r in rows if r.get(col) in val]
            else:
                rows = [r for r in rows if r.get(col) == val]
        return _Result(rows)


class _RpcCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, name, params):
        return _RpcCall(self._wallet_apply_delta(name, params))

    def _wallet_apply_delta(self, name, params):
        assert name == "wallet_apply_delta"
        wallets = self.store.setdefault("wallets", [])
        txns = self.store.setdefault("wallet_transactions", [])
        wallet = next((w for w in wallets if w["id"] == params["p_wallet_id"]), None)
        if wallet is None:
            raise RuntimeError(f"wallet not found or suspended: {params['p_wallet_id']}")

        existing = next(
            (
                t
                for t in txns
                if t["wallet_id"] == params["p_wallet_id"]
                and t.get("reference_id") == params["p_reference_id"]
                and t["type"] == params["p_type"]
            ),
            None,
        )
        if existing is not None:
            return _Result(
                [
                    {
                        "transaction_id": existing["id"],
                        "balance_after": existing["balance_after"],
                        "applied_delta": existing["amount"],
                        "deduped": True,
                    }
                ]
            )

        current = Decimal(str(wallet.get("balance", "0")))
        delta = Decimal(params["p_delta"])
        new_balance = current + delta
        if new_balance < 0:
            raise RuntimeError(f"wallet_below_floor: new={new_balance} floor=0")

        wallet["balance"] = str(new_balance)
        txn_id = f"txn-{len(txns) + 1}"
        txns.append(
            {
                "id": txn_id,
                "wallet_id": params["p_wallet_id"],
                "user_id": params["p_user_id"],
                "type": params["p_type"],
                "amount": str(delta),
                "balance_after": str(new_balance),
                "reference_id": params["p_reference_id"],
            }
        )
        return _Result(
            [
                {
                    "transaction_id": txn_id,
                    "balance_after": str(new_balance),
                    "applied_delta": str(delta),
                    "deduped": False,
                }
            ]
        )


def _fresh_store():
    return {
        "users": [{"id": "user-1", "phone": "+13065551111"}],
        "drivers": [{"id": "drv-1", "user_id": "user-2", "phone": "+13065552222"}],
        "wallets": [],
        "wallet_transactions": [],
    }


def _patches(store):
    return (
        patch("services.wallet_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.wallet_import.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _files(wallets=None, customers=None, drivers=None):
    return {
        "wallets_csv": ("wallets.csv", _csv_bytes(wallets or [WALLET_ROW]), "text/csv"),
        "customers_csv": ("customers.csv", _csv_bytes(customers or [CUSTOMER]), "text/csv"),
        "drivers_csv": ("drivers.csv", _csv_bytes(drivers or [DRIVER]), "text/csv"),
    }


def _post(test_client, path, files=None, data=None):
    return test_client.post(path, files=files if files is not None else _files(), data=data or {})


# --- validate ----------------------------------------------------------------


def test_validate_clean_export_reports_without_writing(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["target_rows"] == 1
    assert body["errors"] == []
    assert store["wallets"] == []
    assert store["wallet_transactions"] == []


def test_validate_report_carries_no_pii(test_client, super_admin_override):
    bad = {**WALLET_ROW, "type": "mystery_bucket"}  # unrecognized -> error
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate", files=_files(wallets=[bad]))
    body = resp.json()
    assert body["can_commit"] is False
    assert body["errors"]
    blob = resp.text
    for pii in ("3065551111", "3065552222"):
        assert pii not in blob, f"PII leaked into report: {pii}"
    for item in body["errors"]:
        assert set(item) == {"row_num", "old_id", "field", "message"}


def test_validate_rejects_non_utf8_and_names_the_file(test_client, super_admin_override):
    store = _fresh_store()
    files = _files()
    files["customers_csv"] = ("customers.csv", b"\xff\xfe_id,phone\n", "text/csv")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate", files=files)
    assert resp.status_code == 422
    assert "customers" in resp.json()["detail"]


def test_validate_rejects_empty_file_and_names_it(test_client, super_admin_override):
    store = _fresh_store()
    files = _files()
    files["drivers_csv"] = ("drivers.csv", b"", "text/csv")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate", files=files)
    assert resp.status_code == 422
    assert "drivers" in resp.json()["detail"]


def test_validate_rejects_oversize_file(test_client, super_admin_override):
    from routes.admin import wallet_import as mod

    store = _fresh_store()
    files = _files()
    files["wallets_csv"] = ("wallets.csv", b"x" * (mod.MAX_CSV_BYTES + 1), "text/csv")
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate", files=files)
    assert resp.status_code == 413
    assert "wallets" in resp.json()["detail"]


def test_validate_requires_all_three_files(test_client, super_admin_override):
    store = _fresh_store()
    files = _files()
    del files["drivers_csv"]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate", files=files)
    assert resp.status_code == 422


# --- authorization -------------------------------------------------------


def test_non_super_admin_is_refused(test_client, staff_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/validate")
    assert resp.status_code == 403
    assert store["wallets"] == []


def test_non_super_admin_cannot_commit(test_client, staff_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/commit")
    assert resp.status_code == 403
    assert store["wallets"] == []


# --- commit ----------------------------------------------------------------


def test_commit_applies_delta_and_creates_wallet(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/commit")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["applied"] == 1
    assert body["deduped"] == 0
    assert body["failed"] == 0
    assert len(store["wallets"]) == 1
    assert store["wallets"][0]["user_id"] == "user-1"
    assert Decimal(store["wallets"][0]["balance"]) == Decimal("50.00")


def test_commit_refuses_dirty_plan_with_200_and_full_report(test_client, super_admin_override):
    bad = {**WALLET_ROW, "status": "withdraw"}  # unrecognized -> error
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/commit", files=_files(wallets=[bad]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["wallets"] == []


def test_commit_is_idempotent_on_resend(test_client, super_admin_override):
    """Re-sending the same wallets CSV a second time must dedup via the RPC,
    not double-credit the wallet."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        first = _post(test_client, "/api/admin/wallets/import/commit")
        second = _post(test_client, "/api/admin/wallets/import/commit")
    assert first.json()["applied"] == 1
    assert second.json()["deduped"] == 1
    assert second.json()["applied"] == 0
    assert len(store["wallets"]) == 1
    assert Decimal(store["wallets"][0]["balance"]) == Decimal("50.00")


def test_commit_driver_owned_entry_uses_drivers_user_id(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    driver_row = {
        **WALLET_ROW,
        "_id": "wal-2",
        "customer_id": "",
        "driver_id": "drv-legacy-1",
        "type": "from_driver_refer",
        "amount": "20.00",
    }
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/wallets/import/commit", files=_files(wallets=[driver_row]))
    assert resp.json()["committed"] is True
    assert store["wallets"][0]["user_id"] == "user-2"  # drivers.user_id, not drivers.id


def test_commit_audits_counts_only(test_client, super_admin_override):
    store = _fresh_store()
    audit = AsyncMock(return_value="audit-1")
    with (
        patch("services.wallet_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.wallet_import.log_admin_action", audit),
    ):
        _post(test_client, "/api/admin/wallets/import/commit")
    audit.assert_awaited_once()
    args = audit.await_args.args
    assert args[1] == "legacy_wallet_import"
    assert args[2] == "wallets"
    payload = args[4]
    assert payload["applied"] == 1
    blob = str(payload)
    for pii in ("3065551111", "3065552222"):
        assert pii not in blob
