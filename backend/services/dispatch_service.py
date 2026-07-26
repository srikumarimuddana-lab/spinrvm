"""
DispatchService — driver-matching logic separated from the HTTP layer.

Extracts the database-bound and pure-computation parts of
``routes.rides.match_driver_to_ride`` into a testable service.

Deliberately OUT OF SCOPE for this extraction:
  - WebSocket fan-out (handled by ``socket_manager.manager`` in the route)
  - Push notifications (handled by ``features.send_push_notification``)
  - Offer-timeout background task (``_offer_timeout_handler`` in the route)

Those are orchestration concerns that belong to ``NotificationService``
(Tier 2.5 in the refactoring playbook). Keeping them in the route means
this service can be unit-tested against a mocked db with no socket /
push / asyncio.create_task machinery in the tests.
"""

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from ..geo_utils import calculate_distance
    from ..settings_loader import get_app_settings
    from ..utils.breadcrumbs import invalidate_active_rides_cache
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.driver_presence import present_driver_ids
    from ..utils.metrics import inc as _metric_inc
    from ..utils.spinr_pass import exhausted_driver_ids
except ImportError:  # pragma: no cover - allow direct module imports in tests
    from geo_utils import calculate_distance
    from settings_loader import get_app_settings
    from utils.breadcrumbs import invalidate_active_rides_cache  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.driver_presence import present_driver_ids
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.spinr_pass import exhausted_driver_ids  # type: ignore


# Valid algorithm values. ``nearest`` is the production default.
# ``combined`` falls back to the nearest sort after passing the rating floor.
VALID_ALGORITHMS = ("nearest", "rating_based", "combined", "round_robin")

DEFAULT_MIN_RATING = 4.0
DEFAULT_SEARCH_RADIUS_KM = 10.0
DEFAULT_MAX_SIMULTANEOUS_OFFERS = 3


def rank_by_eta_with_acceptance(
    drivers_with_eta: List[Tuple[Dict[str, Any], int]],
) -> List[Tuple[Dict[str, Any], int, float]]:
    """Sort drivers by effective_eta = eta_seconds / acceptance_rate.

    Returns [(driver, eta_seconds, effective_eta), ...] ascending.
    Lower effective_eta = closer + more reliable → offered first.
    """
    result = []
    for driver, eta in drivers_with_eta:
        rate = float(driver.get("acceptance_rate") or 1.0)
        rate = max(rate, 0.1)  # floor to avoid division explosion
        effective = eta / rate
        result.append((driver, eta, effective))
    result.sort(key=lambda x: x[2])
    return result


#: km per degree of latitude (WGS-84 mean); longitude shrinks by cos(lat).
_KM_PER_DEG_LAT = 110.574
_KM_PER_DEG_LNG_EQUATOR = 111.320


def dispatch_geo_bounds(pickup_lat: float, pickup_lng: float, radius_km: float) -> List[Dict[str, Any]]:
    """Lat/lng bounding-box clauses for the dispatch candidate query.

    Returns a list of ``{col: {"$gte"/"$lte": val}}`` dicts suitable for a
    ``get_rows`` ``$and`` filter, bounding a box that fully contains the
    ``radius_km`` circle around the pickup. The box is a superset of the
    circle — ``filter_and_rank_drivers`` remains the exact haversine gate —
    so its only job is to keep the SQL candidate fetch geo-relevant: without
    it, an un-geo-filtered ``LIMIT 500`` over all online drivers can return
    500 rows from the far side of the province and report a false
    "no drivers" while the nearest driver sits in row 501.

    A 10% + 1 km margin absorbs the degree-conversion approximation and any
    drift between the raw pin and the road-snapped ``pickup_nav_*`` point.
    Drivers with the default (0, 0) location fall outside any Canadian box,
    matching ``_is_dispatchable_driver`` which already excludes them.
    Longitude wraparound at ±180° is ignored — out of scope for Canada.
    """
    padded_km = radius_km * 1.10 + 1.0
    lat_delta = padded_km / _KM_PER_DEG_LAT
    # Clamp cos(lat) so a corrupt polar latitude can't divide by ~zero.
    lng_delta = padded_km / max(_KM_PER_DEG_LNG_EQUATOR * math.cos(math.radians(pickup_lat)), 1e-3)
    return [
        {"lat": {"$gte": pickup_lat - lat_delta}},
        {"lat": {"$lte": pickup_lat + lat_delta}},
        {"lng": {"$gte": pickup_lng - lng_delta}},
        {"lng": {"$lte": pickup_lng + lng_delta}},
    ]


_LOCATION_STALE_SECONDS = 120


def _is_dispatchable_driver(driver: Dict[str, Any]) -> bool:
    """
    Return True iff this driver row should be considered for dispatch.

    Excludes:
      - Legacy demo rows without a real user_id (can never be notified)
      - Rows missing lat/lng (can't compute distance or ETA)
      - Drivers whose last location update is older than _LOCATION_STALE_SECONDS
        (GPS died / permission revoked while the app stayed connected)
    """
    if not driver.get("user_id"):
        return False
    if driver.get("lat") is None or driver.get("lng") is None:
        return False
    updated_at = driver.get("updated_at")
    if updated_at:
        try:
            if isinstance(updated_at, str):
                ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            else:
                ts = updated_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > _LOCATION_STALE_SECONDS:
                return False
        except (ValueError, TypeError):
            pass
    return True


def _ride_brings_driver_closer_to_destination(driver: Dict[str, Any], ride: Dict[str, Any]) -> bool:
    """
    For destination-mode drivers, gate offers so we only forward rides
    whose dropoff brings the driver closer to their preferred destination.

    A driver who tapped "Heading home" doesn't want to take a fare that
    sends them in the opposite direction — Uber and Lyft both gate this
    with the same closer-to-destination check. Drivers who aren't in
    destination mode return True so the gate is a no-op for them.

    Threshold: the dropoff must be at least 5% closer to the destination
    than the driver's current position is. The 5% buffer absorbs short
    cross-traffic rides that technically reduce great-circle distance
    by a few meters but don't actually progress the driver home.
    """
    if not driver.get("destination_mode"):
        return True
    dest_lat = driver.get("destination_lat")
    dest_lng = driver.get("destination_lng")
    if dest_lat is None or dest_lng is None:
        # destination_mode flag set but no coords stored — fail open so
        # the driver still gets offers rather than going invisible.
        return True
    dropoff_lat = ride.get("dropoff_lat")
    dropoff_lng = ride.get("dropoff_lng")
    if dropoff_lat is None or dropoff_lng is None:
        return True
    driver_to_dest = calculate_distance(driver["lat"], driver["lng"], dest_lat, dest_lng)
    dropoff_to_dest = calculate_distance(dropoff_lat, dropoff_lng, dest_lat, dest_lng)
    if driver_to_dest <= 0:
        return True
    return dropoff_to_dest <= driver_to_dest * 0.95


def filter_and_rank_drivers(
    ride: Dict[str, Any],
    candidate_drivers: List[Dict[str, Any]],
    algorithm: str,
    min_rating: float,
    search_radius_km: float,
) -> List[Tuple[Dict[str, Any], float]]:
    """
    Pure function: filter a candidate pool and attach per-driver distance.

    Returns a list of ``(driver, distance_km)`` tuples for drivers that:
      - Have a user_id and lat/lng (``_is_dispatchable_driver``)
      - Pass the rating floor when the algorithm requires it
      - Are within ``search_radius_km`` of the ride pickup

    No side effects. Safe to call from tests with hand-built dicts.
    """
    # Match against the road-snapped pickup the driver will actually navigate to
    # (pickup_nav_*) when present — for a pin dropped inside a mall/airport the
    # reachable road point can be materially different, so ranking on the raw pin
    # would filter out nearby drivers and produce wrong ETAs. Falls back to the pin.
    pickup_lat = ride["pickup_nav_lat"] if ride.get("pickup_nav_lat") is not None else ride["pickup_lat"]
    pickup_lng = ride["pickup_nav_lng"] if ride.get("pickup_nav_lng") is not None else ride["pickup_lng"]
    needs_rating = algorithm in ("rating_based", "combined")

    wav_required = bool(ride.get("requires_wav"))

    result: List[Tuple[Dict[str, Any], float]] = []
    for d in candidate_drivers:
        if not _is_dispatchable_driver(d):
            continue
        if needs_rating and float(d.get("rating") or 5.0) < min_rating:
            continue
        # Saskatchewan Transportation Act s.22: when the rider requests a WAV,
        # only match drivers whose vehicle has an approved wheelchair lift/ramp.
        if wav_required and not d.get("is_wav"):
            continue
        # P2 destination filter: drivers in destination_mode only see offers
        # whose dropoff brings them closer to their preferred destination.
        # No-op when destination_mode is False or coords are missing.
        if not _ride_brings_driver_closer_to_destination(d, ride):
            continue
        dist_km = calculate_distance(pickup_lat, pickup_lng, d["lat"], d["lng"])
        if dist_km <= search_radius_km:
            result.append((d, dist_km))
    return result


def select_driver_by_algorithm(
    drivers_with_distance: List[Tuple[Dict[str, Any], float]],
    algorithm: str,
    last_assigned_driver_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pure function: pick one driver from ``drivers_with_distance``.

    ``last_assigned_driver_id`` is used only by the ``round_robin``
    algorithm — it's the id of the most recently assigned driver so we
    can pick the next one in the list. Pass None to start from index 0.

    Unknown algorithms fall back to ``nearest`` so a typo in settings
    doesn't silently drop rides on the floor.
    """
    if not drivers_with_distance:
        return None

    if algorithm == "rating_based":
        ranked = sorted(drivers_with_distance, key=lambda x: x[0].get("rating", 5.0), reverse=True)
        return ranked[0][0]

    if algorithm == "round_robin":
        if last_assigned_driver_id is None:
            return drivers_with_distance[0][0]
        last_idx = next(
            (i for i, (d, _) in enumerate(drivers_with_distance) if d["id"] == last_assigned_driver_id),
            -1,
        )
        next_idx = (last_idx + 1) % len(drivers_with_distance)
        return drivers_with_distance[next_idx][0]

    # Default + "nearest" + "combined" all pick the closest driver that
    # passed the (already-applied) rating floor.
    ranked = sorted(drivers_with_distance, key=lambda x: x[1])
    return ranked[0][0]


class DispatchService:
    """
    Driver-matching operations that depend on the database.

    Does not send notifications, does not schedule background tasks —
    callers in the route layer compose this with notification +
    timeout concerns.
    """

    def __init__(self, db):
        self.db = db

    async def resolve_matching_config(
        self,
        ride: Dict[str, Any],
        *,
        app_settings: Optional[Dict[str, Any]] = None,
    ) -> Tuple[str, float, float, int, bool]:
        """
        Return ``(algorithm, min_rating, search_radius_km,
        max_offers, use_eta)`` for this ride.

        Reads ``service_areas`` first (the area can override matching
        behaviour), then falls back to the global ``app_settings``.
        """
        if app_settings is None:
            app_settings = await get_app_settings()

        area_settings: Dict[str, Any] = {}
        if ride.get("service_area_id"):
            area = await self.db.find_one("service_areas", {"id": ride["service_area_id"]})
            if area:
                area_settings = area

        algorithm = area_settings.get("driver_matching_algorithm") or app_settings.get(
            "driver_matching_algorithm", "nearest"
        )
        min_rating = float(
            area_settings.get("min_driver_rating") or app_settings.get("min_driver_rating", DEFAULT_MIN_RATING)
        )
        search_radius_km = float(
            area_settings.get("search_radius_km") or app_settings.get("search_radius_km", DEFAULT_SEARCH_RADIUS_KM)
        )
        max_offers = int(
            area_settings.get("max_simultaneous_offers")
            or app_settings.get("max_simultaneous_offers", DEFAULT_MAX_SIMULTANEOUS_OFFERS)
        )
        max_offers = max(1, min(max_offers, 10))
        # Global false always wins: an operator disabling ETA ranking globally
        # must not have service-area DEFAULT true (set by migration 100) silently
        # re-enable it. Only a service-area explicit true can override a global true.
        global_eta = app_settings.get("use_eta_ranking")
        if global_eta is False:
            use_eta = False
        elif area_settings.get("use_eta_ranking") is not None:
            use_eta = bool(area_settings["use_eta_ranking"])
        else:
            use_eta = bool(global_eta) if global_eta is not None else True
        return algorithm, min_rating, search_radius_km, max_offers, use_eta

    async def find_candidate_drivers(self, ride: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Online + available + verified + *present* drivers for this ride.

        is_verified + status='active' gate unverified / suspended / needs_review
        drivers out of dispatch even if their is_online flag got left on (e.g.
        status flipped server-side after they toggled online).

        Presence filter (Uber/Lyft-style): we only dispatch to drivers whose
        short-TTL Redis heartbeat is still alive. Without this filter, a
        driver whose app was killed mid-shift stays ``is_online=true`` until
        someone notices — and the dispatcher routes a ride to a phone that
        won't ring.

        Safety valve: if Redis is unreachable or returns no presence keys
        at all, we fall back to the raw DB query so a Redis outage can't
        take every driver offline. An empty presence set is an ambiguous
        signal (bootstrap, Redis failover, dev with REDIS_URL unset); we
        prefer dispatching to "online-per-DB" drivers over dispatching to
        nobody.
        """
        driver_filter: Dict[str, Any] = {
            "is_online": True,
            "is_available": True,
            "is_verified": True,
            "status": "active",
            "vehicle_type_id": ride["vehicle_type_id"],
        }
        if ride.get("requires_wav"):
            driver_filter["is_wav"] = True
        rows = await self.db.get_rows(
            "drivers",
            driver_filter,
            limit=500,
        )
        if not rows:
            return rows

        try:
            present = await present_driver_ids([d["id"] for d in rows])
        except Exception as exc:
            # Redis unavailable — skip presence filter but still apply
            # subscription filter below. Emit metric so a sustained outage
            # is visible on dashboards rather than buried at warning level.
            logger.warning(
                "Presence filter failed, dispatching all DB-online drivers",
                exc_info=exc,
            )
            _metric_inc("spinr_dispatch_presence_filter_failed_total")
            present = None  # sentinel: presence filter skipped

        if present is not None:
            if present:
                rows = [d for d in rows if d["id"] in present]
            # else: empty presence set — ambiguous (bootstrap / Redis failover /
            # dev); treat all DB-online drivers as present (fail-open for
            # presence) but still apply the subscription filter below.

        # Spinr Pass guards. Two filters share one batched pass lookup:
        #   1. Subscription requirement — only in pass-required areas, drop
        #      drivers without an active pass.
        #   2. Daily ride allowance — in ANY area, drop finite-pass drivers who
        #      have used today's rides (resets at the next local midnight).
        # One area lookup + one batch IN-query — no N+1 per driver. In free areas
        # with no subscribers the pass query returns empty and both filters no-op.
        if rows and ride.get("service_area_id"):
            try:
                _area = await self.db.find_one("service_areas", {"id": ride["service_area_id"]})
                _svc_sub_required = bool(_area and _area.get("subscription_required"))
                if not _svc_sub_required and _area and _area.get("parent_service_area_id"):
                    _parent = await self.db.find_one("service_areas", {"id": _area["parent_service_area_id"]})
                    _svc_sub_required = bool(_parent and _parent.get("subscription_required"))

                candidate_ids = [d["id"] for d in rows]
                active_subs = await self.db.get_rows(
                    "driver_subscriptions",
                    {"driver_id": {"$in": candidate_ids}, "status": "active"},
                    columns="driver_id,started_at,expires_at,rides_per_day",
                    limit=len(candidate_ids),
                )
                _now = datetime.now(timezone.utc)
                subscribed_ids = set()
                _valid_subs = []
                for _s in active_subs or []:
                    if _s.get("expires_at"):
                        _exp = parse_iso_utc(_s["expires_at"])
                        if _exp is not None and _exp <= _now:
                            continue  # expired
                    subscribed_ids.add(_s["driver_id"])
                    _valid_subs.append(_s)

                # (1) Required-area subscription gate.
                if _svc_sub_required:
                    rows = [d for d in rows if d["id"] in subscribed_ids]

                # (2) Daily ride-allowance filter (all areas). Self-contained —
                # skips unlimited + lapsed passes, empty when nobody holds a
                # finite pass. Backstops force-offline-on-complete (in case that
                # write failed) and covers voluntary passes in non-required areas.
                try:
                    # Anchor the quota day on this ride's service-area timezone
                    # (Regina fallback) so the reset matches the local day.
                    _exhausted = await exhausted_driver_ids(_valid_subs, now=_now, tz=(_area or {}).get("timezone"))
                except Exception:
                    # Fail open on the quota filter: go-online + accept still
                    # gate, so a transient read error must not drop everyone.
                    _exhausted = set()
                    logger.error(
                        "Quota filter failed for area=%s — dispatching unfiltered by quota",
                        ride.get("service_area_id"),
                        exc_info=True,
                    )
                if _exhausted:
                    rows = [d for d in rows if d["id"] not in _exhausted]

                if _svc_sub_required or _exhausted:
                    logger.info(
                        "Pass filter: area=%s required=%s kept %d/%d drivers (%d quota-exhausted)",
                        ride["service_area_id"],
                        _svc_sub_required,
                        len(rows),
                        len(candidate_ids),
                        len(_exhausted),
                    )
            except Exception:
                # Fail open: a DB error here must not block dispatch entirely.
                # The go-online + accept checks are the primary enforcement gates.
                logger.error(
                    "Pass filter failed for area=%s — dispatching unfiltered",
                    ride.get("service_area_id"),
                    exc_info=True,
                )

        return rows

    async def assign_driver_to_ride(self, ride_id: str, driver_id: str, now) -> None:
        """
        Flip a ride from ``searching`` to ``driver_assigned``.

        ``now`` is passed in rather than called internally so tests
        can inject a deterministic timestamp and the timezone policy
        stays the route's responsibility.
        """
        await self.db.update_one(
            "rides",
            {"id": ride_id},
            {
                "$set": {
                    "driver_id": driver_id,
                    "status": "driver_assigned",
                    "assigned_at": now,
                    "driver_notified_at": now,
                    "updated_at": now,
                }
            },
        )
        # The WS location hot path caches the driver's active rides for 5s
        # (B3.1) — drop it so pings right after assignment see the new ride.
        await invalidate_active_rides_cache(driver_id)

    async def last_assigned_driver_id(self) -> Optional[str]:
        """
        Helper for ``round_robin``: find the driver_id of the most
        recently assigned ride. Returns None if no ride has ever been
        assigned (first-dispatch case).
        """
        # assigned_at is indexed by idx_rides_driver_assigned_at (migration 54);
        # ordering by it is semantically correct and uses the index.
        _last_rides = await self.db.get_rows(
            "rides",
            {"driver_id": {"$ne": None}},
            order="assigned_at",
            desc=True,
            limit=1,
        )
        last_ride = _last_rides[0] if _last_rides else None
        return last_ride["driver_id"] if last_ride else None

    async def claim_driver(self, driver_id: str) -> bool:
        """Atomically mark a driver unavailable for dispatch.

        Filters on ``is_available=True`` so only one concurrent dispatcher
        wins the race. Returns True if this caller claimed the driver,
        False if the driver was already taken.
        """
        result = await self.db.update_one(
            "drivers",
            {"id": driver_id, "is_available": True},
            {"$set": {"is_available": False}},
        )
        # Real db returns the updated row dict or None.
        # Test mocks return an object with modified_count.
        # Both are handled: a None result is always False; a dict (no
        # modified_count attr) defaults to 1 so truth follows result; a
        # mock with modified_count=0 evaluates False.
        return bool(result and getattr(result, "modified_count", 1) > 0)

    async def claim_any_driver(self, ranked: List[Tuple[Dict[str, Any], float]]) -> Optional[Dict[str, Any]]:
        """Walk a ranked list of (driver, score) pairs and return the first
        successfully claimed driver, or None if all were already taken."""
        for driver, _score in ranked:
            if await self.claim_driver(driver["id"]):
                return driver
        return None
