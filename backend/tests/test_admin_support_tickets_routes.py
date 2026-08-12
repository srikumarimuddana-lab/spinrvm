"""
Route tests for routes/admin/support_tickets.py: config CRUD, sync, connection
test, dashboard, trends, ticket list/create/detail/search, reply, comment,
patch, tags. (Service-area + AI-suggest routes already have dedicated
coverage in test_support_tickets_service_area_routes.py / test_support_tickets_ai_suggest.py
and are intentionally not duplicated here.)

Auth: require_module("support_tickets") is enforced per-handler via
Depends, which itself depends on get_admin_user. A super_admin override
bypasses the module-membership check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import routes.admin.support_tickets as m
from services.zoho_desk_service import ZohoDeskError

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


def test_authz_denied_without_admin(client):
    resp = client.get("/api/admin/support-tickets/config")
    assert resp.status_code in (401, 403)


# ---------- Config ----------


def test_get_config_never_leaks_secrets(client, _set_admin, monkeypatch):
    monkeypatch.setattr(
        m.db_supabase,
        "find_one",
        AsyncMock(
            return_value={
                "enabled": True,
                "client_id": "abc",
                "client_secret": "shh",
                "refresh_token": "tok",
                "org_id": "org1",
            }
        ),
    )
    resp = client.get("/api/admin/support-tickets/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_client_secret"] is True
    assert "client_secret" not in body
    assert body["connected"] is True


def test_update_config_no_fields_400(client, _set_admin):
    resp = client.put("/api/admin/support-tickets/config", json={})
    assert resp.status_code == 400


def test_update_config_drops_empty_secret_strings(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.put(
        "/api/admin/support-tickets/config",
        json={"client_secret": "", "org_id": "org1"},
    )
    assert resp.status_code == 200
    fields = update_one.call_args.args[2]
    assert "client_secret" not in fields
    assert fields["org_id"] == "org1"


def test_update_config_credential_change_clears_cached_token(client, _set_admin, monkeypatch):
    update_one = AsyncMock()
    monkeypatch.setattr(m.db_supabase, "update_one", update_one)
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.put(
        "/api/admin/support-tickets/config",
        json={"client_id": "new-id"},
    )
    assert resp.status_code == 200
    fields = update_one.call_args.args[2]
    assert fields["access_token"] is None
    assert fields["access_token_expires_at"] is None


def test_trigger_sync_success(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m, "run_sync", AsyncMock(return_value={"upserted": 5}), raising=False)
    import utils.zoho_desk_sync as zds

    monkeypatch.setattr(zds, "run_sync", AsyncMock(return_value={"upserted": 5}))
    resp = client.post("/api/admin/support-tickets/sync")
    assert resp.status_code == 200
    assert resp.json()["upserted"] == 5


def test_trigger_sync_zoho_error_mapped(client, _set_admin, monkeypatch):
    import utils.zoho_desk_sync as zds

    async def _raise():
        raise ZohoDeskError("bad creds", status=401)

    monkeypatch.setattr(zds, "run_sync", _raise)
    resp = client.post("/api/admin/support-tickets/sync")
    assert resp.status_code == 401


def test_config_test_connection_success(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "list_departments", AsyncMock(return_value={"data": [{"id": "d1"}]}))
    resp = client.post("/api/admin/support-tickets/config/test")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_config_test_connection_failure(client, _set_admin, monkeypatch):
    async def _raise():
        raise ZohoDeskError("no creds", status=502)

    monkeypatch.setattr(m.zoho, "list_departments", _raise)
    resp = client.post("/api/admin/support-tickets/config/test")
    assert resp.status_code == 502


# ---------- Dashboard / trends ----------


def test_dashboard_served_from_mirror(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(m.zoho_desk_db, "open_closed_counts", AsyncMock(return_value=(10, 4)))
    monkeypatch.setattr(m.zoho_desk_db, "count_by_status", AsyncMock(return_value=(10, {"Open": 6, "Closed": 4})))
    monkeypatch.setattr(m.zoho_desk_db, "list_mirror", AsyncMock(return_value=[{"id": "t1"}]))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.get("/api/admin/support-tickets/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "db"
    assert body["total"] == 10
    assert body["open"] == 6


def test_dashboard_live_fallback_when_mirror_not_ready(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    monkeypatch.setattr(
        m.zoho,
        "list_tickets",
        AsyncMock(return_value={"data": [{"status": "Open"}, {"status": "Closed"}]}),
    )
    monkeypatch.setattr(m.zoho, "ticket_count", AsyncMock(return_value=2))
    resp = client.get("/api/admin/support-tickets/dashboard")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "live"
    assert body["open"] == 1


def test_dashboard_live_fallback_zoho_error(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))

    async def _raise(**kw):
        raise ZohoDeskError("down", status=502)

    monkeypatch.setattr(m.zoho, "list_tickets", _raise)
    resp = client.get("/api/admin/support-tickets/dashboard")
    assert resp.status_code == 502


def test_trends_served_from_mirror(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=True))
    rows = [
        {
            "createdTime": "2026-07-01T00:00:00.000Z",
            "closedTime": "2026-07-02T00:00:00.000Z",
            "status": "Closed",
            "priority": "High",
        }
    ]
    monkeypatch.setattr(m.zoho_desk_db, "fetch_window", AsyncMock(return_value=rows))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.get("/api/admin/support-tickets/trends")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "db"
    assert body["stats"]["closed"] == 1


def test_trends_live_fallback(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    monkeypatch.setattr(
        m.zoho,
        "list_tickets",
        AsyncMock(return_value={"data": [{"createdTime": "2026-07-29T00:00:00.000Z", "status": "Open"}]}),
    )
    resp = client.get("/api/admin/support-tickets/trends")
    assert resp.status_code == 200
    assert resp.json()["source"] == "live"


# ---------- Agents / departments ----------


def test_agents_list(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "list_agents", AsyncMock(return_value=[{"id": "a1"}]))
    resp = client.get("/api/admin/support-tickets/agents")
    assert resp.status_code == 200


def test_agents_list_zoho_error(client, _set_admin, monkeypatch):
    async def _raise():
        raise ZohoDeskError("boom", status=502)

    monkeypatch.setattr(m.zoho, "list_agents", _raise)
    resp = client.get("/api/admin/support-tickets/agents")
    assert resp.status_code == 502


def test_departments_list(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "list_departments", AsyncMock(return_value={"data": []}))
    resp = client.get("/api/admin/support-tickets/departments")
    assert resp.status_code == 200


# ---------- Tickets CRUD ----------


def test_create_ticket(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "create_ticket", AsyncMock(return_value={"id": "T1"}))
    resp = client.post("/api/admin/support-tickets/tickets", json={"subject": "help"})
    assert resp.status_code == 200
    assert resp.json()["id"] == "T1"


def test_create_ticket_zoho_error(client, _set_admin, monkeypatch):
    async def _raise(**kw):
        raise ZohoDeskError("bad", status=400)

    monkeypatch.setattr(m.zoho, "create_ticket", _raise)
    resp = client.post("/api/admin/support-tickets/tickets", json={"subject": "help"})
    assert resp.status_code == 400


def test_list_tickets_from_mirror(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(m.zoho_desk_db, "list_mirror", AsyncMock(return_value=[{"id": "T1"}]))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.get("/api/admin/support-tickets/tickets")
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


def test_list_tickets_live_fallback(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    monkeypatch.setattr(m.zoho, "list_tickets", AsyncMock(return_value={"data": []}))
    resp = client.get("/api/admin/support-tickets/tickets")
    assert resp.status_code == 200


def test_search_tickets_from_mirror(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(m.zoho_desk_db, "list_mirror", AsyncMock(return_value=[{"id": "T1"}]))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.get("/api/admin/support-tickets/search", params={"q": "billing"})
    assert resp.status_code == 200
    assert resp.json()["source"] == "db"


def test_search_tickets_live_fallback(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho_desk_db, "mirror_ready", AsyncMock(return_value=False))
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    monkeypatch.setattr(m.zoho, "search_tickets", AsyncMock(return_value={"data": []}))
    resp = client.get("/api/admin/support-tickets/search", params={"q": "billing"})
    assert resp.status_code == 200


def test_ticket_detail(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "get_ticket", AsyncMock(return_value={"id": "T1"}))
    resp = client.get("/api/admin/support-tickets/tickets/T1")
    assert resp.status_code == 200


def test_ticket_threads(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "get_ticket_threads", AsyncMock(return_value={"data": []}))
    resp = client.get("/api/admin/support-tickets/tickets/T1/threads")
    assert resp.status_code == 200


def test_ticket_thread_detail(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "get_thread", AsyncMock(return_value={"id": "th1"}))
    resp = client.get("/api/admin/support-tickets/tickets/T1/threads/th1")
    assert resp.status_code == 200


# ---------- Reply / comment / patch / tags ----------


def test_reply_requires_from_email_for_email_channel(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={}))
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/reply",
        json={"content": "hi", "channel": "EMAIL"},
    )
    assert resp.status_code == 400


def test_reply_email_with_configured_from_address(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={"default_from_email": "support@spinr.ca"}))
    send_reply = AsyncMock(return_value={"id": "r1"})
    monkeypatch.setattr(m.zoho, "send_reply", send_reply)
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/reply",
        json={"content": "hi", "channel": "EMAIL"},
    )
    assert resp.status_code == 200
    assert send_reply.call_args.kwargs["from_email"] == "support@spinr.ca"


def test_reply_zoho_error(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.db_supabase, "find_one", AsyncMock(return_value={"default_from_email": "s@spinr.ca"}))

    async def _raise(*a, **kw):
        raise ZohoDeskError("fail", status=502)

    monkeypatch.setattr(m.zoho, "send_reply", _raise)
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/reply",
        json={"content": "hi", "channel": "EMAIL"},
    )
    assert resp.status_code == 502


def test_reply_appends_signature_when_configured(client, _set_admin, monkeypatch):
    monkeypatch.setattr(
        m.db_supabase,
        "find_one",
        AsyncMock(
            return_value={
                "default_from_email": "support@spinr.ca",
                "helpdesk_email_signature": "<b>Spinr Support</b><br>support@spinr.ca",
            }
        ),
    )
    send_reply = AsyncMock(return_value={"id": "r1"})
    monkeypatch.setattr(m.zoho, "send_reply", send_reply)
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/reply",
        json={"content": "<p>Thanks!</p>", "channel": "EMAIL"},
    )
    assert resp.status_code == 200
    sent_content = send_reply.call_args.kwargs["content"]
    assert "<p>Thanks!</p>" in sent_content
    assert "<b>Spinr Support</b>" in sent_content
    assert "border-top" in sent_content


def test_reply_no_signature_when_blank(client, _set_admin, monkeypatch):
    monkeypatch.setattr(
        m.db_supabase,
        "find_one",
        AsyncMock(return_value={"default_from_email": "support@spinr.ca"}),
    )
    send_reply = AsyncMock(return_value={"id": "r1"})
    monkeypatch.setattr(m.zoho, "send_reply", send_reply)
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/reply",
        json={"content": "<p>Thanks!</p>", "channel": "EMAIL"},
    )
    assert resp.status_code == 200
    sent_content = send_reply.call_args.kwargs["content"]
    assert sent_content == "<p>Thanks!</p>"


def test_reply_no_signature_for_non_email_channel(client, _set_admin, monkeypatch):
    monkeypatch.setattr(
        m.db_supabase,
        "find_one",
        AsyncMock(
            return_value={
                "default_from_email": "support@spinr.ca",
                "helpdesk_email_signature": "<b>Sig</b>",
            }
        ),
    )
    send_reply = AsyncMock(return_value={"id": "r1"})
    monkeypatch.setattr(m.zoho, "send_reply", send_reply)
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/reply",
        json={"content": "hi", "channel": "CHAT"},
    )
    assert resp.status_code == 200
    sent_content = send_reply.call_args.kwargs["content"]
    assert sent_content == "hi"


def test_config_returns_signature(client, _set_admin, monkeypatch):
    monkeypatch.setattr(
        m.db_supabase,
        "find_one",
        AsyncMock(return_value={"helpdesk_email_signature": "<b>Team Spinr</b>"}),
    )
    resp = client.get("/api/admin/support-tickets/config")
    assert resp.status_code == 200
    assert resp.json()["helpdesk_email_signature"] == "<b>Team Spinr</b>"


def test_comment_ticket(client, _set_admin, monkeypatch):
    monkeypatch.setattr(m.zoho, "add_comment", AsyncMock(return_value={"id": "c1"}))
    resp = client.post("/api/admin/support-tickets/tickets/T1/comment", json={"content": "note"})
    assert resp.status_code == 200


def test_patch_ticket_no_fields_400(client, _set_admin):
    resp = client.patch("/api/admin/support-tickets/tickets/T1", json={})
    assert resp.status_code == 400


def test_patch_ticket_updates(client, _set_admin, monkeypatch):
    update_ticket = AsyncMock(return_value={"id": "T1", "status": "Closed"})
    monkeypatch.setattr(m.zoho, "update_ticket", update_ticket)
    resp = client.patch("/api/admin/support-tickets/tickets/T1", json={"status": "Closed"})
    assert resp.status_code == 200
    assert update_ticket.call_args.args[1] == {"status": "Closed"}


def test_patch_ticket_zoho_error(client, _set_admin, monkeypatch):
    async def _raise(*a, **kw):
        raise ZohoDeskError("bad status", status=400)

    monkeypatch.setattr(m.zoho, "update_ticket", _raise)
    resp = client.patch("/api/admin/support-tickets/tickets/T1", json={"status": "Bogus"})
    assert resp.status_code == 400


def test_update_ticket_tags_no_changes_400(client, _set_admin):
    resp = client.post("/api/admin/support-tickets/tickets/T1/tags", json={"add": [], "remove": []})
    assert resp.status_code == 400


def test_update_ticket_tags_add_and_remove(client, _set_admin, monkeypatch):
    remove_tags = AsyncMock()
    add_tags = AsyncMock()
    monkeypatch.setattr(m.zoho, "remove_tags", remove_tags)
    monkeypatch.setattr(m.zoho, "add_tags", add_tags)
    resp = client.post(
        "/api/admin/support-tickets/tickets/T1/tags",
        json={"add": ["vip"], "remove": ["stale"]},
    )
    assert resp.status_code == 200
    remove_tags.assert_called_once_with("T1", ["stale"])
    add_tags.assert_called_once_with("T1", ["vip"])
