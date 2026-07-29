"""HTTP-level tests for routes/admin/export_approvals.py (ACTION_ITEMS.md
B10). Exercises the router wiring (require_super_admin gate, status codes)
on top of the service-level tests in test_admin_export_approvals.py.
"""

from unittest.mock import AsyncMock, patch

import pytest

_SUPER_ADMIN = {"id": "admin-001", "role": "super_admin", "email": "admin@spinr.app", "modules": []}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture(autouse=True)
def _set_super_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


def test_list_pending_returns_queue(client):
    with patch(
        "routes.admin.export_approvals.approvals.list_pending",
        AsyncMock(return_value=[{"id": "r1", "status": "pending"}]),
    ):
        resp = client.get("/api/admin/export-approvals/pending")
    assert resp.status_code == 200
    assert resp.json() == [{"id": "r1", "status": "pending"}]


def test_approve_success(client):
    with (
        patch(
            "routes.admin.export_approvals.approvals.approve",
            AsyncMock(return_value={"id": "r1", "status": "approved"}),
        ),
        patch("routes.admin.export_approvals.log_admin_action", AsyncMock(return_value="a1")),
    ):
        resp = client.post("/api/admin/export-approvals/r1/approve", json={"decision_note": "ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


def test_approve_self_approval_returns_403(client):
    from backend.services import admin_export_approvals as approvals

    with patch(
        "routes.admin.export_approvals.approvals.approve",
        AsyncMock(side_effect=approvals.SelfApprovalError()),
    ):
        resp = client.post("/api/admin/export-approvals/r1/approve", json={})
    assert resp.status_code == 403


def test_approve_not_found_returns_404(client):
    from backend.services import admin_export_approvals as approvals

    with patch(
        "routes.admin.export_approvals.approvals.approve",
        AsyncMock(side_effect=approvals.RequestNotFound("r1")),
    ):
        resp = client.post("/api/admin/export-approvals/r1/approve", json={})
    assert resp.status_code == 404


def test_approve_already_decided_returns_409(client):
    from backend.services import admin_export_approvals as approvals

    with patch(
        "routes.admin.export_approvals.approvals.approve",
        AsyncMock(side_effect=approvals.RequestAlreadyDecided("already approved")),
    ):
        resp = client.post("/api/admin/export-approvals/r1/approve", json={})
    assert resp.status_code == 409


def test_deny_success(client):
    with (
        patch(
            "routes.admin.export_approvals.approvals.deny",
            AsyncMock(return_value={"id": "r1", "status": "denied"}),
        ),
        patch("routes.admin.export_approvals.log_admin_action", AsyncMock(return_value="a1")),
    ):
        resp = client.post("/api/admin/export-approvals/r1/deny", json={"decision_note": "not needed"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "denied"


def test_export_approvals_require_super_admin(client, app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: {"id": "admin-2", "role": "admin", "modules": []}
    resp = client.get("/api/admin/export-approvals/pending")
    assert resp.status_code == 403
