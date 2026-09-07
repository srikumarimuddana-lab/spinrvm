"""Endpoint tests for routes/admin/legacy_id_crosswalk.py.

The plan-building/apply logic itself is covered in
test_legacy_id_crosswalk_service.py -- these tests cover only the HTTP
layer's super-admin boundary (allowed and denied paths), matching the
scope of test_admin_migration_data_quality.py's own auth tests for the
sibling no-CSV importer.
"""

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


def test_preview_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/legacy/id-crosswalk-backfill/preview", data={})
    assert resp.status_code == 403


def test_commit_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.post("/api/admin/legacy/id-crosswalk-backfill/commit", data={})
    assert resp.status_code == 403


def test_preview_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/legacy/id-crosswalk-backfill/preview", data={})
    assert resp.status_code in (401, 403)


def test_commit_requires_admin_auth(test_client):
    resp = test_client.post("/api/admin/legacy/id-crosswalk-backfill/commit", data={})
    assert resp.status_code in (401, 403)


def test_preview_allows_super_admin(test_client, super_admin_override, monkeypatch):
    from backend.services import legacy_id_crosswalk_service as svc

    monkeypatch.setattr(
        svc,
        "build_driver_crosswalk_plan",
        lambda: svc.CrosswalkPlan(stats={"new_rows_to_write": 0, "already_recorded": 0, "eligible_drivers_found": 0}),
    )
    monkeypatch.setattr(
        svc,
        "build_rider_crosswalk_plan",
        lambda: svc.CrosswalkPlan(stats={"new_rows_to_write": 0, "already_recorded": 0, "eligible_riders_found": 0}),
    )
    resp = test_client.post("/api/admin/legacy/id-crosswalk-backfill/preview", data={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["can_commit"] is False
