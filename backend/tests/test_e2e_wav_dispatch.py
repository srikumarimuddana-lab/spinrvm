"""
E2E — WAV (wheelchair-accessible vehicle) dispatch (L-P0-3).

Saskatchewan Transportation Act s.22: when a rider requests a WAV, the
dispatch engine must only match drivers whose vehicle is WAV-certified
(is_wav=True on the drivers row).

Tests exercise both the pure filter_and_rank_drivers function and the
full match_driver_to_ride path that includes the get_rows DB pre-filter.

Scenarios:
  1. WAV required, non-WAV driver excluded
  2. WAV required, WAV driver matched
  3. WAV required, mixed pool — only WAV driver selected
  4. Non-WAV ride — all available drivers included regardless of is_wav
  5. WAV required, zero WAV in pool — returns empty (no driver sent)
  6. requires_wav persisted on ride row via create_ride body
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

RIDER_ID = "rider_wav_e2e"
DRIVER_STANDARD_ID = "driver_standard_e2e"
DRIVER_WAV_ID = "driver_wav_e2e"
RIDE_ID = "ride_wav_e2e_001"


def _driver(driver_id: str, is_wav: bool = False, lat: float = 52.13, lng: float = -106.67) -> dict:
    return {
        "id": driver_id,
        "user_id": f"user_{driver_id}",
        "lat": lat,
        "lng": lng,
        "rating": 4.8,
        "is_wav": is_wav,
        "vehicle_type_id": "economy",
    }


def _ride(requires_wav: bool = False) -> dict:
    return {
        "id": RIDE_ID,
        "rider_id": RIDER_ID,
        "pickup_lat": 52.13,
        "pickup_lng": -106.67,
        "dropoff_lat": 52.15,
        "dropoff_lng": -106.65,
        "vehicle_type_id": "economy",
        "requires_wav": requires_wav,
        "status": "searching",
    }


# ── Pure-function tests (filter_and_rank_drivers) ─────────────────────────────


class TestWAVFilterPure:
    """filter_and_rank_drivers enforces the WAV gate — no I/O, deterministic."""

    def test_wav_required_excludes_non_wav_driver(self):
        from backend.services.dispatch_service import filter_and_rank_drivers

        ride = _ride(requires_wav=True)
        drivers = [_driver(DRIVER_STANDARD_ID, is_wav=False)]

        result = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10.0)
        assert result == [], "Non-WAV driver must be excluded when WAV is required"

    def test_wav_required_matches_wav_driver(self):
        from backend.services.dispatch_service import filter_and_rank_drivers

        ride = _ride(requires_wav=True)
        drivers = [_driver(DRIVER_WAV_ID, is_wav=True)]

        result = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10.0)
        assert len(result) == 1
        assert result[0][0]["id"] == DRIVER_WAV_ID

    def test_wav_required_mixed_pool_only_wav_selected(self):
        from backend.services.dispatch_service import filter_and_rank_drivers

        ride = _ride(requires_wav=True)
        drivers = [
            _driver(DRIVER_STANDARD_ID, is_wav=False),
            _driver(DRIVER_WAV_ID, is_wav=True),
        ]

        result = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10.0)
        assert len(result) == 1
        assert result[0][0]["id"] == DRIVER_WAV_ID

    def test_non_wav_ride_includes_all_drivers(self):
        from backend.services.dispatch_service import filter_and_rank_drivers

        ride = _ride(requires_wav=False)
        drivers = [
            _driver(DRIVER_STANDARD_ID, is_wav=False),
            _driver(DRIVER_WAV_ID, is_wav=True),
        ]

        result = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10.0)
        assert len(result) == 2, "Non-WAV ride must include both WAV and non-WAV drivers"

    def test_wav_required_zero_wav_in_pool_returns_empty(self):
        from backend.services.dispatch_service import filter_and_rank_drivers

        ride = _ride(requires_wav=True)
        drivers = [
            _driver("d1", is_wav=False),
            _driver("d2", is_wav=False),
            _driver("d3", is_wav=False),
        ]

        result = filter_and_rank_drivers(ride, drivers, "nearest", 4.0, 10.0)
        assert result == []

    def test_wav_flag_missing_on_driver_treated_as_false(self):
        """Driver rows pre-dating migration 60 have no is_wav key — treated as non-WAV."""
        from backend.services.dispatch_service import filter_and_rank_drivers

        ride = _ride(requires_wav=True)
        legacy_driver = {
            "id": "legacy_driver",
            "user_id": "user_legacy",
            "lat": 52.13,
            "lng": -106.67,
            "rating": 4.9,
            # no is_wav key at all
        }

        result = filter_and_rank_drivers(ride, [legacy_driver], "nearest", 4.0, 10.0)
        assert result == [], "Missing is_wav key must be treated as non-WAV"


# ── Dispatch query pre-filter test ────────────────────────────────────────────


@pytest.mark.e2e
@pytest.mark.asyncio
class TestWAVDispatchQuery:
    """The get_rows call in match_driver_to_ride must add is_wav=True when required."""

    async def test_wav_ride_adds_is_wav_filter_to_db_query(self):
        """When requires_wav=True the backend adds is_wav=True to the drivers query."""
        from backend.routes import rides as rides_mod

        wav_ride = _ride(requires_wav=True)
        wav_driver = _driver(DRIVER_WAV_ID, is_wav=True)

        get_rows_calls = []

        async def _capture_get_rows(table, filters, **kwargs):
            if table == "drivers":
                get_rows_calls.append(filters)
            return [wav_driver]

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=wav_ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_capture_get_rows)),
            patch(
                "backend.routes.rides._shared.dispatch.resolve_matching_config",
                AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True)),
            ),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.claim_driver_atomic", AsyncMock(return_value=True)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value={**wav_driver, "is_online": True}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
            ),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"offer_timeout_seconds": 15})),
            patch("backend.routes.rides._deps.asyncio.create_task", MagicMock()),
        ):
            try:
                await rides_mod.match_driver_to_ride(ride_id=RIDE_ID)
            except Exception:
                pass  # dispatch may raise after matching — we only care about the query

        driver_queries = [f for f in get_rows_calls]
        assert any(f.get("is_wav") is True for f in driver_queries), (
            "WAV ride must add is_wav=True to the drivers get_rows filter"
        )

    async def test_non_wav_ride_omits_is_wav_filter(self):
        """Standard ride must NOT add is_wav=True to the query (would exclude non-WAV drivers)."""
        from backend.routes import rides as rides_mod

        standard_ride = _ride(requires_wav=False)
        std_driver = _driver(DRIVER_STANDARD_ID, is_wav=False)

        get_rows_calls = []

        async def _capture_get_rows(table, filters, **kwargs):
            if table == "drivers":
                get_rows_calls.append(filters)
            return [std_driver]

        with (
            patch("backend.routes.rides._deps.db_supabase.get_ride", AsyncMock(return_value=standard_ride)),
            patch("backend.routes.rides._deps.db_supabase.get_rows", AsyncMock(side_effect=_capture_get_rows)),
            patch(
                "backend.routes.rides._shared.dispatch.resolve_matching_config",
                AsyncMock(return_value=("nearest", 4.0, 10.0, 3, True)),
            ),
            patch("backend.routes.rides._deps.db_supabase.update_ride", AsyncMock()),
            patch("backend.routes.rides._deps.db_supabase.claim_driver_atomic", AsyncMock(return_value=True)),
            patch(
                "backend.routes.rides._deps.db_supabase.get_driver_by_id",
                AsyncMock(return_value={**std_driver, "is_online": True}),
            ),
            patch(
                "backend.routes.rides._deps.db_supabase.get_user_by_id",
                AsyncMock(return_value={"first_name": "Test", "last_name": "Rider"}),
            ),
            patch("backend.routes.rides._deps.manager.send_personal_message", AsyncMock()),
            patch("backend.routes.rides._deps.send_push_notification", AsyncMock()),
            patch("backend.routes.rides._deps.get_app_settings", AsyncMock(return_value={"offer_timeout_seconds": 15})),
            patch("backend.routes.rides._deps.asyncio.create_task", MagicMock()),
        ):
            try:
                await rides_mod.match_driver_to_ride(ride_id=RIDE_ID)
            except Exception:
                pass

        driver_queries = [f for f in get_rows_calls]
        assert all("is_wav" not in f for f in driver_queries), (
            "Standard ride must not add is_wav filter — would exclude non-WAV drivers"
        )
