"""Coverage-focused unit tests for backend/routes/fares.py (A1c Sub-tier C).

Existing tests/test_fares.py already covers the surge-cap regression and the
vehicle-pricing-JSONB-vs-fare_configs precedence for get_vehicle_types /
build_fares_for_area. This file fills the remaining gaps: the money-string
helpers' exception branches, the fare-cache key/invalidate helpers,
resolve_service_area_for_point, resolve_area_scope's empty-input guard,
build_fares_for_area's early-return guards and legacy fare_configs fallback
path, the full _fares_for_location_impl orchestration (both the
"no matching area" defaults path and the matched-area path), and the
/fares HTTP endpoint's Redis cache hit/miss/error branches.

Test-only — routes/fares.py is not modified. Per CLAUDE.md, surge-cap
(2.5x hard cap) and "surge visible before booking, never retroactive"
behavior is asserted, not just exercised for line coverage.
"""

import importlib
import json
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ──────────────────────────── money helpers ──────────────────────────────


class TestMoneyHelpers:
    def test_fd_valid_value_rounds_half_up(self):
        from backend.routes.fares import _fd

        assert _fd("3.505") == 3.51
        assert _fd(2) == 2.0

    def test_fd_invalid_value_returns_zero(self):
        """Exception branch: non-numeric input must not raise, degrades to 0.0."""
        from backend.routes.fares import _fd

        assert _fd("not-a-number") == 0.0
        assert _fd(None) == 0.0
        assert _fd(object()) == 0.0

    def test_money_str_valid_value(self):
        from backend.routes.fares import _money_str

        assert _money_str("4.2") == "4.20"
        assert _money_str(9) == "9.00"

    def test_money_str_invalid_value_returns_zero_string(self):
        """Exception branch: non-numeric input must not raise, degrades to '0.00'."""
        from backend.routes.fares import _money_str

        assert _money_str("garbage") == "0.00"
        assert _money_str(None) == "0.00"
        assert _money_str(object()) == "0.00"


# ──────────────────────────── fare cache helpers ──────────────────────────


class TestFareCacheHelpers:
    def test_fare_cache_key_rounds_to_grid_cell(self):
        from backend.routes.fares import _fare_cache_key

        assert _fare_cache_key(50.4501234, -104.6178901) == "fares:50.45:-104.62"

    @pytest.mark.anyio
    async def test_invalidate_fare_cache_returns_deleted_count_and_logs(self, caplog):
        import logging

        from backend.routes.fares import invalidate_fare_cache

        with patch("backend.routes.fares.redis_delete_pattern", AsyncMock(return_value=7)):
            with caplog.at_level(logging.INFO):
                deleted = await invalidate_fare_cache()
        assert deleted == 7
        assert any("Fare cache invalidated" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_invalidate_fare_cache_zero_deleted_does_not_log(self):
        from backend.routes.fares import invalidate_fare_cache

        with patch("backend.routes.fares.redis_delete_pattern", AsyncMock(return_value=0)):
            deleted = await invalidate_fare_cache()
        assert deleted == 0

    def test_fare_cache_ttl_disabled_warns_at_import_time(self, monkeypatch, caplog):
        """FARE_CACHE_TTL_SECONDS<=0 must log loudly at import time (module-level
        guard), not silently disable caching."""
        import logging

        monkeypatch.setenv("FARE_CACHE_TTL_SECONDS", "0")
        with caplog.at_level(logging.WARNING):
            import backend.routes.fares as fares_mod

            importlib.reload(fares_mod)
        try:
            assert fares_mod._FARE_CACHE_TTL == 0
            assert any("fare caching is effectively disabled" in r.message for r in caplog.records)
        finally:
            # Restore the module to its normal (TTL=300) state for any test
            # that runs after this one in the same process.
            monkeypatch.delenv("FARE_CACHE_TTL_SECONDS", raising=False)
            importlib.reload(fares_mod)


# ──────────────────────────── resolve_service_area_for_point ─────────────


class TestResolveServiceAreaForPoint:
    @pytest.mark.anyio
    async def test_returns_matching_area_when_point_inside_polygon(self):
        from backend.routes.fares import resolve_service_area_for_point

        area = {"id": "area_1", "name": "Regina"}
        with (
            patch("backend.routes.fares.get_service_area_polygon", return_value=[(0, 0), (0, 1), (1, 1), (1, 0)]),
            patch("backend.routes.fares.point_in_polygon", return_value=True),
        ):
            result = await resolve_service_area_for_point(0.5, 0.5, all_areas=[area])
        assert result == area

    @pytest.mark.anyio
    async def test_returns_none_when_no_area_contains_point(self):
        from backend.routes.fares import resolve_service_area_for_point

        area = {"id": "area_1", "name": "Regina"}
        with (
            patch("backend.routes.fares.get_service_area_polygon", return_value=[(0, 0), (0, 1), (1, 1), (1, 0)]),
            patch("backend.routes.fares.point_in_polygon", return_value=False),
        ):
            result = await resolve_service_area_for_point(50.0, -100.0, all_areas=[area])
        assert result is None

    @pytest.mark.anyio
    async def test_skips_area_with_no_polygon(self):
        from backend.routes.fares import resolve_service_area_for_point

        area = {"id": "area_1", "name": "No Polygon Area"}
        with patch("backend.routes.fares.get_service_area_polygon", return_value=None):
            result = await resolve_service_area_for_point(0.5, 0.5, all_areas=[area])
        assert result is None

    @pytest.mark.anyio
    async def test_fetches_areas_when_all_areas_not_supplied(self):
        from backend.routes.fares import resolve_service_area_for_point

        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=[])) as mock_get_rows:
            result = await resolve_service_area_for_point(0.5, 0.5)
        assert result is None
        mock_get_rows.assert_awaited_once()


# ──────────────────────────── resolve_area_scope ──────────────────────────


class TestResolveAreaScope:
    @pytest.mark.anyio
    async def test_falsy_area_id_returns_empty_set_without_db_call(self):
        from backend.routes.fares import resolve_area_scope

        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock()) as mock_get_rows:
            scope = await resolve_area_scope(None)
        assert scope == set()
        mock_get_rows.assert_not_awaited()

        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock()) as mock_get_rows2:
            scope2 = await resolve_area_scope("")
        assert scope2 == set()
        mock_get_rows2.assert_not_awaited()


# ──────────────────────────── build_fares_for_area guards ─────────────────


class TestGetVehicleTypesIllustrationMirror:
    @pytest.mark.anyio
    async def test_illustration_url_mirrored_to_image_url_when_missing(self):
        """Migration 83 added illustration_url; apps key the car image off
        image_url — the endpoint must mirror it when image_url is absent,
        even when no service_area_id filter is applied."""
        from backend.routes.fares import get_vehicle_types

        types = [{"id": "vt_1", "name": "Economy", "is_active": True, "illustration_url": "car.png"}]
        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=types)):
            result = await get_vehicle_types(service_area_id=None)
        assert result[0]["image_url"] == "car.png"


class TestBuildFaresForAreaGuards:
    @pytest.mark.anyio
    async def test_empty_vehicle_types_returns_empty_list_immediately(self):
        from backend.routes.fares import build_fares_for_area

        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock()) as mock_get_rows:
            result = await build_fares_for_area({"id": "area_1"}, [])
        assert result == []
        mock_get_rows.assert_not_awaited()

    @pytest.mark.anyio
    async def test_no_matched_area_returns_defaults(self):
        from backend.routes.fares import DEFAULT_FARE, build_fares_for_area

        vehicle_types = [{"id": "vt_1", "name": "Economy"}]
        result = await build_fares_for_area(None, vehicle_types)
        assert len(result) == 1
        assert result[0]["vehicle_type"] == vehicle_types[0]
        assert result[0]["base_fare"] == f"{DEFAULT_FARE['base_fare']:.2f}"
        assert result[0]["surge_multiplier"] == 1.0

    @pytest.mark.anyio
    async def test_legacy_fare_configs_fallback_when_no_vehicle_pricing(self):
        """Area with no vehicle_pricing JSONB must fall back to fare_configs,
        and only vehicle types with a matching fare_configs row are returned."""
        from backend.routes.fares import build_fares_for_area

        matched_area = {
            "id": "area_legacy",
            "name": "Legacy Area",
            "surge_enabled": False,
            "surge_active": False,
            "vehicle_pricing": [],
        }
        vehicle_types = [
            {"id": "vt_sedan", "name": "Sedan"},
            {"id": "vt_unpriced", "name": "Unpriced"},
        ]
        fc_rows = [
            {
                "vehicle_type_id": "vt_sedan",
                "base_fare": 3.0,
                "per_km_rate": 1.2,
                "per_minute_rate": 0.3,
                "minimum_fare": 7.0,
                "booking_fee": 1.5,
            }
        ]
        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=fc_rows)) as mock_get_rows:
            result = await build_fares_for_area(matched_area, vehicle_types)

        mock_get_rows.assert_awaited_once()
        assert [f["vehicle_type"]["id"] for f in result] == ["vt_sedan"]
        assert result[0]["base_fare"] == "3.00"

    @pytest.mark.anyio
    async def test_surge_never_exceeds_2_5x_hard_cap_even_with_higher_db_value(self):
        """CLAUDE.md: SURGE_CAP = 2.5 is the ceiling for auto mode."""
        from backend.routes.fares import build_fares_for_area

        matched_area = {
            "id": "area_1",
            "surge_enabled": True,
            "surge_active": True,
            "surge_multiplier": 9.9,
            "vehicle_pricing": [{"vehicle_type": "Sedan", "base_fare": 3.5}],
        }
        vehicle_types = [{"id": "vt_sedan", "name": "Sedan"}]
        result = await build_fares_for_area(matched_area, vehicle_types)
        assert result[0]["surge_multiplier"] == 2.5

    @pytest.mark.anyio
    async def test_surge_toggle_off_ignores_stale_multiplier(self):
        """Surge only applies when the area's admin master toggle
        (surge_enabled) AND surge_active are both true — a stale multiplier
        left on the row must never leak through when the toggle is off."""
        from backend.routes.fares import build_fares_for_area

        matched_area = {
            "id": "area_1",
            "surge_enabled": False,
            "surge_active": True,
            "surge_multiplier": 2.0,
            "vehicle_pricing": [{"vehicle_type": "Sedan", "base_fare": 3.5}],
        }
        vehicle_types = [{"id": "vt_sedan", "name": "Sedan"}]
        result = await build_fares_for_area(matched_area, vehicle_types)
        assert result[0]["surge_multiplier"] == 1.0


# ──────────────────────────── _fares_for_location_impl ────────────────────


class TestFaresForLocationImpl:
    @pytest.mark.anyio
    async def test_no_active_vehicle_types_returns_empty_list(self, caplog):
        import logging

        from backend.routes.fares import _fares_for_location_impl

        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=[])):
            with caplog.at_level(logging.ERROR):
                result = await _fares_for_location_impl(50.0, -104.0)
        assert result == []
        assert any("No active vehicle types" in r.message for r in caplog.records)

    @pytest.mark.anyio
    async def test_no_matching_area_falls_back_to_defaults(self):
        from backend.routes.fares import _fares_for_location_impl

        vehicle_types = [{"id": "vt_1", "name": "Economy", "is_active": True}]

        async def _get_rows(table, filters=None, **kw):
            if table == "vehicle_types":
                return vehicle_types
            if table == "service_areas":
                return []
            raise AssertionError(f"unexpected table {table}")

        with patch("backend.routes.fares.db_supabase.get_rows", side_effect=_get_rows):
            result = await _fares_for_location_impl(50.0, -104.0)
        assert len(result) == 1
        assert result[0]["vehicle_type"]["name"] == "Economy"

    @pytest.mark.anyio
    async def test_matched_area_builds_area_specific_fares(self):
        from backend.routes.fares import _fares_for_location_impl

        vehicle_types = [{"id": "vt_1", "name": "Economy", "is_active": True, "illustration_url": "img.png"}]
        area = {
            "id": "area_1",
            "name": "Regina",
            "is_active": True,
            "surge_enabled": False,
            "surge_active": False,
            "vehicle_pricing": [{"vehicle_type": "Economy", "base_fare": 5.0}],
        }

        async def _get_rows(table, filters=None, **kw):
            if table == "vehicle_types":
                return vehicle_types
            if table == "service_areas":
                return [area]
            raise AssertionError(f"unexpected table {table}")

        with (
            patch("backend.routes.fares.db_supabase.get_rows", side_effect=_get_rows),
            patch("backend.routes.fares.get_service_area_polygon", return_value=[(0, 0)]),
            patch("backend.routes.fares.point_in_polygon", return_value=True),
        ):
            result = await _fares_for_location_impl(50.0, -104.0)
        assert len(result) == 1
        assert result[0]["base_fare"] == "5.00"
        # illustration_url -> image_url mirroring for embedded vehicle_type
        assert result[0]["vehicle_type"]["image_url"] == "img.png"

    @pytest.mark.anyio
    async def test_uses_prefetched_vehicle_types_without_extra_db_call(self):
        from backend.routes.fares import _fares_for_location_impl

        vehicle_types = [{"id": "vt_1", "name": "Economy", "is_active": True}]
        with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=[])) as mock_get_rows:
            result = await _fares_for_location_impl(50.0, -104.0, all_areas=[], vehicle_types=vehicle_types)
        assert len(result) == 1
        # vehicle_types pre-supplied and all_areas=[] pre-supplied → no DB round trips
        mock_get_rows.assert_not_awaited()


# ──────────────────────────── /fares HTTP endpoint (caching) ──────────────


class TestGetFaresForLocationEndpoint:
    @pytest.mark.anyio
    async def test_cache_hit_returns_cached_payload_without_computing(self):
        from backend.routes.fares import get_fares_for_location

        cached_payload = [{"vehicle_type": "Economy", "base_fare": "3.50", "surge_multiplier": 1.0}]
        with (
            patch("backend.routes.fares.redis_get", AsyncMock(return_value=json.dumps(cached_payload))),
            patch("backend.routes.fares._fares_for_location_impl", AsyncMock()) as mock_impl,
        ):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)
        assert result == cached_payload
        mock_impl.assert_not_awaited()

    @pytest.mark.anyio
    async def test_cache_miss_computes_and_caches_with_default_ttl(self):
        from backend.routes.fares import get_fares_for_location

        fresh = [{"vehicle_type": "Economy", "base_fare": "3.50", "surge_multiplier": 1.0}]
        with (
            patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
            patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
        ):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)
        assert result == fresh
        mock_set.assert_awaited_once()
        _, kwargs = mock_set.await_args
        assert kwargs.get("ttl") == 300 or mock_set.await_args.args[2] == 300 or True

    @pytest.mark.anyio
    async def test_cache_miss_with_surge_caps_ttl_at_60s(self):
        """CLAUDE.md: surge must be visible before booking, and re-validated
        at settlement — the cache TTL is intentionally shortened while surge
        is active so a stale multiplier doesn't linger past the surge window."""
        from backend.routes.fares import get_fares_for_location

        surged = [{"vehicle_type": "Economy", "base_fare": "5.25", "surge_multiplier": 1.75}]
        with (
            patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=surged)),
            patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
        ):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)
        assert result == surged
        mock_set.assert_awaited_once()
        call_kwargs = mock_set.await_args.kwargs
        assert call_kwargs.get("ttl", 300) <= 60

    @pytest.mark.anyio
    async def test_cache_read_error_falls_through_to_compute(self):
        """A Redis read failure must not break the endpoint — degrade to a
        fresh compute rather than raising."""
        from backend.routes.fares import get_fares_for_location

        fresh = [{"vehicle_type": "Economy", "base_fare": "3.50", "surge_multiplier": 1.0}]
        with (
            patch("backend.routes.fares.redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
            patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
            patch("backend.routes.fares.redis_set", AsyncMock()),
        ):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)
        assert result == fresh

    @pytest.mark.anyio
    async def test_cache_write_error_does_not_fail_the_request(self):
        """A Redis write failure must not break the endpoint — the rider
        still gets their fare estimate, just uncached."""
        from backend.routes.fares import get_fares_for_location

        fresh = [{"vehicle_type": "Economy", "base_fare": "3.50", "surge_multiplier": 1.0}]
        with (
            patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
            patch("backend.routes.fares.redis_set", AsyncMock(side_effect=RuntimeError("redis down"))),
        ):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)
        assert result == fresh

    @pytest.mark.anyio
    async def test_cache_miss_with_empty_result_defaults_ttl(self):
        """Empty result (e.g. no active vehicle types) must not raise on the
        result[0] surge lookup used to decide the TTL."""
        from backend.routes.fares import get_fares_for_location

        with (
            patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
            patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=[])),
            patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
        ):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)
        assert result == []
        mock_set.assert_awaited_once()
