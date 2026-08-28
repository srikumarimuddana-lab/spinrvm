"""Endpoint tests for the admin legacy Mongo driver-import routes
(routes/admin/legacy_driver_import.py, Phase 1 of the 2026-08-27 migration
plan).

Mirrors test_admin_driver_import.py's structure exactly for a different CSV
shape/service-layer pair. The service layer (services/driver_import_service.py)
talks to Supabase, so we patch its module-level ``supabase`` with an in-memory
fake. log_admin_action is stubbed to avoid a real audit write.
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


# Raw Mongo-export column order/shape -- deliberately NOT the Saskatoon CSV's
# columns, to prove the endpoint is reading this file with read_mongo_export_
# csv_text (which preserves "_id"), not read_csv_text (which would corrupt it).
CSV_HEADER = "_id,name,phone,email,ratings,created_at,is_deleted,is_block,set_up_profile"
GOOD_ROW = "6923ea32d1bde481895439f4,Jane Doe,3065551234,jane@example.com,4.5,1700000000000,false,false,true"
BLANK_NAME_ROW = "6923ea32d1bde481895439f5,,3065559876,,,,false,false,false"


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
        self._update = None

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

    def update(self, fields):
        self._update = fields
        return self

    def _matched(self):
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
        return rows

    def execute(self):
        if self._insert is not None:
            self.store.setdefault(self.table, []).extend(self._insert)
            return _Result(list(self._insert))
        if self._update is not None:
            matched = self._matched()
            for row in matched:
                row.update(self._update)
            return _Result(matched)
        return _Result(self._matched())


class _Rpc:
    def __init__(self, params):
        self.params = params

    def execute(self):
        return _Result(f"enc::{self.params.get('plaintext')}")


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
        "users": [],
        "drivers": [],
    }


def _patches(store):
    return (
        patch("services.driver_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.legacy_driver_import.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def _post(test_client, path, csv_bytes):
    return test_client.post(
        path,
        files={"drivers_csv": ("drivers.csv", csv_bytes, "text/csv")},
        data={"service_area_name": "Saskatoon"},
    )


def _validate_then_commit(test_client, csv_bytes):
    validate_resp = _post(test_client, "/api/admin/legacy-drivers/import/validate", csv_bytes)
    assert validate_resp.status_code == 200, validate_resp.text
    report = validate_resp.json()
    return test_client.post(
        "/api/admin/legacy-drivers/import/commit",
        files={"drivers_csv": ("drivers.csv", csv_bytes, "text/csv")},
        data={
            "service_area_name": "Saskatoon",
            "batch": report["batch"],
            "validation_token": report["validation_token"],
        },
    )


def test_validate_clean_csv_preserves_mongo_id_column(test_client, super_admin_override):
    """Proves the endpoint uses read_mongo_export_csv_text, not read_csv_text
    -- the latter would mangle "_id" and every row would error as missing."""
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/legacy-drivers/import/validate", _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert body["counts"]["new_users"] == 1
    assert body["counts"]["new_drivers"] == 1
    assert body["errors"] == []
    # Validate must not write anything.
    assert store["users"] == []
    assert store["drivers"] == []


def test_validate_blank_name_is_warning_not_error(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/legacy-drivers/import/validate", _csv(BLANK_NAME_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is True
    assert any(w["field"] == "name" for w in body["warnings"])


def test_commit_creates_rows(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["new_users"] == 1
    assert body["new_drivers"] == 1
    assert body["linked_accounts"] == 0
    assert body["enriched_drivers"] == 0
    assert len(store["users"]) == 1
    assert len(store["drivers"]) == 1
    assert store["drivers"][0]["status"] == "needs_review"


def test_commit_links_existing_account_no_duplicate_user(test_client, super_admin_override):
    """Existing-match sub-population 1: an existing rider account with no
    driver row gets a new driver linked to it, not a duplicate user."""
    store = _fresh_store()
    store["users"] = [{"id": "existing-user-1", "phone": "+13065551234", "email": None, "is_driver": False}]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["new_users"] == 0
    assert body["new_drivers"] == 1
    assert body["linked_accounts"] == 1
    assert len(store["users"]) == 1  # no duplicate created
    stored_user = store["users"][0]
    assert stored_user["is_driver"] is True
    stored_driver = store["drivers"][0]
    assert stored_driver["user_id"] == "existing-user-1"


def test_commit_enriches_existing_driver_no_duplicate_row(test_client, super_admin_override):
    """Existing-match sub-population 2: an existing driver gets enriched
    history, no competing needs_review row, no live field touched."""
    store = _fresh_store()
    store["drivers"] = [
        {
            "id": "existing-driver-1",
            "phone": "+13065551234",
            "status": "active",
            "name": "Real Driver",
            "legacy_import_metadata": {"source": "legacy_saskatoon_driver_import"},
        }
    ]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _csv(GOOD_ROW))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["new_drivers"] == 0
    assert body["enriched_drivers"] == 1
    assert len(store["drivers"]) == 1  # no duplicate row
    stored_driver = store["drivers"][0]
    assert stored_driver["status"] == "active"  # untouched
    assert stored_driver["name"] == "Real Driver"  # untouched
    assert "mongo_driver_history" in stored_driver["legacy_import_metadata"]


def test_commit_refuses_on_invalid_phone_error(test_client, super_admin_override):
    store = _fresh_store()
    bad_row = "OLD-1,Ann Poe,123,ann@example.com,,,false,false,true"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _validate_then_commit(test_client, _csv(bad_row))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is False
    assert body["errors"]
    assert store["drivers"] == []


def test_commit_without_validation_token_is_422(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/legacy-drivers/import/commit", _csv(GOOD_ROW))
    assert resp.status_code == 422, resp.text


def test_commit_with_wrong_token_is_400(test_client, super_admin_override):
    store = _fresh_store()
    other = "6923ea32d1bde481895439ff,Someone Else,3065559999,other@example.com,,,false,false,true"
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        validate_resp = _post(test_client, "/api/admin/legacy-drivers/import/validate", _csv(other))
        assert validate_resp.status_code == 200, validate_resp.text
        report = validate_resp.json()
        resp = test_client.post(
            "/api/admin/legacy-drivers/import/commit",
            files={"drivers_csv": ("drivers.csv", _csv(GOOD_ROW), "text/csv")},
            data={
                "service_area_name": "Saskatoon",
                "batch": report["batch"],
                "validation_token": report["validation_token"],
            },
        )
    assert resp.status_code == 400, resp.text
    assert store["drivers"] == []


def test_row_limit_enforced(test_client, super_admin_override):
    store = _fresh_store()
    rows = [f"id-{i:06d},Name {i},306555{i:04d},u{i}@example.com,,,false,false,true" for i in range(2001)]
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = _post(test_client, "/api/admin/legacy-drivers/import/validate", _csv(*rows))
    assert resp.status_code == 422, resp.text


def test_requires_admin_auth(test_client):
    # No admin_override fixture -> the router-level get_admin_user gate rejects.
    resp = _post(test_client, "/api/admin/legacy-drivers/import/validate", _csv(GOOD_ROW))
    assert resp.status_code in (401, 403)
