"""
Regression test for backend/features.py :: admin_reset_surge_to_auto.

With the per-area surge_enabled gate, "reset to auto" must flip surge_enabled
back on. Otherwise an area an operator previously disabled would stay gated
off: the surge engine keeps skipping it and fare calc keeps ignoring surge,
even though the endpoint returns success.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


async def test_reset_to_auto_enables_surge_gate():
    """Reset-to-auto must set surge_source=auto, surge_active and surge_enabled."""
    from features import admin_reset_surge_to_auto, db

    area_before = {"id": "area-1", "surge_enabled": False, "surge_source": "manual"}
    area_after = {"id": "area-1", "surge_enabled": True, "surge_source": "auto", "surge_active": True}

    with (
        patch.object(db, "find_one", new=AsyncMock(side_effect=[area_before, area_after])),
        patch.object(db, "update_one", new=AsyncMock()) as mock_update,
    ):
        result = await admin_reset_surge_to_auto("area-1")

    mock_update.assert_awaited_once()
    args, _ = mock_update.call_args
    set_payload = args[2]["$set"]
    assert set_payload["surge_source"] == "auto"
    assert set_payload["surge_active"] is True
    assert set_payload["surge_enabled"] is True
    assert result["surge_enabled"] is True


async def test_reset_to_auto_404_when_area_missing():
    """Unknown area still 404s and writes nothing."""
    from fastapi import HTTPException

    from features import admin_reset_surge_to_auto, db

    with (
        patch.object(db, "find_one", new=AsyncMock(return_value=None)),
        patch.object(db, "update_one", new=AsyncMock()) as mock_update,
    ):
        with pytest.raises(HTTPException) as exc:
            await admin_reset_surge_to_auto("nope")

    assert exc.value.status_code == 404
    mock_update.assert_not_awaited()
