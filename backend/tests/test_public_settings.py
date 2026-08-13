"""Tests for GET /settings (routes/settings.py::get_public_settings).

No dedicated test file existed for this route before. Added alongside
ACTION_ITEMS.md B16 to pin the new driver_discreet_sos_enabled field
(the mobile-facing exposure of the dark-launched rollout gate added in
backend/schemas.py + routes/admin/settings.py), while incidentally also
covering the pre-existing fields this endpoint returns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.mark.anyio
async def test_defaults_flag_off_when_unset():
    from backend.routes import settings as settings_mod

    with patch.object(settings_mod, "get_app_settings", AsyncMock(return_value={})):
        result = await settings_mod.get_public_settings()

    assert result["driver_discreet_sos_enabled"] is False


@pytest.mark.anyio
async def test_reflects_flag_when_enabled():
    from backend.routes import settings as settings_mod

    with patch.object(settings_mod, "get_app_settings", AsyncMock(return_value={"driver_discreet_sos_enabled": True})):
        result = await settings_mod.get_public_settings()

    assert result["driver_discreet_sos_enabled"] is True


@pytest.mark.anyio
async def test_existing_public_fields_still_returned():
    """Regression guard: adding the new field must not drop/rename the
    fields mobile clients already depend on."""
    from backend.routes import settings as settings_mod

    settings = {
        "google_maps_api_key": "gmaps-key",
        "stripe_publishable_key": "pk_test_123",
        "track_base_url": "https://track.spinr.ca",
    }
    with patch.object(settings_mod, "get_app_settings", AsyncMock(return_value=settings)):
        result = await settings_mod.get_public_settings()

    assert result["google_maps_api_key"] == "gmaps-key"
    assert result["stripe_publishable_key"] == "pk_test_123"
    assert result["track_base_url"] == "https://track.spinr.ca"
