"""Per-app scoping of the inbox read/mutate endpoints (routes/notifications.py).

Companion to test_notification_inbox_audience.py, which covers the write half.
A dual-role user is one ``users`` row (routes/auth.py reuses the row found by
get_user_by_phone on OTP verify, setting both is_rider and is_driver), so every
one of these endpoints previously acted across both apps' notifications at
once — the listing, the unread badge, "mark all read", and the destructive
"clear all".

Scoping keys off X-App-Platform, which both apps already send on every request
via the shared client's setAppIdentity() and which core/middleware.py's
ForcedUpgradeMiddleware already recognises.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from routes.notifications import clear_notifications, get_notifications, mark_all_read
except ImportError:  # pragma: no cover
    from backend.routes.notifications import (  # type: ignore
        clear_notifications,
        get_notifications,
        mark_all_read,
    )

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

_USER = {"id": "77777777-7777-7777-7777-777777777777"}

# Every Query()/Header()-defaulted parameter is passed EXPLICITLY below, even
# where the value equals the declared default. Calling a FastAPI route
# function directly bypasses the dependency-injection cycle, so an omitted
# parameter stays bound to the `fastapi.params.Query` marker object rather
# than to the literal default — and that object is truthy, so `if read_only:`
# / `if unread_only:` would take the wrong branch and the assertions here
# would be testing a code path the real endpoint never runs. This matches the
# existing convention in tests/test_notifications_delete.py's
# TestClearNotifications, which passes read_only= on every call for the same
# reason. Do not "tidy" these away.

_DB = "routes.notifications.db_supabase"
try:  # match the module actually imported above
    import routes.notifications  # noqa: F401
except ImportError:  # pragma: no cover
    _DB = "backend.routes.notifications.db_supabase"


def _rider_scope():
    return {"audience": {"$in": ["rider", "both"]}}


def _driver_scope():
    return {"audience": {"$in": ["driver", "both"]}}


@pytest.mark.parametrize(
    ("platform", "expected_scope"),
    [("rider", _rider_scope()), ("driver", _driver_scope())],
)
async def test_listing_and_unread_count_are_both_scoped_to_the_calling_app(platform, expected_scope):
    """Both queries must carry the scope.

    Scoping only the list would leave the bell badge counting notifications
    the app never displays — the same duplicate bug, just relocated to a
    number the user cannot reconcile.
    """
    with (
        patch(f"{_DB}.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch(f"{_DB}.count_documents", AsyncMock(return_value=0)) as count,
    ):
        await get_notifications(limit=30, offset=0, unread_only=False, x_app_platform=platform, current_user=_USER)

    list_filters = get_rows.await_args.args[1]
    count_filters = count.await_args.args[1]

    assert list_filters["audience"] == expected_scope["audience"]
    assert count_filters["audience"] == expected_scope["audience"]
    assert list_filters["user_id"] == _USER["id"]
    assert count_filters["is_read"] is False


async def test_account_level_notices_stay_visible_in_both_apps():
    """'both' must be in every scope — that is what keeps suspension and
    reactivation notices (features.py records them with no target_app)
    reachable from whichever app the user happens to open."""
    with (
        patch(f"{_DB}.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch(f"{_DB}.count_documents", AsyncMock(return_value=0)),
    ):
        await get_notifications(limit=30, offset=0, unread_only=False, x_app_platform="driver", current_user=_USER)

    assert "both" in get_rows.await_args.args[1]["audience"]["$in"]


@pytest.mark.parametrize("platform", [None, "", "web", "admin", "Rider", "DRIVER"])
async def test_missing_or_unrecognised_platform_leaves_the_inbox_unscoped(platform):
    """Backward compatibility, and deliberately case-sensitive.

    An installed build predating setAppIdentity(), an admin tool, or curl
    must keep seeing the whole inbox rather than silently losing half of it.
    Matching is exact so a stray casing never resolves to a surface and hides
    rows; the apps send lowercase literals.
    """
    with (
        patch(f"{_DB}.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch(f"{_DB}.count_documents", AsyncMock(return_value=0)) as count,
    ):
        await get_notifications(limit=30, offset=0, unread_only=False, x_app_platform=platform, current_user=_USER)

    assert "audience" not in get_rows.await_args.args[1]
    assert "audience" not in count.await_args.args[1]


async def test_unread_only_combines_with_the_audience_scope():
    with (
        patch(f"{_DB}.get_rows", AsyncMock(return_value=[])) as get_rows,
        patch(f"{_DB}.count_documents", AsyncMock(return_value=0)),
    ):
        await get_notifications(limit=30, offset=0, unread_only=True, x_app_platform="rider", current_user=_USER)

    filters = get_rows.await_args.args[1]
    assert filters["is_read"] is False
    assert filters["audience"] == _rider_scope()["audience"]


async def test_mark_all_read_does_not_clear_the_other_app_badge():
    with patch(f"{_DB}.update_one", AsyncMock()) as update:
        await mark_all_read(x_app_platform="rider", current_user=_USER)

    filters = update.await_args.args[1]
    assert filters["audience"] == _rider_scope()["audience"]
    assert filters["is_read"] is False


async def test_clear_all_from_one_app_cannot_delete_the_other_apps_history():
    """The destructive case, and the reason this endpoint is scoped at all.

    Unscoped, a dual-role driver tapping "Clear all" in the driver app
    permanently deleted their rider receipts and refund notices — rows that
    app never showed them and that nothing can restore.
    """
    with patch(f"{_DB}.delete_many", AsyncMock()) as delete:
        await clear_notifications(read_only=False, x_app_platform="driver", current_user=_USER)

    filters = delete.await_args.args[1]
    assert filters["audience"] == _driver_scope()["audience"]
    assert filters["user_id"] == _USER["id"]
    assert "is_read" not in filters


async def test_clear_read_only_keeps_both_the_audience_and_read_filters():
    with patch(f"{_DB}.delete_many", AsyncMock()) as delete:
        await clear_notifications(read_only=True, x_app_platform="rider", current_user=_USER)

    filters = delete.await_args.args[1]
    assert filters["audience"] == _rider_scope()["audience"]
    assert filters["is_read"] is True


async def test_clear_all_stays_unscoped_for_a_legacy_client():
    """No header → previous whole-inbox behavior, unchanged."""
    with patch(f"{_DB}.delete_many", AsyncMock()) as delete:
        await clear_notifications(read_only=False, x_app_platform=None, current_user=_USER)

    assert delete.await_args.args[1] == {"user_id": _USER["id"]}
