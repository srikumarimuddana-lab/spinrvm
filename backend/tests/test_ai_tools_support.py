"""FAQ / company / escalation tools.

Pins: audience filtering on FAQ search, the deep-link-only default for
escalation (Zoho ticket only behind the settings flag, and a Zoho outage
never strands the user), and the 911/SOS language on safety escalations.
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
            result, ok = await execute_tool(
                "search_faqs", {"query": "can I schedule a ride for tomorrow"}, user=RIDER
            )
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
    async def test_no_match_returns_empty(self):
        with patch.object(tools_support.db_supabase, "get_rows", AsyncMock(return_value=FAQS)):
            result, ok = await execute_tool("search_faqs", {"query": "zzzqqq"}, user=RIDER)
        assert ok and result["results"] == []


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
        with _settings(ai_escalation_creates_ticket=True), patch(
            "backend.services.zoho_desk_integration.create_support_ticket", ticket
        ):
            result, ok = await execute_tool(
                "escalate_to_support",
                {"reason": "billing dispute", "category": "payment_issue"},
                user=RIDER,
            )
        assert ok and result["ticket_number"] == "T-123"
        ticket.assert_awaited_once()

    @pytest.mark.anyio
    async def test_zoho_outage_still_returns_deep_link(self):
        ticket = AsyncMock(side_effect=RuntimeError("zoho down"))
        with _settings(ai_escalation_creates_ticket=True), patch(
            "backend.services.zoho_desk_integration.create_support_ticket", ticket
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
