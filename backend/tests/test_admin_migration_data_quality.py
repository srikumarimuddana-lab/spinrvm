"""Endpoint tests for routes/admin/migration_data_quality.py.

The detection/tagging logic itself is covered in
test_migration_data_quality_service.py -- these tests cover the HTTP layer:
the super-admin boundary, that preview never writes, and end-to-end commit
(including a clean re-run being a no-op, matching the service's own
idempotency contract).
"""

import json
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


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self._predicates.append(("is_", col, val))
        return self

    def filter(self, col, op, val):
        self._predicates.append(("filter", col, op, val))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def _row_matches(self, row) -> bool:
        for pred in self._predicates:
            kind = pred[0]
            if kind == "eq":
                _, col, val = pred
                if row.get(col) != val:
                    return False
            elif kind == "is_":
                _, col, val = pred
                if val == "null" and row.get(col) is not None:
                    return False
            elif kind == "filter":
                _, col, op, val = pred
                if col == "legacy_import_metadata" and op == "eq":
                    if json.dumps(row.get(col) or {}, sort_keys=True, default=str) != val:
                        return False
                    continue
                import re as _re

                parts = _re.split(r"->>|->", col)
                actual = row.get(parts[0])
                for key in parts[1:]:
                    actual = (actual or {}).get(key)
                if op == "not.is" and val == "null":
                    if actual is None:
                        return False
                elif op == "is" and val == "null":
                    if actual is not None:
                        return False
                else:
                    return False
        return True

    def execute(self):
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        if self._update_payload is not None:
            for r in rows:
                r.update(self._update_payload)
        return _Result([dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store(**tables):
    base = {"rides": []}
    base.update(tables)
    return base


def _ride_row(ride_id, **fields):
    row = {
        "id": ride_id,
        "status": "completed",
        "driver_id": "driver-1",
        "rider_id": "rider-1",
        "pickup_address": "123 Real St",
        "dropoff_address": "456 Real Ave",
        "grand_total": 12.50,
        "legacy_import_metadata": {},
    }
    row.update(fields)
    return row


def _patches(store):
    return (
        patch("services.migration_data_quality_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.migration_data_quality.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def test_preview_is_read_only(test_client, super_admin_override):
    store = _fresh_store(rides=[_ride_row("r1", driver_id=None)])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/data-quality-scan/preview", data={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["missing_driver"] == 1
    assert body["can_commit"] is True
    # No write: the affected ride's flag is still unset.
    assert "data_quality" not in store["rides"][0]["legacy_import_metadata"]


def test_preview_with_nothing_to_flag_cannot_commit(test_client, super_admin_override):
    store = _fresh_store(rides=[_ride_row("r1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/data-quality-scan/preview", data={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_commit"] is False


def test_commit_flags_the_candidates(test_client, super_admin_override):
    store = _fresh_store(rides=[_ride_row("r1", driver_id=None)])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/data-quality-scan/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["rides_flagged"] == 1
    assert body["conflicts"] == 0
    assert store["rides"][0]["legacy_import_metadata"]["data_quality"]["issues"] == ["missing_driver"]


def test_commit_is_idempotent_on_rerun(test_client, super_admin_override):
    store = _fresh_store(rides=[_ride_row("r1", driver_id=None)])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        first = test_client.post("/api/admin/legacy/data-quality-scan/commit", data={"batch": "b1"})
        assert first.json()["rides_flagged"] == 1
        second = test_client.post("/api/admin/legacy/data-quality-scan/commit", data={"batch": "b2"})
    assert second.status_code == 200, second.text
    assert second.json()["committed"] is False  # nothing left to flag


def test_commit_multi_issue_ride_flags_both(test_client, super_admin_override):
    store = _fresh_store(rides=[_ride_row("r1", driver_id=None, grand_total=0)])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/data-quality-scan/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    assert set(store["rides"][0]["legacy_import_metadata"]["data_quality"]["issues"]) == {
        "missing_driver",
        "zero_fare",
    }


def test_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/legacy/data-quality-scan/preview", data={})
    assert resp.status_code == 403


def test_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/legacy/data-quality-scan/preview", data={})
    assert resp.status_code in (401, 403)
