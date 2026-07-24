"""Pure GPS breadcrumb filtering for distance/route computation.

The reported incident showed a 1.8 km trip rendered as a 2.6 km jagged loop:
low-accuracy GPS fixes (parking lots, phone in pocket, urban multipath) zigzag
between consecutive pings, and the settlement haversine sum + OSRM map-matching
turn that noise into phantom distance and invented street loops. Those fixes
pass the existing speed/distance/gap caps because each individual hop looks
plausible (a 30 m jump in 4 s is only ~27 km/h).

This module removes the noise BEFORE distance is summed or the trace is
map-matched:

  * ``filter_low_accuracy`` drops fixes whose reported accuracy is worse than a
    trust threshold (they cannot support a metre-scale distance claim).
  * ``collapse_stationary_clusters`` folds a run of near-stationary fixes (a car
    stopped at a light / waiting at pickup) into a single location while
    preserving the elapsed time, so a parked car contributes ~0 km instead of a
    scribble.

Both are pure functions over lists of breadcrumb dicts — no DB, no network, no
mutation of the input rows (copies are returned). CRITICAL: they are only for
the *computation* path. Raw breadcrumbs are the SGI insurance audit trail and
must never be deleted or rewritten in the database; the dropped/collapsed
counts are surfaced in ``route_quality`` for transparency instead.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

try:
    from ..geo_utils import calculate_distance
except ImportError:  # pragma: no cover - dual import path
    from geo_utils import calculate_distance  # type: ignore

# A fix reporting worse than this horizontal accuracy (metres) cannot support a
# metre-scale distance claim — driving on an open road is typically <15 m, so
# 50 m is a generous ceiling that still excludes the parking-lot/multipath noise
# that inflated the incident trace.
MAX_TRUSTED_ACCURACY_M = 50.0

# Below this reported ground speed (m/s ≈ 2.5 km/h) a fix is treated as
# effectively stationary — walking pace and above is real movement and never
# collapsed.
STATIONARY_SPEED_FLOOR_MPS = 0.7

# Consecutive fixes within this radius (metres, widened by each fix's own
# accuracy) of the cluster anchor are candidates to fold together. Sized so a
# stopped vehicle's jitter collapses without swallowing a slow crawl through an
# intersection.
STATIONARY_MIN_RADIUS_M = 15.0


def _coord(point: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    lat = point.get("lat")
    lng = point.get("lng")
    if lat is None or lng is None:
        return None, None
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None, None


def _accuracy_m(point: Dict[str, Any]) -> float:
    """Reported accuracy in metres, or 0.0 when missing (widens radius by 0)."""
    a = point.get("accuracy")
    if a is None:
        return 0.0
    try:
        return float(a)
    except (TypeError, ValueError):
        return 0.0


def _speed_mps(point: Dict[str, Any]) -> Optional[float]:
    s = point.get("speed")
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def filter_low_accuracy(
    points: List[Dict[str, Any]],
    max_accuracy_m: float = MAX_TRUSTED_ACCURACY_M,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Drop fixes with reported accuracy worse than ``max_accuracy_m``.

    Fixes with no ``accuracy`` value (legacy rows, some background samples) are
    KEPT — we can't prove they're bad — but counted separately so the caller can
    disclose how much of the trace was unverifiable.

    Returns ``(kept, dropped_low_accuracy, null_accuracy_kept)``. Input rows are
    never mutated; ``kept`` holds the same dict objects (references), in order.
    """
    kept: List[Dict[str, Any]] = []
    dropped = 0
    null_kept = 0
    for point in points:
        a = point.get("accuracy")
        if a is None:
            null_kept += 1
            kept.append(point)
            continue
        try:
            acc = float(a)
        except (TypeError, ValueError):
            null_kept += 1
            kept.append(point)
            continue
        if acc > max_accuracy_m:
            dropped += 1
            continue
        kept.append(point)
    return kept, dropped, null_kept


def collapse_stationary_clusters(
    points: List[Dict[str, Any]],
    *,
    speed_floor_mps: float = STATIONARY_SPEED_FLOOR_MPS,
    min_radius_m: float = STATIONARY_MIN_RADIUS_M,
) -> Tuple[List[Dict[str, Any]], int]:
    """Fold runs of near-stationary fixes into a single location.

    A run of consecutive fixes each within ``max(min_radius_m, accuracy)`` of
    the cluster anchor AND (when a speed is reported) below ``speed_floor_mps``
    is replaced by two fixes at the cluster centroid: one carrying the first
    fix's timestamp/phase and one carrying the last fix's timestamp/phase. This
    keeps the elapsed time (so per-phase durations are unchanged) while removing
    the intra-cluster zigzag distance. A real crawl (speed at/above the floor,
    or motion beyond the radius) is left untouched.

    Returns ``(new_points, collapsed_count)`` where ``collapsed_count`` is how
    many input rows were removed. Input rows are never mutated — collapsed
    clusters emit shallow copies with lat/lng/accuracy overridden.
    """
    if len(points) < 2:
        return list(points), 0

    result: List[Dict[str, Any]] = []
    collapsed = 0
    i = 0
    n = len(points)
    while i < n:
        anchor = points[i]
        a_lat, a_lng = _coord(anchor)
        if a_lat is None:
            # Invalid coord — pass through untouched, let downstream reject it.
            result.append(anchor)
            i += 1
            continue

        cluster = [anchor]
        j = i + 1
        while j < n:
            candidate = points[j]
            c_lat, c_lng = _coord(candidate)
            if c_lat is None:
                break
            radius_m = max(min_radius_m, _accuracy_m(anchor), _accuracy_m(candidate))
            dist_m = calculate_distance(a_lat, a_lng, c_lat, c_lng) * 1000.0
            speed = _speed_mps(candidate)
            is_stationary = dist_m <= radius_m and (speed is None or speed < speed_floor_mps)
            if not is_stationary:
                break
            cluster.append(candidate)
            j += 1

        if len(cluster) >= 2:
            lat_sum = 0.0
            lng_sum = 0.0
            for c in cluster:
                cl, cn = _coord(c)
                lat_sum += cl  # type: ignore[operator]
                lng_sum += cn  # type: ignore[operator]
            centroid_lat = lat_sum / len(cluster)
            centroid_lng = lng_sum / len(cluster)
            accs = [float(c["accuracy"]) for c in cluster if c.get("accuracy") is not None]
            best_acc = min(accs) if accs else None

            start = dict(cluster[0])
            start["lat"] = centroid_lat
            start["lng"] = centroid_lng
            start["accuracy"] = best_acc
            end = dict(cluster[-1])
            end["lat"] = centroid_lat
            end["lng"] = centroid_lng
            end["accuracy"] = best_acc
            result.append(start)
            result.append(end)
            collapsed += len(cluster) - 2
            i = j
        else:
            result.append(anchor)
            i += 1

    return result, collapsed
