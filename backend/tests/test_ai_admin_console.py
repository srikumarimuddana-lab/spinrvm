"""Super-admin AI console: role gating, impersonation plumbing, auditing.

Pins the security contract:
- strictly super_admin (a plain admin with every module grant still gets 403)
- admin accounts cannot be chat targets (404, same as missing users)
- the chat turn runs AS the target user with the right audience and the
  admin_actor_id stamp, and every action writes an audit_logs row
- conversation/message reads are scoped to the target user's own threads
"""

from unittest.mock import AsyncMock, patch

import pytest

RIDER_TARGET = {"id": "rider-1", "role": "user", "is_driver": False}
DRIVER_TARGET = {"id": "driver-1", "role": "user", "is_driver": True}

FRAMES = [
    ("meta", {"conversation_id": "conv-1", "user_message_id": "m-1"}),
    ("token", {"text": "Hello "}),
    ("token", {"text": "rider."}),
    ("done", {"message_id": "m-2", "usage": {"input_tokens": 1, "output_tokens": 2}, "stop_reason": "end_turn"}),
]


def _frames_gen(frames):
    async def fake_run_chat_turn(**kwargs):
        fake_run_chat_turn.kwargs = kwargs
        for frame in frames:
            yield frame

    return fake_run_chat_turn


@pytest.fixture
def super_admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin-9", "role": "super_admin"}
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


@pytest.fixture
def plain_admin_client(test_client):
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-2",
        "role": "admin",
        "modules": ["users", "settings", "support"],
    }
    yield test_client
    app.dependency_overrides.pop(get_admin_user, None)


def _patches(target=RIDER_TARGET, frames=FRAMES):
    return (
        patch("backend.routes.admin.ai_console.db_supabase.get_user_by_id", AsyncMock(return_value=target)),
        patch("backend.routes.admin.ai_console.db_supabase.insert_one", AsyncMock()),
        patch("backend.routes.admin.ai_console.run_chat_turn", _frames_gen(frames)),
    )


class TestRoleGate:
    def test_plain_admin_403(self, plain_admin_client):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            resp = plain_admin_client.post("/api/v1/admin/ai/chat", json={"user_id": "rider-1", "message": "hi"})
        assert resp.status_code == 403

    def test_unauthenticated_blocked(self, test_client):
        resp = test_client.post("/api/v1/admin/ai/chat", json={"user_id": "rider-1", "message": "hi"})
        assert resp.status_code in (401, 403)

    def test_reads_also_super_admin_only(self, plain_admin_client):
        p1, p2, p3 = _patches()
        with p1, p2, p3:
            resp = plain_admin_client.get("/api/v1/admin/ai/users/rider-1/conversations")
        assert resp.status_code == 403


class TestChatAsUser:
    def test_runs_as_target_with_actor_stamp(self, super_admin_client):
        gen = _frames_gen(FRAMES)
        p1, p2, _ = _patches()
        with p1, p2, patch("backend.routes.admin.ai_console.run_chat_turn", gen):
            resp = super_admin_client.post(
                "/api/v1/admin/ai/chat", json={"user_id": "rider-1", "message": "where is my ride?"}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "Hello rider."
        assert body["conversation_id"] == "conv-1"
        assert body["audience"] == "rider"
        # the turn ran AS the rider, stamped with the acting admin
        assert gen.kwargs["user"]["id"] == "rider-1"
        assert gen.kwargs["admin_actor_id"] == "admin-9"

    def test_driver_target_gets_driver_audience(self, super_admin_client):
        gen = _frames_gen(FRAMES)
        p1, p2, _ = _patches(target=DRIVER_TARGET)
        with p1, p2, patch("backend.routes.admin.ai_console.run_chat_turn", gen):
            resp = super_admin_client.post("/api/v1/admin/ai/chat", json={"user_id": "driver-1", "message": "payout?"})
        assert resp.json()["audience"] == "driver"
        assert gen.kwargs["audience"] == "driver"

    def test_audience_override_for_dual_role(self, super_admin_client):
        gen = _frames_gen(FRAMES)
        p1, p2, _ = _patches(target=DRIVER_TARGET)
        with p1, p2, patch("backend.routes.admin.ai_console.run_chat_turn", gen):
            super_admin_client.post(
                "/api/v1/admin/ai/chat",
                json={"user_id": "driver-1", "message": "hi", "audience": "rider"},
            )
        assert gen.kwargs["audience"] == "rider"

    @pytest.mark.parametrize("role", ["admin", "super_admin", "operations", "support", "finance", "custom"])
    def test_staff_accounts_are_not_targets(self, super_admin_client, role):
        # Codex review (PR #1797): the full staff-role set must be rejected,
        # not just role == "admin" — mirrors dependencies._admin_roles.
        p1, p2, p3 = _patches(target={"id": "staff-2", "role": role})
        with p1, p2, p3:
            resp = super_admin_client.post("/api/v1/admin/ai/chat", json={"user_id": "staff-2", "message": "hi"})
        assert resp.status_code == 404

    def test_every_chat_is_audited(self, super_admin_client):
        audit = AsyncMock()
        p1, _, p3 = _patches()
        with p1, patch("backend.routes.admin.ai_console.db_supabase.insert_one", audit), p3:
            super_admin_client.post("/api/v1/admin/ai/chat", json={"user_id": "rider-1", "message": "hi"})
        row = audit.await_args.args[1]
        assert audit.await_args.args[0] == "audit_logs"
        assert row["action"] == "admin_ai_chat_as_user"
        assert row["actor_id"] == "admin-9"
        assert row["entity_id"] == "rider-1"

    def test_error_frames_map_to_http(self, super_admin_client):
        frames = [("error", {"code": "ai_disabled", "message": "off"})]
        p1, p2, _ = _patches()
        with p1, p2, patch("backend.routes.admin.ai_console.run_chat_turn", _frames_gen(frames)):
            resp = super_admin_client.post("/api/v1/admin/ai/chat", json={"user_id": "rider-1", "message": "hi"})
        assert resp.status_code == 503


class TestReads:
    def test_conversations_listed_and_audited(self, super_admin_client):
        listing = AsyncMock(return_value=[{"id": "conv-1", "title": "t", "updated_at": "u"}])
        audit = AsyncMock()
        with (
            patch("backend.routes.admin.ai_console.db_supabase.get_user_by_id", AsyncMock(return_value=RIDER_TARGET)),
            patch("backend.routes.admin.ai_console.db_supabase.insert_one", audit),
            patch("backend.routes.admin.ai_console.conversations.list_conversations", listing),
        ):
            resp = super_admin_client.get("/api/v1/admin/ai/users/rider-1/conversations")
        assert resp.status_code == 200
        listing.assert_awaited_once_with("rider-1")
        assert audit.await_args.args[1]["action"] == "admin_ai_conversations_viewed"

    def test_messages_owner_scoped_to_target(self, super_admin_client):
        get_messages = AsyncMock(return_value=None)  # foreign/missing thread
        with (
            patch("backend.routes.admin.ai_console.db_supabase.get_user_by_id", AsyncMock(return_value=RIDER_TARGET)),
            patch("backend.routes.admin.ai_console.db_supabase.insert_one", AsyncMock()),
            patch("backend.routes.admin.ai_console.conversations.get_messages", get_messages),
        ):
            resp = super_admin_client.get("/api/v1/admin/ai/users/rider-1/conversations/other-users-conv/messages")
        assert resp.status_code == 404
        get_messages.assert_awaited_once_with("other-users-conv", "rider-1")


SECURITY_EVENTS = [
    {"id": "e1", "user_id": "rider-1", "event_type": "impersonation", "severity": "critical", "source": "message"},
    {"id": "e2", "user_id": "rider-1", "event_type": "prompt_injection", "severity": "high", "source": "message"},
    {"id": "e3", "user_id": "rider-2", "event_type": "tool_blocked", "severity": "critical", "source": "tool"},
]


class TestSecurityEvents:
    def test_super_admin_only(self, plain_admin_client):
        with patch("backend.routes.admin.ai_console.db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = plain_admin_client.get("/api/v1/admin/ai/security-events")
        assert resp.status_code == 403

    def test_returns_events_and_summary(self, super_admin_client):
        get_rows = AsyncMock(return_value=SECURITY_EVENTS)
        with (
            patch("backend.routes.admin.ai_console.db_supabase.get_rows", get_rows),
            patch("backend.routes.admin.ai_console.db_supabase.insert_one", AsyncMock()),
        ):
            resp = super_admin_client.get("/api/v1/admin/ai/security-events")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["events"]) == 3
        summary = body["summary"]
        assert summary["total"] == 3
        assert summary["by_severity"] == {"critical": 2, "high": 1}
        assert summary["by_type"]["impersonation"] == 1
        # rider-1 has two events → ranked first in top_users
        assert summary["top_users"][0] == {"user_id": "rider-1", "count": 2}
        # most-recent-first ordering requested from the DB
        assert get_rows.await_args.kwargs["order"] == "created_at"
        assert get_rows.await_args.kwargs["desc"] is True

    def test_filters_pushed_to_query(self, super_admin_client):
        get_rows = AsyncMock(return_value=[])
        with (
            patch("backend.routes.admin.ai_console.db_supabase.get_rows", get_rows),
            patch("backend.routes.admin.ai_console.db_supabase.insert_one", AsyncMock()),
        ):
            resp = super_admin_client.get(
                "/api/v1/admin/ai/security-events?severity=critical&event_type=impersonation&source=tool&limit=25"
            )
        assert resp.status_code == 200
        filters = get_rows.await_args.kwargs["filters"]
        assert filters == {"severity": "critical", "event_type": "impersonation", "source": "tool"}
        assert get_rows.await_args.kwargs["limit"] == 25

    def test_invalid_severity_rejected(self, super_admin_client):
        with patch("backend.routes.admin.ai_console.db_supabase.get_rows", AsyncMock(return_value=[])):
            resp = super_admin_client.get("/api/v1/admin/ai/security-events?severity=bogus")
        assert resp.status_code == 422

    def test_limit_capped(self, super_admin_client):
        get_rows = AsyncMock(return_value=[])
        with (
            patch("backend.routes.admin.ai_console.db_supabase.get_rows", get_rows),
            patch("backend.routes.admin.ai_console.db_supabase.insert_one", AsyncMock()),
        ):
            super_admin_client.get("/api/v1/admin/ai/security-events?limit=99999")
        assert get_rows.await_args.kwargs["limit"] == 500

    def test_view_is_audited(self, super_admin_client):
        audit = AsyncMock()
        with (
            patch("backend.routes.admin.ai_console.db_supabase.get_rows", AsyncMock(return_value=SECURITY_EVENTS)),
            patch("backend.routes.admin.ai_console.db_supabase.insert_one", audit),
        ):
            super_admin_client.get("/api/v1/admin/ai/security-events")
        row = audit.await_args.args[1]
        assert row["action"] == "admin_ai_security_events_viewed"
        assert row["details"]["count"] == 3
