"""Nearby drivers, location batch updates, device attestation, admin CRUD.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

import uuid

from pydantic import ValidationError, model_validator

from . import _deps
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Depends,
    Driver,
    Field,
    HTTPException,
    List,
    Query,
    Request,
    Union,
    calculate_distance,
    datetime,
    db_supabase,
    generate_driver_code,
    get_admin_user,
    get_current_user,
    get_token_session_id,
    intent_online,
    location_update_limit,
    logger,
    parse_iso_utc,
    present_driver_ids_checked,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    serialize_doc,
)

router = APIRouter()

_V2_ACTIVE_RIDE_STATUSES = {"driver_assigned", "driver_accepted", "driver_arrived", "in_progress"}
_RAW_LOCATION_RETENTION = timedelta(days=90)


class TripLocationPoint(BaseModel):
    """One immutable driver sensor fix submitted by the durable outbox."""

    sequence_number: int = Field(ge=0)
    captured_at: datetime
    lat: float | None = None
    lng: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    accuracy: float | None = None
    speed: float | None = None
    heading: float | None = None
    altitude: float | None = None
    monotonic_ms: int | None = Field(default=None, ge=0)
    source: str | None = None
    mocked: bool = False
    is_completion_fix: bool = False


class LocationBatchRequest(BaseModel):
    """Strict v2 payload: exactly one ordered recording session for one ride."""

    ride_id: str = Field(min_length=1)
    recording_session_id: uuid.UUID
    points: List[TripLocationPoint] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _sequences_are_contiguous(self):
        sequences = [point.sequence_number for point in self.points]
        if len(set(sequences)) != len(sequences):
            raise ValueError("sequence_number values must be unique")
        expected = list(range(sequences[0], sequences[0] + len(sequences)))
        if sequences != expected:
            raise ValueError("sequence_number values must be contiguous and ordered")
        return self


def _parse_v2_location_batch(batch: Union[List[dict], dict, LocationBatchRequest]) -> LocationBatchRequest | None:
    """Identify v2 bodies while preserving historical list/points payloads."""
    if isinstance(batch, LocationBatchRequest):
        return batch
    if not isinstance(batch, dict) or not ({"ride_id", "recording_session_id"} & set(batch)):
        return None
    try:
        return LocationBatchRequest.model_validate(batch)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc


def _completed_batch_is_within_retention(request: LocationBatchRequest, ride: dict) -> bool:
    """Allow delayed offline delivery only inside the completed ride lifecycle."""
    completed_at = parse_iso_utc(ride.get("ride_completed_at"))
    if completed_at is None or datetime.now(timezone.utc) - completed_at > _RAW_LOCATION_RETENTION:
        return False
    window_start = parse_iso_utc(ride.get("driver_accepted_at")) or parse_iso_utc(ride.get("created_at"))
    return all(
        (window_start is None or point.captured_at >= window_start) and point.captured_at <= completed_at
        for point in request.points
    )


async def _persist_v2_location_batch(request: LocationBatchRequest, current_user: dict) -> dict:
    """Authorize and persist one acknowledged v2 outbox batch before marker updates."""
    driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    if not driver_rows:
        raise HTTPException(status_code=403, detail="Driver profile required")
    driver = driver_rows[0]

    rides = await db_supabase.get_rows(
        "rides",
        {"id": request.ride_id, "driver_id": driver["id"]},
        limit=1,
    )
    if not rides:
        raise HTTPException(status_code=404, detail="Assigned ride not found")
    ride = rides[0]
    if ride.get("status") == "completed":
        if not _completed_batch_is_within_retention(request, ride):
            raise HTTPException(status_code=422, detail="Points fall outside completed ride retention window")
    elif ride.get("status") not in _V2_ACTIVE_RIDE_STATUSES:
        raise HTTPException(status_code=409, detail="Ride cannot accept location points in its current state")

    try:
        try:
            from ...utils.breadcrumbs import persist_trip_location_batch
        except ImportError:
            from utils.breadcrumbs import persist_trip_location_batch  # type: ignore

        result = await persist_trip_location_batch(
            driver["id"],
            request.ride_id,
            str(request.recording_session_id),
            [point.model_dump(mode="json") for point in request.points],
            active_ride=ride,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "location-batch durable persistence failed for driver_id=%s ride_id=%s",
            driver["id"],
            request.ride_id,
            exc_info=True,
        )
        raise HTTPException(status_code=503, detail="Location persistence unavailable") from exc

    rejected_sequences = {rejection.sequence_number for rejection in result.ack.rejected}
    latest = next(
        (point for point in reversed(request.points) if point.sequence_number not in rejected_sequences),
        None,
    )
    if latest is not None:
        lat = latest.latitude if latest.latitude is not None else latest.lat
        lng = latest.longitude if latest.longitude is not None else latest.lng
        if lat is not None and lng is not None:
            update_data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc)}
            if latest.heading is not None:
                update_data["heading"] = latest.heading % 360
            await db_supabase.update_one("drivers", {"id": driver["id"]}, update_data)

    if driver.get("is_online"):
        await _deps.mark_present(driver["id"])
    return result.ack.to_dict()


@router.get("/nearby")
async def get_nearby_drivers_public(
    lat: float = Query(...),
    lng: float = Query(...),
    radius: float = Query(None),
    vehicle_type: str = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get nearby active drivers for riders. Filters by service area + vehicle type.

    Positions are **coarsened** (see utils/driver_map_visibility). Pre-match there
    is no assigned ride to justify exact coordinates, and this endpoint is
    reachable by any authenticated rider with arbitrary lat/lng, so exact
    coordinates here let a caller enumerate and follow individual drivers.
    """
    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        from ...utils.driver_map_visibility import clamp_radius, map_settings, prematch_driver_list
    except ImportError:
        from utils.driver_map_visibility import (  # type: ignore
            clamp_radius,
            map_settings,
            prematch_driver_list,
        )

    app_settings = await get_app_settings() or {}
    show_locations, cell_m, max_radius_km = map_settings(app_settings)
    default_radius = float(app_settings.get("search_radius_km", 10.0))

    # Kill switch (launch plan: map visibility stays behind one). Returning an
    # empty list rather than 404/403 keeps the rider map functional — it renders
    # no cars, and the availability count from /rides/estimate is unaffected
    # because that endpoint never carried coordinates.
    if not show_locations:
        return []

    # Use admin-configured search_radius_km if caller didn't override, and cap
    # whatever we end up with: unbounded, one request sweeps the province.
    radius = clamp_radius(radius if radius is not None else default_radius, max_radius_km, default_radius)

    # is_verified + status='active' prevent unverified / suspended / needs_review
    # drivers from appearing on the rider map even if their is_online flag is
    # stale.
    try:
        from ...services.dispatch_service import dispatch_geo_bounds
    except ImportError:
        from services.dispatch_service import dispatch_geo_bounds  # type: ignore

    query = {
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        # Geo-bound the fetch (same box the dispatch path uses) so the 100-row
        # cap applies to in-area drivers only — otherwise, above 100 online
        # drivers province-wide, the map shows an arbitrary 100 and nearby cars
        # can be missing while far ones render. Scan bounded by the
        # migration-138 partial index; see the no-geo-index rationale at the
        # dispatch fetch in routes/rides.py.
        "$and": dispatch_geo_bounds(lat, lng, radius),
    }
    if vehicle_type:
        query["vehicle_type_id"] = vehicle_type

    # Get all matching drivers — service area filtering by distance (not polygon yet)
    drivers = await db_supabase.get_rows("drivers", query, limit=100)

    # Presence filter: hide drivers whose app is not reachable (force-killed,
    # phone dead, backgrounded past the TTL). Without this, the rider sees
    # ghost cars on the map and tries to book someone who will never receive
    # the offer.
    #
    # Three cases, distinguished via present_driver_ids_checked():
    #   * reachable + non-empty → filter normally (ghost drivers removed).
    #   * reachable + empty     → every candidate is genuinely offline; hide
    #     them all (an empty map is correct here, not a bug).
    #   * NOT reachable (Redis configured but down) → presence is unknowable,
    #     so fall back to DB state rather than blanking the map during a
    #     failover. Dispatch still presence-filters, so a ghost booking that
    #     slips onto the map cannot actually complete an offer.
    try:
        driver_ids = [d["id"] for d in drivers if d.get("id")]
        if driver_ids:
            present, reachable = await present_driver_ids_checked(driver_ids)
            if reachable:
                drivers = [d for d in drivers if d["id"] in present]
            else:
                logger.warning(
                    "/drivers/nearby: presence store unreachable, showing DB "
                    "state (dispatch still presence-filters before any offer)"
                )
    except Exception as exc:
        logger.warning(f"/drivers/nearby presence filter failed, using DB state: {exc}")

    # Resolve vehicle type names + admin-configured marker variant once so
    # the rider app can pick the matching map marker (standard / XL /
    # premium) without an extra round trip.
    vt_name_by_id: dict = {}
    vt_marker_by_id: dict = {}
    if drivers:
        vehicle_types = await db_supabase.get_rows("vehicle_types", {}, limit=100)
        vt_name_by_id = {vt["id"]: vt.get("name") for vt in vehicle_types if vt.get("id")}
        vt_marker_by_id = {vt["id"]: vt.get("marker_variant") for vt in vehicle_types if vt.get("id")}

    # Manual filtering by distance
    in_radius = []
    for d in drivers:
        # Exclude orphan/demo driver rows (no user_id → cannot be dispatched).
        if not d.get("user_id"):
            continue
        # Authoritative intent check using the went_online_at /
        # went_offline_at timestamps (migration 97). The DB pre-filter
        # already used `is_online=True`, but intent_online() also catches
        # the case where the column is stale and prefers the timestamp
        # when both are present. Falls back to is_online for unmigrated rows.
        if not intent_online(d):
            continue
        d_lat = d.get("lat")
        d_lng = d.get("lng")
        # `is not None` (not truthy) so a driver legitimately at lat=0 or
        # lng=0 still matches. The literal (0, 0) is the registration
        # default and means "no GPS yet" — skip those so a freshly-online
        # driver doesn't surface as a ghost car at the origin.
        if d_lat is not None and d_lng is not None and (d_lat != 0 or d_lng != 0):
            # Distance filtering uses the TRUE position so the radius stays
            # accurate; only the coordinates we hand back are coarsened.
            dist = calculate_distance(lat, lng, d_lat, d_lng)
            if dist <= radius:
                in_radius.append(
                    {
                        **d,
                        "vehicle_type_name": vt_name_by_id.get(d.get("vehicle_type_id")),
                        "marker_variant": vt_marker_by_id.get(d.get("vehicle_type_id")),
                    }
                )

    # Single projection point for what a pre-match rider may see — an allowlist,
    # so a new `drivers` column cannot become rider-visible by default. This also
    # drops vehicle_make/vehicle_model, which together with heading made a
    # specific car re-identifiable.
    # viewer_id scopes the marker pseudonyms to this rider so observations from
    # two accounts cannot be pooled into one trace.
    return prematch_driver_list(in_radius, cell_m, viewer_id=current_user.get("id"))


@router.get("")
async def get_drivers(
    lat: float = Query(None),
    lng: float = Query(None),
    radius: float = Query(5.0),
    vehicle_type: str = Query(None),
    admin_user: dict = Depends(get_admin_user),
):
    """
    Get all drivers (admin only) or nearby drivers (if lat/lng provided).
    """
    if lat is not None and lng is not None:
        # Should rely on RPC or geospatial query
        # For now, simplistic implementation as seen in other parts
        drivers = await db_supabase.get_rows("drivers", {"is_online": True}, limit=100)
        return serialize_doc(drivers)

    # Return all drivers for admin
    drivers = await db_supabase.get_rows("drivers", {}, limit=100)
    return serialize_doc(drivers)


@router.post("")
async def create_driver(driver: Driver, admin_user: dict = Depends(get_admin_user)):
    """Register a new driver (admin only or internal process)"""
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"phone": driver.phone}, limit=1)
    )
    if existing:
        raise HTTPException(status_code=400, detail="Driver with this phone already exists")

    row = driver.dict()
    row.setdefault("driver_code", generate_driver_code())
    await db_supabase.insert_one("drivers", row)
    return row


async def _guard_revoked_session(token_session_id: str | None) -> None:
    """Reject a location batch from a session that has been signed out.

    Defense in depth behind the driver app's own logout teardown. A signed-out
    app still holds an access token valid for the rest of its exp, and a stale
    build — or one killed mid-logout before its teardown ran — will keep
    uploading GPS with it. The client fix stops that at the source; this makes a
    client-side regression non-silent instead of re-opening a PIPEDA hole.

    Gated on an ``app_settings`` flag so it can be switched off without a
    redeploy (the settings-in-DB pattern in CLAUDE.md). Defaults ON: the check
    only fires on positive evidence of a logout, so leaving it dark would mean
    shipping the guard without the protection.

    Fails open on every ambiguity, including an unreadable settings row. Dropping
    a legitimate batch loses breadcrumbs that settle billed distance and back the
    SGI per-insurance-period audit, so a false 401 here is worse than a zombie
    writer that the client fix already stopped.
    """
    if not token_session_id:
        return
    try:
        try:
            from ...settings_loader import get_app_settings
        except ImportError:
            from settings_loader import get_app_settings  # type: ignore
        # An explicit NULL in app_settings must mean "unset → use the default",
        # not "disabled". `.get(key, True)` alone returns None for a NULL column
        # and bool(None) is False, which would silently ship the guard dark.
        _flag = (await get_app_settings() or {}).get("location_reject_revoked_sessions_enabled")
        enabled = True if _flag is None else bool(_flag)
    except Exception:
        logger.warning("could not read location_reject_revoked_sessions_enabled; allowing batch", exc_info=True)
        return
    if not enabled:
        return

    try:
        from ...utils.session_revocation import is_session_revoked
    except ImportError:
        from utils.session_revocation import is_session_revoked  # type: ignore

    if await is_session_revoked(token_session_id):
        # 401 (not 403): the credential is dead, so the client should re-auth
        # rather than retry. No session_id or driver_id in the message.
        raise HTTPException(status_code=401, detail="ERR_SESSION_REVOKED")


@router.post("/location-batch")
@location_update_limit
async def update_location_batch(
    batch: Union[List[dict], dict, LocationBatchRequest],
    request: Request = None,
    current_user: dict = Depends(get_current_user),
    token_session_id: str | None = Depends(get_token_session_id),
):
    """Update driver location in batch (from background tracking).

    Rate limited at 60/minute per driver (`location_update_limit`). The driver
    app's outbox flushes every 5-15 s, i.e. ~4-12 requests/minute, so this is
    roughly 5x headroom over normal operation and only bites on a runaway
    client. It is keyed per user, so one misbehaving device cannot exhaust the
    budget for other drivers sharing a carrier NAT egress IP.
    """
    # Ahead of both the v1 and v2 paths so neither can persist a signed-out
    # driver's coordinates.
    await _guard_revoked_session(token_session_id)
    v2_request = _parse_v2_location_batch(batch)
    if v2_request is not None:
        return await _persist_v2_location_batch(v2_request, current_user)

    try:
        from ...utils.location_integrity import check_location_integrity
    except ImportError:
        from utils.location_integrity import check_location_integrity  # type: ignore

    points = []
    if isinstance(batch, list):
        points = batch
    elif isinstance(batch, dict):
        points = batch.get("locations") or batch.get("points") or []

    # Simply take the last point and update current location
    if not points:
        return {"success": True}

    latest = points[-1]
    lat = latest.get("latitude") if latest.get("latitude") is not None else latest.get("lat")
    lng = latest.get("longitude") if latest.get("longitude") is not None else latest.get("lng")
    heading = latest.get("heading")

    if lat is not None and lng is not None:
        # GPS spoofing check
        driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        driver_id = driver_rows[0]["id"] if driver_rows else current_user["id"]
        trusted, _reason = await check_location_integrity(
            driver_id,
            lat,
            lng,
            speed=latest.get("speed"),
            accuracy=latest.get("accuracy"),
            mocked=latest.get("mocked"),
        )
        if not trusted:
            return {"success": False, "reason": "location_rejected"}
        # Update via Supabase wrapper which now handles casting. `heading`
        # column added in migration 113 — persist it so rider/admin map
        # markers can rotate the car icon to the real direction of travel
        # (and so two drivers at the same point don't render as one).
        update_data = {"lat": lat, "lng": lng, "updated_at": datetime.now(timezone.utc)}
        # Normalise to 0–359 and skip clearly-invalid values. We deliberately
        # only write heading when the device sent a usable number, so a
        # stationary fix with no bearing doesn't wipe the last good heading.
        if heading is not None:
            try:
                update_data["heading"] = float(heading) % 360
            except (TypeError, ValueError):
                pass

        # Period-1 (online, no active ride) deadhead-distance scalar accumulator.
        # Off by default; a deliberate opt-in since it measures a contractor's
        # between-rides movement. We fold ONLY a running km total + span start
        # into the same driver update — never the coordinates. resolve_active_ride
        # and get_app_settings are cached, so this stays cheap on the hot path.
        driver_row = driver_rows[0] if driver_rows else None
        if driver_row is not None and driver_row.get("is_online"):
            try:
                from ...settings_loader import get_app_settings
                from ...utils.breadcrumbs import resolve_active_ride
                from ...utils.period1_distance import batch_incremental_distance_km
            except ImportError:
                from settings_loader import get_app_settings  # type: ignore
                from utils.breadcrumbs import resolve_active_ride  # type: ignore
                from utils.period1_distance import batch_incremental_distance_km  # type: ignore
            try:
                _p1_on = bool((await get_app_settings() or {}).get("period1_distance_tracking_enabled"))
            except Exception:
                _p1_on = False
            if _p1_on:
                try:
                    _p1_active = await resolve_active_ride(driver_id)
                except Exception:
                    _p1_active = None
                if _p1_active is None:
                    _p1_delta = batch_incremental_distance_km(points)
                    if _p1_delta > 0:
                        update_data["period1_accum_km"] = round(
                            float(driver_row.get("period1_accum_km") or 0) + _p1_delta, 3
                        )
                        if not driver_row.get("period1_accum_since"):
                            update_data["period1_accum_since"] = datetime.now(timezone.utc)

        await db_supabase.update_one("drivers", {"user_id": current_user["id"]}, update_data)
        # Also sync to generic lat/lng fields if they exist to support legacy queries
        # (Though update_one might not support setting multiple top-level fields easily if we rely on $set mapping)
        # Let's trust db.drivers.update_one to handle the schema or the wrapper.

        # Persist the FULL batch as breadcrumbs, not just the live marker above.
        # Until now this endpoint (background task + WS-down REST fallback) kept
        # only the last point, so any backgrounded stretch of a trip produced no
        # driver_location_history rows — settled distance and the per-insurance-
        # period SGI audit trail both undercounted. The helper is a no-op unless
        # the driver currently has an active ride, and derives ride_id + phase
        # server-side (so a point the client tagged "background" still lands in
        # trip_in_progress). Best-effort: never fail the marker update on it.
        try:
            from ...utils.breadcrumbs import persist_ride_breadcrumbs
        except ImportError:
            from utils.breadcrumbs import persist_ride_breadcrumbs  # type: ignore
        try:
            await persist_ride_breadcrumbs(driver_id, points)
        except Exception:
            logger.error("location-batch breadcrumb persist failed", exc_info=True)

        # Keep presence alive even when the driver's WebSocket briefly
        # drops but the REST location batch keeps flowing (e.g. phone on
        # cellular switching towers).
        driver_row = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
        )
        if driver_row and driver_row.get("is_online"):
            await _deps.mark_present(driver_row["id"])

    return {"success": True}


@router.post("/attest-nonce")
async def attest_nonce(current_user: dict = Depends(get_current_user)):
    """Issue a single-use nonce for Play Integrity / App Attest verification.

    The client passes this nonce to the platform attestation API so the
    signed token can't be replayed from a different session.
    """
    import secrets

    nonce = secrets.token_hex(32)
    return {"nonce": nonce}


@router.post("/attest-device")
async def attest_device(device_info: dict, current_user: dict = Depends(get_current_user)):
    """Verify device integrity on go-online. Flags emulators and suspicious devices."""
    try:
        from ...utils.device_attestation import verify_device
    except ImportError:
        from utils.device_attestation import verify_device  # type: ignore

    driver_rows = await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    driver_id = driver_rows[0]["id"] if driver_rows else current_user["id"]

    result = await verify_device(current_user["id"], driver_id, device_info)
    return result
