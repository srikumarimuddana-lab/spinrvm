"""
Tests for admin vehicle-type illustration_url persistence.

The upload endpoint itself hits Supabase Storage which we don't mock at
that layer here; these tests verify the simpler CRUD path: the
illustration_url field round-trips through create + update + DB write.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestVehicleTypeIllustrationPersistence:
    @pytest.mark.asyncio
    async def test_create_persists_illustration_url(self):
        from backend.routes.admin import vehicle_fleet

        insert_mock = AsyncMock(return_value={"id": "vt_1"})

        with (
            patch("backend.routes.admin.vehicle_fleet.db_supabase.insert_one", insert_mock),
            patch("backend.routes.admin.vehicle_fleet.invalidate_fare_cache", AsyncMock()),
        ):
            result = await vehicle_fleet.admin_create_vehicle_type(
                vehicle_fleet.VehicleTypeCreateRequest(
                    name="Standard",
                    illustration_url="https://example.com/standard.png",
                )
            )

        assert result == {"type_id": "vt_1"}
        insert_mock.assert_called_once()
        doc = insert_mock.call_args.args[1]
        assert doc["name"] == "Standard"
        assert doc["illustration_url"] == "https://example.com/standard.png"

    @pytest.mark.asyncio
    async def test_update_persists_illustration_url(self):
        from backend.routes.admin import vehicle_fleet

        update_mock = AsyncMock()

        with (
            patch("backend.routes.admin.vehicle_fleet.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.vehicle_fleet.invalidate_fare_cache", AsyncMock()),
        ):
            await vehicle_fleet.admin_update_vehicle_type(
                "vt_1",
                vehicle_fleet.VehicleTypeUpdateRequest(
                    illustration_url="https://example.com/standard-v2.png",
                ),
            )

        update_mock.assert_called_once()
        _, filt, patch_doc = update_mock.call_args.args
        assert filt == {"id": "vt_1"}
        assert patch_doc["illustration_url"] == "https://example.com/standard-v2.png"

    @pytest.mark.asyncio
    async def test_empty_string_clears_illustration_url(self):
        """Admin can clear the override by passing "" — stored as None."""
        from backend.routes.admin import vehicle_fleet

        update_mock = AsyncMock()

        with (
            patch("backend.routes.admin.vehicle_fleet.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.vehicle_fleet.invalidate_fare_cache", AsyncMock()),
        ):
            await vehicle_fleet.admin_update_vehicle_type(
                "vt_1",
                vehicle_fleet.VehicleTypeUpdateRequest(illustration_url=""),
            )

        update_mock.assert_called_once()
        _, _, patch_doc = update_mock.call_args.args
        assert patch_doc["illustration_url"] is None

    @pytest.mark.asyncio
    async def test_update_without_illustration_url_leaves_it_alone(self):
        """Omitting the field must NOT overwrite — supports partial updates."""
        from backend.routes.admin import vehicle_fleet

        update_mock = AsyncMock()

        with (
            patch("backend.routes.admin.vehicle_fleet.db_supabase.update_one", update_mock),
            patch("backend.routes.admin.vehicle_fleet.invalidate_fare_cache", AsyncMock()),
        ):
            await vehicle_fleet.admin_update_vehicle_type(
                "vt_1",
                vehicle_fleet.VehicleTypeUpdateRequest(name="Standard renamed"),
            )

        update_mock.assert_called_once()
        _, _, patch_doc = update_mock.call_args.args
        assert "illustration_url" not in patch_doc
