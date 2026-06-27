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
        from ..routes.drivers import (  # type: ignore
            REFERRAL_REWARD_AMOUNT,
            REFERRAL_RIDES_REQUIRED,
            REFERRAL_WINDOW_DAYS,
        )
        from ..routes.users import (  # type: ignore
            RIDER_REFEREE_REWARD,
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRAL_WINDOW_DAYS,
            RIDER_REFERRER_REWARD,
        )
    except ImportError:
        from routes.drivers import (  # type: ignore
            REFERRAL_REWARD_AMOUNT,
            REFERRAL_RIDES_REQUIRED,
            REFERRAL_WINDOW_DAYS,
        )
        from routes.users import (  # type: ignore
            RIDER_REFEREE_REWARD,
            RIDER_REFERRAL_RIDES_REQUIRED,
            RIDER_REFERRAL_WINDOW_DAYS,
            RIDER_REFERRER_REWARD,
        )
    return {
        "rider": {
            "rides": int(RIDER_REFERRAL_RIDES_REQUIRED),
            "referrer": _money(RIDER_REFERRER_REWARD),
            "referee": _money(RIDER_REFEREE_REWARD),
            # Days to reach the threshold before the referral expires; 0 = none.
            "window_days": int(RIDER_REFERRAL_WINDOW_DAYS),
            # No per-area override → caller generates the dynamic T&C sentence.
            "terms": None,
        },
        "driver": {
            "rides": int(REFERRAL_RIDES_REQUIRED),
            "referrer": _money(REFERRAL_REWARD_AMOUNT),
            "referee": _money(0),
            "window_days": int(REFERRAL_WINDOW_DAYS),
            "terms": None,
        },
    }


# service_areas column → terms mapping, per referral kind.
_AREA_COLUMNS = {
    "rider": {
        "rides": "rider_referral_rides_required",
        "referrer": "rider_referrer_reward",
        "referee": "rider_referee_reward",
        # Completion deadline in days (migration 189); NULL → global default.
        "window_days": "rider_referral_window_days",
        # Free-text T&C override (migration 176); NULL → dynamic default.
        "terms": "rider_referral_terms",
    },
    "driver": {
        "rides": "driver_referral_rides_required",
        "referrer": "driver_referral_reward",
        "referee": None,  # drivers have no referee-side reward
        "window_days": "driver_referral_window_days",
        "terms": "driver_referral_terms",
    },
}


async def resolve_referral_terms(service_area_id: Optional[str], kind: str) -> dict:
    """Referral terms for ``kind`` ('rider'|'driver') in ``service_area_id``.

    Returns ``{"rides": int, "referrer": Decimal, "referee": Decimal}``. Falls
    back to the global constants only when the area is genuinely ABSENT —
    ``service_area_id`` is None or the row doesn't exist — or a per-area column
    is NULL.

    A DB/permission/schema error on the lookup is NOT swallowed: it propagates
    so the caller fails loudly. In the payout loop this means the referee is
    retried on the next tick instead of being paid the global default and having
    that wrong amount locked in by the unique claim.
    """
    fallback = _global_terms()[kind]
    if not service_area_id:
        return fallback

    cols = _AREA_COLUMNS[kind]
    rows = await db_supabase.get_rows("service_areas", {"id": service_area_id}, limit=1)
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

    # Completion deadline (days). NULL → global default; 0 is a valid override
    # meaning "no deadline for this area" and must NOT fall back.
    window_col = cols.get("window_days")
    window_val = area.get(window_col) if window_col else None

    # Free-text T&C override: a non-blank value wins; blank/NULL → None so the
    # caller falls back to the dynamically generated default sentence.
    terms_col = cols.get("terms")
    terms_val = area.get(terms_col) if terms_col else None
    terms = terms_val.strip() if isinstance(terms_val, str) and terms_val.strip() else None

    return {
        "rides": int(rides_val) if rides_val is not None else fallback["rides"],
        "referrer": _pick("referrer", fallback["referrer"]),
        "referee": _pick("referee", fallback["referee"]),
        "window_days": int(window_val) if window_val is not None else fallback["window_days"],
        "terms": terms,
    }


async def paid_referral_earnings(referrer_user_id: str, kind: str) -> Optional[Decimal]:
    """Sum of ``referrer_reward`` across this referrer's PAID ``referral_payouts``
    rows of the given kind, or None when no paid row exists yet.

    Earnings are summed from the SNAPSHOTTED ledger amounts (migration 171), not
    recomputed from today's area terms, so a referrer's historical earned total
    can't change retroactively when an admin edits an area's reward/threshold or
    the user moves service areas. Returns None — not 0 — when there are no paid
    rows so callers fall back to the pre-payout estimate (until the payout loop
    pays this referrer there are no paid rows and the estimate is the only
    available signal; the loop runs every 5 min).
    """
    rows = await db_supabase.get_rows(
        "referral_payouts",
        {"referrer_user_id": referrer_user_id, "kind": kind, "status": "paid"},
        columns="referrer_reward",
        limit=10000,
    )
    if not rows:
        return None
    total = Decimal("0")
    for r in rows:
        total += _money(r.get("referrer_reward"))
    return total


async def paid_referee_earnings(referee_user_id: str, kind: str) -> Optional[Decimal]:
    """Sum of ``referee_reward`` across PAID ``referral_payouts`` where this user
    was the REFEREE (signed up with someone's code), or None when no paid row.

    The mirror of ``paid_referral_earnings`` for the referee side — a user is
    referred at most once, so this is 0 or a single snapshotted reward, but the
    sum keeps the shape identical and future-proof. Used to surface the referee's
    own signup bonus in the rider "Refer & Earn" screen (it otherwise only shows
    referrer earnings).
    """
    rows = await db_supabase.get_rows(
        "referral_payouts",
        {"referee_user_id": referee_user_id, "kind": kind, "status": "paid"},
        columns="referee_reward",
        limit=10000,
    )
    if not rows:
        return None
    total = Decimal("0")
    for r in rows:
        total += _money(r.get("referee_reward"))
    return total


async def area_id_for_rider(rider_user_id: str, since_iso: Optional[str] = None) -> Optional[str]:
    """The rider's service area, derived from their most recent COMPLETED ride.

    Riders are not pinned to an area, so we use the newest *completed* ride that
    resolved to one (optionally only rides created at/after ``since_iso``).
    Restricting to completed rides matters for the payout path: it pins the area
    to a ride that actually qualified the referral, so a later started/cancelled
    ride in a different area can't hijack the reward terms. Returns None for a
    rider with no area-resolved completed rides → caller uses the global default.

    A DB error propagates (not swallowed) so the payout loop retries rather than
    silently paying the global default.
    """
    filters: dict = {"rider_id": rider_user_id, "status": "completed"}
    if since_iso:
        filters["created_at"] = {"$gte": since_iso}
    rows = await db_supabase.get_rows(
        "rides",
        filters,
        order="created_at",
        desc=True,
        limit=20,
        columns="service_area_id,created_at",
    )
    for r in rows:
        if r.get("service_area_id"):
            return r["service_area_id"]
    return None


async def area_id_for_driver_user(driver_user_id: str) -> Optional[str]:
    """The driver's assigned service area (drivers.service_area_id), or None.

    A DB error propagates (not swallowed) so the payout loop retries rather than
    silently paying the global default.
    """
    rows = await db_supabase.get_rows("drivers", {"user_id": driver_user_id}, limit=1, columns="service_area_id")
    if rows and rows[0].get("service_area_id"):
        return rows[0]["service_area_id"]
    return None
