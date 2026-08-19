"""ACTION_ITEMS.md N10 (admin/ambiguous bucket): the last slice of the
target_app="rider"/"driver" sweep — admin-triggered pushes across
routes/admin/documents.py, drivers.py, faqs.py, users.py, and
vehicle_fleet.py that previously fell through to the legacy fcm_token
column. routes/admin/rides.py's sites are covered in
test_admin_rides_coverage.py; routes/admin/wallet.py was already fixed
(N15/R31); routes/notifications.py's /test-push is deliberately left on
the legacy column (its whole job is diagnosing that exact token).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ─────────────────────────────────────────────────────────────────────────────
# routes/admin/documents.py — document-rejection push
# ─────────────────────────────────────────────────────────────────────────────


async def test_document_rejection_push_targets_driver_app():
    import routes.admin.documents as admin_docs

    doc = {"id": "doc-1", "driver_id": "drv-1", "document_type": "Insurance", "status": "pending"}
    driver = {"id": "drv-1", "user_id": "usr-1"}
    push = AsyncMock()

    async def _get_rows(table, filters=None, **kwargs):
        if table == "driver_documents":
            return [doc]
        return []

    with (
        patch.object(admin_docs.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
        patch.object(admin_docs.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_docs.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(admin_docs, "_log_driver_activity", AsyncMock()),
        patch.object(admin_docs, "log_admin_action", AsyncMock()),
        patch.object(admin_docs, "send_push_notification", push),
        patch.object(admin_docs, "supabase", None),
    ):
        await admin_docs.admin_review_driver_document(
            "doc-1",
            admin_docs.DocumentReviewRequest(status="rejected", rejection_reason="blurry"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.args[0] == "usr-1"
    assert push.await_args.kwargs.get("target_app") == "driver"


# ─────────────────────────────────────────────────────────────────────────────
# routes/admin/drivers.py — expiry nudge + photo review
# ─────────────────────────────────────────────────────────────────────────────


async def test_expiry_nudge_push_targets_driver_app():
    import routes.admin.drivers as admin_drivers

    driver = {"id": "drv-1", "user_id": "usr-1", "license_expiry_date": None}
    push = AsyncMock()

    with (
        patch.object(admin_drivers.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(admin_drivers.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_drivers, "log_admin_action", AsyncMock()),
        patch.object(admin_drivers, "send_push_notification", push),
    ):
        await admin_drivers.admin_nudge_driver_expiry(
            "drv-1",
            admin_drivers.DriverNudgeExpiryRequest(doc_type="license"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.args[0] == "usr-1"
    assert push.await_args.kwargs.get("target_app") == "driver"


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_photo_review_push_targets_driver_app(action):
    import routes.admin.drivers as admin_drivers

    driver = {"id": "drv-1", "user_id": "usr-1"}
    push = AsyncMock()

    with (
        patch.object(admin_drivers.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(admin_drivers.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_drivers, "send_push_notification", push),
    ):
        await admin_drivers.admin_review_driver_photo(
            "drv-1",
            admin_drivers.DriverPhotoReviewRequest(action=action),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.args[0] == "usr-1"
    assert push.await_args.kwargs.get("target_app") == "driver"


# ─────────────────────────────────────────────────────────────────────────────
# routes/admin/faqs.py — admin_send_notification broadcast branches
# ─────────────────────────────────────────────────────────────────────────────


async def test_broadcast_to_riders_targets_rider_app():
    import routes.admin.faqs as admin_faqs

    push = AsyncMock()
    with (
        patch.object(admin_faqs.db, "get_rows", AsyncMock(return_value=[{"id": "usr-1"}, {"id": "usr-2"}])),
        patch("features.send_push_notification", push),
        patch.object(admin_faqs, "log_admin_action", AsyncMock(return_value="audit-1")),
    ):
        await admin_faqs.admin_send_notification(
            request=None,
            notification=admin_faqs.NotificationRequest(title="t", body="b", audience="riders"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    assert push.await_count == 2
    for call in push.await_args_list:
        assert call.kwargs.get("target_app") == "rider"


async def test_broadcast_to_drivers_targets_driver_app():
    import routes.admin.faqs as admin_faqs

    push = AsyncMock()
    with (
        patch.object(admin_faqs.db, "get_rows", AsyncMock(return_value=[{"id": "usr-1"}])),
        patch("features.send_push_notification", push),
        patch.object(admin_faqs, "log_admin_action", AsyncMock(return_value="audit-2")),
    ):
        await admin_faqs.admin_send_notification(
            request=None,
            notification=admin_faqs.NotificationRequest(title="t", body="b", audience="drivers"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.kwargs.get("target_app") == "driver"


async def test_broadcast_to_all_leaves_target_app_unset():
    """"all" spans both roles with no per-user role lookup in this branch, so
    it can't map to a single target_app — matches the precedent
    routes/admin/messaging.py's own "all" audience already established
    (legacy fcm_token fallback, not a gap)."""
    import routes.admin.faqs as admin_faqs

    push = AsyncMock()
    with (
        patch.object(admin_faqs.db, "get_rows", AsyncMock(return_value=[{"id": "usr-1"}])),
        patch("features.send_push_notification", push),
        patch.object(admin_faqs, "log_admin_action", AsyncMock(return_value="audit-3")),
    ):
        await admin_faqs.admin_send_notification(
            request=None,
            notification=admin_faqs.NotificationRequest(title="t", body="b", audience="all"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.kwargs.get("target_app") is None


# ─────────────────────────────────────────────────────────────────────────────
# routes/admin/users.py — account-status-change push
# ─────────────────────────────────────────────────────────────────────────────


async def test_account_status_push_targets_rider_app():
    """admin_update_user_status's own docstring scopes it to rider moderation
    ("Suspend, ban, or reactivate a rider account" / "cannot request a
    ride") — target_app="rider" is not a guess, it's what the endpoint is
    documented to do."""
    import routes.admin.users as admin_users

    user = {"id": "usr-1", "status": "active"}
    push = AsyncMock()

    with (
        patch.object(admin_users.db_supabase, "get_user_by_id", AsyncMock(return_value=user)),
        patch.object(admin_users.db_supabase, "update_one", AsyncMock()),
        patch.object(admin_users.db_supabase, "insert_one", AsyncMock()),
        patch("features.send_push_notification", push),
    ):
        await admin_users.admin_update_user_status(
            "usr-1",
            admin_users.UserStatusRequest(status="suspended", reason="fraud"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.args[0] == "usr-1"
    assert push.await_args.kwargs.get("target_app") == "rider"


# ─────────────────────────────────────────────────────────────────────────────
# routes/admin/vehicle_fleet.py — lost-and-found push
# ─────────────────────────────────────────────────────────────────────────────


async def test_lost_and_found_push_targets_driver_app():
    import routes.admin.vehicle_fleet as admin_fleet

    ride = {"id": "ride-1", "driver_id": "drv-1", "rider_id": "usr-rider-1"}
    driver = {"id": "drv-1", "user_id": "usr-1"}
    driver_user = {"id": "usr-1", "fcm_token": "some-token"}
    push = AsyncMock()

    with (
        patch.object(admin_fleet.db_supabase, "get_ride", AsyncMock(return_value=ride)),
        patch.object(admin_fleet.db_supabase, "create_lost_and_found", AsyncMock(return_value={"id": "item-1"})),
        patch.object(admin_fleet.db_supabase, "get_driver_by_id", AsyncMock(return_value=driver)),
        patch.object(admin_fleet.db_supabase, "get_user_by_id", AsyncMock(return_value=driver_user)),
        patch.object(admin_fleet.db_supabase, "update_lost_and_found", AsyncMock()),
        patch("features.send_push_notification", push),
        patch.object(admin_fleet, "log_admin_action", AsyncMock(return_value="audit-4")),
    ):
        await admin_fleet.admin_report_lost_item(
            "ride-1",
            admin_fleet.LostAndFoundRequest(item_description="wallet"),
            admin={"id": "adm-1", "role": "super_admin"},
        )

    push.assert_awaited_once()
    assert push.await_args.args[0] == "usr-1"
    assert push.await_args.kwargs.get("target_app") == "driver"
