"""Endpoint tests for routes/admin/migration_driver_repair.py.

The re-matching/repair logic itself is covered in
test_migration_driver_repair_service.py -- these tests cover the HTTP
layer: the super-admin boundary, that preview never writes, and end-to-end
commit (including a clean re-run being a no-op).
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
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self._predicates = []
        self._update_payload = None
        self._insert_rows = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._predicates.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self._predicates.append(("is_", col, val))
        return self

    def in_(self, col, vals):
        self._predicates.append(("in_", col, set(vals)))
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def insert(self, rows):
        self._insert_rows = rows if isinstance(rows, list) else [rows]
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
            elif kind == "in_":
                _, col, vals = pred
                if row.get(col) not in vals:
                    return False
        return True

    def execute(self):
        if self._insert_rows is not None:
            self.store.setdefault(self.table, []).extend(dict(r) for r in self._insert_rows)
            return _Result([dict(r) for r in self._insert_rows])
        rows = [r for r in self.store.get(self.table, []) if self._row_matches(r)]
        if self._update_payload is not None:
            for r in rows:
                r.update(self._update_payload)
        return _Result([dict(r) for r in rows])


class _RpcResult:
    def execute(self):
        raise RuntimeError("no recount RPC in tests -- fall back to per-driver recount")


class _FakeSupabase:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)

    def rpc(self, *_a, **_k):
        return _RpcResult()


def _fresh_store(**tables):
    base = {"rides": [], "drivers": [], "payouts": [], "driver_insurance_periods": []}
    base.update(tables)
    return base


def _ride_row(ride_id, *, old_driver_id="old-d1", driver_earnings=25.0, **fields):
    row = {
        "id": ride_id,
        "status": "completed",
        "driver_id": None,
        "driver_earnings": driver_earnings,
        "driver_arrived_at": "2026-01-01T00:00:00Z",
        "ride_started_at": "2026-01-01T00:05:00Z",
        "ride_completed_at": "2026-01-01T00:30:00Z",
        "legacy_import_metadata": {"old_driver_id": old_driver_id} if old_driver_id else {},
    }
    row.update(fields)
    return row


def _driver_row(driver_id, *, top_level_old_id=None, total_rides=0):
    meta = {"old_driver_id": top_level_old_id} if top_level_old_id else {}
    return {"id": driver_id, "legacy_import_metadata": meta, "total_rides": total_rides}


def _patches(store):
    return (
        patch("services.migration_driver_repair_service.supabase", _FakeSupabase(store)),
        patch("services.booking_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.migration_driver_repair.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def test_preview_is_read_only(test_client, super_admin_override):
    store = _fresh_store(
        rides=[_ride_row("r1")],
        drivers=[_driver_row("driver-1", top_level_old_id="old-d1")],
    )
    p_svc, p_booking, p_audit = _patches(store)
    with p_svc, p_booking, p_audit:
        resp = test_client.post("/api/admin/legacy/driver-repair/preview", data={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["repairable"] == 1
    assert body["can_commit"] is True
    # No write: the ride's driver_id is still unset.
    assert store["rides"][0]["driver_id"] is None
    assert store["payouts"] == []


def test_preview_with_nothing_repairable_cannot_commit(test_client, super_admin_override):
    store = _fresh_store(rides=[_ride_row("r1")], drivers=[])
    p_svc, p_booking, p_audit = _patches(store)
    with p_svc, p_booking, p_audit:
        resp = test_client.post("/api/admin/legacy/driver-repair/preview", data={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_commit"] is False


def test_commit_repairs_the_candidates(test_client, super_admin_override):
    store = _fresh_store(
        rides=[_ride_row("r1")],
        drivers=[_driver_row("driver-1", top_level_old_id="old-d1")],
    )
    p_svc, p_booking, p_audit = _patches(store)
    with p_svc, p_booking, p_audit:
        resp = test_client.post("/api/admin/legacy/driver-repair/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["rides_repaired"] == 1
    assert body["conflicts"] == 0
    assert store["rides"][0]["driver_id"] == "driver-1"
    assert len(store["payouts"]) == 1
    assert len(store["driver_insurance_periods"]) == 2


def test_commit_is_idempotent_on_rerun(test_client, super_admin_override):
    store = _fresh_store(
        rides=[_ride_row("r1")],
        drivers=[_driver_row("driver-1", top_level_old_id="old-d1")],
    )
    p_svc, p_booking, p_audit = _patches(store)
    with p_svc, p_booking, p_audit:
        first = test_client.post("/api/admin/legacy/driver-repair/commit", data={"batch": "b1"})
        assert first.json()["rides_repaired"] == 1
        second = test_client.post("/api/admin/legacy/driver-repair/commit", data={"batch": "b2"})
    assert second.status_code == 200, second.text
    assert second.json()["committed"] is False  # nothing left to repair
    assert len(store["payouts"]) == 1  # not doubled


def test_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/legacy/driver-repair/preview", data={})
    assert resp.status_code == 403


def test_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/legacy/driver-repair/preview", data={})
    assert resp.status_code in (401, 403)
