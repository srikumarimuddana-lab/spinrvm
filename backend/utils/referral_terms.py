"""Per-service-area referral terms resolution (rider + driver).

Single source of truth for "what reward applies to this referral, in this
service area". Used by:
  - the rider/driver referral display endpoints (routes/users.py,
    routes/drivers.py) to SHOW the right numbers, and
  - the payout loop (utils/referral_payout.py) to PAY the right numbers and
    snapshot the area onto the referral_payouts ledger row.

Resolution:
  - service_areas carries per-area columns (migration 173). When a rider/driver
    resolves to an area whose columns are set, those win.
  - When no area is resolved (NULL / outside all areas / brand-new rider with no
    rides), or the area row is missing, fall back to the global constants in
    routes/users.py + routes/drivers.py — the historical default ($5/$5/1,
    $10/10). This keeps behaviour identical for un-mapped users.

Money safety: all reward values are returned as Decimal, never float — callers
write them to the financial ledger.
"""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

try:
    from .. import db_supabase  # type: ignore
except ImportError:
    import db_supabase  # type: ignore

logger = logging.getLogger(__name__)

_TWO = Decimal("0.01")


def _money(v) -> Decimal:
    """Coerce a DB numeric / int / str to a 2dp Decimal (never float)."""
    return Decimal(str(v if v is not None else 0)).quantize(_TWO, rounding=ROUND_HALF_UP)


def _global_terms() -> dict:
    """The historical global constants, used when no area override applies.

    Lazy import: routes import utils, so importing routes at module load would
    risk a circular import.
    """
    try:
        from ..routes.drivers import REFERRAL_REWARD_AMOUNT, REFERRAL_RIDES_REQUIRED  # type: ignore
        from ..routes.users import (  # type: ignore
            RIDER_REFEREE_REWARD,
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRER_REWARD,
        )
    except ImportError:
        from routes.drivers import REFERRAL_REWARD_AMOUNT, REFERRAL_RIDES_REQUIRED  # type: ignore
        from routes.users import (  # type: ignore
            RIDER_REFEREE_REWARD,
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRER_REWARD,
        )
    return {
        "rider": {
            "rides": int(RIDER_REFERRAL_RIDES_REQUIRED),
            "referrer": _money(RIDER_REFERRER_REWARD),
            "referee": _money(RIDER_REFEREE_REWARD),
        },
        "driver": {
            "rides": int(REFERRAL_RIDES_REQUIRED),
            "referrer": _money(REFERRAL_REWARD_AMOUNT),
            "referee": _money(0),
        },
    }


# service_areas column → terms mapping, per referral kind.
_AREA_COLUMNS = {
    "rider": {
        "rides": "rider_referral_rides_required",
        "referrer": "rider_referrer_reward",
        "referee": "rider_referee_reward",
    },
    "driver": {
        "rides": "driver_referral_rides_required",
        "referrer": "driver_referral_reward",
        "referee": None,  # drivers have no referee-side reward
    },
}


async def resolve_referral_terms(service_area_id: Optional[str], kind: str) -> dict:
    """Referral terms for ``kind`` ('rider'|'driver') in ``service_area_id``.

    Returns ``{"rides": int, "referrer": Decimal, "referee": Decimal}``. Falls
    back to the global constants when ``service_area_id`` is None, the area row
    is missing, or a per-area column is NULL.
    """
    fallback = _global_terms()[kind]
    if not service_area_id:
        return fallback

    cols = _AREA_COLUMNS[kind]
    try:
        rows = await db_supabase.get_rows("service_areas", {"id": service_area_id}, limit=1)
    except Exception:
        # Never let a config lookup break the referral flow — fall back loudly.
        logger.error(
            "referral_terms: service area lookup failed; using global default",
            exc_info=True,
            extra={"service_area_id": service_area_id, "kind": kind},
        )
        return fallback

    area = rows[0] if rows else None
    if not area:
        return fallback

    def _pick(field: str, default: Decimal) -> Decimal:
        col = cols.get(field)
        if not col:
            return default
        val = area.get(col)
        return _money(val) if val is not None else default

    rides_col = cols["rides"]
    rides_val = area.get(rides_col)
    return {
        "rides": int(rides_val) if rides_val is not None else fallback["rides"],
        "referrer": _pick("referrer", fallback["referrer"]),
        "referee": _pick("referee", fallback["referee"]),
    }


async def area_id_for_rider(rider_user_id: str, since_iso: Optional[str] = None) -> Optional[str]:
    """The rider's current service area, derived from their most recent ride.

    Riders are not pinned to an area, so we use the newest ride that resolved to
    one (optionally only rides created at/after ``since_iso``). Returns None for
    a brand-new rider with no area-resolved rides → caller uses the global
    default.
    """
    filters: dict = {"rider_id": rider_user_id}
    if since_iso:
        filters["created_at"] = {"$gte": since_iso}
    try:
        rows = await db_supabase.get_rows(
            "rides",
            filters,
            order="created_at",
            desc=True,
            limit=20,
            columns="service_area_id,created_at",
        )
    except Exception:
        logger.error(
            "referral_terms: rider area lookup failed; using global default",
            exc_info=True,
            extra={"rider_id": rider_user_id},
        )
        return None
    for r in rows:
        if r.get("service_area_id"):
            return r["service_area_id"]
    return None


async def area_id_for_driver_user(driver_user_id: str) -> Optional[str]:
    """The driver's assigned service area (drivers.service_area_id), or None."""
    try:
        rows = await db_supabase.get_rows("drivers", {"user_id": driver_user_id}, limit=1, columns="service_area_id")
    except Exception:
        logger.error(
            "referral_terms: driver area lookup failed; using global default",
            exc_info=True,
            extra={"driver_user_id": driver_user_id},
        )
        return None
    if rows and rows[0].get("service_area_id"):
        return rows[0]["service_area_id"]
    return None
