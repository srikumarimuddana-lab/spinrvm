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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

STALE_LOCATION_THRESHOLD = timedelta(seconds=30)

try:
    from ..geo_utils import calculate_distance
    from ..settings_loader import get_app_settings
    from ..utils.driver_presence import present_driver_ids
    from ..utils.redis_client import geo_search_drivers
except ImportError:  # pragma: no cover - allow direct module imports in tests
    from geo_utils import calculate_distance
    from settings_loader import get_app_settings
    from utils.driver_presence import present_driver_ids
    from utils.redis_client import geo_search_drivers


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


def _is_dispatchable_driver(driver: Dict[str, Any]) -> bool:
    """
    Return True iff this driver row should be considered for dispatch.

    Excludes:
      - Legacy demo rows without a real user_id (can never be notified)
      - Rows missing lat/lng (can't compute distance or ETA)
    """
    if not driver.get("user_id"):
        return False
    if driver.get("lat") is None or driver.get("lng") is None:
        return False
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
    pickup_lat = ride["pickup_lat"]
    pickup_lng = ride["pickup_lng"]
    needs_rating = algorithm in ("rating_based", "combined")

    wav_required = bool(ride.get("requires_wav"))

    now = datetime.now(timezone.utc)
    total_before = len(candidate_drivers)
    stale_count = 0

    result: List[Tuple[Dict[str, Any], float]] = []
    for d in candidate_drivers:
        if not _is_dispatchable_driver(d):
            continue
        loc_ts = d.get("location_updated_at")
        if loc_ts:
            try:
                ts = datetime.fromisoformat(str(loc_ts).replace("Z", "+00:00"))
                if (now - ts) > STALE_LOCATION_THRESHOLD:
                    stale_count += 1
                    continue
            except (ValueError, TypeError):
                stale_count += 1
                continue
        else:
            stale_count += 1
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

    if total_before > 0 and stale_count > total_before * 0.5:
        logger.warning(
            "Over 50%% of nearby drivers have stale location data",
            extra={"stale_count": stale_count, "total": total_before},
        )
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
        use_eta = bool(
            area_settings.get("use_eta_ranking")
            if area_settings.get("use_eta_ranking") is not None
            else app_settings.get("use_eta_ranking", True)
        )
        return algorithm, min_rating, search_radius_km, max_offers, use_eta

    async def find_candidate_drivers(self, ride: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Online + available + verified + *present* drivers for this ride.

        Tries the Redis geo index first (O(log N) GEOSEARCH). Falls back to
        the Postgres query if Redis is unavailable or returns no results.

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
        pickup_lat = ride.get("pickup_lat")
        pickup_lng = ride.get("pickup_lng")

        if pickup_lat is not None and pickup_lng is not None:
            try:
                redis_drivers = await geo_search_drivers(pickup_lat, pickup_lng, 15.0, count=50)
                if redis_drivers:
                    logger.info(f"[DISPATCH] Redis geo returned {len(redis_drivers)} candidates")
                    return redis_drivers
            except Exception as exc:
                logger.warning("Redis geo search failed — falling back to Postgres", exc_info=exc)

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
            logger.warning(
                "Presence filter failed, dispatching all DB-online drivers",
                exc_info=exc,
            )
            return rows
        if not present:
            return rows
        return [d for d in rows if d["id"] in present]

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
