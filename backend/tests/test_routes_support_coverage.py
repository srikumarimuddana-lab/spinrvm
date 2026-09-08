"""Coverage for routes/support.py (A1c, Sub-tier B).

The legacy support-chat endpoint plus the human-escalation-to-Zoho-Desk
endpoint. Distinct from routes/admin/support.py and
routes/admin/support_tickets.py (already covered elsewhere) — this file tests
only routes/support.py.

Endpoint functions are called directly (bypassing FastAPI's Depends
machinery), matching the pattern used elsewhere in this repo for
handler-level unit tests (see test_lost_and_found_route_coverage.py).

**2026-09-08 (F04):** /support/chat no longer calls Gemini directly. It
delegates to ai.orchestrator.run_chat_turn — the same engine /api/v1/ai/chat
uses — so the global ai_assistant_enabled switch, the provider factory, the
per-user daily quota, the input length bound and async provider handling all
apply to it for the first time. The Gemini-SDK patching notes that used to
live here are gone with the code they described.

The blanket `except Exception` -> 200 `{"reply": FALLBACK_REPLY}` design is
retained deliberately: an older installed client has no handler for a
structured error body, and this endpoint must never strand a driver
mid-conversation with a 500. The failure still surfaces at error level with
domain/surface tags (CLAUDE.md Observability Conventions). Still no Sentry
capture or a dedicated metric here — a real, still-open gap.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

_RIDER = {"id": "rider-1", "email": "rider1@example.com"}
_DRIVER_USER = {"id": "driver-user-1", "email": "driver1@example.com"}


def _patches(**overrides):
    # scrub_pii is no longer a name on routes.support (F04): scrubbing moved
    # inside the central engine along with the rest of the AI path.
    defaults = {
        "backend.routes.support.create_support_ticket": AsyncMock(return_value={"ticketNumber": "TCK-1"}),
    }
    defaults.update(overrides)
    return [patch(target, value) for target, value in defaults.items()]


def _start(patches):
    for p in patches:
        p.start()
    return patches


def _stop(patches):
    for p in patches:
        p.stop()


# ── support_chat (legacy shim → central AI engine) ──────────────────────
#
# F04 (2026-09-08 AI security assessment). This endpoint used to call Gemini
# directly and sat outside every AI control: no ai_assistant_enabled switch,
# no provider factory, no daily quota, no input length bound, and a
# synchronous generate_content inside an async route. It now delegates to
# ai.orchestrator.run_chat_turn, the same engine /api/v1/ai/chat uses.
#
# The Gemini-specific tests that used to live here (patching
# google.generativeai.configure / GenerativeModel, and the GEMINI_API_KEY /
# GOOGLE_API_KEY env-var branches) are deleted rather than ported: none of
# that code path exists any more, so they would have tested nothing. What
# replaces them is the contract that actually matters now — that the route
# reaches the central engine, keeps its legacy response shape, and never
# lets the caller pick an identity.
#
# The SYSTEM_PROMPT content assertions also moved out, to
# tests/test_ai_prompts_policy.py, pointed at ai/prompts.py — the prompt this
# endpoint now actually uses.


class _FakeTurn:
    """Stands in for run_chat_turn: an async generator of (name, payload)."""

    def __init__(self, frames):
        self.frames = frames
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs

        async def _gen():
            for frame in self.frames:
                yield frame

        return _gen()


class TestSupportChatDelegatesToCentralEngine:
    @pytest.mark.anyio
    async def test_reply_is_assembled_from_token_frames(self):
        from backend.routes.support import ChatRequest, support_chat

        fake = _FakeTurn([("token", {"text": "You keep "}), ("token", {"text": "100% of the fare."})])
        with patch("backend.routes.support.run_chat_turn", fake):
            result = await support_chat(
                ChatRequest(message="How do I get paid?"),
                request=MagicMock(),
                current_user=dict(_DRIVER_USER, is_driver=True),
            )

        assert result == {"reply": "You keep 100% of the fare."}

    @pytest.mark.anyio
    async def test_authenticated_user_is_passed_through_not_the_body(self):
        """driver_id is accepted and ignored. It has never selected an
        identity here and must not start doing so — the turn is scoped to the
        authenticated caller."""
        from backend.routes.support import ChatRequest, support_chat

        fake = _FakeTurn([("token", {"text": "ok"})])
        with patch("backend.routes.support.run_chat_turn", fake):
            await support_chat(
                ChatRequest(message="hi", driver_id="someone-elses-driver-id"),
                request=MagicMock(),
                current_user=dict(_DRIVER_USER, is_driver=True),
            )

        assert fake.kwargs["user"]["id"] == "driver-user-1"
        assert "someone-elses-driver-id" not in str(fake.kwargs)

    @pytest.mark.anyio
    async def test_audience_comes_from_the_user_row(self):
        from backend.routes.support import ChatRequest, support_chat

        fake = _FakeTurn([("token", {"text": "ok"})])
        with patch("backend.routes.support.run_chat_turn", fake):
            await support_chat(
                ChatRequest(message="hi"), request=MagicMock(), current_user=dict(_RIDER, is_driver=False)
            )
        assert fake.kwargs["audience"] == "rider"

        fake2 = _FakeTurn([("token", {"text": "ok"})])
        with patch("backend.routes.support.run_chat_turn", fake2):
            await support_chat(
                ChatRequest(message="hi"),
                request=MagicMock(),
                current_user=dict(_DRIVER_USER, is_driver=True),
            )
        assert fake2.kwargs["audience"] == "driver"

    @pytest.mark.anyio
    async def test_engine_error_frame_falls_back_without_a_500(self):
        """An engine refusal (AI disabled, quota exceeded, provider
        misconfigured) must reach an old client as the legacy fallback string,
        not a structured error body it has no handler for."""
        from backend.routes.support import FALLBACK_REPLY, ChatRequest, support_chat

        fake = _FakeTurn([("error", {"code": "ai_disabled", "message": "disabled"})])
        with patch("backend.routes.support.run_chat_turn", fake):
            result = await support_chat(ChatRequest(message="hi"), request=MagicMock(), current_user=dict(_RIDER))

        assert result == {"reply": FALLBACK_REPLY}

    @pytest.mark.anyio
    async def test_engine_exception_falls_back_and_logs_error(self, caplog):
        """The blanket fallback is deliberate (never strand a driver
        mid-conversation), but CLAUDE.md forbids the failure vanishing — it
        must surface at error level with the domain/surface tags."""
        import logging

        from backend.routes.support import FALLBACK_REPLY, ChatRequest, support_chat

        def _boom(**kwargs):
            raise RuntimeError("provider exploded")

        with patch("backend.routes.support.run_chat_turn", _boom):
            with caplog.at_level(logging.ERROR, logger="backend.routes.support"):
                result = await support_chat(ChatRequest(message="hi"), request=MagicMock(), current_user=dict(_RIDER))

        assert result == {"reply": FALLBACK_REPLY}
        assert any(r.levelno >= logging.ERROR for r in caplog.records)

    @pytest.mark.anyio
    async def test_empty_reply_falls_back(self):
        from backend.routes.support import FALLBACK_REPLY, ChatRequest, support_chat

        fake = _FakeTurn([("token", {"text": "   "})])
        with patch("backend.routes.support.run_chat_turn", fake):
            result = await support_chat(ChatRequest(message="hi"), request=MagicMock(), current_user=dict(_RIDER))
        assert result == {"reply": FALLBACK_REPLY}

    def test_message_is_length_bounded_like_the_central_route(self):
        """The legacy field had no max_length at all, so an arbitrarily long
        body went straight to the provider."""
        import pydantic

        from backend.routes.support import ChatRequest

        with pytest.raises(pydantic.ValidationError):
            ChatRequest(message="x" * 1001)
        with pytest.raises(pydantic.ValidationError):
            ChatRequest(message="")

    def test_route_no_longer_imports_the_deprecated_gemini_sdk(self):
        """The finding named the deprecated google.generativeai SDK on this
        path specifically. Pin that this module no longer reaches for it."""
        import inspect

        from backend.routes import support as support_mod

        assert "google.generativeai" not in inspect.getsource(support_mod)


# ── support_escalate ───────────────────────────────────────────────────


class TestSupportEscalate:
    @pytest.mark.anyio
    async def test_happy_path_returns_ticket_number(self):
        from backend.routes.support import EscalateRequest, support_escalate

        create_ticket = AsyncMock(return_value={"ticketNumber": "TCK-42"})
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            result = await support_escalate(
                EscalateRequest(message="I need help", transcript="user: hi\nbot: hello"),
                current_user=_RIDER,
            )
            assert result == {
                "success": True,
                "ticket_number": "TCK-42",
                "reply": "Your request has been escalated to our support team. We'll follow up by email shortly.",
            }
            create_ticket.assert_awaited_once_with(
                user=_RIDER, message="I need help", transcript="user: hi\nbot: hello"
            )
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_empty_transcript_passed_as_none(self):
        """Empty-string transcript is normalized to None before being
        forwarded to create_support_ticket (`transcript or None`)."""
        from backend.routes.support import EscalateRequest, support_escalate

        create_ticket = AsyncMock(return_value={"ticketNumber": "TCK-1"})
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            await support_escalate(EscalateRequest(message="help"), current_user=_DRIVER_USER)
            create_ticket.assert_awaited_once_with(user=_DRIVER_USER, message="help", transcript=None)
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_missing_ticket_number_key_returns_none(self):
        """result.get("ticketNumber") tolerates a response missing the key
        rather than raising a KeyError."""
        from backend.routes.support import EscalateRequest, support_escalate

        create_ticket = AsyncMock(return_value={})
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            result = await support_escalate(EscalateRequest(message="help"), current_user=_RIDER)
            assert result["success"] is True
            assert result["ticket_number"] is None
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_zoho_desk_error_falls_back_to_support_line(self):
        from backend.routes.support import FALLBACK_REPLY, EscalateRequest, support_escalate
        from backend.services.zoho_desk_service import ZohoDeskError

        create_ticket = AsyncMock(side_effect=ZohoDeskError("Zoho Desk integration is disabled.", status=503))
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            result = await support_escalate(EscalateRequest(message="help"), current_user=_RIDER)
            assert result == {"success": False, "reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_zoho_desk_error_does_not_raise_http_exception(self):
        """Unlike most routes, a ZohoDeskError here is fully absorbed into a
        200 response (`success: False`) rather than surfaced as a 5xx/502 —
        by design, so the user always gets a fallback contact path (see
        module docstring). Confirm no HTTPException escapes."""
        from backend.routes.support import EscalateRequest, support_escalate
        from backend.services.zoho_desk_service import ZohoDeskError

        create_ticket = AsyncMock(side_effect=ZohoDeskError("upstream 500", status=502))
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            try:
                await support_escalate(EscalateRequest(message="help"), current_user=_RIDER)
            except HTTPException:
                pytest.fail("ZohoDeskError should not propagate as HTTPException from support_escalate")
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_default_transcript_is_empty_string(self):
        from backend.routes.support import EscalateRequest

        req = EscalateRequest(message="help")
        assert req.transcript == ""

    @pytest.mark.anyio
    async def test_non_zoho_exception_propagates(self):
        """Only ZohoDeskError is caught; any other exception (e.g. a bug in
        create_support_ticket, a DB error) is NOT swallowed and propagates,
        consistent with CLAUDE.md's "never silently swallow errors" rule."""
        from backend.routes.support import EscalateRequest, support_escalate

        create_ticket = AsyncMock(side_effect=RuntimeError("unexpected"))
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            with pytest.raises(RuntimeError):
                await support_escalate(EscalateRequest(message="help"), current_user=_RIDER)
        finally:
            _stop(patches)
