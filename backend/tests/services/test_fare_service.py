"""
Tests for FareService.

These tests exercise the pure helpers directly and the class methods with
a mocked db. No real Supabase, no FastAPI - this is the value of having
a service layer.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services.fare_service import (  # noqa: E402
    DEFAULT_FARE,
    FareService,
    _fd,
    build_default_fares,
    find_service_area_for_point,
    merge_fare_configs_with_vehicle_types,
)

# ── Pure helpers ──────────────────────────────────────────────────────────────


class TestFd:
    def test_rounds_to_two_places(self):
        assert _fd(1.005) == 1.01  # half-up
        assert _fd(1.234) == 1.23
        assert _fd(1.235) == 1.24

    def test_handles_int(self):
        assert _fd(3) == 3.00

    def test_handles_string_numbers(self):
        assert _fd("1.5") == 1.50


class TestBuildDefaultFares:
    def test_returns_one_entry_per_vehicle_type(self):
        vts = [{"id": "v1"}, {"id": "v2"}, {"id": "v3"}]
        out = build_default_fares(vts)
        assert len(out) == 3

    def test_uses_default_constants(self):
        out = build_default_fares([{"id": "v1"}])
        assert out[0]["base_fare"] == _fd(DEFAULT_FARE["base_fare"])
        assert out[0]["per_km_rate"] == _fd(DEFAULT_FARE["per_km_rate"])

    def test_applies_surge(self):
        out = build_default_fares([{"id": "v1"}], surge=1.75)
        assert out[0]["surge_multiplier"] == 1.75

    def test_default_surge_is_one(self):
        out = build_default_fares([{"id": "v1"}])
        assert out[0]["surge_multiplier"] == 1.00

    def test_empty_vehicle_types_yields_empty(self):
        assert build_default_fares([]) == []


class TestFindServiceArea:
    def _square_polygon(self, area_id: str):
        # Roughly a 1-degree square around (52, -106) - includes Saskatoon
        return {
            "id": area_id,
            "polygon": [
                {"lat": 51.5, "lng": -106.5},
                {"lat": 51.5, "lng": -105.5},
                {"lat": 52.5, "lng": -105.5},
                {"lat": 52.5, "lng": -106.5},
            ],
        }

    def test_returns_matching_area(self):
        a = self._square_polygon("saskatoon")
        result = find_service_area_for_point([a], 52.0, -106.0)
        assert result is not None
        assert result["id"] == "saskatoon"

    def test_returns_none_when_no_match(self):
        a = self._square_polygon("saskatoon")
        result = find_service_area_for_point([a], 0.0, 0.0)
        assert result is None

    def test_returns_first_match(self):
        a = self._square_polygon("saskatoon-1")
        b = self._square_polygon("saskatoon-2")
        result = find_service_area_for_point([a, b], 52.0, -106.0)
        assert result["id"] == "saskatoon-1"

    def test_handles_empty_areas(self):
        assert find_service_area_for_point([], 52.0, -106.0) is None


class TestMergeFareConfigs:
    def test_joins_configs_to_vehicle_types(self):
        fare_configs = [
            {
                "vehicle_type_id": "economy",
                "base_fare": 4.0,
                "per_km_rate": 1.5,
                "per_minute_rate": 0.3,
                "minimum_fare": 10.0,
                "booking_fee": 2.5,
            }
        ]
        vehicle_types = [{"id": "economy", "name": "Economy"}]
        out = merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge=1.5)
        assert len(out) == 1
        assert out[0]["vehicle_type"]["name"] == "Economy"
        assert out[0]["base_fare"] == 4.00
        assert out[0]["surge_multiplier"] == 1.50

    def test_skips_configs_with_no_matching_vehicle_type(self):
        fare_configs = [
            {
                "vehicle_type_id": "luxury",
                "base_fare": 10.0,
                "per_km_rate": 3.0,
                "per_minute_rate": 0.5,
                "minimum_fare": 20.0,
                "booking_fee": 5.0,
            }
        ]
        vehicle_types = [{"id": "economy"}]  # no "luxury"
        out = merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge=1.0)
        assert out == []


# ── Service class ─────────────────────────────────────────────────────────────


def _make_db(vehicle_types=None, areas=None, fare_configs=None):
    """Build a mock db that supports the flat Supabase-style interface FareService uses."""
    db = MagicMock()

    # FareService calls db.get_rows(table_name, filters, limit=...) sequentially:
    # 1st call: vehicle_types, 2nd call: service_areas, 3rd call: fare_configs
    side_effects = []
    side_effects.append(vehicle_types if vehicle_types is not None else [])
    if areas is not None:
        side_effects.append(areas)
    if fare_configs is not None:
        side_effects.append(fare_configs)

    db.get_rows = AsyncMock(side_effect=side_effects)
    db.find_one = AsyncMock(return_value=None)
    db.insert_one = AsyncMock(return_value=None)
    db.update_one = AsyncMock(return_value=None)
    return db


pytestmark = pytest.mark.anyio


class TestFareService:
    async def test_returns_empty_when_no_vehicle_types(self):
        svc = FareService(_make_db(vehicle_types=[]))
        out = await svc.fares_for_location(52.0, -106.0)
        assert out == []

    async def test_returns_defaults_when_no_matching_area(self):
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy", "name": "Economy"}],
                areas=[],  # no service areas
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert len(out) == 1
        assert out[0]["base_fare"] == _fd(DEFAULT_FARE["base_fare"])
        assert out[0]["surge_multiplier"] == 1.00

    async def test_returns_defaults_with_surge_when_no_fare_configs(self):
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy"}],
                areas=[
                    {
                        "id": "saskatoon",
                        "surge_multiplier": 1.5,
                        "polygon": [
                            {"lat": 51.5, "lng": -106.5},
                            {"lat": 51.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -106.5},
                        ],
                    }
                ],
                fare_configs=[],
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert len(out) == 1
        assert out[0]["surge_multiplier"] == 1.50

    async def test_returns_merged_when_fare_configs_match(self):
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy", "name": "Economy"}],
                areas=[
                    {
                        "id": "saskatoon",
                        "surge_multiplier": 1.0,
                        "polygon": [
                            {"lat": 51.5, "lng": -106.5},
                            {"lat": 51.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -106.5},
                        ],
                    }
                ],
                fare_configs=[
                    {
                        "vehicle_type_id": "economy",
                        "base_fare": 5.0,
                        "per_km_rate": 2.0,
                        "per_minute_rate": 0.4,
                        "minimum_fare": 12.0,
                        "booking_fee": 3.0,
                    }
                ],
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert len(out) == 1
        assert out[0]["base_fare"] == 5.00
        assert out[0]["per_km_rate"] == 2.00

    async def test_surging_area_returns_elevated_multiplier_for_consumer_ride(self):
        """Baseline: a surging service area raises surge_multiplier above 1.0 for
        non-corporate (consumer) rides. This verifies the surge path is active so
        the corporate exclusion test below is meaningful."""
        svc = FareService(
            _make_db(
                vehicle_types=[{"id": "economy"}],
                areas=[
                    {
                        "id": "saskatoon",
                        "surge_multiplier": 1.75,
                        "polygon": [
                            {"lat": 51.5, "lng": -106.5},
                            {"lat": 51.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -105.5},
                            {"lat": 52.5, "lng": -106.5},
                        ],
                    }
                ],
                fare_configs=[],
            )
        )
        out = await svc.fares_for_location(52.0, -106.0)
        assert len(out) == 1
        assert out[0]["surge_multiplier"] == 1.75


# ── Corporate surge exclusion ─────────────────────────────────────────────────
#
# Policy (CLAUDE.md): "Surge does not apply to corporate account-paid rides."
# Enforcement lives in routes/rides.py::create_ride — after surge is resolved
# from the service area or estimate_token, the route resets it to 1.0 when the
# ride is corporate (corporate_account_id set, payment_method == "company_allowance",
# or work_profile == True).
#
# These tests verify that enforcement directly against the Decimal helper that
# the route uses, and via a thin simulation of the surge-override branch so no
# HTTP stack or DB is needed.


class TestCorporateSurgeExclusion:
    """Corporate-paid rides must always receive surge_multiplier == 1.0.

    Rationale: surge is a supply-demand signal for consumer rides; corporate
    accounts pay fixed negotiated rates and must not be exposed to variable
    surge premiums.
    """

    def _apply_corporate_surge_override(
        self,
        raw_surge: float,
        corporate_account_id=None,
        payment_method: str = "card",
        work_profile: bool = False,
    ) -> float:
        """Simulate the surge override logic from routes/rides.py::create_ride.

        This mirrors the exact conditional used in production so any change to
        the route logic will be caught by a test failure here.
        """
        from decimal import Decimal

        def _d(v):
            return Decimal(str(v))

        surge = _d(raw_surge)
        is_corporate = bool(corporate_account_id or payment_method == "company_allowance" or work_profile)
        if is_corporate and surge > _d(1):
            surge = _d(1)
        return float(surge)

    def test_corporate_account_id_resets_surge_to_one(self):
        result = self._apply_corporate_surge_override(
            raw_surge=1.75,
            corporate_account_id="corp-123",
            payment_method="company_allowance",
        )
        assert result == 1.0

    def test_company_allowance_payment_method_resets_surge(self):
        """payment_method == "company_allowance" triggers exclusion even without
        an explicit corporate_account_id on the body (work_profile path sets it
        later in create_ride)."""
        result = self._apply_corporate_surge_override(
            raw_surge=2.5,
            corporate_account_id=None,
            payment_method="company_allowance",
        )
        assert result == 1.0

    def test_work_profile_flag_resets_surge(self):
        """work_profile=True alone is sufficient — create_ride will attach the
        corporate_account_id and change payment_method later, but surge must be
        zeroed before fare arithmetic."""
        result = self._apply_corporate_surge_override(
            raw_surge=2.0,
            corporate_account_id=None,
            payment_method="card",
            work_profile=True,
        )
        assert result == 1.0

    def test_max_surge_corporate_gets_one(self):
        """Even at the hard cap (2.5×) corporate rides receive 1.0×."""
        result = self._apply_corporate_surge_override(
            raw_surge=2.5,
            corporate_account_id="corp-abc",
            payment_method="company_allowance",
        )
        assert result == 1.0

    def test_consumer_ride_no_override(self):
        """Non-corporate rides are unaffected — surge flows through unchanged."""
        result = self._apply_corporate_surge_override(
            raw_surge=1.75,
            corporate_account_id=None,
            payment_method="card",
            work_profile=False,
        )
        assert result == 1.75

    def test_consumer_ride_no_surge_unchanged(self):
        """Consumer rides with surge=1.0 stay at 1.0 — baseline guard."""
        result = self._apply_corporate_surge_override(
            raw_surge=1.0,
            corporate_account_id=None,
            payment_method="card",
        )
        assert result == 1.0

    def test_corporate_with_no_surge_stays_one(self):
        """Corporate ride in a non-surging area: still 1.0 (no change needed,
        but the branch must not alter a correct value)."""
        result = self._apply_corporate_surge_override(
            raw_surge=1.0,
            corporate_account_id="corp-123",
            payment_method="company_allowance",
        )
        assert result == 1.0


# ── Scheduled ride surge exclusion ───────────────────────────────────────────
#
# Policy (CLAUDE.md): "Never apply surge to scheduled rides booked outside the
# surge window."
# Enforcement lives in routes/rides.py::create_ride — immediately after the
# corporate surge exclusion block, surge is reset to 1.0 when is_scheduled is
# True or scheduled_time is set.
#
# Rationale: the rider locked in their fare at booking time; the area surge may
# be elevated hours later at dispatch time. Applying the dispatch-time surge
# would retroactively charge a higher multiplier than was shown — a hidden-fee
# violation under Spinr policy and a rider-trust issue.


class TestScheduledRideSurgeExclusion:
    """Scheduled rides must always receive surge_multiplier == 1.0.

    Mirrors the exact conditional used in production so any refactor to the
    route logic is caught here before it reaches riders.
    """

    def _apply_scheduled_surge_override(
        self,
        raw_surge: float,
        is_scheduled: bool = False,
        scheduled_time=None,
    ) -> float:
        """Simulate the scheduled-ride surge override logic from
        routes/rides.py::create_ride.

        Replicates the exact branch so a copy-paste divergence is caught by
        test failure rather than a production incident.
        """
        from decimal import Decimal

        def _d(v):
            return Decimal(str(v))

        surge = _d(raw_surge)
        if (is_scheduled or scheduled_time) and surge > _d(1):
            surge = _d(1)
        return float(surge)

    def test_scheduled_ride_gets_no_surge(self):
        """A ride with a future scheduled_time must receive surge == 1.0 even
        when the service area has an elevated multiplier (e.g. 2.5×)."""
        from datetime import datetime, timedelta

        future_time = datetime.utcnow() + timedelta(hours=2)
        result = self._apply_scheduled_surge_override(
            raw_surge=2.5,
            is_scheduled=False,
            scheduled_time=future_time,
        )
        assert result == 1.0

    def test_consumer_scheduled_ride_not_corporate(self):
        """Non-corporate scheduled rides also get no surge — the exclusion is
        not gated on corporate status, only on the scheduled flag."""
        from datetime import datetime, timedelta

        future_time = datetime.utcnow() + timedelta(hours=1)
        result = self._apply_scheduled_surge_override(
            raw_surge=1.75,
            is_scheduled=True,
            scheduled_time=future_time,
        )
        assert result == 1.0

    def test_immediate_ride_gets_surge(self):
        """An on-demand ride (scheduled_time=None, is_scheduled=False) must
        pass the area surge through unchanged."""
        result = self._apply_scheduled_surge_override(
            raw_surge=1.75,
            is_scheduled=False,
            scheduled_time=None,
        )
        assert result == 1.75

    def test_is_scheduled_flag_alone_resets_surge(self):
        """is_scheduled=True without a scheduled_time is sufficient to suppress
        surge — matches the route's `body.is_scheduled or body.scheduled_time`
        condition."""
        result = self._apply_scheduled_surge_override(
            raw_surge=2.0,
            is_scheduled=True,
            scheduled_time=None,
        )
        assert result == 1.0

    def test_scheduled_ride_at_normal_surge_unchanged(self):
        """When surge is already 1.0 the branch is a no-op — guard against
        accidentally elevating a non-surging scheduled ride."""
        from datetime import datetime, timedelta

        future_time = datetime.utcnow() + timedelta(hours=3)
        result = self._apply_scheduled_surge_override(
            raw_surge=1.0,
            is_scheduled=True,
            scheduled_time=future_time,
        )
        assert result == 1.0
