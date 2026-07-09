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
