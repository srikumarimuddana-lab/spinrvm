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
  * ``filter_teleportation_spikes`` drops fixes implying impossible movement — a
    cell-tower/AGPS/VPN jump reports good accuracy and moves far, so neither of
    the other two filters catches it.
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
    from .datetime_utils import parse_iso_utc
except ImportError:  # pragma: no cover - dual import path
    from geo_utils import calculate_distance  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore

# A fix reporting worse than this horizontal accuracy (metres) cannot support a
# metre-scale distance claim — driving on an open road is typically <15 m, so
# 50 m is a generous ceiling that still excludes the parking-lot/multipath noise
# that inflated the incident trace.
MAX_TRUSTED_ACCURACY_M = 50.0

# Below this reported ground speed (m/s ≈ 2.5 km/h) a fix is treated as
# effectively stationary — walking pace and above is real movement and never
# collapsed.
STATIONARY_SPEED_FLOOR_MPS = 0.7

# Maximum plausible speed between two consecutive fixes (km/h) before the newer
# fix is treated as a teleportation spike — cell-tower fallback, bad AGPS, or
# VPN-shifted location. 200 km/h is well above any legal Saskatchewan road speed
# (max 110 km/h highway) and tolerates momentary GPS catch-up after a tunnel or
# parking garage.
#
# Deliberately NOT named like the codebase's two other speed ceilings, which
# answer different questions and must not be conflated:
#   * ``route_segments.MAX_PLAUSIBLE_SPEED_KPH`` (180) — a route-quality signal
#     on a stored trace; flags a segment, never discards a fix.
#   * ``location_integrity.MAX_SPEED_KMH`` (300) — the anti-spoofing gate that
#     rejects a whole write; loose on purpose so honest noise isn't refused.
# This one discards a fix from a distance sum, so it sits between the two.
# (``route_segments`` imports from this module, so reusing its constant here
# would be a circular import.)
TELEPORT_MAX_SPEED_KMH = 200.0

# Ceiling for a single hop whose elapsed time is unknown (fixes carrying no
# timestamp, or out-of-order ones). Consecutive pings are seconds apart, not
# hours, and Saskatchewan's largest city is ~30 km across, so a hop this long
# with no timing to justify it is almost certainly a spike.
MAX_UNTIMED_HOP_KM = 10.0

# After this many consecutive fixes disagree with the reference, the REFERENCE
# is presumed stale rather than the fixes: that pattern reads as real movement
# across a capture gap (Doze, a backgrounded app, or a run of low-accuracy fixes
# removed upstream), not as a run of bad fixes. The filter re-anchors instead of
# dropping the rest of the batch — see ``filter_teleportation_spikes``.
MAX_CONSECUTIVE_SPIKE_DROPS = 3

# Shortest sampling interval the implied-speed rule will credit (seconds).
# Consecutive fixes routinely share a capture instant or sit microseconds apart
# — fused/batched location providers emit bursts, and device clocks are coarse —
# so a raw elapsed time can be near zero while the two fixes are metres apart,
# which divides out to millions of km/h. Clamping up to the fastest sampling
# rate we actually believe asks the right question of such a pair ("could a
# vehicle cover this ground in one sampling interval?"): normal GPS scatter
# stays, while a hop far too long for one interval is still caught.
MIN_PLAUSIBLE_INTERVAL_S = 1.0

# Epoch values above this are milliseconds, not seconds: 1e12 seconds is the
# year 33658, while 1e12 milliseconds is 2001, so every real second-epoch sits
# below it and every real millisecond-epoch above.
_EPOCH_MS_THRESHOLD = 1e12

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


def point_epoch_seconds(point: Dict[str, Any]) -> Optional[float]:
    """Capture time of a breadcrumb as epoch seconds, or ``None`` if unusable.

    Accepts every timestamp shape the location pipeline actually produces, so a
    caller never has to pre-parse:

      * ``ts`` — already normalised upstream (fast path, see
        ``period1_distance._normalize``)
      * ``datetime`` objects — what the v2 outbox rows carry before they ever
        round-trip through Postgres
      * ISO-8601 strings — legacy v1 payloads and Supabase reads
      * numeric epochs, in seconds or milliseconds (JS ``Date.now()`` clients)

    Key order matches ``breadcrumbs._point_capture_time`` so this reads the same
    field the persistence layer treats as capture time. Returns ``None`` rather
    than raising: a fix with an unreadable timestamp is still a usable fix, it
    just can't support a speed claim.
    """
    for key in ("ts", "captured_at", "device_timestamp", "recorded_at", "timestamp"):
        raw = point.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        if isinstance(raw, (int, float)):
            value = float(raw)
            return value / 1000.0 if abs(value) > _EPOCH_MS_THRESHOLD else value
        parsed = parse_iso_utc(raw)
        if parsed is not None:
            return parsed.timestamp()
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


def filter_teleportation_spikes(
    points: List[Dict[str, Any]],
    max_speed_kmh: float = TELEPORT_MAX_SPEED_KMH,
    *,
    max_untimed_hop_km: float = MAX_UNTIMED_HOP_KM,
    max_consecutive_drops: int = MAX_CONSECUTIVE_SPIKE_DROPS,
) -> Tuple[List[Dict[str, Any]], int]:
    """Drop GPS fixes that imply physically impossible movement.

    Walks the trace and computes the implied speed between consecutive valid
    fixes.  When a fix implies speed > ``max_speed_kmh`` it is treated as a
    teleportation spike (cell-tower fallback, bad AGPS, VPN location shift) and
    dropped.  The *previous* good fix stays the reference for the next
    comparison, so a single wild spike doesn't cascade into dropping the rest
    of the trace.

    A spike that jumps away AND back (the common pattern) loses only the
    outlier point — the return fix is compared against the last *good* point,
    so the distance is near-zero and it passes.

    Two guards keep that reference from going stale and eating real distance:

      * when a pair has no usable elapsed time (no timestamp, or fixes out of
        order) the implied speed is unknowable, so the hop falls back to the
        coarser ``max_untimed_hop_km`` distance cap — and when the interval is
        merely too short to believe, it is clamped to
        ``MIN_PLAUSIBLE_INTERVAL_S`` rather than dividing by ~zero; and
      * after ``max_consecutive_drops`` fixes in a row disagree with the
        reference, the reference itself is presumed stale — that is what real
        movement across a capture gap looks like — and the filter re-anchors on
        the current fix instead of discarding the remainder of the batch.

    Timestamps are read via ``point_epoch_seconds``, which accepts datetimes,
    ISO-8601 strings and numeric epochs; a caller that strips them silently
    reduces this filter to its distance fallback.

    Returns ``(kept, dropped_count)``.  Input rows are never mutated.
    """
    if len(points) < 2:
        return list(points), 0

    kept: List[Dict[str, Any]] = [points[0]]  # first point is always kept
    dropped = 0
    consecutive_drops = 0
    prev_lat, prev_lng = _coord(points[0])
    prev_ts = point_epoch_seconds(points[0])

    for cur in points[1:]:
        c_lat, c_lng = _coord(cur)
        cur_ts = point_epoch_seconds(cur)

        if c_lat is None or prev_lat is None:
            # Can't compute a distance — keep it and let downstream reject it.
            # prev_ts still advances so the NEXT hop measures its own elapsed
            # time instead of one inflated by this gap (an inflated dt hides a
            # spike by making its implied speed look survivable).
            kept.append(cur)
            prev_lat, prev_lng = c_lat, c_lng
            prev_ts = cur_ts
            consecutive_drops = 0
            continue

        dist_km = calculate_distance(prev_lat, prev_lng, c_lat, c_lng)

        if cur_ts is not None and prev_ts is not None and cur_ts >= prev_ts:
            elapsed_s = max(cur_ts - prev_ts, MIN_PLAUSIBLE_INTERVAL_S)
            implied_speed_kmh = dist_km / (elapsed_s / 3600.0)
            implausible = implied_speed_kmh > max_speed_kmh
        else:
            # Elapsed time is unusable: the fixes carry no timestamp, or they
            # arrived out of order, so the interval would be negative. Fall
            # back to distance only.
            implausible = dist_km > max_untimed_hop_km

        if implausible and consecutive_drops < max_consecutive_drops:
            dropped += 1
            consecutive_drops += 1
            continue  # don't update prev — keep last good fix as the reference

        kept.append(cur)
        prev_lat, prev_lng = c_lat, c_lng
        prev_ts = cur_ts
        consecutive_drops = 0

    return kept, dropped


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
