"""
Route tests for the new Help Desk endpoints (routes/admin/support_tickets.py):
service-area list / get / assign, and AI reply suggestion.

Verifies authz gating, status mapping, the 404 on an unknown area, and that the
AI route never sends — it only returns a draft. Auth is faked via a
dependency_override on get_admin_user (require_module wraps it); super_admin
passes the module check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import routes.admin.support_tickets as m

_ADMIN = {"id": "admin-1", "role": "super_admin", "email": "a@spinr.app", "modules": ["support_tickets"]}


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


def test_authz_denied_without_admin(client):
    # No get_admin_user override -> the gate rejects (401 unauth / 403 module).
    resp = client.get("/api/admin/support-tickets/service-areas")
    assert resp.status_code in (401, 403)


def test_service_areas_list(client, _set_admin, monkeypatch):
    monkeypatch.setattr(
        m.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "sa1", "name": "Regina"}])
    )
    resp = client.get("/api/admin/support-tickets/service-areas")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["name"] == "Regina"


def test_get_ticket_service_area_uses_mirror_no_zoho_call(client, _set_admin, monkeypatch):
    # Mirrored ticket -> resolve from the DB, spend no Zoho API credit.
    monkeypatch.setattr(
        m.db_supabase, "find_one", AsyncMock(return_value={"zoho_id": "T1", "contact_email": "a@b.com"})
    )
    get_ticket = AsyncMock()
    monkeypatch.setattr(m.zoho, "get_ticket", get_ticket)
    monkeypatch.setattr(
        m.ticket_area,
        "assignment_view",
        AsyncMock(return_value={"service_area_id": None, "needs_assignment": True, "suggested": None}),
    )
    resp = client.get("/api/admin/support-tickets/tickets/T1/service-area")
    assert resp.status_code == 200
    assert resp.json()["needs_assignment"] is True
    get_ticket.assert_not_called()


def test_get_ticket_service_area_falls_back_to_live_when_unmirrored(client, _set_admin, monkeypatch):
    # Not mirrored yet -> one live Zoho fetch so pre-backfill still works.
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value=None))
    get_ticket = AsyncMock(return_value={"id": "T1", "contact": {"email": "a@b.com"}})
    monkeypatch.setattr(m.zoho, "get_ticket", get_ticket)
    monkeypatch.setattr(
        m.ticket_area,
        "assignment_view",
        AsyncMock(return_value={"service_area_id": None, "needs_assignment": True, "suggested": None}),
    )
    resp = client.get("/api/admin/support-tickets/tickets/T1/service-area")
    assert resp.status_code == 200
    get_ticket.assert_called_once()


def test_put_service_area_valid(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={"id": "sa1", "name": "Regina"}))
    monkeypatch.setattr(
        m.ticket_area,
        "set_ticket_area",
        AsyncMock(return_value={"service_area_id": "sa1", "service_area_name": "Regina", "service_area_source": "manual"}),
    )
    resp = client.put("/api/admin/support-tickets/tickets/T1/service-area", json={"service_area_id": "sa1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["service_area_id"] == "sa1"
    assert body["needs_assignment"] is False


def test_put_service_area_unknown_area_404(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value=None))
    set_mock = AsyncMock()
    monkeypatch.setattr(m.ticket_area, "set_ticket_area", set_mock)
    resp = client.put("/api/admin/support-tickets/tickets/T1/service-area", json={"service_area_id": "ghost"})
    assert resp.status_code == 404
    set_mock.assert_not_called()  # never persist an invalid area


def test_put_service_area_clear(client, _set_admin, monkeypatch):
    # Clearing (None) skips the area existence check and nulls the assignment.
    set_mock = AsyncMock(return_value={"service_area_id": None})
    monkeypatch.setattr(m.ticket_area, "set_ticket_area", set_mock)
    resp = client.put("/api/admin/support-tickets/tickets/T1/service-area", json={"service_area_id": None})
    assert resp.status_code == 200
    assert resp.json()["needs_assignment"] is True
    assert set_mock.call_args.args[1] is None  # area_id passed as None


def test_ai_suggest_returns_draft_only(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "get_ticket", AsyncMock(return_value={"id": "T1", "subject": "help"}))
    monkeypatch.setattr(m.zoho, "get_ticket_threads", AsyncMock(return_value={"data": []}))
    monkeypatch.setattr(m.ticket_area, "get_ticket_area", AsyncMock(return_value={"service_area_name": "Regina"}))

    import ai.support_assistant as sa

    suggest = AsyncMock(return_value={"reply": "Hi there", "provider": "anthropic", "model": "claude"})
    monkeypatch.setattr(sa, "suggest_ticket_reply", suggest)
    # Ensure no reply is ever sent to the customer from the suggest endpoint.
    send = AsyncMock()
    monkeypatch.setattr(m.zoho, "send_reply", send)

    resp = client.post("/api/admin/support-tickets/tickets/T1/ai-suggest-reply")
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Hi there"
    send.assert_not_called()


def test_ai_suggest_disabled_maps_409(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "get_ticket", AsyncMock(return_value={"id": "T1"}))
    monkeypatch.setattr(m.zoho, "get_ticket_threads", AsyncMock(return_value={"data": []}))
    monkeypatch.setattr(m.ticket_area, "get_ticket_area", AsyncMock(return_value={}))

    import ai.support_assistant as sa

    async def _raise(**_kw):
        raise sa.SupportAssistantError("ai_disabled", "disabled")

    monkeypatch.setattr(sa, "suggest_ticket_reply", _raise)
    resp = client.post("/api/admin/support-tickets/tickets/T1/ai-suggest-reply")
    assert resp.status_code == 409


def test_ai_suggest_provider_error_maps_502(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "get_ticket", AsyncMock(return_value={"id": "T1"}))
    monkeypatch.setattr(m.zoho, "get_ticket_threads", AsyncMock(return_value={"data": []}))
    monkeypatch.setattr(m.ticket_area, "get_ticket_area", AsyncMock(return_value={}))

    import ai.support_assistant as sa

    async def _raise(**_kw):
        raise sa.SupportAssistantError("provider_error", "boom")

    monkeypatch.setattr(sa, "suggest_ticket_reply", _raise)
    resp = client.post("/api/admin/support-tickets/tickets/T1/ai-suggest-reply")
    assert resp.status_code == 502
