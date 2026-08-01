"""
Route tests for routes/admin/support.py (disputes, support tickets, flags,
complaints admin CRUD).

Auth: the ``support`` module is enforced at include_router time via
``require_module("support")``, which itself depends on ``get_admin_user``.
Overriding get_admin_user with a super_admin satisfies both the auth check
and the module RBAC check in one shot (super_admin always passes
require_module - see dependencies/__init__.py).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import routes.admin.support as m

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "a@spinr.app", "modules": []}


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def _set_admin(app_fixture):
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    monkeypatch.setattr(m, "log_admin_action", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _no_zoho_tasks(monkeypatch):
    # These are asyncio.create_task fire-and-forget calls in the route; stub
    # them so tests don't depend on the real Zoho Desk integration or leave
    # dangling tasks.
    monkeypatch.setattr(m, "create_ticket_for_dispute", AsyncMock(return_value=None))
    monkeypatch.setattr(m, "create_ticket_for_flag", AsyncMock(return_value=None))
    monkeypatch.setattr(m, "create_ticket_for_complaint", AsyncMock(return_value=None))


def test_authz_denied_without_admin(client):
    resp = client.get("/api/admin/disputes")
    assert resp.status_code in (401, 403)


# ---------- Disputes ----------


def test_get_disputes_filters_status(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/disputes", params={"status": "open"})
    assert resp.status_code == 200
    assert get_rows.call_args.args[1] == {"status": "open"}


def test_get_disputes_table_missing_returns_empty(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=Exception("no table")))
    resp = client.get("/api/admin/disputes")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_disputes_enriches_user_name(client, _set_admin, monkeypatch):
    async def _get_rows(table, filters, **kw):
        if table == "disputes":
            return [{"id": "d1", "user_id": "u1", "status": "open"}]
        if table == "users":
            return [{"id": "u1", "first_name": "Amy", "last_name": "Lee"}]
        return []

    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows))
    resp = client.get("/api/admin/disputes")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["user_name"] == "Amy Lee"


def test_get_disputes_user_enrich_db_error_surfaces_503(client, _set_admin, monkeypatch):
    async def _get_rows(table, filters, **kw):
        if table == "disputes":
            return [{"id": "d1", "user_id": "u1", "status": "open"}]
        raise Exception("db down")

    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows))
    resp = client.get("/api/admin/disputes")
    assert resp.status_code == 503


def test_dispute_stats_normal(client, _set_admin, monkeypatch):
    # NOTE: a resolved row with a non-numeric refund_amount (e.g. "bad") is
    # NOT exercised here — `Decimal(str("bad"))` raises `decimal.InvalidOperation`,
    # which the route's `except (TypeError, ValueError)` does not catch, so it
    # would 500 instead of being tolerated. This is a pre-existing app bug,
    # left unfixed per this task's TEST-ONLY scope (see PR description).
    rows = [
        {"status": "open"},
        {"status": "resolved", "refund_amount": "10.50"},
        {"status": "unknown_status"},
    ]
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=rows))
    resp = client.get("/api/admin/disputes/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["open"] == 1
    assert body["resolved"] == 1
    assert body["total_refunded"] == 10.5


def test_dispute_stats_table_missing(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=Exception("no table")))
    resp = client.get("/api/admin/disputes/stats")
    assert resp.status_code == 200
    assert resp.json()["total_refunded"] == 0


def test_create_dispute(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "insert_one", AsyncMock(return_value=None))
    resp = client.post(
        "/api/admin/disputes",
        json={"ride_id": "r1", "user_id": "u1", "reason": "no-show", "description": "d"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["dispute"]["status"] == "pending"
    assert "user_name" not in body["dispute"]


def test_get_dispute_details_not_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=[]))
    resp = client.get("/api/admin/disputes/d1")
    assert resp.status_code == 404


def test_get_dispute_details_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "d1", "ride_id": "r1"}]))
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1"}))
    resp = client.get("/api/admin/disputes/d1")
    assert resp.status_code == 200
    assert resp.json()["ride_details"]["id"] == "r1"


def test_update_dispute_no_fields_skips_write(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.put("/api/admin/disputes/d1", json={})
    assert resp.status_code == 200
    update_one.assert_not_called()


def test_update_dispute_with_fields(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.put("/api/admin/disputes/d1", json={"status": "resolved", "refund_amount": 5})
    assert resp.status_code == 200
    update_one.assert_called_once()
    assert update_one.call_args.args[2]["status"] == "resolved"


def test_resolve_dispute(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.put("/api/admin/disputes/d1/resolve", json={"status": "resolved", "notes": "ok"})
    assert resp.status_code == 200
    updates = update_one.call_args.args[2]
    assert updates["resolution_status"] == "resolved"
    assert updates["resolved_by"] == "admin-1"


def test_delete_dispute(client, _set_admin, monkeypatch):
    delete_many = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "delete_many", delete_many)
    resp = client.delete("/api/admin/disputes/d1")
    assert resp.status_code == 200
    delete_many.assert_called_once_with("disputes", {"id": "d1"})


# ---------- Support Tickets ----------


def test_get_tickets_filters(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/tickets", params={"status": "open", "service_area_id": "sa1"})
    assert resp.status_code == 200
    assert get_rows.call_args.args[1] == {"status": "open", "service_area_id": "sa1"}


def test_create_ticket(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "insert_one", AsyncMock(return_value=None))
    resp = client.post(
        "/api/admin/tickets",
        json={"subject": "help", "category": "general", "message": "hi", "user_email": "u@x.com"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticket"]["status"] == "open"


def test_get_ticket_details_not_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(return_value=[]))
    resp = client.get("/api/admin/tickets/t1")
    assert resp.status_code == 404


def test_get_ticket_details_found(client, _set_admin, monkeypatch):
    async def _get_rows(table, filters, **kw):
        if table == "support_tickets":
            return [{"id": "t1"}]
        return [{"id": "m1", "message": "hi"}]

    monkeypatch.setattr(m.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows))
    resp = client.get("/api/admin/tickets/t1")
    assert resp.status_code == 200
    assert resp.json()["messages"][0]["id"] == "m1"


def test_reply_to_ticket_with_status_update(client, _set_admin, monkeypatch):
    insert_one = AsyncMock()
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "insert_one", insert_one)
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.post("/api/admin/tickets/t1/reply", json={"message": "we're on it", "status": "in_progress"})
    assert resp.status_code == 200
    insert_one.assert_called_once()
    assert insert_one.call_args.args[1]["sender_id"] == "admin-1"
    update_one.assert_called_once()


def test_reply_to_ticket_without_status(client, _set_admin, monkeypatch):
    insert_one = AsyncMock()
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "insert_one", insert_one)
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.post("/api/admin/tickets/t1/reply", json={"message": "noted"})
    assert resp.status_code == 200
    update_one.assert_not_called()


def test_close_ticket(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.post("/api/admin/tickets/t1/close")
    assert resp.status_code == 200
    assert update_one.call_args.args[2]["status"] == "closed"


def test_update_ticket_no_fields_skips_write(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.put("/api/admin/tickets/t1", json={})
    assert resp.status_code == 200
    update_one.assert_not_called()


def test_update_ticket_with_fields(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    resp = client.put("/api/admin/tickets/t1", json={"priority": "high", "status": "open"})
    assert resp.status_code == 200
    assert update_one.call_args.args[2]["priority"] == "high"


def test_delete_ticket(client, _set_admin, monkeypatch):
    delete_many = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "delete_many", delete_many)
    resp = client.delete("/api/admin/tickets/t1")
    assert resp.status_code == 200
    delete_many.assert_called_once_with("support_tickets", {"id": "t1"})


# ---------- Flags ----------


def test_flag_ride_not_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value=None))
    resp = client.post("/api/admin/rides/r1/flag", json={"target_type": "rider", "reason": "abuse"})
    assert resp.status_code == 404


def test_flag_bad_target_type(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1"}))
    resp = client.post("/api/admin/rides/r1/flag", json={"target_type": "bogus", "reason": "abuse"})
    assert resp.status_code == 400


def test_flag_no_target_assigned(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": None}))
    resp = client.post("/api/admin/rides/r1/flag", json={"target_type": "rider", "reason": "abuse"})
    assert resp.status_code == 400


def test_flag_success_creates_and_audits(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "driver_id": "d1"}))
    create_flag = AsyncMock(return_value={"id": "f1", "target_type": "driver", "target_id": "d1"})
    monkeypatch.setattr(m.db_supabase, "create_flag", create_flag)
    resp = client.post(
        "/api/admin/rides/r1/flag",
        json={"target_type": "driver", "reason": "unsafe", "description": "drove badly"},
    )
    assert resp.status_code == 200
    assert resp.json()["target_id"] == "d1"
    m.log_admin_action.assert_awaited()


def test_list_flags_filters(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/flags", params={"target_type": "driver", "is_active": "true"})
    assert resp.status_code == 200
    assert get_rows.call_args.args[1] == {"target_type": "driver", "is_active": True}


def test_deactivate_flag_not_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "update_one", AsyncMock(return_value=None))
    resp = client.put("/api/admin/flags/f1/deactivate")
    assert resp.status_code == 404


def test_deactivate_flag_success(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "update_one", AsyncMock(return_value={"id": "f1"}))
    resp = client.put("/api/admin/flags/f1/deactivate")
    assert resp.status_code == 200


def test_delete_flag(client, _set_admin, monkeypatch):
    delete_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "delete_one", delete_one)
    resp = client.delete("/api/admin/flags/f1")
    assert resp.status_code == 200
    delete_one.assert_called_once_with("flags", {"id": "f1"})


# ---------- Complaints ----------


def test_complaint_ride_not_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value=None))
    resp = client.post(
        "/api/admin/rides/r1/complaint",
        json={"against_type": "rider", "category": "behavior", "description": "d"},
    )
    assert resp.status_code == 404


def test_complaint_bad_against_type(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1"}))
    resp = client.post(
        "/api/admin/rides/r1/complaint",
        json={"against_type": "bogus", "category": "behavior", "description": "d"},
    )
    assert resp.status_code == 400


def test_complaint_no_target_assigned(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "driver_id": None}))
    resp = client.post(
        "/api/admin/rides/r1/complaint",
        json={"against_type": "driver", "category": "behavior", "description": "d"},
    )
    assert resp.status_code == 400


def test_complaint_success(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "get_ride", AsyncMock(return_value={"id": "r1", "rider_id": "u1"}))
    monkeypatch.setattr(m.db_supabase, "create_complaint", AsyncMock(return_value={"id": "c1", "against_id": "u1"}))
    resp = client.post(
        "/api/admin/rides/r1/complaint",
        json={"against_type": "rider", "category": "fraud", "description": "d"},
    )
    assert resp.status_code == 200
    assert resp.json()["against_id"] == "u1"


def test_resolve_complaint_not_found(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "resolve_complaint", AsyncMock(return_value=None))
    resp = client.put("/api/admin/complaints/c1/resolve", json={"status": "resolved", "resolution": "ok"})
    assert resp.status_code == 404


def test_resolve_complaint_success(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "resolve_complaint", AsyncMock(return_value={"id": "c1", "status": "resolved"}))
    resp = client.put("/api/admin/complaints/c1/resolve", json={"status": "resolved", "resolution": "ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"


def test_list_complaints_filters(client, _set_admin, monkeypatch):
    get_rows = AsyncMock(return_value=[])
    monkeypatch.setattr(m.db_supabase, "get_rows", get_rows)
    resp = client.get("/api/admin/complaints", params={"against_type": "driver", "status": "open"})
    assert resp.status_code == 200
    assert get_rows.call_args.args[1] == {"status": "open", "against_type": "driver"}


def test_delete_complaint(client, _set_admin, monkeypatch):
    delete_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "delete_one", delete_one)
    resp = client.delete("/api/admin/complaints/c1")
    assert resp.status_code == 200
    delete_one.assert_called_once_with("complaints", {"id": "c1"})
