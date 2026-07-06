"""FAQ / company / escalation tools.

Pins: audience filtering on FAQ search (question matches outrank answer
matches), the deep-link-only default for escalation (Zoho ticket only
behind the settings flag, and a Zoho outage never strands the user), the
open_support _client_action card contract, the conversation transcript on
tickets, and the 911/SOS language on safety escalations.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import tools_support
from backend.ai.tools import execute_tool, tool_defs_for

RIDER = {"id": "rider-1"}

FAQS = [
    {
        "question": "Can I schedule a ride in advance?",
        "answer": "Yes — choose Schedule when booking.",
        "category": "rides",
        "audience": "rider",
        "is_active": True,
    },
    {
        "question": "What is the cancellation fee?",
        "answer": "A fee applies after a driver accepts.",
        "category": "fees",
        "audience": "both",
        "is_active": True,
    },
]


def _settings(**overrides):
    base = {
        "ai_escalation_creates_ticket": False,
        "company_name": "Spinr",
        "company_phone": "1-800-SPINR",
        "company_email": "support@spinr.ca",
        "company_address": "",
        "company_website": "",
    }
    base.update(overrides)
    return patch.object(tools_support, "get_app_settings", AsyncMock(return_value=base))


class TestSearchFaqs:
    @pytest.mark.anyio
    async def test_keyword_match_and_shape(self):
        get_rows = AsyncMock(return_value=FAQS)
        with patch.object(tools_support.db_supabase, "get_rows", get_rows):
            result, ok = await execute_tool("search_faqs", {"query": "can I schedule a ride for tomorrow"}, user=RIDER)
        assert ok
        assert result["results"][0]["question"].startswith("Can I schedule")
        # audience filter is server-decided and sent to the DB query
        assert get_rows.await_args.args[1]["audience"] == {"$in": ["both", "rider"]}

    @pytest.mark.anyio
    async def test_driver_audience_passed_through(self):
        get_rows = AsyncMock(return_value=[])
        with patch.object(tools_support.db_supabase, "get_rows", get_rows):
            await execute_tool("search_faqs", {"query": "payout"}, user=RIDER, audience="driver")
        assert get_rows.await_args.args[1]["audience"] == {"$in": ["both", "driver"]}

    @pytest.mark.anyio
    async def test_no_match_returns_empty_with_note(self):
        with patch.object(tools_support.db_supabase, "get_rows", AsyncMock(return_value=FAQS)):
            result, ok = await execute_tool("search_faqs", {"query": "zzzqqq"}, user=RIDER)
        assert ok and result["results"] == []
        assert "No matching" in result["note"]

    @pytest.mark.anyio
    async def test_question_match_outranks_answer_match(self):
        faqs = [
            {
                # Query tokens appear only in the ANSWER (3 hits, weight 1 → 3)
                "question": "How do refunds work?",
                "answer": "You can schedule advance ride pickups from the booking screen.",
                "category": "rides",
                "audience": "both",
                "is_active": True,
            },
            {
                # Query tokens appear in the QUESTION (2 hits, weight 2 → 4)
                "question": "Can I schedule a ride?",
                "answer": "Yes.",
                "category": "rides",
                "audience": "both",
                "is_active": True,
            },
        ]
        with patch.object(tools_support.db_supabase, "get_rows", AsyncMock(return_value=faqs)):
            result, ok = await execute_tool("search_faqs", {"query": "schedule advance ride"}, user=RIDER)
        assert ok
        assert result["results"][0]["question"] == "Can I schedule a ride?"

    @pytest.mark.anyio
    async def test_synonym_reword_matches_via_concepts(self):
        """A query sharing no literal word with the FAQ still matches when the
        words are domain synonyms (earnings ↔ payouts)."""
        faqs = [
            {
                "question": "When are payouts made?",
                "answer": "Deposits arrive weekly to your bank.",
                "category": "money",
                "audience": "both",
                "is_active": True,
            }
        ]
        with patch.object(tools_support.db_supabase, "get_rows", AsyncMock(return_value=faqs)):
            result, ok = await execute_tool("search_faqs", {"query": "when do I get my earnings"}, user=RIDER)
        assert ok
        assert result["results"] and result["results"][0]["question"] == "When are payouts made?"

    @pytest.mark.anyio
    async def test_plural_fallback_matches(self):
        """Trailing-'s' plurals fold onto the concept (cancellations ↔
        cancellation)."""
        with patch.object(tools_support.db_supabase, "get_rows", AsyncMock(return_value=FAQS)):
            result, ok = await execute_tool("search_faqs", {"query": "how do cancellations work"}, user=RIDER)
        assert ok
        assert result["results"] and result["results"][0]["question"] == "What is the cancellation fee?"

    def test_unrelated_terms_do_not_share_a_concept(self):
        # guardrail: distinct topics must not collapse into one concept token
        assert tools_support._match_tokens("surge") & tools_support._match_tokens("refund") == set()


class TestCompanyInfo:
    @pytest.mark.anyio
    async def test_company_info(self):
        with _settings():
            result, ok = await execute_tool("get_company_info", {}, user=RIDER)
        assert ok and result["phone"] == "1-800-SPINR"


class TestEscalation:
    @pytest.mark.anyio
    async def test_default_is_deep_link_only(self):
        ticket = AsyncMock()
        with _settings(), patch.object(tools_support, "create_support_ticket", ticket, create=True):
            result, ok = await execute_tool(
                "escalate_to_support",
                {"reason": "wants a refund for ride", "category": "refund"},
                user=RIDER,
            )
        assert ok
        assert result["action"] == "open_support"
        assert result["link"] == "/support"
        # The card contract: orchestrator lifts this into an SSE action frame
        # so the apps/console render the "Contact support" card.
        assert result["_client_action"] == {"type": "open_support", "category": "refund", "link": "/support"}
        assert "ticket_number" not in result
        ticket.assert_not_awaited()

    @pytest.mark.anyio
    async def test_lost_item_links_to_lost_and_found(self):
        with _settings():
            result, _ = await execute_tool(
                "escalate_to_support",
                {"reason": "left phone in car", "category": "lost_item"},
                user=RIDER,
            )
        assert result["link"] == "/lost-and-found"

    @pytest.mark.anyio
    async def test_safety_always_mentions_911(self):
        with _settings():
            result, _ = await execute_tool(
                "escalate_to_support",
                {"reason": "driver behaved unsafely", "category": "safety"},
                user=RIDER,
            )
        assert "911" in result["message"]
        assert "SOS" in result["message"]

    @pytest.mark.anyio
    async def test_flag_enables_zoho_ticket(self):
        ticket = AsyncMock(return_value={"ticketNumber": "T-123"})
        with (
            _settings(ai_escalation_creates_ticket=True),
            patch("backend.services.zoho_desk_integration.create_support_ticket", ticket),
        ):
            result, ok = await execute_tool(
                "escalate_to_support",
                {"reason": "billing dispute", "category": "payment_issue"},
                user=RIDER,
            )
        assert ok and result["ticket_number"] == "T-123"
        ticket.assert_awaited_once()
        # No conversation id on the user → no transcript on the ticket.
        assert ticket.await_args.kwargs["transcript"] is None

    @pytest.mark.anyio
    async def test_ticket_includes_conversation_transcript(self):
        ticket = AsyncMock(return_value={"ticketNumber": "T-124"})
        history = AsyncMock(
            return_value=[
                {"role": "user", "content": "My driver took a wrong turn and I was overcharged"},
                {"role": "assistant", "content": "I can see the fare — let me hand you to support."},
            ]
        )
        with (
            _settings(ai_escalation_creates_ticket=True),
            patch("backend.services.zoho_desk_integration.create_support_ticket", ticket),
            patch.object(tools_support.conversations, "load_history", history),
        ):
            result, ok = await execute_tool(
                "escalate_to_support",
                {"reason": "billing dispute", "category": "payment_issue"},
                user={**RIDER, "_conversation_id": "conv-1"},
            )
        assert ok and result["ticket_number"] == "T-124"
        transcript = ticket.await_args.kwargs["transcript"]
        assert "User: My driver took a wrong turn" in transcript
        assert "Assistant: I can see the fare" in transcript

    @pytest.mark.anyio
    async def test_transcript_failure_never_blocks_ticket(self):
        ticket = AsyncMock(return_value={"ticketNumber": "T-125"})
        with (
            _settings(ai_escalation_creates_ticket=True),
            patch("backend.services.zoho_desk_integration.create_support_ticket", ticket),
            patch.object(tools_support.conversations, "load_history", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            result, ok = await execute_tool(
                "escalate_to_support",
                {"reason": "billing dispute", "category": "payment_issue"},
                user={**RIDER, "_conversation_id": "conv-1"},
            )
        assert ok and result["ticket_number"] == "T-125"
        assert ticket.await_args.kwargs["transcript"] is None

    @pytest.mark.anyio
    async def test_zoho_outage_still_returns_deep_link(self):
        ticket = AsyncMock(side_effect=RuntimeError("zoho down"))
        with (
            _settings(ai_escalation_creates_ticket=True),
            patch("backend.services.zoho_desk_integration.create_support_ticket", ticket),
        ):
            result, ok = await execute_tool(
                "escalate_to_support",
                {"reason": "billing dispute", "category": "payment_issue"},
                user=RIDER,
            )
        assert ok
        assert result["action"] == "open_support"
        assert "ticket_number" not in result

    @pytest.mark.anyio
    async def test_bad_category_rejected_by_schema(self):
        result, ok = await execute_tool(
            "escalate_to_support", {"reason": "x", "category": "book_me_a_ride"}, user=RIDER
        )
        assert ok is False


def test_support_tools_serve_both_audiences():
    rider = {d["name"] for d in tool_defs_for("rider")}
    driver = {d["name"] for d in tool_defs_for("driver")}
    expected = {"search_faqs", "get_company_info", "escalate_to_support"}
    assert expected <= rider
    assert expected <= driver
    # Ride-data tools stay rider-only.
    assert "get_active_ride" not in driver
