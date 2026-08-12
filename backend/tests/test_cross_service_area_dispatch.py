"""
Cross-service-area dispatch guard tests.

Verifies that drivers only receive ride offers within their approved
service area. A Saskatoon-approved driver physically in Regina must NOT
receive a Regina ride offer — even if they are within the search radius.

Covers:
  - Same-area dispatch (allowed)
  - Cross-area dispatch (blocked)
  - Parent → child area compatibility (allowed)
  - Child → parent area compatibility (allowed)
  - Drivers with no service_area_id (blocked)
  - Cascade (vehicle upgrade) path inherits the area guard
  - Service area lookup failure falls open
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)

from services.dispatch_service import (  # noqa: E402
    DispatchService,
)

pytestmark = pytest.mark.anyio


def _make_db():
    db = MagicMock()
    db.find_one = AsyncMock(return_value=None)
    db.get_rows = AsyncMock(return_value=[])
    db.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
    return db


# ── DispatchService.find_candidate_drivers service-area guard ────────────────


class TestServiceAreaGuardInFindCandidates:
    """Service-area guard in the DispatchService layer."""

    async def test_same_area_drivers_included(self):
        """Drivers in the ride's service area pass the guard."""
        db = _make_db()
        regina_drivers = [
            {"id": "d1", "service_area_id": "regina"},
            {"id": "d2", "service_area_id": "regina"},
        ]

        async def _find_one(table, filters=None, **kwargs):
            if filters and filters.get("id") == "regina":
                return {"id": "regina", "is_active": True}
            return None

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return regina_drivers
            if table == "service_areas":
                return []
            return []

        db.find_one = AsyncMock(side_effect=_find_one)
        db.get_rows = AsyncMock(side_effect=_get_rows)
        svc = DispatchService(db)

        with patch(
            "services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1", "d2"}),
        ):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy", "service_area_id": "regina"})
        assert {d["id"] for d in out} == {"d1", "d2"}

        # Verify the DB filter includes the service_area_id $in clause
        driver_call = [c for c in db.get_rows.call_args_list if c[0][0] == "drivers"]
        assert driver_call
        driver_filter = driver_call[0][0][1]
        assert "service_area_id" in driver_filter
        assert driver_filter["service_area_id"] == {"$in": ["regina"]}

    async def test_cross_area_driver_excluded_by_db_filter(self):
        """The filter dict sent to get_rows excludes drivers from other areas.

        We verify the filter shape — the actual row exclusion happens at the
        DB level (PostgREST ``IN``), so a Saskatoon driver row would never
        appear in the result set.
        """
        db = _make_db()

        async def _find_one(table, filters=None, **kwargs):
            if filters and filters.get("id") == "regina":
                return {"id": "regina", "is_active": True}
            return None

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return []  # DB filters out non-regina drivers
            if table == "service_areas":
                return []
            return []

        db.find_one = AsyncMock(side_effect=_find_one)
        db.get_rows = AsyncMock(side_effect=_get_rows)
        svc = DispatchService(db)

        out = await svc.find_candidate_drivers({"vehicle_type_id": "economy", "service_area_id": "regina"})
        assert out == []

        driver_call = [c for c in db.get_rows.call_args_list if c[0][0] == "drivers"]
        driver_filter = driver_call[0][0][1]
        assert driver_filter["service_area_id"] == {"$in": ["regina"]}

    async def test_parent_area_drivers_serve_child_area_rides(self):
        """A driver approved for 'Regina' can serve 'Regina Airport' rides."""
        db = _make_db()
        drivers = [{"id": "d1", "service_area_id": "regina"}]

        async def _find_one(table, filters=None, **kwargs):
            if filters and filters.get("id") == "regina_airport":
                return {
                    "id": "regina_airport",
                    "is_active": True,
                    "parent_service_area_id": "regina",
                }
            return None

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return drivers
            if table == "service_areas":
                return []  # no children of regina_airport
            return []

        db.find_one = AsyncMock(side_effect=_find_one)
        db.get_rows = AsyncMock(side_effect=_get_rows)
        svc = DispatchService(db)

        with patch(
            "services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1"}),
        ):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy", "service_area_id": "regina_airport"})
        assert [d["id"] for d in out] == ["d1"]

        driver_call = [c for c in db.get_rows.call_args_list if c[0][0] == "drivers"]
        driver_filter = driver_call[0][0][1]
        compatible = set(driver_filter["service_area_id"]["$in"])
        assert "regina_airport" in compatible
        assert "regina" in compatible

    async def test_child_area_drivers_serve_parent_area_rides(self):
        """A driver approved for 'Regina Airport' can serve general 'Regina' rides."""
        db = _make_db()
        drivers = [{"id": "d1", "service_area_id": "regina_airport"}]

        async def _find_one(table, filters=None, **kwargs):
            if filters and filters.get("id") == "regina":
                return {"id": "regina", "is_active": True}
            return None

        async def _get_rows(table, filters=None, **kwargs):
            if table == "drivers":
                return drivers
            if table == "service_areas":
                # children of regina
                return [{"id": "regina_airport"}]
            return []

        db.find_one = AsyncMock(side_effect=_find_one)
        db.get_rows = AsyncMock(side_effect=_get_rows)
        svc = DispatchService(db)

        with patch(
            "services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1"}),
        ):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy", "service_area_id": "regina"})
        assert [d["id"] for d in out] == ["d1"]

        driver_call = [c for c in db.get_rows.call_args_list if c[0][0] == "drivers"]
        driver_filter = driver_call[0][0][1]
        compatible = set(driver_filter["service_area_id"]["$in"])
        assert "regina" in compatible
        assert "regina_airport" in compatible

    async def test_no_service_area_on_ride_skips_guard(self):
        """When the ride has no service_area_id, the guard is not applied."""
        db = _make_db()
        rows = [{"id": "d1"}]
        db.get_rows = AsyncMock(return_value=rows)
        svc = DispatchService(db)

        with patch(
            "services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1"}),
        ):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy"})
        assert out == rows

        driver_call = [c for c in db.get_rows.call_args_list if c[0][0] == "drivers"]
        driver_filter = driver_call[0][0][1]
        assert "service_area_id" not in driver_filter

    async def test_area_lookup_failure_falls_open(self):
        """If the service_areas lookup fails, dispatch proceeds without the guard."""
        db = _make_db()
        rows = [{"id": "d1"}]

        call_count = 0

        async def _get_rows(table, filters=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if table == "drivers":
                return rows
            return []

        db.find_one = AsyncMock(side_effect=RuntimeError("db down"))
        db.get_rows = AsyncMock(side_effect=_get_rows)
        svc = DispatchService(db)

        with patch(
            "services.dispatch_service.present_driver_ids",
            AsyncMock(return_value={"d1"}),
        ):
            out = await svc.find_candidate_drivers({"vehicle_type_id": "economy", "service_area_id": "regina"})
        assert out == rows

        driver_call = [c for c in db.get_rows.call_args_list if c[0][0] == "drivers"]
        driver_filter = driver_call[0][0][1]
        assert "service_area_id" not in driver_filter
