import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...routes.fares import invalidate_fare_cache
    from ...services.fare_service import DEFAULT_FARE
    from ...utils.audit_logger import log_admin_action
    from ...utils.surge_engine import SURGE_CAP
except ImportError:
    import db_supabase
    from dependencies import get_admin_user  # noqa: F401
    from routes.fares import invalidate_fare_cache
    from services.fare_service import DEFAULT_FARE
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.surge_engine import SURGE_CAP

logger = logging.getLogger(__name__)

router = APIRouter()

_SURGE_MAX = 10.0  # absolute ceiling for manual admin override

# Airport zones must be small. The actual airport property is typically
# under ~3 km on either dimension (YQR ≈ 2 km, even YVR ≈ 3 km). 10 km
# is a generous ceiling that still blocks "Regina city limits accidentally
# saved with is_airport=True" — the failure mode that surcharges every ride.
_AIRPORT_MAX_BBOX_KM = 10.0


def _polygon_bbox_km(polygon: Any) -> Optional[Dict[str, float]]:
    """Compute a coarse bounding-box size (km) for a polygon.

    Returns None when the polygon can't be parsed as a sequence of
    {lat,lng} dicts. Uses a haversine-on-axes approximation — exact enough
    to reject "city-sized airport zones" by an order of magnitude. The
    canonical polygon parser lives in geo_utils; we duplicate the shape
    sniff here to keep the admin validation self-contained.
    """
    try:
        from ...geo_utils import get_service_area_polygon
    except ImportError:
        from geo_utils import get_service_area_polygon  # type: ignore
    parsed = get_service_area_polygon({"polygon": polygon})
    if len(parsed) < 3:
        return None
    lats = [p["lat"] for p in parsed]
    lngs = [p["lng"] for p in parsed]
    lat_span_km = (max(lats) - min(lats)) * 111.0  # 1° lat ≈ 111 km
    import math

    mean_lat = (max(lats) + min(lats)) / 2.0
    lng_span_km = (max(lngs) - min(lngs)) * 111.0 * math.cos(math.radians(mean_lat))
    return {"lat_km": lat_span_km, "lng_km": lng_span_km}


def _guard_airport_polygon(is_airport: bool, polygon: Any) -> None:
    """Reject an airport zone whose polygon spans more than _AIRPORT_MAX_BBOX_KM.

    Real-world failure mode this prevents: an admin checks "is_airport"
    on the city-wide Regina service area row, leaving its city-sized
    polygon — which causes calculate_airport_fee to match every Regina
    pickup/dropoff and surcharge every ride.
    """
    if not is_airport or not polygon:
        return
    bbox = _polygon_bbox_km(polygon)
    if bbox is None:
        return  # malformed polygon — let the existing shape validation handle it
    if bbox["lat_km"] > _AIRPORT_MAX_BBOX_KM or bbox["lng_km"] > _AIRPORT_MAX_BBOX_KM:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Airport polygon is too large "
                f"(bbox {bbox['lat_km']:.1f} km × {bbox['lng_km']:.1f} km, "
                f"max {_AIRPORT_MAX_BBOX_KM} km per side). "
                "Either redraw the polygon to cover only the actual airport "
                "property, or uncheck 'is_airport' on this service area."
            ),
        )


def _guard_airport_is_subregion(is_airport: bool, parent_service_area_id: Optional[str]) -> None:
    """Reject is_airport=true on a top-level row.

    The data model is parent / child: a service area like Regina is a
    top-level row (parent_service_area_id NULL); an airport zone like
    YQR is a CHILD row under Regina. Letting a top-level row carry
    is_airport=true is what produced the "every Regina ride gets the
    surcharge" report — the city-sized polygon then matches every
    pickup/dropoff.

    The runtime helpers (calculate_airport_fee, calculate_all_fees)
    already ignore top-level rows for the airport check; this guard
    keeps the data shape consistent so a future regression can't
    re-introduce the bug.
    """
    if is_airport and not parent_service_area_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "is_airport may only be set on a sub-region (a service area "
                "with a parent_service_area_id). Top-level service areas "
                "cannot be airport zones — create the airport as a child "
                "row under its parent service area instead."
            ),
        )


def _coerce_airport_fields(is_airport: Optional[bool], airport_fee: Optional[float]) -> Dict[str, Any]:
    """Keep `is_airport` and `airport_fee` mutually consistent on writes.

      * is_airport=false  → airport_fee must be 0
      * airport_fee == 0  → is_airport must be false

    Caller passes the values being written (None when unchanged). Returns
    overrides to fold into the update payload. This makes "the admin
    deletes the airport fee" automatically also turn off the zone, which
    matches what an operator means when they zero the amount.
    """
    overrides: Dict[str, Any] = {}
    if is_airport is False:
        overrides["airport_fee"] = 0
    if airport_fee is not None and float(airport_fee) == 0 and is_airport is not False:
        # Treat explicit fee=0 as "this is no longer an airport zone" — also
        # flip the flag off so future queries skip the row cleanly.
        overrides["is_airport"] = False
    return overrides


# ---------- Pydantic models ----------


class ServiceAreaCreateRequest(BaseModel):
    name: str
    city: str = ""
    # province + regulatory_* are sent by the admin create form (migration 223).
    # They must be declared here or Pydantic silently drops them and new areas
    # land with NULL regulatory metadata despite the admin filling the form.
    province: Optional[str] = None
    regulatory_authority: Optional[str] = None
    regulatory_region: Optional[str] = None
    regulatory_requirements_url: Optional[str] = None
    regulatory_notes: Optional[str] = None
    geojson: Optional[Any] = None
    polygon: Optional[Any] = None
    is_active: bool = True
    parent_service_area_id: Optional[str] = None
    is_airport: bool = False
    airport_fee: float = Field(default=0, ge=0, le=100)
    surge_enabled: Optional[bool] = None
    surge_active: Optional[bool] = None
    surge_multiplier: float = Field(default=1.0, ge=1.0, le=2.5)
    gst_enabled: bool = True
    gst_rate: float = Field(default=5.0, ge=0, le=100)
    pst_enabled: bool = False
    pst_rate: float = Field(default=0.0, ge=0, le=100)
    hst_enabled: bool = False
    hst_rate: float = Field(default=0.0, ge=0, le=100)
    spinr_pass_enabled: bool = True
    subscription_plan_ids: List[str] = []
    subscription_required: bool = False
    driver_matching_algorithm: str = "nearest"
    search_radius_km: float = Field(default=10.0, ge=1, le=100)
    min_driver_rating: float = Field(default=4.0, ge=1.0, le=5.0)
    max_simultaneous_offers: int = Field(default=3, ge=1, le=10)
    use_eta_ranking: bool = True
    show_demand_heatmap: bool = False
    # Per-area referral rewards (CAD). Defaults equal the global constants.
    rider_referrer_reward: float = Field(default=5, ge=0, le=1000)
    rider_referee_reward: float = Field(default=5, ge=0, le=1000)
    rider_referral_rides_required: int = Field(default=1, ge=1, le=100)
    driver_referral_reward: float = Field(default=10, ge=0, le=1000)
    # Driver signup bonus (migration 201) — off by default; an admin opts a
    # service area in by setting this above 0. Shares driver_referral_rides_required
    # with the referrer (no separate referee-side threshold).
    driver_referee_reward: float = Field(default=0, ge=0, le=1000)
    driver_referral_rides_required: int = Field(default=10, ge=1, le=100)
    # Free-text referral T&C (migration 176); blank → dynamic default sentence.
    rider_referral_terms: Optional[str] = Field(default=None, max_length=2000)
    driver_referral_terms: Optional[str] = Field(default=None, max_length=2000)
    # Dispatch cascade rules (migration 185): [{from: uuid, to: [uuid, ...]}]
    vehicle_cascade_map: List[Any] = []


class ServiceAreaUpdateRequest(BaseModel):
    name: Optional[str] = None
    city: Optional[str] = None
    geojson: Optional[Any] = None
    polygon: Optional[Any] = None
    is_active: Optional[bool] = None
    parent_service_area_id: Optional[str] = None
    is_airport: Optional[bool] = None
    airport_fee: Optional[float] = Field(default=None, ge=0, le=100)
    surge_enabled: Optional[bool] = None
    surge_active: Optional[bool] = None
    surge_multiplier: Optional[float] = Field(default=None, ge=1.0, le=10.0)
    surge_justification: Optional[str] = None
    gst_enabled: Optional[bool] = None
    gst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    pst_enabled: Optional[bool] = None
    pst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    hst_enabled: Optional[bool] = None
    hst_rate: Optional[float] = Field(default=None, ge=0, le=100)
    required_documents: Optional[Any] = None
    spinr_pass_enabled: Optional[bool] = None
    subscription_plan_ids: Optional[List[str]] = None
    subscription_required: Optional[bool] = None
    driver_matching_algorithm: Optional[str] = None
    search_radius_km: Optional[float] = Field(default=None, ge=1, le=100)
    min_driver_rating: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    max_simultaneous_offers: Optional[int] = Field(default=None, ge=1, le=10)
    use_eta_ranking: Optional[bool] = None
    show_demand_heatmap: Optional[bool] = None
    vehicle_pricing: Optional[List[Dict[str, Any]]] = None
    province: Optional[str] = None
    regulatory_authority: Optional[str] = None
    regulatory_region: Optional[str] = None
    regulatory_requirements_url: Optional[str] = None
    regulatory_notes: Optional[str] = None
    max_pickup_radius_km: Optional[float] = Field(default=None, ge=0.1, le=200)
    surge_source: Optional[str] = None
    insurance_fee_percent: Optional[float] = Field(default=None, ge=0, le=100)
    rider_cancel_fee_before_driver: Optional[float] = Field(default=None, ge=0, le=100)
    rider_cancel_fee_after_arrival: Optional[float] = Field(default=None, ge=0, le=100)
    cancel_fee_driver_share: Optional[float] = Field(default=None, ge=0, le=100)
    cancel_fee_admin_share: Optional[float] = Field(default=None, ge=0, le=100)
    driver_cancel_fee: Optional[float] = Field(default=None, ge=0, le=100)
    free_cancel_window_seconds: Optional[int] = Field(default=None, ge=0, le=3600)
    noshow_wait_seconds: Optional[int] = Field(default=None, ge=60, le=1800)
    currency: Optional[str] = None
    # Per-area referral rewards (CAD).
    rider_referrer_reward: Optional[float] = Field(default=None, ge=0, le=1000)
    rider_referee_reward: Optional[float] = Field(default=None, ge=0, le=1000)
    rider_referral_rides_required: Optional[int] = Field(default=None, ge=1, le=100)
    driver_referral_reward: Optional[float] = Field(default=None, ge=0, le=1000)
    driver_referee_reward: Optional[float] = Field(default=None, ge=0, le=1000)
    driver_referral_rides_required: Optional[int] = Field(default=None, ge=1, le=100)
    # Free-text referral T&C (migration 176); blank → dynamic default sentence.
    rider_referral_terms: Optional[str] = Field(default=None, max_length=2000)
    driver_referral_terms: Optional[str] = Field(default=None, max_length=2000)
    # Dispatch cascade rules (migration 185): [{from: uuid, to: [uuid, ...]}]
    vehicle_cascade_map: Optional[List[Any]] = None


class SurgePricingRequest(BaseModel):
    multiplier: float = Field(default=1.0, ge=1.0, le=2.5)
    is_active: bool = False


class AreaFeeCreateRequest(BaseModel):
    fee_name: str = ""
    fee_type: str = "custom"
    calc_mode: str = "flat"
    amount: float = Field(default=0, ge=0, le=100)
    description: str = ""
    conditions: Dict[str, Any] = {}
    is_active: bool = True


class AreaFeeUpdateRequest(BaseModel):
    fee_name: Optional[str] = None
    fee_type: Optional[str] = None
    calc_mode: Optional[str] = None
    amount: Optional[float] = Field(default=None, ge=0, le=100)
    description: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class AreaTaxRequest(BaseModel):
    gst_enabled: Optional[bool] = None
    gst_rate: Optional[float] = None
    pst_enabled: Optional[bool] = None
    pst_rate: Optional[float] = None
    hst_enabled: Optional[bool] = None
    hst_rate: Optional[float] = None


# ---------- Service areas (table: service_areas) ----------


@router.get("/service-areas")
async def admin_get_service_areas():
    """Get all service areas. Sub-regions are nested under their parent as 'sub_regions'."""
    areas = await db_supabase.get_rows("service_areas", order="name", limit=500)
    # Build parent -> children mapping
    parent_map: Dict[str, list] = {}
    parents = []
    for a in areas:
        pid = a.get("parent_service_area_id")
        if pid:
            parent_map.setdefault(pid, []).append(a)
        else:
            parents.append(a)
    # Attach sub_regions to each parent
    for p in parents:
        p["sub_regions"] = parent_map.get(p["id"], [])
    return parents


@router.get("/airport-zones/diagnostic", dependencies=[Depends(get_admin_user)])
async def admin_airport_zones_diagnostic():
    """List every row that could be triggering the airport surcharge.

    Returns rows where is_airport is truthy (covers boolean True AND the
    occasional string "true" / 1 written by older admin tools) plus rows
    that aren't flagged but carry a non-zero airport_fee — both shapes
    can leak into calculate_airport_fee when the column type drifts.

    For "I removed the airport zone but the surcharge still shows":
      * If `airport_zones` is empty → no live row matches; the surcharge
        you're seeing is frozen on an OLD ride row (ride.airport_fee was
        captured at creation). New rides won't be charged.
      * If `airport_zones` is non-empty → the listed row(s) still match.
        Uncheck `is_airport` on each, or delete sub-region rows that
        survived the parent removal.
    """
    rows = await db_supabase.get_rows("service_areas", limit=1000)
    out = []
    for r in rows:
        raw_flag = r.get("is_airport")
        # Treat truthy strings / ints as flagged too — surfaces type drift
        # introduced by older versions of the admin form.
        is_flagged = raw_flag in (True, 1, "true", "True", "TRUE", "1")
        fee = r.get("airport_fee") or 0
        has_fee = float(fee) > 0
        if not (is_flagged or (has_fee and r.get("parent_service_area_id"))):
            # Skip rows that are neither flagged nor a sub-region with a fee.
            # Plain service areas with an unrelated `airport_fee=0` are not
            # signal noise to surface here.
            continue
        bbox = _polygon_bbox_km(r.get("polygon"))
        too_large = bool(bbox and (bbox["lat_km"] > _AIRPORT_MAX_BBOX_KM or bbox["lng_km"] > _AIRPORT_MAX_BBOX_KM))
        parent_id = r.get("parent_service_area_id")
        is_top_level_with_flag = is_flagged and not parent_id
        out.append(
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "city": r.get("city"),
                "is_airport_raw": raw_flag,
                "is_airport_effective": is_flagged,
                "airport_fee": fee,
                "is_active": r.get("is_active"),
                "parent_service_area_id": parent_id,
                "bbox_km": bbox,
                "max_allowed_km": _AIRPORT_MAX_BBOX_KM,
                "looks_misconfigured": too_large or is_top_level_with_flag,
                "is_top_level_with_airport_flag": is_top_level_with_flag,
            }
        )

    # SECOND SURFACE: area_fees rows. A row in area_fees with fee_name
    # containing "airport" but fee_type != "airport" is the silent-fail
    # case — calculate_all_fees only gates fee_type=="airport" on
    # in_airport, so any other type applies to every ride and the
    # receipt still shows the row's fee_name verbatim. This is the
    # common cause of "I removed the airport zone but the surcharge
    # still shows" once airport_zones above is empty.
    area_fees = await db_supabase.get_rows("area_fees", {"is_active": True}, limit=500)
    suspicious_fees = []
    for fee_row in area_fees:
        name = (fee_row.get("fee_name") or "").lower()
        fee_type = (fee_row.get("fee_type") or "").lower()
        if "airport" in name and fee_type != "airport":
            suspicious_fees.append(
                {
                    "id": fee_row.get("id"),
                    "service_area_id": fee_row.get("service_area_id"),
                    "fee_name": fee_row.get("fee_name"),
                    "fee_type": fee_row.get("fee_type"),
                    "calc_mode": fee_row.get("calc_mode"),
                    "amount": fee_row.get("amount"),
                    "is_active": fee_row.get("is_active"),
                    "issue": (
                        "Fee name contains 'airport' but fee_type is not "
                        "'airport' — calculate_all_fees will NOT gate this on "
                        "the airport zone, so it applies to every ride in the "
                        "service area. Either change fee_type to 'airport' OR "
                        "rename / deactivate this row."
                    ),
                }
            )

    return {
        "airport_zones": out,
        "total_service_areas_scanned": len(rows),
        "suspicious_area_fees": suspicious_fees,
        "total_active_area_fees_scanned": len(area_fees),
    }


@router.post("/service-areas")
async def admin_create_service_area(area: ServiceAreaCreateRequest, admin: dict = Depends(get_admin_user)):
    """Create service area with full configuration."""
    polygon = area.geojson if area.geojson is not None else area.polygon or []
    _guard_airport_polygon(area.is_airport, polygon)
    _guard_airport_is_subregion(area.is_airport, area.parent_service_area_id)
    coerced = _coerce_airport_fields(area.is_airport, area.airport_fee)
    effective_is_airport = bool(coerced.get("is_airport", area.is_airport))
    effective_airport_fee = coerced.get("airport_fee", area.airport_fee)
    surge_active = area.surge_active if area.surge_active is not None else (area.surge_enabled or False)
    # Derive the surge gate from any explicit active-surge intent, not only the
    # new surge_enabled flag. Legacy admin/API clients that still send
    # surge_active=true (or a >1.0 multiplier) without surge_enabled would
    # otherwise create an area with surge_enabled=false, and fares now gate on
    # surge_enabled — silently producing a non-surging area despite the request.
    surge_enabled = (
        bool(area.surge_enabled)
        or bool(area.surge_active)
        or (area.surge_multiplier is not None and area.surge_multiplier > 1.0)
    )
    doc = {
        "id": str(uuid.uuid4()),
        "name": area.name,
        "city": area.city,
        "province": area.province,
        "regulatory_authority": area.regulatory_authority,
        "regulatory_region": area.regulatory_region,
        "regulatory_requirements_url": area.regulatory_requirements_url,
        "regulatory_notes": area.regulatory_notes,
        "polygon": polygon,
        "is_active": area.is_active,
        "parent_service_area_id": area.parent_service_area_id,
        "is_airport": effective_is_airport,
        "airport_fee": effective_airport_fee,
        "surge_enabled": surge_enabled,
        "surge_active": surge_active if surge_enabled else False,
        "surge_multiplier": area.surge_multiplier,
        "gst_enabled": area.gst_enabled,
        "gst_rate": area.gst_rate,
        "pst_enabled": area.pst_enabled,
        "pst_rate": area.pst_rate,
        "hst_enabled": area.hst_enabled,
        "hst_rate": area.hst_rate,
        "spinr_pass_enabled": area.spinr_pass_enabled,
        "subscription_plan_ids": area.subscription_plan_ids,
        "subscription_required": area.subscription_required,
        "driver_matching_algorithm": area.driver_matching_algorithm,
        "search_radius_km": area.search_radius_km,
        "min_driver_rating": area.min_driver_rating,
        "max_simultaneous_offers": area.max_simultaneous_offers,
        "use_eta_ranking": area.use_eta_ranking,
        "show_demand_heatmap": area.show_demand_heatmap,
        "rider_referrer_reward": area.rider_referrer_reward,
        "rider_referee_reward": area.rider_referee_reward,
        "rider_referral_rides_required": area.rider_referral_rides_required,
        "driver_referral_reward": area.driver_referral_reward,
        "driver_referee_reward": area.driver_referee_reward,
        "driver_referral_rides_required": area.driver_referral_rides_required,
        "rider_referral_terms": area.rider_referral_terms,
        "driver_referral_terms": area.driver_referral_terms,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Invariant: subscription_required=True ⟹ spinr_pass_enabled=True.
    # Guard on create as well as update so a direct API call with both flags
    # conflicting can't produce a locked-out area from the first insert.
    if doc.get("subscription_required"):
        doc["spinr_pass_enabled"] = True
    # Seed vehicle_pricing with all active vehicle types so every type
    # appears on the rider's ride-options screen from day one.
    if not doc.get("vehicle_pricing"):
        vt_rows = await db_supabase.get_rows("vehicle_types", {"is_active": True}, limit=100)
        doc["vehicle_pricing"] = [
            {
                "vehicle_type": vt["name"],
                "base_fare": DEFAULT_FARE["base_fare"],
                "per_km": DEFAULT_FARE["per_km_rate"],
                "per_min": DEFAULT_FARE["per_minute_rate"],
                "min_fare": DEFAULT_FARE["minimum_fare"],
                "booking_fee": DEFAULT_FARE["booking_fee"],
            }
            for vt in vt_rows
            if vt.get("name")
        ]

    await db_supabase.insert_one("service_areas", doc)
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    await log_admin_action(admin, "service_area_created", "service_areas", doc["id"], {"name": area.name})
    return {"area_id": doc["id"]}


@router.put("/service-areas/{area_id}")
async def admin_update_service_area(
    area_id: str, area: ServiceAreaUpdateRequest, admin: dict = Depends(get_admin_user)
):
    """Update service area — accepts any field."""
    # Resolve aliases
    polygon = area.geojson if area.geojson is not None else area.polygon
    surge_active = area.surge_active if area.surge_active is not None else area.surge_enabled

    # Block "city polygon flipped to airport" — the failure mode that
    # surcharges every ride in town. Resolve the effective is_airport +
    # polygon + parent by overlaying the request on the current row before
    # checking. Same lookup also enforces the sub-region requirement so a
    # top-level row cannot become an airport zone via an update.
    if area.is_airport is True or polygon is not None or area.airport_fee is not None:
        existing = await db_supabase.find_one("service_areas", {"id": area_id}) or {}
        effective_is_airport = area.is_airport if area.is_airport is not None else existing.get("is_airport", False)
        effective_polygon = polygon if polygon is not None else existing.get("polygon")
        effective_parent = (
            area.parent_service_area_id
            if area.parent_service_area_id is not None
            else existing.get("parent_service_area_id")
        )
        _guard_airport_polygon(bool(effective_is_airport), effective_polygon)
        _guard_airport_is_subregion(bool(effective_is_airport), effective_parent)

    # Validate surge_multiplier at the API boundary (F-26).
    # fare_service.py always applies SURGE_CAP (2.5×) at calculation time, so
    # values above SURGE_CAP stored here only take effect as manual overrides
    # that require documented justification per CLAUDE.md.
    #
    # Plain range validation (1.0-_SURGE_MAX) is NOT done here: it's already
    # enforced by ServiceAreaUpdateRequest's Pydantic Field(ge=1.0, le=10.0),
    # which matches _SURGE_MAX exactly, so it 422s before this handler ever
    # runs. A prior manual re-check here was unreachable dead code (#3069).
    if area.surge_multiplier is not None:
        sm = float(area.surge_multiplier)
        # Disabling surge clears the multiplier to 1.0 below, so an above-cap
        # value is being turned off, not applied. Don't trap operators behind a
        # justification requirement just to switch off a risky >2.5x surge from
        # the normal form — the justification gate only guards applying an
        # above-cap multiplier while surge stays on.
        if sm > SURGE_CAP and area.surge_enabled is not False:
            justification = (area.surge_justification or "").strip()
            if not justification:
                raise HTTPException(
                    status_code=400,
                    detail="surge_multiplier above 2.5 requires a written justification (regulatory + reputational risk)",
                )
            logger.warning(
                "surge_multiplier %.2f exceeds auto-mode cap (%.1f) for area %s — "
                "manual override with justification: %s",
                sm,
                SURGE_CAP,
                area_id,
                justification,
            )
            await log_admin_action(
                admin,
                "surge_override_above_cap",
                "service_areas",
                area_id,
                {"surge_multiplier": sm, "justification": justification},
            )

    update_payload: Dict[str, Any] = {}
    for field in [
        "name",
        "city",
        "province",
        "regulatory_authority",
        "regulatory_region",
        "regulatory_requirements_url",
        "regulatory_notes",
        "is_active",
        "parent_service_area_id",
        "is_airport",
        "airport_fee",
        "surge_multiplier",
        "surge_source",
        "gst_enabled",
        "gst_rate",
        "pst_enabled",
        "pst_rate",
        "hst_enabled",
        "hst_rate",
        "required_documents",
        "spinr_pass_enabled",
        "subscription_plan_ids",
        "subscription_required",
        "driver_matching_algorithm",
        "search_radius_km",
        "min_driver_rating",
        "max_simultaneous_offers",
        "use_eta_ranking",
        "show_demand_heatmap",
        "vehicle_pricing",
        "max_pickup_radius_km",
        "insurance_fee_percent",
        "rider_cancel_fee_before_driver",
        "rider_cancel_fee_after_arrival",
        "cancel_fee_driver_share",
        "cancel_fee_admin_share",
        "driver_cancel_fee",
        "free_cancel_window_seconds",
        "noshow_wait_seconds",
        "currency",
        "rider_referrer_reward",
        "rider_referee_reward",
        "rider_referral_rides_required",
        "driver_referral_reward",
        "driver_referee_reward",
        "driver_referral_rides_required",
        "rider_referral_terms",
        "driver_referral_terms",
        "vehicle_cascade_map",
    ]:
        val = getattr(area, field)
        if val is not None:
            update_payload[field] = val
    if polygon is not None:
        update_payload["polygon"] = polygon
    if surge_active is not None:
        update_payload["surge_active"] = surge_active

    # Invariant: subscription_required=True ⟹ spinr_pass_enabled=True.
    # Two cases to guard:
    # (a) payload sets subscription_required=True → always force spinr_pass_enabled on
    # (b) payload sets spinr_pass_enabled=False without touching subscription_required →
    #     check the existing DB row; if it already has subscription_required=True, the
    #     combination would lock drivers out, so coerce spinr_pass_enabled back to True.
    if update_payload.get("subscription_required") is True:
        update_payload["spinr_pass_enabled"] = True
    elif update_payload.get("spinr_pass_enabled") is False and "subscription_required" not in update_payload:
        _existing_area = await db_supabase.find_one("service_areas", {"id": area_id})
        if (_existing_area or {}).get("subscription_required"):
            update_payload["spinr_pass_enabled"] = True

    # Per-area surge master toggle. Persist it explicitly (it is not in the
    # allow-list above). Disabling surge must immediately clear any live surge
    # so a parked multiplier / surge_active flag can't keep pricing rides while
    # the area is "off"; the surge engine also skips disabled areas.
    if area.surge_enabled is not None:
        update_payload["surge_enabled"] = area.surge_enabled
        if area.surge_enabled is False:
            update_payload["surge_active"] = False
            update_payload["surge_multiplier"] = 1.0

    # Keep is_airport <-> airport_fee consistent: deleting the fee turns the
    # zone off; turning the zone off zeroes the fee. Operators can do either
    # one and get the same end state.
    coerced_airport = _coerce_airport_fields(area.is_airport, area.airport_fee)
    for k, v in coerced_airport.items():
        update_payload[k] = v

    if update_payload:
        # NOTE: service_areas table does not have an updated_at column in Supabase schema.
        # Adding it causes PGRST204 -> 500 error.
        await db_supabase.update_one("service_areas", {"id": area_id}, update_payload)
        # PERF-001: Invalidate fare cache
        await invalidate_fare_cache()
        await log_admin_action(
            admin,
            "service_area_updated",
            "service_areas",
            area_id,
            {"updated_fields": list(update_payload.keys())},
        )
    return {"message": "Service area updated"}


@router.delete("/service-areas/{area_id}")
async def admin_delete_service_area(area_id: str, admin: dict = Depends(get_admin_user)):
    """Delete service area."""
    await db_supabase.delete_many("service_areas", {"id": area_id})
    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    await log_admin_action(admin, "service_area_deleted", "service_areas", area_id)
    return {"message": "Service area deleted"}


# ---------- Surge Pricing ----------


@router.put("/service-areas/{area_id}/surge")
async def admin_update_surge_pricing(area_id: str, surge: SurgePricingRequest, admin: dict = Depends(get_admin_user)):
    """Update surge pricing for a service area.

    ``is_active`` is the per-area surge master toggle:
      * is_active=True  → enable surge and price at ``multiplier``
      * is_active=False → disable surge; the area reverts to 1.0× and every
        fare path / the surge engine ignores any parked multiplier.

    The authoritative write is to the ``service_areas`` row (surge_enabled /
    surge_active / surge_multiplier) — the columns every fare builder and the
    surge engine gate on. A ``surge_pricing`` row is also written for history.
    """
    # Authoritative surge state — mirror the toggle onto the columns the fare
    # builders (fare_service.py, routes/fares.py, features.py) and the surge
    # engine actually read. Without this the call only logged a history row and
    # left every ride priced at 1.0× regardless of the requested surge.
    if surge.is_active:
        area_update: Dict[str, Any] = {
            "surge_enabled": True,
            "surge_active": surge.multiplier > 1.0,
            "surge_multiplier": surge.multiplier,
        }
    else:
        area_update = {
            "surge_enabled": False,
            "surge_active": False,
            "surge_multiplier": 1.0,
        }
    await db_supabase.update_one("service_areas", {"id": area_id}, area_update)

    surge_doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "multiplier": surge.multiplier,
        "demand_count": 0,
        "supply_count": 0,
        "ratio": 0,
        "source": "manual",
        "is_active": surge.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("surge_pricing", {"service_area_id": area_id}, limit=1, columns="id")
    )
    if existing:
        await db_supabase.update_one("surge_pricing", {"service_area_id": area_id}, surge_doc)
    else:
        await db_supabase.insert_one("surge_pricing", surge_doc)

    # PERF-001: Invalidate fare cache
    await invalidate_fare_cache()
    await log_admin_action(
        admin,
        "surge_pricing_updated",
        "service_areas",
        area_id,
        {"multiplier": surge.multiplier, "is_active": surge.is_active},
    )

    return {"message": "Surge pricing updated"}


@router.get("/surge/status")
async def admin_get_surge_status():
    """Get current surge status for all active service areas."""
    try:
        from utils.surge_engine import get_surge_status
    except ImportError:
        from ...utils.surge_engine import get_surge_status

    try:
        return await get_surge_status()
    except Exception as exc:
        logger.error("Surge status fetch failed", exc_info=exc)
        raise HTTPException(status_code=503, detail="Unable to fetch surge status") from exc


# ---------- Area Management (Pricing, Tax, Vehicle Pricing) ----------


@router.get("/areas/{area_id}/fees")
async def admin_get_area_fees(area_id: str):
    """Get all fees for a service area."""
    fees = await db_supabase.get_rows("area_fees", {"service_area_id": area_id}, order="created_at", limit=100)
    return fees


@router.post("/areas/{area_id}/fees")
async def admin_create_area_fee(area_id: str, fee: AreaFeeCreateRequest):
    """Create a new fee for a service area."""
    doc = {
        "id": str(uuid.uuid4()),
        "service_area_id": area_id,
        "fee_name": fee.fee_name,
        "fee_type": fee.fee_type,
        "calc_mode": fee.calc_mode,
        "amount": fee.amount,
        "description": fee.description,
        "conditions": fee.conditions,
        "is_active": fee.is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    await db_supabase.insert_one("area_fees", doc)
    return doc


@router.put("/areas/{area_id}/fees/{fee_id}")
async def admin_update_area_fee(area_id: str, fee_id: str, fee: AreaFeeUpdateRequest):
    """Update an area fee."""
    updates: Dict[str, Any] = {}
    if fee.fee_name is not None:
        updates["fee_name"] = fee.fee_name
    if fee.fee_type is not None:
        updates["fee_type"] = fee.fee_type
    if fee.calc_mode is not None:
        updates["calc_mode"] = fee.calc_mode
    if fee.amount is not None:
        updates["amount"] = fee.amount
    if fee.description is not None:
        updates["description"] = fee.description
    if fee.conditions is not None:
        updates["conditions"] = fee.conditions
    if fee.is_active is not None:
        updates["is_active"] = fee.is_active
    if updates:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("area_fees", {"id": fee_id}, updates)
    return {"message": "Area fee updated"}


@router.delete("/areas/{area_id}/fees/{fee_id}")
async def admin_delete_area_fee(area_id: str, fee_id: str):
    """Delete an area fee."""
    await db_supabase.delete_many("area_fees", {"id": fee_id})
    return {"message": "Area fee deleted"}


@router.get("/areas/{area_id}/tax")
async def admin_get_area_tax(area_id: str):
    """Get tax configuration for a service area."""
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    if not area:
        return {
            "service_area_id": area_id,
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": False,
            "pst_rate": 0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
    return {
        "service_area_id": area_id,
        "gst_enabled": area.get("gst_enabled", True),
        "gst_rate": area.get("gst_rate", 5.0),
        "pst_enabled": area.get("pst_enabled", False),
        "pst_rate": area.get("pst_rate", 0),
        "hst_enabled": area.get("hst_enabled", False),
        "hst_rate": area.get("hst_rate", 0),
    }


@router.put("/areas/{area_id}/tax")
async def admin_update_area_tax(area_id: str, tax: AreaTaxRequest):
    """Update tax configuration for a service area."""
    updates = tax.model_dump(exclude_none=True)
    if updates:
        await db_supabase.update_one("service_areas", {"id": area_id}, updates)
    area = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("service_areas", {"id": area_id}, limit=1))
    _TAX_FIELDS = [
        "gst_enabled",
        "gst_rate",
        "pst_enabled",
        "pst_rate",
        "hst_enabled",
        "hst_rate",
    ]
    return {k: area.get(k) for k in _TAX_FIELDS}


@router.get("/areas/{area_id}/vehicle-pricing")
async def admin_get_vehicle_pricing(area_id: str):
    """Get vehicle pricing configuration for a service area.

    Returns {vehicle_types, fare_configs} so the fare-config editor can
    display a row per vehicle type with the area's specific rates.
    """
    vehicle_types = await db_supabase.get_rows("vehicle_types", {"is_active": True}, order="name", limit=50)
    fare_configs = await db_supabase.get_rows("fare_configs", {"service_area_id": area_id}, limit=100)
    return {
        "vehicle_types": vehicle_types or [],
        "fare_configs": fare_configs or [],
    }
