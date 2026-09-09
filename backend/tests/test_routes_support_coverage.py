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
    # F04 removed scrub_pii from routes.support along with the direct Gemini
    # call on /support/chat -- but /support/escalate (a separate endpoint,
    # untouched by F04) sends straight to Zoho Desk with no AI path involved
    # at all, so "scrubbing moved to the central engine" never applied to it.
    # scrub_pii was restored here specifically for that egress boundary --
    # see support_escalate's own docstring and ScrubPolicy.STRICT's list of
    # protected boundaries in ai/pii.py, which already named routes/support.py.
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


# ── support_chat (retired stub) ─────────────────────────────────────────
#
# F04 (PR #5138). This endpoint used to call Gemini directly and sat outside
# every AI control: no ai_assistant_enabled switch, no provider factory, no
# daily quota, no input length bound, and a synchronous generate_content
# inside an async route.
#
# It was first rewritten to delegate to ai.orchestrator.run_chat_turn. Review
# caught that this quietly WIDENED it: the pre-F04 route was a prompt-only FAQ
# bot, and delegating handed a legacy client the full authenticated tool set
# (propose_ride_booking, escalate_to_support — real Zoho tickets) whose action
# frames this response shape cannot carry, plus cross-user FAQ-cache
# eligibility on every call because conversation_id was always None.
#
# It is now a stub that makes no provider call at all. The Gemini-SDK tests
# that used to live here are deleted rather than ported: none of that code
# path exists.


class TestSupportChatIsARetiredStub:
    @pytest.mark.anyio
    async def test_returns_the_fallback_without_calling_any_provider(self):
        from backend.routes.support import FALLBACK_REPLY, ChatRequest, support_chat

        result = await support_chat(
            ChatRequest(message="How do I get paid?"),
            request=MagicMock(),
            current_user=dict(_DRIVER_USER, is_driver=True),
        )
        assert result == {"reply": FALLBACK_REPLY}

    def test_module_reaches_for_no_ai_engine_at_all(self):
        """The strongest form of "the bypass is closed": there is no provider
        call on this path to bypass a control with. Pins the absence of both
        the deprecated SDK and the central engine."""
        import inspect

        from backend.routes import support as support_mod

        src = inspect.getsource(support_mod)
        assert "google.generativeai" not in src
        assert "run_chat_turn" not in src

    def test_message_is_still_length_bounded(self):
        """Kept from the delegating version: the legacy field had no
        max_length, so an arbitrarily long body was accepted."""
        import pydantic

        from backend.routes.support import ChatRequest

        with pytest.raises(pydantic.ValidationError):
            ChatRequest(message="x" * 1001)
        with pytest.raises(pydantic.ValidationError):
            ChatRequest(message="")

    @pytest.mark.anyio
    async def test_driver_id_is_accepted_and_ignored(self):
        """It has never selected an identity here and must not start to."""
        from backend.routes.support import FALLBACK_REPLY, ChatRequest, support_chat

        result = await support_chat(
            ChatRequest(message="hi", driver_id="someone-elses-driver-id"),
            request=MagicMock(),
            current_user=dict(_RIDER),
        )
        assert result == {"reply": FALLBACK_REPLY}


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
    async def test_pii_in_message_and_transcript_is_scrubbed_before_reaching_zoho(self):
        """The actual gap this fix closes: a rider's own phone number or
        email typed into a support message/transcript must never reach Zoho
        Desk (a third party) verbatim."""
        from backend.routes.support import EscalateRequest, support_escalate

        create_ticket = AsyncMock(return_value={"ticketNumber": "TCK-99"})
        patches = _start(_patches(**{"backend.routes.support.create_support_ticket": create_ticket}))
        try:
            await support_escalate(
                EscalateRequest(
                    message="Call me at 306-555-1234, my email is rider@example.com",
                    transcript="transcript line 1\nuser phone: 3065551234",
                ),
                current_user=_RIDER,
            )
            create_ticket.assert_awaited_once_with(
                user=_RIDER,
                message="Call me at [PHONE], my email is [EMAIL]",
                transcript="transcript line 1\nuser phone: [PHONE]",
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
