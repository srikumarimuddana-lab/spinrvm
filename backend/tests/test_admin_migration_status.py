"""Endpoint tests for routes/admin/migration_status.py.

The count/status logic itself is covered in
test_migration_status_service.py -- these tests cover the HTTP layer: the
super-admin boundary and the response shape, with the service function
mocked out.
"""

import pytest

from backend.services.migration_status_service import MigrationStatusReport, ToolStatus


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


def _fake_report() -> MigrationStatusReport:
    return MigrationStatusReport(
        tools=[
            ToolStatus(
                1,
                "bulk_driver_import",
                "Bulk Driver Import (Saskatoon CSV)",
                "done",
                "187 driver(s) imported",
                "/dashboard/drivers/import",
            ),
            ToolStatus(
                10,
                "saved_address_backfill",
                "Legacy Saved-Address Backfill",
                "manual_check_required",
                "Migration 373 not applied",
                "/dashboard/riders/legacy-saved-address-backfill",
                warning="Migration 373 not applied",
            ),
        ]
    )


def test_returns_all_tool_fields(monkeypatch, test_client, super_admin_override):
    from backend.routes.admin import migration_status as route_mod

    monkeypatch.setattr(route_mod.svc, "get_migration_status", _fake_report)
    resp = test_client.get("/api/admin/migration-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["tools"]) == 2

    first = body["tools"][0]
    assert first["order"] == 1
    assert first["id"] == "bulk_driver_import"
    assert first["state"] == "done"
    assert first["warning"] is None

    second = body["tools"][1]
    assert second["state"] == "manual_check_required"
    assert second["warning"] == "Migration 373 not applied"


def test_requires_super_admin(test_client, staff_admin_override):
    resp = test_client.get("/api/admin/migration-status")
    assert resp.status_code == 403


def test_requires_admin_auth(test_client):
    resp = test_client.get("/api/admin/migration-status")
    assert resp.status_code in (401, 403)
