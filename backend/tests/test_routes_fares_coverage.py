"""Coverage tests for backend/routes/fares.py (A1c Sub-tier C).

Test-only change — no application code modified. Written by reading the
source; pytest was NOT run against this file (per task instructions, the
full suite runs once at the end by someone else).

Covers: money-string/float rounding helpers and their exception paths,
the FARE_CACHE_TTL_SECONDS<=0 startup warning, the fare cache key/
invalidate helpers, /vehicle-types filtering (vehicle_pricing JSONB +
legacy fare_configs fallback + image_url mirroring), resolve_service_area_
for_point, resolve_area_scope's falsy-input short-circuit, build_fares_for_
area's empty/no-match/legacy-fallback/skip branches, _fares_for_location_impl,
and the /fares HTTP handler's Redis cache hit/miss/error/TTL-capping paths.

Fixed (2026-08-03, application code change — see
docs/change-log/2026-08-03-a1c-found-not-fixed-bugfixes.md, Entry 3):
`build_fares_for_area` (backend/routes/fares.py) previously did
    min(matched_area.get("surge_multiplier", 1.0), SURGE_CAP)
`dict.get(key, default)` only substitutes the default when the key is
*absent* — a service_areas row with surge_multiplier explicitly stored as
SQL NULL (Python None) while surge_enabled=True and surge_active=True
raised `TypeError: '<' not supported between instances of 'NoneType' and
'float'` inside min(), a 500 on the public /fares endpoint for that
service area. Now does `matched_area.get("surge_multiplier") or 1.0`,
degrading to 1.0x instead of crashing. See
`test_build_fares_for_area_null_surge_multiplier_degrades_to_1x` below.
"""

import importlib
import json
import logging
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ── _fd / _money_str exception paths ────────────────────────────────────


async def test_fd_returns_zero_for_unparseable_value():
    from backend.routes.fares import _fd

    assert _fd("not-a-number") == 0.0
    assert _fd(None) == 0.0
    assert _fd(object()) == 0.0


async def test_fd_rounds_valid_value():
    from backend.routes.fares import _fd

    assert _fd("3.456") == 3.46


async def test_money_str_returns_zero_string_for_unparseable_value():
    from backend.routes.fares import _money_str

    assert _money_str("garbage") == "0.00"
    assert _money_str(None) == "0.00"


async def test_money_str_formats_valid_value():
    from backend.routes.fares import _money_str

    assert _money_str("4.2") == "4.20"


# ── module-level FARE_CACHE_TTL_SECONDS<=0 warning ──────────────────────


async def test_fare_cache_ttl_zero_logs_warning(monkeypatch, caplog):
    """Reload the module with FARE_CACHE_TTL_SECONDS=0 to hit the
    disabled-cache warning branch executed at import time."""
    monkeypatch.setenv("FARE_CACHE_TTL_SECONDS", "0")
    import backend.routes.fares as mod

    try:
        with caplog.at_level(logging.WARNING):
            importlib.reload(mod)
        assert mod._FARE_CACHE_TTL == 0
        assert any("fare caching is effectively disabled" in r.message for r in caplog.records)
    finally:
        monkeypatch.delenv("FARE_CACHE_TTL_SECONDS", raising=False)
        importlib.reload(mod)


# ── _fare_cache_key / invalidate_fare_cache ─────────────────────────────


async def test_fare_cache_key_rounds_to_two_decimals():
    from backend.routes.fares import _fare_cache_key

    assert _fare_cache_key(50.123456, -104.987654) == "fares:50.12:-104.99"


async def test_invalidate_fare_cache_logs_when_keys_removed(caplog):
    from backend.routes.fares import invalidate_fare_cache

    with patch("backend.routes.fares.redis_delete_pattern", AsyncMock(return_value=3)):
        with caplog.at_level(logging.INFO):
            deleted = await invalidate_fare_cache()
    assert deleted == 3
    assert any("Fare cache invalidated: 3 keys removed" in r.message for r in caplog.records)


async def test_invalidate_fare_cache_no_log_when_nothing_removed(caplog):
    from backend.routes.fares import invalidate_fare_cache

    with patch("backend.routes.fares.redis_delete_pattern", AsyncMock(return_value=0)):
        with caplog.at_level(logging.INFO):
            deleted = await invalidate_fare_cache()
    assert deleted == 0
    assert not any("Fare cache invalidated" in r.message for r in caplog.records)


# ── get_vehicle_types ────────────────────────────────────────────────────


async def test_get_vehicle_types_no_area_filter_mirrors_image_url():
    from backend.routes.fares import get_vehicle_types

    all_types = [
        {"id": "vt1", "name": "Economy", "is_active": True, "illustration_url": "http://x/img.png"},
        {"id": "vt2", "name": "XL", "is_active": True, "illustration_url": "http://x/img2.png", "image_url": "kept"},
    ]
    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=all_types)):
        types = await get_vehicle_types(service_area_id=None)

    assert types[0]["image_url"] == "http://x/img.png"
    # image_url already set — must not be overwritten by illustration_url
    assert types[1]["image_url"] == "kept"


async def test_get_vehicle_types_area_with_no_pricing_and_no_legacy_returns_empty():
    from backend.routes.fares import get_vehicle_types

    all_types = [{"id": "vt1", "name": "Economy", "is_active": True}]

    async def _get_rows(table, filters=None, **kw):
        if table == "vehicle_types":
            return all_types
        if table == "service_areas":
            return [{"id": "area_x", "vehicle_pricing": []}]
        if table == "fare_configs":
            return []
        raise AssertionError(table)

    with patch("backend.routes.fares.db_supabase.get_rows", side_effect=_get_rows):
        types = await get_vehicle_types(service_area_id="area_x")

    assert types == []


async def test_get_vehicle_types_area_lookup_returns_no_rows():
    """areas list empty (bad service_area_id) — `.get("vehicle_pricing")` on {} degrades cleanly."""
    from backend.routes.fares import get_vehicle_types

    all_types = [{"id": "vt1", "name": "Economy", "is_active": True}]

    async def _get_rows(table, filters=None, **kw):
        if table == "vehicle_types":
            return all_types
        if table == "service_areas":
            return []
        if table == "fare_configs":
            return []
        raise AssertionError(table)

    with patch("backend.routes.fares.db_supabase.get_rows", side_effect=_get_rows):
        types = await get_vehicle_types(service_area_id="area_missing")

    assert types == []


# ── resolve_service_area_for_point ──────────────────────────────────────


_SQUARE_POLY = [
    {"lat": 0.0, "lng": 0.0},
    {"lat": 0.0, "lng": 10.0},
    {"lat": 10.0, "lng": 10.0},
    {"lat": 10.0, "lng": 0.0},
]


async def test_resolve_service_area_for_point_fetches_when_all_areas_omitted():
    from backend.routes.fares import resolve_service_area_for_point

    area = {"id": "area1", "polygon": _SQUARE_POLY}
    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=[area])) as mock_get_rows:
        result = await resolve_service_area_for_point(5.0, 5.0)

    mock_get_rows.assert_awaited_once()
    assert result == area


async def test_resolve_service_area_for_point_uses_passed_all_areas_without_fetch():
    from backend.routes.fares import resolve_service_area_for_point

    area = {"id": "area1", "polygon": _SQUARE_POLY}
    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock()) as mock_get_rows:
        result = await resolve_service_area_for_point(5.0, 5.0, all_areas=[area])

    mock_get_rows.assert_not_awaited()
    assert result == area


async def test_resolve_service_area_for_point_returns_none_when_no_polygon_matches():
    from backend.routes.fares import resolve_service_area_for_point

    area = {"id": "area1", "polygon": _SQUARE_POLY}
    # Point way outside the square.
    result = await resolve_service_area_for_point(500.0, 500.0, all_areas=[area])
    assert result is None


async def test_resolve_service_area_for_point_skips_area_with_no_polygon():
    from backend.routes.fares import resolve_service_area_for_point

    area_no_poly = {"id": "area_no_poly"}
    area_match = {"id": "area_match", "polygon": _SQUARE_POLY}
    result = await resolve_service_area_for_point(5.0, 5.0, all_areas=[area_no_poly, area_match])
    assert result == area_match


# ── resolve_area_scope falsy-input branch (not covered by test_fares.py) ─


async def test_resolve_area_scope_returns_empty_set_for_falsy_area_id():
    from backend.routes.fares import resolve_area_scope

    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock()) as mock_get_rows:
        scope = await resolve_area_scope(None)

    assert scope == set()
    mock_get_rows.assert_not_awaited()


async def test_resolve_area_scope_returns_empty_set_for_empty_string():
    from backend.routes.fares import resolve_area_scope

    scope = await resolve_area_scope("")
    assert scope == set()


# ── build_fares_for_area ─────────────────────────────────────────────────


async def test_build_fares_for_area_no_vehicle_types_returns_empty_list():
    from backend.routes.fares import build_fares_for_area

    result = await build_fares_for_area({"id": "area1"}, [])
    assert result == []


async def test_build_fares_for_area_no_matched_area_returns_defaults():
    from backend.routes.fares import build_fares_for_area
    from backend.services.fare_service import DEFAULT_FARE

    vehicle_types = [{"id": "vt1", "name": "Economy"}]
    result = await build_fares_for_area(None, vehicle_types)

    assert len(result) == 1
    assert result[0]["vehicle_type"] == {"id": "vt1", "name": "Economy"}
    assert result[0]["base_fare"] == f"{DEFAULT_FARE['base_fare']:.2f}"
    assert result[0]["surge_multiplier"] == 1.0


async def test_build_fares_for_area_surge_disabled_stays_1x_even_if_active_flag_set():
    """surge_enabled False must gate off surge regardless of surge_active/multiplier."""
    from backend.routes.fares import build_fares_for_area

    matched_area = {
        "id": "area1",
        "surge_enabled": False,
        "surge_active": True,
        "surge_multiplier": 3.0,
        "vehicle_pricing": [
            {"vehicle_type": "sedan", "base_fare": 3.5, "per_km": 1.5, "per_min": 0.25, "min_fare": 8, "booking_fee": 2}
        ],
    }
    vehicle_types = [{"id": "vt_sedan", "name": "sedan"}]
    fares = await build_fares_for_area(matched_area, vehicle_types)
    assert fares[0]["surge_multiplier"] == 1.0


async def test_build_fares_for_area_falls_back_to_legacy_fare_configs():
    """No vehicle_pricing JSONB — must fetch + use legacy fare_configs rows."""
    from backend.routes.fares import build_fares_for_area

    matched_area = {"id": "area1", "vehicle_pricing": []}
    vehicle_types = [{"id": "vt_sedan", "name": "sedan"}, {"id": "vt_unpriced", "name": "unpriced"}]
    legacy_rows = [
        {
            "vehicle_type_id": "vt_sedan",
            "base_fare": 3.5,
            "per_km_rate": 1.5,
            "per_minute_rate": 0.25,
            "minimum_fare": 8,
            "booking_fee": 2,
        }
    ]

    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=legacy_rows)) as mock_get_rows:
        fares = await build_fares_for_area(matched_area, vehicle_types)

    mock_get_rows.assert_awaited_once()
    # Only the priced vehicle type is returned — unpriced one is skipped.
    assert [f["vehicle_type"]["id"] for f in fares] == ["vt_sedan"]
    assert fares[0]["base_fare"] == "3.50"


async def test_build_fares_for_area_null_surge_multiplier_degrades_to_1x():
    """Fixed (2026-08-03), see module docstring.

    A service_areas row with surge_multiplier explicitly NULL (not missing)
    plus surge_enabled=True and surge_active=True previously made
    `min(matched_area.get("surge_multiplier", 1.0), SURGE_CAP)` compare
    None < float, raising TypeError (a 500 on the public /fares endpoint).
    Now degrades to a 1.0x multiplier instead of crashing.
    """
    from backend.routes.fares import build_fares_for_area

    matched_area = {
        "id": "area_null_surge",
        "surge_enabled": True,
        "surge_active": True,
        "surge_multiplier": None,  # explicit NULL from DB, not absent key
        "vehicle_pricing": [
            {"vehicle_type": "sedan", "base_fare": 3.5, "per_km": 1.5, "per_min": 0.25, "min_fare": 8, "booking_fee": 2}
        ],
    }
    vehicle_types = [{"id": "vt_sedan", "name": "sedan"}]

    fares = await build_fares_for_area(matched_area, vehicle_types)

    assert len(fares) == 1
    assert fares[0]["surge_multiplier"] == 1.0


# ── _fares_for_location_impl ─────────────────────────────────────────────


async def test_fares_for_location_impl_fetches_vehicle_types_when_omitted():
    from backend.routes.fares import _fares_for_location_impl

    vt = [{"id": "vt1", "name": "Economy", "illustration_url": "http://x/i.png"}]
    with (
        patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=vt)) as mock_get_rows,
        patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock(return_value=None)),
    ):
        result = await _fares_for_location_impl(50.0, -104.0)

    mock_get_rows.assert_awaited_once()
    assert result  # defaults built
    # image_url mirrored onto the vehicle type dict that flows into defaults.
    assert vt[0]["image_url"] == "http://x/i.png"


async def test_fares_for_location_impl_no_active_vehicle_types_logs_error(caplog):
    from backend.routes.fares import _fares_for_location_impl

    with patch("backend.routes.fares.db_supabase.get_rows", AsyncMock(return_value=[])):
        with caplog.at_level(logging.ERROR):
            result = await _fares_for_location_impl(50.0, -104.0)

    assert result == []
    assert any("No active vehicle types found" in r.message for r in caplog.records)


async def test_fares_for_location_impl_no_matching_area_uses_defaults(caplog):
    from backend.routes.fares import _fares_for_location_impl

    vt = [{"id": "vt1", "name": "Economy"}]
    with patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock(return_value=None)):
        with caplog.at_level(logging.INFO):
            result = await _fares_for_location_impl(50.0, -104.0, all_areas=[], vehicle_types=vt)

    assert result[0]["vehicle_type"] == {"id": "vt1", "name": "Economy"}
    assert any("using defaults" in r.message for r in caplog.records)


async def test_fares_for_location_impl_matched_area_delegates_to_build_fares(caplog):
    from backend.routes.fares import _fares_for_location_impl

    vt = [{"id": "vt1", "name": "sedan"}]
    matched_area = {
        "id": "area1",
        "name": "Regina",
        "vehicle_pricing": [
            {"vehicle_type": "sedan", "base_fare": 3.5, "per_km": 1.5, "per_min": 0.25, "min_fare": 8, "booking_fee": 2}
        ],
    }
    with patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock(return_value=matched_area)):
        with caplog.at_level(logging.INFO):
            result = await _fares_for_location_impl(50.0, -104.0, all_areas=[matched_area], vehicle_types=vt)

    assert result[0]["vehicle_type"]["id"] == "vt1"
    assert any("Matched service area 'Regina'" in r.message for r in caplog.records)


async def test_fares_for_location_impl_matched_area_without_name_logs_id(caplog):
    """matched_area.get('name', matched_area['id']) fallback branch."""
    from backend.routes.fares import _fares_for_location_impl

    vt = [{"id": "vt1", "name": "sedan"}]
    matched_area = {
        "id": "area_no_name",
        "vehicle_pricing": [
            {"vehicle_type": "sedan", "base_fare": 3.5, "per_km": 1.5, "per_min": 0.25, "min_fare": 8, "booking_fee": 2}
        ],
    }
    with patch("backend.routes.fares.resolve_service_area_for_point", AsyncMock(return_value=matched_area)):
        with caplog.at_level(logging.INFO):
            await _fares_for_location_impl(50.0, -104.0, all_areas=[matched_area], vehicle_types=vt)

    assert any("Matched service area 'area_no_name'" in r.message for r in caplog.records)


# ── get_fares_for_location (HTTP handler + Redis cache) ──────────────────


async def test_get_fares_for_location_cache_hit_returns_cached_json():
    from backend.routes.fares import get_fares_for_location

    cached_payload = [{"vehicle_type": "vt1", "surge_multiplier": 1.0}]
    with (
        patch("backend.routes.fares.redis_get", AsyncMock(return_value=json.dumps(cached_payload))) as mock_get,
        patch("backend.routes.fares._fares_for_location_impl", AsyncMock()) as mock_impl,
    ):
        result = await get_fares_for_location(lat=50.0, lng=-104.0)

    mock_get.assert_awaited_once()
    mock_impl.assert_not_awaited()
    assert result == cached_payload


async def test_get_fares_for_location_cache_read_error_falls_through_to_compute(caplog):
    from backend.routes.fares import get_fares_for_location

    fresh = [{"vehicle_type": "vt1", "surge_multiplier": 1.0}]
    with (
        patch("backend.routes.fares.redis_get", AsyncMock(side_effect=RuntimeError("redis down"))),
        patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
        patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
    ):
        with caplog.at_level(logging.WARNING):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)

    assert result == fresh
    mock_set.assert_awaited_once()
    assert any("Fare cache read error" in r.message for r in caplog.records)


async def test_get_fares_for_location_caches_fresh_result_full_ttl_when_no_surge():
    import backend.routes.fares as mod

    fresh = [{"vehicle_type": "vt1", "surge_multiplier": 1.0}]
    with (
        patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
        patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
        patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
    ):
        result = await mod.get_fares_for_location(lat=50.0, lng=-104.0)

    assert result == fresh
    args = mock_set.call_args.args
    assert args[0].startswith("fares:")
    # ttl kwarg should equal the full configured TTL (no surge active) — not
    # the surge-capped 60s branch.
    assert mock_set.call_args.kwargs["ttl"] == mod._FARE_CACHE_TTL


async def test_get_fares_for_location_caches_with_capped_ttl_when_surge_active():
    from backend.routes.fares import get_fares_for_location

    fresh = [{"vehicle_type": "vt1", "surge_multiplier": 2.0}]
    with (
        patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
        patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
        patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
    ):
        result = await get_fares_for_location(lat=50.0, lng=-104.0)

    assert result == fresh
    assert mock_set.call_args.kwargs["ttl"] <= 60


async def test_get_fares_for_location_empty_result_defaults_surge_to_1_and_caches():
    from backend.routes.fares import get_fares_for_location

    with (
        patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
        patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=[])),
        patch("backend.routes.fares.redis_set", AsyncMock()) as mock_set,
    ):
        result = await get_fares_for_location(lat=50.0, lng=-104.0)

    assert result == []
    mock_set.assert_awaited_once()


async def test_get_fares_for_location_cache_write_error_is_swallowed_with_warning(caplog):
    from backend.routes.fares import get_fares_for_location

    fresh = [{"vehicle_type": "vt1", "surge_multiplier": 1.0}]
    with (
        patch("backend.routes.fares.redis_get", AsyncMock(return_value=None)),
        patch("backend.routes.fares._fares_for_location_impl", AsyncMock(return_value=fresh)),
        patch("backend.routes.fares.redis_set", AsyncMock(side_effect=RuntimeError("redis write down"))),
    ):
        with caplog.at_level(logging.WARNING):
            result = await get_fares_for_location(lat=50.0, lng=-104.0)

    assert result == fresh
    assert any("Could not cache fare result" in r.message for r in caplog.records)
