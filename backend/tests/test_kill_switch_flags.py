"""Tests for the E5 kill-switch flags (ACTION_ITEMS.md).

surge_engine_enabled, promo_redemption_enabled, corporate_billing_enabled
are new; scheduled_dispatch_enabled already existed in AppSettings and
gated utils/scheduled_rides.py's loop (2026-08-02), but was never added to
SettingsUpdateRequest -- there was previously no way to set it via the
admin API at all, only a direct DB update. All four are plain booleans, no
credential masking or super_admin gate needed (same shape as
admin_theme_v2_enabled / driver_discreet_sos_enabled).

These tests pin: schema defaults (all default True -- a kill switch must
default to "not killing anything"), that SettingsUpdateRequest round-trips
each value through the generic PUT handler without being silently dropped,
and that a non-super-admin CAN change them (none are in
_SUPER_ADMIN_ONLY_FIELDS).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_FLAGS = [
    "scheduled_dispatch_enabled",
    "surge_engine_enabled",
    "promo_redemption_enabled",
    "corporate_billing_enabled",
]


@pytest.mark.parametrize("flag", _FLAGS)
def test_app_settings_defaults_flag_on(flag):
    """A kill switch defaults to True (not killing anything) -- adding it
    must not silently pause a subsystem that was already running."""
    from backend.schemas import AppSettings

    assert getattr(AppSettings(), flag) is True


@pytest.mark.parametrize("flag", _FLAGS)
def test_settings_update_request_round_trips_each_flag(flag):
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(**{flag: False})
    assert req.model_dump(exclude_none=True) == {flag: False}


def test_settings_update_request_omitted_fields_are_excluded():
    """Confirms the flags follow this endpoint's 'None = leave unchanged'
    convention -- omitting them from a save must not reset them to True."""
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest()
    dumped = req.model_dump(exclude_none=True)
    for flag in _FLAGS:
        assert flag not in dumped


@pytest.mark.anyio
@pytest.mark.parametrize("flag", _FLAGS)
async def test_non_super_admin_can_change_each_flag(flag):
    """Not a credential/destination field -- unlike sos_paging_webhook_url,
    a settings-module (non-super-admin) admin must be able to flip these."""
    from backend.routes.admin import settings as admin_settings

    update_one = AsyncMock()
    with (
        patch.object(admin_settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}])),
        patch.object(admin_settings.db_supabase, "update_one", update_one),
        patch.object(admin_settings.db_supabase, "insert_one", AsyncMock()),
    ):
        await admin_settings.admin_update_settings(
            admin_settings.SettingsUpdateRequest(**{flag: False}),
            admin={"id": "admin-1", "role": "admin"},
        )

    update_one.assert_awaited_once()
    _table, _filter, payload = update_one.await_args.args
    assert payload[flag] is False
