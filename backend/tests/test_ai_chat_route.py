"""/api/v1/ai/* route contract (e2e-ish via TestClient, orchestrator patched).

Pins: SSE frame order + headers on the streaming path, the non-streaming
JSON shape used by SupportScreen, error-code → HTTP-status mapping
(kill switch 503, daily cap 429, foreign conversation 404), auth
requirements, and owner-scoped conversation endpoints.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

RIDER = {"id": "rider-1", "is_driver": False, "role": "user"}


def _frames_gen(frames):
    async def fake_run_chat_turn(**kwargs):
        fake_run_chat_turn.kwargs = kwargs
        for frame in frames:
            yield frame

    return fake_run_chat_turn


HAPPY_FRAMES = [
    ("meta", {"conversation_id": "conv-1", "user_message_id": "m-1"}),
    ("token", {"text": "Your driver "}),
    ("tool", {"name": "get_active_ride", "status": "start"}),
    ("tool", {"name": "get_active_ride", "status": "end", "ok": True}),
    ("token", {"text": "is close."}),
    ("done", {"message_id": "m-2", "usage": {"input_tokens": 1, "output_tokens": 2}, "stop_reason": "end_turn"}),
]


def _parse_sse(body: str):
    frames = []
    for block in body.split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        frames.append((event, data))
    return frames


@pytest.fixture
def rider_client(test_client):
    from backend.server import app
    from dependencies import get_current_user

    app.dependency_overrides[get_current_user] = lambda: dict(RIDER)
    yield test_client
    app.dependency_overrides.pop(get_current_user, None)


class TestChatStreaming:
    def test_sse_frame_order_and_headers(self, rider_client):
        with patch("backend.routes.ai.run_chat_turn", _frames_gen(HAPPY_FRAMES)):
            resp = rider_client.post("/api/v1/ai/chat", json={"message": "where is my driver?"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["x-accel-buffering"] == "no"
        frames = _parse_sse(resp.text)
        assert [name for name, _ in frames] == ["meta", "token", "tool", "tool", "token", "done"]
        assert frames[0][1]["conversation_id"] == "conv-1"

    def test_error_frame_streams_inline(self, rider_client):
        frames = [("error", {"code": "ai_disabled", "message": "unavailable"})]
        with patch("backend.routes.ai.run_chat_turn", _frames_gen(frames)):
            resp = rider_client.post("/api/v1/ai/chat", json={"message": "hi"})
        # streaming responses are already 200 when the error frame arrives
        assert resp.status_code == 200
        assert _parse_sse(resp.text) == [("error", {"code": "ai_disabled", "message": "unavailable"})]

    def test_audience_derived_from_user_row(self, rider_client):
        from backend.server import app
        from dependencies import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {"id": "d-1", "is_driver": True}
        gen = _frames_gen(HAPPY_FRAMES)
        with patch("backend.routes.ai.run_chat_turn", gen):
            rider_client.post("/api/v1/ai/chat", json={"message": "payout question"})
        assert gen.kwargs["audience"] == "driver"

    def test_explicit_rider_audience_wins_for_dual_role_user(self, rider_client):
        # The rider app's main-screen assistant must get rider tools even when
        # the account is also a driver (dual-role).
        from backend.server import app
        from dependencies import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {"id": "d-1", "is_driver": True}
        gen = _frames_gen(HAPPY_FRAMES)
        with patch("backend.routes.ai.run_chat_turn", gen):
            rider_client.post("/api/v1/ai/chat", json={"message": "book me a ride", "audience": "rider"})
        assert gen.kwargs["audience"] == "rider"

    def test_driver_audience_rejected_for_non_driver(self, rider_client):
        gen = _frames_gen(HAPPY_FRAMES)
        with patch("backend.routes.ai.run_chat_turn", gen):
            resp = rider_client.post("/api/v1/ai/chat", json={"message": "payouts?", "audience": "driver"})
        assert resp.status_code == 403
        assert not hasattr(gen, "kwargs")

    def test_explicit_driver_audience_allowed_for_driver(self, rider_client):
        from backend.server import app
        from dependencies import get_current_user

        app.dependency_overrides[get_current_user] = lambda: {"id": "d-1", "is_driver": True}
        gen = _frames_gen(HAPPY_FRAMES)
        with patch("backend.routes.ai.run_chat_turn", gen):
            rider_client.post("/api/v1/ai/chat", json={"message": "payouts?", "audience": "driver"})
        assert gen.kwargs["audience"] == "driver"

    def test_invalid_audience_rejected(self, rider_client):
        resp = rider_client.post("/api/v1/ai/chat", json={"message": "hi", "audience": "admin"})
        assert resp.status_code == 422


class TestChatNonStreaming:
    def test_json_reply_shape(self, rider_client):
        frames = HAPPY_FRAMES + [("action", {"type": "open_support", "category": "refund", "link": "/support"})]
        with patch("backend.routes.ai.run_chat_turn", _frames_gen(frames)):
            resp = rider_client.post("/api/v1/ai/chat", json={"message": "hi", "stream": False})
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "conversation_id": "conv-1",
            "message_id": "m-2",
            "reply": "Your driver is close.",
            "actions": [{"type": "open_support", "category": "refund", "link": "/support"}],
        }

    @pytest.mark.parametrize(
        "code,status",
        [("ai_disabled", 503), ("daily_cap", 429), ("not_found", 404), ("provider_error", 502)],
    )
    def test_error_code_status_mapping(self, rider_client, code, status):
        frames = [("error", {"code": code, "message": "x"})]
        with patch("backend.routes.ai.run_chat_turn", _frames_gen(frames)):
            resp = rider_client.post("/api/v1/ai/chat", json={"message": "hi", "stream": False})
        assert resp.status_code == status
        assert resp.json()["detail"]["code"] == code

    def test_message_length_validated(self, rider_client):
        resp = rider_client.post("/api/v1/ai/chat", json={"message": "x" * 1001, "stream": False})
        assert resp.status_code == 422

    def test_requires_auth(self, test_client):
        resp = test_client.post("/api/v1/ai/chat", json={"message": "hi"})
        assert resp.status_code in (401, 403)


class TestConfig:
    def test_config_reflects_settings(self, rider_client):
        settings = {"ai_assistant_enabled": True, "ai_disclaimer": "AI can be wrong."}
        with patch("backend.routes.ai.get_app_settings", AsyncMock(return_value=settings)):
            resp = rider_client.get("/api/v1/ai/config")
        assert resp.json() == {"enabled": True, "mode": "enabled", "disclaimer": "AI can be wrong."}

    def test_disabled_reports_coming_soon_by_default(self, rider_client):
        with patch("backend.routes.ai.get_app_settings", AsyncMock(return_value={"ai_assistant_enabled": False})):
            resp = rider_client.get("/api/v1/ai/config")
        body = resp.json()
        assert body["enabled"] is False and body["mode"] == "coming_soon"

    def test_disabled_hidden_mode_passed_through(self, rider_client):
        settings = {"ai_assistant_enabled": False, "ai_disabled_mode": "hidden"}
        with patch("backend.routes.ai.get_app_settings", AsyncMock(return_value=settings)):
            resp = rider_client.get("/api/v1/ai/config")
        assert resp.json()["mode"] == "hidden"

    def test_config_requires_auth(self, test_client):
        assert test_client.get("/api/v1/ai/config").status_code in (401, 403)


class TestConversations:
    def test_list_scoped_to_caller(self, rider_client):
        listing = AsyncMock(return_value=[{"id": "conv-1", "title": "t", "updated_at": "u"}])
        with patch("backend.routes.ai.conversations.list_conversations", listing):
            resp = rider_client.get("/api/v1/ai/conversations")
        assert resp.status_code == 200
        listing.assert_awaited_once_with("rider-1")

    def test_messages_404_when_not_owner(self, rider_client):
        with patch("backend.routes.ai.conversations.get_messages", AsyncMock(return_value=None)):
            resp = rider_client.get("/api/v1/ai/conversations/foreign-conv/messages")
        assert resp.status_code == 404

    def test_messages_ok(self, rider_client):
        msgs = [{"id": "m1", "role": "user", "content": "hi", "created_at": "t"}]
        with patch("backend.routes.ai.conversations.get_messages", AsyncMock(return_value=msgs)):
            resp = rider_client.get("/api/v1/ai/conversations/conv-1/messages")
        assert resp.json() == {"messages": msgs}

    def test_delete_204_and_404(self, rider_client):
        with patch("backend.routes.ai.conversations.delete_conversation", AsyncMock(return_value=True)):
            assert rider_client.delete("/api/v1/ai/conversations/conv-1").status_code == 204
        with patch("backend.routes.ai.conversations.delete_conversation", AsyncMock(return_value=False)):
            assert rider_client.delete("/api/v1/ai/conversations/conv-1").status_code == 404
