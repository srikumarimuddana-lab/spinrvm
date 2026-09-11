"""The distance a receipt may label the fare line with.

Under fare-lock (migration 46's ``fare_breakdown_snapshot``) the rider was
quoted — and charged — the ROAD distance of the planned route at booking, and
``distance_fare`` is that quote. At completion ``ride_complete`` overwrites
``rides.distance_km`` with the GPS-measured value for stats. A receipt that
prints ``distance_km`` beside the locked amount therefore describes one
number with another: ride SPR-T9NYPB (2026-09-11) was quoted on 6.9 km and
its emailed receipt would have read "Distance (12.2 km)" — the map-matched
figure across the GPS holes an app crash left — next to a fare that never
changed. ``routes/rides/receipts.py`` already substitutes the planned distance
for the in-app receipt; this helper gives the email and PDF renderers the
same rule (ADR 016, Phase 0).

The lock is read from the ride's own snapshot rather than the live
``fare_lock_enabled`` flag: a historical ride priced under lock stays labelled
by its basis even if the flag is later turned off.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def fare_basis_distance_km(ride: Dict[str, Any]) -> Optional[Any]:
    """Distance the fare line was priced on.

    Planned (quoted) distance when the ride carries a locked fare snapshot and
    a planned distance; otherwise the ride's ``distance_km`` (the legacy
    behaviour, where the fare was computed from the measured distance).
    Returns the raw column value so callers keep their own Decimal handling.
    """
    snapshot = ride.get("fare_breakdown_snapshot")
    locked = isinstance(snapshot, dict) and bool(snapshot.get("locked_at") or snapshot.get("lines"))
    planned = ride.get("planned_distance_km")
    if locked and planned is not None:
        return planned
    return ride.get("distance_km")
