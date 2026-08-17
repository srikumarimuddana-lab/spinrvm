"""Safety-panel fields on GET /service-areas (migration 316).

The rider/driver Safety panel renders its local-authority row from these. The
rules being pinned:
  * the fields reach the apps at all (they must be in the public whitelist)
  * emergency_number is NEVER blank -- a 911 button that dials nothing is
    worse than no button
  * driver-licensing metadata (regulatory_*) stays OUT of the public payload
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _area(**over):
    base = {
        "id": "area-1",
        "name": "Regina",
        "city": "Regina",
        "is_active": True,
        "is_airport": False,
        "polygon": [],
        "surge_enabled": False,
        # Driver-licensing metadata -- must not leak publicly.
        "regulatory_authority": "SGI",
        "regulatory_region": "SK",
    }
    base.update(over)
    return base


async def _list_areas(rows):
    from backend.routes import service_areas as mod

    with patch("backend.routes.service_areas.db_supabase.get_rows", AsyncMock(return_value=rows)):
        return await mod.get_service_areas()


@pytest.mark.asyncio
class TestSafetyFieldsExposure:
    async def test_calgary_style_fully_populated_row_reaches_the_apps(self):
        rows = [
            _area(
                name="Calgary",
                emergency_number="911",
                safety_authority_name="City of Calgary 311",
                safety_authority_phone="311",
                safety_authority_url="https://www.calgary.ca/taxis-ride-share/tnc.html",
                safety_authority_hours="24/7",
            )
        ]
        out = (await _list_areas(rows))[0]

        assert out["safety_authority_name"] == "City of Calgary 311"
        # A 3-digit service code must survive intact -- this is the single most
        # important value the column will ever hold.
        assert out["safety_authority_phone"] == "311"
        assert out["safety_authority_hours"] == "24/7"

    async def test_sk_style_row_has_no_phone_so_the_app_renders_a_link_only(self):
        rows = [_area(safety_authority_name="SGI", safety_authority_url="https://sgi.sk.ca")]
        out = (await _list_areas(rows))[0]

        assert out["safety_authority_name"] == "SGI"
        assert out.get("safety_authority_phone") is None

    async def test_emergency_number_defaults_to_911_when_missing_or_blank(self):
        for row in (_area(), _area(emergency_number=""), _area(emergency_number=None)):
            out = (await _list_areas([row]))[0]
            assert out["emergency_number"] == "911", "the panel must never render a dead 911 button"

    async def test_driver_licensing_metadata_is_not_published(self):
        out = (await _list_areas([_area()]))[0]

        assert "regulatory_authority" not in out
        assert "regulatory_region" not in out
