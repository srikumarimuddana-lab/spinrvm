"""Route-level coverage for PUT /admin/settings/heatmap.

Covers ranked-blocker #18 (baseline #12): this endpoint previously wrote no
audit_logs row. Follows the same patch.object(module, "log_admin_action",
AsyncMock) pattern used elsewhere in this sweep
(docs/change-log/2026-08-19-redis-flush-prefix-audit-log-fix.md).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    import routes.admin.settings as settings_route
except ImportError:
    import backend.routes.admin.settings as settings_route  # type: ignore[no-redef]

pytestmark = pytest.mark.anyio

ADMIN = {"id": "admin-1", "role": "admin"}


async def test_update_heatmap_settings_updates_existing_row_and_audits():
    audit_mock = AsyncMock(return_value="audit-1")
    update_mock = AsyncMock()
    with (
        patch.object(settings_route.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "heatmap_settings"}])),
        patch.object(settings_route.db_supabase, "update_one", update_mock),
        patch.object(settings_route, "log_admin_action", audit_mock),
    ):
        req = settings_route.HeatmapSettingsRequest(corporate_heat_map_enabled=True)
        out = await settings_route.admin_update_heatmap_settings(req, ADMIN)

    assert out == {"message": "Heat map settings updated"}
    update_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
    assert audit_mock.call_args[0][1] == "heatmap_settings_updated"
    assert audit_mock.call_args[0][2] == "settings"
    assert "corporate_heat_map_enabled" in audit_mock.call_args[0][4]["fields"]


async def test_update_heatmap_settings_inserts_when_no_existing_row():
    audit_mock = AsyncMock(return_value="audit-2")
    insert_mock = AsyncMock()
    with (
        patch.object(settings_route.db_supabase, "get_rows", AsyncMock(return_value=[])),
        patch.object(settings_route.db_supabase, "insert_one", insert_mock),
        patch.object(settings_route, "log_admin_action", audit_mock),
    ):
        req = settings_route.HeatmapSettingsRequest(regular_rider_heat_map_enabled=False)
        await settings_route.admin_update_heatmap_settings(req, ADMIN)

    insert_mock.assert_awaited_once()
    audit_mock.assert_awaited_once()
