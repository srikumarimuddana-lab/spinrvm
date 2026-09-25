"""GET /settings exposes saved_place_shortcuts_enabled, default ON (2026-09-25)."""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({}, True),
        ({"saved_place_shortcuts_enabled": True}, True),
        ({"saved_place_shortcuts_enabled": False}, False),
    ],
)
async def test_public_settings_carries_the_saved_place_shortcuts_switch(row, expected):
    from backend.routes.settings import get_public_settings

    with patch("backend.routes.settings.get_app_settings", AsyncMock(return_value=row)):
        body = await get_public_settings()

    assert body["saved_place_shortcuts_enabled"] is expected
