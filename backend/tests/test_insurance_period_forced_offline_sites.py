"""BLOCKER #1 (2026-09-22 insurance-period audit): every path that forces a
driver ``is_online=False`` must re-classify their insurance period.

Before the fix, the document-expiry sweep, the accept-path document
suspension, and the admin suspend/ban/reject/status-override endpoints wrote
``is_online=False`` and no period row at all — an open Period 1 stayed open
over personal-auto time forever, and the reconciler could never heal it
(its idle scan only looks at ``is_online = True``).

These tests prove each site calls ``close_period_for_forced_offline`` (the
derivation itself is unit-tested in ``test_insurance_period_close_helpers.py``)
and only after the offline write actually took effect.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# utils/document_expiry.py check_expiring_documents
# ---------------------------------------------------------------------------


def _run_doc_expiry(update_one_return):
    from backend.utils import document_expiry as de

    driver = {"id": "d1", "user_id": "u1", "license_expiry_date": "2020-01-01T00:00:00+00:00"}

    def _get_rows(table, filters=None, limit=None, offset=0, **kw):
        if table == "drivers":
            return [driver] if offset == 0 else []
        return []

    close = AsyncMock(return_value=0)
    with (
        patch("backend.utils.document_expiry.db.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.utils.document_expiry.db.update_one", AsyncMock(return_value=update_one_return)),
        patch("backend.utils.document_expiry.send_push_notification", AsyncMock()),
        patch("backend.utils.document_expiry.clear_presence", AsyncMock()),
        patch("backend.utils.document_expiry.manager", MagicMock()),
        patch("backend.utils.document_expiry.close_period_for_forced_offline", close),
    ):
        asyncio.run(de.check_expiring_documents())
    return close


def test_document_expiry_suspension_closes_insurance_period():
    close = _run_doc_expiry({"id": "d1", "status": "suspended"})
    close.assert_awaited_once_with("d1", reason="document_expired")


def test_document_expiry_lost_claim_does_not_touch_period():
    """Another replica won the suspension claim and already closed it."""
    close = _run_doc_expiry(None)
    close.assert_not_awaited()


# ---------------------------------------------------------------------------
# routes/drivers/_shared.py _suspend_driver_for_expired_documents (accept path)
# ---------------------------------------------------------------------------


def _run_accept_path_suspend(update_one):
    from backend.routes.drivers import _shared

    close = AsyncMock(return_value=2)
    with (
        patch("backend.routes.drivers._deps.db_supabase.update_one", update_one),
        patch("backend.routes.drivers._deps.close_period_for_forced_offline", close),
    ):
        asyncio.run(_shared._suspend_driver_for_expired_documents("drv-1", ["Driver's License"]))
    return close


def test_accept_path_suspension_closes_insurance_period():
    """Once this write sets status='suspended', the 12h sweep's CAS matches
    zero rows for the driver, so the sweep will never close their period."""
    close = _run_accept_path_suspend(AsyncMock(return_value={"id": "drv-1", "status": "suspended"}))
    close.assert_awaited_once_with("drv-1", reason="document_expired")


def test_accept_path_lost_claim_does_not_touch_period():
    close = _run_accept_path_suspend(AsyncMock(return_value=None))
    close.assert_not_awaited()


def test_accept_path_failed_write_does_not_touch_period():
    close = _run_accept_path_suspend(AsyncMock(side_effect=RuntimeError("db down")))
    close.assert_not_awaited()


# ---------------------------------------------------------------------------
# routes/admin/drivers.py admin_driver_action / admin_override_driver_status
# ---------------------------------------------------------------------------

_ADMIN_DRIVER = {"id": "drv-1", "user_id": "usr-1", "status": "active"}


@pytest.fixture
def super_admin_override():
    from backend.server import app
    from dependencies import get_admin_user

    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin_1", "role": "super_admin", "email": "a@b.com"}
    yield
    app.dependency_overrides.pop(get_admin_user, None)


def _admin_call(test_client, close, method, url, body, update_one=None):
    with (
        patch("db_supabase.get_driver_by_id", AsyncMock(return_value=_ADMIN_DRIVER)),
        patch("db_supabase.update_one", update_one or AsyncMock()),
        patch("db_supabase.insert_one", AsyncMock()),
        patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="audit-1")),
        patch("features.send_push_notification", AsyncMock()),
        patch("routes.admin.drivers.close_period_for_forced_offline", close),
    ):
        return getattr(test_client, method)(url, json=body)


@pytest.mark.parametrize("action", ["suspend", "ban", "reject"])
def test_admin_forced_offline_action_closes_period(test_client, super_admin_override, action):
    close = AsyncMock(return_value=0)
    resp = _admin_call(test_client, close, "post", "/api/admin/drivers/drv-1/action", {"action": action, "reason": "r"})
    assert resp.status_code == 200, resp.text
    close.assert_awaited_once_with("drv-1", reason=f"admin_{action}")


@pytest.mark.parametrize("action", ["approve", "unban", "reactivate"])
def test_admin_non_offline_action_leaves_period_alone(test_client, super_admin_override, action):
    close = AsyncMock()
    resp = _admin_call(test_client, close, "post", "/api/admin/drivers/drv-1/action", {"action": action, "reason": "r"})
    assert resp.status_code == 200, resp.text
    close.assert_not_awaited()


def test_admin_action_failed_write_does_not_touch_period(test_client, super_admin_override):
    """The close must run only after the offline write succeeded."""
    close = AsyncMock()
    resp = _admin_call(
        test_client,
        close,
        "post",
        "/api/admin/drivers/drv-1/action",
        {"action": "suspend", "reason": "r"},
        update_one=AsyncMock(side_effect=RuntimeError("db down")),
    )
    assert resp.status_code == 500
    close.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "expect_close"),
    [("suspended", True), ("banned", True), ("rejected", True), ("needs_review", True), ("active", False)],
)
def test_admin_status_override_closes_period_when_taking_offline(
    test_client, super_admin_override, status, expect_close
):
    close = AsyncMock(return_value=0)
    resp = _admin_call(
        test_client, close, "put", "/api/admin/drivers/drv-1/status-override", {"status": status, "reason": "r"}
    )
    assert resp.status_code == 200, resp.text
    if expect_close:
        close.assert_awaited_once_with("drv-1", reason="admin_status_override")
    else:
        close.assert_not_awaited()
