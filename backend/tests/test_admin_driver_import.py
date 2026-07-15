"""Endpoint tests for the admin bulk driver-import routes.

The service layer (services/driver_import_service.py) talks to Supabase, so we
patch its module-level ``supabase`` with an in-memory fake. log_admin_action is
stubbed to avoid a real audit write.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def super_admin_override():
    """Admin routes here sit behind require_module("drivers"); a super_admin
    passes that gate regardless of the modules claim."""
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


CSV_HEADER = "old_driver_id,full_name,phone,email,vehicle_plate,vehicle_type,vehicle_year,vehicle_make,vehicle_model"
GOOD_ROW = "OLD-1,Jane Doe,3065551234,jane@example.com,ABC123,Sedan,2020,Toyota,Corolla"


def _csv(*rows: str) -> bytes:
    return ("\n".join([CSV_HEADER, *rows]) + "\n").encode()


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

    def ilike(self, col, val):
        self._filters.append(("ilike", col, val))
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
        for op, col, val in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(col) == val]
            elif op == "in":
                allowed = set(val)
                rows = [r for r in rows if r.get(col) in allowed]
            elif op == "ilike":
                needle = str(val).strip("%").lower()
                rows = [r for r in rows if needle in str(r.get(col, "")).lower()]
        return _Result(rows)


class _Rpc:
    def __init__(self, params):
        self.params = params

    def execute(self):
        return _Result(self.params.get("plaintext"))


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, _name, params):
        return _Rpc(params)


def _fresh_store():
    return {
        "service_areas": [
            {
                "id": "sa-1",
                "name": "Saskatoon",
                "province": "SK",
                "required_documents": [],
                "regulatory_authority": "SGI",
                "regulatory_region": "SK",
            }
        ],
        "vehicle_types": [{"id": "vt-sedan", "name": "Sedan"}],
        "users": [],
        "drivers": [],
    }


def _patches(store):
    return (
        patch("services.driver_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.driver_import.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _post(test_client, path, csv_bytes):
    return test_client.post(
        path,
        files={"drivers_csv": ("drivers.csv", csv_bytes, "text/csv")},
        data={"service_area_name": "Saskatoon"},
    )


def test_validate_clean_csv(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/drivers/import/validate", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["users"] == 1
    assert body["counts"]["drivers"] == 1
    assert body["errors"] == []
    # Validate must not write anything.
    assert store["users"] == []
    assert store["drivers"] == []


def test_validate_reports_bad_vehicle_type(test_client, super_admin_override):
    store = _fresh_store()
    bad = "OLD-2,John Roe,3065559876,john@example.com,XYZ789,Spaceship,2019,Ford,Focus"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/drivers/import/validate", _csv(bad))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert any(e["field"] == "vehicle_type" for e in body["errors"])


def test_commit_creates_rows(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/drivers/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["imported_drivers"] == 1
    assert body["imported_users"] == 1
    assert len(store["users"]) == 1
    assert len(store["drivers"]) == 1


def test_commit_refuses_on_errors(test_client, super_admin_override):
    store = _fresh_store()
    bad = "OLD-3,Ann Poe,3065550000,ann@example.com,LMN456,Spaceship,2021,Honda,Civic"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/drivers/import/commit", _csv(bad))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["drivers"] == []


def test_row_limit_enforced(test_client, super_admin_override):
    store = _fresh_store()
    rows = [f"OLD-{i},Name {i},306555{i:04d},u{i}@example.com,P{i},Sedan,2020,Toyota,Corolla" for i in range(501)]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/drivers/import/validate", _csv(*rows))
    assert resp.status_code == 422, resp.text


def test_requires_admin_auth(test_client):
    # No admin_override fixture → the router-level get_admin_user gate rejects.
    resp = _post(test_client, "/api/admin/drivers/import/validate", _csv(GOOD_ROW))
    assert resp.status_code in (401, 403)
