"""Endpoint tests for the admin Stripe payout-sync routes.

Supabase is faked in-memory on BOTH service modules (the sync service and
driver_import_service, whose _select_in it reuses); stripe.Transfer.list is
patched with canned per-account histories; log_admin_action is stubbed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


class _Result:
    def __init__(self, data):
        self.data = data


class _UniqueViolation(Exception):
    code = "23505"


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None

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

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._insert is not None:
            existing = {r.get("id") for r in rows}
            for r in self._insert:
                if r.get("id") in existing:
                    raise _UniqueViolation("duplicate key value")
            rows.extend(dict(r) for r in self._insert)
            return _Result(list(self._insert))
        out = list(rows)
        for op, col, val in self._filters:
            if op == "eq":
                out = [r for r in out if r.get(col) == val]
            elif op == "in":
                allowed = {str(v) for v in val}
                out = [r for r in out if str(r.get(col)) in allowed]
        return _Result(out)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


class _FakeTransferList:
    def __init__(self, transfers):
        self._transfers = transfers

    def auto_paging_iter(self):
        return iter(self._transfers)


def _transfer(tr_id, amount, created=1735689600, **extra):
    return {"id": tr_id, "amount": amount, "amount_reversed": 0, "currency": "cad", "created": created, **extra}


def _patches(store, by_account, seen_params=None):
    fake = _FakeSupabase(store)

    def _list(**params):
        if seen_params is not None:
            seen_params.append(dict(params))
        return _FakeTransferList(by_account.get(params.get("destination"), []))

    return (
        patch("services.stripe_payout_sync_service.supabase", fake),
        patch("services.driver_import_service.supabase", fake),
        patch("routes.admin.stripe_payout_sync.log_admin_action", AsyncMock(return_value="audit-1")),
        patch(
            "routes.admin.stripe_payout_sync.get_app_settings",
            AsyncMock(return_value={"stripe_secret_key": "sk_test_abc"}),
        ),
        patch("stripe.Transfer.list", side_effect=_list),
    )


def _store():
    return {
        "drivers": [{"id": "drv-1", "stripe_account_id": "acct_A1"}],
        "payouts": [],
    }


def test_validate_reports_plan_without_writes(test_client, super_admin_override):
    store = _store()
    ps = _patches(store, {"acct_A1": [_transfer("tr_1", 2999)]})
    with ps[0], ps[1], ps[2], ps[3], ps[4]:
        resp = test_client.post("/api/admin/stripe/payout-sync/validate", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["stats"]["payouts_planned"] == 1
    assert body["stats"]["sum_planned"] == 29.99
    assert store["payouts"] == []  # dry run — no writes


def test_validate_year_scopes_stripe_window(test_client, super_admin_override):
    seen: list[dict] = []
    ps = _patches(_store(), {"acct_A1": []}, seen_params=seen)
    with ps[0], ps[1], ps[2], ps[3], ps[4]:
        resp = test_client.post("/api/admin/stripe/payout-sync/validate", json={"year": 2025})
    assert resp.status_code == 200, resp.text
    assert seen[0]["created"]["gte"] == 1735689600  # 2025-01-01T00:00:00Z
    assert seen[0]["created"]["lte"] == 1767225599  # 2025-12-31T23:59:59Z


def test_commit_inserts_and_audits(test_client, super_admin_override):
    store = _store()
    ps = _patches(store, {"acct_A1": [_transfer("tr_1", 2999), _transfer("tr_2", 5000)]})
    with ps[0], ps[1], ps[2] as audit, ps[3], ps[4]:
        resp = test_client.post("/api/admin/stripe/payout-sync/commit", json={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["inserted"] == 2
    assert {p["id"] for p in store["payouts"]} == {"stripe-sync-tr_1", "stripe-sync-tr_2"}
    assert all(p["payout_type"] == "stripe_sync" and p["status"] == "completed" for p in store["payouts"])
    audit.assert_awaited_once()


def test_commit_refuses_on_plan_errors(test_client, super_admin_override):
    store = _store()
    # Non-CAD currency is a plan error — commit must refuse and write nothing.
    ps = _patches(store, {"acct_A1": [_transfer("tr_usd", 1000, currency="usd")]})
    with ps[0], ps[1], ps[2] as audit, ps[3], ps[4]:
        resp = test_client.post("/api/admin/stripe/payout-sync/commit", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["can_commit"] is False
    assert store["payouts"] == []
    audit.assert_not_awaited()


def test_recommit_converges_on_existing_rows(test_client, super_admin_override):
    store = _store()
    store["payouts"] = [
        {
            "id": "stripe-sync-tr_1",
            "driver_id": "drv-1",
            "stripe_transfer_id": "tr_1",
            "stripe_payout_id": None,
            "payout_type": "stripe_sync",
            "status": "completed",
        }
    ]
    ps = _patches(store, {"acct_A1": [_transfer("tr_1", 2999)]})
    with ps[0], ps[1], ps[2], ps[3], ps[4]:
        resp = test_client.post("/api/admin/stripe/payout-sync/commit", json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["inserted"] == 0
    assert body["stats"]["already_tracked"] == 1
    assert len(store["payouts"]) == 1


def test_non_super_admin_is_403(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin"}
    try:
        ps = _patches(_store(), {"acct_A1": []})
        with ps[0], ps[1], ps[2], ps[3], ps[4]:
            resp = test_client.post("/api/admin/stripe/payout-sync/validate", json={})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(get_admin_user, None)
