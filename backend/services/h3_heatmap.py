"""Aggregate ride GPS into H3 cells for heatmaps (PIPEDA k-anonymity).

Exact pickup/dropoff coordinates must not ship to the admin heat map or
the driver demand overlay. Cell centroids + counts at resolution 9 are
the replacement payload; cells below ``k_floor`` are dropped entirely.

The driver-app ``points: [[lat, lng, weight]]`` shape is preserved so a
flag flip does not require a mobile deploy.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional

try:
    from ..utils.h3_cells import DEFAULT_HEATMAP_RESOLUTION, boundary, cell_for, centroid
except ImportError:  # pragma: no cover
    from utils.h3_cells import DEFAULT_HEATMAP_RESOLUTION, boundary, cell_for, centroid  # type: ignore


def _parse_created(value: Any, now: datetime) -> datetime:
    if not value:
        return now
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return now


def aggregate_rides(
    rides: Iterable[dict],
    *,
    lat_key: str,
    lng_key: str,
    k_floor: int,
    res: int = DEFAULT_HEATMAP_RESOLUTION,
    decay_half_life_days: float = 0.0,
    now: Optional[datetime] = None,
    weight_fn: Optional[Callable[[dict, float], float]] = None,
) -> dict[str, Any]:
    """Return centroid points plus hex metadata. Cells with count < k_floor vanish."""
    now = now or datetime.now(timezone.utc)
    floor = max(int(k_floor), 3)
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0.0, "weight": 0.0})
    skipped = 0
    total = 0
    for ride in rides:
        lat, lng = ride.get(lat_key), ride.get(lng_key)
        if lat is None or lng is None:
            skipped += 1
            continue
        try:
            cell = cell_for(float(lat), float(lng), res)
        except ValueError:
            skipped += 1
            continue
        total += 1
        age_days = max((_parse_created(ride.get("created_at"), now) - now).total_seconds() / -86400.0, 0)
        if weight_fn is not None:
            weight = float(weight_fn(ride, age_days))
        elif decay_half_life_days > 0:
            weight = 0.5 ** (age_days / decay_half_life_days)
        else:
            weight = 1.0
        buckets[cell]["count"] += 1
        buckets[cell]["weight"] += weight

    points: list[list[float]] = []
    cells_out: list[dict[str, Any]] = []
    suppressed = 0
    for cell, stats in buckets.items():
        if stats["count"] < floor:
            suppressed += 1
            continue
        clat, clng = centroid(cell)
        weight = round(stats["weight"], 2)
        points.append([clat, clng, weight])
        cells_out.append(
            {
                "h3": cell,
                "count": int(stats["count"]),
                "weight": weight,
                "lat": clat,
                "lng": clng,
                "boundary": boundary(cell),
            }
        )
    return {
        "points": points,
        "cells": cells_out,
        "k_floor": floor,
        "resolution": res,
        "aggregated": True,
        "stats": {
            "input_rides": total,
            "skipped_invalid": skipped,
            "cells_emitted": len(cells_out),
            "cells_suppressed": suppressed,
        },
    }
