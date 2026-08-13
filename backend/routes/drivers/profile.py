"""Driver profile, registration, config, heatmap, destination mode.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps, _shared
from ._deps import (  # noqa: F401
    APIRouter,
    BaseModel,
    Body,
    Depends,
    HTTPException,
    Optional,
    datetime,
    db_supabase,
    generate_driver_code,
    get_current_user,
    logger,
    timedelta,
    timezone,
)
from ._shared import (  # noqa: F401
    _STRIP_FROM_SELF_RESPONSE,
    serialize_doc,
)

router = APIRouter()


@router.get("/config")
async def get_driver_config(current_user: dict = Depends(get_current_user)):
    """Return operational settings the driver-app should honor at runtime.

    Driver-app constants that used to live hardcoded in
    `driver-app/shared/config/spinr.config.ts` and
    `driver-app/store/driverStore.ts` are now served from the backend
    so operations can tune them per deploy without shipping a new app
    build. Fields fall back to sensible defaults when the DB
    `settings` row doesn't include them yet.

    * ``ride_offer_timeout_seconds`` — how long a driver has to
      accept/decline a ride offer before it auto-declines. Default 15.
      Capped to [5, 60] so a bad admin input can't brick the UX.
    * ``pickup_radius_meters`` — how close the driver must be to the
      pickup point to mark "arrived" (geofence check). Default 100.
      Capped to [10, 1000].
    """
    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore

    try:
        app_settings = await get_app_settings() or {}
    except Exception as e:
        logger.error(f"get_driver_config: failed to read app_settings: {e}", exc_info=True)
        app_settings = {}

    def _clamp(value, lo, hi, default):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, n))

    # Admin-uploaded mp3/wav URL the driver-app plays as the ride-offer
    # ping. Null/empty → driver-app falls back to the bundled placeholder.
    ride_offer_sound_url = app_settings.get("ride_offer_sound_url") or None
    return {
        "ride_offer_timeout_seconds": _clamp(app_settings.get("ride_offer_timeout_seconds"), 5, 60, 15),
        "pickup_radius_meters": _clamp(app_settings.get("pickup_radius_meters"), 10, 1000, 100),
        "ride_offer_sound_url": ride_offer_sound_url,
    }


@router.get("/me")
async def get_my_driver(current_user: dict = Depends(get_current_user)):
    """Get the current user's driver profile."""
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")
    response_data = serialize_doc(await _shared._decrypt_driver_pii(driver))
    for field in _STRIP_FROM_SELF_RESPONSE:
        response_data.pop(field, None)
    return response_data


class UpdateDriverProfileRequest(BaseModel):
    """Strict schema for driver profile updates — only whitelisted fields accepted."""

    # Safe fields (no re-verification)
    gst_registered: Optional[bool] = None
    gst_bn: Optional[str] = None  # CRA Business Number, format 123456789RT0001
    # Write-only. Validated (9 digits + Luhn) and Vault-encrypted before it
    # reaches a column; never returned by any endpoint. Optional on purpose —
    # making it required would lock every already-onboarded driver out of
    # their profile mid-session.
    sin: Optional[str] = None
    preferred_language: Optional[str] = None
    photo_url: Optional[str] = None
    is_wav: Optional[bool] = None
    # Vehicle/document fields (triggers re-review on verified drivers)
    vehicle_type_id: Optional[str] = None
    vehicle_make: Optional[str] = None
    vehicle_model: Optional[str] = None
    vehicle_color: Optional[str] = None
    vehicle_year: Optional[int] = None
    license_plate: Optional[str] = None
    vehicle_vin: Optional[str] = None
    license_number: Optional[str] = None
    license_expiry_date: Optional[str] = None
    insurance_expiry_date: Optional[str] = None
    vehicle_inspection_expiry_date: Optional[str] = None
    background_check_expiry_date: Optional[str] = None
    work_eligibility_expiry_date: Optional[str] = None
    city: Optional[str] = None
    service_area_id: Optional[str] = None


@router.put("/me")
async def update_my_driver(body: UpdateDriverProfileRequest, current_user: dict = Depends(get_current_user)):
    """Update the current user's driver profile.

    Accepts vehicle info, personal details, and preferences. When a
    verified driver changes vehicle fields, they are automatically
    un-verified and must wait for admin re-approval.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )

    # Fields that always update without affecting verification
    safe_fields = {
        "gst_registered",
        "gst_bn",
        "preferred_language",
        "is_wav",
        # Tax identity, like gst_bn — supplying it must not flip a verified
        # driver to needs_review and knock them offline mid-shift.
        "sin",
    }
    # Vehicle/doc fields — changing these on a verified driver triggers re-review
    vehicle_fields = {
        "vehicle_type_id",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vehicle_year",
        "license_plate",
        "vehicle_vin",
        "license_number",
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
        "work_eligibility_expiry_date",
        "city",
        "service_area_id",
    }
    allowed_fields = safe_fields | vehicle_fields

    updates = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in allowed_fields}

    # Validate the SIN before anything else touches it. A typo is not caught
    # until CRA rejects the T4A months later, by which time the driver may be
    # unreachable — so a bad number must never reach the column. The
    # ValueError message describes what is wrong and never echoes the value,
    # so it is safe to return to the client.
    #
    # Membership test, NOT truthiness: `{"sin": ""}` survives exclude_none,
    # and a truthiness gate would skip validation and write the empty string
    # verbatim — a non-NULL, non-token value that permanently fails the
    # `sin IS NULL` compare-and-set below, locking the driver out of ever
    # entering their real SIN. Empty goes through validate_sin → 422.
    if "sin" in updates:
        # Immutable after first entry. A SIN change post-collection is either
        # a typo (needs a human to verify against the CRA-issued document) or
        # someone else's number (needs a human, full stop) — both go through
        # an admin, never a self-serve overwrite. The T4A and Stripe both key
        # off this value, so a silent swap would corrupt the tax record.
        if driver and driver.get("sin"):
            raise HTTPException(
                status_code=403,
                detail=(
                    "Your SIN is already on file and cannot be changed from "
                    "the app. Contact support to request a correction — an "
                    "admin will verify and update it."
                ),
            )
        try:
            from ...utils.sin import sin_last4, validate_sin
        except ImportError:  # pragma: no cover - dual-import pattern
            from utils.sin import sin_last4, validate_sin  # type: ignore
        try:
            validated = validate_sin(str(updates["sin"]))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        # `sin` is encrypted by _encrypt_driver_pii on the way to the DB;
        # last4 is stored in the clear so on-file state is visible without a
        # decrypt, and it is the only part ever displayed.
        updates["sin"] = validated
        updates["sin_last4"] = sin_last4(validated)
        updates["sin_collected_at"] = datetime.now(timezone.utc).isoformat()

    if not updates:
        return {"success": True}

    # Auto-create a driver row if one doesn't exist yet (new driver adding
    # vehicle details for the first time from the vehicle-info screen).
    if not driver:
        import uuid

        first = current_user.get("first_name", "")
        last = current_user.get("last_name", "")
        new_driver = {
            "id": str(uuid.uuid4()),
            "driver_code": generate_driver_code(),
            "user_id": current_user["id"],
            "name": f"{first} {last}".strip() or current_user.get("phone", ""),
            "first_name": first or None,
            "last_name": last or None,
            "phone": current_user.get("phone", ""),
            "status": "pending",
            "is_verified": False,
            "is_online": False,
            "is_available": False,
            "rating": 5.0,
            "total_rides": 0,
            "lat": 0,
            "lng": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **updates,
        }
        # Encrypt on this path too. It was writing `**updates` straight to the
        # DB, so a driver whose FIRST profile write carried a license_number
        # (or now a SIN) stored it as plaintext — the exact PIPEDA failure
        # _vault_encrypt is fail-closed to prevent. The update path below has
        # always encrypted; only this auto-create branch did not.
        await db_supabase.insert_one("drivers", await _shared._encrypt_driver_pii(new_driver))
        # Mark as driver; is_rider is intentionally left unchanged so a
        # driver who was already riding keeps both flags (dual-role).
        await db_supabase.update_one("users", {"id": current_user["id"]}, {"role": "driver", "is_driver": True})
        # Serialize the pre-encryption dict, so strip the write-only PII
        # rather than echoing a SIN the caller just sent us.
        return serialize_doc({k: v for k, v in new_driver.items() if k not in _STRIP_FROM_SELF_RESPONSE})

    # Check if an active driver changed vehicle/document fields → needs review
    changed_vehicle = any(k in vehicle_fields for k in updates)
    if changed_vehicle and driver.get("status") == "active":
        updates["status"] = "needs_review"
        updates["is_online"] = False
        updates["is_available"] = False
        logger.info(f"[DRIVER] Driver {driver['id']} updated vehicle info → status set to needs_review")

    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_filter: dict = {"id": driver["id"]}
    if "sin" in updates:
        # DB-level compare-and-set backing the 403 above. The in-memory check
        # reads `driver` once at the top of the request, so two concurrent
        # first writes (double-tap submit, client retry) both pass it; without
        # this filter the second would silently overwrite the first — the
        # exact bypass the immutability rule forbids. Same convention as the
        # ride-acceptance {'status': 'searching'} guard: the losing request
        # matches 0 rows and is told so, instead of winning by being last.
        write_filter["sin"] = None
    result = await db_supabase.update_one("drivers", write_filter, await _shared._encrypt_driver_pii(updates))
    if "sin" in updates and result is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Your SIN was already saved by another request and cannot be "
                "overwritten. Contact support if it needs a correction."
            ),
        )
    # Append-only vehicle/identity change history (SGI/insurance audit). Uses
    # the pre-update `driver` row as the "before" snapshot.
    if changed_vehicle:
        try:
            from ...utils.vehicle_history import record_vehicle_changes
        except ImportError:
            from utils.vehicle_history import record_vehicle_changes  # type: ignore
        await record_vehicle_changes(
            driver["id"], driver, updates, changed_by_user_id=current_user["id"], role="driver"
        )
    # M-5: SGI insurance period audit — vehicle/document edits flip an
    # active driver to needs_review and force them offline. If they were
    # actually online before this update, that's a 1→0 transition.
    if changed_vehicle and driver.get("status") == "active" and driver.get("is_online"):
        await _deps.record_period_transition(driver["id"], 0)
    # This transition takes the driver offline. Without a notice the only
    # signal is the Go-online toggle silently refusing them later, so tell
    # them what happened and why. Best-effort: the status change is already
    # committed and must not be rolled back by a push failure.
    if updates.get("status") == "needs_review":
        try:
            from ...utils.driver_status_notifications import notify_driver_status_change, status_message
        except ImportError:
            from utils.driver_status_notifications import (  # type: ignore
                notify_driver_status_change,
                status_message,
            )
        await notify_driver_status_change(driver, status_message("needs_review"), "vehicle_edit")
    updated = await db_supabase.get_driver_by_id(driver["id"])
    # Same strip GET /drivers/me applies. This response was returning the raw
    # row, so stripe_account_id / bank_account / fcm_token came back on every
    # profile update while the GET withheld them — and `sin` would have
    # followed them out. One shape for the driver's own record, both verbs.
    response_data = serialize_doc(await _shared._decrypt_driver_pii(updated))
    for field in _STRIP_FROM_SELF_RESPONSE:
        response_data.pop(field, None)
    return response_data


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    """Coerce an admin-supplied setting to an int within [lo, hi].

    Any non-numeric value (None, "", "abc") falls back to ``default`` rather
    than raising — these run on a per-request driver path where a bad settings
    row must not 500 the whole fleet.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _clamp_float(value, lo: float, hi: float, default: float) -> float:
    """Float counterpart to :func:`_clamp_int` (cell sizes, decay half-life)."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    if n != n:  # NaN
        return default
    return max(lo, min(hi, n))


def _heatmap_cell_key(lat: float, lng: float, cell_lat: float, cell_lng: float):
    import math

    return (math.floor(lat / cell_lat), math.floor(lng / cell_lng))


def _heatmap_centroid(cr: int, cc: int, cell_lat: float, cell_lng: float):
    return (round((cr + 0.5) * cell_lat, 6), round((cc + 0.5) * cell_lng, 6))


@router.get("/demand-heatmap")
async def get_demand_heatmap(current_user: dict = Depends(get_current_user)):
    """Return aggregated demand heatmap cells for the driver's service area.

    v1 (always): ``points: [[lat, lng, weight]]`` — decayed 7-day aggregate.
    v2 (gated):  ``cells``, ``surge``, ``forecast`` — component-separated
    for layer UI + next-6h demand timeline.
    """
    import json as _json

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )

    service_area = None
    if driver and driver.get("service_area_id"):
        service_area = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("service_areas", {"id": driver["service_area_id"]}, limit=1)
        )

    try:
        from ...settings_loader import get_app_settings  # type: ignore
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    try:
        from ...utils.redis_client import redis_get as _redis_get  # type: ignore
        from ...utils.redis_client import redis_set as _redis_set
    except ImportError:
        from utils.redis_client import redis_get as _redis_get  # type: ignore
        from utils.redis_client import redis_set as _redis_set
    try:
        from ...utils.metrics import inc as _metric_inc  # type: ignore
        from ...utils.metrics import observe as _metric_observe
    except ImportError:
        from utils.metrics import inc as _metric_inc  # type: ignore
        from utils.metrics import observe as _metric_observe
    try:
        from ...utils.heatmap_config import config_fingerprint, resolve_heatmap_config  # type: ignore
    except ImportError:
        from utils.heatmap_config import config_fingerprint, resolve_heatmap_config  # type: ignore

    app_settings = {}
    try:
        app_settings = await get_app_settings() or {}
    except Exception as e:
        # Degrades to safe defaults (v2 off, k_floor 3) but must not be silent —
        # this is a settings-table read failure on a per-request path.
        logger.error(f"get_demand_heatmap: failed to read app_settings: {e}", exc_info=True)
        app_settings = {}

    # Global kill switch first: one flip takes the feature down fleet-wide
    # without touching every service area. Deliberately checked BEFORE the
    # per-area toggle and before any cache read, so disabling it takes effect
    # within one client refresh rather than one cache TTL. Absent key => True,
    # preserving pre-migration-311 behaviour.
    if not bool(app_settings.get("driver_heatmap_enabled", True)):
        return {"enabled": False, "points": [], "total_rides": 0}

    enabled = bool(service_area and service_area.get("show_demand_heatmap"))
    if not enabled:
        return {"enabled": False, "points": [], "total_rides": 0}

    area_id = driver["service_area_id"]

    v2_global = bool(app_settings.get("driver_heatmap_v2_enabled", False))
    v2_allowlist = app_settings.get("heatmap_internal_driver_ids") or []
    v2_enabled = v2_global or (current_user["id"] in v2_allowlist)

    # Resolve tuning: per-area override → global settings → code default,
    # clamped at the end regardless of source. Both upstream sources are
    # ordinary DB rows and therefore writable out-of-band, so the clamp is the
    # read site's own defence, not a restatement of the write side's.
    hm_cfg = resolve_heatmap_config(service_area, app_settings)

    cache_version = "v2" if v2_enabled else "v1"
    # The config fingerprint is part of the key: a cached payload is only valid
    # for the config that built it. Without it, retuning an area (or tightening
    # its k-anonymity floor) would keep serving cells built under the old
    # settings until the TTL lapsed.
    cache_key = f"spinr:heatmap:{area_id}:{cache_version}:{config_fingerprint(hm_cfg)}"
    # Cache failure must degrade to a cache miss, never to a 500. redis_get
    # re-raises when REDIS_URL is set, so an unguarded read turns a Redis blip
    # into a hard-down heatmap for every polling driver while the DB is healthy.
    cached = None
    try:
        cached = await _redis_get(cache_key)
    except Exception as e:
        logger.warning(f"get_demand_heatmap: cache read failed, rebuilding: {e}")
        _metric_inc("spinr_drivers_heatmap_cache_errors_total", {"op": "get"})
    if cached is not None:
        _metric_inc("spinr_drivers_heatmap_requests_total", {"cache": "hit"})
        return _json.loads(cached)

    _metric_inc("spinr_drivers_heatmap_requests_total", {"cache": "miss"})

    import time as _time

    _build_start = _time.monotonic()

    k_floor = hm_cfg["k_floor"]
    cell_lat = hm_cfg["cell_lat_deg"]
    cell_lng = hm_cfg["cell_lng_deg"]
    decay_half_life = hm_cfg["decay_half_life_days"]
    refresh_seconds = hm_cfg["refresh_seconds"]

    now = datetime.now(timezone.utc)
    cutoff_live = (now - timedelta(days=hm_cfg["live_window_days"])).isoformat()

    # ── v1 aggregate (always built) ──────────────────────────────────────
    rides = await db_supabase.get_rows(
        "rides",
        {"created_at": {"$gte": cutoff_live}, "service_area_id": area_id},
        order="created_at",
        desc=True,
        limit=5_000,
        columns="pickup_lat,pickup_lng,created_at,status",
    )

    cells: dict = {}
    total_rides = 0
    for r in rides:
        lat = r.get("pickup_lat")
        lng = r.get("pickup_lng")
        if lat is None or lng is None:
            continue
        total_rides += 1
        lat_f = float(lat)
        lng_f = float(lng)
        key = _heatmap_cell_key(lat_f, lng_f, cell_lat, cell_lng)
        created_str = r.get("created_at", "")
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            created = now
        age_days = max((now - created).total_seconds() / 86400.0, 0)
        weight = 0.5 ** (age_days / decay_half_life) if decay_half_life > 0 else 1.0
        if key not in cells:
            cells[key] = {"weight": 0.0, "count": 0}
        cells[key]["weight"] += weight
        cells[key]["count"] += 1

    suppressed = 0
    points = []
    for (cr, cc), cell in cells.items():
        if cell["count"] < k_floor:
            suppressed += 1
            continue
        clat, clng = _heatmap_centroid(cr, cc, cell_lat, cell_lng)
        points.append([clat, clng, round(cell["weight"], 2)])

    # ── v2 components (only when enabled) ────────────────────────────────
    v2_cells = None
    v2_surge = None
    if v2_enabled:
        _ACTIVE = {"searching", "driver_assigned", "driver_accepted", "driver_arrived", "in_progress"}
        cutoff_now = (now - timedelta(minutes=hm_cfg["now_window_minutes"])).isoformat()
        cutoff_baseline = (now - timedelta(days=hm_cfg["baseline_window_days"])).isoformat()
        cutoff_scheduled = (now + timedelta(hours=hm_cfg["scheduled_lookahead_hours"])).isoformat()

        # live: rides in active statuses requested in last 10 min
        live_rides = await db_supabase.get_rows(
            "rides",
            {"created_at": {"$gte": cutoff_now}, "service_area_id": area_id},
            limit=5_000,
            columns="pickup_lat,pickup_lng,status",
        )

        live_grid: dict = {}
        for r in live_rides:
            if r.get("status") not in _ACTIVE:
                continue
            lat, lng = r.get("pickup_lat"), r.get("pickup_lng")
            if lat is None or lng is None:
                continue
            key = _heatmap_cell_key(float(lat), float(lng), cell_lat, cell_lng)
            live_grid[key] = live_grid.get(key, 0) + 1

        # baseline: 28-day hour-of-week demand, normalized 0-1 per area
        current_dow = now.weekday()
        current_hour = now.hour

        baseline_rides = await db_supabase.get_rows(
            "rides",
            {"created_at": {"$gte": cutoff_baseline}, "service_area_id": area_id},
            limit=5_000,
            columns="pickup_lat,pickup_lng,created_at",
        )

        baseline_grid: dict = {}
        for r in baseline_rides:
            lat, lng = r.get("pickup_lat"), r.get("pickup_lng")
            if lat is None or lng is None:
                continue
            created_str = r.get("created_at", "")
            try:
                created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                continue
            if created.weekday() == current_dow and created.hour == current_hour:
                key = _heatmap_cell_key(float(lat), float(lng), cell_lat, cell_lng)
                baseline_grid[key] = baseline_grid.get(key, 0) + 1

        # Suppress below-floor cells on the RAW count before normalizing.
        # Normalizing first and then testing the score is a k-anonymity hole:
        # a cell with a single historical ride still normalizes to a non-zero
        # value, so a lone rider's recurring pickup (e.g. their home, same
        # hour every week) would be emitted with its centroid.
        baseline_raw = {k: v for k, v in baseline_grid.items() if v >= k_floor}
        suppressed += len(baseline_grid) - len(baseline_raw)

        # normalize baseline 0-1 over the surviving cells only
        bl_max = max(baseline_raw.values()) if baseline_raw else 1
        baseline_grid = {}
        if bl_max > 0:
            for k, v in baseline_raw.items():
                baseline_grid[k] = round(v / bl_max, 2)

        # scheduled: rides with status='scheduled' and pickup in next 2h.
        # The lower bound is load-bearing: without it, scheduled rides stuck
        # past their pickup time (dispatch loop lagging, or the scheduled
        # dispatch kill switch off) are counted forever and drivers reposition
        # toward pickups that will never dispatch.
        sched_rides = await db_supabase.get_rows(
            "rides",
            {
                "status": "scheduled",
                "service_area_id": area_id,
                "scheduled_pickup_time": {"$gte": now.isoformat(), "$lte": cutoff_scheduled},
            },
            limit=5_000,
            columns="pickup_lat,pickup_lng",
        )

        sched_grid: dict = {}
        for r in sched_rides:
            lat, lng = r.get("pickup_lat"), r.get("pickup_lng")
            if lat is None or lng is None:
                continue
            key = _heatmap_cell_key(float(lat), float(lng), cell_lat, cell_lng)
            sched_grid[key] = sched_grid.get(key, 0) + 1

        # merge all cell keys; per-component k-floor
        all_keys = set(live_grid) | set(baseline_grid) | set(sched_grid)
        v2_cells = []
        for key in all_keys:
            live_val = live_grid.get(key, 0)
            bl_val = baseline_grid.get(key, 0.0)
            sched_val = sched_grid.get(key, 0)

            # cell survives if ANY component clears k-floor; below-floor zeroed.
            # baseline_grid only ever contains cells whose RAW count already
            # cleared k_floor (filtered above), so presence here is the floor
            # check — never test the normalized score, which is non-zero for a
            # single-ride cell.
            live_ok = live_val >= k_floor
            sched_ok = sched_val >= k_floor
            bl_ok = key in baseline_grid
            if not (live_ok or sched_ok or bl_ok):
                suppressed += 1
                continue

            clat, clng = _heatmap_centroid(key[0], key[1], cell_lat, cell_lng)
            v2_cells.append(
                {
                    "lat": clat,
                    "lng": clng,
                    "live": live_val if live_ok else 0,
                    "baseline": bl_val if bl_ok else 0,
                    "scheduled": sched_val if sched_ok else 0,
                }
            )

        # Surge mirror from service_area fields. Gated on surge_enabled the same
        # way the public /service-areas projection is: a stale surge_active /
        # multiplier on an area where surge is administratively off must never
        # surface to a client, or drivers chase surge earnings riders are not
        # being charged. `or 1.0` guards an explicit NULL column (.get returns
        # None, not the default).
        _surge_on = bool(service_area.get("surge_enabled"))
        v2_surge = {
            "multiplier": float(service_area.get("surge_multiplier") or 1.0) if _surge_on else 1.0,
            "active": bool(service_area.get("surge_active", False)) if _surge_on else False,
        }

        # forecast: next 6h hourly demand from the forecast engine (HM-23)
        v2_forecast = None
        try:
            try:
                from ...utils.demand_forecast import forecast_demand as _forecast_demand  # type: ignore
            except ImportError:
                from utils.demand_forecast import forecast_demand as _forecast_demand  # type: ignore

            raw_fc = await _forecast_demand(
                area_id=area_id,
                hours_ahead=hm_cfg["forecast_hours_ahead"],
                lookback_days=hm_cfg["forecast_lookback_days"],
            )
            if raw_fc:
                max_pred = max((f["predicted_rides"] for f in raw_fc), default=1) or 1
                v2_forecast = [
                    {
                        "hour": f["hour"],
                        "day_name": f["day_name"],
                        "demand": round(f["predicted_rides"] / max_pred, 2),
                        "is_peak": f["is_peak"],
                    }
                    for f in raw_fc
                ]
        except Exception as e:
            logger.warning(f"demand-heatmap: forecast build failed: {e}")

    _build_ms = (_time.monotonic() - _build_start) * 1000
    _metric_observe("spinr_drivers_heatmap_build_duration_ms", _build_ms)
    _metric_inc("spinr_drivers_heatmap_cells_suppressed_total", by=suppressed)

    result = {
        "enabled": True,
        "points": points,
        "total_rides": total_rides,
        "refresh_seconds": refresh_seconds,
        "generated_at": now.isoformat(),
    }
    if v2_cells is not None:
        result["cells"] = v2_cells
        result["surge"] = v2_surge
        if v2_forecast:
            result["forecast"] = v2_forecast

    try:
        await _redis_set(cache_key, _json.dumps(result), ttl=60)
    except Exception as e:
        logger.warning(f"demand-heatmap: cache write failed: {e}")

    return result


@router.post("/register")
async def register_driver(
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Create or update the driver row for the authenticated user (become-driver flow).

    Called by the driver app's `registerDriver()` in authStore after the user
    submits vehicle + document info. Upsert so re-submission updates the row
    rather than erroring.
    """
    user_id = current_user["id"]
    user_phone = current_user.get("phone", "")

    # Build the driver name from the register body, then fall back to the
    # user's account profile (phone signup captures first/last on the users
    # row), then any legacy combined name field. The generic "Driver" label is
    # an absolute last resort for the display `name` only — it must NEVER be
    # split into first_name/last_name. Doing so is what created brand-new
    # drivers rendered literally as "Driver" in the admin panel.
    first_name = (body.get("first_name") or current_user.get("first_name") or "").strip()
    last_name = (body.get("last_name") or current_user.get("last_name") or "").strip()
    if not first_name and not last_name:
        # Recover a real name from a legacy combined field on the account, but
        # never from the generic fallback below.
        _account_name = (current_user.get("name") or current_user.get("full_name") or "").strip()
        if _account_name:
            _parts = _account_name.split(" ", 1)
            first_name = _parts[0]
            last_name = _parts[1].strip() if len(_parts) > 1 else ""
    _first_name_split = first_name
    _last_name_split = last_name
    # Display name: the real name, else the phone number (matches the other
    # driver auto-create paths), else a generic label so the column is not null.
    full_name = f"{first_name} {last_name}".strip() or user_phone or "Driver"

    existing = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("drivers", {"user_id": user_id}, limit=1))

    # Reject registration attempts that would collide with an existing
    # driver record owned by someone else — prevents the phone-level
    # duplicates we saw in migration 30_identity_audit. Only enforced when
    # creating (no `existing` row for this user); updates of your own
    # record aren't blocked.
    if not existing and user_phone:
        phone_match = (lambda _r: _r[0] if _r else None)(
            await db_supabase.get_rows("drivers", {"phone": user_phone}, limit=1)
        )
        if phone_match and phone_match.get("user_id") != user_id:
            raise HTTPException(
                status_code=409,
                detail="A driver account with this phone already exists. Log in to that account instead.",
            )

    # Fields the client is allowed to set on register
    allowed = {
        "first_name",
        "last_name",
        "email",
        "gender",
        "city",
        "service_area_id",
        "vehicle_type_id",
        "vehicle_make",
        "vehicle_model",
        "vehicle_color",
        "vehicle_year",
        "license_plate",
        "vehicle_vin",
        "license_number",
        "license_expiry_date",
        "insurance_expiry_date",
        "vehicle_inspection_expiry_date",
        "background_check_expiry_date",
        "work_eligibility_expiry_date",
        "documents",
    }
    payload = {k: v for k, v in body.items() if k in allowed and v is not None}

    if existing:
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["submitted_at"] = datetime.now(timezone.utc).isoformat()
        await db_supabase.update_one("drivers", {"id": existing["id"]}, await _shared._encrypt_driver_pii(payload))
        driver = await db_supabase.get_driver_by_id(existing["id"])
        return serialize_doc(await _shared._decrypt_driver_pii(driver))

    # Create new row
    import uuid as _uuid

    new_driver = {
        "id": str(_uuid.uuid4()),
        "driver_code": generate_driver_code(),
        "user_id": user_id,
        "name": full_name,
        "first_name": _first_name_split or None,
        "last_name": _last_name_split or None,
        "phone": current_user.get("phone", ""),
        "rating": 5.0,
        "total_rides": 0,
        "is_online": False,
        "is_available": False,
        "is_verified": False,
        "status": "pending",
        "lat": 0.0,
        "lng": 0.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    await db_supabase.insert_one("drivers", await _shared._encrypt_driver_pii(new_driver))

    # Canonicalize is_driver flag. is_rider is intentionally NOT cleared here
    # so that a driver who already has is_rider=true keeps dual-role status.
    if not current_user.get("is_driver"):
        try:
            # users table has no updated_at column (supabase_schema.sql).
            await db_supabase.update_one(
                "users",
                {"id": user_id},
                {"role": "driver", "is_driver": True},
            )
        except Exception as exc:
            logger.error(
                f"register_driver: failed to flip users.role for {user_id}: {exc}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="Driver registration partially failed. Please try again.",
            ) from exc

    return serialize_doc(new_driver)


# Push token registration happens via POST /notifications/register-token
# (routes/notifications.py), which writes to both push_tokens and users.fcm_token.
# The previous POST /drivers/push-token duplicated that surface without the
# push_tokens row, so it was removed to keep a single registration path.
#
# Driver online/offline toggling happens via PUT /drivers/{driver_id}/status
# (further down in this file). POST /drivers/status was a never-wired
# duplicate and has been removed.


# ── Destination Mode ─────────────────────────────────────────────────


class SetDestinationRequest(BaseModel):
    address: str
    lat: float
    lng: float


@router.post("/destination")
async def set_destination_mode(req: SetDestinationRequest, current_user: dict = Depends(get_current_user)):
    """Set driver's preferred destination. Ride matching will prioritize
    rides heading toward this destination to reduce empty miles."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await _deps.db.update_one(
        "drivers",
        {"id": driver["id"]},
        {
            "destination_mode": True,
            "destination_address": req.address,
            "destination_lat": req.lat,
            "destination_lng": req.lng,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "success": True,
        "destination_mode": True,
        "destination_address": req.address,
    }


@router.delete("/destination")
async def clear_destination_mode(current_user: dict = Depends(get_current_user)):
    """Clear driver's destination mode."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    await _deps.db.update_one(
        "drivers",
        {"id": driver["id"]},
        {
            "destination_mode": False,
            "destination_address": None,
            "destination_lat": None,
            "destination_lng": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {"success": True, "destination_mode": False}


@router.get("/destination")
async def get_destination_mode(current_user: dict = Depends(get_current_user)):
    """Get driver's current destination mode status."""
    driver = await _deps.db.find_one("drivers", {"user_id": current_user["id"]})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    return {
        "destination_mode": driver.get("destination_mode", False),
        "destination_address": driver.get("destination_address"),
        "destination_lat": driver.get("destination_lat"),
        "destination_lng": driver.get("destination_lng"),
    }
