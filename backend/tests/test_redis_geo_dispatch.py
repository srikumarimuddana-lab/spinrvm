"""Tests for Redis GEOSPATIAL dispatch integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestGeoSearchDrivers:
    @pytest.mark.anyio
    async def test_nearby_drivers_from_redis_returns_candidates(self):
        mock_redis = AsyncMock()
        mock_redis.geosearch.return_value = [
            ["driver_1", "0.5", ("52.13", "-106.67")],
            ["driver_2", "1.2", ("52.14", "-106.68")],
        ]
        mock_redis.hgetall.side_effect = [
            {
                "lat": "52.13",
                "lng": "-106.67",
                "rating": "4.8",
                "is_wav": "0",
                "vehicle_class": "economy",
                "is_online": "1",
                "location_updated_at": "2026-05-27T10:00:00Z",
            },
            {
                "lat": "52.14",
                "lng": "-106.68",
                "rating": "4.5",
                "is_wav": "1",
                "vehicle_class": "xl",
                "is_online": "1",
                "location_updated_at": "2026-05-27T10:00:00Z",
            },
        ]

        with patch("backend.utils.redis_client._get_redis", return_value=mock_redis):
            from backend.utils.redis_client import geo_search_drivers

            result = await geo_search_drivers(52.13, -106.67, 10.0)

        assert result is not None
        assert len(result) == 2
        assert result[0]["id"] == "driver_1"
        assert result[1]["is_wav"] is True

    @pytest.mark.anyio
    async def test_redis_fallback_on_unavailable(self):
        with patch("backend.utils.redis_client._get_redis", return_value=None):
            from backend.utils.redis_client import geo_search_drivers

            result = await geo_search_drivers(52.13, -106.67, 10.0)

        assert result is None

    @pytest.mark.anyio
    async def test_offline_driver_removed_from_geo_index(self):
        mock_redis = AsyncMock()

        with patch("backend.utils.redis_client._get_redis", return_value=mock_redis):
            from backend.utils.redis_client import geo_remove_driver

            await geo_remove_driver("driver_1")

        mock_redis.zrem.assert_called_once_with("spinr:driver_locations", "driver_1")
        mock_redis.delete.assert_called_once_with("spinr:driver:driver_1")
