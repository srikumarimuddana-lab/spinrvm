"""Tests for the A29 tax-rate audit trail on backend/features.py's
``pricing_router`` PUT /areas/{area_id}/tax endpoint (``update_area_tax``).

ACTION_ITEMS.md A29 sub-finding: "No audit trail on the tax-rate admin
endpoints themselves." This mirrors the surge-cap override audit pattern in
routes/admin/service_areas.py's admin_update_service_area — a required
written justification plus an audit_logs row via the shared
log_admin_action helper.

Uses the same direct-function-call + ``patch("backend.features.db_supabase.<fn>",
...)`` pattern already established by test_calculate_all_fees_tax.py and
test_p3_push_notifications.py for this module.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pydantic
import pytest
from fastapi import HTTPException

from backend.features import UpdateTaxConfigRequest, update_area_tax

_ADMIN = {"id": "admin-1", "email": "admin@spinr.ca", "role": "super_admin"}


class TestUpdateAreaTaxAudit:
    @pytest.mark.anyio
    async def test_writes_audit_log_with_old_new_and_justification(self):
        old_row = {
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": False,
            "pst_rate": 0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
        new_row = {
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": True,
            "pst_rate": 6.0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
        update_one = AsyncMock()
        log_admin_action = AsyncMock()
        with (
            patch.multiple(
                "backend.features.db_supabase",
                update_one=update_one,
                get_rows=AsyncMock(side_effect=[[old_row], [new_row]]),
            ),
            patch("backend.features.log_admin_action", log_admin_action),
        ):
            req = UpdateTaxConfigRequest(pst_enabled=True, pst_rate=6.0, justification="Enabling PST per SK tax notice")
            result = await update_area_tax("area-1", req, admin=_ADMIN)

        update_one.assert_awaited_once()
        written_updates = update_one.await_args.args[2]
        # justification is request-only metadata, never a service_areas column.
        assert "justification" not in written_updates
        assert result["pst_enabled"] is True
        assert result["pst_rate"] == 6.0

        log_admin_action.assert_awaited_once()
        call_args = log_admin_action.await_args.args
        assert call_args[0] == _ADMIN
        assert call_args[1] == "tax_rate_changed"
        assert call_args[2] == "service_areas"
        assert call_args[3] == "area-1"
        details = call_args[4]
        assert details["old"]["pst_enabled"] is False
        assert details["new"]["pst_enabled"] is True
        assert details["new"]["pst_rate"] == 6.0
        assert details["justification"] == "Enabling PST per SK tax notice"

    @pytest.mark.anyio
    async def test_empty_field_payload_skips_write_but_still_audits(self):
        row = {
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": False,
            "pst_rate": 0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
        update_one = AsyncMock()
        log_admin_action = AsyncMock()
        with (
            patch.multiple(
                "backend.features.db_supabase",
                update_one=update_one,
                get_rows=AsyncMock(return_value=[row]),
            ),
            patch("backend.features.log_admin_action", log_admin_action),
        ):
            req = UpdateTaxConfigRequest(justification="No-op confirmation, no fields changed")
            result = await update_area_tax("area-1", req, admin=_ADMIN)

        update_one.assert_not_awaited()
        assert result["gst_rate"] == 5.0
        log_admin_action.assert_awaited_once()

    @pytest.mark.anyio
    async def test_missing_justification_field_is_422(self):
        # justification has no default, so a request body without it fails
        # Pydantic validation before the handler runs — FastAPI surfaces this
        # as a 422 at the request layer.
        with pytest.raises(pydantic.ValidationError):
            UpdateTaxConfigRequest(pst_rate=6.0)

    @pytest.mark.anyio
    async def test_blank_justification_is_400_and_writes_nothing(self):
        update_one = AsyncMock()
        log_admin_action = AsyncMock()
        with (
            patch.multiple(
                "backend.features.db_supabase",
                update_one=update_one,
                get_rows=AsyncMock(return_value=[{}]),
            ),
            patch("backend.features.log_admin_action", log_admin_action),
        ):
            req = UpdateTaxConfigRequest(pst_rate=6.0, justification="   ")
            with pytest.raises(HTTPException) as exc_info:
                await update_area_tax("area-1", req, admin=_ADMIN)

        assert exc_info.value.status_code == 400
        update_one.assert_not_awaited()
        log_admin_action.assert_not_awaited()

    @pytest.mark.anyio
    async def test_area_not_found_returns_404_without_audit(self):
        with (
            patch.multiple(
                "backend.features.db_supabase",
                update_one=AsyncMock(),
                get_rows=AsyncMock(return_value=[]),
            ),
            patch("backend.features.log_admin_action", AsyncMock()) as log_admin_action,
        ):
            req = UpdateTaxConfigRequest(pst_rate=6.0, justification="Valid justification")
            with pytest.raises(HTTPException) as exc_info:
                await update_area_tax("missing-area", req, admin=_ADMIN)

        assert exc_info.value.status_code == 404
        log_admin_action.assert_not_awaited()
