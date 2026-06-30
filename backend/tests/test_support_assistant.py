"""
Unit tests for the Zoho Desk AI reply assistant (ai/support_assistant.py).

Verifies: disabled -> SupportAssistantError; PII is scrubbed out of the prompt
before it reaches the provider; a normal turn returns the assembled draft.
"""

from __future__ import annotations

import pytest

import ai.support_assistant as sa
from ai.providers.base import StreamEvent

pytestmark = pytest.mark.anyio


class _FakeAdapter:
    provider = "anthropic"
    model = "claude-haiku-4-5"

    def __init__(self):
        self.captured = None

    async def stream_turn(self, *, system, messages, tools):
        self.captured = {"system": system, "messages": messages, "tools": tools}
        yield StreamEvent(type="text", text="Hi Sam, ")
        yield StreamEvent(type="text", text="thanks for reaching out.")
        yield StreamEvent(type="turn_end", stop_reason="end_turn", usage={})


async def test_disabled_raises(monkeypatch):
    with pytest.raises(sa.SupportAssistantError) as ei:
        await sa.suggest_ticket_reply(ticket={"subject": "x"}, settings={"ai_assistant_enabled": False})
    assert ei.value.code == "ai_disabled"


async def test_returns_draft_and_scrubs_pii(monkeypatch):
    fake = _FakeAdapter()

    async def _get_adapter():
        return fake

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)

    ticket = {
        "subject": "Refund to rider@example.com please",
        "description": "Email me at rider@example.com or call 306-555-1234",
        "contact": {"firstName": "Sam", "email": "rider@example.com"},
        "category": "Payment",
    }
    thread = [
        {"type": "thread", "direction": "in", "content": "My card 306-555-9999 was charged twice"},
        {"type": "comment", "content": "internal: check Stripe"},
    ]

    out = await sa.suggest_ticket_reply(
        ticket=ticket,
        thread=thread,
        service_area_name="Regina",
        settings={"ai_assistant_enabled": True, "company_email": "support@spinr.ca"},
    )

    assert out["reply"] == "Hi Sam, thanks for reaching out."
    assert out["provider"] == "anthropic"

    # The single user message must carry no raw email/phone — PII is redacted,
    # including in the subject line (not just the body/thread).
    user_msg = fake.captured["messages"][0]["content"]
    assert "rider@example.com" not in user_msg
    assert "306-555-1234" not in user_msg
    assert "306-555-9999" not in user_msg
    assert "[EMAIL]" in user_msg and "[PHONE]" in user_msg
    subject_line = next(ln for ln in user_msg.splitlines() if ln.startswith("Subject:"))
    assert "rider@example.com" not in subject_line and "[EMAIL]" in subject_line
    # The customer's name is intentionally withheld from the LLM (can't be
    # regex-scrubbed), but non-PII context the model may use is present.
    assert "Sam" not in user_msg
    assert "Regina" in user_msg
    # No tools are offered for a one-shot draft.
    assert fake.captured["tools"] == []


async def test_instruction_steers_draft_and_full_thread_body_used(monkeypatch):
    """The agent's guidance reaches the prompt verbatim, and the conversation
    uses each thread's full body (not a truncated snippet)."""
    fake = _FakeAdapter()

    async def _get_adapter():
        return fake

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)

    long_body = "The rider explains in detail. " * 80  # > the old 1500-char clamp
    out = await sa.suggest_ticket_reply(
        ticket={"subject": "Help", "description": "see thread"},
        thread=[{"type": "thread", "direction": "in", "content": long_body}],
        instruction="Confirm the $25 refund is approved and ask for the trip date.",
        settings={"ai_assistant_enabled": True},
    )
    assert out["reply"] == "Hi Sam, thanks for reaching out."

    user_msg = fake.captured["messages"][0]["content"]
    # Agent guidance is first-party input — passed through unscrubbed (amounts intact).
    assert "Confirm the $25 refund is approved" in user_msg
    # The full mail body is present, not clipped to the old snippet length.
    assert long_body.strip() in user_msg


async def test_conversation_presented_newest_first(monkeypatch):
    """The thread (passed oldest->newest) is presented to the model newest-first,
    with the latest message flagged as the one to reply to."""
    fake = _FakeAdapter()

    async def _get_adapter():
        return fake

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)
    await sa.suggest_ticket_reply(
        ticket={"subject": "Help", "description": "ORIGINAL ticket request body"},
        thread=[
            {"type": "thread", "direction": "in", "content": "OLDEST customer message"},
            {"type": "thread", "direction": "out", "content": "MIDDLE agent reply"},
            {"type": "thread", "direction": "in", "content": "NEWEST customer message"},
        ],
        settings={"ai_assistant_enabled": True},
    )
    user_msg = fake.captured["messages"][0]["content"]
    pos_new = user_msg.index("NEWEST customer message")
    pos_mid = user_msg.index("MIDDLE agent reply")
    pos_old = user_msg.index("OLDEST customer message")
    pos_desc = user_msg.index("ORIGINAL ticket request body")
    # Newest appears before middle, which appears before oldest.
    assert pos_new < pos_mid < pos_old
    # The original ticket request is bottom-anchored: below the whole chain.
    assert pos_desc > pos_old
    # The latest message is explicitly flagged for the model.
    assert "(latest — reply to this)" in user_msg
    assert user_msg.index("(latest — reply to this)") < pos_new + len("NEWEST customer message")


async def test_no_thread_treats_description_as_reply_target(monkeypatch):
    """With no replies yet, the original request is framed as the message to
    reply to (not buried as background context)."""
    fake = _FakeAdapter()

    async def _get_adapter():
        return fake

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)
    await sa.suggest_ticket_reply(
        ticket={"subject": "Help", "description": "Please help me"},
        settings={"ai_assistant_enabled": True},
    )
    user_msg = fake.captured["messages"][0]["content"]
    assert "this is the message you are replying to" in user_msg
    assert "Please help me" in user_msg


async def test_no_instruction_omits_guidance_block(monkeypatch):
    fake = _FakeAdapter()

    async def _get_adapter():
        return fake

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)
    await sa.suggest_ticket_reply(ticket={"subject": "Help"}, settings={"ai_assistant_enabled": True})
    user_msg = fake.captured["messages"][0]["content"]
    assert "support agent handling this ticket gave you specific guidance" not in user_msg


async def test_misconfigured_adapter_raises(monkeypatch):
    from ai.providers.base import AIConfigError

    async def _get_adapter():
        raise AIConfigError("anthropic", "no API key configured")

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)
    with pytest.raises(sa.SupportAssistantError) as ei:
        await sa.suggest_ticket_reply(ticket={"subject": "x"}, settings={"ai_assistant_enabled": True})
    assert ei.value.code == "ai_misconfigured"


async def test_provider_error_midstream_raises(monkeypatch):
    class _Boom(_FakeAdapter):
        async def stream_turn(self, *, system, messages, tools):
            yield StreamEvent(type="text", text="partial")
            raise RuntimeError("upstream 529 overloaded")

    async def _get_adapter():
        return _Boom()

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)
    with pytest.raises(sa.SupportAssistantError) as ei:
        await sa.suggest_ticket_reply(ticket={"subject": "x"}, settings={"ai_assistant_enabled": True})
    assert ei.value.code == "provider_error"


async def test_empty_generation_raises(monkeypatch):
    class _Empty(_FakeAdapter):
        async def stream_turn(self, *, system, messages, tools):
            yield StreamEvent(type="turn_end", stop_reason="end_turn", usage={})

    async def _get_adapter():
        return _Empty()

    monkeypatch.setattr(sa, "get_adapter", _get_adapter)
    with pytest.raises(sa.SupportAssistantError) as ei:
        await sa.suggest_ticket_reply(ticket={"subject": "x"}, settings={"ai_assistant_enabled": True})
    assert ei.value.code == "empty"
