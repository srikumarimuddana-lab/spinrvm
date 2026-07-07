import asyncio
import json
import secrets
import time as _time_mod
import uuid
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

try:
    from .. import db_supabase
    from ..dependencies import generate_pickup_otp, get_current_user, get_current_user_allow_expired
    from ..features import (
        calculate_airport_fee,
        calculate_all_fees,
        notify_safety_team,
        send_push_notification,
    )
    from ..geo_utils import (
        calculate_distance,
        get_service_area_polygon,
        multi_leg_distance,
        point_in_polygon,
    )
    from ..models.ride_status import RideStatus
    from ..schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from ..services import DispatchService
    from ..services.dispatch_service import (
        dispatch_geo_bounds,
        filter_and_rank_drivers,
        rank_by_eta_with_acceptance,
    )
    from ..services.fare_service import build_fare_breakdown_lines, calculate_fare
    from ..settings_loader import get_app_settings
    from ..sms_service import send_sms
    from ..socket_manager import manager
    from ..utils.audit_logger import log_user_action
    from ..utils.background import spawn
    from ..utils.error_handling import (
        ErrorCode,
        RideNotFoundException,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from ..utils.error_keys import ErrorKeys
    from ..utils.idempotency import idempotent_endpoint
    from ..utils.maps_eta import batch_get_etas
    from ..utils.pii import first_name_only
    from ..utils.rate_limiter import (
        api_rate_limit,
        cancel_ride_limit,
        payment_action_limit,
        ride_action_limit,
        ride_message_limit,
        ride_rating_limit,
        ride_read_limit,
        ride_request_limit,
    )
    from ..validators import validate_ride_location
except ImportError:
    import db_supabase
    from dependencies import generate_pickup_otp, get_current_user, get_current_user_allow_expired
    from features import (
        calculate_airport_fee,
        calculate_all_fees,
        notify_safety_team,
        send_push_notification,
    )
    from geo_utils import calculate_distance, get_service_area_polygon, multi_leg_distance, point_in_polygon
    from models.ride_status import RideStatus  # noqa: F401
    from schemas import CreateRideRequest, DriverPublicView, Ride, RideRatingRequest
    from services.dispatch_service import (
        DispatchService,
        dispatch_geo_bounds,
        filter_and_rank_drivers,
        rank_by_eta_with_acceptance,
    )
    from services.fare_service import build_fare_breakdown_lines, calculate_fare
    from settings_loader import get_app_settings
    from sms_service import send_sms
    from socket_manager import manager
    from utils.audit_logger import log_user_action
    from utils.background import spawn  # type: ignore
    from utils.error_handling import (
        ErrorCode,
        RideNotFoundException,
        SpinrException,
        db_error_text,
        pg_error_code,
    )
    from utils.error_keys import ErrorKeys
    from utils.idempotency import idempotent_endpoint
    from utils.maps_eta import batch_get_etas
    from utils.pii import first_name_only
    from utils.rate_limiter import (
        api_rate_limit,
        cancel_ride_limit,
        payment_action_limit,
        ride_action_limit,
        ride_message_limit,
        ride_rating_limit,
        ride_read_limit,
        ride_request_limit,
    )
    from validators import validate_ride_location


from .fares import _fares_for_location_impl, get_fares_for_location

try:
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.earnings_snapshot import build_earnings_snapshot
    from ..utils.insurance_periods import record_period_transition
    from ..utils.live_activity import (
        EVENT_END,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from ..utils.metrics import inc as _metric_inc
    from ..utils.metrics import observe as _metric_observe
    from ..utils.metrics import timed as _metric_timed
    from ..utils.ride_code import generate_ride_code
except ImportError:
    from utils.datetime_utils import parse_iso_utc
    from utils.earnings_snapshot import build_earnings_snapshot  # noqa: F401
    from utils.insurance_periods import record_period_transition  # type: ignore[assignment]
    from utils.live_activity import (  # type: ignore
        EVENT_END,
        EVENT_UPDATE,
        send_live_activity_update,
    )
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.metrics import observe as _metric_observe  # type: ignore
    from utils.metrics import timed as _metric_timed  # type: ignore
    from utils.ride_code import generate_ride_code

try:
    from ..utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )
except ImportError:
    from utils.estimate_token import (
        EstimateTokenError,
        sign_estimate_token,
        verify_estimate_token,
    )

try:
    from ..services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore
except ImportError:
    from services.corporate_policy_service import evaluate_policy_for_ride  # type: ignore

try:
    from ..core.config import settings as _settings
except ImportError:
    from core.config import settings as _settings  # noqa: F401 — dual-import pattern

try:
    from ..utils.offer_card_token import sign_offer_card_token
except ImportError:
    from utils.offer_card_token import sign_offer_card_token

try:
    from ..services.cancellation_service import (
        calculate_cancellation_fee,
        pay_driver_cancellation_fee,
    )
    from ..services.payment_service import (
        send_ride_receipt,
        settle_card,
        settle_corporate,
        settle_wallet,
    )
    from ..utils.stripe_charge import authorize_ride, charge_ancillary_fee, verify_authorization
except ImportError:
    from services.cancellation_service import calculate_cancellation_fee, pay_driver_cancellation_fee  # type: ignore
    from services.payment_service import send_ride_receipt, settle_card, settle_corporate, settle_wallet  # type: ignore
    from utils.stripe_charge import authorize_ride, charge_ancillary_fee, verify_authorization  # type: ignore

db = db_supabase  # legacy alias

import httpx as _httpx  # noqa: E402 — late import to avoid circular at module load


def _push_in_background(*args, _ctx: str = "", **kwargs) -> None:
    """Fire an informational push without blocking the request path.

    FCM/Expo round-trips run 100–300 ms; awaiting them inline adds that
    straight onto user-facing latency for pushes that are best-effort by
    design (tip received, rating received, trip shared). Failures are
    logged with ``_ctx`` — never re-raised into the caller. Time-critical
    pushes (dispatch/safety) must NOT use this: they have their own
    retry-queue fallback inside send_push_notification.
    """

    async def _send() -> None:
        try:
            await send_push_notification(*args, **kwargs)
        except Exception:
            logger.error(f"background push failed ({_ctx})", exc_info=True)

    spawn(_send())


def _decode_polyline(encoded: str) -> list:
    """Decode a Google encoded polyline string to [[lat, lng], ...] list."""
    coords: list = []
    index = 0
    lat = 0
    lng = 0
    while index < len(encoded):
        for is_lng in (False, True):
            result = 0
            shift = 0
            while True:
                if index >= len(encoded):
                    raise ValueError("Truncated encoded polyline at index %d" % index)
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 32:
                    break
            value = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lng:
                lng += value
            else:
                lat += value
        coords.append([lat / 1e5, lng / 1e5])
    return coords


async def _fetch_directions_polyline(
    pickup_lat: float,
    pickup_lng: float,
    dropoff_lat: float,
    dropoff_lng: float,
    api_key: str,
    waypoints: Optional[list] = None,
) -> Optional[list]:
    """Call Google Directions API and return [[lat, lng], ...] overview polyline.

    Returns None on any failure — callers must treat this as a soft error and
    fall back to the client-computed polyline or the Directions API on-device.
    Timeout is 3 s, well within the ride-creation SLA.
    waypoints is an optional list of {lat, lng} stop dicts (multi-stop rides).
    """
    if not api_key:
        return None
    try:
        params: dict = {
            "origin": f"{pickup_lat},{pickup_lng}",
            "destination": f"{dropoff_lat},{dropoff_lng}",
            "key": api_key,
        }
        if waypoints:
            params["waypoints"] = "|".join(f"{w['lat']},{w['lng']}" for w in waypoints)
        async with _httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/directions/json",
                params=params,
            )
            data = resp.json()
        if data.get("status") != "OK" or not data.get("routes"):
            logger.warning(
                "_fetch_directions_polyline: status=%s — no route returned",
                data.get("status"),
            )
            return None
        encoded = data["routes"][0].get("overview_polyline", {}).get("points", "")
        if not encoded:
            return None
        pts = _decode_polyline(encoded)
        return pts if len(pts) >= 2 else None
    except Exception as exc:
        logger.warning("_fetch_directions_polyline failed (non-fatal): %s", exc)
        return None


async def _get_active_service_area_for_point(
    lat: float,
    lng: float,
    active_areas: List[dict],
) -> Optional[dict]:
    """Return an active service area containing a point.

    Prefer the PostGIS RPC when its area geography column is populated, but
    fall back to the JSON/GeoJSON polygon column used by the admin dashboard.
    Some production rows have polygon coverage without a synced geography value;
    the fallback keeps estimate geofence checks aligned with what admins see.
    """
    matched_area = await db_supabase.get_service_area_for_point(lat, lng)
    if matched_area and matched_area.get("is_active", True) is not False:
        return matched_area

    for area in active_areas or []:
        poly = get_service_area_polygon(area)
        if poly and point_in_polygon(lat, lng, poly):
            return area
    return None


async def _require_ride_in_state_rider(ride_id: str, rider_id: str, allowed_states: tuple) -> dict:
    """Load a rider's ride only if it is in one of allowed_states.

    Raises 409 if the ride exists but is in the wrong state.
    Raises 404 if the ride doesn't exist or isn't owned by this rider.
    """
    ride = await db.find_one(
        "rides",
        {"id": ride_id, "rider_id": rider_id, "status": {"$in": list(allowed_states)}},
    )
    if ride:
        return ride
    existing = await db.find_one("rides", {"id": ride_id, "rider_id": rider_id})
    if existing:
        current = existing.get("status", "unknown")
        raise SpinrException(
            message=f"Ride is in status '{current}'; cannot perform this action from that state (allowed: {list(allowed_states)}).",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=409,
            details={"current_status": current, "allowed": list(allowed_states)},
            message_key=ErrorKeys.RIDE_INVALID_STATUS,
        )
    raise RideNotFoundException(
        ride_id=ride_id,
        message_key=ErrorKeys.RIDE_NOT_FOUND,
    )


dispatch = DispatchService(db_supabase)  # module-level instance for legacy call sites

# ── Decimal helpers for accurate currency arithmetic ──────────────────────────
_TWO_PLACES = Decimal("0.01")


def _d(v) -> Decimal:
    """Convert any numeric value to Decimal safely (avoids float precision loss)."""
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _reestimate_fare_for_stops(ride: dict, new_stops: list) -> dict:
    """Recalculate distance, duration, and fare after a stop mutation.

    Derives per-km and per-minute rates from the values already stored on the
    ride row (distance_fare / (distance_km * surge) and time_fare / (duration *
    surge)), then applies them to the new multi-leg distance.  Returns a dict
    suitable for merging into a $set update.
    """
    # Multi-leg distance pickup → stops → dropoff (shared with /rides/estimate).
    new_distance_km = multi_leg_distance(
        ride["pickup_lat"],
        ride["pickup_lng"],
        ride["dropoff_lat"],
        ride["dropoff_lng"],
        new_stops,
    )
    new_duration_minutes = max(5, int(new_distance_km / 30 * 60) + 5)

    surge = _d(ride.get("surge_multiplier", 1.0)) or _d(1)
    old_dist = _d(ride.get("distance_km") or 1)
    old_dur = _d(ride.get("duration_minutes") or 1)

    per_km_effective = _d(ride.get("distance_fare", 0)) / (old_dist * surge)
    per_min_effective = _d(ride.get("time_fare", 0)) / (old_dur * surge)

    new_distance_fare = _round(per_km_effective * _d(new_distance_km) * surge)
    new_time_fare = _round(per_min_effective * _d(new_duration_minutes) * surge)
    new_total = _round(
        _d(ride.get("base_fare", 0)) + new_distance_fare + new_time_fare + _d(ride.get("booking_fee", 0))
    )

    return {
        "distance_km": round(new_distance_km, 2),
        "duration_minutes": new_duration_minutes,
        "distance_fare": _money_str(new_distance_fare),
        "time_fare": _money_str(new_time_fare),
        "estimated_fare": _money_str(new_total),
        "total_fare": _money_str(new_total),
    }


def _f(v: Decimal) -> float:
    """Convert Decimal to float.

    Reserved for legacy callers that genuinely need a float — Pydantic field
    coercion (Ride model takes float for ``surge_multiplier``), signed-token
    payloads (``sign_estimate_token`` / ``verify_estimate_token``), and
    internal helpers like ``calculate_all_fees`` whose contract is float.

    For JSON wire responses use ``_money_str`` instead — Audit-17 P0-1
    mandates that money fields cross the wire as decimal strings, never
    IEEE-754 floats. See CLAUDE.md § Critical Conventions ("Money arithmetic").
    """
    return float(v)


def _money_str(v: Decimal) -> str:
    """Quantize ``v`` to 2 decimal places and emit as a JSON-safe string.

    Use on every money-shaped value placed into a dict response or
    WebSocket payload. Pydantic response models already route money through
    ``DecimalStr``; this helper covers the remaining hand-built dict
    responses where there is no schema between us and the wire.
    """
    return str(_round(_d(v)))


import re as _re

_HOUSE_NUM_RE = _re.compile(
    r"^\s*#?\d[\d\w/-]*[\s,]+",
)


def _truncate_address(addr: str | None) -> str | None:
    """Strip house/unit numbers from an address, keeping street and city.

    '123 Main Street, Saskatoon, SK' → 'Main Street, Saskatoon, SK'
    '#4-567 Broadway Ave, Regina'    → 'Broadway Ave, Regina'
    """
    if not addr:
        return addr
    truncated = _HOUSE_NUM_RE.sub("", addr, count=1)
    return truncated if truncated else addr


def _redact_driver_location_fields(ride: dict) -> None:
    """Redact addresses to street-level and coordinates to ~110m for drivers."""
    for key in ("pickup_address", "dropoff_address"):
        if key in ride:
            ride[key] = _truncate_address(ride[key])
    for key in ("pickup_lat", "dropoff_lat", "pickup_lng", "dropoff_lng"):
        val = ride.get(key)
        if val is not None:
            try:
                ride[key] = round(float(val), 3)
            except (ValueError, TypeError):
                pass


def _actual_duration_minutes(ride: dict) -> int | None:
    """Derive the actual trip-in-progress duration in whole minutes.

    Preferred source is ``ride_metrics.phases.trip_in_progress.actual_duration_minutes``
    (migration 89), assembled at completion from ride timestamps. Falls back
    to the GPS-derived ``phase_durations`` (migration 15) for rides completed
    before migration 89 landed. Returns None when neither source is populated
    so callers can fall back to the booking-time estimate.
    """
    trip_phase = ((ride.get("ride_metrics") or {}).get("phases") or {}).get("trip_in_progress") or {}
    persisted = trip_phase.get("actual_duration_minutes")
    if isinstance(persisted, (int, float)) and persisted > 0:
        return int(persisted)
    phase = (ride.get("phase_durations") or {}).get("trip_in_progress")
    if phase is None:
        return None
    try:
        secs = float(phase)
    except (TypeError, ValueError):
        return None
    if secs <= 0:
        return None
    return max(1, int(round(secs / 60)))


def _sum_fare_breakdown(lines: list[dict]) -> float:
    """Sum the numeric `amount` of every fare_breakdown line.

    This IS the rider's bill — the same number the receipt UI computes by
    summing the rendered items. Modifier rows (e.g. surge multiplier) carry
    amount=None and are skipped. Summed in Decimal (HALF_UP) so the returned
    grand_total always equals the exact sum of the line items shown; result
    is rounded to cents and clamped at 0.
    """
    total = Decimal("0")
    for line in lines or []:
        amt = line.get("amount") if isinstance(line, dict) else None
        if amt is None:
            continue
        try:
            total += _d(amt)
        except (TypeError, ValueError, InvalidOperation):
            continue
    return _f(_round(max(Decimal("0"), total)))


def _build_fare_breakdown(ride: dict) -> list[dict]:
    """Build a dynamic fare_breakdown list from ride fields.

    Reused by get_ride and get_ride_history so every surface sees the same
    line-item structure.
    """
    lines: list[dict] = []
    base = _d(ride.get("base_fare", 0))
    dist_surged = _d(ride.get("distance_fare", 0))
    time_surged = _d(ride.get("time_fare", 0))
    booking = _d(ride.get("booking_fee", 0) or 0)
    airport = _d(ride.get("airport_fee", 0) or 0)
    surge = _d(ride.get("surge_multiplier") or 1)

    # C4: disclose surge as a real dollar line on the actual bill surfaces
    # (receipt / history / admin), matching the estimate — it was amount:None
    # here. surge multiplies only distance+time; the pre-surge ride fare + the
    # surge delta sum to the same total. Cap the delta at what surge actually
    # added: if the minimum fare clamped the surged fare, surge contributed $0.
    surge_delta = Decimal("0")
    if surge > Decimal("1"):
        surged_dt = dist_surged + time_surged
        unsurged_dt = _round(surged_dt / surge)
        surged_subtotal = base + surged_dt + booking + airport
        total_fare = _d(ride.get("total_fare") or surged_subtotal)
        min_clamped = (total_fare - surged_subtotal) > Decimal("0.005")
        surge_delta = Decimal("0") if min_clamped else _round(surged_dt - unsurged_dt)

    ride_fare_d = base + dist_surged + time_surged - surge_delta
    if ride_fare_d > 0:
        dist_km = round(float(ride.get("distance_km") or 0), 1)
        lines.append({"label": f"Ride fare ({dist_km} km)", "amount": _f(_round(ride_fare_d)), "type": "ride"})
    if airport > 0:
        lines.append({"label": "Airport surcharge", "amount": ride["airport_fee"], "type": "fee"})
    if booking > 0:
        lines.append({"label": "Booking fee", "amount": ride["booking_fee"], "type": "fee"})
    if surge > Decimal("1"):
        lines.append({"label": f"Surge ({float(surge)}×)", "amount": _f(_round(surge_delta)), "type": "modifier"})
    for af in ride.get("area_fees_breakdown") or []:
        afv = af.get("calculated_value", 0)
        if float(afv) > 0:
            lines.append({"label": af.get("name", "Fee"), "amount": afv, "type": "fee"})
    for tax_name, tax_info in (ride.get("tax_breakdown") or {}).items():
        if tax_info.get("amount", 0) > 0:
            rate = tax_info.get("rate", 0)
            lbl = f"{tax_name} ({rate}%)" if rate else tax_name
            lines.append({"label": lbl, "amount": tax_info["amount"], "type": "tax"})
    if ride.get("discount_amount") and float(ride["discount_amount"]) > 0:
        promo_label = f"Promo ({ride['promo_code']})" if ride.get("promo_code") else "Promo discount"
        # Promos apply to ride fare (driver earnings) only — never to fees or
        # taxes. Cap the displayed discount at ride_fare so legacy rides with
        # an uncapped discount_amount still render a sane breakdown.
        raw_discount = float(ride["discount_amount"])
        # Cap against the full (surged) ride fare — the driver's 100%-share base,
        # which the promo discounts — not the pre-surge line shown above.
        ride_fare = float(base + dist_surged + time_surged)
        capped_discount = min(raw_discount, ride_fare) if ride_fare > 0 else raw_discount
        lines.append({"label": promo_label, "amount": -capped_discount, "type": "discount"})
    if ride.get("tip_amount") and float(ride["tip_amount"]) > 0:
        lines.append({"label": "Tip", "amount": ride["tip_amount"], "type": "tip"})
    return lines


def _is_corporate_paid(
    *,
    payment_method: Optional[str],
    work_profile: Optional[bool],
    corporate_account_id: Optional[str],
) -> bool:
    """True when the ride will be settled against a corporate account.

    Surge does not apply to corporate-paid rides (CLAUDE.md Surge rules:
    "Surge does not apply to corporate account-paid rides"). The caller
    needs the answer before fare arithmetic so the multiplier can be
    pinned to 1.0× before distance/time fares are computed.

    Two booking shapes route to corporate billing:
      1. ``payment_method == "company_allowance"`` — the rider explicitly
         picked Company Allowance.
      2. ``work_profile=True`` with a ``corporate_account_id`` — the
         rider toggled Work mode; rides.py:907 reclassifies these to
         ``payment_method="company_allowance"`` at persist time.
    Both paths must bypass surge before the fare is locked in, otherwise
    the rider would see the surged estimate and the company would be
    billed for it.
    """
    if not corporate_account_id:
        return False
    if (payment_method or "").lower() == "company_allowance":
        return True
    if work_profile:
        return True
    return False


api_router = APIRouter(prefix="/rides", tags=["Rides"])


def _rider_visible_photo(user: Optional[dict]) -> Optional[str]:
    """Driver avatar shown to RIDERS, gated on moderation status.

    Driver photos upload as 'pending_review' and must be admin-approved before
    riders see them (identity/safety). 'pending_review'/'rejected' → hidden.
    Legacy photos with no status are treated as visible.
    """
    if not user:
        return None
    if user.get("profile_image_status") in ("pending_review", "rejected"):
        return None
    return user.get("profile_image")


class TipRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500, description="Tip in CAD (max $500)")


async def create_demo_drivers(vehicle_type_id: str, lat: float, lng: float):
    """DEPRECATED — intentionally a no-op.

    This used to insert 3 fake driver rows with user_id=NULL whenever the
    dispatch RPC returned zero drivers. That turned the `drivers` table into
    a junkyard of orphan rows that polluted the rider-app's home-map pins,
    inflated `/rides/estimate` driver counts for vehicle types where no real
    driver was online, and wasted dispatch cycles on drivers that could
    never be notified (no user_id = no WebSocket key = no push token).

    The dispatch path no longer calls this function. Keeping the symbol
    exported as a no-op so any stale import from callers outside this file
    still resolves and short-circuits instead of inserting garbage rows.
    Delete entirely once you've confirmed no other module references it.
    """
    logger.warning(
        "[DISPATCH] create_demo_drivers was called but is deprecated and does nothing. "
        f"vehicle_type_id={vehicle_type_id} pickup=({lat},{lng})"
    )
    return


# Cap the no-driver re-dispatch chain. At 10s/attempt this is ~5 min, matching
# the stuck-ride sweeper's cancel window — defense-in-depth so a sweeper failure
# can't leave a ride re-dispatching (and re-querying drivers) forever.
_MAX_DISPATCH_ATTEMPTS = 30

# Escalating re-arm delays for dispatch ERRORS (DB blip mid-attempt): back off
# 10s → 30s → 60s so a struggling dependency isn't hammered at a fixed cadence.
# The healthy no-drivers re-poll stays at 10s — that path is waiting for supply,
# not for a dependency to recover.
_DISPATCH_ERROR_BACKOFF = (10, 30, 60)


def _dispatch_error_delay(attempt: int) -> int:
    """Delay before re-arming after a FAILED dispatch attempt (not the
    no-drivers poll): 10s, 30s, then 60s for every later attempt."""
    return _DISPATCH_ERROR_BACKOFF[min(max(attempt, 0), len(_DISPATCH_ERROR_BACKOFF) - 1)]


async def _dispatch_retry(ride_id: str, delay: int = 10, *, attempt: int = 1) -> None:
    """Re-attempt dispatch after a delay. Stops if the ride left searching or the
    per-ride attempt cap is reached (the stuck-ride sweeper then owns resolution)."""
    await asyncio.sleep(delay)
    if attempt > _MAX_DISPATCH_ATTEMPTS:
        logger.warning(
            f"[DISPATCH] ride {ride_id} hit {_MAX_DISPATCH_ATTEMPTS} dispatch attempts — "
            f"stopping retries; stuck-ride sweeper will resolve it"
        )
        return
    try:
        ride = await db_supabase.get_ride(ride_id)
        if not ride or ride.get("status") != RideStatus.SEARCHING:
            return
        logger.info(f"[DISPATCH] retry {attempt} for ride {ride_id}")
        await match_driver_to_ride(ride_id, ride=ride, attempt=attempt)
    except Exception as e:
        # Keep the chain alive on transient failures (DB blip, Redis hiccup):
        # ending it here would strand the ride in `searching` until the
        # stuck-ride sweeper cancels it. The attempt cap above bounds this;
        # the escalating delay stops a broad outage becoming a retry storm.
        logger.error(f"[DISPATCH] retry failed for {ride_id}: {e}", exc_info=True)
        spawn(_dispatch_retry(ride_id, delay=_dispatch_error_delay(attempt), attempt=attempt + 1))


async def match_driver_to_ride(ride_id: str, *, ride: Optional[dict] = None, attempt: int = 0):
    """Dispatch a driver for ``ride_id`` — recovery shell.

    Runs one dispatch attempt and, if it raises, re-arms ``_dispatch_retry``
    with escalating backoff (10s/30s/60s, bounded by the attempt cap). This is
    the single recovery chokepoint for EVERY dispatch entry point — booking,
    offer-timeout re-dispatch, and scheduled dispatch — so a transient failure
    anywhere in an attempt can't strand the ride in ``searching`` until the
    stuck-ride sweeper cancels it. Callers that catch exceptions around this
    function will never see one; recovery is owned here.

    If offers from this attempt are already pending when the failure hits
    (e.g. a WS/notify error after the ride_offers insert), no retry is armed —
    the offer-timeout handlers own progression from there, and re-arming would
    over-offer beyond max_offers.
    """
    try:
        await _match_driver_to_ride_attempt(ride_id, ride=ride, attempt=attempt)
    except Exception:
        logger.error(
            f"[DISPATCH] attempt {attempt} failed mid-dispatch for ride {ride_id} — re-arming retry with backoff",
            exc_info=True,
        )
        try:
            _pending = await db_supabase.get_rows("ride_offers", {"ride_id": ride_id, "status": "pending"}, limit=1)
        except Exception:
            # Can't tell — prefer re-arming: _dispatch_retry re-checks ride
            # status and claimed drivers are excluded, so a duplicate arm is
            # near-idempotent, while NOT arming risks a stranded ride.
            _pending = []
        if not _pending:
            spawn(_dispatch_retry(ride_id, delay=_dispatch_error_delay(attempt), attempt=attempt + 1))


async def _match_driver_to_ride_attempt(ride_id: str, *, ride: Optional[dict] = None, attempt: int = 0):
    """One dispatch attempt for ``ride_id`` (raises on mid-attempt failure;
    the ``match_driver_to_ride`` shell owns recovery).

    ``ride`` may be passed when the caller already has the fresh row
    (e.g. straight after ``insert_ride``) so we skip a redundant
    ``get_ride`` round-trip. When omitted, the ride is re-fetched —
    needed by the offer-timeout and scheduled-ride paths that run
    much later and must see the current state.
    """
    if ride is None:
        ride = await db_supabase.get_ride(ride_id)
    if not ride:
        logger.error(f"[DISPATCH] match_driver_to_ride: ride {ride_id} not found")
        return

    # C2: never dispatch a ride that is no longer searching. The offer-timeout
    # re-dispatch path runs ~15 awaits after its own status check, so a driver
    # accept can land in that window and flip the ride to driver_accepted;
    # without this guard we would claim fresh drivers and emit phantom offers on
    # an already-live ride (ride-state invariant violation) and needlessly pull
    # available drivers offline. Every caller either passes a fresh searching
    # ride (booking) or re-fetches after ensuring searching (scheduled flips
    # scheduled→searching first; offer-timeout / decline re-dispatch re-fetch),
    # so this only ever skips a genuinely stale (already-accepted) ride.
    if ride.get("status") != RideStatus.SEARCHING:
        logger.info(
            "[DISPATCH] skipping dispatch for ride %s — status is %s, not searching",
            ride_id,
            ride.get("status"),
        )
        return

    # Refuse to dispatch a ride with missing coordinates — the driver-app
    # cannot render the map polyline and would either drop the offer or
    # plot (0,0) (Gulf of Guinea). Surfacing loudly per CLAUDE.md ("Do not
    # silently swallow errors"); insurance Period 2 also requires a known
    # origin/destination.
    _coords = [
        ride.get("pickup_lat"),
        ride.get("pickup_lng"),
        ride.get("dropoff_lat"),
        ride.get("dropoff_lng"),
    ]
    if any(c is None for c in _coords):
        logger.error(
            f"[DISPATCH] ride {ride_id} has missing coordinates "
            f"pickup=({_coords[0]},{_coords[1]}) dropoff=({_coords[2]},{_coords[3]}) — aborting dispatch",
        )
        return

    # Single app_settings fetch — used both for matching config (via
    # DispatchService.resolve_matching_config) and for the offer-timeout
    # lookup at the end. Previously this loaded twice; the dead
    # ``get_rows("service_areas", {"id": ...})`` call that followed has
    # been removed — resolve_matching_config does its own find_one
    # against the same table.
    app_settings = await get_app_settings()
    # Compute offer timeout early so it can be embedded in dispatch payloads —
    # driver-app uses this for the per-offer countdown instead of a cached
    # value, which drifted when admin changed the setting mid-session.
    offer_timeout = int(app_settings.get("ride_offer_timeout_seconds", 15))

    # Algorithm + radius + rating floor + batch config (area overrides app settings).
    algorithm, min_rating, search_radius, max_offers, use_eta = await dispatch.resolve_matching_config(
        ride, app_settings=app_settings
    )

    # PIPEDA: do not log raw pickup lat/lng — coordinates are forbidden in logs
    # (and this line fires on every dispatch). Correlate by ride_id only.
    logger.info(
        f"[DISPATCH] match start ride_id={ride_id} "
        f"vehicle_type_id={ride['vehicle_type_id']} algorithm={algorithm} "
        f"radius_km={search_radius} batch={max_offers} eta={use_eta}"
    )

    # Find candidate drivers. We read the drivers table directly and filter
    # in Python using the legacy lat/lng columns — same pattern as /rides/estimate.
    # We deliberately DO NOT use the find_nearby_drivers RPC because it reads
    # the PostGIS `location` column, which update_driver_location does not
    # populate, so the RPC would always return zero drivers.
    #
    # We also require user_id IS NOT NULL to skip legacy "demo" driver rows
    # that lack a real user and can never be notified.
    # Mirror DispatchService.find_candidate_drivers: is_verified + status='active'
    # keep unverified / suspended / needs_review drivers out of dispatch even if
    # their is_online flag was left on (e.g. status flipped server-side after
    # they toggled online). Without these, accept_ride blocks them at accept time
    # but they still receive — and can see — offers they can never fulfil.
    # Bounding-box pre-filter. Without it the LIMIT 500 below is an
    # *arbitrary* 500 of all online drivers province-wide — above 500
    # candidates the nearest driver can sit in row 501 and dispatch reports a
    # false "no drivers". The box is a superset of the search radius;
    # filter_and_rank_drivers stays the exact haversine gate. Anchored on the
    # same nav-snapped pickup that filter_and_rank_drivers ranks against.
    #
    # No dedicated (lat, lng) index — deliberate (PR #2028 review):
    # idx_drivers_online_available_recency (migration 138, partial WHERE
    # is_online AND is_available) already bounds this scan to the online
    # fleet, so the box predicates only filter within that small walk; and
    # drivers already carries a trigger-maintained PostGIS location_geog +
    # partial GiST index (migration 170) — a future radius query should go
    # through an RPC on that column rather than a second btree that every
    # location heartbeat would have to maintain.
    _box_lat = ride["pickup_nav_lat"] if ride.get("pickup_nav_lat") is not None else ride["pickup_lat"]
    _box_lng = ride["pickup_nav_lng"] if ride.get("pickup_nav_lng") is not None else ride["pickup_lng"]
    _dispatch_filter: dict = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "vehicle_type_id": ride["vehicle_type_id"],
        "$and": dispatch_geo_bounds(_box_lat, _box_lng, search_radius),
    }
    if ride.get("requires_wav"):
        _dispatch_filter["is_wav"] = True
    # A transient Supabase failure here is NOT "no drivers" — it raises to the
    # match_driver_to_ride recovery shell, which re-arms the retry chain with
    # backoff instead of letting the ride strand until the sweeper cancels it.
    all_drivers = await db_supabase.get_rows(
        "drivers",
        _dispatch_filter,
        # P1: project only what ranking/filtering reads — NOT "*". The full row
        # carries encrypted PII (address, licence, vehicle details) that this hot
        # path (up to 500 rows every dispatch + retry, every replica) never needs;
        # the offer payload is built from the post-claim get_driver_by_id re-read.
        columns="id,user_id,lat,lng,rating,is_wav,acceptance_rate,destination_mode,destination_lat,destination_lng,vehicle_type_id",
        limit=500,
    )

    logger.info(
        f"[DISPATCH] candidate pool (pre-filter): {len(all_drivers)} drivers "
        f"matching vehicle_type_id + online + available within {search_radius}km box"
    )
    if len(all_drivers) >= 500:
        # Even inside the box the pool is truncated — the dropped rows are
        # in-radius candidates, so ranking quality degrades. Surface it.
        logger.warning(
            f"[DISPATCH] candidate pool hit the 500-row cap inside the "
            f"{search_radius}km box for ride {ride_id} — pool truncated"
        )

    # Presence filter: only dispatch to drivers whose WebSocket heartbeat is
    # still alive (Uber/Lyft-style). Matches the filter applied in the rider-
    # facing /drivers/nearby endpoint so the cars a rider sees on the map are
    # exactly the drivers who can receive an offer.
    # Presence filter: only dispatch to drivers whose Redis heartbeat key is
    # alive. We distinguish two empty-set cases:
    #   - Redis reachable, set empty → all candidates' heartbeats have expired
    #     (ghost drivers) → apply the filter so they get no offer
    #   - Redis unavailable (in-process fallback or connection error) → skip
    #     the filter so a Redis outage can't halt dispatching entirely
    try:
        try:
            from ..utils.driver_presence import present_driver_ids as _present_ids  # type: ignore
            from ..utils.redis_client import _get_redis as _check_redis  # type: ignore
        except ImportError:
            from utils.driver_presence import present_driver_ids as _present_ids  # type: ignore
            from utils.redis_client import _get_redis as _check_redis  # type: ignore
        _redis_live = await _check_redis() is not None
        _present_ids_set = await _present_ids([d["id"] for d in all_drivers])
        if _redis_live:
            before_presence = len(all_drivers)
            all_drivers = [d for d in all_drivers if d["id"] in _present_ids_set]
            logger.info(f"[DISPATCH] presence filter: {len(all_drivers)}/{before_presence} driver(s) reachable")
        else:
            logger.warning("[DISPATCH] Redis unavailable — presence filter skipped, using all DB-online drivers")
    except Exception as _pres_exc:
        logger.warning(f"[DISPATCH] presence filter failed, using all DB-online drivers: {_pres_exc}")

    # Skip drivers who recently timed out or declined this specific offer
    # so the same driver is not hammered with repeat notifications. Batch the
    # lookups into one MGET — the old per-candidate redis_get was an N+1 on the
    # dispatch hot path (N round-trips per attempt per replica).
    try:
        from ..utils.redis_client import redis_mget as _redis_mget  # type: ignore
    except ImportError:
        from utils.redis_client import redis_mget as _redis_mget  # type: ignore
    _skip_keys = [f"spinr:offer_skip:{ride_id}:{_d['id']}" for _d in all_drivers]
    _skip_vals = await _redis_mget(_skip_keys)
    _skip_ids: set = {_d["id"] for _d, _v in zip(all_drivers, _skip_vals, strict=False) if _v}
    if _skip_ids:
        all_drivers = [d for d in all_drivers if d["id"] not in _skip_ids]
        logger.info(f"[DISPATCH] skipped {len(_skip_ids)} driver(s) with recent timeout/decline for ride {ride_id}")

    # Subscription guard: if the ride's service area requires a Spinr Pass,
    # filter out candidates without an active subscription.  One batch IN
    # query — no N+1 per driver.  Fails open on DB error so a transient fault
    # cannot halt dispatch entirely; the accept_ride guard is the backstop.
    _sub_required: bool = False  # pre-init so cascade block can read it if try block skips
    if all_drivers and ride.get("service_area_id"):
        try:
            _disp_area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            # Finding F: child areas (airport sub-regions) inherit subscription_required
            # from their parent so airport pickups in a required city don't bypass the gate.
            _sub_required = bool(_disp_area and _disp_area.get("subscription_required"))
            if not _sub_required and _disp_area and _disp_area.get("parent_service_area_id"):
                _parent_area = await db_supabase.find_one("service_areas", {"id": _disp_area["parent_service_area_id"]})
                _sub_required = bool(_parent_area and _parent_area.get("subscription_required"))
            if _sub_required:
                _candidate_ids = [d["id"] for d in all_drivers]
                _active_subs = await db_supabase.get_rows(
                    "driver_subscriptions",
                    {"driver_id": {"$in": _candidate_ids}, "status": "active"},
                    columns="driver_id,expires_at,plan_id",
                    limit=len(_candidate_ids),
                )
                _now_utc = datetime.now(timezone.utc)
                # Filter by expiry; guard None from parse_iso_utc so one malformed
                # row can't zero out all candidates via TypeError → except → all_drivers=[].
                _valid_subs = []
                for _s in _active_subs or []:
                    if _s.get("expires_at"):
                        _exp = parse_iso_utc(_s["expires_at"])
                        if _exp is not None and _exp <= _now_utc:
                            continue  # expired
                    _valid_subs.append(_s)
                # Finding B: also filter by plan service_area scope so drivers with a
                # pass for another area don't receive offers they'll 402 on acceptance.
                _plan_ids = {_s["plan_id"] for _s in _valid_subs if _s.get("plan_id")}
                _plan_areas: dict = {}
                if _plan_ids:
                    _plans = await db_supabase.get_rows(
                        "subscription_plans",
                        {"id": {"$in": list(_plan_ids)}},
                        columns="id,service_areas",
                        limit=len(_plan_ids),
                    )
                    _plan_areas = {p["id"]: p.get("service_areas") for p in (_plans or [])}
                _ride_service_area = ride["service_area_id"]
                # A plan scoped to the parent area is valid for child (e.g. airport) areas.
                _ride_parent_area_id = (_disp_area or {}).get("parent_service_area_id")
                _subscribed_ids = set()
                for _s in _valid_subs:
                    _allowed = _plan_areas.get(_s.get("plan_id"))
                    if _allowed and _ride_service_area not in _allowed:
                        # Accept if the plan covers the parent area.
                        if not (_ride_parent_area_id and _ride_parent_area_id in _allowed):
                            continue
                    _subscribed_ids.add(_s["driver_id"])
                _before = len(all_drivers)
                all_drivers = [d for d in all_drivers if d["id"] in _subscribed_ids]
                logger.info(
                    "[DISPATCH] subscription filter: area=%s kept %d/%d drivers",
                    ride["service_area_id"],
                    len(all_drivers),
                    _before,
                )
        except Exception:
            logger.error(
                "[DISPATCH] subscription filter failed for area=%s — aborting dispatch attempt",
                ride.get("service_area_id"),
                exc_info=True,
            )
            all_drivers = []  # fail closed; the no-drivers path below schedules a retry

    # Daily Spinr Pass ride-allowance filter (all areas). Mirrors the gate in
    # DispatchService.find_candidate_drivers, but on the LIVE dispatch path:
    # drop finite-pass drivers who've used today's rides so they don't receive
    # an offer they'd 403 on at accept (wasting a dispatch cycle + pinging a
    # driver who can't take it). Fails OPEN — go-online/accept still gate, so a
    # transient read error must not drop everyone like the subscription filter.
    #
    # Timezone anchor: this filter uses the RIDE's service-area timezone for the
    # calendar-day window, whereas the per-driver gates (go-online, accept,
    # force-offline, /subscription/current) use the DRIVER's home service area.
    # These coincide in a single-timezone deployment (SK today). Across a tz
    # boundary they can differ by ≤1h only in the window between the two local
    # midnights; both paths fail open, so the worst case is one extra/fewer offer
    # near that boundary — never an overcharge or a stranded driver. If Spinr
    # launches in a second timezone, unify on the driver's home area here.
    if all_drivers and ride.get("service_area_id"):
        try:
            try:
                from ..utils.spinr_pass import area_timezone, exhausted_driver_ids
            except ImportError:
                from utils.spinr_pass import area_timezone, exhausted_driver_ids  # type: ignore

            _q_ids = [d["id"] for d in all_drivers]
            _q_subs = await db_supabase.get_rows(
                "driver_subscriptions",
                {"driver_id": {"$in": _q_ids}, "status": "active"},
                columns="driver_id,started_at,expires_at,rides_per_day",
                limit=len(_q_ids),
            )
            if _q_subs:
                _q_tz = await area_timezone(ride["service_area_id"])
                _q_exhausted = await exhausted_driver_ids(_q_subs, tz=_q_tz)
                if _q_exhausted:
                    _q_before = len(all_drivers)
                    all_drivers = [d for d in all_drivers if d["id"] not in _q_exhausted]
                    logger.info(
                        "[DISPATCH] quota filter: area=%s kept %d/%d drivers (%d quota-exhausted)",
                        ride["service_area_id"],
                        len(all_drivers),
                        _q_before,
                        len(_q_exhausted),
                    )
        except Exception:
            logger.error(
                "[DISPATCH] quota filter failed for area=%s — dispatching unfiltered by quota",
                ride.get("service_area_id"),
                exc_info=True,
            )

    # Pure filter+rank: drops orphan/no-location/low-rated drivers and
    # attaches per-driver distance. Pure function — no I/O.
    drivers_with_distance = filter_and_rank_drivers(ride, all_drivers, algorithm, min_rating, search_radius)
    logger.info(
        f"[DISPATCH] candidate pool (post-filter): {len(drivers_with_distance)} "
        f"real drivers within {search_radius}km with valid lat/lng and "
        f"rating>={min_rating if algorithm in ('rating_based', 'combined') else 'n/a'}"
    )

    if not drivers_with_distance and ride.get("service_area_id") and ride.get("vehicle_type_id"):
        # Vehicle cascade: the area may define upgrade types to try when no
        # driver of the exact requested type is available (e.g. SUV → XL).
        # This runs after all other filters so only genuinely-available
        # upgrade drivers are offered the ride.
        try:
            _casc_area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            _casc_map: list = (_casc_area or {}).get("vehicle_cascade_map") or []
            # Fix 6: child areas (airport sub-regions) inherit parent's cascade map
            # when the child row has none configured — mirrors subscription_required inheritance.
            if not _casc_map and (_casc_area or {}).get("parent_service_area_id"):
                _casc_parent = await db_supabase.find_one("service_areas", {"id": _casc_area["parent_service_area_id"]})
                _casc_map = (_casc_parent or {}).get("vehicle_cascade_map") or []
            _casc_to: list = next(
                (rule.get("to") or [] for rule in _casc_map if rule.get("from") == ride["vehicle_type_id"]),
                [],
            )
            if _casc_to:
                logger.info(
                    "[DISPATCH] cascade: ride %s — no %s drivers, trying upgrade types %s",
                    ride_id,
                    ride["vehicle_type_id"],
                    _casc_to,
                )
                _casc_filter: dict = {
                    "is_online": True,
                    "is_available": True,
                    "is_verified": True,
                    "status": "active",
                    "vehicle_type_id": {"$in": _casc_to},
                    # Same geo box as the primary pool — an un-bounded LIMIT 500
                    # here has the identical row-501 false-negative failure mode.
                    "$and": dispatch_geo_bounds(_box_lat, _box_lng, search_radius),
                }
                if ride.get("requires_wav"):
                    _casc_filter["is_wav"] = True
                _casc_pool = await db_supabase.get_rows(
                    "drivers",
                    _casc_filter,
                    columns="id,user_id,lat,lng,rating,is_wav,acceptance_rate,destination_mode,destination_lat,destination_lng,vehicle_type_id",
                    limit=500,
                )
                # Fix 4: Presence filter using _checked variant so a Redis outage
                # (configured-but-unavailable) cannot silently empty the cascade pool.
                # present_driver_ids_checked returns (set, reachable=False) on failure;
                # we only apply the filter when the presence store was actually reached.
                try:
                    try:
                        from ..utils.driver_presence import (
                            present_driver_ids_checked as _casc_presence_checked,  # type: ignore
                        )
                        from ..utils.redis_client import redis_mget as _casc_mget
                    except ImportError:
                        from utils.driver_presence import (
                            present_driver_ids_checked as _casc_presence_checked,  # type: ignore
                        )
                        from utils.redis_client import redis_mget as _casc_mget
                    _casc_present_set, _casc_reachable = await _casc_presence_checked([d["id"] for d in _casc_pool])
                    if _casc_reachable:
                        _casc_pool = [d for d in _casc_pool if d["id"] in _casc_present_set]
                    else:
                        logger.warning(
                            "[DISPATCH] cascade: Redis unavailable — presence filter skipped for ride %s",
                            ride_id,
                        )
                    # Skip drivers who already timed-out / declined this ride
                    _casc_skip_keys = [f"spinr:offer_skip:{ride_id}:{d['id']}" for d in _casc_pool]
                    _casc_skip_vals = await _casc_mget(_casc_skip_keys)
                    _casc_pool = [d for d, v in zip(_casc_pool, _casc_skip_vals, strict=False) if not v]
                except Exception as _casc_redis_exc:
                    logger.debug("[DISPATCH] cascade Redis filter skipped (unavailable): %s", _casc_redis_exc)
                # Fix 2: apply subscription filter to cascade pool when the service area
                # requires a Spinr Pass — cascade must not offer rides to non-subscribers.
                if _sub_required and _casc_pool:
                    try:
                        _casc_cand_ids = [d["id"] for d in _casc_pool]
                        _casc_subs = await db_supabase.get_rows(
                            "driver_subscriptions",
                            {"driver_id": {"$in": _casc_cand_ids}, "status": "active"},
                            columns="driver_id,expires_at,plan_id",
                            limit=len(_casc_cand_ids),
                        )
                        _casc_now = datetime.now(timezone.utc)
                        _casc_valid_subs = []
                        for _cs in _casc_subs or []:
                            if _cs.get("expires_at"):
                                _cs_exp = parse_iso_utc(_cs["expires_at"])
                                if _cs_exp is not None and _cs_exp <= _casc_now:
                                    continue
                            _casc_valid_subs.append(_cs)
                        _casc_plan_ids = {_cs["plan_id"] for _cs in _casc_valid_subs if _cs.get("plan_id")}
                        _casc_plan_areas: dict = {}
                        if _casc_plan_ids:
                            _casc_plans = await db_supabase.get_rows(
                                "subscription_plans",
                                {"id": {"$in": list(_casc_plan_ids)}},
                                columns="id,service_areas",
                                limit=len(_casc_plan_ids),
                            )
                            _casc_plan_areas = {p["id"]: p.get("service_areas") for p in (_casc_plans or [])}
                        _casc_sa_id = ride["service_area_id"]
                        _casc_parent_sa_id = (_casc_area or {}).get("parent_service_area_id")
                        _casc_subscribed: set = set()
                        for _cs in _casc_valid_subs:
                            _cs_allowed = _casc_plan_areas.get(_cs.get("plan_id"))
                            if _cs_allowed and _casc_sa_id not in _cs_allowed:
                                if not (_casc_parent_sa_id and _casc_parent_sa_id in _cs_allowed):
                                    continue
                            _casc_subscribed.add(_cs["driver_id"])
                        _casc_before_sub = len(_casc_pool)
                        _casc_pool = [d for d in _casc_pool if d["id"] in _casc_subscribed]
                        logger.info(
                            "[DISPATCH] cascade subscription filter: kept %d/%d drivers for ride %s",
                            len(_casc_pool),
                            _casc_before_sub,
                            ride_id,
                        )
                    except Exception:
                        logger.error(
                            "[DISPATCH] cascade subscription filter failed for area=%s — failing closed",
                            ride.get("service_area_id"),
                            exc_info=True,
                        )
                        _casc_pool = []  # fail closed; non-subscriber cascade would bypass the gate
                drivers_with_distance = filter_and_rank_drivers(ride, _casc_pool, algorithm, min_rating, search_radius)
                if drivers_with_distance:
                    logger.info(
                        "[DISPATCH] cascade found %d eligible driver(s) for ride %s",
                        len(drivers_with_distance),
                        ride_id,
                    )
                else:
                    logger.info("[DISPATCH] cascade also found no eligible drivers for ride %s", ride_id)
        except Exception:
            logger.error("[DISPATCH] cascade lookup failed for ride %s", ride_id, exc_info=True)

    if not drivers_with_distance:
        logger.info(f"[DISPATCH] no eligible drivers for ride {ride_id} — scheduling retry in 10s (attempt {attempt})")
        spawn(_dispatch_retry(ride_id, delay=10, attempt=attempt + 1))
        return

    # ── ETA ranking ───────────────────────────────────────────────
    # Pre-filter to top 15 by haversine, then batch-query Distance
    # Matrix for real ETAs. Falls back to haversine if API fails.
    drivers_with_distance.sort(key=lambda x: x[1])
    pre_filtered = drivers_with_distance[: max(max_offers * 5, 15)]

    if use_eta:
        try:
            maps_key = app_settings.get("google_maps_api_key", "")
            eta_map = await asyncio.wait_for(
                batch_get_etas(
                    [d for d, _ in pre_filtered],
                    # ETA to the road-snapped pickup the driver actually drives to.
                    ride["pickup_nav_lat"] if ride.get("pickup_nav_lat") is not None else ride["pickup_lat"],
                    ride["pickup_nav_lng"] if ride.get("pickup_nav_lng") is not None else ride["pickup_lng"],
                    maps_key,
                ),
                # P3: bound the external Distance-Matrix call to the dispatch
                # budget — its own _MAPS_TIMEOUT is 3s, too long for the <2s
                # offer clock. A TimeoutError is caught below → haversine
                # ranking, so a slow Maps API can't blow the dispatch SLA.
                timeout=1.2,
            )
            ranked = rank_by_eta_with_acceptance([(d, eta_map.get(d["id"], 9999)) for d, _ in pre_filtered])
        except Exception as e:
            logger.error(f"[DISPATCH] ETA ranking failed, falling back to haversine: {e}", exc_info=True)
            ranked = [(d, int(dist * 120), dist) for d, dist in pre_filtered]
    else:
        ranked = [(d, int(dist * 120), dist) for d, dist in pre_filtered]

    # ── Batch claim ───────────────────────────────────────────────
    claimed_drivers: list[tuple[dict, int]] = []
    for driver, eta_sec, _ in ranked:
        if len(claimed_drivers) >= max_offers:
            break
        if await db_supabase.claim_driver_atomic(driver["id"]):
            fresh = await db_supabase.get_driver_by_id(driver["id"])
            # Revalidate the FULL eligibility set on the freshly-read row, not
            # just is_online. claim_driver_atomic only guards id + is_available,
            # so an admin who suspended the driver or flipped them back to
            # needs_review between the candidate read and the claim would
            # otherwise still get offered — the exact stale-status case the
            # candidate filter (is_verified + status='active') is meant to stop.
            if fresh and fresh.get("is_online") and fresh.get("is_verified") and fresh.get("status") == "active":
                claimed_drivers.append((fresh, eta_sec))
            else:
                await db_supabase.set_driver_available(driver["id"], True)

    if not claimed_drivers:
        logger.info(f"[DISPATCH] no drivers could be claimed for ride {ride_id}")
        return

    logger.info(
        f"[DISPATCH] batch: claimed {len(claimed_drivers)} driver(s) for ride {ride_id}: "
        f"{[d['id'] for d, _ in claimed_drivers]}"
    )

    # ── Insert ride_offers rows ───────────────────────────────────
    now = datetime.now(timezone.utc)
    offer_rows = [
        {
            "ride_id": ride_id,
            "driver_id": d["id"],
            "status": "pending",
            "eta_seconds": eta,
            "offered_at": now.isoformat(),
        }
        for d, eta in claimed_drivers
    ]
    try:
        await db_supabase.run_sync(lambda: db_supabase.supabase.table("ride_offers").insert(offer_rows).execute())
    except Exception as e:
        logger.error(f"[DISPATCH] ride_offers insert failed: {e}", exc_info=True)
        for d, _ in claimed_drivers:
            await db_supabase.set_driver_available(d["id"], True)
        # Re-raise after releasing the claims: no offers exist, so the
        # recovery shell re-arms the retry chain instead of stranding the
        # ride in `searching` (the old `return` armed nothing).
        raise
    _metric_inc("spinr_dispatch_offer_sent_total", by=len(offer_rows))

    # ── Parallel enrichment (shared across all drivers) ───────────
    async def _fetch_rider() -> dict | None:
        try:
            return await db_supabase.get_user_by_id(ride["rider_id"])
        except Exception as e:
            logger.error(f"[DISPATCH] could not load rider user {ride['rider_id']}: {e}", exc_info=True)
            return None

    async def _fetch_incentives() -> tuple[list, float]:
        try:
            iq = (
                db_supabase.supabase.table("ride_incentives")
                .select(
                    "id, name, bonus_amount, incentive_type, bonus_type, conditions, service_area_id, vehicle_type_id"
                )
                .eq("is_active", True)
            )
            sa_id = ride.get("service_area_id")
            if sa_id:
                iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
            ir = await db_supabase.run_sync(iq.execute)
            vt_id = ride.get("vehicle_type_id")
            incentives = []
            total_bonus = 0.0
            for inc in ir.data or []:
                if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
                    continue
                ba = float(inc.get("bonus_amount") or 0)
                incentives.append(
                    {
                        "name": inc["name"],
                        "bonus_amount": ba,
                        "incentive_type": inc.get("incentive_type", "per_ride"),
                    }
                )
                total_bonus += ba
            return incentives, total_bonus
        except Exception as e:
            logger.error(f"[DISPATCH] incentive lookup failed: {e}", exc_info=True)
            return [], 0.0

    async def _fetch_service_area_polygon() -> Optional[list]:
        sa_id = ride.get("service_area_id")
        if not sa_id:
            return None
        try:
            sa = await db_supabase.find_one("service_areas", {"id": sa_id})
            poly = get_service_area_polygon(sa or {})
            return poly or None
        except Exception as e:
            logger.warning("[DISPATCH] service_area polygon fetch failed: %s", e)
            return None

    rider_user, (_incentives, _total_bonus), _service_area_polygon = await asyncio.gather(
        _fetch_rider(),
        _fetch_incentives(),
        _fetch_service_area_polygon(),
    )

    # PIPEDA (C5): the driver sees the rider's FIRST name only (never the legal
    # surname), and only via the WebSocket payload below — rider_name is
    # stripped from the FCM data payload further down.
    rider_display_name = first_name_only(rider_user) or None

    _surge_mult = float(ride.get("surge_multiplier") or 1.0)
    from datetime import timedelta as _td

    _offer_expires_at = (now + _td(seconds=offer_timeout)).isoformat()

    # ── Notify each claimed driver ────────────────────────────────
    for driver, _eta in claimed_drivers:
        # Per-driver quest progress
        _quest_hint = None
        try:
            driver_uid = driver.get("user_id")
            if driver_uid:
                qr = await db_supabase.run_sync(
                    db_supabase.supabase.table("quest_progress")
                    .select("current_value, status, quest:quests(title, target_value, reward_amount)")
                    .eq("driver_id", driver_uid)
                    .eq("status", "active")
                    .limit(1)
                    .execute
                )
                if qr.data:
                    qp = qr.data[0]
                    q = qp.get("quest") or {}
                    tv = float(q.get("target_value") or 1)
                    cv = float(qp.get("current_value") or 0)
                    _quest_hint = {
                        "title": q.get("title", ""),
                        "current_value": cv,
                        "target_value": tv,
                        "progress_pct": round(min(cv / tv, 1.0) * 100, 1) if tv else 0,
                        "reward_amount": float(q.get("reward_amount") or 0),
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch quest progress for driver {driver['id']}: {e}")

        # Per-driver signed URL for the notification's BigPicture fare banner.
        # Bound to this ride + driver and short-lived; rendered on demand by
        # routes/offer_card.py (never here, to keep the dispatch hot path fast).
        _offer_card_url = None
        try:
            _oc_token = sign_offer_card_token(
                ride_id=ride_id,
                driver_id=str(driver.get("user_id") or driver.get("id") or ""),
            )
            _offer_card_url = (
                f"{_settings.PUBLIC_API_BASE_URL.rstrip('/')}/api/v1/offer-cards/{ride_id}.png?t={_oc_token}"
            )
        except Exception as e:
            logger.warning("[DISPATCH] offer-card URL build failed for ride %s: %s", ride_id, e)

        dispatch_payload = {
            "type": "new_ride_assignment",
            "ride_id": ride_id,
            "offer_card_url": _offer_card_url,
            "pickup_address": ride.get("pickup_address"),
            "dropoff_address": ride.get("dropoff_address"),
            "pickup_lat": ride.get("pickup_lat"),
            "pickup_lng": ride.get("pickup_lng"),
            # Road-snapped pickup for driver navigation (falls back to pickup_*).
            "pickup_nav_lat": ride.get("pickup_nav_lat"),
            "pickup_nav_lng": ride.get("pickup_nav_lng"),
            "dropoff_lat": ride.get("dropoff_lat"),
            "dropoff_lng": ride.get("dropoff_lng"),
            "fare": ride.get("driver_earnings"),
            "distance_km": ride.get("distance_km"),
            "duration_minutes": ride.get("duration_minutes"),
            "rider_name": rider_display_name,
            "rider_rating": (rider_user or {}).get("rating"),
            "rider_profile_image": (rider_user or {}).get("profile_image"),
            "requires_wav": bool(ride.get("requires_wav")),
            "countdown_seconds": offer_timeout,
            "offer_expires_at": _offer_expires_at,
            "surge_multiplier": _surge_mult if _surge_mult > 1.0 else None,
            "incentives": _incentives if _incentives else None,
            "total_bonus": _total_bonus if _total_bonus > 0 else None,
            "quest_hint": _quest_hint,
            "payment_method": ride.get("payment_method"),
            "planned_route_polyline": ride.get("planned_route_polyline") or None,
            "service_area_polygon": _service_area_polygon,
        }

        if driver.get("user_id"):
            await manager.send_personal_message(dispatch_payload, f"driver_{driver['user_id']}")
            try:
                # Exclude large spatial fields from FCM data payload — FCM
                # enforces a 4 KB data-message limit and detailed polygons/
                # polylines can easily blow it. Drivers receive these via the
                # WebSocket message (dispatch_payload) which has no size cap.
                # PIPEDA (C5): rider_name is excluded too — no rider name may
                # ride in an FCM data payload (cleartext in the device tray,
                # Google/US infra). The driver gets the first name via the WS
                # message (dispatch_payload) only.
                _FCM_EXCLUDE = {
                    "service_area_polygon",
                    "planned_route_polyline",
                    "rider_profile_image",
                    "rider_name",
                }
                fcm_data = {
                    k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else ""
                    for k, v in dispatch_payload.items()
                    if k not in _FCM_EXCLUDE
                }
                fcm_data["deeplink"] = "/driver/"
                fcm_data["booking_id"] = str(ride_id)

                pickup_label = ride.get("pickup_address") or "Nearby pickup"
                dropoff_label = ride.get("dropoff_address") or "destination"
                try:
                    earnings_label = f"${float(ride.get('driver_earnings') or 0):.2f}"
                except (TypeError, ValueError):
                    earnings_label = "New fare"

                # Fire the push without blocking the per-driver offer loop —
                # send_push_notification now delivers inline (≈100–300 ms FCM
                # round-trip), and we don't want N drivers serialized on it.
                # The WebSocket offer above already reached any foreground app;
                # this push covers backgrounded / locked / killed devices.
                spawn(
                    send_push_notification(
                        driver["user_id"],
                        f"New ride · {earnings_label}",
                        f"{pickup_label} → {dropoff_label}",
                        fcm_data,
                        priority="dispatch",
                        target_app="driver",
                    )
                )
            except Exception as e:
                logger.error(f"[DISPATCH] push failed for driver {driver['user_id']}: {e}", exc_info=True)

    # ── Batch timeout handler (no grace period) ───────────────────
    spawn(
        _batch_offer_timeout_handler(
            ride_id,
            rider_id=ride.get("rider_id"),
            timeout_seconds=offer_timeout,
        )
    )


async def _offer_timeout_handler(
    ride_id: str,
    driver_id: str,
    rider_id: str | None,
    timeout_seconds: int = 30,
):
    """Auto-expire a driver's ride offer if they don't accept/decline.

    Sleeps for `timeout_seconds` then checks whether the ride is still
    in `driver_assigned` status with this specific `driver_id`. If yes,
    releases the driver, sets the ride back to `searching`, notifies the
    rider, and re-dispatches.

    Consecutive misses are tracked per driver. When the streak reaches
    ``auto_offline_miss_threshold`` (app_settings, default 3) the driver
    is set fully offline instead of released back to the available pool —
    a sleeping driver stops absorbing offers that just expire.

    This mirrors the driver-app's client-side countdown timer but is
    authoritative — it fires even if the device crashes or loses network.
    The 15 s grace period between the driver-app countdown (default 15 s)
    and this handler (default 30 s = 15 + 15) avoids racing the
    client-side decline call.
    """
    try:
        from ..utils.driver_presence import (
            clear_presence,
            increment_miss_streak,
            reset_miss_streak,
        )
    except ImportError:
        from utils.driver_presence import (  # type: ignore
            clear_presence,
            increment_miss_streak,
            reset_miss_streak,
        )

    await asyncio.sleep(timeout_seconds)
    try:
        ride = await db.find_one("rides", {"id": ride_id})
        if not ride:
            return

        # Only act if the ride hasn't progressed past assignment.
        if ride.get("status") != RideStatus.DRIVER_ASSIGNED or ride.get("driver_id") != driver_id:
            return

        logger.info(
            f"[DISPATCH] Offer expired: ride {ride_id} driver {driver_id} "
            f"didn't respond within {timeout_seconds}s — re-searching"
        )

        # Track consecutive misses and decide whether to auto-offline.
        miss_count = await increment_miss_streak(driver_id)
        try:
            settings = await get_app_settings()
            miss_threshold = int(settings.get("auto_offline_miss_threshold", 3))
        except Exception:
            miss_threshold = 3

        auto_offline = miss_count >= miss_threshold

        if auto_offline:
            # Driver has missed N consecutive offers — take them offline.
            logger.info(
                f"[DISPATCH] Auto-offline: driver {driver_id} missed "
                f"{miss_count} consecutive offers (threshold={miss_threshold})"
            )
            await db.update_one(
                "drivers",
                {"id": driver_id},
                {"$set": {"is_online": False, "is_available": False}},
            )
            await clear_presence(driver_id)
            await reset_miss_streak(driver_id)
            # Period 0: driver is fully offline (personal insurance only).
            await record_period_transition(driver_id, 0)
        else:
            # Normal timeout — release driver back to the available pool.
            # Use set_driver_available() so the is_available ⇒ is_online
            # invariant is enforced (clamps to False if driver went offline
            # between the offer being sent and the timeout firing).
            released = await db_supabase.set_driver_available(driver_id, available=True)
            # Only record Period 1 (online, no ride) if the release actually
            # made the driver available. If they went offline between offer
            # dispatch and this timeout, set_driver_available clamps
            # is_available→False; their go-offline already logged Period 0, so
            # opening a Period 1 audit row here would falsely reopen an
            # online/commercial-insurance window for an offline driver.
            if isinstance(released, dict) and released.get("is_available"):
                await record_period_transition(driver_id, 1)

        # Put the ride back in the searching state so it can be
        # re-dispatched or picked up by the next dispatch cycle.
        await db.update_one(
            "rides",
            {"id": ride_id},
            {
                "$set": {
                    "status": RideStatus.SEARCHING,
                    "driver_id": None,
                    "driver_notified_at": None,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )

        # Notify rider via WebSocket.
        if rider_id:
            await manager.send_personal_message(
                {
                    "type": "driver_timeout",
                    "ride_id": ride_id,
                    "message": "Driver didn't respond. Finding another driver...",
                },
                f"rider_{rider_id}",
            )

        # Notify the driver. If auto-offlined, send a distinct event so
        # the app can show an explanation and flip its local state.
        try:
            driver_row = await db_supabase.get_driver_by_id(driver_id)
            driver_user_id = (driver_row or {}).get("user_id")
            if driver_user_id:
                if auto_offline:
                    await manager.send_personal_message(
                        {
                            "type": "auto_offline",
                            "reason": "missed_offers",
                            "miss_count": miss_count,
                            "message": (
                                "You've been taken offline because you missed "
                                f"{miss_count} ride offers in a row. "
                                "Tap 'Go Online' when you're ready."
                            ),
                        },
                        f"driver_{driver_user_id}",
                    )
                    await send_push_notification(
                        driver_user_id,
                        "You're now offline",
                        f"You missed {miss_count} ride offers in a row. "
                        "Tap 'Go Online' when you're ready to drive again.",
                        data={"type": "auto_offline", "reason": "missed_offers"},
                    )
                else:
                    await manager.send_personal_message(
                        {"type": "ride_offer_expired", "ride_id": ride_id},
                        f"driver_{driver_user_id}",
                    )
        except Exception as e:
            logger.warning(f"[DISPATCH] could not notify driver of offer expiry for ride {ride_id}: {e}")

        # Record this driver's timeout so they are skipped on the next
        # dispatch cycle for this ride (5-minute cooldown).
        try:
            from ..utils.redis_client import redis_set as _redis_set  # type: ignore
        except ImportError:
            from utils.redis_client import redis_set as _redis_set  # type: ignore
        try:
            await _redis_set(f"spinr:offer_skip:{ride_id}:{driver_id}", "1", ttl=300)
        except Exception as _e:
            logger.warning(f"[DISPATCH] could not set offer cooldown key for ride {ride_id} driver {driver_id}: {_e}")

        # Attempt re-dispatch to the next available driver.
        await match_driver_to_ride(ride_id)

    except Exception as e:
        logger.error(
            f"[DISPATCH] Offer timeout handler error for ride {ride_id}: {e}",
            exc_info=True,
        )


async def _batch_offer_timeout_handler(
    ride_id: str,
    rider_id: str | None,
    timeout_seconds: int = 15,
):
    """Expire all still-pending batch offers after timeout (no grace period)."""
    try:
        from ..repositories.driver_repo import update_acceptance_rate
        from ..utils.driver_presence import (
            clear_presence,
            increment_miss_streak,
            reset_miss_streak,
        )
        from ..utils.redis_client import redis_set as _redis_set
    except ImportError:
        from repositories.driver_repo import update_acceptance_rate  # type: ignore
        from utils.driver_presence import (  # type: ignore
            clear_presence,
            increment_miss_streak,
            reset_miss_streak,
        )
        from utils.redis_client import redis_set as _redis_set  # type: ignore

    await asyncio.sleep(timeout_seconds)
    try:
        ride = await db_supabase.get_ride(ride_id)
        if not ride or ride.get("status") != RideStatus.SEARCHING:
            return

        pending = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_offers")
                .select("driver_id")
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )
        pending_ids = [r["driver_id"] for r in (pending.data or [])]
        if not pending_ids:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_offers")
                .update({"status": "expired", "responded_at": now_iso})
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )

        try:
            settings = await get_app_settings()
            miss_threshold = int(settings.get("auto_offline_miss_threshold", 3))
        except Exception:
            miss_threshold = 3

        for did in pending_ids:
            miss_count = await increment_miss_streak(did)
            await update_acceptance_rate(did, accepted=False)

            auto_offline = miss_count >= miss_threshold
            if auto_offline:
                logger.info(
                    f"[DISPATCH] Auto-offline: driver {did} missed "
                    f"{miss_count} consecutive offers (threshold={miss_threshold})"
                )
                await db_supabase.set_driver_available(did, False)
                await db_supabase.run_sync(
                    lambda _did=did: (
                        db_supabase.supabase.table("drivers")
                        .update({"is_online": False, "is_available": False})
                        .eq("id", _did)
                        .execute()
                    )
                )
                await clear_presence(did)
                await reset_miss_streak(did)
                await record_period_transition(did, 0)
            else:
                await db_supabase.set_driver_available(did, True)
                await record_period_transition(did, 1)

            try:
                await _redis_set(f"spinr:offer_skip:{ride_id}:{did}", "1", ttl=300)
            except Exception as e:
                logger.warning(f"Failed to set offer skip key for driver {did}: {e}")
            try:
                drv = await db_supabase.get_driver_by_id(did)
                uid = (drv or {}).get("user_id")
                if uid:
                    msg_type = "auto_offline" if auto_offline else "ride_offer_expired"
                    await manager.send_personal_message(
                        {"type": msg_type, "ride_id": ride_id},
                        f"driver_{uid}",
                    )
            except Exception as e:
                logger.warning(f"Failed to send offer_expired WS to driver {did}: {e}")

        if rider_id:
            await manager.send_personal_message(
                {
                    "type": "driver_timeout",
                    "ride_id": ride_id,
                    "message": "Driver didn't respond. Finding another driver...",
                },
                f"rider_{rider_id}",
            )

        await match_driver_to_ride(ride_id)

    except Exception as e:
        logger.error(f"[DISPATCH] Batch timeout handler error for ride {ride_id}: {e}", exc_info=True)


class RideEstimateRequest(BaseModel):
    pickup_lat: float = Field(..., ge=-90, le=90)
    pickup_lng: float = Field(..., ge=-180, le=180)
    dropoff_lat: float = Field(..., ge=-90, le=90)
    dropoff_lng: float = Field(..., ge=-180, le=180)
    stops: Optional[List[dict]] = None
    # Corporate-billing context — when present, surge is suppressed so the
    # quote shown to the rider matches what the company will be invoiced.
    # Optional for backwards compatibility with consumer-only callers.
    payment_method: Optional[str] = None
    corporate_account_id: Optional[str] = None
    work_profile: Optional[bool] = False
    requires_wav: bool = False


async def _filter_reachable_drivers(all_drivers: list) -> list:
    """Drop ghost drivers from a DB-online pool for rider-facing counts.

    A driver is kept iff their Redis presence key is still alive (heartbeat
    within the TTL) AND their durable intent is online. Mirrors the guard in
    /drivers/nearby and dispatch so the estimate "X drivers" badge matches
    what dispatch can actually reach.

    Redis-outage policy (matches /drivers/nearby):
      * reachable + present set → filter to truly reachable drivers
      * reachable + empty set   → everyone is offline; an empty result is
        correct, not a bug
      * NOT reachable (Redis configured but down) → presence is unknowable,
        so fall back to DB state (still applying intent_online to catch a
        stale is_online column). Dispatch presence-filters before any offer,
        so a ghost that slips through here cannot actually accept a ride.

    Any unexpected error degrades to DB state rather than blanking the count.
    """
    try:
        from ..utils.driver_online import intent_online
        from ..utils.driver_presence import present_driver_ids_checked
    except ImportError:  # pragma: no cover - dual import path
        from utils.driver_online import intent_online  # type: ignore
        from utils.driver_presence import present_driver_ids_checked  # type: ignore

    driver_ids = [d["id"] for d in all_drivers if d.get("id")]
    if not driver_ids:
        return all_drivers

    try:
        present, reachable = await present_driver_ids_checked(driver_ids)
    except Exception as exc:
        logger.warning("[estimate] presence filter error, using DB state: %s", exc)
        return [d for d in all_drivers if intent_online(d)]

    if reachable:
        before = len(all_drivers)
        filtered = [d for d in all_drivers if d.get("id") in present and intent_online(d)]
        logger.info("[estimate] presence filter: %d/%d driver(s) reachable", len(filtered), before)
        return filtered

    # Redis configured but unreachable — keep DB state, log warning.
    filtered = [d for d in all_drivers if intent_online(d)]
    logger.warning(
        "[estimate] presence store unreachable, using DB state (%d drivers after intent_online filter)",
        len(filtered),
    )
    return filtered


async def compute_ride_estimates(
    body: RideEstimateRequest,
    rider_id: str,
    *,
    include_polyline: bool = True,
) -> dict:
    """Shared estimate engine behind POST /rides/estimate.

    Single fare path for every quoting surface (rider app, AI assistant
    get_fare_quote): geofence gates, live driver availability/ETA, surge,
    area fees + taxes, and per-vehicle surge-locked estimate tokens.
    Raises HTTPException(400 OUTSIDE_SERVICE_AREA) exactly like the route.
    ``include_polyline=False`` skips the Directions fetch for callers that
    don't render a map (saves a Maps API call and its latency).
    """
    validate_ride_location(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)
    # Price the actual route through any intermediate stops, not the straight
    # pickup→dropoff line — otherwise adding a stop never changes the quote.
    distance_km = multi_leg_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng, body.stops)
    duration_minutes = int(distance_km / 30 * 60) + 5

    fares = await get_fares_for_location(body.pickup_lat, body.pickup_lng)

    # Resolve service area once for fees/taxes — shared across all vehicle-type iterations
    # so calculate_all_fees doesn't re-fetch service_areas N times.
    _est_all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=500)

    # Use PostGIS RPC for geofence checks, with a JSON/GeoJSON polygon
    # fallback for service-area rows whose geography column is not synced.
    _est_matched_area = await _get_active_service_area_for_point(
        body.pickup_lat,
        body.pickup_lng,
        _est_all_areas,
    )
    if _est_matched_area:
        logger.info(
            "[estimate] matched service area '%s' for fees",
            _est_matched_area.get("name", _est_matched_area.get("id")),
        )
    else:
        logger.info(
            "[estimate] no service area matched pickup (%.5f, %.5f) — area fees will be empty",
            body.pickup_lat,
            body.pickup_lng,
        )

    # Geofence gates — pickup, dropoff, and every stop must be inside an
    # active service area before we show prices. The lookup combines PostGIS
    # with the admin-visible polygon JSON fallback above. Fail-open when no
    # active areas are configured (DB outage or fresh install).
    if _est_all_areas:
        if _est_matched_area is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "OUTSIDE_SERVICE_AREA",
                    "message": (
                        "Sorry, your pickup location is outside our coverage area. "
                        "Please choose a pickup within a serviced zone."
                    ),
                },
            )
        _dropoff_area = await _get_active_service_area_for_point(
            body.dropoff_lat,
            body.dropoff_lng,
            _est_all_areas,
        )
        if _dropoff_area is None:
            logger.info(
                "[estimate] reject dropoff=(%.5f,%.5f) — outside service areas",
                body.dropoff_lat,
                body.dropoff_lng,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "OUTSIDE_SERVICE_AREA",
                    "message": (
                        "Sorry, your dropoff location is outside our coverage area. "
                        "Please choose a dropoff within a serviced zone."
                    ),
                },
            )
        for idx, stop in enumerate(body.stops or []):
            s_lat, s_lng = stop.get("lat"), stop.get("lng")
            if s_lat is None or s_lng is None:
                continue
            _stop_area = await _get_active_service_area_for_point(s_lat, s_lng, _est_all_areas)
            if _stop_area is None:
                logger.info(
                    "[estimate] reject stop[%d]=(%.5f,%.5f) — outside service areas",
                    idx,
                    s_lat,
                    s_lng,
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "OUTSIDE_SERVICE_AREA",
                        "message": (
                            "Sorry, one of your stops is outside our coverage area. "
                            "Please choose stops within a serviced zone."
                        ),
                    },
                )

    # Kick off the Directions polyline fetch NOW so its round-trip overlaps
    # the driver/fare work below instead of stacking on top of it (the fare
    # estimate budget is <300ms P95; Directions alone can take seconds).
    # Placed after the geofence gates so rejected requests never spend a
    # Maps API call. Never raises — resolves to None on any failure.
    async def _polyline_fetch() -> Optional[list]:
        try:
            _ps = await get_app_settings()
            _maps_key = (_ps or {}).get("google_maps_api_key", "")
            if not _maps_key:
                return None
            return await _fetch_directions_polyline(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                _maps_key,
                waypoints=body.stops or [],
            )
        except Exception as _poly_err:
            logger.warning("[estimate] polyline fetch failed (non-fatal): %s", _poly_err)
            return None

    polyline_task = spawn(_polyline_fetch()) if include_polyline else None

    # Fetch nearby online+available drivers once, geo-bounded to a box around
    # the pickup (same dispatch_geo_bounds the dispatch path uses) so the
    # 200-row cap applies to in-area drivers only — the rider's "X drivers"
    # badge must be computed from the same pool dispatch would select from,
    # not an arbitrary province-wide page. Order by went_online_at DESC so
    # recently-toggled-online drivers fill the page first: ghost drivers
    # (is_available=True in DB but heartbeat expired) tend to carry stale
    # went_online_at values and fall toward the tail, so they are less likely
    # to crowd real drivers out of the cap before the presence filter below.
    # Scan cost is bounded by idx_drivers_online_available_recency
    # (migration 138, partial WHERE is_online AND is_available, ordered by
    # went_online_at DESC): the planner walks it in order and the lat/lng box
    # only filters within that online-fleet-sized walk, so the geo predicates
    # need no index of their own (see the dispatch-path note above).
    all_drivers = await db_supabase.get_rows(
        "drivers",
        {
            "is_online": True,
            "is_available": True,
            # 10 km matches the exact haversine gate in the loop below.
            "$and": dispatch_geo_bounds(body.pickup_lat, body.pickup_lng, 10.0),
        },
        # P1: same projection as the dispatch pool — the estimate badge only
        # needs id/user_id/lat/lng/vehicle_type_id/is_wav, never encrypted PII.
        columns="id,user_id,lat,lng,rating,is_wav,acceptance_rate,destination_mode,destination_lat,destination_lng,vehicle_type_id",
        order="went_online_at",
        desc=True,
        limit=200,
    )

    logger.info(
        "[estimate] fetched %d online+available drivers from DB",
        len(all_drivers),
    )

    # Presence filter: strip drivers whose heartbeat has expired (app crashed /
    # network lost) so the rider's "X drivers" badge matches what dispatch can
    # actually reach. See _filter_reachable_drivers for the Redis-outage policy.
    all_drivers = await _filter_reachable_drivers(all_drivers)

    # Filter to drivers within 10km radius and group by vehicle_type_id.
    # Exclude drivers without a user_id — those are orphan/demo rows that
    # cannot be dispatched to, and counting them would inflate the rider's
    # "X drivers available" badge and cause rides to fail at dispatch time.
    from collections import defaultdict

    drivers_by_type = defaultdict(list)
    skipped_reasons: dict = defaultdict(int)
    for d in all_drivers:
        if not d.get("user_id"):
            skipped_reasons["no_user_id"] += 1
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        if not d_lat or not d_lng:
            skipped_reasons["no_lat_lng"] += 1
            continue
        dist = calculate_distance(body.pickup_lat, body.pickup_lng, d_lat, d_lng)
        if dist > 10.0:
            skipped_reasons["outside_10km"] += 1
            continue
        vt_id = d.get("vehicle_type_id")
        if not vt_id:
            skipped_reasons["no_vehicle_type_id"] += 1
            continue
        drivers_by_type[vt_id].append(
            {
                "driver": d,
                "distance_km": dist,
            }
        )

    if skipped_reasons:
        logger.info("[estimate] skipped drivers: %s", dict(skipped_reasons))
    logger.info(
        "[estimate] matched drivers by vehicle_type: %s",
        {k: len(v) for k, v in drivers_by_type.items()},
    )

    # Check airport surcharge (pickup, dropoff, or any stop in airport sub-region)
    airport_result = await calculate_airport_fee(
        body.pickup_lat,
        body.pickup_lng,
        body.dropoff_lat,
        body.dropoff_lng,
        stops=body.stops,
    )
    airport_fee = airport_result.get("airport_fee", 0.0)

    # CLAUDE.md: surge does not apply to corporate-paid rides. Resolve the
    # bypass once per request — fares list is per-vehicle, not per-payment.
    corporate_bypass = _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    )

    logger.info(
        "[estimate] fares=%d vehicle_types=%s",
        len(fares),
        [f.get("vehicle_type", {}).get("name", "?") for f in fares],
    )

    # Fix 3: pre-resolve the cascade map once so each vehicle-type iteration can check
    # whether cascade upgrade types have drivers even when the exact type has none.
    # Child areas inherit the parent's map (same pattern as dispatch).
    _est_cascade_map: list = []
    if _est_matched_area:
        _est_cascade_map = _est_matched_area.get("vehicle_cascade_map") or []
        if not _est_cascade_map and _est_matched_area.get("parent_service_area_id"):
            try:
                _est_parent_area = await db_supabase.find_one(
                    "service_areas", {"id": _est_matched_area["parent_service_area_id"]}
                )
                _est_cascade_map = (_est_parent_area or {}).get("vehicle_cascade_map") or []
            except Exception as _est_casc_exc:
                logger.debug("[estimate] parent cascade map fetch skipped: %s", _est_casc_exc)

    estimates = []
    for fare_info in fares:
        surge = Decimal("1.0") if corporate_bypass else _d(fare_info.get("surge_multiplier", 1.0))
        fb = calculate_fare(
            fare_info,
            distance_km,
            duration_minutes,
            surge=surge,
            airport_fee=airport_fee,
        )

        # Calculate area fees + taxes so the rider sees them before booking.
        # Pass the pre-resolved area to avoid a redundant DB fetch per vehicle type.
        fees_result = {}
        try:
            fees_result = await calculate_all_fees(
                body.pickup_lat,
                body.pickup_lng,
                body.dropoff_lat,
                body.dropoff_lng,
                distance_km,
                _f(fb.total_fare),
                _all_areas=_est_all_areas,
                _matched_area=_est_matched_area,
            )
        except Exception as e:
            logger.error("[estimate] calculate_all_fees failed: %s", e, exc_info=True)

        area_fees_total = fees_result.get("fees_total", 0)
        tax_amount = fees_result.get("tax_amount", 0)
        grand_total = _f(_round(fb.total_fare + _d(area_fees_total) + _d(tax_amount)))

        # Check real driver availability for this vehicle type
        vt_id = fare_info["vehicle_type"].get("id")
        nearby_for_type = drivers_by_type.get(vt_id, [])
        driver_count = len(nearby_for_type)
        is_available = driver_count > 0
        wav_available = sum(1 for entry in nearby_for_type if entry["driver"].get("is_wav"))

        # Calculate ETA: closest driver's distance / avg speed (30km/h in city)
        eta_minutes = None
        closest_driver_km = None
        if nearby_for_type:
            closest = min(nearby_for_type, key=lambda x: x["distance_km"])
            closest_driver_km = round(closest["distance_km"], 1)
            eta_minutes = max(2, int(closest["distance_km"] / 30 * 60) + 1)

        # Fix 3: when no exact-type drivers are nearby, check cascade upgrade types.
        # If cascade drivers exist the vehicle type is still bookable — dispatch will
        # find them — so we must not show it as unavailable.
        if not is_available and _est_cascade_map:
            _est_casc_to = next(
                (rule.get("to") or [] for rule in _est_cascade_map if rule.get("from") == vt_id),
                [],
            )
            for _est_casc_vt_id in _est_casc_to:
                _est_casc_drivers = drivers_by_type.get(_est_casc_vt_id, [])
                if _est_casc_drivers:
                    is_available = True
                    driver_count = len(_est_casc_drivers)
                    _est_casc_closest = min(_est_casc_drivers, key=lambda x: x["distance_km"])
                    closest_driver_km = round(_est_casc_closest["distance_km"], 1)
                    eta_minutes = max(2, int(_est_casc_closest["distance_km"] / 30 * 60) + 1)
                    break

        # P0-4 surge-lock: sign a token per vehicle_type so POST /rides can
        # reuse the surge_multiplier shown here instead of re-reading the
        # service area (which may have changed between estimate + confirm).
        estimate_token = sign_estimate_token(
            rider_id=rider_id,
            vehicle_type_id=vt_id,
            pickup_lat=body.pickup_lat,
            pickup_lng=body.pickup_lng,
            dropoff_lat=body.dropoff_lat,
            dropoff_lng=body.dropoff_lng,
            surge_multiplier=round(float(surge), 2),
            total_fare=_f(fb.total_fare),
        )

        fare_breakdown_lines = build_fare_breakdown_lines(
            fb,
            distance_km=distance_km,
            duration_minutes=duration_minutes,
            area_fees=fees_result.get("fees", []),
            tax_breakdown=fees_result.get("tax_breakdown", {}),
        )

        estimates.append(
            {
                "vehicle_type": fare_info["vehicle_type"],
                "distance_km": round(distance_km, 2),
                "duration_minutes": duration_minutes,
                "base_fare": _money_str(fb.base_fare),
                "distance_fare": _money_str(fb.distance_fare),
                "time_fare": _money_str(fb.time_fare),
                "booking_fee": _money_str(fb.booking_fee),
                "surge_multiplier": round(float(surge), 2),
                "total_fare": _money_str(fb.total_fare),
                "area_fees": fees_result.get("fees", []),
                "area_fees_total": area_fees_total,
                "tax_breakdown": fees_result.get("tax_breakdown", {}),
                "tax_amount": tax_amount,
                "grand_total": grand_total,
                "fare_breakdown": fare_breakdown_lines,
                "available": is_available,
                "eta_minutes": eta_minutes,
                "closest_driver_km": closest_driver_km,
                "driver_count": driver_count,
                "wav_available": wav_available,
                "estimate_token": estimate_token,
            }
        )

    # Collect the road-following polyline started before the driver/fare
    # work. Same for all vehicle types, so fetched once; the rider app uses
    # it to render the gradient route line without a client-side Directions
    # call. Only a short top-up wait is granted here — a slow Directions API
    # must not drag the estimate past its latency budget. On timeout the
    # task is cancelled and the app falls back to straight-line rendering.
    route_polyline = None
    if polyline_task is not None:
        try:
            route_polyline = await asyncio.wait_for(polyline_task, timeout=0.5)
        except asyncio.TimeoutError:
            logger.info("[estimate] polyline not ready within budget — returning without it (non-fatal)")
        except Exception as _poly_err:  # defensive — _polyline_fetch traps its own errors
            logger.warning("[estimate] polyline await failed (non-fatal): %s", _poly_err)

    logger.info(
        "[estimate] returning %d estimates (polyline=%d pts): %s",
        len(estimates),
        len(route_polyline) if route_polyline else 0,
        [(e["vehicle_type"].get("name", "?"), e["available"], e["driver_count"]) for e in estimates],
    )
    return {"estimates": estimates, "route_polyline": route_polyline}


@api_router.post("/estimate")
@api_rate_limit
@_metric_timed("spinr_fare_calc_duration_ms")
async def estimate_ride(
    body: RideEstimateRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    return await compute_ride_estimates(body, current_user["id"])


async def ride_search_timeout(r_id: str, timeout_seconds: int = 300):
    """Auto-cancel a ride if it's still ``searching`` after ``timeout_seconds``.

    Matches Uber/Lyft's 5-minute default. Publishes a ``ride_cancelled`` WS
    message to the rider's channel and fires a push notification so the rider
    is alerted even if the app is backgrounded.

    Extracted from ``create_ride`` so it can be unit-tested directly — see
    backend/tests/test_p0_ship_blockers.py::TestNoDriversAvailableTimeout.
    """
    await asyncio.sleep(timeout_seconds)
    try:
        current_ride = await db_supabase.get_ride(r_id)
        if current_ride and current_ride.get("status") == RideStatus.SEARCHING:
            now = datetime.now(timezone.utc)
            base_update = {
                "status": RideStatus.CANCELLED,
                "cancelled_at": now,
                "cancellation_reason": "No nearby drivers found. Please try again.",
                "updated_at": now,
            }
            # Migration 38 adds cancelled_by / cancellation_type so the
            # admin panel can filter "No Driver Found" separately. Fall
            # back to base_update on PGRST204 ("column does not exist")
            # so the rider-facing cancel still succeeds before the
            # migration lands in prod.
            try:
                await db_supabase.update_ride(
                    r_id,
                    {
                        **base_update,
                        "cancelled_by": "system",
                        "cancellation_type": "no_drivers_found",
                    },
                )
            except Exception as _col_exc:
                logger.warning(f"[AUTO-CANCEL] attribution write failed ({_col_exc}); retrying minimal")
                await db_supabase.update_ride(r_id, base_update)
            await manager.send_personal_message(
                {
                    "type": "ride_cancelled",
                    "ride_id": r_id,
                    "reason": "No nearby drivers available. Your ride has been automatically cancelled.",
                },
                f"rider_{current_ride['rider_id']}",
            )
            await manager.broadcast_ride_status(
                r_id,
                RideStatus.CANCELLED,
                rider_id=current_ride["rider_id"],
                reason="no_drivers_found",
                is_auto=True,
            )
            try:
                await manager.broadcast_to_admins(
                    {
                        "type": "ride_cancelled",
                        "ride_id": r_id,
                        "reason": "no_drivers_found",
                        "is_auto": True,
                    }
                )
            except Exception as _exc:  # pragma: no cover - best effort
                logger.warning(f"ride timeout admin broadcast failed: {_exc}")
            await send_push_notification(
                current_ride["rider_id"],
                "Ride Cancelled ❌",
                "No nearby drivers were found. Your ride has been automatically cancelled. Please try again.",
                {"type": "ride_cancelled", "ride_id": r_id, "is_auto": "true"},
            )
            if current_ride.get("guest_booking"):
                # Corporate guest customer (no app): tell them by SMS —
                # otherwise they'd stand at the pickup point waiting for a
                # car that is never coming.
                try:
                    from ..services.guest_notification_service import notify_guest_cancelled
                except ImportError:
                    from services.guest_notification_service import notify_guest_cancelled  # type: ignore
                spawn(notify_guest_cancelled(dict(current_ride)))
            logger.info(f"Ride {r_id} auto-cancelled after {timeout_seconds}s - no driver found")
    except Exception as e:
        logger.error(f"Ride timeout handler error for {r_id}: {e}", exc_info=True)


# Decline codes where re-authorizing at a LOWER amount (fare without the buffer)
# can still succeed — the card is fundable, the buffer just tipped a thin balance
# over. Any other decline (lost/stolen/generic) is the card itself being bad, so
# a smaller hold won't help and we block instead of retrying.
_RETRYABLE_AT_LOWER_AMOUNT = frozenset({"insufficient_funds"})


@dataclass
class _PreauthOutcome:
    """Result of a booking-time card pre-authorization attempt.

    ``fields`` are merged into ``ride_data`` (empty when no hold was placed).
    ``requires_action`` signals the rider-app must complete an on-device SCA /
    Apple Pay confirm for ``client_secret`` and then re-book passing
    ``payment_intent_id`` back as ``preauthorized_payment_intent_id``.
    """

    fields: dict = dataclass_field(default_factory=dict)
    requires_action: bool = False
    client_secret: Optional[str] = None
    payment_intent_id: Optional[str] = None


def _card_declined_result(decline_code: Optional[str], block_on_decline: bool) -> _PreauthOutcome:
    """Terminal decline of a pre-auth hold. At interactive booking
    (``block_on_decline=True``) raise 402 so the rider fixes their card before a
    driver is disturbed. At scheduled dispatch (``block_on_decline=False``) the
    rider is not present, so degrade to no hold and let post-trip settlement +
    the retry/unpaid-block safety net handle it — never strand a scheduled ride.
    """
    if not block_on_decline:
        return _PreauthOutcome()
    raise HTTPException(
        status_code=402,
        detail={
            "code": "CARD_DECLINED",
            "message": "Your card was declined. Please update your payment method and try booking again.",
            "decline_code": decline_code,
        },
    )


async def _attach_preauthorized_hold(
    *,
    ride_id: str,
    rider_id: str,
    payment_intent_id: str,
    stripe_customer_id: Optional[str],
    min_amount: Decimal,
) -> dict:
    """Verify a PaymentIntent the rider-app already confirmed on-device (SCA /
    Apple Pay two-step) and return the ride fields to persist. A failed/abandoned
    authentication raises 402 so the rider re-tries — we never attach an
    unconfirmed hold, and we re-read the PI from Stripe rather than trusting the
    client. ``authorized_amount`` is taken from Stripe (the true held amount).

    SECURITY: the PI must (a) belong to this rider's Stripe customer, (b) hold at
    least the ride fare, and (c) not already be attached to another ride — so a
    client cannot attach someone else's hold, replay a cheaper one, or reuse a PI
    across rides."""
    # Fail CLOSED: ownership is verified against the rider's Stripe customer, so
    # we cannot attach a client-supplied PI when we have no customer to check it
    # against (e.g. a crafted work_profile card request). Reject rather than skip.
    if not stripe_customer_id:
        logger.error("[preauth][security] no Stripe customer to verify PI ownership for ride=%s", ride_id)
        raise HTTPException(
            status_code=402,
            detail={"code": "CARD_DECLINED", "message": "No payment method on file. Please add a card first."},
        )

    # (c) Reuse guard: a PI already on another ride must not be re-attached.
    try:
        existing = await db_supabase.get_rows(
            "rides",
            {"payment_intent_id": payment_intent_id},
            limit=1,
            columns="id",
        )
    except Exception as e:
        # Fail OPEN here is acceptable: ownership + amount are still enforced
        # against Stripe below, so a degraded reuse-lookup can't bypass security.
        logger.error("[preauth] PI-reuse lookup failed for ride=%s pi=%s: %s", ride_id, payment_intent_id, e)
        existing = []
    if existing and existing[0].get("id") != ride_id:
        logger.error("[preauth][security] PI already attached to ride=%s (declining)", existing[0].get("id"))
        raise HTTPException(
            status_code=402,
            detail={
                "code": "CARD_DECLINED",
                "message": "This authorization can't be reused. Please try booking again.",
            },
        )

    min_cents = int((_round(_d(min_amount)) * Decimal("100")).to_integral_value(rounding=ROUND_HALF_UP))
    outcome = await verify_authorization(
        ride_id=ride_id,
        payment_intent_id=payment_intent_id,
        expected_customer_id=stripe_customer_id,
        min_amount_cents=min_cents,
    )
    if outcome.status == "authorized":
        return {
            "payment_intent_id": outcome.payment_intent_id,
            "authorized_amount": _f(outcome.charged_amount),
            "auth_status": "authorized",
        }
    if outcome.status == "declined":
        logger.info("[preauth] on-device auth not completed for ride=%s pi=%s", ride_id, payment_intent_id)
        raise HTTPException(
            status_code=402,
            detail={
                "code": "CARD_DECLINED",
                "message": "Card authentication wasn't completed. Please try booking again.",
            },
        )
    # unconfigured (dev) / failed (ops) — degrade to no hold; post-trip settles.
    logger.error(
        "[preauth] verify hold failed for ride=%s pi=%s: %s", ride_id, payment_intent_id, outcome.error_message
    )
    return {}


async def _preauthorize_ride_card(
    *,
    ride_id: str,
    rider_id: str,
    grand_total: Decimal,
    stripe_customer_id: Optional[str],
    payment_method_id: Optional[str],
    block_on_decline: bool = True,
) -> _PreauthOutcome:
    """Place a buffered card hold at booking; return a ``_PreauthOutcome``.

    Holds ``grand_total + RIDE_AUTH_BUFFER_CAD`` via a manual-capture
    PaymentIntent so a post-trip tip can later be captured on the SAME intent
    (one Stripe fee) and a dead card is surfaced BEFORE a driver is dispatched.
    The held PaymentIntent id reuses the existing ``payment_intent_id`` column.

    Outcomes:
      - hold placed → ``fields`` populated (authorized / fare_only).
      - ``requires_action`` → ``requires_action=True`` with client_secret +
        payment_intent_id so the caller drives the on-device SCA / Apple Pay
        two-step (interactive booking only).
      - genuine decline → raises 402 when ``block_on_decline`` (interactive
        booking); returns empty when not (scheduled dispatch — never strand it).
      - ``failed`` / ``unconfigured`` / no card on file → empty fields, ride
        proceeds on the post-trip settlement path.
    """
    if not stripe_customer_id or not payment_method_id:
        # No saved card to hold against — leave settlement to the post-trip path.
        return _PreauthOutcome()

    buffer = _round(_d(_settings.RIDE_AUTH_BUFFER_CAD))
    hold_amount = _round(_d(grand_total) + buffer)
    _ride_stub = {"id": ride_id, "payment_method": "card"}

    outcome = await authorize_ride(
        ride=_ride_stub,
        rider_id=rider_id,
        amount=hold_amount,
        payment_method_id=payment_method_id,
        stripe_customer_id=stripe_customer_id,
    )

    if outcome.status == "authorized":
        return _PreauthOutcome(
            fields={
                "payment_intent_id": outcome.payment_intent_id,
                "authorized_amount": _f(hold_amount),
                "auth_status": "authorized",
            }
        )

    if outcome.status == "requires_action":
        # 3DS / Apple Pay biometric needed — hand the client_secret to the app to
        # confirm on-device, then re-book passing the PI back. Scheduled dispatch
        # (block_on_decline=False) can't drive an on-device sheet → degrade.
        if not block_on_decline:
            logger.info("[preauth] SCA needed at scheduled dispatch for ride=%s — degrading to post-trip", ride_id)
            return _PreauthOutcome()
        logger.info("[preauth] card requires SCA at booking for ride=%s — returning client_secret", ride_id)
        return _PreauthOutcome(
            requires_action=True,
            client_secret=outcome.client_secret,
            payment_intent_id=outcome.payment_intent_id,
        )

    if outcome.status == "declined":
        if outcome.decline_code in _RETRYABLE_AT_LOWER_AMOUNT:
            # Buffer tipped a thin balance over — retry holding the fare only so
            # a rider who can afford the ride still rides (loses single-fee tips).
            fare_outcome = await authorize_ride(
                ride=_ride_stub,
                rider_id=rider_id,
                amount=_round(_d(grand_total)),
                payment_method_id=payment_method_id,
                stripe_customer_id=stripe_customer_id,
            )
            if fare_outcome.status == "authorized":
                logger.info(
                    "[preauth] buffered hold declined (insufficient_funds); fare-only hold placed for ride=%s",
                    ride_id,
                )
                return _PreauthOutcome(
                    fields={
                        "payment_intent_id": fare_outcome.payment_intent_id,
                        "authorized_amount": _f(_round(_d(grand_total))),
                        "auth_status": "fare_only",
                    }
                )
            if fare_outcome.status == "requires_action":
                if not block_on_decline:
                    return _PreauthOutcome()
                return _PreauthOutcome(
                    requires_action=True,
                    client_secret=fare_outcome.client_secret,
                    payment_intent_id=fare_outcome.payment_intent_id,
                )
            if fare_outcome.status == "declined":
                logger.info("[preauth] fare-only hold also declined for ride=%s", ride_id)
                return _card_declined_result(fare_outcome.decline_code, block_on_decline)
            # fare-only hit ops / unconfigured — degrade to no hold.
            return _PreauthOutcome()

        # Hard decline (lost/stolen/generic) — a smaller hold won't help.
        logger.info("[preauth] hard card decline (code=%s) for ride=%s", outcome.decline_code, ride_id)
        return _card_declined_result(outcome.decline_code, block_on_decline)

    # failed (Stripe ops) / unconfigured — proceed without a hold; the post-trip
    # settlement path (and its retry loop) remains the safety net.
    if outcome.status == "failed":
        logger.error("[preauth] authorization ops error for ride=%s: %s", ride_id, outcome.error_message)
    return _PreauthOutcome()


@api_router.post("")
@ride_request_limit
@idempotent_endpoint(scope="ride_create")
async def create_ride(
    body: CreateRideRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    # SlowAPI's @ride_request_limit needs a parameter literally named
    # ``request`` that is a starlette Request; otherwise it raises
    # "parameter `request` must be an instance of starlette.requests.Request"
    # for every call. The Pydantic body is ``body`` — do not rename it
    # back to ``request`` without also reworking the rate-limit decorator.
    validate_ride_location(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng)

    # R-P1-7: Idempotency — if the client retries after a network drop, return
    # the previously-created ride instead of charging the rider twice.
    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing = await db_supabase.find_one(
            "rides",
            {"idempotency_key": idempotency_key, "rider_id": current_user["id"]},
        )
        if existing:
            return existing

    # Ban check + payment method validation share a single users-row read.
    # Previously this was two round-trips: ``find_one(users)`` (for card path)
    # + ``get_user_status`` (always). ``status`` lives on the same row.
    rider_row = await db.find_one("users", {"id": current_user["id"]})
    user_status = (rider_row or {}).get("status", "active")
    if user_status == "banned":
        raise HTTPException(
            status_code=403,
            detail=(rider_row or {}).get("status_reason")
            or "Your account has been deactivated due to policy violations. Please contact support.",
        )
    if user_status == "suspended":
        # A temporary suspension (suspended_until set) auto-lifts once that time
        # passes — the rider can book again without an admin reactivating.
        suspended_until = (rider_row or {}).get("suspended_until")
        still_suspended = True
        if suspended_until:
            try:
                still_suspended = datetime.fromisoformat(str(suspended_until).replace("Z", "+00:00")) > datetime.now(
                    timezone.utc
                )
            except ValueError:
                still_suspended = True
        if still_suspended:
            raise HTTPException(
                status_code=403,
                detail=(rider_row or {}).get("status_reason")
                or "Your account is currently suspended. Please contact support.",
            )
    # Only a ride that will ACTUALLY settle against a corporate account is exempt
    # from the personal-card checks — i.e. company_allowance, or work_profile +
    # corporate_account_id (which is reclassified to company_allowance below and
    # routed to settle_corporate). A bare corporate_account_id with
    # payment_method="card" and no work_profile is NOT corporate-billed: it
    # settles through settle_card() against the rider's card, so it must still
    # pin one. Keying the exemption on corporate_account_id alone (an earlier
    # attempt) re-opened the stale-default-card charge for exactly that shape —
    # use the canonical predicate instead.
    _corporate_billed = _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    )
    if body.payment_method == "card" and not _corporate_billed:
        # Demo/local mode (Stripe intentionally unconfigured) has no real Stripe
        # customers or cards; card rides settle through charge_ride's
        # "unconfigured" path (marked paid, no charge). Only enforce the
        # card-on-file requirements when Stripe is actually configured —
        # otherwise demo/staging could never book a card ride.
        _stripe_configured = bool((await get_app_settings()).get("stripe_secret_key"))
        if _stripe_configured:
            if not rider_row or not rider_row.get("stripe_customer_id"):
                raise HTTPException(
                    status_code=400,
                    detail="No payment method on file. Please add a card first.",
                )
            # Defense-in-depth (money): a card ride must name the exact card to
            # charge. Without an explicit payment_method_id, settle_card() falls
            # back to the Stripe customer's default_payment_method — which is how
            # a stale/forgotten card (e.g. an old 4242 test card) ends up charged
            # for a ride the rider never knowingly put on it. The rider app
            # already guards this in the UI; this server check closes the same
            # gap for any direct API caller or client-state race. The SCA
            # two-step second leg is exempt: its hold
            # (preauthorized_payment_intent_id) already pins a real card, and
            # settlement captures that hold rather than the customer default.
            if not body.payment_method_id and not body.preauthorized_payment_intent_id:
                raise HTTPException(
                    status_code=400,
                    detail="Select a payment card before booking.",
                )

    active_statuses = list(RideStatus.active_statuses())
    existing_ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {"rider_id": current_user["id"], "status": {"$in": active_statuses}},
            limit=1,
        )
    )
    if existing_ride:
        raise SpinrException(
            message="You already have an active ride",
            error_code=ErrorCode.RIDE_INVALID_STATUS,
            status_code=409,
            message_key=ErrorKeys.RIDE_INVALID_STATUS,
        )

    unpaid_rides = await db_supabase.get_rows(
        "rides",
        {
            "rider_id": current_user["id"],
            "status": RideStatus.COMPLETED,
            "payment_status": "failed",
        },
        limit=1,
    )
    if unpaid_rides:
        raise SpinrException(
            message="You have an unpaid ride. Please update your payment method to continue booking.",
            error_code=ErrorCode.PAYMENT_UNPAID_RIDE_BLOCK,
            status_code=402,
            details={"unpaid_ride_id": unpaid_rides[0]["id"]},
            message_key=ErrorKeys.PAYMENT_UNPAID_RIDE_BLOCK,
        )

    # Charge the multi-leg route (pickup → stops → dropoff) so the booked fare
    # matches the multi-stop quote shown at /rides/estimate.
    distance_km = multi_leg_distance(body.pickup_lat, body.pickup_lng, body.dropoff_lat, body.dropoff_lng, body.stops)
    duration_minutes = int(distance_km / 30 * 60) + 5

    # Fetch service_areas ONCE for this request and share across:
    # (1) fare resolution, (2) airport-fee lookup, (3) area-fees/taxes,
    # (4) service_area_id resolution. Previously each of these hit the
    # table independently — 3-4 full scans per POST /rides.
    all_areas = []
    try:
        all_areas = await db_supabase.get_rows("service_areas", {"is_active": True}, limit=500)
    except Exception as e:
        logger.error(f"Failed to fetch service areas: {e}", exc_info=True)

    # Resolve the pickup service area once and pass the match downstream.
    matched_area = await _get_active_service_area_for_point(
        body.pickup_lat,
        body.pickup_lng,
        all_areas,
    )
    service_area_id = matched_area["id"] if matched_area else None

    # Geofence gate: pickup must fall inside an active service area. Without
    # this the request silently dispatches against drivers anywhere on the
    # map, which is what produced the "I'm outside the zone but the app is
    # still searching for drivers" report. Dropoff and intermediate stops are
    # checked below with the same service-area matcher.
    if matched_area is None and all_areas:
        logger.info(
            "[geofence] reject pickup=(%.5f,%.5f) — outside %d active service area(s)",
            body.pickup_lat,
            body.pickup_lng,
            len(all_areas),
        )
        raise HTTPException(
            status_code=400,
            detail={
                "code": "OUTSIDE_SERVICE_AREA",
                "message": (
                    "Sorry, your pickup location is outside our coverage area. "
                    "Please choose a pickup within a serviced zone."
                ),
            },
        )

    # Geofence gate: dropoff must also fall inside an active service area.
    # Uses PostGIS plus the admin-visible polygon JSON fallback.
    if all_areas:
        _dropoff_area = await _get_active_service_area_for_point(
            body.dropoff_lat,
            body.dropoff_lng,
            all_areas,
        )
        if _dropoff_area is None:
            logger.info(
                "[geofence] reject dropoff=(%.5f,%.5f) — outside service areas",
                body.dropoff_lat,
                body.dropoff_lng,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "OUTSIDE_SERVICE_AREA",
                    "message": (
                        "Sorry, your dropoff location is outside our coverage area. "
                        "Please choose a dropoff within a serviced zone."
                    ),
                },
            )

    # Geofence gate: every intermediate stop must also be in coverage.
    if all_areas:
        for idx, stop in enumerate(body.stops or []):
            s_lat, s_lng = stop.get("lat"), stop.get("lng")
            if s_lat is None or s_lng is None:
                continue
            _stop_area = await _get_active_service_area_for_point(s_lat, s_lng, all_areas)
            if _stop_area is None:
                logger.info(
                    "[geofence] reject stop[%d]=(%.5f,%.5f) — outside service areas",
                    idx,
                    s_lat,
                    s_lng,
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "OUTSIDE_SERVICE_AREA",
                        "message": (
                            "Sorry, one of your stops is outside our coverage area. "
                            "Please choose stops within a serviced zone."
                        ),
                    },
                )

    # Vehicle types are also needed by fare building — fetch once, reuse.
    vehicle_types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)

    fares = await _fares_for_location_impl(
        body.pickup_lat,
        body.pickup_lng,
        all_areas=all_areas,
        vehicle_types=vehicle_types,
    )

    fare_info = next(
        (f for f in fares if f["vehicle_type"]["id"] == body.vehicle_type_id),
        None,
    )

    if not fare_info:
        logger.info(
            "[create_ride] reject vehicle_type_id=%s — not configured for pickup service area",
            body.vehicle_type_id,
        )
        raise HTTPException(status_code=400, detail="Invalid vehicle type for this service area")

    # Use Decimal for all monetary arithmetic (CQ-009 — eliminates float rounding errors)
    # P0-4 surge-lock: if the client sent back the estimate_token we issued
    # from /rides/estimate, reuse that surge instead of re-reading the area.
    # Invalid / expired tokens fall back to current surge — we do not 400
    # the request, because that would strand a rider mid-confirm if the
    # token just expired. The worst case of a bad token is parity with the
    # pre-token behavior (read current surge).
    current_surge = _d(fare_info.get("surge_multiplier", 1.0))
    surge = current_surge
    if body.estimate_token:
        try:
            payload = verify_estimate_token(
                body.estimate_token,
                rider_id=current_user["id"],
                vehicle_type_id=body.vehicle_type_id,
                pickup_lat=body.pickup_lat,
                pickup_lng=body.pickup_lng,
                dropoff_lat=body.dropoff_lat,
                dropoff_lng=body.dropoff_lng,
            )
            surge = _d(payload["sm"])
            logger.info(
                f"Surge locked from estimate_token for rider={current_user['id']} "
                f"vt={body.vehicle_type_id}: {float(surge)} (current was {float(current_surge)})"
            )
        except EstimateTokenError as e:
            logger.warning(
                f"estimate_token rejected ({e}); falling back to current surge "
                f"{float(current_surge)} for rider={current_user['id']}"
            )

    # Corporate surge bypass — applied AFTER estimate_token resolution so the
    # corporate flag wins over any token-locked multiplier. Without this
    # ordering a rider could pin a surged token in personal mode, switch the
    # toggle to Work, and still bill the company at the surged rate.
    if _is_corporate_paid(
        payment_method=body.payment_method,
        work_profile=body.work_profile,
        corporate_account_id=body.corporate_account_id,
    ) and surge != Decimal("1.0"):
        logger.info(
            f"Corporate surge bypass for rider={current_user['id']} "
            f"corp={body.corporate_account_id}: forcing surge {float(surge)} → 1.0"
        )
        surge = Decimal("1.0")

    # Scheduled ride surge exclusion (policy): a rider who books a scheduled ride
    # locks in the fare at booking time, before the surge window opens. Applying
    # the current area surge at dispatch time would retroactively charge a higher
    # multiplier than the rider was shown — a hidden-fee violation. Reset to 1.0
    # unconditionally when is_scheduled is True or a scheduled_time is present.
    if (body.is_scheduled or body.scheduled_time) and surge > Decimal("1.0"):
        logger.info(f"Scheduled ride: surge reset from {float(surge)} to 1.0 for rider={current_user['id']}")
        surge = Decimal("1.0")

    # Airport surcharge (pickup, dropoff, or any stop in airport sub-region).
    # Reuses the same all_areas list — filters for is_airport locally.
    airport_result = await calculate_airport_fee(
        body.pickup_lat,
        body.pickup_lng,
        body.dropoff_lat,
        body.dropoff_lng,
        stops=body.stops,
        _all_areas=all_areas,
    )
    airport_fee = _d(airport_result.get("airport_fee", 0.0))
    airport_zone_name = airport_result.get("airport_zone_name")

    fb = calculate_fare(fare_info, distance_km, duration_minutes, surge=surge, airport_fee=airport_fee)
    base_fare = fb.base_fare
    distance_fare = fb.distance_fare
    time_fare = fb.time_fare
    booking_fee = fb.booking_fee
    total_fare = fb.total_fare
    driver_earnings = fb.driver_earnings
    admin_earnings = fb.admin_earnings

    # Calculate area fees + taxes (reuses all_areas + pre-resolved match)
    fees_result = {}
    try:
        fees_result = await calculate_all_fees(
            body.pickup_lat,
            body.pickup_lng,
            body.dropoff_lat,
            body.dropoff_lng,
            distance_km,
            _f(total_fare),
            _all_areas=all_areas,
            _matched_area=matched_area,
        )
    except Exception as e:
        logger.error(f"Failed to calculate area fees: {e}", exc_info=True)

    area_fees_total = fees_result.get("fees_total", 0)
    tax_amount = fees_result.get("tax_amount", 0)
    grand_total = _f(_round(total_fare + _d(area_fees_total) + _d(tax_amount)))

    # Payment pre-validation: reject the ride before dispatching if the rider
    # clearly cannot pay. Wallet rides require sufficient balance upfront;
    # corporate rides have their own policy check below.
    if body.payment_method == "wallet":
        wallet = await db_supabase.find_one("wallets", {"user_id": current_user["id"]})
        wallet_balance = _d((wallet or {}).get("balance", 0))
        if wallet_balance < _d(grand_total):
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient wallet balance. Estimated fare is ${grand_total}, wallet has ${_f(wallet_balance)}. Please top up your wallet or switch to card payment.",
            )

    # CR-1: a ride booked for a future time must NOT dispatch a live driver now.
    # It is parked in SCHEDULED and the scheduled-ride dispatcher loop flips it
    # to SEARCHING at its scheduled_time. A request is deferred whenever a
    # future scheduled_time is present — the CreateRideRequest validator
    # guarantees that time is ≥5 min out. We key off scheduled_time (not the
    # is_scheduled flag) because a client can send scheduled_time while leaving
    # is_scheduled at its False default; the surge/fare logic above already
    # treats scheduled_time presence as scheduled, and dispatching such a ride
    # immediately would route a live driver hours early.
    _is_deferred_schedule = body.scheduled_time is not None
    # Corporate time-window policy is enforced against the *pickup* time, so a
    # deferred ride must be evaluated against its scheduled_time, not the moment
    # of booking — otherwise a rider booking inside company hours for an
    # out-of-hours pickup (or vice-versa) is wrongly allowed/blocked.
    _policy_pickup_time = body.scheduled_time if _is_deferred_schedule else datetime.now(timezone.utc)

    # Pre-dispatch corporate policy check (spec §4 — booking phase).
    # Only runs when rider explicitly books with company_allowance payment method.
    _corp_member_id: Optional[str] = None
    if body.corporate_account_id and body.payment_method == "company_allowance":
        _policy_result = await evaluate_policy_for_ride(
            corporate_account_id=body.corporate_account_id,
            rider_id=current_user["id"],
            estimated_fare=total_fare,
            ride_type="standard",
            pickup_time=_policy_pickup_time,
        )
        if not _policy_result.passed:
            reasons = []
            for rule in _policy_result.failed_rules:
                if rule == "max_fare_per_ride":
                    reasons.append("Fare exceeds company limit per ride.")
                elif rule == "time_window":
                    reasons.append("Booking is outside allowed company hours.")
                elif rule == "allowed_payment_source":
                    reasons.append("Insufficient corporate allowance balance.")
                else:
                    reasons.append(f"Violated corporate policy: {rule}.")
            message = "Blocked by company policy:\n" + "\n".join(reasons) if reasons else "Violated corporate policy."
            raise HTTPException(
                status_code=403,
                detail={
                    "message": message,
                    "failed_rules": _policy_result.failed_rules,
                },
            )
        _corp_members = await db_supabase.get_rows(
            "corporate_members",
            {
                "company_id": body.corporate_account_id,
                "user_id": current_user["id"],
                "status": "active",
            },
            limit=1,
        )
        if _corp_members:
            _corp_member_id = _corp_members[0]["id"]

    # _is_deferred_schedule is computed above (before the corporate policy
    # check). Deferred rides are parked in SCHEDULED; the scheduled-ride
    # dispatcher loop (utils/scheduled_rides.py) flips them to SEARCHING and
    # runs driver matching when scheduled_time arrives. A ride with
    # is_scheduled but no time falls through to immediate dispatch.
    _initial_status = RideStatus.SCHEDULED if _is_deferred_schedule else RideStatus.SEARCHING

    pickup_otp_plain = generate_pickup_otp()
    ride = Ride(
        rider_id=current_user["id"],
        vehicle_type_id=body.vehicle_type_id,
        pickup_address=body.pickup_address,
        pickup_lat=body.pickup_lat,
        pickup_lng=body.pickup_lng,
        dropoff_address=body.dropoff_address,
        dropoff_lat=body.dropoff_lat,
        dropoff_lng=body.dropoff_lng,
        distance_km=round(distance_km, 2),
        duration_minutes=duration_minutes,
        base_fare=_f(base_fare),
        distance_fare=_f(distance_fare),
        time_fare=_f(time_fare),
        booking_fee=_f(booking_fee),
        surge_multiplier=round(float(surge), 2),
        total_fare=_f(total_fare),
        stops=body.stops,
        # Persist is_scheduled=True for any deferred ride even if the client
        # left the flag at its default — the scheduled-ride dispatcher filters
        # on {is_scheduled: True, status: 'scheduled'}, so a scheduled_time-only
        # request must carry the flag or it would never be picked up.
        is_scheduled=bool(body.is_scheduled or _is_deferred_schedule),
        requires_wav=body.requires_wav,
        quiet_mode=body.quiet_mode,
        rider_notes=body.rider_notes,
        scheduled_time=body.scheduled_time,
        driver_earnings=_f(driver_earnings),
        admin_earnings=_f(admin_earnings),
        payment_method=body.payment_method,
        payment_method_id=body.payment_method_id,
        # NOTE: ``requires_wav`` was passed twice (once at line ~1090, again
        # here) — Python 3.11+ raises SyntaxError, blocking module import
        # and the entire test suite. Drop-in fix unblocks Audit-17 Phase 1c.
        status=_initial_status,
        pickup_otp=pickup_otp_plain,
        ride_requested_at=datetime.now(timezone.utc),
    )

    ride_data = ride.dict()
    # Pickup road-snap (pickup_nav_*) happens in _prep_and_dispatch after the
    # insert — the Roads API round-trip was blocking the booking response.
    # Readers fall back to pickup_lat/lng while pickup_nav_* is NULL.
    if body.corporate_account_id:
        ride_data["corporate_account_id"] = body.corporate_account_id
    if _corp_member_id:
        ride_data["corporate_member_id"] = _corp_member_id
    if service_area_id:
        ride_data["service_area_id"] = service_area_id
    # Preserve the original planned (straight-line) distance. ride.distance_km
    # will be overwritten with the actual GPS-measured distance on completion.
    ride_data["planned_distance_km"] = round(distance_km, 2)
    if body.planned_route_polyline:
        ride_data["planned_route_polyline"] = body.planned_route_polyline
    # Only store airport surcharge when it actually applies
    if airport_fee > 0:
        ride_data["airport_fee"] = _f(airport_fee)
        if airport_zone_name:
            ride_data["airport_zone_name"] = airport_zone_name

    ride_data["area_fees_breakdown"] = fees_result.get("fees", [])
    ride_data["area_fees_total"] = area_fees_total
    ride_data["tax_amount"] = tax_amount
    ride_data["tax_breakdown"] = fees_result.get("tax_breakdown", {})
    ride_data["grand_total"] = grand_total
    if idempotency_key:
        ride_data["idempotency_key"] = idempotency_key

    # ── Corporate work-profile pre-dispatch check ─────────────────────────────
    # Activated when the rider sends work_profile=true + corporate_account_id.
    # Policy violation or insufficient funds → reject before a driver is
    # disturbed. Personal rides (no work_profile flag) skip this block entirely.
    if body.work_profile and body.corporate_account_id:
        _corp_company_id = body.corporate_account_id

        # 1. Resolve active membership
        _memberships = await db_supabase.list_active_memberships_for_user(current_user["id"])
        _membership = next((m for m in _memberships if m.get("company_id") == _corp_company_id), None)
        if not _membership:
            raise HTTPException(
                status_code=400,
                detail={"reason": "no_corporate_membership"},
            )

        # 2–4. Fetch allowance + policy, evaluate all rules.
        _policy_result = await evaluate_policy_for_ride(
            corporate_account_id=_corp_company_id,
            rider_id=current_user["id"],
            estimated_fare=total_fare,
            ride_type=body.vehicle_type_id or "standard",
            pickup_time=_policy_pickup_time,
            policy_override=_membership.get("policy_override", False),
        )
        if not _policy_result.passed:
            raise HTTPException(
                status_code=400,
                detail={
                    "reason": "policy_violation",
                    "failed_rules": _policy_result.failed_rules,
                },
            )

        # 5. Pre-auth buffer check (skip for unlimited allowances).
        # Uses allowance + policy surfaced by evaluate_policy_for_ride so we
        # don't need a second round-trip to fetch them separately.
        _allowance = _policy_result.allowance
        _policy = _policy_result.policy
        if _allowance.get("type") != "unlimited":
            _remaining = _d(str(_allowance.get("amount") or 0)) - max(_d(str(_allowance.get("used") or 0)), _d("0"))
            _master_permitted = _policy.get("allowed_payment_source", "both") in (
                "master_only",
                "both",
            )
            if _remaining < _round(_d(str(_f(total_fare))) * _d("1.5")) and not _master_permitted:
                raise HTTPException(
                    status_code=400,
                    detail={"reason": "allowance_low"},
                )

        # 6. Tag ride as corporate
        ride_data["corporate_account_id"] = _corp_company_id
        ride_data["payment_method"] = "company_allowance"

    # ── Card pre-authorization (buffered hold) ────────────────────────────────
    # For an immediate CARD ride, place a manual-capture hold of
    # (grand_total + RIDE_AUTH_BUFFER_CAD) BEFORE the ride is inserted/dispatched.
    # This catches a dead card up front (a true decline raises 402 here, before a
    # driver is disturbed) and reserves headroom so a post-trip tip captures on
    # the same PaymentIntent. Skipped for wallet/corporate (different settlement)
    # and for scheduled rides (authorized at dispatch time — see scheduled_rides).
    # Any non-decline failure degrades to "no hold" and the existing post-trip
    # settlement path; the buffer never blocks a ride the rider could take.
    if ride_data.get("payment_method") == "card" and not _is_deferred_schedule:
        if body.preauthorized_payment_intent_id:
            # SCA two-step, second leg: the app already confirmed this hold
            # on-device (3DS / Apple Pay). Verify with Stripe and attach it —
            # do NOT authorize again (that would place a second hold).
            ride_data.update(
                await _attach_preauthorized_hold(
                    ride_id=ride_data["id"],
                    rider_id=current_user["id"],
                    payment_intent_id=body.preauthorized_payment_intent_id,
                    stripe_customer_id=(rider_row or {}).get("stripe_customer_id"),
                    min_amount=_d(grand_total),
                )
            )
        else:
            _preauth = await _preauthorize_ride_card(
                ride_id=ride_data["id"],
                rider_id=current_user["id"],
                grand_total=_d(grand_total),
                stripe_customer_id=(rider_row or {}).get("stripe_customer_id"),
                payment_method_id=body.payment_method_id,
            )
            if _preauth.requires_action:
                # SCA two-step, first leg: the hold needs an on-device confirm.
                # Do NOT create the ride yet — hand the client_secret back; the
                # app runs the Stripe sheet, then re-books with
                # preauthorized_payment_intent_id set. Keeps the ride state
                # machine clean: no ride exists until the hold is real.
                return {
                    "requires_action": True,
                    "payment_authorization": {
                        "client_secret": _preauth.client_secret,
                        "payment_intent_id": _preauth.payment_intent_id,
                    },
                }
            ride_data.update(_preauth.fields)

    fresh_ride, _idempotent_reuse = await _insert_ride_with_code(ride_data, current_user["id"])
    if _idempotent_reuse:
        # DB-enforced idempotency: a concurrent duplicate request already
        # created this ride — return it instead of double-charging.
        return fresh_ride

    # ── Apply promo code if provided ──
    if body.promo_code:
        try:
            try:
                from ..routes.promotions import (
                    _record_promo_application,
                    _validate_promo_for_user,
                )
            except ImportError:
                from routes.promotions import (
                    _record_promo_application,
                    _validate_promo_for_user,
                )

            server_fare = _d(fresh_ride.get("total_fare", 0))
            ride_portion = (
                _d(fresh_ride.get("base_fare") or 0)
                + _d(fresh_ride.get("distance_fare") or 0)
                + _d(fresh_ride.get("time_fare") or 0)
            ) or server_fare
            validation = await _validate_promo_for_user(
                code=body.promo_code,
                user_id=current_user["id"],
                ride_fare=ride_portion,
                ride_id=ride.id,
                grand_total=_d(fresh_ride.get("grand_total") or server_fare),
            )
            if validation.get("valid"):
                discount = _d(validation["discount_amount"])
                # Backstop cap: promos discount the ride fare only (driver
                # earnings = base+dist+time), never fees or taxes. _validate_promo_for_user
                # already caps for flat/percentage promos; mirror it here so any
                # future validation path cannot store a discount > ride_portion.
                # free_ride covers the full grand_total by design and bypasses this cap.
                if not validation.get("free_ride") and ride_portion > 0:
                    discount = min(discount, ride_portion)
                application_id = await _record_promo_application(
                    promo_id=validation["promo_id"],
                    code=validation["code"],
                    user_id=current_user["id"],
                    discount=discount,
                )
                discounted_grand = _f(_round(_d(fresh_ride.get("grand_total", server_fare)) - discount))
                # NOTE: do NOT mutate total_fare. total_fare is the fare-side
                # subtotal (base+dist+time+booking+airport) used by area-fee
                # and tax math downstream. The rider's effective bill goes on
                # grand_total only. Overwriting total_fare to server_fare-discount
                # produced bills like total_fare=$2.14 (12.14-10) while
                # grand_total=$5.30 — and any rider-app path that fell back to
                # total_fare displayed the wrong amount to the customer.
                # subtotal_fare records the pre-promo subtotal for receipts.
                await db_supabase.update_one(
                    "rides",
                    {"id": ride.id},
                    {
                        "subtotal_fare": _f(server_fare),
                        "discount_amount": _f(discount),
                        "promo_code": validation["code"],
                        "promo_application_id": application_id,
                        "grand_total": discounted_grand,
                    },
                )
                fresh_ride["subtotal_fare"] = _f(server_fare)
                fresh_ride["discount_amount"] = _f(discount)
                fresh_ride["promo_code"] = validation["code"]
                fresh_ride["promo_application_id"] = application_id
                fresh_ride["grand_total"] = discounted_grand
        except HTTPException as he:
            logger.warning(
                "create_ride: promo '%s' rejected for rider %s on ride %s: %s",
                body.promo_code,
                current_user["id"],
                ride.id,
                he.detail,
            )
            fresh_ride["promo_error"] = he.detail if isinstance(he.detail, str) else str(he.detail)
        except Exception as e:
            logger.error(
                f"create_ride: promo application failed for code={body.promo_code}: {e}",
                exc_info=True,
            )
            fresh_ride["promo_error"] = "Promo could not be applied"

    # ── Fare breakdown snapshot ──
    # Frozen receipt: the exact line items shown to the rider at booking.
    # Only stores lines + grand_total — all component fields (base_fare,
    # tax_breakdown, promo_code, etc.) already live on the ride row.
    # When fare_lock_enabled, GET /rides/{id} serves this instead of
    # recomputing from (potentially recalculated) ride fields.
    snapshot_lines = _build_fare_breakdown(fresh_ride)
    fare_snapshot = {
        "lines": snapshot_lines,
        "grand_total": _sum_fare_breakdown(snapshot_lines),
        "locked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db_supabase.update_one(
            "rides",
            {"id": ride.id},
            {"fare_breakdown_snapshot": fare_snapshot},
        )
        fresh_ride["fare_breakdown_snapshot"] = fare_snapshot
    except Exception as snap_err:
        logger.warning(f"create_ride: fare snapshot save failed: {snap_err}")

    # ── Route snapshot at creation ──
    # Generate a PNG map of the planned route and upload to Supabase
    # Storage. Available even if the ride is later cancelled/failed.
    planned_poly = fresh_ride.get("planned_route_polyline")
    logger.info(
        f"create_ride: planned_route_polyline check — "
        f"present={planned_poly is not None}, "
        f"type={type(planned_poly).__name__}, "
        f"len={len(planned_poly) if isinstance(planned_poly, list) else 'N/A'}"
    )
    if planned_poly and isinstance(planned_poly, list) and len(planned_poly) >= 2:

        async def _create_planned_snapshot():
            try:
                try:
                    from .drivers import _generate_and_store_ride_snapshot
                except ImportError:
                    from routes.drivers import _generate_and_store_ride_snapshot
                logger.info(
                    f"create_ride: generating planned route snapshot for ride {ride.id} with {len(planned_poly)} points"
                )
                await _generate_and_store_ride_snapshot(
                    ride_id=ride.id,
                    pickup_lat=fresh_ride.get("pickup_lat"),
                    pickup_lng=fresh_ride.get("pickup_lng"),
                    dropoff_lat=fresh_ride.get("dropoff_lat"),
                    dropoff_lng=fresh_ride.get("dropoff_lng"),
                    phase_polylines=None,
                    route_polyline=planned_poly,
                )
                logger.info(f"create_ride: planned route snapshot completed for ride {ride.id}")
            except Exception as exc:
                logger.error(f"create_ride: planned route snapshot failed: {exc}", exc_info=True)

        spawn(_create_planned_snapshot())

    # Let admin live-monitoring see the request before dispatch starts —
    # previously the dashboard only observed a ride once a driver accepted,
    # which made it impossible to watch an unassigned ride sit in queue.
    # Deferred scheduled rides are NOT live requests yet; the scheduler
    # broadcasts ride_requested when it flips them to SEARCHING.
    #
    # The payload must match the dashboard's MonitoringRide contract (a nested
    # ``ride`` object — see admin-dashboard/.../monitoring/types.ts); the
    # handler calls applyRide(event.ride) to add the new row.
    if not _is_deferred_schedule:
        try:
            from .admin.monitoring import build_monitoring_ride
        except ImportError:
            from routes.admin.monitoring import build_monitoring_ride
        try:
            await manager.broadcast_to_admins(
                {
                    "type": "ride_requested",
                    "ride": build_monitoring_ride(fresh_ride, rider=current_user),
                }
            )
        except Exception as _exc:  # pragma: no cover - best effort
            logger.warning(f"create_ride: admin broadcast failed: {_exc}")

    # Booking-latency fix: the pickup road-snap, the server-side Directions
    # polyline, and the entire matching pipeline (driver scan, Redis
    # presence, ETA matrix, offer inserts, per-driver quest reads) used to
    # run inline here, so the rider's booking request blocked on all of it
    # (multiple seconds against a <2s dispatch SLA). They now run in
    # _prep_and_dispatch as a background task; the rider gets the inserted
    # ride back immediately and learns about assignment via the
    # ride_status_changed WS event — the same path scheduled rides and
    # offer-timeout re-dispatch already use.
    #
    # CR-1: deferred scheduled rides still skip dispatch (the scheduler loop
    # dispatches at scheduled_time); the task still prepares nav + polyline
    # so the eventual offers carry a road-following route.
    if _is_deferred_schedule:
        logger.info(
            f"create_ride: ride {ride.id} scheduled for {body.scheduled_time} — "
            "parked in 'scheduled', deferring dispatch to scheduler loop"
        )
    spawn(
        _prep_and_dispatch(
            ride.id,
            fresh_ride,
            pickup_lat=body.pickup_lat,
            pickup_lng=body.pickup_lng,
            stops=body.stops or [],
            dispatch=not _is_deferred_schedule,
        )
    )

    # Dispatch may have set driver_id / status; read the current state
    # for the response. Skipping this extra fetch would mean the rider
    # app sees "searching" even when a driver was already assigned in
    # the same request, so we keep this one round-trip on purpose.
    updated_ride = await db_supabase.get_ride(ride.id)

    # Small helper to ensure we return a clean dict
    def serialize_doc(doc):
        return doc

    if updated_ride and updated_ride.get("status") == RideStatus.SEARCHING:
        spawn(ride_search_timeout(ride.id))

    spawn(
        log_user_action(
            current_user,
            "ride_created",
            "rides",
            ride.id,
            {"status": (updated_ride.get("status") if updated_ride else RideStatus.SEARCHING)},
        )
    )

    # Carry forward transient fields that don't live in the DB but the
    # client needs (promo_error is set when promo validation fails).
    if fresh_ride.get("promo_error") and updated_ride:
        updated_ride["promo_error"] = fresh_ride["promo_error"]

    return serialize_doc(updated_ride)


async def _insert_ride_with_code(ride_data: dict, rider_id: str) -> tuple[dict, bool]:
    """Insert a ride row, allocating a unique SPR-XXXXXX ride_code.

    Shared by create_ride and the corporate guest-booking service so the
    collision/idempotency semantics live in one place. Returns
    ``(ride_row, idempotent_reuse)`` — when ``idempotent_reuse`` is True the
    row is a previously-created ride matched by idempotency_key and the
    caller must NOT run post-insert side effects (promo, dispatch) again.

    ``insert_ride`` returns the row Supabase just wrote — used directly
    instead of a follow-up ``get_ride`` round-trip; falls back to the local
    ride_data if the driver returns None (e.g. stub DB in tests).

    ride_code (migration 40) is a short SPR-XXXXXX string operators and
    riders can quote. The UUID in ride_data["id"] stays primary key. On the
    astronomically-unlikely unique-constraint collision we retry with a
    fresh code; on PGRST204 ("column does not exist", migration hasn't
    landed yet) fall back to inserting without the code.
    """
    inserted = None
    last_exc: Optional[Exception] = None
    for _attempt in range(3):
        ride_data["ride_code"] = generate_ride_code()
        try:
            inserted = await db_supabase.insert_ride(ride_data)
            break
        except Exception as e:
            last_exc = e
            # db_supabase.run_sync wraps PostgREST errors in DatabaseError/
            # DuplicateRecordError, so str(e) is a generic sentinel — the SQLSTATE
            # and constraint name live in details['original']/__cause__. Match the
            # real text + structured code, not str(e) (which silently missed every
            # branch below and fell through to a generic 409/503).
            msg = db_error_text(e)
            code = pg_error_code(e).upper()
            if code == "PGRST204" or "pgrst204" in msg or "column" in msg:
                ride_data.pop("ride_code", None)
                inserted = await db_supabase.insert_ride(ride_data)
                break
            if "rides_one_active_per_rider" in msg:
                raise HTTPException(status_code=409, detail="You already have an active ride") from e
            if "idx_rides_rider_idempotency_key" in msg:
                # Concurrent duplicate request lost the race — surface the
                # already-created ride to the caller.
                existing = await db_supabase.find_one(
                    "rides",
                    {"idempotency_key": ride_data.get("idempotency_key"), "rider_id": rider_id},
                )
                if existing:
                    return existing, True
                raise HTTPException(status_code=409, detail="Duplicate ride request") from e
            if code == "23505" or "unique" in msg or "duplicate" in msg:
                continue  # retry with a new code for ride_code conflicts
            raise
    else:
        logger.error(f"_insert_ride_with_code: could not allocate unique ride_code after 3 tries: {last_exc}")
        raise HTTPException(status_code=503, detail="Could not allocate ride code")

    return inserted or ride_data, False


async def _prep_and_dispatch(
    ride_id: str,
    fresh_ride: dict,
    *,
    pickup_lat: float,
    pickup_lng: float,
    stops: list,
    dispatch: bool,
) -> None:
    """Post-booking pipeline: pickup road-snap → server polyline → dispatch.

    Runs as a background task so the booking response never blocks on the
    Roads API, Directions, or the matching pipeline. ``dispatch=False``
    (deferred scheduled rides) still prepares nav/polyline but leaves
    dispatch to the scheduler loop. Dispatch failures surface loudly —
    ride_search_timeout and the stuck-ride sweeper remain the safety nets
    for a ride stranded in 'searching'.
    """
    try:
        # Pickup snap: best-effort; the rider's exact pin (pickup_lat/lng) is
        # untouched, and readers fall back to it while pickup_nav_* is NULL.
        try:
            try:
                from ..utils.route_distance import snap_to_road
            except ImportError:
                from utils.route_distance import snap_to_road  # type: ignore
            _snapped = await snap_to_road(pickup_lat, pickup_lng)
            if _snapped:
                await db_supabase.update_ride(ride_id, {"pickup_nav_lat": _snapped[0], "pickup_nav_lng": _snapped[1]})
                fresh_ride["pickup_nav_lat"], fresh_ride["pickup_nav_lng"] = _snapped
        except Exception:
            logger.warning("pickup snap_to_road failed; using original pin", exc_info=True)

        # Server-side planned_route_polyline: only when the rider-app didn't
        # send one (race between booking tap and Directions finishing, or no
        # Maps key on client) — every offer should carry a road-following
        # polyline instead of the dashed straight-line driver-app fallback.
        _existing_poly = fresh_ride.get("planned_route_polyline")
        if not _existing_poly or (isinstance(_existing_poly, list) and len(_existing_poly) < 2):
            try:
                _s = await get_app_settings()
                _maps_key = (_s or {}).get("google_maps_api_key", "")
                if _maps_key:
                    _computed = await _fetch_directions_polyline(
                        fresh_ride["pickup_lat"],
                        fresh_ride["pickup_lng"],
                        fresh_ride["dropoff_lat"],
                        fresh_ride["dropoff_lng"],
                        _maps_key,
                        waypoints=stops,
                    )
                    if _computed:
                        await db_supabase.update_ride(ride_id, {"planned_route_polyline": _computed})
                        fresh_ride["planned_route_polyline"] = _computed
                        logger.info(
                            "create_ride: server-computed polyline (%d pts) stored for ride %s",
                            len(_computed),
                            ride_id,
                        )
            except Exception as _poly_err:
                logger.warning("create_ride: polyline computation failed (non-fatal): %s", _poly_err)

        if dispatch:
            # Pass the fresh ride through so the dispatch path doesn't
            # re-fetch the row we just inserted.
            await match_driver_to_ride(ride_id, ride=fresh_ride)
    except Exception:
        # A failure here strands the ride in 'searching' with no offers —
        # dispatch-domain errors must never vanish into a dropped task.
        logger.error(f"[DISPATCH] post-booking pipeline failed for ride {ride_id}", exc_info=True)


from fastapi import Request  # noqa: E402


@ride_read_limit
@api_router.get("/active")
async def get_active_ride(request: Request = None, current_user: dict = Depends(get_current_user)):
    """Get rider's current active/pending ride (if any). Used on app launch to resume."""
    # First check for rides that need payment (completed but not paid)
    # Then check for active rides
    active_statuses = list(RideStatus.active_statuses())

    # Check for unpaid completed ride first (must pay before new ride).
    # ``waived_admin`` is the terminal value written by admin force-complete
    # (admin/rides.py admin_complete_ride) — no real charge happened, but
    # the rider must not be trapped on the payment screen.
    unpaid_ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {
                "rider_id": current_user["id"],
                "status": RideStatus.COMPLETED,
                "payment_status": {"$nin": ["paid", "waived_admin"]},
            },
            limit=1,
        )
    )
    if unpaid_ride:
        ride = unpaid_ride
    else:
        ride = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows(
                "rides",
                {
                    "rider_id": current_user["id"],
                    "status": {"$in": active_statuses},
                },
                limit=1,
            )
        )

    if not ride:
        return {"active": False, "ride": None}

    # Attach driver info if assigned
    driver = None
    if ride.get("driver_id"):
        driver = await db_supabase.get_driver_by_id(ride["driver_id"])
        if driver:
            user = await db_supabase.get_user_by_id(driver.get("user_id"))
            driver = {
                "id": driver["id"],
                "driver_code": driver.get("driver_code"),
                "name": (f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "Driver"),
                "rating": driver.get("rating", 4.8),
                "total_rides": driver.get("total_rides", 0),
                # Driver photo lives on the USER row (users.profile_image),
                # shown to riders only once admin-approved.
                "photo_url": _rider_visible_photo(user),
                "vehicle_make": driver.get("vehicle_make"),
                "vehicle_model": driver.get("vehicle_model"),
                "vehicle_color": driver.get("vehicle_color"),
                "vehicle_year": driver.get("vehicle_year"),
                "license_plate": driver.get("license_plate"),
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                "heading": driver.get("heading"),
            }

    def serialize_doc(doc):
        return doc

    ride_data = serialize_doc(ride)
    ride_data["driver"] = driver
    return {"active": True, "ride": ride_data}


_RIDE_HISTORY_VISIBLE_OR = (
    f"status.eq.{RideStatus.COMPLETED.value},and(status.eq.{RideStatus.CANCELLED.value},driver_id.not.is.null)"
)


def _ride_history_cursor_or(cursor_ts: Optional[str], before: Optional[str]) -> Optional[str]:
    if not cursor_ts or not before:
        return None
    return f"created_at.lt.{cursor_ts},and(created_at.eq.{cursor_ts},id.lt.{before})"


async def _fetch_ride_history_page(
    *,
    rider_id: str,
    limit: int,
    cursor_ts: Optional[str],
    before: Optional[str],
) -> list[dict]:
    """Fetch one stable ride-history page, including one extra row for has-more."""
    if not db_supabase.supabase:
        return []

    cursor_clause = _ride_history_cursor_or(cursor_ts, before)

    def _fn():
        q = db_supabase.supabase.table("rides").select("*").eq("rider_id", rider_id).or_(_RIDE_HISTORY_VISIBLE_OR)
        if cursor_clause:
            q = q.or_(cursor_clause)
        q = q.order("created_at", desc=True).order("id", desc=True).limit(limit + 1)
        res = q.execute()
        return getattr(res, "data", None) or []

    return await db_supabase.run_sync(_fn)


@ride_read_limit
@api_router.get("/history")
async def get_ride_history(
    request: Request = None,
    limit: int = Query(default=20, ge=1, le=100),
    before: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Get rider's past rides for the activity tab with cursor-based pagination.

    Pass ``before=<ride_id>`` to fetch the page of rides older than that ride.
    Returns ``next_cursor`` (the id of the last ride in this page) to use as
    the next ``before`` value, or ``null`` when there are no more pages.
    """
    # Resolve cursor to a timestamp so we can push the predicate to the DB.
    # Fetching 500 rows and slicing in Python caused O(n) reads on busy accounts.
    cursor_ts = None
    if before:
        cursor_ride = await db_supabase.find_one("rides", {"id": before, "rider_id": current_user["id"]})
        if cursor_ride:
            cursor_ts = cursor_ride.get("created_at")

    candidates = await _fetch_ride_history_page(
        rider_id=current_user["id"],
        limit=limit,
        cursor_ts=cursor_ts,
        before=before,
    )
    rides = candidates[:limit]
    has_more = len(candidates) > limit

    try:
        _settings = await get_app_settings()
        _fare_locked = _settings.get("fare_lock_enabled", False) if _settings else False
    except Exception:
        _fare_locked = False

    for r in rides:
        snap = r.get("fare_breakdown_snapshot")
        if _fare_locked and snap and isinstance(snap, dict) and snap.get("lines"):
            lines = list(snap["lines"])
            ride_tip = float(_d(r.get("tip_amount") or 0))
            has_tip_line = any(ln.get("type") == "tip" for ln in lines)
            if ride_tip > 0 and not has_tip_line:
                lines.append({"label": "Tip", "amount": _f(_d(ride_tip)), "type": "tip"})
            r["fare_breakdown"] = lines
            r["grand_total"] = _sum_fare_breakdown(lines)
            r["fare_locked"] = True
        else:
            r["fare_breakdown"] = _build_fare_breakdown(r)
            r["grand_total"] = _sum_fare_breakdown(r["fare_breakdown"])
            r["fare_locked"] = False
        r["actual_duration_minutes"] = _actual_duration_minutes(r)

    next_cursor = rides[-1]["id"] if has_more and rides else None

    return {"rides": rides, "limit": limit, "next_cursor": next_cursor}


@api_router.get("/stats")
async def get_rider_stats(
    period: str = Query(default="today"),
    current_user: dict = Depends(get_current_user),
):
    """Aggregated trip stats for the rider activity summary card.

    Timezone is derived from the service area of the rider's most recent completed
    ride so 'today' aligns with the driver's local calendar day, not UTC midnight.
    """
    from zoneinfo import ZoneInfo

    _tz_name = "America/Regina"
    _recent = await db_supabase.get_rows(
        "rides",
        {"rider_id": current_user["id"], "status": RideStatus.COMPLETED},
        order="ride_completed_at",
        desc=True,
        limit=1,
    )
    if _recent and _recent[0].get("service_area_id"):
        _sa = await db_supabase.get_rows("service_areas", {"id": _recent[0]["service_area_id"]}, limit=1)
        if _sa and _sa[0].get("timezone"):
            _tz_name = _sa[0]["timezone"]

    now = datetime.now(ZoneInfo(_tz_name))
    use_date_filter = True
    if period in ("today", "day"):
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start_date = now - timedelta(days=7)
    elif period == "month":
        start_date = now - timedelta(days=30)
    elif period == "all":
        use_date_filter = False
        start_date = None
    else:
        start_date = now - timedelta(days=7)

    filters: dict = {
        "rider_id": current_user["id"],
        "status": RideStatus.COMPLETED,
    }
    if use_date_filter and start_date:
        filters["ride_completed_at"] = {"$gte": start_date.isoformat()}

    rides = await db_supabase.get_rows("rides", filters, limit=10000)

    total_distance = sum(_d(r.get("distance_km") or 0) for r in rides)
    total_rides = len(rides)
    total_saved = sum(_d(r.get("discount_amount") or 0) for r in rides)
    # CO2 saving vs. driving solo: 0.12 kg per km (rideshare vs. personal vehicle)
    co2_saved_kg = round(float(total_distance) * 0.12, 2)

    return {
        "period": period,
        "total_rides": total_rides,
        "total_distance_km": round(float(total_distance), 1),
        "total_saved": str(_round(total_saved)),
        "co2_saved_kg": co2_saved_kg,
    }


@ride_read_limit
@api_router.get("/scheduled")
async def get_scheduled_rides(request: Request = None, current_user: dict = Depends(get_current_user)):
    """Get all upcoming scheduled rides for the current rider."""
    rides = await db_supabase.get_rows(
        "rides",
        {
            "rider_id": current_user["id"],
            "is_scheduled": True,
            "status": {"$nin": [RideStatus.COMPLETED, RideStatus.CANCELLED]},
        },
        order="scheduled_time",
        desc=False,
        limit=50,
    )
    return rides


@ride_read_limit
@api_router.get("/{ride_id}")
async def get_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Fetch details of a specific ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise RideNotFoundException(
            ride_id=ride_id,
            message_key=ErrorKeys.RIDE_NOT_FOUND,
        )

    # Security check: must be rider or driver of this ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        # Admin check
        if current_user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Not authorized to view this ride")

    # Include driver details if assigned.
    # Previously this dumped the ENTIRE driver row to the rider, which
    # included license_number, insurance_expiry_date,
    # background_check_expiry_date, work_eligibility_expiry_date,
    # vehicle_vin, document URLs, and the driver's stored phone — a
    # material PII leak to any rider on any ride. Only surface the
    # fields the rider actually needs to identify the driver and the
    # car pulling up (name, plate + make/model/color, rating, and the
    # live coordinates used for the map marker).
    if ride.get("driver_id"):
        assigned_driver = await db_supabase.get_driver_by_id(ride["driver_id"])
        if assigned_driver:
            # Driver photo lives on the user row (users.profile_image), shown to
            # riders only once admin-approved.
            _drv_user = await db_supabase.get_user_by_id(assigned_driver.get("user_id"))
            ride["driver"] = DriverPublicView(
                id=assigned_driver.get("id", ""),
                name=assigned_driver.get("name", ""),
                rating=assigned_driver.get("rating"),
                total_rides=assigned_driver.get("total_rides"),
                photo_url=_rider_visible_photo(_drv_user),
                vehicle_make=assigned_driver.get("vehicle_make"),
                vehicle_model=assigned_driver.get("vehicle_model"),
                vehicle_color=assigned_driver.get("vehicle_color"),
                license_plate=assigned_driver.get("license_plate"),
                vehicle_year=assigned_driver.get("vehicle_year"),
                lat=assigned_driver.get("lat"),
                lng=assigned_driver.get("lng"),
            ).dict()

    # Derive free_cancel_seconds_remaining + cancellation_fee from app_settings (UX-001).
    # These allow the frontend to show accurate countdown/fee without hardcoding.
    try:
        from settings_loader import get_app_settings  # type: ignore
    except ImportError:
        try:
            from ..settings_loader import get_app_settings  # type: ignore
        except ImportError:
            get_app_settings = None  # type: ignore

    free_cancel_window = 120
    cancellation_fee_amount = Decimal("4.50")
    if get_app_settings:
        try:
            settings = await get_app_settings()
            # Per-service-area overrides take priority over global settings
            area = None
            if ride.get("service_area_id"):
                area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            if area and area.get("free_cancel_window_seconds") is not None:
                free_cancel_window = int(area["free_cancel_window_seconds"])
            else:
                free_cancel_window = int(settings.get("free_cancel_window_seconds", 120))
            fee_admin = Decimal(
                str((area or {}).get("cancel_fee_admin_share") or settings.get("cancellation_fee_admin", "0.50"))
            )
            fee_driver = Decimal(
                str((area or {}).get("cancel_fee_driver_share") or settings.get("cancellation_fee_driver", "4.00"))
            )
            cancellation_fee_amount = fee_admin + fee_driver
        except Exception:
            logger.error("Failed to fetch app settings for cancellation config", exc_info=True)

    driver_accepted_at = ride.get("driver_accepted_at")
    ride_status = ride.get("status")

    # Once the driver has physically arrived, the free-cancel window is over
    # regardless of elapsed time — the driver already spent fuel/time.
    if ride_status == RideStatus.DRIVER_ARRIVED:
        ride["free_cancel_seconds_remaining"] = 0
    elif driver_accepted_at:
        from datetime import datetime, timezone

        try:
            if isinstance(driver_accepted_at, str):
                accepted_dt = datetime.fromisoformat(driver_accepted_at.replace("Z", "+00:00"))
            else:
                accepted_dt = driver_accepted_at
            if accepted_dt.tzinfo is None:
                accepted_dt = accepted_dt.replace(tzinfo=timezone.utc)
            elapsed = int((datetime.now(timezone.utc) - accepted_dt).total_seconds())
            ride["free_cancel_seconds_remaining"] = max(0, free_cancel_window - elapsed)
        except Exception:
            ride["free_cancel_seconds_remaining"] = 0
    else:
        ride["free_cancel_seconds_remaining"] = None  # driver not yet accepted

    ride["free_cancel_window_seconds"] = free_cancel_window
    ride["cancellation_fee"] = cancellation_fee_amount

    # No-show timer: once the driver arrives, they must wait 5 minutes
    # before they can mark the rider as a no-show. Expose the countdown
    # so both apps can show an accurate timer.
    noshow_wait_seconds = 300
    if get_app_settings:
        try:
            settings = await get_app_settings()
            # Per-service-area override → global fallback
            if area and area.get("noshow_wait_seconds") is not None:
                noshow_wait_seconds = int(area["noshow_wait_seconds"])
            else:
                noshow_wait_seconds = int(settings.get("noshow_wait_seconds", 300))
        except Exception:
            logger.warning("Failed to load settings or service area override for noshow_wait_seconds")
    ride["noshow_wait_seconds"] = noshow_wait_seconds

    if ride_status == RideStatus.DRIVER_ARRIVED:
        driver_arrived_at = ride.get("driver_arrived_at")
        if driver_arrived_at:
            from datetime import datetime, timezone

            try:
                if isinstance(driver_arrived_at, str):
                    arrived_dt = datetime.fromisoformat(driver_arrived_at.replace("Z", "+00:00"))
                else:
                    arrived_dt = driver_arrived_at
                if arrived_dt.tzinfo is None:
                    arrived_dt = arrived_dt.replace(tzinfo=timezone.utc)
                elapsed = int((datetime.now(timezone.utc) - arrived_dt).total_seconds())
                ride["noshow_seconds_remaining"] = max(0, noshow_wait_seconds - elapsed)
                ride["noshow_eligible"] = elapsed >= noshow_wait_seconds
            except Exception:
                ride["noshow_seconds_remaining"] = noshow_wait_seconds
                ride["noshow_eligible"] = False
        else:
            ride["noshow_seconds_remaining"] = noshow_wait_seconds
            ride["noshow_eligible"] = False
    else:
        ride["noshow_seconds_remaining"] = None
        ride["noshow_eligible"] = False

    # M-4: Expose offer expiry so the rider app can show an accurate
    # countdown progress bar while waiting for the driver to accept.
    # Only meaningful in driver_assigned state; cleared once accepted.
    if ride.get("status") == RideStatus.DRIVER_ASSIGNED:
        driver_notified_at = ride.get("driver_notified_at")
        offer_timeout_seconds = 15
        if get_app_settings:
            try:
                settings = await get_app_settings()
                offer_timeout_seconds = int(settings.get("ride_offer_timeout_seconds", 15))
            except Exception:
                # Non-fatal: fall back to hardcoded default if settings fetch fails
                logger.error(
                    "Failed to fetch app settings for offer timeout config",
                    exc_info=True,
                )
        ride["offer_timeout_seconds"] = offer_timeout_seconds
        if driver_notified_at:
            try:
                from datetime import datetime, timedelta, timezone

                if isinstance(driver_notified_at, str):
                    notified_dt = datetime.fromisoformat(driver_notified_at.replace("Z", "+00:00"))
                else:
                    notified_dt = driver_notified_at
                if notified_dt.tzinfo is None:
                    notified_dt = notified_dt.replace(tzinfo=timezone.utc)
                expires_dt = notified_dt + timedelta(seconds=offer_timeout_seconds + 15)
                ride["offer_expires_at"] = expires_dt.isoformat()
            except Exception:
                ride["offer_expires_at"] = None
        else:
            ride["offer_expires_at"] = None
    else:
        ride["offer_expires_at"] = None
        ride["offer_timeout_seconds"] = None

    # PIPEDA / threat-model RI-2: drivers see street-level (no house number)
    # addresses and block-level coordinates for completed rides. Exact
    # addresses are stripped to mitigate address-based stalking (RAT-1)
    # while still giving drivers useful trip history (Uber/Lyft pattern).
    if is_driver and not is_rider:
        ride.pop("pickup_otp", None)
        if ride.get("status") in RideStatus.terminal_statuses():
            _redact_driver_location_fields(ride)

    # When a fare snapshot exists and fare_lock is enabled, the snapshot
    # IS the bill — use it verbatim instead of recomputing from ride fields
    # that may have been adjusted at completion.
    snapshot = ride.get("fare_breakdown_snapshot")
    fare_locked = False
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines"):
        try:
            settings = await get_app_settings()
            fare_locked = settings.get("fare_lock_enabled", False) if settings else False
        except Exception:
            fare_locked = False
    if fare_locked and snapshot:
        lines = list(snapshot["lines"])
        # Ensure tip is reflected even for snapshots that pre-date the tip update
        ride_tip = float(_d(ride.get("tip_amount") or 0))
        has_tip_line = any(ln.get("type") == "tip" for ln in lines)
        if ride_tip > 0 and not has_tip_line:
            lines.append({"label": "Tip", "amount": _f(_d(ride_tip)), "type": "tip"})
        ride["fare_breakdown"] = lines
        ride["grand_total"] = _sum_fare_breakdown(lines)
        ride["fare_locked"] = True
    else:
        ride["fare_breakdown"] = _build_fare_breakdown(ride)
        ride["grand_total"] = _sum_fare_breakdown(ride["fare_breakdown"])
        ride["fare_locked"] = False
    ride["actual_duration_minutes"] = _actual_duration_minutes(ride)

    # Enrich with incentive claims and cancellation fee for this ride.
    # run_sync is mandatory here: a bare .execute() is a synchronous HTTP
    # round-trip that freezes the event loop — and with it every concurrent
    # request on this replica — for the full Supabase latency.
    try:
        _claims_res = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_incentive_claims")
                .select("bonus_amount, incentive_id")
                .eq("ride_id", ride_id)
                .execute()
            )
        )
        _claims = _claims_res.data or []
        _incentive_total = sum(float(c.get("bonus_amount") or 0) for c in _claims)
        ride["incentive_amount"] = round(_incentive_total, 2)
    except Exception:
        logger.debug("ride incentive_claims lookup failed", exc_info=True)
        ride["incentive_amount"] = 0

    # Prefer the frozen driver_earnings_snapshot when available
    des = ride.get("driver_earnings_snapshot")
    if des and isinstance(des, dict) and "total" in des:
        ride["fare_only"] = round(float(des.get("fare") or 0), 2)
        ride["cancel_fee_earned"] = round(float(des.get("cancel_fee") or 0), 2)
        ride["tax_amount_total"] = round(float(des.get("tax") or 0), 2)
        _snap_incentive = float(des.get("incentive") or 0)
        if _snap_incentive > 0:
            ride["incentive_amount"] = round(_snap_incentive, 2)
        tip = float(des.get("tip") or 0)
        ride["total_earned"] = round(
            ride["fare_only"] + tip + ride["incentive_amount"] + ride["cancel_fee_earned"] + ride["tax_amount_total"],
            2,
        )
    else:
        tip = float(ride.get("tip_amount") or 0)
        fare_only = (
            float(ride.get("base_fare") or 0)
            + float(ride.get("distance_fare") or 0)
            + float(ride.get("time_fare") or 0)
        )
        cancel_fee = float(ride.get("cancellation_fee_driver") or 0)
        tax = float(ride.get("tax_amount") or 0)
        if tax == 0:
            snap = ride.get("fare_breakdown_snapshot") or {}
            for ln in snap.get("lines") or []:
                if ln.get("type") in ("tax", "gst", "pst"):
                    tax += float(ln.get("amount") or 0)
            tax = round(tax, 2)
        ride["fare_only"] = round(fare_only, 2)
        ride["cancel_fee_earned"] = round(cancel_fee, 2)
        ride["tax_amount_total"] = tax
        ride["total_earned"] = round(fare_only + tip + ride["incentive_amount"] + cancel_fee + tax, 2)

    return ride


@api_router.post("/{ride_id}/tip")
@payment_action_limit
@idempotent_endpoint(scope="ride_tip")
async def add_tip(
    ride_id: str,
    req: TipRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    # Money arithmetic uses Decimal per CLAUDE.md. The old `float(req.amount)`
    # path drifted when summed with existing driver_earnings.
    tip_amount = _round(_d(req.amount))
    if tip_amount <= 0:
        raise HTTPException(status_code=400, detail="Tip amount must be greater than zero")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized to tip this ride")

    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Can only tip completed rides")

    # R-P1-20: Block duplicate tips — one tip per ride.
    existing_tip = _d(ride.get("tip_amount") or 0)
    if existing_tip > 0:
        raise HTTPException(status_code=400, detail="ERR_TIP_DUPLICATE")

    existing_earnings = _d(ride.get("driver_earnings") or 0)
    new_tip = _round(existing_tip + tip_amount)
    new_driver_earnings = _round(existing_earnings + tip_amount)

    update_payload = {"tip_amount": _f(new_tip), "driver_earnings": _f(new_driver_earnings)}

    # Update fare_breakdown_snapshot to include the tip so invoices and
    # ride details reflect the final charged amount.
    snapshot = ride.get("fare_breakdown_snapshot")
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines") is not None:
        updated_lines = [ln for ln in snapshot["lines"] if ln.get("type") != "tip"]
        updated_lines.append({"label": "Tip", "amount": _f(new_tip), "type": "tip"})
        snapshot["lines"] = updated_lines
        snapshot["grand_total"] = _sum_fare_breakdown(updated_lines)
        update_payload["fare_breakdown_snapshot"] = snapshot

    # Update driver_earnings_snapshot with the tip — rebuild via the Decimal
    # builder so the frozen total stays an exact component sum (feeds T4A).
    des = ride.get("driver_earnings_snapshot")
    if des and isinstance(des, dict):
        des.update(
            build_earnings_snapshot(
                fare=des.get("fare") or 0,
                tip=new_tip,
                incentive=des.get("incentive") or 0,
                tax=des.get("tax") or 0,
                cancel_fee=des.get("cancel_fee") or 0,
            )
        )
        update_payload["driver_earnings_snapshot"] = des

    await db_supabase.update_ride(ride_id, update_payload)

    # Notify the assigned driver so the tip shows up immediately instead
    # of only after the next earnings refresh. Best-effort — the tip has
    # already been persisted, so a failed notification must not surface
    # as a rider-facing error.
    driver_user_id = None
    driver_row_id = ride.get("driver_id")
    if driver_row_id:
        try:
            driver = await db_supabase.get_driver_by_id(driver_row_id)
            driver_user_id = driver.get("user_id") if driver else None
        except Exception as exc:
            logger.error(
                f"[TIP] Could not resolve driver user_id for ride {ride_id}: {exc}",
                exc_info=True,
            )

    if driver_user_id:
        rider = await db_supabase.get_user_by_id(ride["rider_id"]) or {}
        # PIPEDA (C5): first name only to the driver (WS); never the surname.
        rider_name = first_name_only(rider, "Your rider")
        payload = {
            "type": "tip_received",
            "ride_id": str(ride_id),
            "amount": _money_str(tip_amount),
            "new_total": _money_str(new_tip),
            "rider_name": rider_name,
        }
        try:
            await manager.send_personal_message(payload, f"driver_{driver_user_id}")
        except Exception as exc:
            logger.warning(f"[TIP] WS notify driver {driver_user_id} failed: {exc}")
        _push_in_background(
            driver_user_id,
            "You got a tip! 💸",
            # PIPEDA (C5): no rider name in the push body (cleartext to Google).
            f"You received a ${tip_amount:.2f} tip",
            data={
                "type": "tip_received",
                "ride_id": str(ride_id),
                "amount": f"{tip_amount:.2f}",
            },
            _ctx=f"[TIP] driver {driver_user_id}",
        )

    return {"success": True, "tip_amount": _money_str(new_tip)}


class ProcessPaymentRequest(BaseModel):
    tip_amount: Decimal = Field(default=Decimal("0"), ge=0, le=500)
    # In-app "Change Card" escape: when set, charge THIS card (fresh charge on
    # a card the rider picked after a decline / no-card failure) instead of the
    # booking-time card or hold. Card rides only; ignored for wallet/corporate.
    payment_method_id: Optional[str] = None


def _record_settlement_metrics(payment_method: str, result, duration_ms: float) -> None:
    """KPI: spinr_payment_settlement_total{method,outcome} + duration histogram.

    Outcome mapping: already_paid is split out from success because no money
    moved (idempotent replay) — counting it as success would mask retry storms
    behind a healthy-looking settlement rate.
    """
    method = {"wallet": "wallet", "company_allowance": "corporate"}.get(payment_method, "card")
    outcome = "already_paid" if result.already_paid else ("success" if result.success else "failed")
    _metric_inc("spinr_payment_settlement_total", {"method": method, "outcome": outcome})
    _metric_observe("spinr_payment_settlement_duration_ms", duration_ms, {"method": method})


@api_router.post("/{ride_id}/process-payment")
@payment_action_limit
async def process_payment(
    ride_id: str,
    req: ProcessPaymentRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Process payment for completed ride. Charges rider's card for fare + tip."""
    tip_amount = req.tip_amount

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized")

    _ride_status = ride.get("status", "")
    if _ride_status != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Ride is in status '{_ride_status}'; payment requires completed state.",
        )

    def _charged(r: dict) -> str:
        _g = r.get("grand_total")
        if _g is None:
            _g = r.get("total_fare", 0)
        return _money_str(_d(_g) + _d(r.get("tip_amount", 0) or 0))

    _pstatus = ride.get("payment_status")
    _pmethod = (ride.get("payment_method") or "card").lower()

    if _pstatus == "paid":
        logger.info(f"[PAYMENT] Ride {ride_id} already paid — skipping duplicate charge")
        return {"success": True, "charged_amount": _charged(ride), "already_paid": True}

    # 'processing' for a CARD/corporate ride may mean the charge was CAPTURED
    # with the DB write lost (settle_card's captured-but-unconfirmed path), so
    # reporting already-paid avoids a double charge; the reconcile/retry loop
    # reconciles the truth. WALLET 'processing' is different: settlement is a
    # single atomic RPC (wallet_pay_for_ride debits AND marks paid in one txn,
    # migration 50/107), so a wallet ride still at 'processing' was provably
    # NEVER debited — it's a crashed/timed-out settle that must be RE-DRIVEN,
    # not reported as paid. The RPC is idempotent (107: no-op if already paid),
    # so re-driving cannot double-charge even under a concurrent retry.
    if _pstatus == "processing" and _pmethod != "wallet":
        logger.info(f"[PAYMENT] Ride {ride_id} processing ({_pmethod}) — skipping duplicate charge")
        return {"success": True, "charged_amount": _charged(ride), "already_paid": True}

    # An admin has emailed (or is creating) a payable Stripe invoice for this
    # ride — collection has moved to the hosted invoice (settled by the
    # invoice.paid webhook). Charging in-app while ANY invoice claim is on the
    # row would collect a second time, and the later invoice.paid would see the
    # ride already paid and skip (no refund of the extra). Block on any non-null
    # value: a finalized invoice (in_*) and a 'pending:' creation claim alike.
    # We never unblock by age here — a stuck claim is recovered admin-side (which
    # creates invoices crash-safely), not by silently re-opening in-app charging.
    if ride.get("stripe_invoice_id"):
        # Structured code so the rider app shows the "pay via emailed invoice"
        # instruction instead of the generic Change Card/Support alert (which would
        # just loop back into this same guard on every retry).
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invoice_issued",
                "message": "An invoice has been emailed for this ride. Please pay using the link in your email.",
            },
        )

    # Validate the tip BEFORE the atomic claim — raising after the claim would
    # leave payment_status stuck at 'processing' with no charge ever attempted.
    if tip_amount < 0:
        raise HTTPException(status_code=400, detail="Tip amount cannot be negative")
    if tip_amount > 500:
        raise HTTPException(status_code=400, detail="Tip amount exceeds maximum ($500)")

    # Atomic claim. 'pending' is the normal first payment; 'failed' lets a retry
    # after a decline re-drive; for WALLET we also re-claim 'processing' so a
    # crashed/stuck settlement is recovered on the rider's next attempt (the
    # idempotent RPC is the double-charge guard). We do NOT gate this on
    # updated_at — /rate bumps updated_at immediately before /process-payment,
    # so an age filter never fires and the ride stays stuck forever.
    _wallet_redrive_lock_key: str | None = None
    _redis_del = None
    _claim_states = ["pending", "failed"]
    if _pmethod == "wallet" and _pstatus == "processing":
        # Recovery re-drive of a stuck wallet 'processing' ride.
        #
        # DO NOT add 'processing' to _claim_states unconditionally — that makes
        # the claim non-exclusive. A double-tap on a first payment (ride at
        # 'pending') would have Call B see 'processing' after Call A commits and
        # still match the WHERE clause, so both calls would enter settlement.
        #
        # Instead: gate the 'processing' claim state behind a Redis NX lock so
        # exactly one re-drive holds the gate. Only after acquiring the lock is
        # 'processing' added to _claim_states, making the DB update a no-op
        # marker (the row is already 'processing') rather than an exclusive
        # transition. Subsequent concurrent re-drives get 409 before the DB.
        try:
            from ..utils.redis_client import redis_delete as _redis_del
            from ..utils.redis_client import redis_set_nx as _redis_nx
        except ImportError:
            from utils.redis_client import redis_delete as _redis_del  # type: ignore
            from utils.redis_client import redis_set_nx as _redis_nx  # type: ignore
        _wallet_redrive_lock_key = f"spinr:wallet_settle:{ride_id}"
        if not await _redis_nx(_wallet_redrive_lock_key, "1", 30):
            raise HTTPException(
                status_code=409,
                detail="Payment retry already in progress. Please try again in a moment.",
            )
        _claim_states.append("processing")  # only added after the lock is held
    guard_row = await db_supabase.update_one(
        "rides",
        # stripe_invoice_id=NULL is asserted atomically (mirrors admin send-invoice
        # asserting payment_status NOT IN processing/paid/...): if an admin claimed
        # the ride for an invoice between our pre-read invoice-guard above and this
        # claim, 0 rows match and the rider is not charged in-app alongside it.
        {"id": ride_id, "payment_status": {"$in": _claim_states}, "stripe_invoice_id": None},
        {
            "payment_status": "processing",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if guard_row is not None and _pstatus == "processing":
        logger.warning(f"[PAYMENT] Re-driving stuck wallet 'processing' ride {ride_id} for re-settlement")

    if guard_row is None:
        # Couldn't claim. Re-read the real state — only report already-paid when
        # the ride is genuinely paid (or a captured-card 'processing'); a wallet
        # state we couldn't claim returns a retryable 409 rather than a false
        # "Paid".
        fresh = await db_supabase.get_ride(ride_id) or ride
        if fresh.get("payment_status") == "paid":
            return {"success": True, "already_paid": True, "charged_amount": _charged(fresh)}
        if fresh.get("payment_status") == "processing" and _pmethod != "wallet":
            return {"success": True, "already_paid": True, "charged_amount": _charged(fresh)}
        raise HTTPException(status_code=409, detail="Payment is processing; please retry in a moment.")

    def _q(v) -> Decimal:
        return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Explicit None check, not truthiness: a legitimate $0 grand_total (comp /
    # fully-covered ride) must charge $0, not fall through to a non-zero
    # total_fare and overcharge. Fall back to total_fare only when grand_total
    # was never written (legacy rides predating the column).
    _grand = ride.get("grand_total")
    if _grand is None:
        _grand = ride.get("total_fare", 0)
    tip_rounded = _q(tip_amount)  # canonical 2dp value passed to all settle fns
    total_charge = _q(_grand) + tip_rounded
    payment_method = (ride.get("payment_method") or "card").lower()

    # _tip_db_update carries only the fare_breakdown_snapshot (cosmetic display
    # update, written best-effort after settlement). tip_amount and
    # driver_earnings are written atomically:
    #   - wallet:    inside wallet_pay_for_ride RPC (migration 110)
    #   - card/corp: inside settle_card / settle_corporate via _tip_ride_update
    # The in-memory ride dict is still updated so the receipt email sees the
    # correct totals without a DB re-fetch.
    _tip_db_update: dict = {}
    if tip_rounded > 0:
        tip_d = tip_rounded
        existing_tip = _d(ride.get("tip_amount") or 0)
        tip_delta = tip_d - existing_tip
        snapshot = ride.get("fare_breakdown_snapshot")
        if snapshot and isinstance(snapshot, dict) and snapshot.get("lines") is not None:
            updated_lines = [ln for ln in snapshot["lines"] if ln.get("type") != "tip"]
            updated_lines.append({"label": "Tip", "amount": _f(tip_d), "type": "tip"})
            snapshot["lines"] = updated_lines
            snapshot["grand_total"] = _sum_fare_breakdown(updated_lines)
            _tip_db_update["fare_breakdown_snapshot"] = snapshot
        ride["tip_amount"] = _f(tip_d)
        if tip_delta > 0:
            ride["driver_earnings"] = _f(_round(_d(ride.get("driver_earnings") or 0) + tip_delta))

    _snap = ride.get("fare_breakdown_snapshot")
    _snap_lines = (_snap.get("lines") if isinstance(_snap, dict) else None) if _snap else None

    _settle_started = _time_mod.monotonic()
    if payment_method == "wallet":
        result = await settle_wallet(
            ride,
            ride_id,
            current_user["id"],
            total_charge,
            tip_rounded,
            fare_breakdown=_snap_lines or _build_fare_breakdown(ride),
        )
    elif payment_method == "company_allowance":
        result = await settle_corporate(ride, ride_id, total_charge, tip_rounded)
    else:
        result = await settle_card(
            ride,
            ride_id,
            current_user["id"],
            total_charge,
            tip_rounded,
            payment_method_id_override=req.payment_method_id,
        )

    _record_settlement_metrics(payment_method, result, (_time_mod.monotonic() - _settle_started) * 1000.0)

    if not result.success:
        detail = result.error or "Payment failed"
        if result.error_code:
            detail = {"code": result.error_code, "message": result.error}
            if result.decline_code:
                detail["decline_code"] = result.decline_code
            if result.extra:
                detail.update(result.extra)
        raise HTTPException(status_code=result.status_code, detail=detail)

    # Release the wallet re-drive lock now that settlement is complete so any
    # subsequent legitimate retry sees the paid status immediately rather than
    # waiting 30 s for the TTL to expire.
    if _wallet_redrive_lock_key:
        try:
            await _redis_del(_wallet_redrive_lock_key)
        except Exception as _lock_err:
            logger.debug("wallet redrive lock release failed (TTL will expire): %s", _lock_err)

    # Skip tip persistence and receipt on already_paid: no money moved, so we
    # must not mutate tip_amount/driver_earnings in the DB or send a duplicate
    # receipt. The ledger write was also skipped by settle_wallet on this path.
    email_sent = False
    if not result.already_paid:
        if _tip_db_update:
            try:
                await db_supabase.update_ride(ride_id, _tip_db_update)
            except Exception as _snap_err:
                logger.error(
                    "[PAYMENT] fare_breakdown_snapshot write failed for ride %s — "
                    "payment succeeded, snapshot will be stale: %s",
                    ride_id,
                    _snap_err,
                )
        # Receipt email backgrounded off the payment path (<1s settlement
        # SLA): send_ride_receipt logs and swallows its own failures, and no
        # client surface reads the delivery outcome. email_sent now means
        # "queued" (field kept for API-shape compatibility; the explicit
        # resend endpoint still awaits delivery and reports it honestly).
        spawn(send_ride_receipt(ride, current_user["id"], tip_rounded))
        email_sent = True
    return {
        "success": True,
        "charged_amount": _money_str(result.charged_amount),
        "email_sent": email_sent,
    }


# ============================================================
# GAP FIX: Share Ride Link (Uber/Lyft standard feature)
# ============================================================


@api_router.get("/{ride_id}/share")
async def get_share_trip_link(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Generate a shareable trip tracking link for safety contacts (like Uber's 'Share My Trip')."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to share this ride")

    if ride.get("status") in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")

    # Generate or reuse a share token (with creation timestamp for expiry)
    share_token = ride.get("shared_trip_token")
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await db_supabase.update_ride(ride_id, {"shared_trip_token": share_token})

    # Clean customer-facing link: {tracking-domain}/{token}. The tracking
    # domain (e.g. track.spinr.ca) rewrites /{token} → /track/{token} server-side.
    share_url = f"{_settings.TRACKING_BASE_URL}/{share_token}"

    return {
        "success": True,
        "share_token": share_token,
        "share_url": share_url,
        "ride_id": ride_id,
    }


class ShareTripWithContactRequest(BaseModel):
    contact_name: str
    contact_phone: str


@api_router.post("/{ride_id}/share")
@api_rate_limit
async def share_trip_with_contact(
    ride_id: str,
    body: ShareTripWithContactRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Share trip with a specific contact and send them a notification."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Cannot share a completed or cancelled ride")

    # Get or create share token
    share_token = ride.get("shared_trip_token")
    if not share_token:
        share_token = secrets.token_urlsafe(32)
        await db.update_one(
            "rides",
            {"id": ride_id},
            {
                "$set": {
                    "shared_trip_token": share_token,
                    "shared_trip_token_created_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

    # Record the contact in shared_with list
    shared_with = ride.get("shared_with") or []
    contact_entry = {
        "name": body.contact_name,
        "phone": body.contact_phone,
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    # Avoid duplicates by phone
    if not any(c.get("phone") == body.contact_phone for c in shared_with):
        shared_with.append(contact_entry)
        await db.update_one(
            "rides",
            {"id": ride_id},
            {"$set": {"shared_with": shared_with}},
        )

    # Clean customer-facing link: {tracking-domain}/{token}. The tracking
    # domain (e.g. track.spinr.ca) rewrites /{token} → /track/{token} server-side.
    share_url = f"{_settings.TRACKING_BASE_URL}/{share_token}"

    # Send push notification to contact if they're a registered user
    contact_user = await db.find_one("users", {"phone": body.contact_phone})
    if contact_user:
        rider = await db.find_one("users", {"id": current_user["id"]})
        # PIPEDA (C5): the rider's FIRST name only to their chosen contact —
        # never the legal surname in a cleartext push title.
        rider_name = first_name_only(rider, "Someone")
        _push_in_background(
            contact_user["id"],
            f"{rider_name} is sharing their ride with you",
            # PIPEDA (C5): no exact pickup/dropoff addresses in the push body —
            # FCM is cleartext in the device tray and stored in Google/US infra.
            # The contact taps through to live tracking via the share token; the
            # body needs no address detail.
            "Tap to follow their live trip location.",
            data={
                "type": "trip_shared",
                "share_token": share_token,
                "ride_id": ride_id,
            },
            _ctx=f"[SHARE] contact {contact_user['id']}",
        )

    return {
        "success": True,
        "share_token": share_token,
        "share_url": share_url,
        "contact_notified": contact_user is not None,
        "shared_with": shared_with,
    }


@api_router.get("/{ride_id}/shared-contacts")
async def get_shared_contacts(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get list of contacts this ride has been shared with."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"contacts": ride.get("shared_with") or []}


@api_router.get("/track/{share_token}")
async def track_shared_ride(share_token: str):
    """Public endpoint - Get ride status via share token (no auth required)."""
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("rides", {"shared_trip_token": share_token}, limit=1)
    )
    if not ride:
        raise HTTPException(status_code=404, detail="Shared ride not found or link expired")

    # Expire share tokens after 24 hours
    token_created = ride.get("shared_trip_token_created_at")
    if token_created:
        from datetime import timedelta

        try:
            created_dt = datetime.fromisoformat(token_created) if isinstance(token_created, str) else token_created
            if datetime.now(timezone.utc) - created_dt > timedelta(hours=24):
                raise HTTPException(status_code=404, detail="Share link has expired")
        except (ValueError, TypeError):
            pass  # Malformed timestamp — allow access but log
            logger.error(f"Malformed shared_trip_token_created_at for ride {ride.get('id')}")

    if ride.get("status") in RideStatus.terminal_statuses():
        return {
            "status": ride.get("status"),
            "message": "This ride has ended.",
            "pickup_address": ride.get("pickup_address"),
            "dropoff_address": ride.get("dropoff_address"),
        }

    # Get driver location for live tracking — surface what a safety contact
    # legitimately needs to see the live map (driver coords + plate to
    # identify the car) without leaking PII (phone, email, license number).
    driver_info = None
    eta_minutes: Optional[int] = None
    if ride.get("driver_id"):
        driver = await db_supabase.get_driver_by_id(ride["driver_id"])
        if driver:
            _drv_user = await db_supabase.get_user_by_id(driver.get("user_id"))
            driver_info = {
                "name": driver.get("name", "Driver"),
                "lat": driver.get("lat"),
                "lng": driver.get("lng"),
                "vehicle_make": driver.get("vehicle_make"),
                "vehicle_model": driver.get("vehicle_model"),
                "vehicle_color": driver.get("vehicle_color"),
                "vehicle_year": driver.get("vehicle_year"),
                "license_plate": driver.get("license_plate"),
                "rating": driver.get("rating"),
                # users.profile_image, shown to riders only once admin-approved.
                "photo_url": _rider_visible_photo(_drv_user),
            }
            # Cheap ETA: straight-line driver→dropoff at 30 km/h city speed.
            # Same formula used at /rides/estimate so the number stays consistent.
            d_lat, d_lng = driver.get("lat"), driver.get("lng")
            tgt_lat, tgt_lng = ride.get("dropoff_lat"), ride.get("dropoff_lng")
            if d_lat is not None and d_lng is not None and tgt_lat is not None and tgt_lng is not None:
                try:
                    km = calculate_distance(d_lat, d_lng, tgt_lat, tgt_lng)
                    eta_minutes = max(1, int(km / 30 * 60) + 1)
                except Exception:
                    eta_minutes = None

    return {
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "pickup_lat": ride.get("pickup_lat"),
        "pickup_lng": ride.get("pickup_lng"),
        "dropoff_lat": ride.get("dropoff_lat"),
        "dropoff_lng": ride.get("dropoff_lng"),
        "ride_code": ride.get("ride_code"),
        "eta_minutes": eta_minutes,
        "driver": driver_info,
    }


@api_router.post("/{ride_id}/rate")
@ride_rating_limit
async def rate_driver(
    ride_id: str,
    rating_data: RideRatingRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider rates the driver"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=404, detail="Ride not found or unauthorized")

    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Ride must be completed before rating")

    # Save rating using existing columns (rider_rating = rating rider gave the driver)
    await db_supabase.update_ride(
        ride_id,
        {
            "rider_rating": rating_data.rating,
            "rider_comment": rating_data.comment or "",
            "updated_at": datetime.now(timezone.utc),
        },
    )

    driver_id = ride.get("driver_id")
    if not driver_id:
        return {"success": True}

    if rating_data.tip_amount > 0:
        # Decimal-safe accumulation: float addition drifts when summing existing
        # tip + this tip (e.g. 0.1 + 0.2 == 0.30000000000000004), and would
        # corrupt driver_earnings on rides that receive multiple tips.
        # Legacy rides may store NULL for tip_amount/driver_earnings rather
        # than 0. ``ride.get(k, 0)`` returns the literal None in that case
        # (the key exists, the value is None) so coerce explicitly.
        tip_delta = _d(rating_data.tip_amount)
        new_tip = _round(_d(ride.get("tip_amount") or 0) + tip_delta)
        new_driver_earnings = _round(_d(ride.get("driver_earnings") or 0) + tip_delta)
        _rate_update: dict = {"tip_amount": _f(new_tip), "driver_earnings": _f(new_driver_earnings)}
        des = ride.get("driver_earnings_snapshot")
        if des and isinstance(des, dict):
            des.update(
                build_earnings_snapshot(
                    fare=des.get("fare") or 0,
                    tip=new_tip,
                    incentive=des.get("incentive") or 0,
                    tax=des.get("tax") or 0,
                    cancel_fee=des.get("cancel_fee") or 0,
                )
            )
            _rate_update["driver_earnings_snapshot"] = des
        await db_supabase.update_ride(ride_id, _rate_update)

    # Aggregate driver rating using rolling average to avoid O(n) ride fetch.
    driver = await db_supabase.get_driver_by_id(driver_id)
    if driver:
        old_count = int(driver.get("total_ratings") or 0)
        old_avg = Decimal(str(driver.get("rating") or 0))
        new_count = old_count + 1
        new_avg = ((old_avg * old_count + Decimal(str(rating_data.rating))) / new_count).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        await db_supabase.update_one(
            "drivers",
            {"id": driver_id},
            {
                "rating": float(new_avg),
                "total_ratings": new_count,
            },
        )

    # G19: Notify the driver that they received a rating. This creates a
    # feedback loop — drivers see their rating improve/decline in real time
    # instead of only noticing on their next profile check.
    if driver and driver.get("user_id") and rating_data.rating:
        stars = "⭐" * int(rating_data.rating)
        tip_note = f" + ${rating_data.tip_amount:.2f} tip!" if rating_data.tip_amount > 0 else ""
        _push_in_background(
            driver["user_id"],
            f"New Rating: {stars}",
            f"A rider rated you {rating_data.rating}/5{tip_note}",
            {
                "type": "rating_received",
                "rating": str(rating_data.rating),
                "ride_id": ride_id,
            },
            _ctx=f"[RATING] driver {driver['user_id']}",
        )

    spawn(
        log_user_action(
            current_user,
            "driver_rated",
            "rides",
            ride_id,
            {"rating": str(rating_data.rating), "driver_id": driver_id},
        )
    )
    return {"success": True}


class LiveActivityRegisterRequest(BaseModel):
    """Rider app registers its live-activity push token for a ride."""

    platform: str = Field(..., pattern="^(ios|android)$")
    # Real tokens are small (APNs ≤100, FCM ≤256 chars); 512 is generous headroom
    # and bounds abuse. Mirrored by a CHECK on the column (migration 195). The
    # charset accepts iOS ActivityKit hex AND Android FCM (base64url + ':') while
    # blocking path/structural chars (the iOS token is later interpolated into the
    # APNs URL path — apns_client also re-validates).
    push_token: str = Field(..., min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_.:-]+$")


@api_router.post("/{ride_id}/live-activity/register")
@ride_action_limit
async def register_live_activity(
    ride_id: str,
    body: LiveActivityRegisterRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Store the rider app's live-activity push token for a ride.

    Called when the rider app starts a Live Activity (iOS) / ongoing
    notification (Android) at ``driver_accepted``. The backend reads this token
    on each ride-state transition to push an update (Phase 3 — Phase 1 only
    logs). Upsert: one token per ``(ride_id, platform)``; re-registration
    (reinstall / new activity) replaces it and clears any prior ``ended_at``.
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    now = datetime.now(timezone.utc).isoformat()
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "ride_live_activities",
            {"ride_id": ride_id, "platform": body.platform},
            limit=1,
        )
    )
    if existing:
        await db_supabase.update_one(
            "ride_live_activities",
            {"id": existing["id"]},
            {"push_token": body.push_token, "ended_at": None, "updated_at": now},
        )
        activity_id = existing["id"]
    else:
        activity_id = str(uuid.uuid4())
        await db_supabase.insert_one(
            "ride_live_activities",
            {
                "id": activity_id,
                "ride_id": ride_id,
                "rider_id": current_user["id"],
                "platform": body.platform,
                "push_token": body.push_token,
                "started_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
    return {"success": True, "activity_id": activity_id}


@api_router.post("/{ride_id}/cancel")
@cancel_ride_limit
async def cancel_ride_rider(
    ride_id: str,
    reason: str = Query(""),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider cancels the ride. Optional `reason` is captured for the admin
    Cancellation card (preset reason or free-text note from the rider app)."""
    try:
        from ..logging_utils import diag_logger  # type: ignore
    except ImportError:
        from logging_utils import diag_logger  # type: ignore

    diag_logger.info(f"[CANCEL] called ride_id={ride_id} user_id={current_user.get('id')}")

    # Prefer the reason from the JSON body — free-text notes must not ride in the
    # URL query string (proxy/access logs, crash breadcrumbs leak it). Fall back
    # to the legacy ?reason= for older app builds.
    _body_reason = None
    if request is not None:
        try:
            _b = await request.json()
            if isinstance(_b, dict):
                _body_reason = _b.get("reason")
        except Exception:
            _body_reason = None
    reason = (str(_body_reason).strip() if _body_reason else "") or reason

    _cancellable_states = (
        "requested",
        RideStatus.SEARCHING,
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        "en_route",
        RideStatus.DRIVER_ARRIVED,
    )
    ride = await _require_ride_in_state_rider(ride_id, current_user["id"], _cancellable_states)
    diag_logger.info(
        f"[CANCEL] entry ride_id={ride_id} pre_status={ride.get('status')} driver_id={ride.get('driver_id')}"
    )

    # Atomically claim the cancel BEFORE charging any fee. _require_ride_in_state_rider
    # only read+validated the status; in the window before the write the driver could
    # call verify-otp/start and flip the ride to in_progress. A non-atomic cancel would
    # then overwrite in_progress -> cancelled (violating "never cancel after trip start")
    # AND charge a cancellation fee on a ride that actually began. The $in guard matches
    # zero rows once the ride has left the pre-trip states -> 409, nothing charged.
    _cancel_now = datetime.now(timezone.utc)
    _cancel_claim = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "status": {"$in": list(_cancellable_states)}},
        {"status": RideStatus.CANCELLED, "cancelled_at": _cancel_now, "updated_at": _cancel_now},
    )
    if _cancel_claim is None:
        diag_logger.info(f"[CANCEL] claim rejected ride_id={ride_id} — ride left pre-trip state")
        raise HTTPException(
            status_code=409,
            detail="Ride can no longer be cancelled (it has started or already ended)",
        )

    driver_id = ride.get("driver_id")

    # The cancel is already persisted by the atomic claim above, so the
    # assigned driver MUST be released, transitioned back to Period 1, and
    # notified regardless of what happens while computing or charging the fee.
    # EVERYTHING from the settings/area lookup through the fee writes is
    # best-effort after the claim: the settings read, the service-area read,
    # the fee calculation, and the wallet/driver-payout writes can each raise,
    # and if any does we must not exit before the set_driver_available /
    # insurance / notification cleanup below — that would strand the driver as
    # unavailable and uninformed on a ride that is already cancelled. Surface
    # failures loudly (error + traceback, per repo policy) for reconciliation,
    # then fall through to driver cleanup. charged_* default to 0 so a failed
    # fee computation records no fee rather than a stale/partial one.
    charged_admin = charged_driver = Decimal("0")
    cancel_fee_payment_status: Optional[str] = None
    cancel_fee_payment_intent_id: Optional[str] = None
    cancel_fee_charge_attempted = False
    try:
        settings = await get_app_settings()
        area = None
        if ride.get("service_area_id"):
            area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
        charged_admin, charged_driver = calculate_cancellation_fee(ride, settings, area)
        total_cancel_fee = _round(charged_admin + charged_driver)

        # Charge the rider the cancellation fee before paying the driver.
        if total_cancel_fee > 0:
            payment_method = (ride.get("payment_method") or "card").lower()
            if payment_method == "wallet":
                rider_wallet = await db_supabase.find_one("wallets", {"user_id": current_user["id"]})
                if rider_wallet:
                    old_balance = _round(_d(rider_wallet.get("balance", 0)))
                    new_balance = max(_round(old_balance - total_cancel_fee), Decimal("0"))
                    actual_charge = _round(old_balance - new_balance)
                    if actual_charge > 0:
                        await db_supabase.update_one(
                            "wallets",
                            {"id": rider_wallet["id"]},
                            {
                                "balance": _f(new_balance),
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        await db_supabase.insert_one(
                            "wallet_transactions",
                            {
                                "id": str(uuid.uuid4()),
                                "wallet_id": rider_wallet["id"],
                                "user_id": current_user["id"],
                                "type": "cancellation_fee",
                                "amount": -_f(actual_charge),
                                "balance_after": _f(new_balance),
                                "reference_id": ride_id,
                                "description": f"Cancellation fee for ride {ride_id[:8]}",
                                "metadata": {"ride_id": ride_id},
                                "created_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
            elif payment_method == "card":
                # Mirrors settle_card's payment-method resolution: a card pinned
                # to the ride wins (e.g. the in-app "Change Card" escape), else
                # the rider's saved default. Company-allowance / corporate-paid
                # rides are intentionally excluded — that fee belongs on the
                # corporate wallet ledger, not a personal Stripe card, and isn't
                # wired up here.
                rider_user = await db_supabase.get_user_by_id(current_user["id"])
                stripe_customer_id = (rider_user or {}).get("stripe_customer_id")
                payment_method_id = ride.get("payment_method_id") or (rider_user or {}).get("default_payment_method")
                outcome = await charge_ancillary_fee(
                    ride=ride,
                    rider_id=current_user["id"],
                    amount=total_cancel_fee,
                    payment_method_id=payment_method_id,
                    stripe_customer_id=stripe_customer_id,
                    fee_type="cancellation_fee",
                )
                if outcome.status == "unconfigured":
                    # Stripe isn't wired up (dev/test) — no Stripe call was made
                    # at all, so leave payment_status/payment_intent_id untouched
                    # rather than mislabel a config gap as a decline.
                    logger.error(
                        "[CANCEL] cancellation fee charge skipped (stripe unconfigured) ride=%s amount=%s",
                        ride_id,
                        total_cancel_fee,
                    )
                else:
                    # A real charge attempt happened (success or decline) — always
                    # overwrite both fields together, even to None on a decline.
                    # Leaving a stale booking-time hold's payment_intent_id in
                    # place next to a fresh payment_status="failed" would make
                    # payment_retry.py's blind PI-status scan retry the wrong
                    # PaymentIntent. Mirrors settle_card's declined-branch write.
                    cancel_fee_charge_attempted = True
                    cancel_fee_payment_intent_id = outcome.payment_intent_id
                    if outcome.status == "succeeded":
                        cancel_fee_payment_status = "paid"
                        try:
                            await db_supabase.insert_one(
                                "financial_events",
                                {
                                    "event_type": "stripe_charge",
                                    "user_id": current_user["id"],
                                    "ride_id": ride_id,
                                    "delta_cents": int(_round(total_cancel_fee * Decimal("100"))),
                                    "ref": outcome.payment_intent_id,
                                    "metadata": {"source": "cancellation_fee", "driver_id": driver_id or ""},
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                        except Exception:
                            # Never let a ledger-write failure block the cancel or
                            # mask that the card WAS actually charged — log loudly
                            # so ops can backfill the reconciliation row.
                            logger.error(
                                "[CANCEL] financial_events write failed for cancellation fee "
                                "ride=%s pi=%s amount=%s — charge succeeded but is unrecorded",
                                ride_id,
                                outcome.payment_intent_id,
                                total_cancel_fee,
                                exc_info=True,
                            )
                    else:
                        cancel_fee_payment_status = "failed"
                        logger.error(
                            "[CANCEL] cancellation fee card charge failed ride=%s rider=%s amount=%s "
                            "status=%s error=%s",
                            ride_id,
                            current_user["id"],
                            total_cancel_fee,
                            outcome.status,
                            outcome.error_message,
                        )

        if driver_id and charged_driver > 0:
            await pay_driver_cancellation_fee(
                ride_id=ride_id,
                driver_id=driver_id,
                fee=charged_driver,
                actor_user_id=current_user["id"],
                ride_status_at_cancel=ride.get("status"),
            )
    except Exception as _fee_exc:
        logger.error(
            "[CANCEL] cancellation-fee write failed after the cancel was "
            "persisted for ride %s; releasing the driver anyway — fee needs "
            "reconciliation: %s",
            ride_id,
            getattr(_fee_exc, "details", {}).get("original", _fee_exc) if hasattr(_fee_exc, "details") else _fee_exc,
            exc_info=True,
        )

    _now = datetime.now(timezone.utc)
    _base_update = {
        "status": RideStatus.CANCELLED,
        "cancelled_at": _now,
        "cancellation_fee_admin": _f(charged_admin),
        "cancellation_fee_driver": _f(charged_driver),
        "updated_at": _now,
    }
    if cancel_fee_charge_attempted:
        # Overwrite both together, even payment_intent_id -> None on a decline —
        # never leave a stale booking-time hold's PI paired with a fresh status.
        _base_update["payment_status"] = cancel_fee_payment_status
        _base_update["payment_intent_id"] = cancel_fee_payment_intent_id
    # Migration 38 — attribution. Fall back to the legacy payload on
    # PGRST204 so the rider's cancel button never 503s if the column
    # isn't in prod yet.
    _reason = (reason or "").strip() or None
    try:
        await db_supabase.update_ride(
            ride_id,
            {
                **_base_update,
                "cancelled_by": "rider",
                "cancellation_type": "rider_cancel",
                "cancellation_reason": _reason,
            },
        )
    except Exception as _col_exc:
        logger.warning(f"[CANCEL] attribution write failed ({_col_exc}); retrying minimal")
        await db_supabase.update_ride(ride_id, _base_update)

    # Verify the cancel actually landed in the database. Same class of
    # silent-failure we hit with go-online and accept: the update_one wrapper
    # returns None when zero rows are affected and the handler would
    # otherwise return {success: true} while the ride is still in its prior
    # state — the rider then reloads and sees the ride still "searching".
    try:
        verify_ride = await db_supabase.get_ride(ride_id)
    except Exception as e:
        verify_ride = None
        diag_logger.info(f"[CANCEL] verify re-read failed: {e}")

    diag_logger.info(
        f"[CANCEL] post-update ride_id={ride_id} "
        f"post_status={verify_ride.get('status') if verify_ride else 'ROW_GONE'} "
        f"post_cancelled_at={verify_ride.get('cancelled_at') if verify_ride else 'ROW_GONE'}"
    )

    if not verify_ride or verify_ride.get("status") != RideStatus.CANCELLED:
        diag_logger.info(
            f"[CANCEL] SILENT NO-OP: ride_id={ride_id} did not flip to "
            f"'cancelled'. Likely a missing column in the rides table "
            f"(e.g. cancelled_at / cancellation_fee_admin / "
            f"cancellation_fee_driver) or a wrapper dispatching the "
            f"update to the wrong path. Rider will see the ride as still "
            f"active after reload."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Cancel did not persist. Backend write returned successfully "
                "but the ride row is unchanged. Check backend logs for "
                "[CANCEL] lines."
            ),
        )

    if driver_id:
        await db_supabase.set_driver_available(driver_id, True)
        # M-5: SGI insurance period audit — rider-side cancel after the
        # driver was assigned releases the driver back to period 1. If
        # the ride had no driver_id we never left period 1, so no row.
        await record_period_transition(driver_id, 1)

        # Notify driver
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {
                    "type": "ride_cancelled",
                    "ride_id": ride_id,
                    "reason": "Rider cancelled",
                },
                f"driver_{driver['user_id']}",
            )

    # Batch dispatch: cancel pending ride_offers and notify those drivers.
    # With batch dispatch driver_id is NOT set on the ride row — offers
    # live in ride_offers. Without this block, drivers keep showing a
    # stale offer panel for a ride the rider already cancelled.
    try:
        pending_offers = await db_supabase.run_sync(
            lambda: (
                db_supabase.supabase.table("ride_offers")
                .select("driver_id")
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )
        if pending_offers.data:
            _cancel_now = datetime.now(timezone.utc).isoformat()
            await db_supabase.run_sync(
                lambda: (
                    db_supabase.supabase.table("ride_offers")
                    .update({"status": "cancelled", "responded_at": _cancel_now})
                    .eq("ride_id", ride_id)
                    .eq("status", "pending")
                    .execute()
                )
            )
            for offer_row in pending_offers.data:
                _offer_did = offer_row["driver_id"]
                await db_supabase.set_driver_available(_offer_did, True)
                try:
                    _drv = await db_supabase.get_driver_by_id(_offer_did)
                    _uid = (_drv or {}).get("user_id")
                    if _uid:
                        await manager.send_personal_message(
                            {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
                            f"driver_{_uid}",
                        )
                except Exception as _e:
                    logger.warning(f"[CANCEL] failed to notify batch-offer driver {_offer_did}: {_e}")
    except Exception as _batch_exc:
        logger.error(f"[CANCEL] batch offer cleanup failed for ride {ride_id}: {_batch_exc}", exc_info=True)

    # Notify the rider's own connection — broadcast_ride_status only fans
    # out to the rider when rider_id is passed, but an explicit message
    # ensures clearRide() fires immediately in useRiderSocket without
    # waiting for the next poll cycle.
    await manager.send_personal_message(
        {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"},
        f"rider_{current_user['id']}",
    )
    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=current_user["id"],
        reason="rider_cancelled",
    )
    # End the rider's live activity (Lock Screen / ongoing notification).
    spawn(send_live_activity_update({"id": ride_id, "status": RideStatus.CANCELLED}, EVENT_END))
    try:
        await manager.broadcast_to_admins({"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"rider cancel admin broadcast failed: {_exc}")

    spawn(
        log_user_action(
            current_user,
            "ride_cancelled",
            "rides",
            ride_id,
            {
                "reason": "rider_cancelled",
                "cancellation_fee": str(charged_admin + charged_driver),
            },
        )
    )
    return {"success": True, "cancellation_fee": charged_admin + charged_driver}


# ── Mid-Trip Stop Editing ─────────────────────────────────────────────


class AddStopMidTripRequest(BaseModel):
    address: str
    lat: float
    lng: float
    position: Optional[int] = None  # Insert at this index; None = append


@api_router.post("/{ride_id}/stops")
@ride_action_limit
async def add_stop_mid_trip(
    ride_id: str,
    req: AddStopMidTripRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Add a stop to an active ride mid-trip."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in (
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
        RideStatus.IN_PROGRESS,
    ):
        raise HTTPException(status_code=400, detail="Can only edit stops on an active ride")

    stops = ride.get("stops") or []
    new_stop = {"address": req.address, "lat": req.lat, "lng": req.lng}

    if req.position is not None and 0 <= req.position <= len(stops):
        stops.insert(req.position, new_stop)
    else:
        stops.append(new_stop)

    fare_update = _reestimate_fare_for_stops(ride, stops)
    await db.update_one(
        "rides",
        {"id": ride_id},
        {
            "$set": {
                **fare_update,
                "stops": stops,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Notify driver via WebSocket
    if ride.get("driver_id"):
        driver = await db.find_one("drivers", {"id": ride["driver_id"]})
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {
                    "type": "stops_updated",
                    "ride_id": ride_id,
                    "stops": stops,
                    "estimated_fare": fare_update["estimated_fare"],
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


@api_router.delete("/{ride_id}/stops/{stop_index}")
@ride_action_limit
async def remove_stop_mid_trip(
    ride_id: str,
    stop_index: int,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Remove a stop from an active ride by index."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in (
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
        RideStatus.IN_PROGRESS,
    ):
        raise HTTPException(status_code=400, detail="Can only edit stops on an active ride")

    stops = ride.get("stops") or []
    if stop_index < 0 or stop_index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")

    stops.pop(stop_index)

    fare_update = _reestimate_fare_for_stops(ride, stops)
    await db.update_one(
        "rides",
        {"id": ride_id},
        {
            "$set": {
                **fare_update,
                "stops": stops,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Notify driver
    if ride.get("driver_id"):
        driver = await db.find_one("drivers", {"id": ride["driver_id"]})
        if driver and driver.get("user_id"):
            await manager.send_personal_message(
                {
                    "type": "stops_updated",
                    "ride_id": ride_id,
                    "stops": stops,
                    "estimated_fare": fare_update["estimated_fare"],
                },
                f"driver_{driver['user_id']}",
            )

    return {"success": True, "stops": stops, **fare_update}


class RideNotesUpdateRequest(BaseModel):
    notes: str = Field(default="", max_length=200)


@api_router.patch("/{ride_id}/notes")
@ride_action_limit
async def patch_ride_notes(
    ride_id: str,
    body: RideNotesUpdateRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider attaches/updates a free-text note for the driver.

    UX intent: the booking screen no longer collects this field — riders
    add the note from the ride-status screen *after* confirming, matching
    the Uber/Lyft pattern. Allowed while the driver is still en route;
    once the trip starts the note is locked (the driver is already
    on the way; tail-end edits are confusing).
    """
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    allowed = (
        RideStatus.SEARCHING,
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
    )
    if ride.get("status") not in allowed:
        raise HTTPException(
            status_code=409,
            detail="Notes can only be edited before pickup",
        )

    notes = (body.notes or "").strip() or None
    await db.update_one(
        "rides",
        {"id": ride_id},
        {
            "$set": {
                "rider_notes": notes,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        },
    )

    # Push to the assigned driver if one exists. Best-effort — a failed
    # WS send must not error out the API (the driver-app will reconcile
    # via its next ride fetch).
    if ride.get("driver_id"):
        try:
            driver = await db.find_one("drivers", {"id": ride["driver_id"]})
            if driver and driver.get("user_id"):
                await manager.send_personal_message(
                    {"type": "ride_notes_updated", "ride_id": ride_id, "notes": notes},
                    f"driver_{driver['user_id']}",
                )
        except Exception as e:
            logger.warning(f"[notes] WS push to driver failed for ride {ride_id}: {e}")

    return {"success": True, "notes": notes}


class EmergencyRequest(BaseModel):
    message: str = "Emergency assistance requested"
    latitude: Optional[float] = None
    longitude: Optional[float] = None


@api_router.post("/{ride_id}/emergency")
@ride_action_limit
async def trigger_emergency(
    ride_id: str,
    body: EmergencyRequest,
    request: Request = None,
    # SOS is never gated behind an auth refresh: a signature-valid token
    # that merely expired mid-trip still identifies the caller. Ride
    # membership is enforced below regardless.
    current_user: dict = Depends(get_current_user_allow_expired),
):
    """Trigger an emergency alert for a live ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Verify the user is part of the ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to trigger emergency for this ride")

    # Consolidated onto safety_incidents (migration 94). The legacy
    # `emergencies` table was never read by anything (no UI surfaced
    # it, no migration even created it), so this is a clean cutover
    # rather than a parallel write. After this, the rider SOS path
    # lives in the same admin Safety queue as the driver report and
    # the auto check-in escalation.
    now_iso = datetime.now(timezone.utc).isoformat()
    incident = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "reported_by_user_id": current_user["id"],
        "role": "rider" if is_rider else "driver",
        "category": "sos_button",
        "description": body.message or "Emergency assistance requested",
        "status": "open",
        "latitude": body.latitude,
        "longitude": body.longitude,
        "reported_at": now_iso,
        "created_at": now_iso,
    }

    await db_supabase.insert_one("safety_incidents", incident)

    # Notify admin dashboard via WebSocket. Keep the existing
    # emergency_alert event firing for backward compatibility with any
    # listener wired to that name; notify_safety_team below also emits
    # safety_incident_opened which the safety queue UI listens for.
    try:
        await manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
    except Exception as _exc:  # pragma: no cover - best effort
        logger.error(f"emergency_alert admin broadcast failed: {_exc}", exc_info=True)
    logger.critical(f"EMERGENCY ALERT TRIGGERED for ride {ride_id} by user {current_user['id']}")

    # Email the safety distribution list + CRITICAL log line.
    # No field-name bridging needed now that the incident row uses the
    # safety_incidents schema directly (was previously bridging from
    # the legacy `emergencies` shape which used `message` instead of
    # `description` and had no `category`).
    try:
        await notify_safety_team(incident)
    except Exception:  # pragma: no cover — best effort, never block the SMS path below
        logger.error(
            f"notify_safety_team failed for rider SOS ride={ride_id} incident={incident['id']}",
            exc_info=True,
        )

    # Notify emergency contacts via SMS (Twilio when configured, console log in dev)
    contacts_notified = 0
    try:
        sms_settings = await get_app_settings()
        contacts_rows = await db_supabase.get_rows("emergency_contacts", {"user_id": current_user["id"]}, limit=5)
        contacts = list(contacts_rows) if contacts_rows else []

        user = await db_supabase.get_user_by_id(current_user["id"])
        user_name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() if user else "A Spinr user"

        location_text = " Location shared with emergency services." if body.latitude and body.longitude else ""
        sms_body = (
            f"URGENT: {user_name} triggered an emergency alert during a Spinr ride."
            f"{location_text} Call them or emergency services immediately."
        )

        # Send all contact SMS concurrently. Serial sends stacked up to five
        # Twilio round-trips on the SOS response; gather cuts that to one.
        # Deliberately awaited (not fire-and-forget): the response's
        # contacts_notified count must reflect what actually happened —
        # this is a safety flow, never claim delivery that wasn't attempted.
        _sms_targets = [c for c in contacts if c.get("phone")]
        _sms_results = await asyncio.gather(
            *(
                send_sms(
                    c["phone"],
                    sms_body,
                    twilio_sid=(sms_settings.get("twilio_account_sid", "") if sms_settings else ""),
                    twilio_token=(sms_settings.get("twilio_auth_token", "") if sms_settings else ""),
                    twilio_from=(sms_settings.get("twilio_from_number", "") if sms_settings else ""),
                )
                for c in _sms_targets
            ),
            return_exceptions=True,
        )
        for contact, result in zip(_sms_targets, _sms_results, strict=False):
            if isinstance(result, BaseException):
                # PIPEDA: never log exception text here — Twilio errors embed
                # the destination number. Type name only; contact id is fine.
                logger.error(f"SOS SMS failed for contact {contact.get('id')}: {type(result).__name__}")
            elif result.get("success"):
                contacts_notified += 1
            else:
                # send_sms guarantees 'error' is a PII-free "type code=N
                # status=N" string (never str(exception) — see sms_service).
                logger.error(f"SOS SMS failed for contact {contact.get('id')}: {result.get('error')}")

        if contacts:
            logger.info(
                f"SOS: notified {contacts_notified}/{len(contacts)} emergency contacts for user {current_user['id']}"
            )
    except Exception as e:
        logger.error(f"SOS emergency contact notification failed: {e}", exc_info=True)
        return {
            "success": True,
            "incident_id": incident["id"],
            "contacts_notified": 0,
            "notification_warning": "Emergency contacts could not be reached — please call them directly.",
        }

    return {
        "success": True,
        "incident_id": incident["id"],
        "contacts_notified": contacts_notified,
    }


@api_router.get("/{ride_id}/chat-status")
async def get_chat_status(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Check if chat is available for this ride (active rides + 24h post-trip window)."""
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Authorization: only the rider or the assigned driver may query chat
    # status. Without this guard any authenticated user could probe an
    # arbitrary ride_id and learn whether it exists and its status/timing.
    # Return the SAME 404 as a missing ride for an unauthorized caller — a 403
    # here would still leak ride existence (403 = exists-but-not-yours vs
    # 404 = no-such-ride), the exact disclosure this guard closes.
    if ride.get("rider_id") != current_user["id"]:
        driver = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if not (driver and ride.get("driver_id") == driver["id"]):
            raise HTTPException(status_code=404, detail="Ride not found")

    status = ride.get("status", "")
    if status == RideStatus.CANCELLED:
        return {"available": False, "reason": "Ride was cancelled"}

    if status == RideStatus.COMPLETED:
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at:
            elapsed = (datetime.now(timezone.utc) - completed_at).total_seconds()
            remaining = max(0, 86400 - elapsed)
            if remaining <= 0:
                return {"available": False, "reason": "Post-trip chat window expired"}
            hours_left = int(remaining // 3600)
            return {"available": True, "post_trip": True, "hours_remaining": hours_left}
        return {"available": True, "post_trip": True, "hours_remaining": 24}

    # Active ride — chat is fully available
    return {"available": True, "post_trip": False}


# NOTE: there is deliberately no GET /{ride_id}/call endpoint. Rider↔driver
# contact is in-app chat only — real phone numbers are never exposed to the
# other party (privacy decision, 2026-06). test_coverage_rides.py pins this.


@api_router.get("/{ride_id}/messages")
async def get_ride_messages(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Fetch persistent chat messages for a ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Verify the user is part of the ride
    is_rider = ride.get("rider_id") == current_user["id"]
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to track this ride")

    messages = await db_supabase.get_rows(
        "ride_messages", {"ride_id": ride_id}, limit=100, order="timestamp", desc=False
    )

    # Serialize datetime
    serialized = []
    for msg in messages:
        # Provide fallback serialize
        if "timestamp" in msg and isinstance(msg["timestamp"], datetime):
            msg["timestamp"] = msg["timestamp"].isoformat()
        serialized.append(msg)

    return {"success": True, "messages": serialized}


class SendMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


@api_router.post("/{ride_id}/messages")
@ride_message_limit
async def send_ride_message(
    ride_id: str,
    body: SendMessageRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Send a chat message for an active or recently completed ride.

    Persists the message in `ride_messages` and forwards it to the
    other party via WebSocket (if they're connected). Works as a REST
    fallback for screens that don't hold a direct WS reference (e.g.
    the rider-app chat screen).

    Post-trip chat: messages are allowed for 24 hours after ride
    completion to support lost-item, feedback, and coordination use cases.
    Only the rider or the assigned driver of the ride can send.
    """
    ride = await db.find_one("rides", {"id": ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Block chat on cancelled rides
    if ride.get("status") == RideStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Cannot send messages on a cancelled ride")

    # Post-trip chat window: allow messages for 24h after completion
    if ride.get("status") == RideStatus.COMPLETED:
        completed_at = parse_iso_utc(ride.get("ride_completed_at") or ride.get("updated_at"))
        if completed_at and (datetime.now(timezone.utc) - completed_at).total_seconds() > 86400:
            raise HTTPException(status_code=400, detail="Post-trip chat window has expired (24 hours)")

    is_rider = ride.get("rider_id") == current_user["id"]
    driver = await db.find_one("drivers", {"user_id": current_user["id"]})
    is_driver = driver and ride.get("driver_id") == driver["id"]

    if not (is_rider or is_driver):
        raise HTTPException(status_code=403, detail="Not authorized to send messages in this ride")

    sender = "rider" if is_rider else "driver"
    msg_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "text": body.text.strip(),
        "sender": sender,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    await db.insert_one("ride_messages", msg_data)

    # Forward to the other party via WebSocket + push notification (for
    # backgrounded/offline recipients). The push fires as a background task
    # so it never adds latency to the HTTP response.
    target = None
    push_recipient_user_id: str | None = None
    push_target_app: str | None = None
    push_deeplink: str | None = None

    if sender == "rider" and ride.get("driver_id"):
        d = await db.find_one("drivers", {"id": ride["driver_id"]})
        if d and d.get("user_id"):
            target = f"driver_{d['user_id']}"
            push_recipient_user_id = d["user_id"]
            push_target_app = "driver"
            push_deeplink = f"/driver/chat?rideId={ride_id}"
    elif sender == "driver":
        target = f"rider_{ride['rider_id']}"
        push_recipient_user_id = ride["rider_id"]
        push_target_app = "rider"
        push_deeplink = f"/chat-driver?rideId={ride_id}"

    if target:
        await manager.send_personal_message({**msg_data, "type": "chat_message"}, target)

    if push_recipient_user_id:
        sender_name = (
            (current_user.get("first_name") or "").strip()
            or (current_user.get("name") or "").strip()
            or ("Rider" if sender == "rider" else "Driver")
        )
        preview = body.text.strip()
        if len(preview) > 100:
            preview = preview[:97] + "…"
        spawn(
            send_push_notification(
                push_recipient_user_id,
                f"Message from {sender_name}",
                preview,
                {
                    "type": "chat_message",
                    "ride_id": ride_id,
                    "deeplink": push_deeplink or "",
                },
                priority="normal",
                target_app=push_target_app,
            )
        )

    return {"success": True, "message": msg_data}


class TypingRequest(BaseModel):
    sender: str


@api_router.post("/{ride_id}/typing")
async def send_typing_indicator(
    ride_id: str,
    body: TypingRequest,
    current_user: dict = Depends(get_current_user),
):
    """Broadcast a typing indicator to the other party via WebSocket."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    uid = current_user["id"]
    rider_id = ride.get("rider_id")
    driver_id = ride.get("driver_id")

    if uid == rider_id:
        sender = "rider"
        target = f"driver_{driver_id}" if driver_id else None
    elif uid == driver_id or uid in ((await db_supabase.get_driver_by_id(driver_id) or {}).get("user_id", ""),):
        sender = "driver"
        target = f"rider_{rider_id}" if rider_id else None
    else:
        raise HTTPException(status_code=403, detail="Not a participant")

    if target:
        await manager.send_personal_message(
            {"type": "chat_typing", "ride_id": ride_id, "sender": sender},
            target,
        )

    return {"success": True}


@api_router.delete("/scheduled/{ride_id}")
@cancel_ride_limit
async def cancel_scheduled_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Cancel a scheduled ride.

    Only the pre-dispatch ``scheduled`` state is handled here, behind an
    atomic status-filtered claim. ``is_scheduled`` stays True after the
    dispatch loop flips the ride live, so an id-only cancel here would
    overwrite a searching/accepted/in_progress ride with no driver release,
    no insurance-period transition, no fee, and no WS event. Once dispatched
    the ride is a normal active ride — delegate to cancel_ride_rider, which
    owns the atomic pre-trip claim and full cleanup (and 409s once the trip
    is in_progress).
    """
    ride = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows(
            "rides",
            {"id": ride_id, "rider_id": current_user["id"], "is_scheduled": True},
            limit=1,
        )
    )
    if not ride:
        raise RideNotFoundException(
            ride_id=ride_id,
            message_key=ErrorKeys.RIDE_NOT_FOUND,
        )
    if ride.get("status") in RideStatus.terminal_statuses():
        raise SpinrException(
            message="Ride is already completed or cancelled",
            error_code=ErrorCode.RIDE_ALREADY_CANCELLED,
            status_code=400,
            message_key=ErrorKeys.RIDE_ALREADY_CANCELLED,
        )

    if ride.get("status") == RideStatus.SCHEDULED:
        _now = datetime.now(timezone.utc)
        _base = {
            "status": RideStatus.CANCELLED,
            "cancelled_at": _now,
            "cancellation_reason": "Cancelled by rider (scheduled)",
            "updated_at": _now,
        }
        _claim_filter = {
            "id": ride_id,
            "rider_id": current_user["id"],
            "status": RideStatus.SCHEDULED,
        }
        try:
            claimed = await db_supabase.update_one(
                "rides",
                _claim_filter,
                {**_base, "cancelled_by": "rider", "cancellation_type": "rider_cancel"},
            )
        except Exception as _col_exc:
            # Only a genuine missing-attribution-column error (migration 38
            # not applied yet) may fall back to the minimal payload. Anything
            # else is a real DB failure and must surface, not be retried as a
            # routine schema mismatch. The column-missing text lives in
            # details['original'] / __cause__, not str(_col_exc).
            _details_attr = getattr(_col_exc, "details", None)
            _detail = str(_details_attr.get("original") or "") if isinstance(_details_attr, dict) else ""
            _cause_text = str(getattr(_col_exc, "__cause__", "") or "")
            _combined = f"{_col_exc} {_detail} {_cause_text}".lower()
            if not any(col in _combined for col in ("cancelled_by", "cancellation_type", "pgrst204")):
                raise
            logger.warning(
                f"[SCHED-CANCEL] attribution column(s) missing; retrying minimal. original={_detail or _col_exc}"
            )
            claimed = await db_supabase.update_one("rides", _claim_filter, _base)
        if claimed is not None:
            # Pre-dispatch there is no driver, offer, or card hold to unwind;
            # notify the rider's own devices and any watching admin console.
            await manager.send_personal_message(
                {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"},
                f"rider_{current_user['id']}",
            )
            await manager.broadcast_ride_status(
                ride_id,
                RideStatus.CANCELLED,
                rider_id=current_user["id"],
                reason="rider_cancelled",
                is_scheduled=True,
            )
            return {"success": True}
        # Zero rows: the dispatch loop (or a concurrent cancel) won the race
        # since the read above. Re-read and fall through to the live-ride
        # path so the outcome matches the ride's real state.
        ride = await db_supabase.get_ride(ride_id)
        if not ride or ride.get("status") in RideStatus.terminal_statuses():
            raise SpinrException(
                message="Ride is already completed or cancelled",
                error_code=ErrorCode.RIDE_ALREADY_CANCELLED,
                status_code=400,
                message_key=ErrorKeys.RIDE_ALREADY_CANCELLED,
            )

    # Dispatched (searching → driver_arrived): full rider-cancel path —
    # atomic pre-trip claim (409 once in_progress), cancellation fee,
    # driver + batch-offer release, period-1 insurance transition, WS fan-out.
    return await cancel_ride_rider(
        ride_id,
        reason="Cancelled by rider (scheduled)",
        request=request,
        current_user=current_user,
    )


@api_router.post("/{ride_id}/simulate-arrival")
@api_rate_limit
async def simulate_driver_arrival(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Dev/test only: Simulate driver arriving at pickup, returns OTP."""
    if _settings.ENV.lower() == "production":
        raise HTTPException(status_code=403, detail="Not available in production")
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    await db_supabase.update_ride(
        ride_id,
        {
            "status": RideStatus.DRIVER_ARRIVED,
            "driver_arrived_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    updated_ride = await db_supabase.get_ride(ride_id)
    return {"success": True, "pickup_otp": updated_ride.get("pickup_otp", "0000")}


@api_router.post("/{ride_id}/start")
@ride_action_limit
async def rider_start_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Start a ride. Restricted to the assigned driver only (R-P1-17)."""
    # R-P1-17: Only the assigned driver may mark a ride as started.
    if not current_user.get("is_driver"):
        raise HTTPException(status_code=403, detail="ERR_DRIVER_ONLY")
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    # Verify this driver is the one assigned to the ride
    driver_row = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver_row or ride.get("driver_id") != driver_row["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.DRIVER_ARRIVED:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start ride with status: {ride.get('status')}",
        )

    # Atomic transition guards against duplicate taps / a concurrent driver-side
    # start. update_one returns None when zero rows matched (status already moved).
    guard = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "driver_id": driver_row["id"], "status": RideStatus.DRIVER_ARRIVED},
        {
            "status": RideStatus.IN_PROGRESS,
            "ride_started_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        },
    )
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in driver_arrived state")

    # Insurance Period 3 (passenger aboard — full TNC commercial coverage).
    # Only recorded once the transition actually took effect. Compliance-grade:
    # record_period_transition logs+swallows on failure, never blocks the start.
    await record_period_transition(driver_row["id"], 3, ride_id=ride_id)

    # Every state change must emit a WS event to both parties (CLAUDE.md). Without
    # this the rider's client stays on "driver arrived" until its next poll.
    rider_id = ride.get("rider_id")
    if rider_id:
        await manager.send_personal_message({"type": "ride_started", "ride_id": ride_id}, f"rider_{rider_id}")
        spawn(
            send_push_notification(
                rider_id,
                "Ride Started! ▶️",
                "Your ride has started. Have a safe trip!",
                data={"type": "ride_started", "ride_id": str(ride_id)},
            )
        )
    await manager.broadcast_ride_status(ride_id, RideStatus.IN_PROGRESS, rider_id=rider_id)
    # Update the rider's live activity to the in-progress state.
    spawn(send_live_activity_update({**ride, "status": RideStatus.IN_PROGRESS}, EVENT_UPDATE))
    return {"success": True}


@api_router.post("/{ride_id}/complete")
@ride_action_limit
async def rider_complete_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider-initiated ride completion (early end-ride).

    The rider pays the full agreed fare. We mark the ride completed and
    free the driver, mirroring the essential parts of
    drivers.py::complete_ride but skipping GPS aggregation (that data
    is still captured and available for admin review).
    """
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="Ride is not in progress")

    now = datetime.now(timezone.utc)
    update_fields = {
        "status": RideStatus.COMPLETED,
        "ride_completed_at": now,
        "updated_at": now,
    }
    # Atomic transition: only complete from in_progress. Guards against a
    # concurrent driver-side complete double-running the settlement/incentive
    # logic below. update_one returns None when zero rows matched.
    guard = await db_supabase.update_one("rides", {"id": ride_id, "status": RideStatus.IN_PROGRESS}, update_fields)
    if guard is None:
        raise HTTPException(status_code=409, detail="Ride is not in progress")

    driver_id = ride.get("driver_id")
    driver_user_id = None
    if driver_id:
        await db_supabase.set_driver_available(driver_id, available=True, total_rides_inc=1)
        try:
            await record_period_transition(driver_id, 1)
        except Exception:
            logger.error(
                f"rider_complete_ride: period transition failed for driver {driver_id}",
                exc_info=True,
            )
        driver_row = await db_supabase.get_driver_by_id(driver_id)
        driver_user_id = driver_row.get("user_id") if driver_row else None

        # Daily Spinr Pass allowance: flip the driver offline now (DB-level) if
        # this completion used their last ride for the day. Driver WS notice is
        # sent at the end, after the ride_completed events.
        try:
            from ..utils.spinr_pass import force_offline_if_exhausted
        except ImportError:
            from utils.spinr_pass import force_offline_if_exhausted  # type: ignore
        try:
            # Pass the home service area explicitly so the quota day is anchored
            # on the driver's local timezone even if driver_row is unexpectedly
            # missing (force_offline only auto-resolves it from a dict driver).
            _quota_offline = await force_offline_if_exhausted(
                driver_row or driver_id, area_id=(driver_row or {}).get("service_area_id")
            )
        except Exception:
            _quota_offline = None
            logger.error("rider_complete_ride: quota offline check failed for driver=%s", driver_id, exc_info=True)
    else:
        _quota_offline = None

    # ── Record incentive claims (same logic as drivers.py complete_ride) ──
    _rider_incentive_total = Decimal("0")
    if driver_id:
        try:
            sa_id = ride.get("service_area_id")
            vt_id = ride.get("vehicle_type_id")
            iq = (
                db_supabase.supabase.table("ride_incentives")
                .select("id, bonus_amount, vehicle_type_id")
                .eq("is_active", True)
            )
            if sa_id:
                iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
            else:
                iq = iq.is_("service_area_id", "null")
            inc_result = await db_supabase.run_sync(iq.execute)
            for inc in inc_result.data or []:
                if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
                    continue
                ba = Decimal(str(inc.get("bonus_amount") or 0))
                if ba <= 0:
                    continue
                await db_supabase.insert_one(
                    "ride_incentive_claims",
                    {
                        "id": str(uuid.uuid4()),
                        "ride_id": ride_id,
                        "driver_id": driver_id,
                        "incentive_id": inc["id"],
                        "bonus_amount": float(ba.quantize(Decimal("0.01"))),
                        "claimed_at": now.isoformat(),
                    },
                )
            _rider_incentive_total = sum(
                Decimal(str(inc.get("bonus_amount") or 0))
                for inc in (inc_result.data or [])
                if (not inc.get("vehicle_type_id") or inc["vehicle_type_id"] == vt_id)
                and Decimal(str(inc.get("bonus_amount") or 0)) > 0
            )
        except Exception:
            _rider_incentive_total = Decimal("0")
            logger.error(
                "rider_complete_ride: incentive claim failed for ride %s",
                ride_id,
                exc_info=True,
            )

    # ── Driver earnings snapshot ──
    try:
        _fare_d = _d(ride.get("base_fare") or 0) + _d(ride.get("distance_fare") or 0) + _d(ride.get("time_fare") or 0)
        await db_supabase.update_one(
            "rides",
            {"id": ride_id},
            {
                "driver_earnings_snapshot": build_earnings_snapshot(
                    fare=_fare_d,
                    tip=ride.get("tip_amount") or 0,
                    incentive=_rider_incentive_total,
                    tax=ride.get("tax_amount") or 0,
                    cancel_fee=ride.get("cancellation_fee_driver") or 0,
                )
            },
        )
    except Exception:
        logger.error("rider_complete_ride: driver_earnings_snapshot failed for ride %s", ride_id, exc_info=True)

    completed_ride = await db_supabase.get_ride(ride_id)
    total_fare = (completed_ride or {}).get("total_fare", ride.get("total_fare", 0))
    rider_bill = (completed_ride or {}).get("grand_total") or total_fare

    if driver_user_id:
        await manager.send_personal_message(
            {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare},
            f"driver_{driver_user_id}",
        )
    await manager.send_personal_message(
        {"type": "ride_completed", "ride_id": ride_id, "total_fare": total_fare, "grand_total": rider_bill},
        f"rider_{current_user['id']}",
    )
    await manager.broadcast_ride_status(
        ride_id,
        RideStatus.COMPLETED,
        rider_id=current_user["id"],
        driver_user_id=driver_user_id,
        total_fare=total_fare,
    )
    # End the rider's live activity on trip completion.
    spawn(send_live_activity_update(completed_ride or {"id": ride_id, "status": RideStatus.COMPLETED}, EVENT_END))
    try:
        await manager.broadcast_to_admins(
            {
                "type": "ride_completed",
                "ride_id": ride_id,
                "total_fare": total_fare,
                "completed_by": "rider",
            }
        )
    except Exception as _bcast_err:
        logger.warning("admin broadcast failed for ride_completed %s: %s", ride_id, _bcast_err)

    # Advance any active driver quests this completion contributes to. Runs once
    # per ride because the atomic in_progress→completed guard above lets only the
    # winning completion path reach here. Scheduled as a background task so the
    # per-quest queries/updates never block the completion response (the ride is
    # already completed); the tracker swallows its own errors internally.
    if driver_id:
        try:
            try:
                from ..utils.quest_tracker import update_quest_progress_on_ride_complete
            except ImportError:
                from utils.quest_tracker import update_quest_progress_on_ride_complete
            spawn(update_quest_progress_on_ride_complete(driver_id, completed_ride or ride))
        except Exception:
            logger.error(
                "rider_complete_ride: scheduling quest progress update failed for ride %s", ride_id, exc_info=True
            )

    # Notify the driver (and admins) if this completion took them offline for
    # the day. Reuses the existing 'auto_offline' client handler.
    if _quota_offline and driver_user_id:
        _reset_h = round(_quota_offline.get("hours_until_reset") or 0)
        try:
            await manager.send_personal_message(
                {
                    "type": "auto_offline",
                    "reason": "quota_exhausted",
                    "message": (
                        f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for "
                        f"today. You're now offline — your allowance resets in about {_reset_h}h."
                    ),
                    "quota_resets_at": _quota_offline.get("quota_resets_at"),
                },
                f"driver_{driver_user_id}",
            )
            await manager.broadcast_to_admins(
                {"type": "driver_status_changed", "driver_id": driver_id, "is_online": False}
            )
        except Exception:
            logger.warning("rider_complete_ride: quota auto_offline notify failed for driver=%s", driver_id)
        # Push so the driver sees it even with the app backgrounded.
        try:
            await send_push_notification(
                driver_user_id,
                "Daily ride limit reached",
                (
                    f"You've used all {_quota_offline.get('rides_per_day')} Spinr Pass rides for today. "
                    f"You're now offline — your allowance resets in about {_reset_h}h."
                ),
                data={"type": "quota_exhausted", "driver_id": str(driver_id)},
                target_app="driver",
            )
        except Exception:
            logger.warning("rider_complete_ride: quota push failed for driver=%s", driver_id)

    return completed_ride or ride


@api_router.get("/{ride_id}/receipt")
async def get_ride_receipt(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Get a detailed receipt for a completed ride"""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized to view this receipt")

    if ride.get("status") not in RideStatus.terminal_statuses():
        raise HTTPException(
            status_code=400,
            detail="Receipts are only available for completed or cancelled rides",
        )

    driver = None
    if ride.get("driver_id"):
        driver = await db_supabase.get_driver_by_id(ride["driver_id"])

    driver_profile = None
    if driver and driver.get("user_id"):
        driver_profile = await db_supabase.get_user_by_id(driver["user_id"])

    vehicle = None
    if ride.get("vehicle_type_id"):
        vehicle = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("vehicle_types", {"id": ride["vehicle_type_id"]}, limit=1)
        )

    corporate_account = None
    if ride.get("corporate_account_id"):
        corporate_account = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("corporate_accounts", {"id": ride["corporate_account_id"]}, limit=1)
        )

    # Resolve fare display: snapshot (when fare_lock is active) or dynamic rebuild.
    snapshot = ride.get("fare_breakdown_snapshot")
    fare_locked = False
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines"):
        try:
            settings = await get_app_settings()
            fare_locked = settings.get("fare_lock_enabled", False) if settings else False
        except Exception:
            fare_locked = False

    if fare_locked and snapshot:
        fare_lines = list(snapshot["lines"])
        ride_tip = float(_d(ride.get("tip_amount") or 0))
        has_tip_line = any(ln.get("type") == "tip" for ln in fare_lines)
        if ride_tip > 0 and not has_tip_line:
            fare_lines.append({"label": "Tip", "amount": _f(_d(ride_tip)), "type": "tip"})
        receipt_grand_total = _sum_fare_breakdown(fare_lines)
    else:
        fare_lines = _build_fare_breakdown(ride)
        receipt_grand_total = ride.get("grand_total") or (
            (ride.get("total_fare", 0) or 0)
            + (ride.get("area_fees_total", 0) or 0)
            + (ride.get("tax_amount", 0) or 0)
            + (ride.get("tip_amount", 0) or 0)
        )

    receipt_data = {
        "ride_id": ride_id,
        "ride_code": ride.get("ride_code"),
        "date": ride.get("ride_completed_at") or ride.get("cancelled_at") or ride.get("created_at"),
        "status": ride.get("status"),
        "pickup_address": ride.get("pickup_address"),
        "dropoff_address": ride.get("dropoff_address"),
        "stops": ride.get("stops", []),
        "distance_km": ride.get("distance_km"),
        "duration_minutes": ride.get("duration_minutes"),
        "base_fare": ride.get("base_fare", 0),
        "distance_fare": ride.get("distance_fare", 0),
        "time_fare": ride.get("time_fare", 0),
        "airport_fee": ride.get("airport_fee", 0),
        "booking_fee": ride.get("booking_fee", 0),
        "cancellation_fee": (
            (ride.get("cancellation_fee_admin", 0) + ride.get("cancellation_fee_driver", 0))
            if ride.get("status") == RideStatus.CANCELLED
            else 0
        ),
        "surge_multiplier": ride.get("surge_multiplier", 1.0),
        "area_fees_total": ride.get("area_fees_total", 0),
        "area_fees_breakdown": ride.get("area_fees_breakdown", []),
        "tax_amount": ride.get("tax_amount", 0),
        "tax_breakdown": ride.get("tax_breakdown", {}),
        "tip_amount": ride.get("tip_amount", 0),
        "total_charged": ride.get("total_fare", 0),
        "grand_total": receipt_grand_total,
        "fare_breakdown": fare_lines,
        "fare_locked": fare_locked,
        "payment_method": (
            "Corporate Account"
            if corporate_account
            else (ride.get("payment_method_id") or "Credit Card ending in ****")
        ),
        "corporate_account_name": (corporate_account.get("company_name") if corporate_account else None),
        "driver_name": (
            f"{driver_profile.get('first_name', '')} {driver_profile.get('last_name', '')}".strip()
            if driver_profile
            else "Unknown Driver"
        ),
        "vehicle_type": vehicle.get("name") if vehicle else "Standard",
    }

    return {"success": True, "receipt": receipt_data}


@api_router.post("/{ride_id}/email-receipt")
@ride_action_limit
async def email_ride_receipt(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Re-send the receipt email for a completed ride."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") not in RideStatus.terminal_statuses():
        raise HTTPException(status_code=400, detail="Receipts are only available for completed rides")

    tip_amount = Decimal(str(ride.get("tip_amount") or 0))
    email_sent = await send_ride_receipt(ride, current_user["id"], tip_amount)
    if not email_sent:
        raise HTTPException(
            status_code=503,
            detail="Could not send receipt email. Please try again later.",
        )
    return {"success": True}


class RiderLostItemRequest(BaseModel):
    item_description: str = Field(..., min_length=3, max_length=500)
    item_category: str = Field(default="other")


@api_router.post("/{ride_id}/lost-and-found")
@ride_action_limit
async def rider_report_lost_item(
    ride_id: str,
    req: RiderLostItemRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider reports a lost item from a completed ride."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=400,
            detail="Lost items can only be reported for completed rides",
        )

    driver_id = ride.get("driver_id")
    if not driver_id:
        raise HTTPException(status_code=400, detail="No driver assigned to this ride")

    valid_categories = {"electronics", "clothing", "bag", "document", "keys", "other"}
    category = req.item_category if req.item_category in valid_categories else "other"

    item_data = {
        "id": str(uuid.uuid4()),
        "ride_id": ride_id,
        "reporter_id": current_user["id"],
        "rider_user_id": current_user["id"],
        "driver_id": driver_id,
        "reporter_type": "rider",
        "item_description": req.item_description,
        "item_category": category,
        "status": "reported",
        "contact_method": "in_app",
    }

    item = await db_supabase.create_lost_and_found(item_data)

    try:
        driver = await db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            driver_user = await db_supabase.get_user_by_id(driver["user_id"])
            if driver_user:
                await send_push_notification(
                    driver_user["id"],
                    "Lost Item Report",
                    f"A rider reported a lost item: {req.item_description}. Please check your vehicle.",
                    {"type": "lost_and_found", "case_id": item["id"], "ride_id": ride_id},
                    target_app="driver",
                )
                await db_supabase.update_lost_and_found(
                    item["id"],
                    {
                        "status": "driver_notified",
                        "notified_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
    except Exception as e:
        logger.error(f"Lost item driver notification failed for ride {ride_id}: {e}", exc_info=True)

    return {"success": True, "item": item}


# ---------------------------------------------------------------------------
# Safety check-in response (Feature D — P3)
# ---------------------------------------------------------------------------


@api_router.post("/{ride_id}/safety-checkin")
@ride_action_limit
async def safety_checkin_response(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider taps 'I'm okay' on the safety check-in push notification.

    Records the response in Redis so the safety_checkin_loop does not escalate
    this ride to the trust-and-safety team.
    """
    try:
        from ..utils.redis_client import redis_set
    except ImportError:
        from utils.redis_client import redis_set  # type: ignore

    user_id = current_user.get("id")

    # Verify the ride belongs to this rider and is still in_progress.
    ride = await db_supabase.get_rows("rides", {"id": ride_id, "rider_id": user_id}, limit=1)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride[0].get("status") != RideStatus.IN_PROGRESS:
        raise HTTPException(status_code=409, detail="Ride is not in progress")

    # 4-hour TTL mirrors the sent/escalated keys in safety_checkin_loop.
    await redis_set(f"safety:checkin:ok:{ride_id}", "1", ttl=4 * 3600)

    logger.info(f"[SAFETY_CHECKIN] Rider {user_id} confirmed OK for ride {ride_id}")
    return {"success": True}


@api_router.get("/{ride_id}/live-route")
async def get_live_route(ride_id: str, current_user: dict = Depends(get_current_user)):
    """Road-routed line + ETA from the driver's live position to the active
    destination (pickup pre-trip, dropoff in-trip) for the live trip map.

    Routing provider chain lives in utils.route_distance.compute_route: self-
    hosted OSRM first, Google Directions fallback (budget-gated) when OSRM is
    unconfigured or failing. Returns an empty polyline (eta_seconds=None) when
    there's no active route, no live driver position, or every provider failed
    — the client then keeps its saved planned line. Keeps the routing engine
    internal (the apps draw this line on the Google map canvas)."""
    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise RideNotFoundException(ride_id=ride_id, message_key=ErrorKeys.RIDE_NOT_FOUND)

    # Ownership: rider or assigned driver (admin allowed).
    is_rider = ride.get("rider_id") == current_user["id"]
    driver_self = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    is_driver = bool(driver_self) and ride.get("driver_id") == driver_self["id"]
    if not (is_rider or is_driver) and current_user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to view this ride")

    status = ride.get("status")
    if status in ("driver_assigned", "driver_accepted", "driver_arrived"):
        dest_lat, dest_lng, destination = ride.get("pickup_lat"), ride.get("pickup_lng"), "pickup"
    elif status == "in_progress":
        dest_lat, dest_lng, destination = ride.get("dropoff_lat"), ride.get("dropoff_lng"), "dropoff"
    else:
        # No live leg for searching / scheduled / completed / cancelled.
        return {"polyline": [], "eta_seconds": None, "distance_km": None, "destination": None}

    empty = {"polyline": [], "eta_seconds": None, "distance_km": None, "destination": destination}

    # Live origin = the assigned driver's current position (drivers.lat/lng).
    assigned = (
        (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"id": ride.get("driver_id")}, limit=1)
        )
        if ride.get("driver_id")
        else None
    )
    o_lat = assigned.get("lat") if assigned else None
    o_lng = assigned.get("lng") if assigned else None
    if o_lat is None or o_lng is None or dest_lat is None or dest_lng is None:
        return empty

    try:
        from ..utils.route_distance import compute_route
    except ImportError:
        from utils.route_distance import compute_route  # type: ignore

    result = await compute_route(float(o_lat), float(o_lng), float(dest_lat), float(dest_lng))
    if not result:
        return empty
    result["destination"] = destination
    return result
