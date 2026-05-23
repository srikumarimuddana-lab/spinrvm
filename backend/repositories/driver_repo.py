"""Driver repository — driver lookups, location, availability, atomic claims.

Extracted from db_supabase.py (Phase 4 of god-object decomposition).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from ._base import (
        _DRIVER_BY_USER_CACHE_TTL_SECONDS,
        _DRIVER_CACHE_TTL_SECONDS,
        _driver_by_user_cache_key,
        _driver_cache_key,
        _read_cached_row,
        _rows_from_res,
        _single_row_from_res,
        _write_cached_row,
        invalidate_driver_cache,
        run_sync,
        supabase,
    )
except ImportError:
    from repositories._base import (  # type: ignore
        _DRIVER_BY_USER_CACHE_TTL_SECONDS,
        _DRIVER_CACHE_TTL_SECONDS,
        _driver_by_user_cache_key,
        _driver_cache_key,
        _read_cached_row,
        _rows_from_res,
        _single_row_from_res,
        _write_cached_row,
        invalidate_driver_cache,
        run_sync,
        supabase,
    )


# ============ Driver Helpers ============


async def get_driver_by_id(driver_id: str) -> Optional[Dict[str, Any]]:
    if not supabase:
        return None

    key = _driver_cache_key(driver_id)
    cached = await _read_cached_row(key)
    if cached is not None:
        return None if cached == {} else cached

    driver = await run_sync(
        lambda: _single_row_from_res(
            supabase.table("drivers").select("*").eq("id", driver_id).is_("deleted_at", "null").execute()
        )
    )
    await _write_cached_row(key, driver, ttl=_DRIVER_CACHE_TTL_SECONDS)
    return driver


async def get_driver_by_user_id_cached(user_id: str) -> Optional[Dict[str, Any]]:
    """Driver row for the given user_id, with a 30s Redis cache.

    Hot path: get_current_user reads this on every authenticated request
    to set user["is_driver"]. Raw Supabase look-up adds ~40-80ms per call;
    caching collapses that to ~1ms on the hit path.
    """
    if not supabase:
        return None

    key = _driver_by_user_cache_key(user_id)
    cached = await _read_cached_row(key)
    if cached is not None:
        return None if cached == {} else cached

    rows = await run_sync(
        lambda: _rows_from_res(
            supabase.table("drivers").select("*").eq("user_id", user_id).is_("deleted_at", "null").limit(1).execute()
        )
    )
    driver = rows[0] if rows else None
    await _write_cached_row(key, driver, ttl=_DRIVER_BY_USER_CACHE_TTL_SECONDS)
    return driver


async def get_service_area_for_point(lat: float, lng: float) -> Optional[Dict[str, Any]]:
    """Return the first active service area containing (lat, lng) via PostGIS RPC, or None."""
    if not supabase:
        return None

    def _call():
        res = supabase.rpc(
            "get_service_area_for_point",
            {"lat": lat, "lng": lng},
        ).execute()
        rows = _rows_from_res(res)
        return rows[0] if rows else None

    try:
        return await run_sync(_call)
    except Exception as exc:
        logger.error(f"get_service_area_for_point failed: {exc}", exc_info=True)
        return None


async def find_nearby_drivers(lat: float, lng: float, radius_meters: float) -> List[Dict[str, Any]]:
    """Use PostGIS RPC to find nearby drivers."""
    if not supabase:
        return []

    def _fn():
        res = supabase.rpc("find_nearby_drivers", {"lat": lat, "lng": lng, "radius_meters": radius_meters}).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def update_driver_location(driver_id: str, lat: float, lng: float):
    if not supabase:
        return None

    def _update():
        data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc).isoformat()}
        supabase.table("drivers").update(data).eq("id", str(driver_id)).execute()
        return True

    return await run_sync(_update)


async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        logger.warning("[GO-ONLINE] set_driver_available: supabase client is None!")
        return None

    await invalidate_driver_cache(driver_id=driver_id)

    def _update():
        payload: Dict[str, Any] = {"is_available": available}
        logger.info(
            f"[GO-ONLINE] set_driver_available CALLED driver_id={driver_id} "
            f"available={available} total_rides_inc={total_rides_inc} "
            f"payload={payload} (NOTE: only writes is_available, drops any "
            f"other fields the caller may have passed)"
        )
        if total_rides_inc == 0:
            res = supabase.table("drivers").update(payload).eq("id", driver_id).execute()
            logger.info(f"[GO-ONLINE] set_driver_available executed, res.data={getattr(res, 'data', None)}")
            return _single_row_from_res(res)

        # If increment needed, read then write (simulated atomic)
        cur = supabase.table("drivers").select("total_rides").eq("id", driver_id).execute()
        cur_data = _rows_from_res(cur)
        cur_val = cur_data[0].get("total_rides", 0) if cur_data else 0

        payload["total_rides"] = cur_val + total_rides_inc
        res = supabase.table("drivers").update(payload).eq("id", driver_id).execute()
        return _single_row_from_res(res)

    result = await run_sync(_update)
    # Driver row changed (is_available / total_rides) → evict both the
    # by-id and by-user cache entries.
    user_id = result.get("user_id") if isinstance(result, dict) else None
    await invalidate_driver_cache(driver_id=driver_id, user_id=user_id)
    return result


async def match_and_claim_driver(
    vehicle_type_id: str,
    pickup_lat: float,
    pickup_lng: float,
    radius_km: float,
    min_rating: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Atomically find and claim the nearest available driver via PostGIS RPC.

    Returns the claimed driver row, or None if no eligible driver is available.
    Uses SELECT ... FOR UPDATE SKIP LOCKED in the DB — safe under concurrent dispatch.
    """
    if not supabase:
        return None

    def _call():
        res = supabase.rpc(
            "match_and_claim_driver",
            {
                "p_vehicle_type_id": vehicle_type_id,
                "p_pickup_lat": pickup_lat,
                "p_pickup_lng": pickup_lng,
                "p_radius_km": radius_km,
                "p_min_rating": min_rating,
            },
        ).execute()
        rows = _rows_from_res(res)
        return rows[0] if rows else None

    try:
        result = await run_sync(_call)
        if result:
            await invalidate_driver_cache(driver_id=result["id"])
        return result
    except Exception as exc:
        logger.error(f"match_and_claim_driver RPC failed: {exc}", exc_info=True)
        return None


async def claim_driver_atomic(driver_id: str) -> bool:
    """Atomically set is_available = false for driver if currently available."""
    if not supabase:
        return False

    await invalidate_driver_cache(driver_id=driver_id)

    def _claim():
        res = (
            supabase.table("drivers")
            .update({"is_available": False})
            .eq("id", driver_id)
            .eq("is_available", True)
            .execute()
        )
        data = _rows_from_res(res)
        return len(data) > 0

    claimed = await run_sync(_claim)
    if claimed:
        # The driver row is now is_available=false — make sure the cache
        # doesn't keep serving a stale "still available" value to
        # concurrent dispatch queries for the next 30s.
        await invalidate_driver_cache(driver_id=driver_id)
    return claimed


_EWMA_ALPHA = 0.1


async def update_acceptance_rate(driver_id: str, accepted: bool) -> None:
    """Update driver.acceptance_rate using exponentially weighted moving average.

    alpha=0.1 so recent behaviour matters without a single bad day
    cratering the score.  new = alpha * outcome + (1-alpha) * old.
    """
    if not supabase:
        return
    try:
        row = await run_sync(
            lambda: _single_row_from_res(
                supabase.table("drivers").select("acceptance_rate").eq("id", driver_id).execute()
            )
        )
        old = float((row or {}).get("acceptance_rate") or 1.0)
        outcome = 1.0 if accepted else 0.0
        new_rate = round(_EWMA_ALPHA * outcome + (1 - _EWMA_ALPHA) * old, 4)
        await run_sync(
            lambda: supabase.table("drivers").update({"acceptance_rate": new_rate}).eq("id", driver_id).execute()
        )
    except Exception as exc:
        logger.error(f"update_acceptance_rate failed for {driver_id}: {exc}", exc_info=True)


async def claim_ride_atomic(ride_id: str, driver_id: str) -> bool:
    """Atomically claim a ride offer for `driver_id`.

    Issues a single conditional UPDATE that sets
    ``status='driver_accepted'`` and ``driver_id=<driver>`` only if the
    ride is (1) identified by `ride_id`, (2) in an open, claimable state
    (`searching` or `driver_assigned`), and (3) either unassigned or
    already pre-assigned to THIS driver. Supabase's PostgREST layer
    evaluates all three filters atomically in one SQL statement, so two
    drivers racing to accept the same offer cannot both succeed — the
    loser's UPDATE matches zero rows and this function returns False.

    Returns:
        True  — we successfully claimed the ride and the driver-app can
                proceed with the ride flow.
        False — the ride was gone or already accepted by another driver;
                the caller should surface a "ride already taken" UX.
    """
    if not supabase:
        return False

    now_iso = datetime.now(timezone.utc).isoformat()

    def _claim():
        res = (
            supabase.table("rides")
            .update(
                {
                    "status": "driver_accepted",
                    "driver_id": driver_id,
                    "driver_accepted_at": now_iso,
                    "updated_at": now_iso,
                }
            )
            .eq("id", ride_id)
            # Status must be open/claimable. Any status past `driver_accepted`
            # (arrived / in_progress / completed / cancelled) is terminal for
            # the accept flow.
            .in_("status", ["searching", "driver_assigned"])
            # Ride must be either unassigned or already pre-assigned to this
            # driver. PostgREST's `.or_()` accepts a comma-separated filter
            # list; `is.null` maps to `IS NULL` in SQL.
            .or_(f"driver_id.is.null,driver_id.eq.{driver_id}")
            .execute()
        )
        data = _rows_from_res(res)
        return len(data) > 0

    return await run_sync(_claim)


async def get_driver_status_by_user(user_id: str) -> Optional[str]:
    """Get driver account status by user_id (active/suspended/banned)."""
    if not supabase:
        return None
    driver = await run_sync(
        lambda: _single_row_from_res(supabase.table("drivers").select("status").eq("user_id", user_id).execute())
    )
    return driver.get("status", "active") if driver else None
