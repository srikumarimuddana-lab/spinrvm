"""Pure incremental-distance helper for the Period-1 scalar accumulator.

Sums the road-less driven distance across ONE location batch the driver app
sends while online with no active ride (insurance Period 1). Low-accuracy fixes
are dropped and near-stationary jitter (a parked car drifting) is collapsed
first, so idling never inflates the deadhead total. Returns kilometres.

Pure: no DB, no network, never mutates inputs, no coordinates retained by the
caller (only the scalar km is stored).
"""

from __future__ import annotations

from typing import Any, Dict, List

try:
    from ..geo_utils import calculate_distance
    from .gps_filtering import (
        collapse_stationary_clusters,
        filter_low_accuracy,
        filter_teleportation_spikes,
        point_epoch_seconds,
    )
except ImportError:  # python -m backend.server vs top-level
    from geo_utils import calculate_distance  # type: ignore
    from utils.gps_filtering import (  # type: ignore
        collapse_stationary_clusters,
        filter_low_accuracy,
        filter_teleportation_spikes,
        point_epoch_seconds,
    )


def _normalize(point: Dict[str, Any]) -> Dict[str, Any]:
    """Map a raw location-batch point to the {lat,lng,accuracy,speed,ts} shape
    the gps_filtering helpers read. Accepts both latitude/longitude and lat/lng.

    ``ts`` is the capture time in epoch seconds, parsed from whichever shape the
    caller sent: v2 outbox rows carry ``datetime`` objects, legacy v1 payloads
    carry ISO-8601 strings or numeric epochs. It MUST be carried through —
    ``filter_teleportation_spikes`` needs elapsed time to judge implied speed,
    and a normalised point without it silently degrades that filter to its
    coarse distance-only fallback (a 5 km spike then reads as real movement).
    """
    lat = point.get("lat")
    if lat is None:
        lat = point.get("latitude")
    lng = point.get("lng")
    if lng is None:
        lng = point.get("longitude")
    return {
        "lat": lat,
        "lng": lng,
        "accuracy": point.get("accuracy"),
        "speed": point.get("speed"),
        "ts": point_epoch_seconds(point),
    }


def batch_incremental_distance_km(points: List[Dict[str, Any]]) -> float:
    """Filtered haversine length of a Period-1 location batch, in km.

    Returns 0.0 for an empty/degenerate batch. Drops low-accuracy fixes and
    collapses stationary jitter before summing so a driver sitting still adds
    nothing to their deadhead distance.
    """
    if not points or len(points) < 2:
        return 0.0

    normalized = [_normalize(p) for p in points]
    kept, _dropped, _null = filter_low_accuracy(normalized)
    kept, _spikes = filter_teleportation_spikes(kept)
    collapsed, _clusters = collapse_stationary_clusters(kept)

    total = 0.0
    prev = None
    for point in collapsed:
        lat, lng = point.get("lat"), point.get("lng")
        if lat is None or lng is None:
            continue
        try:
            cur = (float(lat), float(lng))
        except (TypeError, ValueError):
            continue
        if prev is not None:
            total += calculate_distance(prev[0], prev[1], cur[0], cur[1])
        prev = cur
    return round(total, 3)
