"""Endpoint tests for routes/admin/driver_dormancy.py.

The classification logic itself is covered in
test_driver_dormancy_service.py -- these tests cover the HTTP layer: the
super-admin boundary, that preview never writes, and end-to-end commit
(including a clean re-run being a no-op, matching the service's own
idempotency contract).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

NOW = datetime(2026, 9, 11, tzinfo=timezone.utc)


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


class _NotProxy:
    def __init__(self, query):
        self._query = query

    def in_(self, col, vals):
        self._query._predicates.append(("not_in", col, list(vals)))
        return self._query


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None

    @property
    def not_(self):
        return _NotProxy(self)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
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
            elif kind == "not_in":
                _, col, vals = pred
                if row.get(col) in vals:
                    return False
            elif kind == "filter":
                _, col, op, val = pred
                if col == "legacy_import_metadata" and op == "eq":
                    if json.dumps(row.get(col) or {}, sort_keys=True, default=str) != val:
                        return False
                    continue
                if "->>" in col:
                    base, key = col.split("->>", 1)
                    actual = (row.get(base) or {}).get(key)
                    if actual is not None:
                        actual = str(actual).lower() if isinstance(actual, bool) else str(actual)
                else:
                    actual = row.get(col)
                if op == "is" and val == "null":
                    if actual is not None:
                        return False
                elif op == "eq":
                    if actual != val:
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
        return _Result([dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store(**tables):
    base = {"drivers": []}
    base.update(tables)
    return base


def _days_ago(days: int) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _dormant_driver(driver_id, *, idle_days=400):
    return {
        "id": driver_id,
        "is_online": False,
        "is_suspended": False,
        "status": "active",
        "created_at": _days_ago(idle_days),
        "went_online_at": None,
        "went_offline_at": None,
        "legacy_import_metadata": {},
    }


def _patches(store):
    return (
        patch("services.driver_dormancy_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.driver_dormancy.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def test_preview_is_read_only(test_client, super_admin_override):
    store = _fresh_store(drivers=[_dormant_driver("drv-1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/drivers/dormancy/preview", data={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["long_dormant_candidates"] == 1
    assert body["can_commit"] is True
    # No write: the dormant driver's flag is still unset.
    assert "dormant" not in store["drivers"][0]["legacy_import_metadata"]


def test_preview_with_nothing_to_flag_cannot_commit(test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/drivers/dormancy/preview", data={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_commit"] is False


def test_commit_flags_the_candidates(test_client, super_admin_override):
    store = _fresh_store(drivers=[_dormant_driver("drv-1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/drivers/dormancy/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["drivers_flagged"] == 1
    assert body["conflicts"] == 0
    meta = store["drivers"][0]["legacy_import_metadata"]
    assert meta["dormant"] is True
    assert meta["dormancy_tier"] == "long_dormant"
    assert meta["dormancy_flag"]["batch"] == "b1"


def test_commit_is_idempotent_on_rerun(test_client, super_admin_override):
    store = _fresh_store(drivers=[_dormant_driver("drv-1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        first = test_client.post("/api/admin/drivers/dormancy/commit", data={"batch": "b1"})
        assert first.json()["drivers_flagged"] == 1
        second = test_client.post("/api/admin/drivers/dormancy/commit", data={"batch": "b2"})
    assert second.status_code == 200, second.text
    assert second.json()["committed"] is False  # nothing left to flag


def test_commit_never_flags_a_currently_online_driver(test_client, super_admin_override):
    row = _dormant_driver("drv-1")
    row["is_online"] = True
    store = _fresh_store(drivers=[row])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/drivers/dormancy/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is False
    assert "dormant" not in store["drivers"][0]["legacy_import_metadata"]


def test_commit_never_flags_a_needs_review_driver(test_client, super_admin_override):
    row = _dormant_driver("drv-1")
    row["status"] = "needs_review"
    store = _fresh_store(drivers=[row])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/drivers/dormancy/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is False
    assert "dormant" not in store["drivers"][0]["legacy_import_metadata"]


def test_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/drivers/dormancy/preview", data={})
    assert resp.status_code == 403


def test_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/drivers/dormancy/preview", data={})
    assert resp.status_code in (401, 403)
