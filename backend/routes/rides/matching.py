"""Driver dispatch: matching engine, offer timeouts, search timeout.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps, _shared
from ._deps import (  # noqa: F401
    Optional,
    RideStatus,
    _metric_inc,
    asyncio,
    batch_get_etas,
    datetime,
    dispatch_geo_bounds,
    first_name_only,
    get_service_area_polygon,
    json,
    logger,
    parse_iso_utc,
    rank_by_eta_with_acceptance,
    sign_offer_card_token,
    timezone,
)


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
        ride = await _deps.db_supabase.get_ride(ride_id)
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
        _deps.spawn(_dispatch_retry(ride_id, delay=_dispatch_error_delay(attempt), attempt=attempt + 1))


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
            _pending = await _deps.db_supabase.get_rows(
                "ride_offers", {"ride_id": ride_id, "status": "pending"}, limit=1
            )
        except Exception:
            # Can't tell — prefer re-arming: _dispatch_retry re-checks ride
            # status and claimed drivers are excluded, so a duplicate arm is
            # near-idempotent, while NOT arming risks a stranded ride.
            _pending = []
        if not _pending:
            _deps.spawn(_dispatch_retry(ride_id, delay=_dispatch_error_delay(attempt), attempt=attempt + 1))


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
        ride = await _deps.db_supabase.get_ride(ride_id)
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
    app_settings = await _deps.get_app_settings()
    # Compute offer timeout early so it can be embedded in dispatch payloads —
    # driver-app uses this for the per-offer countdown instead of a cached
    # value, which drifted when admin changed the setting mid-session.
    offer_timeout = int(app_settings.get("ride_offer_timeout_seconds", 15))

    # Algorithm + radius + rating floor + batch config (area overrides app settings).
    algorithm, min_rating, search_radius, max_offers, use_eta = await _shared.dispatch.resolve_matching_config(
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
    all_drivers = await _deps.db_supabase.get_rows(
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
            from ...utils.driver_presence import present_driver_ids as _present_ids  # type: ignore
            from ...utils.redis_client import _get_redis as _check_redis  # type: ignore
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
        from ...utils.redis_client import redis_mget as _redis_mget  # type: ignore
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
            _disp_area = await _deps.db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            # Finding F: child areas (airport sub-regions) inherit subscription_required
            # from their parent so airport pickups in a required city don't bypass the gate.
            _sub_required = bool(_disp_area and _disp_area.get("subscription_required"))
            if not _sub_required and _disp_area and _disp_area.get("parent_service_area_id"):
                _parent_area = await _deps.db_supabase.find_one(
                    "service_areas", {"id": _disp_area["parent_service_area_id"]}
                )
                _sub_required = bool(_parent_area and _parent_area.get("subscription_required"))
            if _sub_required:
                _candidate_ids = [d["id"] for d in all_drivers]
                _active_subs = await _deps.db_supabase.get_rows(
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
                    _plans = await _deps.db_supabase.get_rows(
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
                from ...utils.spinr_pass import area_timezone, exhausted_driver_ids
            except ImportError:
                from utils.spinr_pass import area_timezone, exhausted_driver_ids  # type: ignore

            _q_ids = [d["id"] for d in all_drivers]
            _q_subs = await _deps.db_supabase.get_rows(
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
    drivers_with_distance = _deps.filter_and_rank_drivers(ride, all_drivers, algorithm, min_rating, search_radius)
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
            _casc_area = await _deps.db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
            _casc_map: list = (_casc_area or {}).get("vehicle_cascade_map") or []
            # Fix 6: child areas (airport sub-regions) inherit parent's cascade map
            # when the child row has none configured — mirrors subscription_required inheritance.
            if not _casc_map and (_casc_area or {}).get("parent_service_area_id"):
                _casc_parent = await _deps.db_supabase.find_one(
                    "service_areas", {"id": _casc_area["parent_service_area_id"]}
                )
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
                _casc_pool = await _deps.db_supabase.get_rows(
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
                        from ...utils.driver_presence import (
                            present_driver_ids_checked as _casc_presence_checked,  # type: ignore
                        )
                        from ...utils.redis_client import redis_mget as _casc_mget
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
                        _casc_subs = await _deps.db_supabase.get_rows(
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
                            _casc_plans = await _deps.db_supabase.get_rows(
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
                drivers_with_distance = _deps.filter_and_rank_drivers(
                    ride, _casc_pool, algorithm, min_rating, search_radius
                )
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
        _deps.spawn(_dispatch_retry(ride_id, delay=10, attempt=attempt + 1))
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
        if await _deps.db_supabase.claim_driver_atomic(driver["id"]):
            fresh = await _deps.db_supabase.get_driver_by_id(driver["id"])
            # Revalidate the FULL eligibility set on the freshly-read row, not
            # just is_online. claim_driver_atomic only guards id + is_available,
            # so an admin who suspended the driver or flipped them back to
            # needs_review between the candidate read and the claim would
            # otherwise still get offered — the exact stale-status case the
            # candidate filter (is_verified + status='active') is meant to stop.
            if fresh and fresh.get("is_online") and fresh.get("is_verified") and fresh.get("status") == "active":
                claimed_drivers.append((fresh, eta_sec))
            else:
                await _deps.db_supabase.set_driver_available(driver["id"], True)

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
        await _deps.db_supabase.run_sync(
            lambda: _deps.db_supabase.supabase.table("ride_offers").insert(offer_rows).execute()
        )
    except Exception as e:
        logger.error(f"[DISPATCH] ride_offers insert failed: {e}", exc_info=True)
        for d, _ in claimed_drivers:
            await _deps.db_supabase.set_driver_available(d["id"], True)
        # Re-raise after releasing the claims: no offers exist, so the
        # recovery shell re-arms the retry chain instead of stranding the
        # ride in `searching` (the old `return` armed nothing).
        raise
    _metric_inc("spinr_dispatch_offer_sent_total", by=len(offer_rows))

    # ── Parallel enrichment (shared across all drivers) ───────────
    async def _fetch_rider() -> dict | None:
        try:
            return await _deps.db_supabase.get_user_by_id(ride["rider_id"])
        except Exception as e:
            logger.error(f"[DISPATCH] could not load rider user {ride['rider_id']}: {e}", exc_info=True)
            return None

    async def _fetch_incentives() -> tuple[list, float]:
        try:
            iq = (
                _deps.db_supabase.supabase.table("ride_incentives")
                .select(
                    "id, name, bonus_amount, incentive_type, bonus_type, conditions, service_area_id, vehicle_type_id"
                )
                .eq("is_active", True)
            )
            sa_id = ride.get("service_area_id")
            if sa_id:
                iq = iq.or_(f"service_area_id.is.null,service_area_id.eq.{sa_id}")
            ir = await _deps.db_supabase.run_sync(iq.execute)
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
            sa = await _deps.db_supabase.find_one("service_areas", {"id": sa_id})
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
                qr = await _deps.db_supabase.run_sync(
                    _deps.db_supabase.supabase.table("quest_progress")
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
                f"{_deps._settings.PUBLIC_API_BASE_URL.rstrip('/')}/api/v1/offer-cards/{ride_id}.png?t={_oc_token}"
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
            # Rider-set preference (migration 62). Surfaced to the driver in the
            # offer panel so they know a quiet ride was requested before accepting.
            "quiet_mode": bool(ride.get("quiet_mode")),
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
            await _deps.manager.send_personal_message(dispatch_payload, f"driver_{driver['user_id']}")
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
                _deps.spawn(
                    _deps.send_push_notification(
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
    _deps.spawn(
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
        from ...utils.driver_presence import (
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
        ride = await _deps.db.find_one("rides", {"id": ride_id})
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
            settings = await _deps.get_app_settings()
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
            await _deps.db.update_one(
                "drivers",
                {"id": driver_id},
                {"$set": {"is_online": False, "is_available": False}},
            )
            await clear_presence(driver_id)
            await reset_miss_streak(driver_id)
            # Period 0: driver is fully offline (personal insurance only).
            await _deps.record_period_transition(driver_id, 0)
        else:
            # Normal timeout — release driver back to the available pool.
            # Use set_driver_available() so the is_available ⇒ is_online
            # invariant is enforced (clamps to False if driver went offline
            # between the offer being sent and the timeout firing).
            released = await _deps.db_supabase.set_driver_available(driver_id, available=True)
            # Only record Period 1 (online, no ride) if the release actually
            # made the driver available. If they went offline between offer
            # dispatch and this timeout, set_driver_available clamps
            # is_available→False; their go-offline already logged Period 0, so
            # opening a Period 1 audit row here would falsely reopen an
            # online/commercial-insurance window for an offline driver.
            if isinstance(released, dict) and released.get("is_available"):
                await _deps.record_period_transition(driver_id, 1)

        # Put the ride back in the searching state so it can be
        # re-dispatched or picked up by the next dispatch cycle.
        await _deps.db.update_one(
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
            await _deps.manager.send_personal_message(
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
            driver_row = await _deps.db_supabase.get_driver_by_id(driver_id)
            driver_user_id = (driver_row or {}).get("user_id")
            if driver_user_id:
                if auto_offline:
                    await _deps.manager.send_personal_message(
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
                    await _deps.send_push_notification(
                        driver_user_id,
                        "You're now offline",
                        f"You missed {miss_count} ride offers in a row. "
                        "Tap 'Go Online' when you're ready to drive again.",
                        data={"type": "auto_offline", "reason": "missed_offers"},
                    )
                else:
                    await _deps.manager.send_personal_message(
                        {"type": "ride_offer_expired", "ride_id": ride_id},
                        f"driver_{driver_user_id}",
                    )
        except Exception as e:
            logger.warning(f"[DISPATCH] could not notify driver of offer expiry for ride {ride_id}: {e}")

        # Record this driver's timeout so they are skipped on the next
        # dispatch cycle for this ride (5-minute cooldown).
        try:
            from ...utils.redis_client import redis_set as _redis_set  # type: ignore
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
        from ...repositories.driver_repo import update_acceptance_rate
        from ...utils.driver_presence import (
            clear_presence,
            increment_miss_streak,
            reset_miss_streak,
        )
        from ...utils.redis_client import redis_set as _redis_set
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
        ride = await _deps.db_supabase.get_ride(ride_id)
        if not ride or ride.get("status") != RideStatus.SEARCHING:
            return

        pending = await _deps.db_supabase.run_sync(
            lambda: (
                _deps.db_supabase.supabase.table("ride_offers")
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
        await _deps.db_supabase.run_sync(
            lambda: (
                _deps.db_supabase.supabase.table("ride_offers")
                .update({"status": "expired", "responded_at": now_iso})
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )

        try:
            settings = await _deps.get_app_settings()
            miss_threshold = int(settings.get("auto_offline_miss_threshold", 3))
        except Exception:
            miss_threshold = 3

        # P2: each pending driver's release is independent (distinct driver,
        # no cross-driver ordering), so run them concurrently instead of one
        # serial chain of DB/WS round-trips. Sub-steps within a driver stay
        # sequential (they have their own ordering).
        async def _release_pending_driver(did: str) -> None:
            miss_count = await increment_miss_streak(did)
            await update_acceptance_rate(did, accepted=False)

            auto_offline = miss_count >= miss_threshold
            if auto_offline:
                logger.info(
                    f"[DISPATCH] Auto-offline: driver {did} missed "
                    f"{miss_count} consecutive offers (threshold={miss_threshold})"
                )
                await _deps.db_supabase.set_driver_available(did, False)
                await _deps.db_supabase.run_sync(
                    lambda _did=did: (
                        _deps.db_supabase.supabase.table("drivers")
                        .update({"is_online": False, "is_available": False})
                        .eq("id", _did)
                        .execute()
                    )
                )
                await clear_presence(did)
                await reset_miss_streak(did)
                await _deps.record_period_transition(did, 0)
            else:
                # Only open Period 1 (online, no ride) if the release actually
                # made the driver available. If they went offline between offer
                # dispatch and this timeout, set_driver_available clamps
                # is_available→False; recording Period 1 here would falsely
                # reopen a commercial-insurance window for an offline driver.
                # Mirrors the single-offer guard at _offer_timeout_handler.
                released = await _deps.db_supabase.set_driver_available(did, True)
                if isinstance(released, dict) and released.get("is_available"):
                    await _deps.record_period_transition(did, 1)

            try:
                await _redis_set(f"spinr:offer_skip:{ride_id}:{did}", "1", ttl=300)
            except Exception as e:
                logger.warning(f"Failed to set offer skip key for driver {did}: {e}")
            try:
                drv = await _deps.db_supabase.get_driver_by_id(did)
                uid = (drv or {}).get("user_id")
                if uid:
                    msg_type = "auto_offline" if auto_offline else "ride_offer_expired"
                    await _deps.manager.send_personal_message(
                        {"type": msg_type, "ride_id": ride_id},
                        f"driver_{uid}",
                    )
            except Exception as e:
                logger.warning(f"Failed to send offer_expired WS to driver {did}: {e}")

        await asyncio.gather(*(_release_pending_driver(did) for did in pending_ids))

        if rider_id:
            await _deps.manager.send_personal_message(
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
        current_ride = await _deps.db_supabase.get_ride(r_id)
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
                await _deps.db_supabase.update_ride(
                    r_id,
                    {
                        **base_update,
                        "cancelled_by": "system",
                        "cancellation_type": "no_drivers_found",
                    },
                )
            except Exception as _col_exc:
                logger.warning(f"[AUTO-CANCEL] attribution write failed ({_col_exc}); retrying minimal")
                await _deps.db_supabase.update_ride(r_id, base_update)
            await _deps.manager.send_personal_message(
                {
                    "type": "ride_cancelled",
                    "ride_id": r_id,
                    "reason": "No nearby drivers available. Your ride has been automatically cancelled.",
                },
                f"rider_{current_ride['rider_id']}",
            )
            await _deps.manager.broadcast_ride_status(
                r_id,
                RideStatus.CANCELLED,
                rider_id=current_ride["rider_id"],
                reason="no_drivers_found",
                is_auto=True,
            )
            try:
                await _deps.manager.broadcast_to_admins(
                    {
                        "type": "ride_cancelled",
                        "ride_id": r_id,
                        "reason": "no_drivers_found",
                        "is_auto": True,
                    }
                )
            except Exception as _exc:  # pragma: no cover - best effort
                logger.warning(f"ride timeout admin broadcast failed: {_exc}")
            await _deps.send_push_notification(
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
                    from ...services.guest_notification_service import notify_guest_cancelled
                except ImportError:
                    from services.guest_notification_service import notify_guest_cancelled  # type: ignore
                _deps.spawn(notify_guest_cancelled(dict(current_ride)))
            logger.info(f"Ride {r_id} auto-cancelled after {timeout_seconds}s - no driver found")
    except Exception as e:
        logger.error(f"Ride timeout handler error for {r_id}: {e}", exc_info=True)
