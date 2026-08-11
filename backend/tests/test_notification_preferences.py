"""Unit tests for notification-preferences endpoints (routes/notifications.py).

Regression for the driver-app "Earnings Summary" toggle: the field must be
accepted by PreferencesUpdate, written by PUT /notifications/preferences, and
present in the GET defaults — previously Pydantic silently dropped it and the
PUT returned 200 without persisting anything.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from routes.notifications import PreferencesUpdate, get_preferences, update_preferences
except ImportError:  # pragma: no cover
    from backend.routes.notifications import (  # type: ignore
        PreferencesUpdate,
        get_preferences,
        update_preferences,
    )

pytestmark = [pytest.mark.anyio, pytest.mark.unit]

_USER = {"id": "77777777-7777-7777-7777-777777777777"}

_DB = "routes.notifications.db_supabase"
try:  # match the module actually imported above
    import routes.notifications  # noqa: F401
except ImportError:  # pragma: no cover
    _DB = "backend.routes.notifications.db_supabase"


async def test_get_defaults_include_every_toggleable_field():
    with patch(f"{_DB}.get_rows", AsyncMock(return_value=[])):
        prefs = await get_preferences(current_user=_USER)

    assert set(prefs) == {
        "push_enabled",
        "email_enabled",
        "sms_enabled",
        "ride_updates",
        "promotions",
        "safety_alerts",
        "earnings_summary",
    }
    assert prefs["earnings_summary"] is True


async def test_put_persists_earnings_summary_on_existing_row():
    update = AsyncMock(return_value=None)
    with (
        patch(f"{_DB}.get_rows", AsyncMock(return_value=[{"user_id": _USER["id"]}])),
        patch(f"{_DB}.update_one", update),
    ):
        body = PreferencesUpdate(earnings_summary=False)
        res = await update_preferences(body, current_user=_USER)

    assert res == {"success": True}
    update.assert_awaited_once()
    written = update.call_args.args[2]
    assert written["earnings_summary"] is False
    # Partial update: untouched fields must not be written.
    assert "push_enabled" not in written


async def test_put_inserts_row_with_earnings_summary_when_none_exists():
    insert = AsyncMock(return_value=None)
    with (
        patch(f"{_DB}.get_rows", AsyncMock(return_value=[])),
        patch(f"{_DB}.insert_one", insert),
    ):
        body = PreferencesUpdate(earnings_summary=False, push_enabled=True)
        await update_preferences(body, current_user=_USER)

    insert.assert_awaited_once()
    written = insert.call_args.args[1]
    assert written["earnings_summary"] is False
    assert written["push_enabled"] is True
    assert written["user_id"] == _USER["id"]


async def test_push_suppressed_when_user_opted_out():
    """send_push_notification must honor push_enabled=false for informational
    pushes (the toggle previously saved the flag but nothing read it)."""
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    deliver = AsyncMock(return_value=True)
    with (
        patch.object(features, "_record_inbox_notification"),
        patch.object(features, "_deliver_push_now", deliver),
        patch.object(
            features.db,
            "get_rows",
            AsyncMock(return_value=[{"user_id": _USER["id"], "push_enabled": False}]),
        ),
        patch.object(features.db, "find_one", AsyncMock()) as find_one,
    ):
        result = await features.send_push_notification(_USER["id"], "t", "b")

    assert result is False
    deliver.assert_not_awaited()
    find_one.assert_not_awaited()  # suppressed before the token lookup


async def test_push_opt_out_does_not_block_dispatch_safety_or_account():
    """Ride offers and SOS alerts bypass the preference because they are
    time-critical. Account-state notices (driver rejected/suspended/banned)
    bypass it for a different reason: a driver who can no longer earn must be
    told why, not left to discover it as a 403 on their next go-online."""
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    for priority in ("dispatch", "safety", "account"):
        deliver = AsyncMock(return_value=True)
        with (
            patch.object(features, "_record_inbox_notification"),
            patch.object(features, "_deliver_push_now", deliver),
            patch.object(
                features.db,
                "get_rows",
                AsyncMock(return_value=[{"user_id": _USER["id"], "push_enabled": False}]),
            ),
            patch.object(
                features.db,
                "find_one",
                AsyncMock(return_value={"id": _USER["id"], "fcm_token": "tok"}),
            ),
        ):
            result = await features.send_push_notification(_USER["id"], "t", "b", priority=priority)
        assert result is True
        deliver.assert_awaited_once()


async def test_push_suppressed_when_ride_updates_disabled():
    """ACTION_ITEMS.md N9: ride_updates=false must suppress a push whose
    data.type is in features._RIDE_UPDATE_PUSH_TYPES, even when push_enabled
    is still true — the two toggles are independent."""
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    deliver = AsyncMock(return_value=True)
    with (
        patch.object(features, "_record_inbox_notification"),
        patch.object(features, "_deliver_push_now", deliver),
        patch.object(
            features.db,
            "get_rows",
            AsyncMock(return_value=[{"user_id": _USER["id"], "push_enabled": True, "ride_updates": False}]),
        ),
        patch.object(features.db, "find_one", AsyncMock()) as find_one,
    ):
        result = await features.send_push_notification(_USER["id"], "t", "b", data={"type": "driver_arrived"})

    assert result is False
    deliver.assert_not_awaited()
    find_one.assert_not_awaited()


async def test_push_not_suppressed_by_ride_updates_for_unrelated_type():
    """ride_updates=false must not touch pushes outside the narrow ride-status
    set (e.g. a wallet top-up notice) — only push_enabled governs those."""
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    deliver = AsyncMock(return_value=True)
    with (
        patch.object(features, "_record_inbox_notification"),
        patch.object(features, "_deliver_push_now", deliver),
        patch.object(
            features.db,
            "get_rows",
            AsyncMock(return_value=[{"user_id": _USER["id"], "push_enabled": True, "ride_updates": False}]),
        ),
        patch.object(
            features.db,
            "find_one",
            AsyncMock(return_value={"id": _USER["id"], "fcm_token": "tok"}),
        ),
    ):
        result = await features.send_push_notification(_USER["id"], "t", "b", data={"type": "wallet_topup"})

    assert result is True
    deliver.assert_awaited_once()


async def test_push_ride_update_type_sent_when_ride_updates_enabled():
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    deliver = AsyncMock(return_value=True)
    with (
        patch.object(features, "_record_inbox_notification"),
        patch.object(features, "_deliver_push_now", deliver),
        patch.object(
            features.db,
            "get_rows",
            AsyncMock(return_value=[{"user_id": _USER["id"], "push_enabled": True, "ride_updates": True}]),
        ),
        patch.object(
            features.db,
            "find_one",
            AsyncMock(return_value={"id": _USER["id"], "fcm_token": "tok"}),
        ),
    ):
        result = await features.send_push_notification(_USER["id"], "t", "b", data={"type": "ride_started"})

    assert result is True
    deliver.assert_awaited_once()


async def test_push_ride_updates_opt_out_does_not_block_dispatch_priority():
    """ride_cancelled sent at priority='dispatch' (to a driver) must bypass
    the ride_updates gate the same way it bypasses push_enabled — the
    time_critical short-circuit runs before any preference is read."""
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    deliver = AsyncMock(return_value=True)
    with (
        patch.object(features, "_record_inbox_notification"),
        patch.object(features, "_deliver_push_now", deliver),
        patch.object(
            features.db,
            "get_rows",
            AsyncMock(return_value=[{"user_id": _USER["id"], "push_enabled": True, "ride_updates": False}]),
        ),
        patch.object(
            features.db,
            "find_one",
            AsyncMock(return_value={"id": _USER["id"], "fcm_token": "tok"}),
        ),
    ):
        result = await features.send_push_notification(
            _USER["id"], "t", "b", data={"type": "ride_cancelled"}, priority="dispatch"
        )

    assert result is True
    deliver.assert_awaited_once()


async def test_push_preference_lookup_failure_fails_open():
    """A prefs-table hiccup must not start dropping pushes."""
    try:
        import features
    except ImportError:  # pragma: no cover
        from backend import features  # type: ignore

    deliver = AsyncMock(return_value=True)
    with (
        patch.object(features, "_record_inbox_notification"),
        patch.object(features, "_deliver_push_now", deliver),
        patch.object(features.db, "get_rows", AsyncMock(side_effect=RuntimeError("db down"))),
        patch.object(
            features.db,
            "find_one",
            AsyncMock(return_value={"id": _USER["id"], "fcm_token": "tok"}),
        ),
    ):
        result = await features.send_push_notification(_USER["id"], "t", "b")

    assert result is True
    deliver.assert_awaited_once()


async def test_model_accepts_every_field_the_apps_send():
    """The client vocabulary must stay in sync with the model — this is the
    contract-drift guard. If a client-side key is added, it must be added here
    AND to PreferencesUpdate + the PUT write-list + the GET defaults."""
    fields = set(PreferencesUpdate.model_fields)
    assert fields == {
        "push_enabled",
        "email_enabled",
        "sms_enabled",
        "ride_updates",
        "promotions",
        "safety_alerts",
        "earnings_summary",
    }
