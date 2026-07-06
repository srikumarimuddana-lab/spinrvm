"""Driver self-service tools: application status, documents/CRC, earnings.

All scoped to the authenticated driver (resolved from user_id, never an arg);
whitelisted fields only. Driver-audience only.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.ai import tools_driver
from backend.ai.tools import execute_tool, tool_defs_for

DRIVER = {"id": "user-d1"}
DRIVER_ROW = {"id": "drv-1", "user_id": "user-d1", "status": "active", "is_verified": True}


def _patch_driver(row):
    return patch.object(tools_driver.db_supabase, "get_driver_by_user_id_cached", AsyncMock(return_value=row))


class TestApplicationStatus:
    @pytest.mark.anyio
    async def test_no_application_on_file(self):
        with _patch_driver(None):
            result, ok = await execute_tool("get_driver_application_status", {}, user=DRIVER, audience="driver")
        assert ok and result["status"] == "not_started"

    @pytest.mark.anyio
    async def test_active_driver_can_go_online(self):
        with _patch_driver(DRIVER_ROW):
            result, ok = await execute_tool("get_driver_application_status", {}, user=DRIVER, audience="driver")
        assert ok and result["status"] == "active" and result["can_go_online"] is True

    @pytest.mark.anyio
    async def test_pending_has_review_summary(self):
        with _patch_driver({**DRIVER_ROW, "status": "pending", "is_verified": False}):
            result, ok = await execute_tool("get_driver_application_status", {}, user=DRIVER, audience="driver")
        assert result["can_go_online"] is False
        assert "review" in result["summary"].lower()

    @pytest.mark.anyio
    async def test_expired_crc_is_flagged(self):
        row = {**DRIVER_ROW, "background_check_expiry_date": "2020-01-01T00:00:00Z"}
        with _patch_driver(row):
            result, ok = await execute_tool("get_driver_application_status", {}, user=DRIVER, audience="driver")
        flags = result["expiring_or_expired_documents"]
        assert any(f["document"] == "Criminal Record Check" and f["state"] == "expired" for f in flags)


class TestDocumentStatus:
    @pytest.mark.anyio
    async def test_maps_docs_and_hides_notes_when_approved(self):
        docs = [
            {"document_type": "passport", "status": "approved", "uploaded_at": "2026-06-01T10:00:00Z",
             "reviewer_notes": "internal note"},
            {"document_type": "criminal_record_check", "status": "rejected",
             "uploaded_at": "2026-06-02T10:00:00Z", "reviewer_notes": "CRC expired, upload a recent one"},
        ]
        with _patch_driver(DRIVER_ROW), patch.object(
            tools_driver.db_supabase, "get_rows", AsyncMock(return_value=docs)
        ):
            result, ok = await execute_tool("get_document_status", {}, user=DRIVER, audience="driver")
        assert ok
        by_type = {d["document_type"]: d for d in result["documents"]}
        assert "action_needed" not in by_type["passport"]  # approved → no note leaked
        assert by_type["criminal_record_check"]["action_needed"].startswith("CRC expired")

    @pytest.mark.anyio
    async def test_never_returns_file_url(self):
        docs = [{"document_type": "license_front", "status": "approved", "file_url": "https://secret/doc.jpg",
                 "uploaded_at": "2026-06-01T10:00:00Z"}]
        with _patch_driver(DRIVER_ROW), patch.object(
            tools_driver.db_supabase, "get_rows", AsyncMock(return_value=docs)
        ):
            result, ok = await execute_tool("get_document_status", {}, user=DRIVER, audience="driver")
        assert "secret" not in str(result)


class TestEarnings:
    @pytest.mark.anyio
    async def test_sums_earnings_as_string(self):
        rides = [
            {"driver_earnings": "12.50", "payment_status": "paid", "ride_completed_at": "2026-06-01T10:00:00Z"},
            {"driver_earnings": "7.25", "payment_status": "paid", "ride_completed_at": "2026-06-02T10:00:00Z"},
        ]
        with _patch_driver(DRIVER_ROW), patch.object(
            tools_driver.db_supabase, "get_rows", AsyncMock(return_value=rides)
        ):
            result, ok = await execute_tool("get_driver_earnings_summary", {}, user=DRIVER, audience="driver")
        assert ok and result["recent_trip_count"] == 2
        assert result["recent_earnings_total"] == "19.75"  # Decimal sum, no float drift


class TestAudienceScoping:
    def test_driver_tools_are_driver_only(self):
        driver = {d["name"] for d in tool_defs_for("driver")}
        rider = {d["name"] for d in tool_defs_for("rider")}
        names = {"get_driver_application_status", "get_document_status", "get_driver_earnings_summary"}
        assert names <= driver
        assert not (names & rider)

    @pytest.mark.anyio
    async def test_rider_cannot_call_driver_tool(self):
        result, ok = await execute_tool("get_driver_application_status", {}, user={"id": "rider-x"}, audience="rider")
        assert not ok and "not available" in result["error"]
