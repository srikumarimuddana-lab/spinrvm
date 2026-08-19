"""Persist driver GPS breadcrumbs from batched (background / REST) uploads.

Historically the WebSocket location handler (``routes/websocket.py``) was the
*only* writer of ``driver_location_history``. The background-task and REST
``POST /drivers/location-batch`` paths updated just the live driver marker and
dropped every point but the last, so any stretch a driver spent with the app
backgrounded produced **zero** breadcrumbs. Trip distance is computed from those
breadcrumbs at settlement, and the per-insurance-period trail is what SGI / the
Saskatchewan Traffic Safety Act audit relies on.

This helper persists the full batch, but it must NOT blindly stamp every point
with the driver's *current* ride/phase: a REST/background batch can carry
buffered points from a previous ride or from an earlier phase (e.g. a crash
reload of the on-device buffer, or a batch spanning the pickup→trip
transition). Restamping those would attach stale navigation/previous-ride
breadcrumbs to the current trip and inflate billable ``trip_in_progress`` and
the audit trail. So we:

  * discard points whose embedded ``ride_id`` belongs to a different ride;
  * discard points captured before the current ride's window (stale);
  * attribute each surviving point's phase from ITS OWN capture timestamp
    against the ride's server-recorded milestones (``driver_accepted_at`` /
    ``driver_arrived_at`` / ``ride_started_at``) — never the client's phase tag,
    which a driver could spoof to inflate billable distance;
  * cap the batch (a driver client must not force an unbounded Supabase insert).

NOTE: ``RIDE_STATUS_TO_PHASE`` mirrors the inline map in ``routes/websocket.py``
(~line 607). Keep the two in sync until the WS handler is refactored to import
this constant.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

try:
    from .datetime_utils import parse_iso_utc
    from .location_integrity import evaluate_gps_plausibility
    from .redis_client import redis_delete, redis_get, redis_set
except ImportError:
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.location_integrity import evaluate_gps_plausibility  # type: ignore
    from utils.redis_client import redis_delete, redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

# B3.1: the WS location handler used to run a rides query on EVERY GPS ping
# (~1/s per online driver) just to resolve the active ride. 5 s of staleness
# is acceptable here — phase attribution is re-derived per point from server
# milestones, and the settlement-time anomaly filter is the billing authority.
_ACTIVE_RIDES_CACHE_TTL = 5

# Ride status → insurance/tracking phase. Mirror of routes/websocket.py.
RIDE_STATUS_TO_PHASE: Dict[str, str] = {
    "driver_assigned": "navigating_to_pickup",
    "driver_accepted": "navigating_to_pickup",
    "driver_arrived": "arrived_at_pickup",
    "in_progress": "trip_in_progress",
}
_ACTIVE_STATUSES = list(RIDE_STATUS_TO_PHASE.keys())

# Mirror the WebSocket batch path's bound so the REST path can't force an
# arbitrarily large single insert (worker stall + breadcrumb-table bloat).
MAX_BREADCRUMB_BATCH = 500
_MAX_CLIENT_CLOCK_SKEW = timedelta(minutes=5)
_MAX_ACTIVE_TRIP_FUTURE_SKEW = timedelta(seconds=30)
_LATE_ROUTE_REFINALIZE_DEBOUNCE = timedelta(seconds=30)
_ACTIVE_RIDE_NOT_PROVIDED = object()


def _active_rides_cache_key(driver_id: str) -> str:
    return f"driver:{driver_id}:active_rides"


async def resolve_active_rides_cached(driver_id: str) -> List[Dict[str, Any]]:
    """Driver's active ride rows, via a 5 s Redis cache (B3.1, <150ms SLA).

    Empty results are cached too — idle online drivers ping constantly and
    are exactly the case that must not hit the rides table per ping. Redis
    being down degrades to the original per-ping query, never to an error.
    """
    key = _active_rides_cache_key(driver_id)
    try:
        raw = await redis_get(key)
        if raw is not None:
            return json.loads(raw)
    except Exception:
        logger.debug("active-rides cache read failed for %s; querying DB", driver_id, exc_info=True)

    active_rides = await db_supabase.get_rows(
        "rides",
        {"driver_id": driver_id, "status": {"$in": _ACTIVE_STATUSES}},
        order="created_at",
        desc=True,
        limit=10,
    )
    try:
        await redis_set(key, json.dumps(active_rides, default=str), ttl=_ACTIVE_RIDES_CACHE_TTL)
    except Exception:
        logger.debug("active-rides cache write failed for %s", driver_id, exc_info=True)
    return active_rides


async def resolve_active_ride(driver_id: str) -> Optional[Dict[str, Any]]:
    """Return the driver's current active ride row, or None."""
    active_rides = await resolve_active_rides_cached(driver_id)
    return active_rides[0] if active_rides else None


async def invalidate_active_rides_cache(driver_id: Optional[str]) -> None:
    """Drop the cached active-rides list for a driver.

    Must be called when a ride becomes attached to a driver (assignment /
    acceptance): a cached empty list would otherwise keep the WS hot path
    blind to the new ride for up to the TTL — pings right at trip start
    would be persisted as online_idle and riders would miss live-location
    fan-out. Best-effort: Redis being down means the cache is also down,
    so there is nothing stale to serve.
    """
    if not driver_id:
        return
    try:
        await redis_delete(_active_rides_cache_key(driver_id))
    except Exception:
        logger.debug("active-rides cache invalidation failed for %s", driver_id, exc_info=True)


def _phase_for_timestamp(ride: Dict[str, Any], ts: Optional[datetime], current_phase: str) -> str:
    """Server-authoritative phase for a point captured at ``ts``.

    Derived from the ride's recorded milestones so a point buffered during
    navigation and uploaded mid-trip is billed as navigation, not trip. Falls
    back to the ride's current phase when the point carries no usable timestamp.
    """
    if ts is None:
        return current_phase
    started = parse_iso_utc(ride.get("ride_started_at"))
    if started and ts >= started:
        return "trip_in_progress"
    arrived = parse_iso_utc(ride.get("driver_arrived_at"))
    if arrived and ts >= arrived:
        return "arrived_at_pickup"
    return "navigating_to_pickup"


def _coord(point: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        v = point.get(k)
        if v is not None:
            try:
                value = float(v)
            except (TypeError, ValueError):
                return None
            return value if math.isfinite(value) else None
    return None


def _valid_lat_lng(lat: float, lng: float) -> bool:
    # Reject invalid ranges and the common no-fix/default coordinate.
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def _bounded_capture_time(captured_at: Optional[datetime], received_at: datetime) -> datetime:
    """Use device capture time only when it is close enough to server receipt time.

    Future device clocks otherwise create future ``driver_location_history.timestamp``
    values that evade timestamp-based cleanup jobs until that future date.
    """
    if captured_at is None:
        return received_at
    if captured_at > received_at + _MAX_CLIENT_CLOCK_SKEW:
        logger.warning(
            "breadcrumb capture time is in the future; using received_at captured_at=%s received_at=%s",
            captured_at.isoformat(),
            received_at.isoformat(),
        )
        return received_at
    return captured_at


@dataclass(frozen=True)
class LocationPointRejection:
    """A permanent per-point rejection that an outbox may safely discard."""

    sequence_number: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"sequence_number": self.sequence_number, "reason": self.reason}


@dataclass(frozen=True)
class LocationBatchAck:
    """Contiguous v2 outbox acknowledgement returned after durable persistence."""

    recording_session_id: str
    acked_through: Optional[int]
    accepted_count: int
    rejected: Tuple[LocationPointRejection, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recording_session_id": self.recording_session_id,
            "acked_through": self.acked_through,
            "accepted_count": self.accepted_count,
            "rejected": [rejection.to_dict() for rejection in self.rejected],
        }


@dataclass(frozen=True)
class LocationBatchPersistResult:
    """Internal persistence result; API callers expose only ``ack``."""

    ack: LocationBatchAck
    inserted_count: int


def _batch_acked_through(sequence_numbers: List[int]) -> Optional[int]:
    """Return the last sequence in the contiguous submitted range."""
    if not sequence_numbers:
        return None

    ordered = sorted(sequence_numbers)
    acked_through = ordered[0]
    for sequence_number in ordered[1:]:
        if sequence_number != acked_through + 1:
            break
        acked_through = sequence_number
    return acked_through


def _point_capture_time(point: Dict[str, Any]) -> Optional[datetime]:
    """Read a v2 sensor timestamp without replacing it with server receipt time."""
    return parse_iso_utc(point.get("captured_at") or point.get("device_timestamp") or point.get("timestamp"))


async def persist_trip_location_batch(
    driver_id: str,
    ride_id: str,
    recording_session_id: str,
    points: List[Dict[str, Any]],
    *,
    active_ride: Optional[Dict[str, Any]] | object = _ACTIVE_RIDE_NOT_PROVIDED,
    driver_last_known: Optional[Dict[str, Any]] = None,
) -> LocationBatchPersistResult:
    """Persist one v2 active-trip location batch with an idempotent acknowledgement.

    The server-provided ``ride_id`` and ``active_ride`` are authoritative. Client
    phase and ride fields are intentionally ignored, while the immutable sensor
    timestamp is kept in ``captured_at`` and server receipt in ``received_at``.

    Every accepted point is run through the same GPS plausibility heuristic
    (mock flag / impossible speed / accuracy sanity / teleportation) as the v1
    REST and WebSocket single-ping paths (``utils/location_integrity``), but
    chained across the WHOLE batch — each point is checked against the last
    point *accepted* before it, not just once for the batch as a whole. The
    boundary pair (driver's last known DB position -> this batch's first
    point) is covered too when the caller passes ``driver_last_known`` (the
    already-fetched ``drivers`` row's ``lat``/``lng``/``updated_at`` — see
    ``routes/drivers/location.py::_persist_v2_location_batch``, which already
    has that row in hand, so this costs no extra DB read).

    Deliberately uses ``evaluate_gps_plausibility`` (pure, no I/O) rather than
    ``check_location_integrity`` (Redis-backed) for the per-point loop: a
    batch capped at 500 points must not turn into up to 500 sequential Redis
    round trips on this endpoint. A rejected point does not become the new
    "last known" baseline — later points are still compared against the last
    point that actually passed, mirroring how ``check_location_integrity``
    never overwrites its cached point on a failed check.
    """
    if not isinstance(points, list) or not points:
        raise ValueError("points must be a non-empty list")
    if len(points) > MAX_BREADCRUMB_BATCH:
        raise ValueError(f"location batch exceeds {MAX_BREADCRUMB_BATCH} points")
    try:
        normalized_session_id = str(uuid.UUID(recording_session_id))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("recording_session_id must be a UUID") from exc

    ride = await resolve_active_ride(driver_id) if active_ride is _ACTIVE_RIDE_NOT_PROVIDED else active_ride
    if not isinstance(ride, dict) or ride.get("id") != ride_id:
        raise ValueError("ride must be assigned to the authenticated driver")

    current_phase = RIDE_STATUS_TO_PHASE.get(ride.get("status", ""), "online_idle")
    window_start = parse_iso_utc(ride.get("driver_accepted_at")) or parse_iso_utc(ride.get("created_at"))
    window_end = parse_iso_utc(ride.get("ride_completed_at"))
    received_at = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    rejections: List[LocationPointRejection] = []
    sequence_numbers: List[int] = []
    seen_sequences = set()

    # Seed the chain with the driver's last known DB position (if the caller
    # has one) so the boundary pair -- pre-batch position -> this batch's
    # first point -- is checked too, not just pairs within the batch.
    prev_lat: Optional[float] = None
    prev_lng: Optional[float] = None
    prev_captured_at: Optional[datetime] = None
    if driver_last_known:
        _last_lat = _coord(driver_last_known, "lat", "latitude")
        _last_lng = _coord(driver_last_known, "lng", "longitude")
        _last_ts = parse_iso_utc(driver_last_known.get("updated_at"))
        if _last_lat is not None and _last_lng is not None and _valid_lat_lng(_last_lat, _last_lng) and _last_ts:
            prev_lat, prev_lng, prev_captured_at = _last_lat, _last_lng, _last_ts

    for point in points:
        if not isinstance(point, dict):
            raise TypeError("location batch points must be objects")
        sequence_number = point.get("sequence_number")
        if isinstance(sequence_number, bool) or not isinstance(sequence_number, int) or sequence_number < 0:
            raise ValueError("sequence_number must be a non-negative integer")
        if sequence_number in seen_sequences:
            raise ValueError("sequence_number must be unique within a batch")
        seen_sequences.add(sequence_number)
        sequence_numbers.append(sequence_number)

        lat = _coord(point, "lat", "latitude")
        lng = _coord(point, "lng", "longitude")
        if lat is None or lng is None or not _valid_lat_lng(lat, lng):
            rejections.append(LocationPointRejection(sequence_number, "invalid_coordinate"))
            continue

        captured_at = _point_capture_time(point)
        if captured_at is None:
            rejections.append(LocationPointRejection(sequence_number, "invalid_capture_time"))
            continue
        if window_end is None and captured_at > received_at + _MAX_ACTIVE_TRIP_FUTURE_SKEW:
            # Future active-trip points can suppress the gap monitor and make a
            # missing route tail look healthy. Completed rides are instead
            # bounded by their immutable server completion timestamp below.
            rejections.append(LocationPointRejection(sequence_number, "future_capture_time"))
            continue
        if window_start is not None and captured_at < window_start:
            rejections.append(LocationPointRejection(sequence_number, "before_ride_window"))
            continue
        if window_end is not None and captured_at > window_end:
            rejections.append(LocationPointRejection(sequence_number, "after_ride_window"))
            continue

        monotonic_ms = point.get("monotonic_ms")
        if monotonic_ms is not None and (isinstance(monotonic_ms, bool) or not isinstance(monotonic_ms, int)):
            rejections.append(LocationPointRejection(sequence_number, "invalid_monotonic_time"))
            continue

        elapsed_seconds = (captured_at - prev_captured_at).total_seconds() if prev_captured_at is not None else None
        trusted, integrity_reason = evaluate_gps_plausibility(
            lat,
            lng,
            prev_lat=prev_lat,
            prev_lng=prev_lng,
            elapsed_seconds=elapsed_seconds,
            speed=point.get("speed"),
            accuracy=point.get("accuracy"),
            mocked=point.get("mocked"),
        )
        if not trusted:
            # No raw lat/lng in the log -- integrity_reason is one of a fixed
            # set of short codes (mock_location / zero_accuracy / low_accuracy
            # / impossible_speed / teleport), never a coordinate.
            logger.warning(
                "location-batch point failed GPS plausibility check "
                "driver_id=%s ride_id=%s sequence_number=%s reason=%s",
                driver_id,
                ride_id,
                sequence_number,
                integrity_reason,
            )
            rejections.append(LocationPointRejection(sequence_number, integrity_reason or "gps_implausible"))
            continue

        # Only a point that actually passed becomes the next comparison
        # baseline -- a rejected point must not let a spoofed jump "reset"
        # the trusted trail for the points that follow it in the batch.
        prev_lat, prev_lng, prev_captured_at = lat, lng, captured_at

        rows.append(
            {
                "driver_id": driver_id,
                "ride_id": ride_id,
                "recording_session_id": normalized_session_id,
                "sequence_number": sequence_number,
                "captured_at": captured_at,
                "received_at": received_at,
                # ``timestamp`` remains populated for legacy settlement readers
                # until the finalizer migrates them to captured_at ordering.
                "timestamp": captured_at,
                "monotonic_ms": monotonic_ms,
                "source": point.get("source") or "driver_app",
                "mocked": bool(point.get("mocked", False)),
                "is_completion_fix": bool(point.get("is_completion_fix", False)),
                "lat": lat,
                "lng": lng,
                "speed": point.get("speed"),
                "heading": point.get("heading"),
                "accuracy": point.get("accuracy"),
                "altitude": point.get("altitude"),
                "tracking_phase": _phase_for_timestamp(ride, captured_at, current_phase),
            }
        )

    inserted: list = []
    if rows:
        try:
            inserted = await db_supabase.insert_many_ignore_conflicts(
                "driver_location_history",
                rows,
                on_conflict="ride_id,driver_id,recording_session_id,sequence_number",
            )
        except Exception:
            # The ON CONFLICT clause requires the unique index from migration
            # 239. If that migration hasn't been applied yet, fall back to a
            # plain INSERT so GPS points still reach the DB. Duplicates are
            # harmless (the finalizer de-dups by sequence_number).
            logger.warning(
                "insert_many_ignore_conflicts failed for ride %s — "
                "falling back to plain insert (migration 239 may be missing)",
                ride_id,
            )
            inserted = await db_supabase.insert_many("driver_location_history", rows)
    # A completed ride can receive a delayed offline batch inside its retention
    # window. Re-finalize only when this call added new evidence; an idempotent
    # replay must not churn revisions or starve the worker's pending queue.
    if inserted and ride.get("status") == "completed":
        try:
            from .metrics import inc as _metric_inc
        except ImportError:
            from utils.metrics import inc as _metric_inc  # type: ignore
        # Post-deploy signal for the completion pre-flush fix: how often a
        # trip's tail still arrives after ride_completed_at. Should trend
        # toward zero as the driver-app flush changes roll out.
        _metric_inc("spinr_drivers_late_tail_batches_total")
        await db_supabase.update_one(
            "ride_routes",
            {"ride_id": ride_id},
            {
                "processing_status": "pending",
                "processing_claimed_at": None,
                "next_retry_at": datetime.now(timezone.utc) + _LATE_ROUTE_REFINALIZE_DEBOUNCE,
                "snapshot_revision": 0,
                "snapshot_object_path": None,
                "snapshot_url": None,
                "finalized_at": None,
            },
            upsert=False,
        )
    ack = LocationBatchAck(
        recording_session_id=normalized_session_id,
        acked_through=_batch_acked_through(sequence_numbers),
        accepted_count=len(rows),
        rejected=tuple(rejections),
    )
    return LocationBatchPersistResult(ack=ack, inserted_count=len(inserted))


async def persist_idle_location_batch(
    driver_id: str,
    recording_session_id: str,
    points: List[Dict[str, Any]],
    *,
    active_ride: Optional[Dict[str, Any]] = None,
) -> tuple["LocationBatchPersistResult", List[Dict[str, Any]]]:
    """Persist one durable Period-1 ("driving around") outbox batch.

    Same ack contract as persist_trip_location_batch, for rows with
    ``ride_id NULL`` / ``tracking_phase 'online_idle'``. Differences by design:
      * ``mocked`` points are rejected terminally (``mocked_fix``) — idle rows
        feed the deadhead audit and the daily rollup, and this path is new, so
        it starts strict rather than shadowing.
      * Points captured at/after the driver's active ride window (if any) are
        rejected as ``ride_active`` — the trip recorder owns those; accepting
        them here would double-write the trip leg as deadhead.
    Returns the persist result plus the accepted rows so the route layer can
    feed the Period-1 distance accumulator without re-validating.
    """
    normalized_session_id = str(uuid.UUID(str(recording_session_id)))
    if not isinstance(points, list) or not points:
        raise ValueError("location batch requires a non-empty list of points")
    if len(points) > MAX_BREADCRUMB_BATCH:
        raise ValueError("location batch exceeds the maximum batch size")

    ride_window_start = None
    if isinstance(active_ride, dict):
        ride_window_start = (
            parse_iso_utc(active_ride.get("driver_accepted_at"))
            or parse_iso_utc(active_ride.get("assigned_at"))
            or parse_iso_utc(active_ride.get("created_at"))
        )

    received_at = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    rejections: List[LocationPointRejection] = []
    sequence_numbers: List[int] = []
    seen_sequences: set = set()

    for point in points:
        if not isinstance(point, dict):
            raise TypeError("location batch points must be objects")
        sequence_number = point.get("sequence_number")
        if isinstance(sequence_number, bool) or not isinstance(sequence_number, int) or sequence_number < 0:
            raise ValueError("sequence_number must be a non-negative integer")
        if sequence_number in seen_sequences:
            raise ValueError("sequence_number must be unique within a batch")
        seen_sequences.add(sequence_number)
        sequence_numbers.append(sequence_number)

        if bool(point.get("mocked", False)):
            rejections.append(LocationPointRejection(sequence_number, "mocked_fix"))
            continue
        lat = _coord(point, "lat", "latitude")
        lng = _coord(point, "lng", "longitude")
        if lat is None or lng is None or not _valid_lat_lng(lat, lng):
            rejections.append(LocationPointRejection(sequence_number, "invalid_coordinate"))
            continue
        captured_at = _point_capture_time(point)
        if captured_at is None:
            rejections.append(LocationPointRejection(sequence_number, "invalid_capture_time"))
            continue
        if captured_at > received_at + _MAX_ACTIVE_TRIP_FUTURE_SKEW:
            rejections.append(LocationPointRejection(sequence_number, "future_capture_time"))
            continue
        if ride_window_start is not None and captured_at >= ride_window_start:
            rejections.append(LocationPointRejection(sequence_number, "ride_active"))
            continue
        monotonic_ms = point.get("monotonic_ms")
        if monotonic_ms is not None and (isinstance(monotonic_ms, bool) or not isinstance(monotonic_ms, int)):
            rejections.append(LocationPointRejection(sequence_number, "invalid_monotonic_time"))
            continue

        rows.append(
            {
                "driver_id": driver_id,
                "ride_id": None,
                "recording_session_id": normalized_session_id,
                "sequence_number": sequence_number,
                "captured_at": captured_at,
                "received_at": received_at,
                "timestamp": captured_at,
                "monotonic_ms": monotonic_ms,
                "source": point.get("source") or "driver_app",
                "mocked": False,
                "is_completion_fix": False,
                "lat": lat,
                "lng": lng,
                "speed": point.get("speed"),
                "heading": point.get("heading"),
                "accuracy": point.get("accuracy"),
                "altitude": point.get("altitude"),
                "tracking_phase": "online_idle",
            }
        )

    inserted: list = []
    if rows:
        try:
            inserted = await db_supabase.insert_many_ignore_conflicts(
                "driver_location_history",
                rows,
                on_conflict="driver_id,recording_session_id,sequence_number",
            )
        except Exception:
            # Requires the migration-333 unique index; fall back to a plain
            # insert so idle points still land (finalizer/rollup de-dup later).
            logger.warning(
                "idle insert_many_ignore_conflicts failed for driver %s — "
                "falling back to plain insert (migration 341 may be missing)",
                driver_id,
            )
            inserted = await db_supabase.insert_many("driver_location_history", rows)

    ack = LocationBatchAck(
        recording_session_id=normalized_session_id,
        acked_through=_batch_acked_through(sequence_numbers),
        accepted_count=len(rows),
        rejected=tuple(rejections),
    )
    return LocationBatchPersistResult(ack=ack, inserted_count=len(inserted)), rows


async def persist_ride_breadcrumbs(
    driver_id: str,
    points: List[Dict[str, Any]],
    *,
    persist_idle: bool = False,
    active_ride: Optional[Dict[str, Any]] | object = _ACTIVE_RIDE_NOT_PROVIDED,
) -> int:
    """Persist GPS points as ``driver_location_history`` breadcrumbs.

    When the driver has an active ride, ride_id + phase are derived/validated
    server-side per point (see module docstring): stale or other-ride points
    are discarded, phase comes from each point's own capture timestamp, and
    the batch is capped at ``MAX_BREADCRUMB_BATCH``. Single live WebSocket
    pings can pass ``persist_idle=True`` to keep the historical online-idle
    breadcrumb behavior when no active ride exists. Returns inserted rows.
    """
    if not isinstance(points, list):
        logger.warning("breadcrumb persist received non-list points for driver_id=%s", driver_id)
        return 0
    points = [p for p in points if isinstance(p, dict)]
    if not points:
        return 0

    ride = await resolve_active_ride(driver_id) if active_ride is _ACTIVE_RIDE_NOT_PROVIDED else active_ride

    v2_points = [point for point in points if "recording_session_id" in point or "sequence_number" in point]
    if v2_points:
        if len(v2_points) != len(points):
            raise ValueError("v2 location batches cannot mix identified and legacy points")
        if not isinstance(ride, dict):
            return 0
        session_ids = {point.get("recording_session_id") for point in points}
        if len(session_ids) != 1:
            raise ValueError("v2 location batch must use one recording session")
        result = await persist_trip_location_batch(
            driver_id,
            str(ride["id"]),
            str(session_ids.pop()),
            points,
            active_ride=ride,
        )
        return result.inserted_count

    ride_id = ride.get("id") if isinstance(ride, dict) else None
    current_phase = (
        RIDE_STATUS_TO_PHASE.get(ride.get("status", ""), "online_idle") if isinstance(ride, dict) else "online_idle"
    )
    # Points captured before the ride was assigned to this driver are stale
    # (previous ride / idle) and must not attach to this trip.
    window_start = (
        parse_iso_utc(ride.get("driver_accepted_at")) or parse_iso_utc(ride.get("created_at"))
        if isinstance(ride, dict)
        else None
    )
    if not isinstance(ride, dict) and not persist_idle:
        return 0

    # Cap before building rows — keep the most recent points if over the bound.
    if len(points) > MAX_BREADCRUMB_BATCH:
        logger.info(
            "location-batch over cap for driver_id=%s ride_id=%s: %d points -> keeping last %d",
            driver_id,
            ride_id,
            len(points),
            MAX_BREADCRUMB_BATCH,
        )
        points = points[-MAX_BREADCRUMB_BATCH:]

    rows: List[Dict[str, Any]] = []
    for p in points:
        lat = _coord(p, "lat", "latitude")
        lng = _coord(p, "lng", "longitude")
        if lat is None or lng is None or not _valid_lat_lng(lat, lng):
            continue
        # Drop obviously spoofed fixes; the heavy anomaly filter (speed /
        # distance / gap caps) runs once at settlement in routes/drivers.py.
        if p.get("mocked") is True:
            continue
        # Discard points that belong to a different ride (stale buffer / prior trip).
        point_ride = p.get("ride_id")
        if point_ride and point_ride != ride_id:
            continue
        captured_at = parse_iso_utc(
            p.get("captured_at") or p.get("device_timestamp") or p.get("recorded_at") or p.get("timestamp")
        )
        received_at = datetime.now(timezone.utc)
        bounded_captured_at = _bounded_capture_time(captured_at, received_at)
        # Discard points captured before this ride's window (pre-ride / stale).
        if captured_at is not None and window_start is not None and bounded_captured_at < window_start:
            continue
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "driver_id": driver_id,
                "ride_id": ride_id,
                "lat": lat,
                "lng": lng,
                "speed": p.get("speed"),
                "heading": p.get("heading"),
                "accuracy": p.get("accuracy"),
                "altitude": p.get("altitude"),
                # Server-authoritative phase from the point's own capture time.
                "tracking_phase": _phase_for_timestamp(ride, bounded_captured_at, current_phase)
                if isinstance(ride, dict)
                else "online_idle",
                "timestamp": bounded_captured_at,
                "received_at": received_at,
            }
        )

    if not rows:
        return 0
    await db_supabase.insert_many("driver_location_history", rows)
    return len(rows)
