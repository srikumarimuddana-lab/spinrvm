"""GET /settings exposes rider_no_drivers_sheet_enabled (migration 470), default off."""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("row", "expected"),
    [({}, False), ({"rider_no_drivers_sheet_enabled": False}, False), ({"rider_no_drivers_sheet_enabled": True}, True)],
)
async def test_public_settings_carries_the_no_drivers_sheet_switch(row, expected):
    from backend.routes.settings import get_public_settings

    with patch("backend.routes.settings.get_app_settings", AsyncMock(return_value=row)):
        body = await get_public_settings()

    assert body["rider_no_drivers_sheet_enabled"] is expected
