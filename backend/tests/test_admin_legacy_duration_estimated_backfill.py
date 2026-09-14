"""Endpoint tests for routes/admin/legacy_duration_estimated_backfill.py.

The plan/apply logic itself is already covered end-to-end in
test_legacy_duration_estimated_backfill_service.py (write-time guard,
never-clobber, concurrent-writer hardening) -- these tests cover the HTTP
layer only: the super-admin boundary, that preview never writes, and
end-to-end commit (including a clean re-run being a no-op, matching the
service's own idempotency contract). Mirrors
test_admin_migration_data_quality.py's fake-supabase harness, extended with
the `->>`-path `eq` filter this module's fetch step needs (see
test_legacy_duration_estimated_backfill_service.py's own harness, which
already supports it) -- migration_data_quality's harness only supports
`is`/`not.is` on a JSON path, not `eq`.
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
                if op == "eq":
                    if actual != val:
                        return False
                elif op == "not.is" and val == "null":
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


def _legacy_ride(ride_id, **fields):
    from backend.services.booking_import_service import IMPORT_SOURCE

    row = {
        "id": ride_id,
        "ride_started_at": None,
        "legacy_import_metadata": {"source": IMPORT_SOURCE, "old_booking_id": "CB123"},
    }
    row.update(fields)
    return row


def _patches(store):
    return (
        patch("services.booking_import_service.supabase", _FakeSupabase(store)),
        patch("routes.admin.legacy_duration_estimated_backfill.log_admin_action", AsyncMock(return_value="audit-1")),
    )


def test_preview_is_read_only(test_client, super_admin_override):
    store = _fresh_store(rides=[_legacy_ride("r1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/duration-estimated-backfill/preview", data={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["counts"]["rows_to_stamp"] == 1
    assert body["counts"]["duration_estimated_true"] == 1
    assert body["can_commit"] is True
    # No write: the affected ride's metadata is still unstamped.
    assert "duration_estimated" not in store["rides"][0]["legacy_import_metadata"]


def test_preview_with_nothing_to_stamp_cannot_commit(test_client, super_admin_override):
    ride = _legacy_ride("r1")
    ride["legacy_import_metadata"]["duration_estimated"] = False
    store = _fresh_store(rides=[ride])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/duration-estimated-backfill/preview", data={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["can_commit"] is False
    assert body["counts"]["already_marked_skipped"] == 1


def test_commit_stamps_the_candidates(test_client, super_admin_override):
    store = _fresh_store(rides=[_legacy_ride("r1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/duration-estimated-backfill/commit", data={"batch": "b1"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["committed"] is True
    assert body["updated"] == 1
    assert body["conflicts"] == 0
    meta = store["rides"][0]["legacy_import_metadata"]
    assert meta["duration_estimated"] is True
    assert meta["legacy_duration_estimated_backfill"]["batch"] == "b1"
    # duration_minutes is never touched by this backfill
    assert "duration_minutes" not in store["rides"][0] or store["rides"][0].get("duration_minutes") is None


def test_commit_never_touches_duration_minutes(test_client, super_admin_override):
    store = _fresh_store(rides=[_legacy_ride("r1", duration_minutes=45)])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        test_client.post("/api/admin/legacy/duration-estimated-backfill/commit", data={"batch": "b1"})
    assert store["rides"][0]["duration_minutes"] == 45


def test_commit_is_idempotent_on_rerun(test_client, super_admin_override):
    store = _fresh_store(rides=[_legacy_ride("r1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        first = test_client.post("/api/admin/legacy/duration-estimated-backfill/commit", data={"batch": "b1"})
        assert first.json()["updated"] == 1
        second = test_client.post("/api/admin/legacy/duration-estimated-backfill/commit", data={"batch": "b2"})
    assert second.status_code == 200, second.text
    assert second.json()["committed"] is False  # nothing left to stamp


def test_commit_only_scans_this_importers_legacy_rides(test_client, super_admin_override):
    organic_ride = {"id": "organic-1", "ride_started_at": None, "legacy_import_metadata": {}}
    store = _fresh_store(rides=[organic_ride, _legacy_ride("r1")])
    p_sb, p_audit = _patches(store)
    with p_sb, p_audit:
        resp = test_client.post("/api/admin/legacy/duration-estimated-backfill/preview", data={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["counts"]["legacy_rides_scanned"] == 1


def test_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/legacy/duration-estimated-backfill/preview", data={})
    assert resp.status_code == 403


def test_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/legacy/duration-estimated-backfill/preview", data={})
    assert resp.status_code in (401, 403)
