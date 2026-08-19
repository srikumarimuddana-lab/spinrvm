"""Server-side GPS spoofing detection.

Heuristic checks applied to every driver location update. A single failed
check doesn't ban the driver — it flags the point as untrusted so dispatch
and billing logic can act accordingly.

Checks:
1. Mock flag (Android `mocked=true` forwarded by client)
2. Impossible speed (> 300 km/h between consecutive points)
3. Accuracy sanity (0 or > 500 m — GPS chipsets don't produce these normally)
4. Teleportation (> 10 km jump in < 10 s)
"""

from __future__ import annotations

import logging
import math
import time
from typing import Optional

try:
    from .redis_client import redis_get, redis_set
except ImportError:
    from utils.redis_client import redis_get, redis_set  # type: ignore

logger = logging.getLogger(__name__)

MAX_SPEED_KMH = 300
MAX_ACCURACY_METERS = 500
TELEPORT_THRESHOLD_KM = 10
TELEPORT_MIN_SECONDS = 10
_CACHE_TTL = 120  # keep last known point for 2 min


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def evaluate_gps_plausibility(
    lat: float,
    lng: float,
    *,
    prev_lat: Optional[float] = None,
    prev_lng: Optional[float] = None,
    elapsed_seconds: Optional[float] = None,
    speed: Optional[float] = None,
    accuracy: Optional[float] = None,
    mocked: Optional[bool] = None,
) -> tuple[bool, Optional[str]]:
    """Pure (no I/O) plausibility check — same rules as
    ``check_location_integrity`` below (mock flag, impossible speed, accuracy
    sanity, teleportation) but with the "previous point" passed in explicitly
    instead of read from Redis.

    ``check_location_integrity`` is the right call for a single live-ping
    caller (v1 REST, WS single-ping) that has no other source for "the
    driver's last point" and needs Redis to supply it. A caller that already
    knows its own point ordering and a same-request last-known point — e.g.
    the v2 location-batch path, checking N consecutive points against each
    other and against the driver's already-fetched last DB position — should
    call this instead: it lets the batch loop run purely in memory, with zero
    Redis round trips per point (a batch capped at 500 points must not turn
    into up to 500 sequential Redis calls on the 150ms-budget location-write
    path). Same thresholds and constants as ``check_location_integrity``, so
    the two never drift.
    """
    if mocked:
        return False, "mock_location"

    if accuracy is not None:
        if accuracy == 0:
            return False, "zero_accuracy"
        if accuracy > MAX_ACCURACY_METERS:
            return False, "low_accuracy"

    if speed is not None and speed > (MAX_SPEED_KMH / 3.6):
        return False, "impossible_speed"

    if (
        prev_lat is not None
        and prev_lng is not None
        and elapsed_seconds is not None
        and 0 < elapsed_seconds < TELEPORT_MIN_SECONDS
    ):
        dist = _haversine_km(prev_lat, prev_lng, lat, lng)
        if dist > TELEPORT_THRESHOLD_KM:
            return False, "teleport"

    return True, None


async def check_location_integrity(
    driver_id: str,
    lat: float,
    lng: float,
    speed: Optional[float] = None,
    accuracy: Optional[float] = None,
    mocked: Optional[bool] = None,
) -> tuple[bool, Optional[str]]:
    """Return (trusted, reason). If trusted is False, the point should be discarded."""

    # Truthy check, not `is True` — the mock flag reaches this function via
    # raw dict access from client-supplied JSON (no Pydantic bool coercion
    # upstream), so a client sending `1` or `"true"` instead of a literal
    # JSON boolean must still be caught. `mocked is True` let both of those
    # sail through, defeating the entire point of this check.
    if mocked:
        logger.warning("[location_integrity] mock flag detected driver=%s", driver_id)
        return False, "mock_location"

    if accuracy is not None:
        if accuracy == 0:
            return False, "zero_accuracy"
        if accuracy > MAX_ACCURACY_METERS:
            return False, "low_accuracy"

    if speed is not None and speed > (MAX_SPEED_KMH / 3.6):
        return False, "impossible_speed"

    # Teleportation check against last known point in Redis
    cache_key = f"loc:last:{driver_id}"
    try:
        prev_raw = await redis_get(cache_key)
        if prev_raw:
            parts = prev_raw.split(",")
            if len(parts) == 3:
                prev_lat, prev_lng, prev_ts = float(parts[0]), float(parts[1]), float(parts[2])
                elapsed = time.time() - prev_ts
                if 0 < elapsed < TELEPORT_MIN_SECONDS:
                    dist = _haversine_km(prev_lat, prev_lng, lat, lng)
                    if dist > TELEPORT_THRESHOLD_KM:
                        logger.warning(
                            "[location_integrity] teleport detected driver=%s dist=%.1fkm elapsed=%.1fs",
                            driver_id,
                            dist,
                            elapsed,
                        )
                        return False, "teleport"
    except Exception as exc:
        logger.debug("[location_integrity] redis_get failed: %s", exc)

    # Store current point
    try:
        await redis_set(cache_key, f"{lat},{lng},{time.time()}", ttl=_CACHE_TTL)
    except Exception as exc:
        logger.debug("[location_integrity] redis_set failed: %s", exc)

    return True, None
