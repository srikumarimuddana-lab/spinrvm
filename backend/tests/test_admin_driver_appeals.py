"""Route-level tests for POST /admin/driver-appeals/{appeal_id}/resolve.

Covers ranked-blocker #18 (baseline #12): this endpoint previously wrote no
audit_logs row at all. Follows the same patch.object(module, "log_admin_action",
AsyncMock) pattern used in test_admin_monitoring_coverage.py's flush-prefix
fix (docs/change-log/2026-08-19-redis-flush-prefix-audit-log-fix.md).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    import routes.admin.driver_appeals as driver_appeals_route
except ImportError:
    import backend.routes.admin.driver_appeals as driver_appeals_route  # type: ignore[no-redef]

pytestmark = pytest.mark.anyio

ADMIN = {"id": "admin-1", "role": "admin"}


def _appeal(**overrides):
    appeal = {
        "id": "appeal-1",
        "driver_id": "driver-1",
        "appeal_type": "suspension",
        "status": "pending",
    }
    appeal.update(overrides)
    return appeal


async def test_resolve_appeal_approved_writes_audit_row_and_reactivates_driver():
    audit_mock = AsyncMock(return_value="audit-abc")
    driver_action_mock = AsyncMock(return_value={"message": "Driver reactivated successfully"})
    with (
        patch.object(driver_appeals_route.appeals_service, "get_appeal", AsyncMock(return_value=_appeal())),
        patch.object(driver_appeals_route.appeals_service, "mark_resolved", AsyncMock()),
        patch.object(driver_appeals_route, "admin_driver_action", driver_action_mock),
        patch.object(driver_appeals_route, "log_admin_action", audit_mock),
    ):
        req = driver_appeals_route.ResolveAppealRequest(decision="approved", admin_note="Looks fine")
        out = await driver_appeals_route.admin_resolve_driver_appeal("appeal-1", req, ADMIN)

    assert out["driver_reactivated"] is True
    assert out["audit_log_id"] == "audit-abc"
    audit_mock.assert_awaited_once_with(
        ADMIN,
        "driver_appeal_approved",
        "driver_appeals",
        "appeal-1",
        {
            "decision": "approved",
            "admin_note": "Looks fine",
            "appeal_type": "suspension",
            "driver_id": "driver-1",
            "driver_reactivated": True,
        },
    )


async def test_resolve_appeal_denied_writes_audit_row_without_driver_action():
    audit_mock = AsyncMock(return_value="audit-xyz")
    driver_action_mock = AsyncMock()
    with (
        patch.object(driver_appeals_route.appeals_service, "get_appeal", AsyncMock(return_value=_appeal())),
        patch.object(driver_appeals_route.appeals_service, "mark_resolved", AsyncMock()),
        patch.object(driver_appeals_route, "admin_driver_action", driver_action_mock),
        patch.object(driver_appeals_route, "log_admin_action", audit_mock),
    ):
        req = driver_appeals_route.ResolveAppealRequest(decision="denied", admin_note=None)
        out = await driver_appeals_route.admin_resolve_driver_appeal("appeal-1", req, ADMIN)

    assert out["driver_reactivated"] is False
    driver_action_mock.assert_not_awaited()
    audit_mock.assert_awaited_once_with(
        ADMIN,
        "driver_appeal_denied",
        "driver_appeals",
        "appeal-1",
        {
            "decision": "denied",
            "admin_note": None,
            "appeal_type": "suspension",
            "driver_id": "driver-1",
            "driver_reactivated": False,
        },
    )


async def test_resolve_appeal_not_found_returns_404_and_no_audit():
    audit_mock = AsyncMock()
    from fastapi import HTTPException

    with (
        patch.object(driver_appeals_route.appeals_service, "get_appeal", AsyncMock(return_value=None)),
        patch.object(driver_appeals_route, "log_admin_action", audit_mock),
    ):
        req = driver_appeals_route.ResolveAppealRequest(decision="approved")
        with pytest.raises(HTTPException) as exc_info:
            await driver_appeals_route.admin_resolve_driver_appeal("missing", req, ADMIN)

    assert exc_info.value.status_code == 404
    audit_mock.assert_not_awaited()
