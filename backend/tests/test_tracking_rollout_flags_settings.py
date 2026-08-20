"""Tracking-overhaul rollout flags are flippable via the admin settings API.

The GPS tracking-overhaul flags (migrations 345/349) existed as settings
columns and every consumer reads them via get_app_settings() with a
default of False (ship dark) — but none of them were on
SettingsUpdateRequest, so the admin dashboard's save silently dropped them
(extra="ignore") and the documented "flip via admin settings" rollout only
worked as a direct DB edit. These tests pin the wiring that makes the
rollout operable: each flag round-trips through the generic PUT handler, a
non-super-admin can flip them (none are credentials), omitting them leaves
the stored value unchanged, and idle_breadcrumb_retention_hours enforces
its evidence-safety bounds (24h floor — below a day the purge would eat
breadcrumbs the route finalizer's late-tail revisions still need; 2160h
ceiling — the blanket 90-day GPS purge makes anything longer a lie).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

_FLAGS = [
    "idle_location_v2_enabled",
    "period1_distance_tracking_enabled",
    "p2_route_geometry_enabled",
    "rider_show_pickup_leg_enabled",
    "location_health_push_nudge_enabled",
    "stale_p3_autoclose_enabled",
    "route_booked_dropoff_anchor_enabled",
]


@pytest.mark.parametrize("flag", _FLAGS)
def test_settings_update_request_round_trips_each_flag(flag):
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(**{flag: True})
    assert req.model_dump(exclude_none=True) == {flag: True}


def test_settings_update_request_omitted_flags_are_excluded():
    """'None = leave unchanged' convention: a save that doesn't mention a
    flag must not reset a dark-shipped feature that was already flipped on."""
    from backend.routes.admin.settings import SettingsUpdateRequest

    dumped = SettingsUpdateRequest().model_dump(exclude_none=True)
    for flag in _FLAGS:
        assert flag not in dumped
    assert "idle_breadcrumb_retention_hours" not in dumped


@pytest.mark.anyio
@pytest.mark.parametrize("flag", _FLAGS)
async def test_non_super_admin_can_flip_each_flag(flag):
    from backend.routes.admin import settings as admin_settings

    update_one = AsyncMock()
    with (
        patch.object(admin_settings.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "app_settings"}])),
        patch.object(admin_settings.db_supabase, "update_one", update_one),
        patch.object(admin_settings.db_supabase, "insert_one", AsyncMock()),
    ):
        await admin_settings.admin_update_settings(
            admin_settings.SettingsUpdateRequest(**{flag: True}),
            admin={"id": "admin-1", "role": "admin"},
        )

    update_one.assert_awaited_once()
    _table, _filter, payload = update_one.await_args.args
    assert payload[flag] is True


@pytest.mark.parametrize("hours", [24, 720, 2160])
def test_idle_retention_hours_accepts_evidence_safe_values(hours):
    from backend.routes.admin.settings import SettingsUpdateRequest

    req = SettingsUpdateRequest(idle_breadcrumb_retention_hours=hours)
    assert req.model_dump(exclude_none=True) == {"idle_breadcrumb_retention_hours": hours}


@pytest.mark.parametrize("hours", [0, 12, 5000])
def test_idle_retention_hours_rejects_out_of_bounds_values(hours):
    from backend.routes.admin.settings import SettingsUpdateRequest

    with pytest.raises(Exception):
        SettingsUpdateRequest(idle_breadcrumb_retention_hours=hours)
