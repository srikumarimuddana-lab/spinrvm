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
        _postgrest_or_value,
        _read_cached_row,
        _rows_from_res,
        _single_row_from_res,
        _write_cached_row,
        _write_skipped,
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
        _write_skipped,
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
        logger.opt(exception=True).error(f"get_service_area_for_point failed: {exc}")
        return None


async def find_nearby_drivers(lat: float, lng: float, radius_meters: float) -> List[Dict[str, Any]]:
    """Use PostGIS RPC to find nearby drivers."""
    if not supabase:
        return []

    def _fn():
        res = supabase.rpc("find_nearby_drivers", {"lat": lat, "lng": lng, "radius_meters": radius_meters}).execute()
        return _rows_from_res(res)

    return await run_sync(_fn)


async def dispatch_candidate_drivers(
    lat: float,
    lng: float,
    radius_m: float,
    vehicle_type_ids: List[str],
    *,
    requires_wav: bool = False,
    area_ids: Optional[List[str]] = None,
    allow_unassigned_area: bool = True,
    limit: int = 500,
) -> List[Dict[str, Any]]:
    """Candidate drivers for dispatch, via the PostGIS GiST index (migration 395).

    The indexed replacement for the bounding-box + Python-haversine candidate
    fetch in ``routes/rides/matching.py``. Two behavioural notes that matter to
    callers:

    - ``radius_m`` must ALREADY carry the padding ``dispatch_geo_bounds`` applies
      (``radius_km * 1.10 + 1.0``). The RPC returns a superset of the true
      circle on purpose, exactly as the box did, because
      ``filter_and_rank_drivers`` is and remains the exact distance gate. Passing
      an unpadded radius would silently tighten dispatch.
    - Rows come back distance-ordered, so ``limit`` truncates the FARTHEST
      candidates rather than an arbitrary set — which is the row-501 bug in the
      box path.

    Raises rather than returning ``[]`` on a DB error. "No drivers nearby" and
    "the database did not answer" must not look alike to dispatch: the caller
    re-arms its retry chain on an exception, but would strand the ride until the
    sweeper cancels it if a failure were reported as an empty pool.
    """
    if not supabase:
        return []

    params: Dict[str, Any] = {
        "p_lat": float(lat),
        "p_lng": float(lng),
        "p_radius_m": float(radius_m),
        # A LIST, not a scalar: the cascade path in matching.py widens to a set
        # of upgrade vehicle types (`$in`), and a scalar RPC could not serve it.
        # drivers.vehicle_type_id is TEXT, so coerce — a UUID object or int id
        # from a caller would silently match no rows.
        "p_vehicle_type_ids": [str(v) for v in vehicle_type_ids],
        "p_requires_wav": bool(requires_wav),
        # NULL (not an empty array) means "no area restriction" — an empty array
        # would match nothing and blank the candidate pool.
        "p_area_ids": [str(a) for a in area_ids] if area_ids else None,
        "p_allow_unassigned_area": bool(allow_unassigned_area),
        "p_limit": int(limit),
    }

    def _fn():
        res = supabase.rpc("dispatch_candidate_drivers", params).execute()
        return _rows_from_res(res)

    return await run_sync(_fn, retry_policy="read")


async def update_driver_location(driver_id: str, lat: float, lng: float, heading=None):
    if not supabase:
        _write_skipped("update_driver_location", "drivers")
        return None

    def _update():
        data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc).isoformat()}
        # Persist heading (migration 113) so /drivers/nearby can rotate the
        # rider map marker. Normalise to 0–359 and only write when the device
        # sent a usable number, so a fix with no bearing doesn't wipe the last
        # good heading. Mirrors the REST /location-batch path.
        if heading is not None:
            try:
                data["heading"] = float(heading) % 360
            except (TypeError, ValueError):
                pass
        supabase.table("drivers").update(data).eq("id", str(driver_id)).execute()
        return True

    return await run_sync(_update)


async def set_driver_available(driver_id: str, available: bool = True, total_rides_inc: int = 0):
    if not supabase:
        _write_skipped("set_driver_available", "drivers")
        return None

    await invalidate_driver_cache(driver_id=driver_id)

    def _update():
        payload: Dict[str, Any] = {"is_available": available}
        # C3: releasing the driver clears the claim stamp so the reaper won't
        # consider them orphaned. (Claiming sets it; releasing unsets it.)
        if available:
            payload["availability_claimed_at"] = None

        # Enforce the invariant is_available ⇒ is_online. is_online is
        # driver-toggled, so we must NOT flip it on here; instead, when asked
        # to make a driver available we clamp to their current online state —
        # an offline driver can never be marked available. Releasing
        # (available=False) is always safe and needs no read. A read is needed
        # when we're making the driver available or incrementing total_rides.
        needs_read = available or total_rides_inc != 0
        cur_val = 0
        if needs_read:
            cur = supabase.table("drivers").select("total_rides, is_online").eq("id", driver_id).execute()
            cur_data = _rows_from_res(cur)
            row = cur_data[0] if cur_data else {}
            cur_val = row.get("total_rides", 0) or 0
            if available and not row.get("is_online", False):
                payload["is_available"] = False

        if total_rides_inc != 0:
            payload["total_rides"] = cur_val + total_rides_inc

        logger.info(
            f"[GO-ONLINE] set_driver_available CALLED driver_id={driver_id} "
            f"available={available} total_rides_inc={total_rides_inc} "
            f"payload={payload} (is_available clamped to is_online; is_online "
            f"is never written here)"
        )
        res = supabase.table("drivers").update(payload).eq("id", driver_id).execute()
        # PIPEDA: never log res.data — supabase-py returns the full driver row by
        # default, which includes the encrypted address, driver-license number,
        # and name/phone. Log only the driver_id and whether a row was updated.
        _rows = getattr(res, "data", None) or []
        logger.info(f"[GO-ONLINE] set_driver_available executed driver_id={driver_id} rows_updated={len(_rows)}")
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
        _write_skipped("match_and_claim_driver", "drivers")
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

    # No try/except here on purpose: run_sync raises a typed DatabaseError on
    # failure, and swallowing it into ``return None`` made a Supabase blip
    # indistinguishable from a genuine "no drivers available" — callers would
    # cancel the ride instead of surfacing a retryable 503 (CLAUDE.md: never
    # soften DB/dispatch errors). None strictly means the RPC ran and found
    # no eligible driver.
    result = await run_sync(_call)
    if result:
        await invalidate_driver_cache(driver_id=result["id"])
    return result


async def claim_driver_atomic(driver_id: str) -> Optional[Dict[str, Any]]:
    """Atomically set is_available = false for driver if currently available.

    Returns the CLAIMED ROW on success, None on failure. It used to return a
    bare bool and throw the row away, which forced the caller into an
    immediate ``get_driver_by_id`` — guaranteed uncached, because this
    function invalidates that very cache entry twice. Dispatch claims up to
    max_simultaneous_offers drivers per attempt on a path with a P95 < 2 s
    SLA, so that was up to 10 avoidable round-trips per dispatch.

    The returned row is the post-update representation, so it is not merely as
    fresh as a follow-up SELECT — it is strictly fresher: it is the state at
    the instant of the atomic claim, with no window for a concurrent write to
    slip in between. Callers revalidating eligibility (is_online / is_verified
    / status) should use it directly.

    Truthiness is unchanged, so ``if await claim_driver_atomic(...)`` reads the
    same as before.
    """
    if not supabase:
        # Returning falsy is the SAFE direction here — an unclaimed driver is
        # simply not offered the ride, so dispatch degrades rather than
        # double-offering. It is still a write that silently did not happen,
        # and this is the fifth helper named in _base.update_one's deferred
        # note; it logs for the same reason as the other four.
        _write_skipped("claim_driver_atomic", "drivers")
        return None

    await invalidate_driver_cache(driver_id=driver_id)

    def _claim():
        res = (
            supabase.table("drivers")
            # C3: stamp a dedicated claim time so the orphan-claim reaper can
            # release this driver if the offer-insert never lands (crash/restart)
            # without racing the sub-second claim→insert window. Cleared on release.
            .update(
                {
                    "is_available": False,
                    "availability_claimed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", driver_id)
            .eq("is_available", True)
            .execute()
        )
        data = _rows_from_res(res)
        # The updated representation, not just a row count — the caller needs
        # it to revalidate eligibility without a second read.
        return data[0] if data else None

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
        _write_skipped("update_acceptance_rate", "drivers")
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
        logger.opt(exception=True).error(f"update_acceptance_rate failed for {driver_id}: {exc}")


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
        _write_skipped("claim_ride_atomic", "rides")
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
            # list; `is.null` maps to `IS NULL` in SQL. `driver_id` is routed
            # through `_postgrest_or_value` per the "Query filters" convention
            # (CLAUDE.md) — the layer, not the caller, owns escaping any
            # reserved `,()"\` characters so a malformed value can't silently
            # corrupt or widen the or-clause.
            .or_(f"driver_id.is.null,driver_id.eq.{_postgrest_or_value(driver_id)}")
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
