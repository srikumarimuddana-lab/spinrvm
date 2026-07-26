"""Endpoint tests for the admin Stripe mapping-import routes.

Supabase is faked in-memory on BOTH service modules (the mapping service and
driver_import_service, whose _select_in it reuses); Stripe retrieves are
patched; log_admin_action and the background KYC sync are stubbed.
"""

import re
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    """Routes sit behind require_module("drivers"); super_admin passes."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _csv(header, *rows):
    return ("\n".join([header, *rows]) + "\n").encode()


DRIVERS_CSV = _csv("old_driver_id,stripe_account_id", "OLD-1,acct_A1")
RIDERS_CSV = _csv("phone,stripe_customer_id", "3065559999,cus_C1")


class _Result:
    def __init__(self, data):
        self.data = data


def _resolve(row, col):
    """Resolve PostgREST JSONB paths (``meta->child->>leaf``) like the real API."""
    if "->" not in col:
        return row.get(col)
    value = row
    for part in re.split(r"->>?", col):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._update = None

    def select(self, *_a, **_k):
        return self

    def update(self, fields):
        self._update = dict(fields)
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def is_(self, col, val):
        self._filters.append(("is", col, val))
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if _resolve(r, col) == val]
            elif op == "in":
                allowed = {str(v) for v in val}
                rows = [r for r in rows if str(_resolve(r, col)) in allowed]
            elif op == "is":
                rows = [r for r in rows if _resolve(r, col) is None]
        if self._update is not None:
            for r in rows:
                r.update(self._update)
        return _Result(rows)


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _driver_row():
    return {
        "id": "drv-1",
        "phone": "+13065551234",
        "stripe_account_id": None,
        "stripe_payouts_enabled": False,
        "stripe_details_submitted": False,
        "legacy_import_metadata": {"source": "legacy_saskatoon_driver_import", "old_driver_id": "OLD-1"},
    }


def _user_row():
    return {
        "id": "usr-1",
        "phone": "+13065559999",
        "email": "rider@example.com",
        "stripe_customer_id": None,
        "legacy_import_metadata": {},
    }


ACCT_PAYLOAD = {
    "id": "acct_A1",
    "type": "express",
    "country": "CA",
    "business_type": "individual",
    "details_submitted": True,
    "payouts_enabled": True,
    "livemode": False,
    "capabilities": {"transfers": "active"},
    "requirements": {"currently_due": [], "disabled_reason": None},
}
CUS_PAYLOAD = {"id": "cus_C1", "livemode": False, "metadata": {}}


def _patches(store, *, kyc_sync=None):
    import stripe

    fake = _FakeSupabase(store)
    return (
        patch("services.stripe_mapping_import_service.supabase", fake),
        patch("services.driver_import_service.supabase", fake),
        patch("routes.admin.stripe_import.log_admin_action", AsyncMock(return_value="audit-1")),
        patch(
            "routes.admin.stripe_import.get_app_settings", AsyncMock(return_value={"stripe_secret_key": "sk_test_abc"})
        ),
        patch.object(stripe.Account, "retrieve", lambda _id, api_key=None: dict(ACCT_PAYLOAD)),
        patch.object(stripe.Customer, "retrieve", lambda _id, api_key=None: dict(CUS_PAYLOAD)),
        patch(
            "services.stripe_mapping_import_service.sync_kyc_after_commit",
            kyc_sync or AsyncMock(return_value={"ok": 1, "failed": 0, "failed_driver_ids": []}),
        ),
    )


def _post(test_client, path, csv_bytes, kind="drivers", **data):
    return test_client.post(
        path,
        files={"mapping_csv": ("mapping.csv", csv_bytes, "text/csv")},
        data={"kind": kind, **data},
    )


def test_validate_clean_csv_no_writes(test_client, super_admin_override):
    store = {"drivers": [_driver_row()], "users": []}
    ps = _patches(store)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/validate", DRIVERS_CSV)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"] == {"rows": 1, "to_map": 1, "skipped_already_mapped": 0}
    assert body["errors"] == []
    assert store["drivers"][0]["stripe_account_id"] is None  # no writes


def test_validate_surfaces_row_errors(test_client, super_admin_override):
    store = {"drivers": [], "users": []}  # nothing to match against
    ps = _patches(store)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/validate", DRIVERS_CSV)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert [e["field"] for e in body["errors"]] == ["no_match"]


def test_non_super_admin_is_403(test_client):
    # Module grants are not enough: writing stripe_account_id redirects a
    # driver's payout destination, so every endpoint is super_admin-only.
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_2", "role": "admin"}
    try:
        ps = _patches({"drivers": [], "users": []})
        with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
            for path in ("/api/admin/stripe/import/validate", "/api/admin/stripe/import/commit"):
                resp = _post(test_client, path, DRIVERS_CSV)
                assert resp.status_code == 403, resp.text
            resp = test_client.get("/api/admin/stripe/import/status", params={"batch": "b1"})
            assert resp.status_code == 403, resp.text
    finally:
        app.dependency_overrides.pop(get_admin_user, None)


def test_validate_rejects_unknown_kind(test_client, super_admin_override):
    ps = _patches({"drivers": [], "users": []})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/validate", DRIVERS_CSV, kind="corporate")
    assert resp.status_code == 422, resp.text


def test_commit_refuses_on_errors_no_writes(test_client, super_admin_override):
    store = {"drivers": [], "users": []}
    ps = _patches(store)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/commit", DRIVERS_CSV)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]


def test_commit_maps_driver_and_starts_kyc_sync(test_client, super_admin_override):
    store = {"drivers": [_driver_row()], "users": []}
    kyc_sync = AsyncMock(return_value={"ok": 1, "failed": 0, "failed_driver_ids": []})
    ps = _patches(store, kyc_sync=kyc_sync)
    with ps[0], ps[1], ps[2] as audit, ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/commit", DRIVERS_CSV, batch="batch-1")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated_drivers"] == 1
    assert body["conflicts"] == []
    assert body["kyc_sync"] == "started"
    row = store["drivers"][0]
    assert row["stripe_account_id"] == "acct_A1"
    assert row["legacy_import_metadata"]["stripe_migration"]["batch"] == "batch-1"
    kyc_sync.assert_called_once_with(["drv-1"], "batch-1")
    audit.assert_awaited_once()
    # Audit details carry counts only, never CSV contents.
    details = audit.call_args.args[4]
    assert details == {"updated_drivers": 1, "updated_users": 0, "conflicts": 0}


def test_commit_riders_kind(test_client, super_admin_override):
    store = {"drivers": [], "users": [_user_row()]}
    ps = _patches(store)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/commit", RIDERS_CSV, kind="riders")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated_users"] == 1
    assert body["kyc_sync"] == "not_applicable"
    assert store["users"][0]["stripe_customer_id"] == "cus_C1"


def test_row_and_size_caps(test_client, super_admin_override):
    rows = [f"OLD-{i},acct_{i}" for i in range(201)]
    ps = _patches({"drivers": [], "users": []})
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = _post(test_client, "/api/admin/stripe/import/validate", _csv("old_driver_id,stripe_account_id", *rows))
        assert resp.status_code == 422, resp.text
        big = b"old_driver_id,stripe_account_id\n" + b"x" * 1_000_001
        resp = _post(test_client, "/api/admin/stripe/import/validate", big)
        assert resp.status_code == 413, resp.text


def test_status_aggregates_batch(test_client, super_admin_override):
    done = _driver_row()
    done.update(
        {
            "stripe_account_id": "acct_A1",
            "stripe_payouts_enabled": True,
            "stripe_details_submitted": True,
            "legacy_import_metadata": {"stripe_migration": {"batch": "batch-1", "kyc_sync": "ok"}},
        }
    )
    pending = _driver_row()
    pending.update(
        {
            "id": "drv-2",
            "stripe_account_id": "acct_A2",
            "legacy_import_metadata": {"stripe_migration": {"batch": "batch-1"}},
        }
    )
    other_batch = _driver_row()
    other_batch.update(
        {"id": "drv-3", "legacy_import_metadata": {"stripe_migration": {"batch": "batch-2", "kyc_sync": "ok"}}}
    )
    store = {"drivers": [done, pending, other_batch], "users": []}
    ps = _patches(store)
    with ps[0], ps[1], ps[2], ps[3], ps[4], ps[5], ps[6]:
        resp = test_client.get("/api/admin/stripe/import/status", params={"batch": "batch-1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["drivers"] == 2
    assert body["kyc_sync"] == {"ok": 1, "pending": 1}
    assert body["payouts_enabled"] == 1
    assert body["details_submitted"] == 1
