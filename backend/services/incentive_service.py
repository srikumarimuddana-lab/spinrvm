"""Single source of truth for "which incentives apply to this ride, and for how much".

The matching rule was once copy-pasted five times — twice on the paths that
*write* ``ride_incentive_claims`` and three times on the paths that only
*display* a projected bonus. The display copies had drifted looser, so drivers
were quoted bonuses settlement would never pay. This module holds the rule
once; every path calls it, and ``record_incentive_claims`` writes the rows, so
the quoted figure and the paid figure come from the same evaluation.

Eligibility, in order:

1. ``is_active``
2. service area — the ride's area, or a globally-scoped incentive; an
   area-less ride matches only globally-scoped ones
3. vehicle type — the ride's type, or an untyped incentive
4. a resolved bonus greater than zero (see ``_resolve_bonus``)
5. **gated on the ``incentive_eligibility_enforced`` rollout switch** — the
   ``start_date``/``end_date`` window, the ``conditions`` JSONB, and the
   ``max_budget`` cap

Steps 1-4 are the historical behaviour. Step 5 is new: those three columns
have existed since migration 96 and were honoured by nothing, so a campaign
that ended, or that blew through its budget cap, kept paying forever while the
admin dashboard rendered a budget bar as if it were enforced. Turning that into
real enforcement changes what drivers are *paid*, so it ships dark behind the
flag and can be switched on without a redeploy.

``db`` is passed in rather than imported so the dispatch hot path can hand over
its own injected ``_deps.db_supabase``.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

try:
    from ..repositories._base import _postgrest_or_value
    from ..settings_loader import get_app_settings
    from ..utils.datetime_utils import parse_iso_utc
    from ..utils.money import to_decimal as _d
except ImportError:  # pragma: no cover - dual-import pattern
    from repositories._base import _postgrest_or_value  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.datetime_utils import parse_iso_utc  # type: ignore
    from utils.money import to_decimal as _d  # type: ignore

logger = logging.getLogger(__name__)

# Saskatchewan is UTC-6 year-round (no DST). A peak-hour window is advertised in
# the driver's local time, so evaluating it in UTC would shift it by six hours.
# Mirrors utils/quest_tracker.py's _DEFAULT_TZ. Callers serving a second
# timezone must pass tz_name; there is no per-area lookup here because that
# would add a query to the dispatch hot path for a condition the admin UI
# cannot currently even set.
_DEFAULT_TZ = "America/Regina"

# ``id`` is needed by record_incentive_claims; the rest builds the driver-facing
# chip and drives the filters.
INCENTIVE_SELECT = (
    "id, name, bonus_amount, bonus_type, incentive_type, conditions, "
    "service_area_id, vehicle_type_id, start_date, end_date, max_budget"
)


@dataclass(frozen=True)
class MatchedIncentive:
    """One incentive that applies to a ride, with its bonus already resolved.

    Display and settlement both consume this, so a percentage incentive can
    never be quoted as one number and paid as another.
    """

    id: str
    name: str
    incentive_type: str
    bonus: Decimal


async def _enforcement_enabled(area: Optional[Dict[str, Any]]) -> bool:
    """Resolve the rollout switch for one ride's service area.

    ``enforce = settings.incentive_eligibility_enforced OR
                service_areas.incentive_eligibility_enforced`` (migration 376).

    The per-area column is the staged, city-by-city rollout — incentives are
    configured per area, so the switch governing them lives at the same
    granularity. The global one stays as the fleet-wide master switch. OR, not
    AND: requiring both would make a freshly-enabled area silently do nothing
    until the global was also on, which reads as a broken toggle.

    A ride with no service area has no per-area row, so only the global switch
    can govern it — such a ride matches only globally-scoped incentives anyway.

    Fails CLOSED to today's behaviour (no enforcement) on a read error: a
    settings or area lookup failure must not silently start denying bonuses
    drivers were already quoted, which is the more damaging direction.
    """
    if area and bool(area.get("incentive_eligibility_enforced")):
        return True
    try:
        return bool((await get_app_settings() or {}).get("incentive_eligibility_enforced", False))
    except Exception:
        logger.error("incentive eligibility flag read failed; leaving enforcement off", exc_info=True)
        return False


def _driver_fare_base(ride: Dict[str, Any]) -> Decimal:
    """The amount a ``percentage`` incentive is a percentage OF.

    The driver's fare share — ``driver_earnings`` (fare-only by design), or
    ``total_fare`` for a row that has not been settled yet. At offer time this
    is the booking estimate and at settlement the settled value, so a
    percentage bonus can move between quote and payout exactly as the fare
    itself can. Flat incentives — everything the admin UI can create — are
    unaffected.
    """
    return _d(ride.get("driver_earnings") or ride.get("total_fare") or 0)


def _resolve_bonus(inc: Dict[str, Any], ride: Dict[str, Any], enforce: bool) -> Decimal:
    """Resolve a row's payable bonus in dollars.

    ``bonus_type='percentage'`` means ``bonus_amount`` is a percent of the
    driver's fare share, not a dollar figure. Nothing honoured this before, so
    such a row was quoted and paid as if 10 meant $10 rather than 10%.

    Gated on ``enforce`` like every other eligibility rule, even though paying
    a percentage as dollars is unambiguously a bug: the flag's contract is that
    flipping it is the ONE moment payouts change, so that a bad rollout is a
    setting revert rather than a redeploy. The admin UI only ever writes 'flat'
    (its input is labelled "Bonus Amount ($)" and it has no bonus_type
    control), so this path is reachable only through the API.
    """
    amount = _d(inc.get("bonus_amount") or 0)
    if enforce and (inc.get("bonus_type") or "flat") == "percentage":
        return _d(amount * _driver_fare_base(ride) / Decimal("100"))
    return amount


def _within_date_window(inc: Dict[str, Any], now: datetime) -> bool:
    """True when ``now`` is inside the campaign's [start_date, end_date] window.

    A NULL bound is open-ended. An unparseable timestamp is treated as open —
    a malformed date must not silently stop paying a bonus a driver was quoted.
    """
    for key, ok in (("start_date", lambda d: d <= now), ("end_date", lambda d: d >= now)):
        raw = inc.get(key)
        if not raw:
            continue
        parsed = parse_iso_utc(raw) if isinstance(raw, str) else raw
        if parsed is None:
            logger.error(
                "incentive %s has an unparseable %s (%r); treating the bound as open",
                inc.get("id"), key, raw,
            )
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if not ok(parsed):
            return False
    return True


def _peak_hour_windows(raw: Any, incentive_id: Any) -> Optional[List[Tuple[int, int]]]:
    """Parse ``conditions.peak_hours`` as consecutive [start, end) hour pairs.

    Migration 96 documents the shape as ``[7, 9, 16, 19]`` — 07:00-09:00 and
    16:00-19:00. Returns None (meaning "no constraint") for anything that does
    not parse, so a malformed condition never silently withholds a bonus.
    """
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    if len(raw) % 2 != 0:
        logger.error(
            "incentive %s has an odd-length peak_hours %r; expected [start, end] pairs — "
            "ignoring the constraint",
            incentive_id, raw,
        )
        return None
    try:
        bounds = [int(h) for h in raw]
    except (TypeError, ValueError):
        logger.error("incentive %s has a non-numeric peak_hours %r; ignoring", incentive_id, raw)
        return None
    return list(zip(bounds[::2], bounds[1::2], strict=True))


def _conditions_met(inc: Dict[str, Any], ride: Dict[str, Any], now: datetime, tz_name: str) -> bool:
    """Evaluate the ``conditions`` JSONB.

    Conditions are applied whenever present, regardless of ``incentive_type``:
    the type is a descriptive label an admin picks from a dropdown, and keying
    behaviour off it would mean a ``conditions.min_distance_km`` on a row typed
    ``per_ride`` was silently ignored. ``time_limited`` is served by the date
    window and ``area_boost`` by the service-area filter, so neither needs a
    condition of its own.
    """
    conditions = inc.get("conditions")
    if not isinstance(conditions, dict) or not conditions:
        return True

    min_km = conditions.get("min_distance_km")
    if min_km is not None:
        try:
            # The BOOKED distance, which is what the driver was quoted on at
            # offer time — not actual_distance_km, which only exists after the
            # trip and would let a bonus vanish between quote and payout.
            if _d(ride.get("distance_km") or 0) < _d(min_km):
                return False
        except Exception:
            logger.error(
                "incentive %s has a non-numeric min_distance_km %r; ignoring the constraint",
                inc.get("id"), min_km,
            )

    windows = _peak_hour_windows(conditions.get("peak_hours"), inc.get("id"))
    if windows:
        try:
            local_hour = now.astimezone(ZoneInfo(tz_name)).hour
        except Exception:
            logger.error("unknown timezone %r for incentive %s; ignoring peak_hours", tz_name, inc.get("id"))
            return True
        if not any(start <= local_hour < end for start, end in windows):
            return False
    return True


async def _budget_spent(db, incentive_ids: Sequence[str]) -> Dict[str, Decimal]:
    """Dollars already claimed per incentive, summed from ``ride_incentive_claims``.

    The append-only claims ledger is the source of truth, not the
    ``ride_incentives.budget_used`` counter — that counter was never
    incremented by anything, so every capped campaign rendered "$0 / $500" in
    the admin dashboard no matter how much it had paid out.
    """
    if not incentive_ids:
        return {}
    result = await db.run_sync(
        db.supabase.table("ride_incentive_claims")
        .select("incentive_id, bonus_amount")
        .in_("incentive_id", list(incentive_ids))
        .execute
    )
    spent: Dict[str, Decimal] = {}
    for row in result.data or []:
        key = row.get("incentive_id")
        if key:
            spent[key] = spent.get(key, Decimal("0")) + _d(row.get("bonus_amount") or 0)
    return spent


async def match_ride_incentives(
    db,
    ride: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    tz_name: str = _DEFAULT_TZ,
    enforce: Optional[bool] = None,
    service_area: Optional[Dict[str, Any]] = None,
) -> List[MatchedIncentive]:
    """Return the incentives this ride qualifies for, with bonuses resolved.

    ``enforce`` overrides the rollout switches; leave it None outside tests so
    every call site shares one resolution (see ``_enforcement_enabled``).

    ``service_area`` is the ride's already-fetched ``service_areas`` row. Pass
    it whenever the caller has one — dispatch does — so resolving the per-area
    rollout flag costs nothing on the offer→accept path. Omitted, it is fetched
    here, and only when the global switch has not already decided the answer.

    Raises on a DB failure rather than returning an empty list — an empty
    result and a failed lookup mean very different things to a driver being
    quoted a bonus, and each caller already decides how to degrade.
    """
    now = now or datetime.now(timezone.utc)
    sa_id = ride.get("service_area_id")

    if enforce is None:
        area = service_area
        if area is None and sa_id:
            # Best-effort: an area we cannot read must not flip enforcement ON
            # for a ride that was quoted without it, so a failure here leaves
            # the decision to the global switch.
            try:
                area = await db.find_one("service_areas", {"id": sa_id})
            except Exception:
                logger.error(
                    "incentive eligibility: service_area %s lookup failed; "
                    "falling back to the global switch",
                    sa_id,
                    exc_info=True,
                )
                area = None
        enforce = await _enforcement_enabled(area)

    vt_id = ride.get("vehicle_type_id")

    query = db.supabase.table("ride_incentives").select(INCENTIVE_SELECT).eq("is_active", True)
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

    candidates: List[Tuple[Dict[str, Any], Decimal]] = []
    for inc in result.data or []:
        if inc.get("vehicle_type_id") and inc["vehicle_type_id"] != vt_id:
            continue
        bonus = _resolve_bonus(inc, ride, enforce)
        if bonus <= 0:
            continue
        if enforce:
            if not _within_date_window(inc, now):
                continue
            if not _conditions_met(inc, ride, now, tz_name):
                continue
        candidates.append((inc, bonus))

    if enforce:
        capped = [inc["id"] for inc, _ in candidates if inc.get("max_budget") is not None and inc.get("id")]
        # Only pay for the ledger read when a cap is actually configured, so an
        # uncapped fleet adds nothing to the dispatch hot path.
        spent = await _budget_spent(db, capped) if capped else {}
        remaining: List[Tuple[Dict[str, Any], Decimal]] = []
        for inc, bonus in candidates:
            cap = inc.get("max_budget")
            if cap is not None:
                already = spent.get(inc.get("id"), Decimal("0"))
                if already + bonus > _d(cap):
                    logger.info(
                        "incentive %s skipped: budget cap reached (%s claimed of %s)",
                        inc.get("id"), already, cap,
                    )
                    continue
            remaining.append((inc, bonus))
        candidates = remaining

    return [
        MatchedIncentive(
            id=str(inc.get("id") or ""),
            name=inc.get("name") or "Incentive",
            incentive_type=inc.get("incentive_type") or "per_ride",
            bonus=bonus,
        )
        for inc, bonus in candidates
    ]


def incentive_display_payload(
    matched: Sequence[MatchedIncentive],
) -> Tuple[List[Dict[str, Any]], float]:
    """Shape matched incentives into the ``(incentives, total_bonus)`` apps read."""
    items = [
        {
            "name": m.name,
            "bonus_amount": float(m.bonus),
            "incentive_type": m.incentive_type,
        }
        for m in matched
    ]
    return items, float(sum((m.bonus for m in matched), Decimal("0")))


async def record_incentive_claims(
    db,
    ride_id: str,
    driver_id: str,
    matched: Sequence[MatchedIncentive],
    *,
    now: datetime,
) -> Decimal:
    """Write ``ride_incentive_claims`` rows for `matched` and return the total.

    Idempotent per (ride_id, incentive_id): a re-run skips incentives this ride
    has already claimed, so a retried settlement cannot pay the same bonus
    twice. Both completion paths call this instead of hand-rolling the insert,
    which is what let their eligibility rules drift apart in the first place.
    """
    if not matched:
        return Decimal("0")

    existing = await db.run_sync(
        db.supabase.table("ride_incentive_claims")
        .select("incentive_id")
        .eq("ride_id", ride_id)
        .execute
    )
    already_claimed = {r.get("incentive_id") for r in (existing.data or [])}

    total = Decimal("0")
    touched: List[str] = []
    for m in matched:
        if m.id in already_claimed:
            logger.info("incentive %s already claimed for ride %s; skipping", m.id, ride_id)
            continue
        await db.insert_one(
            "ride_incentive_claims",
            {
                "id": str(uuid.uuid4()),
                "ride_id": ride_id,
                "driver_id": driver_id,
                "incentive_id": m.id,
                "bonus_amount": float(m.bonus),
                "claimed_at": now.isoformat(),
            },
        )
        total += m.bonus
        touched.append(m.id)

    if touched:
        await _refresh_budget_used(db, touched)
    return total


async def _refresh_budget_used(db, incentive_ids: Sequence[str]) -> None:
    """Recompute ``budget_used`` from the claims ledger for display.

    Denormalized mirror only — never authoritative, since it is recomputed from
    the ledger every time rather than incremented. That makes it self-healing
    and keeps the admin dashboard's budget bar honest without introducing a
    counter that can drift. Best-effort: a failure here costs an accurate
    progress bar, never a claim.
    """
    try:
        spent = await _budget_spent(db, incentive_ids)
        for incentive_id in incentive_ids:
            await db.update_one(
                "ride_incentives",
                {"id": incentive_id},
                {"budget_used": float(spent.get(incentive_id, Decimal("0")))},
            )
    except Exception:
        logger.error("budget_used refresh failed for %s", list(incentive_ids), exc_info=True)
