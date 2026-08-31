"""Endpoint tests for routes/admin/pre_launch_flag.py.

The matching/flagging logic itself is covered in
test_pre_launch_flag_service.py -- these tests cover the HTTP layer: the
super-admin boundary, that preview never writes, and end-to-end commit
(including a clean re-run being a no-op, matching the service's own
idempotency contract).
"""

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

    def lt(self, col, val):
        self._predicates.append(("lt", col, val))
        return self

    def in_(self, col, vals):
        self._predicates.append(("in", col, list(vals)))
        return self

    def filter(self, col, op, val):
        self._predicates.append(("filter", col, op, val))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def _row_matches(self, row) -> bool:
        import json

        for pred in self._predicates:
            kind = pred[0]
            if kind == "eq":
                _, col, val = pred
                if row.get(col) != val:
                    return False
            elif kind == "lt":
                _, col, val = pred
                actual = row.get(col)
                if actual is None or not (actual < val):
                    return False
            elif kind == "in":
                _, col, vals = pred
                if row.get(col) not in vals:
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
                else:
                    actual = row.get(col)
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
        return _Result([dict(r) for r in rows])


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _fresh_store(**tables):
    base = {"drivers": [], "rides": [], "driver_insurance_periods": []}
    base.update(tables)
    return base


def _driver_row(driver_id):
    return {
        "id": driver_id,
        "created_at": "2026-02-01",
        "legacy_import_metadata": {"source": "legacy_mongo_driver_import"},
    }


def _patches(store):
    return (
        patch("services.pre_launch_flag_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.pre_launch_flag.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def test_preview_is_read_only(test_client, super_admin_override):
    store = _fresh_store(drivers=[_driver_row("drv-1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/pre-launch-flag/preview", data={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["driver_candidates"] == 1
    assert body["can_commit"] is True
    # No write: the dormant driver's flag is still unset.
    assert "pre_launch_test" not in store["drivers"][0]["legacy_import_metadata"]


def test_preview_with_nothing_to_flag_cannot_commit(monkeypatch, test_client, super_admin_override):
    store = _fresh_store()
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/pre-launch-flag/preview", data={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_commit"] is False


def test_commit_flags_the_candidates(test_client, super_admin_override):
    store = _fresh_store(drivers=[_driver_row("drv-1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/pre-launch-flag/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["drivers_flagged"] == 1
    assert body["driver_conflicts"] == 0
    assert store["drivers"][0]["legacy_import_metadata"]["pre_launch_test"] is True


def test_commit_is_idempotent_on_rerun(test_client, super_admin_override):
    store = _fresh_store(drivers=[_driver_row("drv-1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        first = test_client.post("/api/admin/legacy/pre-launch-flag/commit", data={"batch": "b1"})
        assert first.json()["drivers_flagged"] == 1
        second = test_client.post("/api/admin/legacy/pre-launch-flag/commit", data={"batch": "b2"})
    assert second.status_code == 200, second.text
    assert second.json()["committed"] is False  # nothing left to flag


def test_commit_never_flags_a_driver_with_a_ride(test_client, super_admin_override):
    store = _fresh_store(
        drivers=[_driver_row("drv-1")],
        rides=[{"id": "ride-1", "driver_id": "drv-1", "created_at": "2026-06-01", "legacy_import_metadata": {}}],
    )
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/pre-launch-flag/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["committed"] is False
    assert "pre_launch_test" not in store["drivers"][0]["legacy_import_metadata"]


def test_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/legacy/pre-launch-flag/preview", data={})
    assert resp.status_code == 403


def test_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/legacy/pre-launch-flag/preview", data={})
    assert resp.status_code in (401, 403)
