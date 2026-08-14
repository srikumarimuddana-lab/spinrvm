"""Tests for the driver_discreet_sos_enabled feature flag (ACTION_ITEMS.md B16).

Dark-launched rollout gate for the driver SOS discreet-hold-shield UX
(shared/components/SafetyShield.tsx + SafetyOverlay.tsx). Plain boolean,
default-off, no credential masking or super_admin gate needed — same shape
as admin_theme_v2_enabled / dual_approval_exports_enabled, unlike the
sos_paging_* pair (a destination credential) added for B15.

These tests pin: the schema default, that AppSettingsUpdate round-trips the
value through the generic PUT /admin/settings handler without being
silently dropped, and that a non-super-admin CAN change it (it's not in
_SUPER_ADMIN_ONLY_FIELDS).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


def test_app_settings_defaults_flag_off():
    from backend.schemas import AppSettings

    assert AppSettings().driver_discreet_sos_enabled is False


def test_settings_update_request_round_trips_the_flag():
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(driver_discreet_sos_enabled=True)
    assert req.model_dump(exclude_none=True) == {"driver_discreet_sos_enabled": True}


def test_settings_update_request_omitted_field_is_excluded():
    """Confirms the flag follows this endpoint's 'None = leave unchanged'
    convention -- omitting it from a save must not reset it to False."""
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest()
    assert "driver_discreet_sos_enabled" not in req.model_dump(exclude_none=True)


@pytest.mark.anyio
async def test_non_super_admin_can_change_the_flag():
    """Not a credential/destination field -- unlike sos_paging_webhook_url,
    a settings-module (non-super-admin) admin must be able to flip it."""
    from backend.routes.admin import settings as admin_settings

    update_one = AsyncMock()
    with (
        patch.object(admin_settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}])),
        patch.object(admin_settings.db_supabase, "update_one", update_one),
        patch.object(admin_settings.db_supabase, "insert_one", AsyncMock()),
    ):
        await admin_settings.admin_update_settings(
            admin_settings.SettingsUpdateRequest(driver_discreet_sos_enabled=True),
            admin={"id": "admin-1", "role": "admin"},
        )

    update_one.assert_awaited_once()
    _table, _filter, payload = update_one.await_args.args
    assert payload["driver_discreet_sos_enabled"] is True
