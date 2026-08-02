"""Coverage for routes/support.py (A1c, Sub-tier B).

Rider/driver-facing AI support chat endpoint (Gemini 1.5 Flash) plus the
human-escalation-to-Zoho-Desk endpoint. Distinct from routes/admin/support.py
and routes/admin/support_tickets.py (already covered elsewhere) — this file
tests only routes/support.py. Had no dedicated test file; 42.22% coverage.

Endpoint functions are called directly (bypassing FastAPI's Depends
machinery), matching the pattern used elsewhere in this repo for
handler-level unit tests (see test_lost_and_found_route_coverage.py).

Gemini SDK notes: `google.generativeai` is imported *locally* inside
`support_chat` (`import google.generativeai as genai`), so it cannot be
patched via a `backend.routes.support.<name>` string target — the module
attribute doesn't exist on `backend.routes.support`. Instead we patch the
real `google.generativeai.configure` / `google.generativeai.GenerativeModel`
attributes directly (the package is a real dependency per requirements.txt),
which the local `import` picks up since Python module imports are cached
singletons.

Bug found, not fixed (test-only scope): `support_chat`'s `except Exception`
handler catches *everything*, including a missing/misconfigured Gemini SDK,
network errors, and bad API keys, and always converts them into a 200 OK
`{"reply": FALLBACK_REPLY}` response. Per CLAUDE.md's "never silently
swallow errors" convention this masks real outages (e.g. GEMINI_API_KEY
misconfigured, quota exhausted) as ordinary "I don't know" chat replies —
there is no Sentry capture, error-level log, or metric distinguishing "no
key configured" from "Gemini API failure" from "malformed response"; only
a `logging.warning(...)` with no `domain`/`surface` tags. Not modified here
(test-only scope) — flagging for the route owner.

Test-only change — no application code modified.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.unit

_RIDER = {"id": "rider-1", "email": "rider1@example.com"}
_DRIVER_USER = {"id": "driver-user-1", "email": "driver1@example.com"}


def _patches(**overrides):
    defaults = {
        "backend.routes.support.scrub_pii": MagicMock(side_effect=lambda msg: msg),
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


# ── support_chat ────────────────────────────────────────────────────────


class TestSupportChat:
    @pytest.mark.anyio
    async def test_no_api_key_returns_fallback(self, monkeypatch):
        from backend.routes.support import ChatRequest, FALLBACK_REPLY, support_chat

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        patches = _start(_patches())
        try:
            result = await support_chat(ChatRequest(message="How do I get paid?"), user_id="driver-user-1")
            assert result == {"reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_google_api_key_env_var_also_accepted(self, monkeypatch):
        """GOOGLE_API_KEY is the secondary env-var name accepted for the key."""
        from backend.routes.support import ChatRequest, support_chat

        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")

        fake_response = MagicMock()
        fake_response.text = "You get paid via Stripe payouts."
        fake_model_instance = MagicMock()
        fake_model_instance.generate_content.return_value = fake_response

        patches = _start(
            _patches(
                **{
                    "google.generativeai.configure": MagicMock(),
                    "google.generativeai.GenerativeModel": MagicMock(return_value=fake_model_instance),
                }
            )
        )
        try:
            result = await support_chat(ChatRequest(message="When do I get paid?"), user_id="driver-user-1")
            assert result == {"reply": "You get paid via Stripe payouts."}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_happy_path_scrubs_pii_and_returns_stripped_reply(self, monkeypatch):
        from backend.routes.support import ChatRequest, support_chat

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        fake_response = MagicMock()
        fake_response.text = "  Call 1-800-SPINR for help.  \n"
        fake_model_instance = MagicMock()
        fake_model_instance.generate_content.return_value = fake_response
        fake_model_cls = MagicMock(return_value=fake_model_instance)
        scrub = MagicMock(return_value="my phone is [REDACTED]")

        patches = _start(
            _patches(
                **{
                    "backend.routes.support.scrub_pii": scrub,
                    "google.generativeai.configure": MagicMock(),
                    "google.generativeai.GenerativeModel": fake_model_cls,
                }
            )
        )
        try:
            result = await support_chat(
                ChatRequest(message="my phone is 306-555-1234", driver_id="driver-1"), user_id="driver-user-1"
            )
            # Reply is stripped of surrounding whitespace.
            assert result == {"reply": "Call 1-800-SPINR for help."}
            # PII was scrubbed BEFORE being sent to Gemini (PIPEDA / DV-16).
            scrub.assert_called_once_with("my phone is 306-555-1234")
            fake_model_instance.generate_content.assert_called_once_with("my phone is [REDACTED]")
            fake_model_cls.assert_called_once()
            _, kwargs = fake_model_cls.call_args
            assert kwargs["model_name"] == "gemini-1.5-flash"
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_empty_response_text_returns_fallback(self, monkeypatch):
        from backend.routes.support import ChatRequest, FALLBACK_REPLY, support_chat

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        fake_response = MagicMock()
        fake_response.text = ""
        fake_model_instance = MagicMock()
        fake_model_instance.generate_content.return_value = fake_response

        patches = _start(
            _patches(
                **{
                    "google.generativeai.configure": MagicMock(),
                    "google.generativeai.GenerativeModel": MagicMock(return_value=fake_model_instance),
                }
            )
        )
        try:
            result = await support_chat(ChatRequest(message="hi"), user_id="driver-user-1")
            assert result == {"reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_none_response_text_returns_fallback(self, monkeypatch):
        from backend.routes.support import ChatRequest, FALLBACK_REPLY, support_chat

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        fake_response = MagicMock()
        fake_response.text = None
        fake_model_instance = MagicMock()
        fake_model_instance.generate_content.return_value = fake_response

        patches = _start(
            _patches(
                **{
                    "google.generativeai.configure": MagicMock(),
                    "google.generativeai.GenerativeModel": MagicMock(return_value=fake_model_instance),
                }
            )
        )
        try:
            result = await support_chat(ChatRequest(message="hi"), user_id="driver-user-1")
            assert result == {"reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_gemini_configure_raises_returns_fallback_not_500(self, monkeypatch):
        """Any exception from the Gemini SDK path (bad key, quota, network)
        is caught and converted into a 200 fallback reply rather than
        propagating — see module docstring for the swallowed-error note."""
        from backend.routes.support import ChatRequest, FALLBACK_REPLY, support_chat

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        patches = _start(
            _patches(
                **{
                    "google.generativeai.configure": MagicMock(side_effect=RuntimeError("upstream down")),
                }
            )
        )
        try:
            result = await support_chat(ChatRequest(message="hi"), user_id="driver-user-1")
            assert result == {"reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_generate_content_raises_returns_fallback(self, monkeypatch):
        from backend.routes.support import ChatRequest, FALLBACK_REPLY, support_chat

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        fake_model_instance = MagicMock()
        fake_model_instance.generate_content.side_effect = Exception("Gemini API error")

        patches = _start(
            _patches(
                **{
                    "google.generativeai.configure": MagicMock(),
                    "google.generativeai.GenerativeModel": MagicMock(return_value=fake_model_instance),
                }
            )
        )
        try:
            result = await support_chat(ChatRequest(message="hi"), user_id="driver-user-1")
            assert result == {"reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_scrub_pii_raising_is_also_caught_by_fallback(self, monkeypatch):
        """scrub_pii runs inside the same try/except as the Gemini call, so
        a scrubber bug does not leak an unhandled 500 to the client."""
        from backend.routes.support import ChatRequest, FALLBACK_REPLY, support_chat

        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        patches = _start(
            _patches(
                **{
                    "backend.routes.support.scrub_pii": MagicMock(side_effect=ValueError("scrub failed")),
                    "google.generativeai.configure": MagicMock(),
                    "google.generativeai.GenerativeModel": MagicMock(),
                }
            )
        )
        try:
            result = await support_chat(ChatRequest(message="hi"), user_id="driver-user-1")
            assert result == {"reply": FALLBACK_REPLY}
        finally:
            _stop(patches)

    @pytest.mark.anyio
    async def test_default_driver_id_is_empty_string(self):
        from backend.routes.support import ChatRequest

        req = ChatRequest(message="hi")
        assert req.driver_id == ""


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
            create_ticket.assert_awaited_once_with(user=_RIDER, message="I need help", transcript="user: hi\nbot: hello")
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
        from backend.routes.support import EscalateRequest, FALLBACK_REPLY, support_escalate
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
