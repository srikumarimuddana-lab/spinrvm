"""Ride repository — ride CRUD, admin enrichment, flags, complaints, lost-and-found.

Extracted from db_supabase.py (Phase 4 of god-object decomposition).
"""

import asyncio
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from ._base import (
        _rows_from_res,
        _serialize_for_api,
        _single_row_from_res,
        _write_skipped,
        run_sync,
        supabase,
    )
except ImportError:
    from repositories._base import (  # type: ignore
        _rows_from_res,
        _serialize_for_api,
        _single_row_from_res,
        _write_skipped,
        run_sync,
        supabase,
    )


# ============ Ride Helpers ============

_PRIVATE_ROUTE_SNAPSHOT_BUCKET = "ride-route-snapshots"
_PRIVATE_ROUTE_SNAPSHOT_TTL_SECONDS = 15 * 60
_PUBLIC_ROUTE_PROVIDERS = {"osrm_match", "google_roads", "observed_fallback", "osrm_inferred"}
_PUBLIC_ROUTE_KINDS = {"observed", "inferred"}
_PUBLIC_ROUTE_GAP_REASONS = {"missing_start", "internal_gap", "missing_tail"}


def _safe_route_segments(raw_segments: Any) -> list[dict]:
    """Allowlist display geometry and provenance from stored JSON sections."""
    if not isinstance(raw_segments, list):
        return []
    projected: list[dict] = []
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, dict):
            continue
        coordinates = []
        for raw_coordinate in raw_segment.get("coordinates") or []:
            if not isinstance(raw_coordinate, (list, tuple)) or len(raw_coordinate) < 2:
                coordinates = []
                break
            try:
                lat = float(raw_coordinate[0])
                lng = float(raw_coordinate[1])
            except (TypeError, ValueError):
                coordinates = []
                break
            if not -90 <= lat <= 90 or not -180 <= lng <= 180:
                coordinates = []
                break
            coordinates.append([lat, lng])
        if not coordinates:
            continue

        section: dict = {"coordinates": coordinates}
        provider = raw_segment.get("provider")
        geometry_kind = raw_segment.get("geometry_kind")
        gap_reason = raw_segment.get("gap_reason")
        phase = raw_segment.get("phase")
        if phase in ("navigating_to_pickup", "arrived_at_pickup", "trip_in_progress"):
            section["phase"] = phase
        if provider in _PUBLIC_ROUTE_PROVIDERS:
            section["provider"] = provider
        if geometry_kind in _PUBLIC_ROUTE_KINDS:
            section["geometry_kind"] = geometry_kind
        if geometry_kind == "inferred" and gap_reason in _PUBLIC_ROUTE_GAP_REASONS:
            section["gap_reason"] = gap_reason
        projected.append(section)
    return projected


async def create_route_snapshot_signed_url(object_path: str) -> str:
    """Create a short-lived URL for one private route image.

    Callers must authorize access to the ride before invoking this helper. The
    durable database row stores only the object path, never a bearer URL.
    """
    if not isinstance(object_path, str) or not object_path.strip():
        raise ValueError("route snapshot object path is required")
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    response = await run_sync(
        lambda: supabase.storage.from_(_PRIVATE_ROUTE_SNAPSHOT_BUCKET).create_signed_url(
            object_path, _PRIVATE_ROUTE_SNAPSHOT_TTL_SECONDS
        )
    )
    if isinstance(response, dict):
        signed_url = response.get("signedURL") or response.get("signedUrl")
    else:
        signed_url = getattr(response, "signedURL", None) or getattr(response, "signed_url", None)
    if not isinstance(signed_url, str) or not signed_url:
        raise RuntimeError("Supabase Storage did not return a route snapshot signed URL")
    return signed_url


async def _driver_profile_image(user_id: Optional[str]) -> str:
    """Driver avatar = the driver's users.profile_image (base64). Empty if none.

    The drivers table has no photo column; the photo is on the linked user row.
    """
    if not user_id or not supabase:
        return ""
    row = await run_sync(
        lambda uid=user_id: _single_row_from_res(
            supabase.table("users").select("profile_image").eq("id", uid).execute()
        )
    )
    return (row or {}).get("profile_image", "") or ""


async def _project_route_detail(
    ride: Dict[str, Any], route: Dict[str, Any], *, include_pickup_leg: bool = False
) -> None:
    """Attach a safe, display-ready route projection to one authorized detail.

    Version 2 geometry is intentionally segmented.  Consumers must never join
    its arrays: each boundary represents a gap or a separate capture session.
    Legacy fields remain available only for pre-v2 route rows while historic
    receipts are within their retention window.
    """
    schema_version = int(route.get("route_schema_version") or 1)
    if schema_version >= 2:
        # Do not let an old denormalized column present a misleading continuous
        # line beside the v2 segmented route.
        for key in ("road_polyline", "road_polyline_pickup", "phase_polylines"):
            ride.pop(key, None)
        stored_segments = route.get("road_matched_segments") or route.get("observed_segments") or []
        safe_segments = _safe_route_segments(stored_segments)
        if not include_pickup_leg:
            # Rider/driver surfaces keep the 2026-07-20 actual-route-only
            # contract: the passenger trip only. Untagged segments predate
            # phase tagging and are P3 by construction. Admin detail passes
            # include_pickup_leg=True to also see the Period-2 pickup leg.
            safe_segments = [s for s in safe_segments if s.get("phase") in (None, "trip_in_progress")]
        ride["actual_route_segments"] = safe_segments
        # Surface the measured-distance basis (from ride_metrics) into
        # route_quality so the shared label can show "estimated from booking"
        # when GPS was too incomplete to trust — without a separate client field.
        _quality = dict(route.get("route_quality") or {})
        _trip_metrics = ((ride.get("ride_metrics") or {}).get("phases") or {}).get("trip_in_progress") or {}
        _basis = _trip_metrics.get("distance_basis")
        if _basis is not None:
            _quality.setdefault("distance_basis", _basis)
        ride["route_quality"] = _quality
        ride["route_schema_version"] = schema_version
        ride["route_revision"] = int(route.get("route_revision") or 0)
        ride["route_geometry_status"] = route.get("processing_status") or "pending"

        # The completion fix is useful as an authorized map marker, but its
        # session, sequence, timestamp, accuracy, and integrity metadata stay
        # private. It is a guardrail marker, never substitute route geometry.
        completion = route.get("completion_point")
        if isinstance(completion, dict):
            try:
                completion_lat = float(completion["lat"])
                completion_lng = float(completion["lng"])
            except (KeyError, TypeError, ValueError):
                completion_lat = completion_lng = None
            if (
                completion_lat is not None
                and completion_lng is not None
                and -90 <= completion_lat <= 90
                and -180 <= completion_lng <= 180
            ):
                ride["actual_completion_point"] = {
                    "latitude": completion_lat,
                    "longitude": completion_lng,
                }

        snapshot_revision = int(route.get("snapshot_revision") or 0)
        ride["snapshot_revision"] = snapshot_revision
        object_path = route.get("snapshot_object_path")
        if snapshot_revision == ride["route_revision"] and object_path:
            try:
                ride["route_snapshot_url"] = await create_route_snapshot_signed_url(str(object_path))
            except Exception:
                # A transient Storage/signing failure must degrade only the route
                # thumbnail, never fail the whole authorized ride read (rider
                # receipt / admin detail). The segmented geometry above already
                # conveys the route; the signed image is best-effort.
                logger.exception("route snapshot signing failed for ride_id={}", ride.get("id"))
                ride.pop("route_snapshot_url", None)
        else:
            ride.pop("route_snapshot_url", None)
        return

    # Legacy rows retain the shape existing admin views and historical
    # receipts understand; new routes never fall back to these fields.
    ride["road_polyline"] = route.get("road_polyline") or []
    ride["road_polyline_pickup"] = route.get("road_polyline_pickup") or []
    for key in ("phase_polylines", "phase_distances", "phase_durations", "route_quality"):
        if route.get(key):
            ride[key] = route[key]
    ride["route_geometry_status"] = route.get("save_status") or ride.get("route_geometry_status")
    ride["route_geometry_error"] = route.get("save_error") or ride.get("route_geometry_error")


async def get_ride(ride_id: str, *, include_route: bool = False) -> Optional[Dict[str, Any]]:
    """Read a ride, optionally adding its lightweight, versioned route detail.

    Lists deliberately keep ``include_route`` false to avoid loading geometry
    for every card.  The authorized ``GET /rides/{ride_id}`` endpoint opts in
    after it performs its rider/driver ownership check.
    """
    if not supabase:
        return None
    ride = await run_sync(
        lambda: _single_row_from_res(
            supabase.table("rides").select("*").eq("id", ride_id).is_("deleted_at", "null").execute()
        )
    )
    if not ride or not include_route:
        return ride

    route = await run_sync(
        lambda: _single_row_from_res(supabase.table("ride_routes").select("*").eq("ride_id", ride_id).execute())
    )
    if route:
        # rider_show_pickup_leg_enabled (default off) extends the rider/driver
        # detail with the observed Period-2 pickup leg. Display-only: fares,
        # distance stats, and the P3 main pipeline are untouched; clients
        # render non-trip phases dashed. Settings failure → flag off (the
        # 2026-07-20 actual-route-only contract stays the safe default).
        include_pickup_leg = False
        try:
            try:
                from ..settings_loader import get_app_settings  # type: ignore
            except ImportError:
                from settings_loader import get_app_settings  # type: ignore
            include_pickup_leg = bool(((await get_app_settings()) or {}).get("rider_show_pickup_leg_enabled", False))
        except Exception:
            # Fail-safe direction: trip-only contract. Still ERROR with the
            # underlying exception — a settings read failing on the ride
            # detail path must surface loudly (CLAUDE.md: never silently swallow).
            logger.exception("rider pickup-leg flag read failed; defaulting off")
        await _project_route_detail(ride, route, include_pickup_leg=include_pickup_leg)
    return ride


async def insert_ride(payload: Dict[str, Any]):
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    payload = _serialize_for_api(payload)
    # PIPEDA: never log the full payload — it carries raw GPS (pickup/dropoff
    # lat/lng) and exact addresses. Log only the field names present.
    logger.info(f"[DEBUG-INSERT-RIDE] payload keys: {sorted(payload.keys())}")
    try:
        result = await run_sync(lambda: _single_row_from_res(supabase.table("rides").insert(payload).execute()))
        logger.info(f"[DEBUG-INSERT-RIDE] SUCCESS ride_id={payload.get('id')}")
        return result
    except Exception as e:
        logger.error(f"[DEBUG-INSERT-RIDE] FAILED: {e}")
        raise


async def update_ride(ride_id: str, updates: Dict[str, Any]):
    if not supabase:
        _write_skipped("update_ride", "rides")
        return None
    # Strip MongoDB-style $set wrapper if present
    updates = updates.get("$set", updates)
    updates = _serialize_for_api(updates)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("rides").update(updates).eq("id", ride_id).execute())
    )


async def claim_ride_payment_processing(ride_id: str) -> bool:
    """Atomically transition payment_status from 'pending' to 'processing'.

    Returns True if this caller claimed the row (proceed to call Stripe).
    Returns False if another concurrent request already claimed it (return 409).
    Raises if Supabase is unreachable.
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    def _fn() -> bool:
        res = (
            supabase.table("rides")
            .update({"payment_status": "processing"})
            .eq("id", ride_id)
            .eq("payment_status", "pending")
            .execute()
        )
        rows = _rows_from_res(res)
        return len(rows) > 0

    return await run_sync(_fn)


async def get_rides_for_user(rider_id: str, limit: int = 100):
    if not supabase:
        return []
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table("rides")
            .select("*")
            .eq("rider_id", rider_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
    )


async def get_rides_for_driver(
    driver_id: str,
    statuses: Optional[List[str]] = None,
    limit: int = 100,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
):
    if not supabase:
        return []

    def _fn():
        q = supabase.table("rides").select("*").eq("driver_id", driver_id)
        if statuses:
            status_filters = ",".join([f"status.eq.{s}" for s in statuses])
            q = q.or_(status_filters)
        if from_date:
            q = q.gte("created_at", from_date)
        if to_date:
            q = q.lt("created_at", to_date)
        q = q.order("created_at", desc=True).limit(limit)
        return _rows_from_res(q.execute())

    return await run_sync(_fn)


# ============ Rides Admin Dashboard – New Helpers ============


async def get_ride_count_by_date_range(start_iso: str, end_iso: str) -> int:
    """Count rides created within a date range using Supabase SDK."""
    if not supabase:
        return 0

    def _fn():
        res = (
            supabase.table("rides")
            .select("id", count="exact")
            .limit(1)
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .execute()
        )
        if hasattr(res, "count") and res.count is not None:
            return int(res.count)
        return 0

    return await run_sync(_fn)


async def get_ride_details_enriched(ride_id: str) -> Optional[Dict[str, Any]]:
    """Get a ride with enriched rider/driver details, flags, complaints, lost items."""
    if not supabase:
        return None

    def _get_ride():
        return _single_row_from_res(supabase.table("rides").select("*").eq("id", ride_id).execute())

    ride = await run_sync(_get_ride)
    if not ride:
        return None

    rider_id = ride.get("rider_id")
    driver_id = ride.get("driver_id")

    # --- Batch 1: all queries that depend only on rider_id / driver_id / ride_id ---

    async def _fetch_rider():
        if not rider_id:
            return None
        return await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("users")
                .select("first_name,last_name,phone,email,profile_image,status,created_at")
                .eq("id", rid)
                .execute()
            )
        )

    async def _fetch_rider_area():
        if not rider_id:
            return None
        return await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("rides")
                .select("service_area_id")
                .eq("rider_id", rid)
                .neq("service_area_id", "null")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
        )

    async def _fetch_rider_count():
        if not rider_id:
            return None
        return await run_sync(
            lambda rid=rider_id: (
                supabase.table("rides").select("id", count="exact").limit(1).eq("rider_id", rid).execute()
            )
        )

    async def _fetch_driver():
        if not driver_id:
            return None
        return await run_sync(
            lambda did=driver_id: _single_row_from_res(
                supabase.table("drivers")
                .select(
                    "user_id,driver_code,name,phone,vehicle_make,vehicle_model,vehicle_color,vehicle_year,vehicle_vin,license_plate,rating,status,photo_url,vehicle_type_id,total_rides,service_area_id"
                )
                .eq("id", did)
                .execute()
            )
        )

    async def _fetch_driver_completed():
        if not driver_id:
            return None
        return await run_sync(
            lambda did=driver_id: (
                supabase.table("rides")
                .select("id", count="exact")
                .limit(1)
                .eq("driver_id", did)
                .eq("status", "completed")
                .execute()
            )
        )

    async def _fetch_driver_total():
        if not driver_id:
            return None
        return await run_sync(
            lambda did=driver_id: (
                supabase.table("rides").select("id", count="exact").limit(1).eq("driver_id", did).execute()
            )
        )

    async def _fetch_rider_flags():
        if not rider_id:
            return []
        return await run_sync(
            lambda rid=rider_id: _rows_from_res(
                supabase.table("flags")
                .select("*")
                .eq("target_type", "rider")
                .eq("target_id", rid)
                .eq("is_active", True)
                .order("created_at", desc=True)
                .execute()
            )
        )

    async def _fetch_driver_flags():
        if not driver_id:
            return []
        return await run_sync(
            lambda did=driver_id: _rows_from_res(
                supabase.table("flags")
                .select("*")
                .eq("target_type", "driver")
                .eq("target_id", did)
                .eq("is_active", True)
                .order("created_at", desc=True)
                .execute()
            )
        )

    async def _fetch_complaints():
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("complaints").select("*").eq("ride_id", rid).order("created_at", desc=True).execute()
            )
        )

    async def _fetch_lost_items():
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("lost_and_found").select("*").eq("ride_id", rid).order("created_at", desc=True).execute()
            )
        )

    async def _fetch_location_trail():
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("driver_location_history")
                .select("lat,lng,speed,heading,accuracy,altitude,tracking_phase,timestamp,received_at")
                .eq("ride_id", rid)
                .order("timestamp")
                .limit(5000)
                .execute()
            )
        )

    async def _fetch_offers():
        # The per-ride dispatch funnel: which drivers were offered this ride
        # and what each did (accepted / declined / expired==ignored / pending).
        return await run_sync(
            lambda rid=ride_id: _rows_from_res(
                supabase.table("ride_offers")
                .select("driver_id,status,eta_seconds,offered_at,responded_at")
                .eq("ride_id", rid)
                .order("offered_at")
                .execute()
            )
        )

    (
        rider,
        rider_area,
        rider_count_res,
        driver,
        driver_completed_res,
        driver_total_assigned_res,
        rider_flags,
        driver_flags,
        ride_complaints,
        ride_lost_items,
        ride_location_trail,
        ride_offers,
    ) = await asyncio.gather(
        _fetch_rider(),
        _fetch_rider_area(),
        _fetch_rider_count(),
        _fetch_driver(),
        _fetch_driver_completed(),
        _fetch_driver_total(),
        _fetch_rider_flags(),
        _fetch_driver_flags(),
        _fetch_complaints(),
        _fetch_lost_items(),
        _fetch_location_trail(),
        _fetch_offers(),
    )

    # --- Batch 2: lookups that depend on batch 1 results ---
    rider_area_id = rider_area.get("service_area_id") if rider_area else None
    driver_area_id = driver.get("service_area_id") if driver else None
    vtype_id = driver.get("vehicle_type_id") if driver else None

    async def _fetch_service_area(area_id):
        if not area_id:
            return None
        return await run_sync(
            lambda aid=area_id: _single_row_from_res(
                supabase.table("service_areas").select("name,city").eq("id", aid).execute()
            )
        )

    async def _fetch_vehicle_type(vid):
        if not vid:
            return None
        return await run_sync(
            lambda v=vid: _single_row_from_res(
                supabase.table("vehicle_types").select("name,description,capacity").eq("id", v).execute()
            )
        )

    area, d_area, vtype = await asyncio.gather(
        _fetch_service_area(rider_area_id),
        _fetch_service_area(driver_area_id),
        _fetch_vehicle_type(vtype_id),
    )

    # --- Assemble rider fields ---
    if rider_id and rider:
        ride["rider_name"] = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or rider_id[:12]
        ride["rider_phone"] = rider.get("phone", "")
        ride["rider_email"] = rider.get("email", "")
        ride["rider_profile_image"] = rider.get("profile_image", "")
        ride["rider_status"] = rider.get("status", "active")
        ride["rider_joined"] = rider.get("created_at", "")

    if rider_id:
        ride["rider_region"] = area.get("name", "") if area else ""
        ride["rider_city"] = area.get("city", "") if area else ""
        ride["rider_total_rides"] = (
            int(rider_count_res.count)
            if rider_count_res is not None and hasattr(rider_count_res, "count") and rider_count_res.count is not None
            else 0
        )

    # --- Assemble driver fields ---
    if driver_id and driver:
        ride["driver_code"] = driver.get("driver_code", "")
        ride["driver_name"] = driver.get("name", driver_id[:12])
        ride["driver_phone"] = driver.get("phone", "")
        ride["driver_vehicle_make"] = driver.get("vehicle_make", "")
        ride["driver_vehicle_model"] = driver.get("vehicle_model", "")
        ride["driver_vehicle_color"] = driver.get("vehicle_color", "")
        ride["driver_vehicle_year"] = driver.get("vehicle_year")
        ride["driver_vehicle_vin"] = driver.get("vehicle_vin", "")
        ride["driver_license_plate"] = driver.get("license_plate", "")
        ride["driver_rating"] = driver.get("rating", 0)
        ride["driver_status"] = driver.get("status", "active")
        # Driver photo lives on the user row (users.profile_image), not the
        # non-existent drivers.photo_url column.
        ride["driver_photo_url"] = await _driver_profile_image(driver.get("user_id"))
        ride["driver_region"] = d_area.get("name", "") if d_area else ""
        ride["driver_city"] = d_area.get("city", "") if d_area else ""
        ride["driver_vehicle"] = f"{driver.get('vehicle_make', '')} {driver.get('vehicle_model', '')}".strip()
        ride["driver_total_rides"] = driver.get("total_rides", 0)

        if vtype:
            ride["driver_vehicle_type_name"] = vtype.get("name", "")
            ride["driver_vehicle_capacity"] = vtype.get("capacity", 0)

        completed = (
            int(driver_completed_res.count)
            if driver_completed_res is not None
            and hasattr(driver_completed_res, "count")
            and driver_completed_res.count is not None
            else 0
        )
        total_assigned = (
            int(driver_total_assigned_res.count)
            if driver_total_assigned_res is not None
            and hasattr(driver_total_assigned_res, "count")
            and driver_total_assigned_res.count is not None
            else 0
        )
        ride["driver_acceptance_rate"] = round((completed / total_assigned * 100), 1) if total_assigned > 0 else 0
        ride["driver_completed_rides"] = completed

    # --- Flags ---
    flags = [{**f, "_party": "rider"} for f in rider_flags] + [{**f, "_party": "driver"} for f in driver_flags]
    ride["flags"] = flags
    ride["rider_flag_count"] = sum(1 for f in flags if f.get("_party") == "rider")
    ride["driver_flag_count"] = sum(1 for f in flags if f.get("_party") == "driver")

    # --- Ride-level data ---
    ride["complaints"] = ride_complaints
    ride["lost_and_found"] = ride_lost_items
    ride["location_trail"] = ride_location_trail

    # --- Offer funnel (which drivers were offered this ride and their reply) ---
    if ride_offers:
        offer_driver_ids = list({o.get("driver_id") for o in ride_offers if o.get("driver_id")})
        offer_drivers = (
            await run_sync(
                lambda ids=offer_driver_ids: _rows_from_res(
                    supabase.table("drivers").select("id,name,rating,user_id").in_("id", ids).execute()
                )
            )
            if offer_driver_ids
            else []
        )
        offer_driver_map = {d["id"]: d for d in offer_drivers if d.get("id")}
        for o in ride_offers:
            d = offer_driver_map.get(o.get("driver_id"))
            o["driver_name"] = (d.get("name") if d else None) or (o.get("driver_id") or "")[:12]
            o["driver_rating"] = d.get("rating") if d else None
    ride["offers"] = ride_offers

    # --- Driver incentives/bonuses paid on this ride (0% commission model:
    #     these are platform-funded bonuses on top of the 100% fare) ---
    def _get_incentive_claims():
        claims = _rows_from_res(
            supabase.table("ride_incentive_claims")
            .select("incentive_id,bonus_amount,claimed_at")
            .eq("ride_id", ride_id)
            .order("claimed_at")
            .execute()
        )
        if claims:
            inc_ids = list({c.get("incentive_id") for c in claims if c.get("incentive_id")})
            if inc_ids:
                names = _rows_from_res(
                    supabase.table("ride_incentives").select("id,name,incentive_type").in_("id", inc_ids).execute()
                )
                name_map = {n["id"]: n for n in names if n.get("id")}
                for c in claims:
                    meta = name_map.get(c.get("incentive_id"))
                    c["name"] = (meta.get("name") if meta else None) or "Incentive"
                    c["incentive_type"] = meta.get("incentive_type") if meta else None
        return claims

    try:
        incentive_claims = await run_sync(_get_incentive_claims)
    except Exception:
        incentive_claims = []
    ride["incentive_claims"] = incentive_claims
    ride["incentive_total"] = round(sum(float(c.get("bonus_amount") or 0) for c in incentive_claims), 2)

    # --- Route geometry (ride_routes side-table; off the hot rides row) ---
    # Reuse the public detail projection so rider, driver, receipt, and admin
    # surfaces receive the exact same versioned geometry contract. In
    # particular, v2 route segments must never be collapsed into a continuous
    # legacy polyline for a support or dispute review.
    def _get_route():
        return _single_row_from_res(supabase.table("ride_routes").select("*").eq("ride_id", ride_id).execute())

    route = await run_sync(_get_route)
    if route:
        # Admin/dispute review sees the Period-2 pickup leg too; rider/driver
        # projections (get_ride) stay passenger-trip-only.
        await _project_route_detail(ride, route, include_pickup_leg=True)

    return ride


async def create_flag(flag_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a flag and check if auto-ban threshold (3) is reached."""
    if not supabase:
        raise RuntimeError("Supabase client not configured")

    flag_data = _serialize_for_api(flag_data)

    # Insert flag
    flag = await run_sync(lambda: _single_row_from_res(supabase.table("flags").insert(flag_data).execute()))

    # Count active flags for this target
    target_type = flag_data["target_type"]
    target_id = flag_data["target_id"]

    count_res = await run_sync(
        lambda: (
            supabase.table("flags")
            .select("id", count="exact")
            .limit(1)
            .eq("target_type", target_type)
            .eq("target_id", target_id)
            .eq("is_active", True)
            .execute()
        )
    )
    active_count = int(count_res.count) if hasattr(count_res, "count") and count_res.count is not None else 0

    auto_banned = False
    if active_count >= 3:
        ban_table = "users" if target_type == "rider" else "drivers"
        await run_sync(lambda: supabase.table(ban_table).update({"status": "banned"}).eq("id", target_id).execute())
        auto_banned = True

    return {"flag": flag, "active_flag_count": active_count, "auto_banned": auto_banned}


async def create_complaint(complaint_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Insert a complaint record."""
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    complaint_data = _serialize_for_api(complaint_data)
    return await run_sync(lambda: _single_row_from_res(supabase.table("complaints").insert(complaint_data).execute()))


async def resolve_complaint(complaint_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Resolve or dismiss a complaint."""
    if not supabase:
        _write_skipped("resolve_complaint", "complaints")
        return None
    update_data = _serialize_for_api(update_data)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("complaints").update(update_data).eq("id", complaint_id).execute())
    )


async def create_lost_and_found(item_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Insert a lost and found report."""
    if not supabase:
        raise RuntimeError("Supabase client not configured")
    item_data = _serialize_for_api(item_data)
    return await run_sync(lambda: _single_row_from_res(supabase.table("lost_and_found").insert(item_data).execute()))


async def update_lost_and_found(item_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Update a lost and found item status."""
    if not supabase:
        _write_skipped("update_lost_and_found", "lost_and_found")
        return None
    update_data = _serialize_for_api(update_data)
    return await run_sync(
        lambda: _single_row_from_res(supabase.table("lost_and_found").update(update_data).eq("id", item_id).execute())
    )


async def get_ride_location_trail(ride_id: str) -> List[Dict[str, Any]]:
    """Get driver location trail for a specific ride."""
    if not supabase:
        return []
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table("driver_location_history")
            .select("lat,lng,speed,heading,accuracy,altitude,tracking_phase,timestamp,received_at")
            .eq("ride_id", ride_id)
            .order("timestamp")
            .limit(5000)
            .execute()
        )
    )


async def get_live_ride_data(ride_id: str) -> Optional[Dict[str, Any]]:
    """Get live ride data including current driver location."""
    if not supabase:
        return None

    ride = await run_sync(lambda: _single_row_from_res(supabase.table("rides").select("*").eq("id", ride_id).execute()))
    if not ride:
        return None

    driver_id = ride.get("driver_id")
    if driver_id:
        driver = await run_sync(
            lambda did=driver_id: _single_row_from_res(
                supabase.table("drivers")
                .select(
                    "user_id,name,phone,lat,lng,vehicle_make,vehicle_model,vehicle_color,license_plate,rating,photo_url"
                )
                .eq("id", did)
                .execute()
            )
        )
        if driver:
            ride["driver_current_lat"] = driver.get("lat", 0)
            ride["driver_current_lng"] = driver.get("lng", 0)
            ride["driver_name"] = driver.get("name", "")
            ride["driver_phone"] = driver.get("phone", "")
            ride["driver_vehicle"] = f"{driver.get('vehicle_make', '')} {driver.get('vehicle_model', '')}".strip()
            ride["driver_license_plate"] = driver.get("license_plate", "")
            ride["driver_rating"] = driver.get("rating", 0)
            ride["driver_photo_url"] = await _driver_profile_image(driver.get("user_id"))

    rider_id = ride.get("rider_id")
    if rider_id:
        rider = await run_sync(
            lambda rid=rider_id: _single_row_from_res(
                supabase.table("users").select("first_name,last_name,phone").eq("id", rid).execute()
            )
        )
        if rider:
            ride["rider_name"] = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip()
            ride["rider_phone"] = rider.get("phone", "")

    return ride


async def get_user_status(user_id: str) -> Optional[str]:
    """Get user account status (active/suspended/banned)."""
    if not supabase:
        return None
    user = await run_sync(
        lambda: _single_row_from_res(supabase.table("users").select("status").eq("id", user_id).execute())
    )
    return user.get("status", "active") if user else None


async def get_flags_for_target(target_type: str, target_id: str) -> List[Dict[str, Any]]:
    """Get all active flags for a rider or driver."""
    if not supabase:
        return []
    return await run_sync(
        lambda: _rows_from_res(
            supabase.table("flags")
            .select("*")
            .eq("target_type", target_type)
            .eq("target_id", target_id)
            .eq("is_active", True)
            .order("created_at", desc=True)
            .execute()
        )
    )
