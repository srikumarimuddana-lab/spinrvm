"""Endpoint tests for the admin legacy saved-address backfill routes
(routes/admin/legacy_saved_address_backfill.py, Phase 4 of the 2026-08-27
migration plan).

Mirrors test_admin_legacy_vehicle_history_backfill.py's structure/fake-
Supabase harness for a two-CSV input. The service layer
(services/saved_address_import_service.py) talks to Supabase, so we patch
its module-level ``supabase`` with an in-memory fake. log_admin_action is
stubbed to avoid a real audit write.

The matching/filtering logic itself is exercised at the service layer
(test_saved_address_import_service.py) -- these tests cover the route
wrapping: two-file upload, the combined-hash commit-token binding, and the
validate/commit contract.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    """This router sits behind require_module("users"), same as
    rider_import_router; a super_admin passes that gate regardless of the
    modules claim."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


# customer_addresses.csv shape: _id, customer_id (a Mongo customer _id, not
# a Stripe id -- see service-layer test file), lat, long, name (the full
# address text), type (home/work/blank), created_at (epoch ms).
ADDRESS_HEADER = "_id,customer_id,lat,long,name,type,created_at"
GOOD_ADDRESS_ROW = 'OA-1,OC-1,52.1332,-106.6700,"123 Main Street, Saskatoon, SK S7K 0J5",home,1700000000000'
OUT_OF_PROVINCE_ROW = 'OA-2,OC-1,30.7190586,76.7487044,"Some address in India",home,1700000000000'

# customers.csv shape: only _id/phone matter to this crosswalk.
CUSTOMERS_HEADER = "_id,name,phone,email,created_at"
GOOD_CUSTOMERS_ROW = "OC-1,Jane Doe,3065551234,jane@example.com,1700000000000"


def _address_csv(*rows: str) -> bytes:
    return ("\n".join([ADDRESS_HEADER, *rows]) + "\n").encode()


def _customers_csv(*rows: str) -> bytes:
    return ("\n".join([CUSTOMERS_HEADER, *rows]) + "\n").encode()


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._filters = []
        self._insert = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def in_(self, col, vals):
        self._filters.append(("in", col, list(vals)))
        return self

    def insert(self, rows):
        self._insert = rows if isinstance(rows, list) else [rows]
        return self

    def _matched(self):
        rows = list(self.store.get(self.table, []))
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
        return rows

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _Result(list(self._insert))
        return _Result(self._matched())


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store():
    return {
        "users": [{"id": "rider-1", "phone": "+13065551234", "is_rider": True}],
        "saved_addresses": [],
    }


def _patches(store):
    return (
        patch("services.saved_address_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.legacy_saved_address_backfill.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _files(address_bytes: bytes, customers_bytes: bytes):
    return {
        "addresses_csv": ("customer_addresses.csv", address_bytes, "text/csv"),
        "customers_csv": ("customers.csv", customers_bytes, "text/csv"),
    }


def _post(test_client, path, address_bytes, customers_bytes, data=None):
    return test_client.post(path, files=_files(address_bytes, customers_bytes), data=data or {})


def _validate_then_commit(test_client, address_bytes, customers_bytes):
    validate_resp = _post(
        test_client, "/api/admin/riders/saved-address-backfill/validate", address_bytes, customers_bytes
    )
    assert validate_resp.status_code == 200, validate_resp.text
    report = validate_resp.json()
    return test_client.post(
        "/api/admin/riders/saved-address-backfill/commit",
        files=_files(address_bytes, customers_bytes),
        data={"batch": report["batch"], "validation_token": report["validation_token"]},
    )


def test_validate_clean_csvs_no_writes(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/riders/saved-address-backfill/validate",
            _address_csv(GOOD_ADDRESS_ROW),
            _customers_csv(GOOD_CUSTOMERS_ROW),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["address_rows"] == 1
    assert body["counts"]["addresses_to_insert"] == 1
    assert body["errors"] == []
    assert "validation_token" in body
    assert store["saved_addresses"] == []


def test_commit_creates_saved_address_row(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _address_csv(GOOD_ADDRESS_ROW), _customers_csv(GOOD_CUSTOMERS_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["addresses_inserted"] == 1
    assert len(store["saved_addresses"]) == 1
    row = store["saved_addresses"][0]
    assert row["user_id"] == "rider-1"
    assert row["address"] == "123 Main Street, Saskatoon, SK S7K 0J5"
    assert row["name"] == "Home"
    assert row["legacy_import_metadata"]["source"] == "legacy_customer_address_import"


def test_commit_is_idempotent_on_rerun(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        first = _validate_then_commit(test_client, _address_csv(GOOD_ADDRESS_ROW), _customers_csv(GOOD_CUSTOMERS_ROW))
        assert first.json()["addresses_inserted"] == 1
        second = _validate_then_commit(test_client, _address_csv(GOOD_ADDRESS_ROW), _customers_csv(GOOD_CUSTOMERS_ROW))
    assert second.status_code == 200, second.text
    assert second.json()["addresses_inserted"] == 0
    assert len(store["saved_addresses"]) == 1


def test_out_of_province_row_is_excluded(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/riders/saved-address-backfill/validate",
            _address_csv(OUT_OF_PROVINCE_ROW),
            _customers_csv(GOOD_CUSTOMERS_ROW),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["addresses_to_insert"] == 0
    assert body["counts"]["skipped_out_of_province"] == 1


def test_commit_refuses_on_missing_required_columns(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    bad_csv = b"foo,bar\n1,2\n"
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, bad_csv, _customers_csv(GOOD_CUSTOMERS_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["saved_addresses"] == []


def test_commit_without_validation_token_is_422(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(
            test_client,
            "/api/admin/riders/saved-address-backfill/commit",
            _address_csv(GOOD_ADDRESS_ROW),
            _customers_csv(GOOD_CUSTOMERS_ROW),
        )
    assert resp.status_code == 422, resp.text


def test_commit_with_swapped_address_csv_is_400(test_client, super_admin_override):
    """The commit token binds sha256(address_bytes + '|' + customer_bytes)
    -- swapping just customer_addresses.csv between validate and commit
    must still be caught."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        validate_resp = _post(
            test_client,
            "/api/admin/riders/saved-address-backfill/validate",
            _address_csv(GOOD_ADDRESS_ROW),
            _customers_csv(GOOD_CUSTOMERS_ROW),
        )
        report = validate_resp.json()
        resp = test_client.post(
            "/api/admin/riders/saved-address-backfill/commit",
            files=_files(_address_csv(OUT_OF_PROVINCE_ROW), _customers_csv(GOOD_CUSTOMERS_ROW)),
            data={"batch": report["batch"], "validation_token": report["validation_token"]},
        )
    assert resp.status_code == 400, resp.text


def test_requires_admin_auth(test_client):
    resp = _post(
        test_client,
        "/api/admin/riders/saved-address-backfill/validate",
        _address_csv(GOOD_ADDRESS_ROW),
        _customers_csv(GOOD_CUSTOMERS_ROW),
    )
    assert resp.status_code in (401, 403)
