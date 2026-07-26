"""
promotions.py – Promo codes & referral system for Spinr.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

try:
    from .. import db_supabase
    from ..db_supabase import increment_promo_uses
    from ..dependencies import get_admin_user, get_current_user
    from ..models.ride_status import RideStatus
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.rate_limiter import promo_available_limit, promo_validate_limit
except ImportError:
    import db_supabase
    from db_supabase import increment_promo_uses
    from dependencies import get_admin_user, get_current_user
    from models.ride_status import RideStatus
    from utils.datetime_utils import parse_iso_utc
    from utils.rate_limiter import promo_available_limit, promo_validate_limit

get_current_admin = get_admin_user

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/promo", tags=["Promotions"])


# ============ Pydantic Models ============


class ValidatePromoRequest(BaseModel):
    code: str
    ride_fare: Decimal = Decimal("0.00")  # ride portion only (base+dist+time); ignored when ride_id is provided
    grand_total: Optional[Decimal] = None  # full amount including fees; required for free_ride promos
    ride_id: Optional[str] = None  # When set, fare is fetched server-side (R-P2-33)


class ApplyPromoRequest(BaseModel):
    code: str
    ride_id: str  # Required for server-side fare verification (R-P1-8)


class CreatePromoCodeRequest(BaseModel):
    code: str
    free_ride: bool = False  # when True, covers entire grand_total; discount_type/value ignored
    discount_type: str = "flat"  # flat | percentage
    discount_value: Decimal = Field(default=Decimal("0"), ge=0, le=500)  # 0 allowed for free_ride
    max_discount: Optional[Decimal] = None  # Cap for percentage discounts
    max_uses: int = 100
    max_uses_per_user: int = 1
    expiry_date: Optional[str] = None  # ISO 8601
    is_active: bool = True
    description: Optional[str] = None
    service_area_id: Optional[str] = None  # None = all areas

    @field_validator("discount_value")
    @classmethod
    def validate_discount_value(cls, value, info):
        if info.data.get("free_ride"):
            return value
        if value <= 0:
            raise ValueError("discount_value must be greater than 0")
        if info.data.get("discount_type") == "percentage" and value > 100:
            raise ValueError("Percentage discount cannot exceed 100")
        return value


class UpdatePromoCodeRequest(BaseModel):
    free_ride: Optional[bool] = None
    discount_type: Optional[str] = None
    discount_value: Optional[Decimal] = None
    max_discount: Optional[Decimal] = None
    max_uses: Optional[int] = None
    max_uses_per_user: Optional[int] = None
    expiry_date: Optional[str] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None
    service_area_id: Optional[str] = None


# ============ User-Facing Endpoints ============


async def _validate_promo_for_user(
    code: str,
    user_id: str,
    ride_fare: Decimal,  # ride portion only (base+dist+time = driver earnings)
    ride_id: Optional[str] = None,
    grand_total: Optional[Decimal] = None,  # full amount including fees; required for free_ride promos
) -> Dict[str, Any]:
    """Validate ``code`` against all 10 promo rules for ``user_id``.

    Pure helper — does not depend on FastAPI ``Depends``. Used by:
    - ``POST /promo/validate`` (rider self-service)
    - ``POST /promo/apply`` (rider self-service)
    - admin "apply on behalf of rider" via ``apply_promo_for_admin``

    Per-user limits are scoped to ``user_id``, so an admin-applied promo
    counts against the rider just like a rider-applied one (the chosen
    behaviour for the admin Create Ride flow).

    ``ride_fare`` is the ride-only portion (driver earnings: base+dist+time).
    Regular percentage/flat promos apply to this amount only — never to fees or taxes.
    ``grand_total`` is the full rider-facing total. ``free_ride`` promos use it
    so the rider pays $0.
    """
    code = code.strip().upper()
    now = datetime.now(timezone.utc)
    promo = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("promotions", {"code": code}, limit=1))

    if not promo:
        raise HTTPException(status_code=404, detail="Invalid promo code")

    if not promo.get("is_active", False):
        raise HTTPException(status_code=400, detail="This promo code is no longer active")

    # 1. Expiry
    expiry = promo.get("expiry_date")
    if expiry and isinstance(expiry, str):
        try:
            exp_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt < now:
                raise HTTPException(status_code=400, detail="This promo code has expired")
        except (ValueError, HTTPException) as e:
            if isinstance(e, HTTPException):
                raise  # noqa: E701

    # 2. Total usage limit (0 = unlimited)
    max_uses = promo.get("max_uses", 0)
    if max_uses > 0 and promo.get("uses", 0) >= max_uses:
        raise HTTPException(status_code=400, detail="This promo code has reached its usage limit")

    # 3. Per-user usage limit (0 = unlimited)
    max_per_user = promo.get("max_uses_per_user", 1)
    if max_per_user > 0:
        user_uses = await db_supabase.count_documents(
            "promo_applications",
            {
                "promo_id": promo["id"],
                "user_id": user_id,
            },
        )
        if user_uses >= max_per_user:
            raise HTTPException(
                status_code=400,
                detail="You have already used this promo code the maximum number of times",
            )

    # 4. Minimum ride fare — check against ride portion (base+dist+time),
    #    matching what /promo/available uses so a promo that is shown to the
    #    rider is never rejected at booking. Fees and taxes are excluded.
    min_fare = promo.get("min_ride_fare", 0)
    if min_fare > 0:
        effective_fare = ride_fare
        if ride_id:
            ride_rows = await db_supabase.get_rows("rides", {"id": ride_id, "rider_id": user_id}, limit=1)
            if not ride_rows:
                raise HTTPException(status_code=404, detail="Ride not found")
            r = ride_rows[0]
            effective_fare = (
                Decimal(str(r.get("base_fare") or 0))
                + Decimal(str(r.get("distance_fare") or 0))
                + Decimal(str(r.get("time_fare") or 0))
            )
        if effective_fare < min_fare:
            raise HTTPException(
                status_code=400,
                detail=f"Minimum ride fare of ${min_fare:.2f} required for this promo",
            )

    # 5. Private coupon — assigned to specific users only
    assigned_users = promo.get("assigned_user_ids", [])
    if assigned_users and user_id not in assigned_users:
        raise HTTPException(status_code=400, detail="This promo code is not available for your account")

    # 6. First ride only
    if promo.get("first_ride_only"):
        ride_count = await db_supabase.count_documents("rides", {"rider_id": user_id, "status": RideStatus.COMPLETED})
        if ride_count > 0:
            raise HTTPException(status_code=400, detail="This promo is for first-time riders only")

    # 7. New user restriction (user account must be less than X days old)
    new_user_days = promo.get("new_user_days", 0)
    if new_user_days > 0:
        user = await db_supabase.get_user_by_id(user_id)
        if user and user.get("created_at"):
            created = parse_iso_utc(user["created_at"])
            if created is not None and (now - created).days > new_user_days:
                raise HTTPException(status_code=400, detail="This promo is for new users only")

    # 8. Inactive user targeting (no rides in X days)
    inactive_days = promo.get("inactive_days", 0)
    if inactive_days > 0:
        cutoff = (now - timedelta(days=inactive_days)).isoformat()
        recent_rides = await db_supabase.count_documents(
            "rides",
            {
                "rider_id": user_id,
                "status": RideStatus.COMPLETED,
                "ride_completed_at": {"$gte": cutoff},
            },
        )
        if recent_rides > 0:
            raise HTTPException(
                status_code=400,
                detail="This promo is for returning riders who haven't ridden recently",
            )

    # 9. Minimum / maximum total rides
    min_rides = promo.get("min_total_rides", 0)
    max_rides = promo.get("max_total_rides", 0)
    if min_rides > 0 or max_rides > 0:
        total_rides = await db_supabase.count_documents("rides", {"rider_id": user_id, "status": RideStatus.COMPLETED})
        if min_rides > 0 and total_rides < min_rides:
            raise HTTPException(
                status_code=400,
                detail=f"You need at least {min_rides} completed rides to use this promo",
            )
        if max_rides > 0 and total_rides >= max_rides:
            raise HTTPException(
                status_code=400,
                detail="This promo is not available for your ride count",
            )

    # 10. Budget check
    total_budget = promo.get("total_budget", 0)
    if total_budget > 0 and promo.get("budget_used", 0) >= total_budget:
        raise HTTPException(status_code=400, detail="This promotion has reached its budget limit")

    # Calculate discount — Decimal end-to-end (no float round-trip)
    free_ride = promo.get("free_ride", False)
    discount_type = promo.get("discount_type", "flat")
    discount_value = Decimal(str(promo.get("discount_value", 0) or 0))

    if free_ride:
        # Covers the entire grand_total — rider pays $0
        discount = grand_total if grand_total is not None else ride_fare
    elif discount_type == "percentage":
        # Percentage applies to ride portion only (never to fees or taxes)
        discount = (ride_fare * discount_value / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        max_cap = promo.get("max_discount")
        if max_cap and discount > Decimal(str(max_cap)):
            discount = Decimal(str(max_cap))
    else:
        # Flat discount capped at ride_fare (driver earnings only — never discounts fees/taxes)
        discount = min(discount_value, ride_fare)

    return {
        "valid": True,
        "code": code,
        "free_ride": free_ride,
        "discount_type": discount_type,
        "discount_value": float(discount_value),
        "discount_amount": discount,
        "max_discount": promo.get("max_discount"),
        "promo_id": promo["id"],
        "description": promo.get("description", ""),
    }


async def _record_promo_application(promo_id: str, code: str, user_id: str, discount: Decimal) -> str:
    """Insert promo_applications row and atomically increment promotions.uses.

    Returns the application id. Raises 409 if the promo just got fully
    redeemed by a concurrent caller.
    """
    promo_row = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("promotions", {"id": promo_id}, limit=1))
    max_per_user = int((promo_row or {}).get("max_uses_per_user", 1))

    # AUTHORITATIVE per-user gate (migration 257). The rule-3 count_documents()
    # check in the validation path is racy — N concurrent redemptions all read
    # user_uses=0 and pass. This atomic row-locked claim is what actually caps
    # per-user redemptions; raise the same 400 the pre-check would have.
    claimed = await db_supabase.claim_promo_user_slot(promo_id, user_id, max_per_user)
    if not claimed:
        raise HTTPException(
            status_code=400,
            detail="You have already used this promo code the maximum number of times",
        )

    application = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "promo_id": promo_id,
        "code": code,
        "discount_applied": discount,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("promo_applications", application)

    max_uses = int((promo_row or {}).get("max_uses", 0))
    incremented = await increment_promo_uses(promo_id, max_uses)
    if not incremented:
        # Global cap exhausted after we claimed this user's slot — release it so
        # the user isn't charged a redemption for a promo they never got.
        await db_supabase.release_promo_user_slot(promo_id, user_id)
        raise HTTPException(status_code=409, detail="Promo code has been fully redeemed")
    return application["id"]


async def apply_promo_for_admin(
    code: str,
    rider_id: str,
    ride_fare: Decimal,
    ride_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Admin-side promo application on behalf of a rider.

    Runs the same 10-rule validation against ``rider_id`` (so the rider's
    per-user limit is honoured) and records the redemption in
    ``promo_applications`` keyed to the rider. The caller (admin/rides.py)
    is responsible for persisting ``promo_application_id`` + ``discount_amount``
    on the ride row.
    """
    validation = await _validate_promo_for_user(code, rider_id, ride_fare, ride_id)
    application_id = await _record_promo_application(
        promo_id=validation["promo_id"],
        code=validation["code"],
        user_id=rider_id,
        discount=validation["discount_amount"],
    )
    return {
        "promo_id": validation["promo_id"],
        "code": validation["code"],
        "discount_amount": validation["discount_amount"],
        "application_id": application_id,
    }


@api_router.post("/validate")
@promo_validate_limit
async def validate_promo(
    request: Request,
    req: ValidatePromoRequest,
    current_user: dict = Depends(get_current_user),
):
    """Validate promo code against all rules: usage, expiry, area, user targeting, fare minimum."""
    return await _validate_promo_for_user(
        code=req.code,
        user_id=current_user["id"],
        ride_fare=req.ride_fare,
        ride_id=req.ride_id,
        grand_total=req.grand_total,
    )


@api_router.post("/apply")
async def apply_promo(
    req: ApplyPromoRequest,
    current_user: dict = Depends(get_current_user),
):
    """Apply a promo code (records usage). Call after ride creation.
    R-P1-8: uses server-stored ride fare, not client-supplied fare.
    """
    # Fetch server-side fares to prevent client-manipulated discount inflation.
    ride = await db_supabase.find_one("rides", {"id": req.ride_id})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user["id"]:
        raise HTTPException(status_code=403, detail="ERR_FORBIDDEN")

    # ride_fare = driver earnings (base+dist+time); the portion promos discount against
    server_ride_fare = Decimal(str(ride.get("driver_earnings") or "0"))
    if server_ride_fare == 0:
        server_ride_fare = (
            Decimal(str(ride.get("base_fare") or "0"))
            + Decimal(str(ride.get("distance_fare") or "0"))
            + Decimal(str(ride.get("time_fare") or "0"))
        )
    server_grand_total = Decimal(str(ride.get("total_fare", 0)))

    validation = await _validate_promo_for_user(
        code=req.code,
        user_id=current_user["id"],
        ride_fare=server_ride_fare,
        grand_total=server_grand_total,
    )

    application_id = await _record_promo_application(
        promo_id=validation["promo_id"],
        code=validation["code"],
        user_id=current_user["id"],
        discount=validation["discount_amount"],
    )

    return {
        "success": True,
        "discount_applied": validation["discount_amount"],
        "application_id": application_id,
    }


def compute_promo_discount(promo: Dict[str, Any], ride_portion: Decimal, grand_total: Decimal) -> Decimal:
    """Discount a promo yields for a quote — pure Decimal math shared by
    /promo/available and the AI fare-quote tool so the advertised savings
    always equal what settlement charges.

    ``ride_portion`` is base+dist+time (the only part regular promos
    discount — never fees or taxes); ``free_ride`` promos cover the full
    ``grand_total`` so the rider pays $0.
    """
    if promo.get("free_ride"):
        return grand_total
    discount_type = promo.get("discount_type", "flat")
    discount_value = Decimal(str(promo.get("discount_value", 0) or 0))
    if discount_type == "percentage":
        if ride_portion <= 0:
            return Decimal("0")
        discount = (ride_portion * discount_value / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        max_cap = promo.get("max_discount")
        if max_cap and discount > Decimal(str(max_cap)):
            discount = Decimal(str(max_cap))
        return discount
    # Flat discount capped at the ride portion (driver earnings). When the
    # ride portion is unknown (0), fall back to grand_total so we never
    # advertise a discount larger than the rider would actually be charged.
    cap_basis = ride_portion if ride_portion > 0 else grand_total
    return min(discount_value, cap_basis) if cap_basis > 0 else discount_value


async def list_available_promos(
    user_id: str,
    *,
    ride_fare: float = 0.0,
    ride_portion: Optional[float] = None,
    pickup_lat: Optional[float] = None,
    pickup_lng: Optional[float] = None,
) -> list:
    """Full promo-eligibility engine behind GET /promo/available.

    Shared by the route and the AI assistant's quoting tool so every
    surface advertises the same promos with the same amounts.
    ``ride_fare`` is the grand total; ``ride_portion`` is base+dist+time.
    Returns entries sorted eligible-first, biggest discount first.
    """
    now = datetime.now(timezone.utc)

    # Resolve pickup service area so we can filter area-specific promos.
    pickup_area_id: Optional[str] = None
    if pickup_lat is not None and pickup_lng is not None:
        try:
            rows = await db_supabase.rpc(
                "get_service_area_for_point",
                {"lat": pickup_lat, "lng": pickup_lng},
            )
            if rows:
                pickup_area_id = rows[0].get("id")
        except Exception as e:
            logger.warning(
                "Could not resolve service area for pickup (%.4f, %.4f): %s",
                pickup_lat,
                pickup_lng,
                e,
            )

    promos = await db_supabase.get_rows("promotions", {"is_active": True}, limit=100)

    # Keep only promos that match the pickup area (or have no area restriction).
    if pickup_area_id:
        promos = [p for p in promos if p.get("service_area_id") is None or p.get("service_area_id") == pickup_area_id]
    else:
        # Pickup unknown — show only global (unrestricted) promos.
        promos = [p for p in promos if p.get("service_area_id") is None]

    # Pre-fetch user data for targeting checks
    user = await db_supabase.get_user_by_id(user_id)
    total_rides = await db_supabase.count_documents("rides", {"rider_id": user_id, "status": RideStatus.COMPLETED})
    recent_cutoff_30 = (now - timedelta(days=30)).isoformat()
    await db_supabase.count_documents(
        "rides",
        {
            "rider_id": user_id,
            "status": RideStatus.COMPLETED,
            "ride_completed_at": {"$gte": recent_cutoff_30},
        },
    )

    # Pre-fetch all user promo applications in one query so per-promo usage
    # check is O(1) instead of one DB call per promo (N+1 reduction).
    all_user_apps = await db_supabase.get_rows("promo_applications", {"user_id": user_id}, limit=2000) or []
    user_usage_by_promo: dict = {}
    for app in all_user_apps:
        pid = app.get("promo_id")
        if pid:
            user_usage_by_promo[pid] = user_usage_by_promo.get(pid, 0) + 1

    available = []
    for p in promos:
        try:
            # Expiry — keep both sides tz-aware so the comparison never raises
            # "can't compare offset-naive and offset-aware datetimes" when a
            # promo row has a TIMESTAMPTZ value with offset info.
            expiry = p.get("expiry_date")
            if expiry:
                exp_dt = parse_iso_utc(expiry) if isinstance(expiry, str) else expiry
                if exp_dt and exp_dt < now:
                    continue  # noqa: E701

            # Total usage (0 = unlimited)
            max_uses = p.get("max_uses", 0)
            if max_uses > 0 and p.get("uses", 0) >= max_uses:
                continue  # noqa: E701

            # Per-user usage (0 = unlimited) — uses pre-fetched map, not a per-promo query
            max_per_user = p.get("max_uses_per_user", 1)
            if max_per_user > 0:
                user_uses = user_usage_by_promo.get(p["id"], 0)
                if user_uses >= max_per_user:
                    continue  # noqa: E701

            # Min fare — compare against ride portion (base+dist+time) when
            # available; fall back to ride_fare (grand_total) for older clients.
            # Instead of hiding, mark as ineligible so the rider sees the promo
            # but can't apply it (avoids confusion).
            _min_check_fare = ride_portion if ride_portion is not None else ride_fare
            _below_min_fare = p.get("min_ride_fare", 0) > 0 and _min_check_fare < p["min_ride_fare"]

            # Private coupon
            assigned = p.get("assigned_user_ids", [])
            if assigned and user_id not in assigned:
                continue  # noqa: E701

            # First ride only
            if p.get("first_ride_only") and total_rides > 0:
                continue  # noqa: E701

            # New user only
            new_days = p.get("new_user_days", 0)
            if new_days > 0 and user and user.get("created_at"):
                created = parse_iso_utc(user["created_at"])
                if created is not None and (now - created).days > new_days:
                    continue  # noqa: E701

            # Inactive user targeting
            inactive_days = p.get("inactive_days", 0)
            if inactive_days > 0:
                cutoff = (now - timedelta(days=inactive_days)).isoformat()
                recent = await db_supabase.count_documents(
                    "rides",
                    {
                        "rider_id": user_id,
                        "status": RideStatus.COMPLETED,
                        "ride_completed_at": {"$gte": cutoff},
                    },
                )
                if recent > 0:
                    continue  # noqa: E701

            # Min/max ride count
            if p.get("min_total_rides", 0) > 0 and total_rides < p["min_total_rides"]:
                continue  # noqa: E701
            if p.get("max_total_rides", 0) > 0 and total_rides >= p["max_total_rides"]:
                continue  # noqa: E701

            # Budget
            if p.get("total_budget", 0) > 0 and p.get("budget_used", 0) >= p["total_budget"]:
                continue  # noqa: E701

            # Calculate discount — same Decimal HALF_UP math as the validate/
            # apply path so the advertised amount always equals the charged
            # amount (the old float round() here could disagree by a cent).
            effective_ride = Decimal(str(ride_portion if ride_portion is not None else ride_fare))
            ride_fare_d = Decimal(str(ride_fare))
            free_ride_flag = p.get("free_ride", False)
            discount_type = p.get("discount_type", "flat")
            discount_value = Decimal(str(p.get("discount_value", 0) or 0))
            discount = compute_promo_discount(p, effective_ride, ride_fare_d)

            entry: dict = {
                "promo_id": p["id"],
                "code": p.get("code"),
                "free_ride": free_ride_flag,
                "discount_type": discount_type,
                "discount_value": float(discount_value),
                "max_discount": p.get("max_discount"),
                "discount_amount": float(discount),
                "description": p.get("description", ""),
                "expiry_date": p.get("expiry_date"),
                "min_ride_fare": p.get("min_ride_fare", 0),
            }
            if _below_min_fare:
                entry["eligible"] = False
                entry["ineligible_reason"] = f"Minimum ride fare of ${p['min_ride_fare']:.2f} required"
            else:
                entry["eligible"] = True
            available.append(entry)
        except Exception as promo_err:
            logger.error(f"[PROMOS] Skipping promo {p.get('id')} due to error: {promo_err}")
            continue

    # Eligible first, then by biggest discount
    available.sort(key=lambda x: (not x.get("eligible", True), -x["discount_amount"]))
    return available


@api_router.get("/available")
@promo_available_limit
async def get_available_promos(
    request: Request,
    ride_fare: float = Query(0.0),  # grand total (kept for backward compat + min-fare check)
    ride_portion: Optional[float] = Query(None),  # ride-only portion (base+dist+time) for promo calculation
    pickup_lat: Optional[float] = Query(None),
    pickup_lng: Optional[float] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get all promos available to this user, filtered by pickup service area."""
    return await list_available_promos(
        current_user["id"],
        ride_fare=ride_fare,
        ride_portion=ride_portion,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
    )


# ============ Admin Promo Code CRUD ============

admin_router = APIRouter(
    prefix="/admin/promo-codes",
    tags=["Admin Promotions"],
    dependencies=[Depends(get_current_admin)],
)


@admin_router.get("")
async def admin_get_promo_codes():
    """Get all promo codes."""
    codes = await db_supabase.get_rows("promotions", order="created_at", desc=True, limit=500)
    return codes


@admin_router.post("")
async def admin_create_promo_code(req: CreatePromoCodeRequest):
    """Create a new promo code."""
    code = req.code.strip().upper()

    # Uniqueness is (code, service_area_id) — same code is allowed in different areas.
    area_id = req.service_area_id or None
    lookup: dict = {"code": code}
    if area_id:
        lookup["service_area_id"] = area_id
    existing_rows = await db_supabase.get_rows("promotions", lookup, limit=1)
    # For global promos (area_id is None) we must also exclude rows where service_area_id IS NULL.
    if area_id is None:
        existing_rows = [r for r in (existing_rows or []) if r.get("service_area_id") is None]
    if existing_rows:
        area_label = " in this service area" if area_id else " (global)"
        raise HTTPException(status_code=400, detail=f"Promo code '{code}' already exists{area_label}")

    if not req.free_ride:
        if req.discount_type not in ("flat", "percentage"):
            raise HTTPException(status_code=400, detail="discount_type must be 'flat' or 'percentage'")
        # P1-4: cap discount_value to prevent absurd promo creation
        if req.discount_type == "percentage" and req.discount_value > 100:
            raise HTTPException(status_code=400, detail="Percentage discount cannot exceed 100%")
        if req.discount_type == "flat" and req.discount_value > 500:
            raise HTTPException(status_code=400, detail="Flat discount cannot exceed $500")

    promo = {
        "id": str(uuid.uuid4()),
        "code": code,
        "free_ride": req.free_ride,
        "discount_type": req.discount_type,
        "discount_value": req.discount_value,
        "max_discount": req.max_discount,
        "max_uses": req.max_uses,
        "max_uses_per_user": req.max_uses_per_user,
        "uses": 0,
        "expiry_date": req.expiry_date,
        "is_active": req.is_active,
        "description": req.description or "",
        "service_area_id": req.service_area_id or None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    await db_supabase.insert_one("promotions", promo)
    return {"success": True, "promo": promo}


@admin_router.put("/{promo_id}")
async def admin_update_promo_code(promo_id: str, req: UpdatePromoCodeRequest):
    """Update an existing promo code."""
    update_data: Dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
    for field in [
        "free_ride",
        "discount_type",
        "discount_value",
        "max_discount",
        "max_uses",
        "max_uses_per_user",
        "expiry_date",
        "is_active",
        "description",
        "service_area_id",
    ]:
        val = getattr(req, field)
        if val is not None:
            update_data[field] = val

    await db_supabase.update_one("promotions", {"id": promo_id}, update_data)
    updated = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("promotions", {"id": promo_id}, limit=1))
    if not updated:
        raise HTTPException(status_code=404, detail="Promo code not found")
    return updated


@admin_router.delete("/{promo_id}")
async def admin_delete_promo_code(promo_id: str):
    """Delete a promo code."""
    await db_supabase.delete_one("promotions", {"id": promo_id})
    return {"deleted": True}
