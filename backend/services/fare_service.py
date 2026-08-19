"""
FareService — pricing logic separated from the HTTP layer.

This is the reference implementation of the service-layer pattern
(see services/README.md). The route in `routes/fares.py` should now
be a thin wrapper that delegates here.

Pure helpers (`_fd`, `build_default_fares`) are static so they can be
tested without instantiating the service.
"""

import logging
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from ..geo_utils import get_service_area_polygon, point_in_polygon
    from ..utils.surge_engine import SURGE_CAP
except ImportError:  # pragma: no cover - allow direct module imports in tests
    from geo_utils import get_service_area_polygon, point_in_polygon
    from utils.surge_engine import SURGE_CAP


_TWO_PLACES = Decimal("0.01")

# Default fare values used when no service-area-specific config exists.
# Centralized here so they're discoverable and changeable in one place.
DEFAULT_FARE = {
    "base_fare": 3.50,
    "per_km_rate": 1.50,
    "per_minute_rate": 0.25,
    "minimum_fare": 8.00,
    "booking_fee": 2.00,
}


def _d(v: Any) -> Decimal:
    """Convert any numeric value to Decimal without float precision loss."""
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    """Round a Decimal to 2 decimal places (ROUND_HALF_UP)."""
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _f(v: Decimal) -> float:
    """Convert Decimal to float — only for use at the final JSON response boundary."""
    return float(v)


def _fd(v: Any) -> float:
    """Convert any numeric value to a 2-decimal-place float (backward-compat alias for tests)."""
    return float(_round(_d(v)))


def build_default_fares(vehicle_types: List[Dict[str, Any]], surge: Any = _d(1)) -> List[Dict[str, Any]]:
    """
    Build a default fare entry per vehicle type.

    Pure function — no DB or external calls. Useful when:
      - No matching service area is found
      - A service area exists but has no fare_configs

    Returns one entry per vehicle type in `vehicle_types`.
    """
    return [
        {
            "vehicle_type": vt,
            "base_fare": _round(_d(DEFAULT_FARE["base_fare"])),
            "per_km_rate": _round(_d(DEFAULT_FARE["per_km_rate"])),
            "per_minute_rate": _round(_d(DEFAULT_FARE["per_minute_rate"])),
            "minimum_fare": _round(_d(DEFAULT_FARE["minimum_fare"])),
            "booking_fee": _round(_d(DEFAULT_FARE["booking_fee"])),
            "surge_multiplier": _round(_d(surge)),
        }
        for vt in vehicle_types
    ]


def find_service_area_for_point(areas: List[Dict[str, Any]], lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """
    Return the first service area whose polygon contains (lat, lng), or None.

    Pure function — useful in tests with hand-constructed area dicts.
    """
    for area in areas:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            return area
    return None


def merge_vehicle_pricing_with_vehicle_types(
    vehicle_pricing: List[Dict[str, Any]],
    vehicle_types: List[Dict[str, Any]],
    surge: Decimal,
) -> List[Dict[str, Any]]:
    """Join service_areas.vehicle_pricing rows to active vehicle types.

    The admin Service Areas editor stores canonical area pricing as JSONB rows
    keyed by vehicle type name. Only matched rows are returned so callers show
    exactly the vehicle types assigned to that service area.
    """
    if not vehicle_pricing:
        return []

    pricing_by_name = {
        row.get("vehicle_type"): row for row in vehicle_pricing if isinstance(row, dict) and row.get("vehicle_type")
    }
    result = []
    for vt in vehicle_types:
        pricing = pricing_by_name.get(vt.get("name"))
        if not pricing:
            continue
        result.append(
            {
                "vehicle_type": vt,
                "base_fare": _round(_d(pricing.get("base_fare", 0))),
                "per_km_rate": _round(_d(pricing.get("per_km", pricing.get("per_km_rate", 0)))),
                "per_minute_rate": _round(_d(pricing.get("per_min", pricing.get("per_minute_rate", 0)))),
                "minimum_fare": _round(_d(pricing.get("min_fare", pricing.get("minimum_fare", 0)))),
                "booking_fee": _round(_d(pricing.get("booking_fee", 0))),
                "surge_multiplier": _round(_d(surge)),
            }
        )
    return result


def merge_fare_configs_with_vehicle_types(
    fare_configs: List[Dict[str, Any]],
    vehicle_types: List[Dict[str, Any]],
    surge: Decimal,
) -> List[Dict[str, Any]]:
    """
    Join fare_configs to vehicle_types and apply surge.

    Returns one entry per fare_config that matches a vehicle type.
    Pure function — no I/O.
    """
    vt_map = {vt["id"]: vt for vt in vehicle_types}
    result = []
    for fare in fare_configs:
        vt = vt_map.get(fare["vehicle_type_id"])
        if vt:
            result.append(
                {
                    "vehicle_type": vt,
                    "base_fare": _round(_d(fare["base_fare"])),
                    "per_km_rate": _round(_d(fare["per_km_rate"])),
                    "per_minute_rate": _round(_d(fare["per_minute_rate"])),
                    "minimum_fare": _round(_d(fare["minimum_fare"])),
                    "booking_fee": _round(_d(fare["booking_fee"])),
                    "surge_multiplier": _round(_d(surge)),
                }
            )
    return result


@dataclass(frozen=True)
class FareBreakdown:
    """Result of a single fare calculation — all values are Decimal."""

    base_fare: Decimal
    distance_fare: Decimal
    time_fare: Decimal
    booking_fee: Decimal
    airport_fee: Decimal
    total_fare: Decimal
    minimum_fare: Decimal
    surge_multiplier: Decimal
    driver_earnings: Decimal
    admin_earnings: Decimal
    # Pre-surge distance/time fares (C4). surge multiplies ONLY distance+time,
    # so these let the receipt itemise the surge delta as a real dollar figure
    # instead of baking it into "Ride fare" behind an amount-less "Surge" line.
    # Default 0 so existing FareBreakdown constructors are unaffected.
    distance_fare_base: Decimal = Decimal("0")
    time_fare_base: Decimal = Decimal("0")


def calculate_fare(
    fare_info: Dict[str, Any],
    distance_km: float,
    duration_minutes: float,
    surge: Decimal = Decimal("1"),
    airport_fee: Any = 0,
) -> FareBreakdown:
    """Single source of truth for fare arithmetic.

    Pure function — no DB or external calls. All intermediate math uses
    Decimal; callers convert at the serialization boundary.
    """
    base_fare = _d(fare_info["base_fare"])
    per_km = _d(fare_info["per_km_rate"])
    per_min = _d(fare_info["per_minute_rate"])
    booking = _d(fare_info.get("booking_fee", DEFAULT_FARE["booking_fee"]))
    minimum = _d(fare_info.get("minimum_fare", DEFAULT_FARE["minimum_fare"]))
    ap_fee = _d(airport_fee)

    distance_fare_base = _round(per_km * _d(distance_km))
    time_fare_base = _round(per_min * _d(duration_minutes))
    distance_fare = _round(per_km * _d(distance_km) * surge)
    time_fare = _round(per_min * _d(duration_minutes) * surge)

    subtotal = _round(base_fare + distance_fare + time_fare + booking + ap_fee)
    total_fare = _round(max(subtotal, minimum))

    # Attribution invariant: total_fare == driver_earnings + admin_earnings.
    # admin_earnings is the platform's cut (booking + airport fees); the driver
    # keeps everything else — including the minimum-fare uplift (minimum −
    # subtotal). Spinr takes 0% commission on the ride fare, so any amount the
    # rider is charged above the fee floor is the driver's. Deriving driver from
    # (total − admin) — rather than base+distance+time — is what makes the
    # payout ledger reconcile with the "Driver earns 100%" receipt line on a
    # clamped ride. Both operands are 2-dp Decimals, so the invariant is exact.
    admin_earnings = _round(booking + ap_fee)
    driver_earnings = _round(total_fare - admin_earnings)

    return FareBreakdown(
        base_fare=base_fare,
        distance_fare=distance_fare,
        time_fare=time_fare,
        booking_fee=booking,
        airport_fee=ap_fee,
        total_fare=total_fare,
        minimum_fare=minimum,
        surge_multiplier=surge,
        driver_earnings=driver_earnings,
        admin_earnings=admin_earnings,
        distance_fare_base=distance_fare_base,
        time_fare_base=time_fare_base,
    )


def driver_earnings_with_tip(ride: Dict[str, Any], tip_amount: Any) -> Decimal:
    """Canonical, idempotent driver_earnings — the ONLY way any code path
    should compute it once a tip is involved.

    ``driver_earnings = (total_fare - admin_earnings) + tip_amount``, always
    computed FRESH from the ride's own persisted fare columns — never
    "existing driver_earnings + tip delta". That accumulate-style pattern is
    what caused a real production underpayment (2026-08-19 live-testing
    incident): three independent call sites (routes/rides/rating.py's
    tip-while-rating, routes/rides/payments.py's add_tip late-tip flow, and
    the settlement RPC's tip-delta logic in migration 288) each mutated
    driver_earnings relative to whatever value they read, with no shared
    source of truth. A ride that touched more than one of those paths (e.g.
    tipped via /rate before paying, then the payment request ALSO carried
    the same tip amount) landed on whichever path ran last, and that path's
    view of "current earnings" didn't necessarily include what an earlier
    path had already added — a driver was credited $0.17 instead of $0.67
    on a real, live-charged ride.

    ``admin_earnings`` is strictly booking_fee + airport_fee (CLAUDE.md: "the
    driver keeps 100% of the fare" — 0% commission model). See
    calculate_fare()'s own docstring for why the minimum-fare uplift is
    deliberately the driver's, not admin's.

    Every call site that credits a tip to driver_earnings — rating.py,
    add_tip, and the settlement RPC's Python-side legacy fallback — must call
    this instead of reading and incrementing the existing column. It is
    naturally idempotent: calling it twice with the same ride+tip produces
    the same answer, so call order and staleness stop mattering.

    Refuses a legacy-imported ride outright. ``total_fare``/``base_fare`` on
    those rows are a receipt-display reconstruction (booking_import_service.py),
    not real fare components — ``total_fare - admin_earnings`` there
    algebraically equals the OLD app's admin commission, not a minimum-fare
    uplift, so this formula would silently inflate driver_earnings by that
    commission. rating.py and add_tip both already reject a tip on a legacy
    ride before reaching here; this is the belt-and-suspenders stop for
    whatever future call site doesn't remember to check first.
    """
    if ride.get("legacy_import_metadata"):
        raise ValueError("driver_earnings_with_tip is not valid for a legacy-imported ride")
    # fare_service._d has no None/""-guard by design (its other callers only
    # ever pass already-defaulted fare_info values) — this function reads
    # directly from a `rides` row instead, where NULL/missing is routine, so
    # guard explicitly here rather than weakening _d for every other caller.
    total_fare = _d(ride.get("total_fare") or 0)
    admin_earnings = _round(_d(ride.get("booking_fee") or 0) + _d(ride.get("airport_fee") or 0))
    base_earnings = _round(total_fare - admin_earnings)
    return _round(max(base_earnings, Decimal("0")) + _d(tip_amount or 0))


def build_fare_breakdown_lines(
    fb: FareBreakdown,
    distance_km: float,
    duration_minutes: int,
    area_fees: List[Dict[str, Any]] = (),
    tax_breakdown: Dict[str, Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """Build a flat list of labelled fare line items for the rider UI.

    The frontend renders this array verbatim — no hardcoded field knowledge
    needed. Labels are owned by the backend so admin fee changes propagate
    without a client release.
    """
    lines: List[Dict[str, Any]] = []

    # C4: surge multiplies only distance+time, so split it out as a real dollar
    # line rather than baking it into "Ride fare" behind an amount-less "Surge"
    # line. The pre-surge ride fare + the surge delta sum back to the exact same
    # total (base + surged distance + surged time), so the grand total is
    # unchanged — only the disclosure improves.
    surged_dt = fb.distance_fare + fb.time_fare
    base_dt = fb.distance_fare_base + fb.time_fare_base
    if fb.surge_multiplier > Decimal("1") and base_dt <= Decimal("0"):
        # Older FareBreakdown without the pre-surge fields — derive from the multiplier.
        base_dt = _round(surged_dt / fb.surge_multiplier)
    if fb.surge_multiplier > Decimal("1"):
        # Show what surge ACTUALLY added to the price, not the raw distance/time
        # delta: on a short ride the minimum fare can absorb some or all of the
        # surge, so the real contribution is (surged total − unsurged total),
        # both minimum-clamped. Prevents overstating surge on minimum-fare rides.
        unsurged_total = max(fb.base_fare + base_dt + fb.booking_fee + fb.airport_fee, fb.minimum_fare)
        surge_delta = max(Decimal("0"), _round(fb.total_fare - unsurged_total))
    else:
        surge_delta = Decimal("0")

    # Consolidated ride line — absorbs the minimum-fare adjustment so ride +
    # surge + booking + airport == total_fare exactly. Driver earns 100%.
    ride_subtotal = _f(fb.total_fare - surge_delta - fb.booking_fee - fb.airport_fee)
    lines.append(
        {
            "label": f"Ride fare ({round(distance_km, 1)} km)",
            "amount": ride_subtotal,
            "type": "ride",
        }
    )

    if fb.airport_fee > Decimal("0"):
        lines.append({"label": "Airport surcharge", "amount": _f(fb.airport_fee), "type": "fee"})

    if fb.booking_fee > Decimal("0"):
        lines.append({"label": "Booking fee", "amount": _f(fb.booking_fee), "type": "fee"})

    if fb.surge_multiplier > Decimal("1"):
        lines.append(
            {
                # Label text, not money: this float() formats the multiplier for
                # display, no arithmetic is done on it, and the amount below
                # comes from `surge_delta`, which is Decimal throughout.
                # Deliberately NOT switched to the Decimal's own repr —
                # Decimal("1.50") renders "1.50" where float renders "1.5", so
                # that swap would silently change rider-visible receipt copy in
                # order to satisfy a lint rule.
                # nosemgrep: spinr-no-float-in-money
                "label": f"Surge ({float(fb.surge_multiplier)}×)",
                "amount": _f(surge_delta),
                "type": "modifier",
            }
        )

    for fee in area_fees or []:
        # Route through Decimal like every other money value here. This was the
        # one line item built with raw float(), which put it out of step with
        # the same fee's treatment in the total (`_d(af["calculated_value"])`
        # below) and in the PDF and email receipts (both `_d(...)`). `_d` is
        # Decimal(str(v)) and `_f` converts back at the response boundary, so
        # the emitted amount is byte-identical to before — this fixes the type
        # discipline, not the number, and keeps any future arithmetic on this
        # value safe by construction.
        fee_amount = _d(fee.get("calculated_value", 0))
        if fee_amount > 0:
            lines.append({"label": fee.get("name", "Fee"), "amount": _f(fee_amount), "type": "fee"})

    if tax_breakdown:
        for tax_name, info in tax_breakdown.items():
            if info.get("amount", 0) > 0:
                rate = info.get("rate", 0)
                label = f"{tax_name} ({rate}%)" if rate else tax_name
                lines.append({"label": label, "amount": info["amount"], "type": "tax"})

    return lines


def recalculate_fare_for_distance(
    ride: Dict[str, Any],
    actual_distance_km: float,
    minimum_fare: Optional[Decimal] = None,
) -> Dict[str, Any]:
    """Recalculate fare when actual distance differs from the estimate.

    Derives the effective per-km rate from the stored ride row, then
    reapplies it to the actual distance. Falls back to the default
    per-km rate (with surge) when planned distance is missing/zero.

    Re-applies the minimum-fare floor so a short actual trip never settles
    below what the rider was quoted, and keeps the attribution invariant
    (total_fare == driver_earnings + admin_earnings) — the driver keeps the
    minimum-fare uplift. Pass ``minimum_fare`` to enforce an explicit floor;
    otherwise the floor is inferred from whether booking clamped the ride.
    """
    planned = _d(ride.get("distance_km") or ride.get("planned_distance_km") or 0)
    if planned <= 0:
        surge = _d(ride.get("surge_multiplier") or 1)
        per_km_rate = _round(_d(DEFAULT_FARE["per_km_rate"]) * surge)
        logger.error(
            "recalculate_fare_for_distance: planned distance is %s for ride %s — "
            "falling back to default per-km rate %s (surge=%s)",
            planned,
            ride.get("id", "?"),
            per_km_rate,
            surge,
        )
    else:
        per_km_rate = _d(ride.get("distance_fare", 0)) / planned
    new_distance_fare = _round(per_km_rate * _d(actual_distance_km))
    booking = _d(ride.get("booking_fee", 0))
    airport = _d(ride.get("airport_fee") or 0)
    new_total_fare = _round(
        _d(ride.get("base_fare", 0)) + new_distance_fare + _d(ride.get("time_fare", 0)) + booking + airport
    )

    # Re-apply the minimum-fare floor at completion. The minimum isn't persisted
    # on the ride row, so infer whether booking clamped it: a stored total_fare
    # sitting above the sum of its own components means the ride was floored, so
    # keep at least that floor now. Do NOT re-fetch the live fare config here —
    # it may have changed since booking and the rider was quoted the
    # booking-time minimum. An explicit ``minimum_fare`` (future callers) wins too.
    booked_total = _d(ride.get("total_fare") or 0)
    booked_components = _round(
        _d(ride.get("base_fare", 0))
        + _d(ride.get("distance_fare", 0))
        + _d(ride.get("time_fare", 0))
        + booking
        + airport
    )
    floor = Decimal("0")
    if booked_total - booked_components > Decimal("0.005"):
        floor = booked_total
    if minimum_fare is not None:
        floor = max(floor, _d(minimum_fare))
    if floor > new_total_fare:
        new_total_fare = _round(floor)

    # Attribution invariant (see calculate_fare): the driver keeps everything
    # above the platform's fee cut — including any minimum-fare uplift. Booking
    # and airport fees do not change at completion, so admin stays fixed.
    new_admin_earnings = _round(booking + airport)
    new_driver_earnings = _round(new_total_fare - new_admin_earnings)

    area_fees_total = _d(ride.get("area_fees_total", 0))
    if area_fees_total == 0:
        for af in ride.get("area_fees_breakdown") or []:
            if isinstance(af, dict):
                area_fees_total += _d(af.get("calculated_value", 0))
    tax_amount = _d(ride.get("tax_amount", 0))
    discount = _d(ride.get("discount_amount", 0))
    new_grand_total = _round(new_total_fare + area_fees_total + tax_amount - discount)

    return {
        "distance_km": round(actual_distance_km, 2),
        "distance_fare": _f(new_distance_fare),
        "total_fare": _f(new_total_fare),
        "driver_earnings": _f(new_driver_earnings),
        "grand_total": _f(new_grand_total),
    }


class FareService:
    """
    Pricing logic that depends on the database.

    Routes should instantiate this with the shared `db` and call methods on it.
    All public methods are async and return plain dicts/lists ready for JSON
    serialization.
    """

    def __init__(self, db):
        self.db = db

    async def list_active_vehicle_types(self) -> List[Dict[str, Any]]:
        """Fetch all active vehicle types."""
        return await self.db.get_rows("vehicle_types", {"is_active": True}, limit=100)

    async def fares_for_location(self, lat: float, lng: float) -> List[Dict[str, Any]]:
        """
        Compute the fare list for a given (lat, lng).

        Resolution order:
          1. If no active vehicle types: return []
          2. If no matching service area: return defaults
          3. If matching area but no fare_configs: return []
          4. If matching area + fare_configs: return only configured vehicle types
          5. If merge yields nothing (vehicle types changed): return []
        """
        vehicle_types = await self.list_active_vehicle_types()
        if not vehicle_types:
            return []

        all_areas = await self.db.get_rows("service_areas", {"is_active": True}, limit=100)
        matching_area = find_service_area_for_point(all_areas, lat, lng)

        if not matching_area:
            return build_default_fares(vehicle_types)

        # Surge gated on the per-area admin master toggle + current active flag
        # (see routes/fares.py). Disabled areas always price at 1.0×.
        surge = (
            min(_d(matching_area.get("surge_multiplier", 1)), _d(SURGE_CAP))
            if matching_area.get("surge_enabled", False) and matching_area.get("surge_active", False)
            else _d(1)
        )
        vehicle_pricing = matching_area.get("vehicle_pricing") or []
        vehicle_pricing_fares = merge_vehicle_pricing_with_vehicle_types(vehicle_pricing, vehicle_types, surge)
        if vehicle_pricing_fares:
            return vehicle_pricing_fares

        fare_configs = await self.db.get_rows(
            "fare_configs",
            {"service_area_id": matching_area["id"], "is_active": True},
            limit=100,
        )

        if not fare_configs:
            return []

        return merge_fare_configs_with_vehicle_types(fare_configs, vehicle_types, surge)
