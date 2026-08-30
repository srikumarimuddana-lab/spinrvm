"""Single source of truth for "which incentives apply to this ride".

The same matching rule was reimplemented five times — twice on the paths that
actually *write* ``ride_incentive_claims`` (``routes/drivers/ride_complete.py``
and ``routes/rides/lifecycle.py``) and three times on the paths that only
*display* a projected bonus to the driver (dispatch's offer payload, the
offer-card notification banner, and the active-ride read). The two families
had drifted apart in ways that all leaned the same direction — the driver was
shown a bonus larger than the one settlement would ever pay:

1. **Zero-value incentives.** The settlement paths skip a row whose
   ``bonus_amount`` is ``<= 0``; the display paths counted it, so a disabled-
   by-zeroing incentive still rendered a ``+$0.00`` chip.
2. **Rides with no service area.** When ``ride.service_area_id`` is unset the
   settlement paths restrict to globally-scoped incentives
   (``service_area_id IS NULL``); the display paths applied no area filter at
   all and so promised *every* area's incentives, none of which would be
   claimed.

This module encodes the settlement rule verbatim and the display paths call
it, so the number quoted to a driver at offer time is the number settlement
will pay at completion. The settlement paths are deliberately left writing
their own claim rows — this changes what drivers are *told*, never what they
are *paid*.

``db`` is passed in rather than imported so the dispatch hot path can hand
over its own injected ``_deps.db_supabase`` (the reason ``offer_card.py``
gave for duplicating the lookup in the first place).
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Tuple

try:
    from ..repositories._base import _postgrest_or_value
except ImportError:
    from repositories._base import _postgrest_or_value  # type: ignore

_TWO_PLACES = Decimal("0.01")

# Superset of the columns the call sites need: ``id`` for a claim row, the
# rest for the driver-facing chip. One select keeps every caller on the same
# row shape.
INCENTIVE_SELECT = "id, name, bonus_amount, incentive_type, service_area_id, vehicle_type_id"


def _d(v: Any) -> Decimal:
    """Convert any numeric value to Decimal without float precision loss."""
    return Decimal(str(v))


def _round(v: Decimal) -> Decimal:
    """Round a Decimal to 2 decimal places (ROUND_HALF_UP)."""
    return v.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


async def match_ride_incentives(db, ride: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the active ``ride_incentives`` rows this ride would claim.

    Mirrors the settlement filter exactly: active, scoped to the ride's
    service area (or globally scoped when the ride has none), matching the
    ride's vehicle type (or untyped), and worth more than zero.

    Raises on a DB failure rather than returning an empty list — an empty
    result and a failed lookup mean very different things to a driver being
    quoted a bonus, and each caller already decides how to degrade.
    """
    sa_id = ride.get("service_area_id")
    vt_id = ride.get("vehicle_type_id")

    query = (
        db.supabase.table("ride_incentives")
        .select(INCENTIVE_SELECT)
        .eq("is_active", True)
    )
    if sa_id:
        # Escaped per CLAUDE.md's query-filter convention: the layer owns
        # escaping the reserved `,()"\` characters so a malformed id can't
        # widen the or-clause.
        query = query.or_(
            f"service_area_id.is.null,service_area_id.eq.{_postgrest_or_value(sa_id)}"
        )
    else:
        query = query.is_("service_area_id", "null")

    result = await db.run_sync(query.execute)

    matched: List[Dict[str, Any]] = []
    for inc in result.data or []:
        if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
            continue
        if _d(inc.get("bonus_amount") or 0) <= 0:
            continue
        matched.append(inc)
    return matched


def incentive_display_payload(
    rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], float]:
    """Shape matched rows into the ``(incentives, total_bonus)`` the apps read.

    ``incentive_type`` defaults to ``per_ride`` because the driver clients
    type it as always-present.
    """
    items = [
        {
            "name": r.get("name") or "Incentive",
            "bonus_amount": float(_round(_d(r.get("bonus_amount") or 0))),
            "incentive_type": r.get("incentive_type") or "per_ride",
        }
        for r in rows
    ]
    total = _round(sum((_d(r.get("bonus_amount") or 0) for r in rows), Decimal("0")))
    return items, float(total)
