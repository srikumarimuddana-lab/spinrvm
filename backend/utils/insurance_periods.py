"""Driver insurance-period audit logging (M-5).

Every moment a driver spends in the app maps to one of four insurance
periods (0=offline, 1=available, 2=en-route, 3=passenger). SGI and the
Saskatchewan Transportation Act require an append-only audit trail of
those transitions; the schema lives in migration 64.

This module owns the close-and-open atomicity around the partial unique
index `driver_insurance_periods_open` (one open row per driver). Callers
in the ride/driver state machine fire-and-forget into
``record_period_transition`` whenever the driver moves between periods.

Compliance trade-off
--------------------
The CLAUDE.md "do not silently swallow errors" rule has an explicit
exception for compliance-grade audit writes: a missed audit row is
preferable to blocking the driver state machine. Failures here are
logged at ERROR (so they show up in Sentry / log search and can be
backfilled from the rides table) but never raised.
"""

from __future__ import annotations

import logging
from typing import Optional

try:
    from .. import db_supabase
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    import db_supabase  # type: ignore

try:
    from . import metrics as _metrics
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    try:
        from utils import metrics as _metrics  # type: ignore
    except ImportError:  # pragma: no cover
        _metrics = None  # type: ignore

try:
    from ..models.ride_status import RideStatus
except ImportError:  # pragma: no cover - dual-import per CLAUDE.md
    from models.ride_status import RideStatus  # type: ignore

logger = logging.getLogger(__name__)

_VALID_PERIODS = (0, 1, 2, 3)
# PostgreSQL unique_violation SQLSTATE — postgrest-py surfaces this
# verbatim in the error message when an INSERT conflicts with the
# partial unique index on (driver_id) WHERE ended_at IS NULL.
_PG_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        _PG_UNIQUE_VIOLATION in msg or "duplicate key" in msg or "unique constraint" in msg or "already exists" in msg
    )


def _metric_inc(name: str, labels: Optional[dict] = None) -> None:
    if _metrics is None:
        return
    try:
        _metrics.inc(name, labels)
    except Exception:  # noqa: BLE001, S110 - metrics are best-effort by design
        pass


# Ride states that put a driver in Period 2 (en route to pickup). Period 2
# opens on `driver_assigned`, NOT on `driver_accepted`: per CLAUDE.md the
# driver is obligated to the ride the instant `claim_driver_atomic` succeeds
# and the offer is live, not only once they tap Accept.
_EN_ROUTE_STATUSES = frozenset(
    {
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
    }
)

_KNOWN_RIDE_STATUSES = frozenset(s.value for s in RideStatus)


def derive_insurance_period(
    *,
    ride_status: Optional[str] = None,
    is_online: bool = False,
    has_live_offer: bool = False,
) -> int:
    """Map a driver's current situation to their TNC insurance period.

    Single source of truth for CLAUDE.md's Period 0-3 table. Pure: no DB,
    no async, no clock. Keyword-only on purpose — this is a regulatory
    classification and a transposed positional argument would silently
    misstate SGI commercial coverage.

        Period 3  passenger aboard        ride is `in_progress`
        Period 2  en route to pickup      ride is assigned/accepted/arrived,
                                          OR a live offer is outstanding
        Period 1  available, no ride      driver is online, none of the above
        Period 0  offline                 everything else

    `has_live_offer` is what `driver_assigned` means for batch dispatch,
    which holds no `rides.driver_id` link pre-acceptance — the claim lives
    in `ride_offers`. Callers that can see a pending offer must pass it, or
    they will classify an obligated driver as merely available.

    Unknown `ride_status`
    ---------------------
    CLAUDE.md says a `ride.status` outside the enum is a contract violation
    that must surface loudly. It also says an audit write must never block
    the driver state machine (see this module's docstring). Both are
    honoured: an unknown status is logged at ERROR and counted, then
    ignored for the purposes of this mapping — so it degrades toward the
    offer/online signals rather than raising into a driver's go-online
    request or a dispatch tick. It never degrades *upward* into a higher
    period, so a bad row can't invent commercial coverage.
    """
    if ride_status is not None and ride_status not in _KNOWN_RIDE_STATUSES:
        logger.error(
            "insurance period derivation saw a ride status outside RideStatus: %r. "
            "Treating as no active ride; fix the writer of this value.",
            ride_status,
        )
        _metric_inc("spinr_rides_unknown_status_total", {"source": "insurance_period_derivation"})
        # No reassignment needed: an unrecognised value already fails both
        # the IN_PROGRESS comparison and the _EN_ROUTE_STATUSES membership
        # test below, so it falls through to the offer/online signals on
        # its own. Mutation testing caught an explicit `ride_status = None`
        # here as dead code.

    if ride_status == RideStatus.IN_PROGRESS:
        return 3
    if ride_status in _EN_ROUTE_STATUSES:
        return 2
    if has_live_offer:
        return 2
    if is_online:
        return 1
    return 0


async def record_period_transition(
    driver_id: str,
    new_period: int,
    ride_id: Optional[str] = None,
) -> None:
    """Close the driver's currently-open period (if any) and open a new one.

    Compliance-grade: failures are logged at ERROR but never raised. A
    missed transition row is preferable to blocking the driver state
    machine. The partial unique index on ``(driver_id) WHERE
    ended_at IS NULL`` is the source of truth — racing callers will
    serialise on the index, and the loser turns into a logged warning.

    Args:
        driver_id: The drivers.id PK (NOT users.id).
        new_period: One of 0, 1, 2, 3. See module docstring.
        ride_id: Required when ``new_period == 3``; optional otherwise.

    Raises:
        ValueError: programmer error — invalid period or missing ride_id
            for period 3. These are pre-DB checks; they should never
            fire in production and surfacing them helps catch
            wiring bugs at call sites early.
    """
    if new_period not in _VALID_PERIODS:
        raise ValueError(f"new_period must be one of {_VALID_PERIODS}, got {new_period!r}")
    if new_period == 3 and not ride_id:
        raise ValueError("ride_id is required when new_period == 3 (passenger aboard)")

    try:
        rpc_params = {
            "p_driver_id": driver_id,
            "p_new_period": new_period,
            "p_ride_id": ride_id,
        }

        def _rpc_call():
            sb = db_supabase.supabase
            if sb is None:
                return None
            res = sb.rpc("record_insurance_period_transition", rpc_params).execute()
            return getattr(res, "data", None) or {}

        result = await db_supabase.run_sync(_rpc_call, retry_policy="write")

        if result is None:
            logger.error(
                "insurance_periods: supabase client unavailable, dropping transition "
                "driver_id=%s new_period=%s ride_id=%s",
                driver_id,
                new_period,
                ride_id,
            )
            _metric_inc(
                "spinr_insurance_period_write_failed_total",
                {"reason": "no_client"},
            )
            return

        status = result.get("status") if isinstance(result, dict) else "ok"

        if status == "noop":
            logger.info(
                "insurance_periods: no-op transition (already open) driver_id=%s new_period=%s ride_id=%s",
                driver_id,
                new_period,
                ride_id,
            )
            _metric_inc(
                "spinr_insurance_period_noop_total",
                {"period": str(new_period)},
            )
        elif status == "race":
            logger.warning(
                "insurance_periods: concurrent transition (race) driver_id=%s new_period=%s ride_id=%s",
                driver_id,
                new_period,
                ride_id,
            )
            _metric_inc(
                "spinr_insurance_period_race_total",
                {"period": str(new_period)},
            )
        else:
            _metric_inc(
                "spinr_insurance_period_recorded_total",
                {"period": str(new_period)},
            )
    except Exception:
        logger.error(
            "insurance_periods: transition write FAILED (swallowed) driver_id=%s new_period=%s ride_id=%s",
            driver_id,
            new_period,
            ride_id,
            exc_info=True,
        )
        _metric_inc(
            "spinr_insurance_period_write_failed_total",
            {"reason": "exception", "period": str(new_period)},
        )


# Reasons a driver gets released from an offer/assignment. Label only — see
# release_driver_and_close_period's contract.
RELEASE_REASONS = frozenset(
    {
        "lost_race",  # another driver accepted first (batch-offer loser)
        "offer_declined",  # the driver tapped Decline
        "offer_timeout",  # the offer expired unanswered
        "rider_cancelled",  # the rider cancelled after assignment
    }
)


async def release_driver_and_close_period(
    driver_id: str,
    *,
    reason: str,
    ride_id: Optional[str] = None,
) -> Optional[int]:
    """Release a driver from an offer/assignment and close their open Period 2
    with the period they ACTUALLY are now.

    This is the single definition of a pattern that was hand-mirrored at five
    call sites — ``_release_loser`` and ``decline_ride`` in
    ``routes/drivers/ride_flow.py``, ``routes/rides/cancellation.py``, and both
    ``_offer_timeout_handler`` and ``process_expired_offer`` in
    ``routes/rides/matching.py`` — where it had already drifted into two
    different behaviours. CLAUDE.md's 2026-09-12 audit finding is exactly this
    shape: a fix proven in one copy that never reached its sibling.

    Why it cannot be "just record Period 1"
    ---------------------------------------
    Every one of these drivers holds an **open Period 2**, opened at claim/offer
    time (``routes/rides/matching.py``, one per claimed driver — the batch model
    has no separate ``driver_assigned`` write). Losing/declining/timing out ends
    that obligation, so the row has to be closed with what the driver actually
    is now:

    * still online  → Period 1 (available, no ride — TNC contingent liability)
    * gone offline  → Period 0 (personal auto only)

    ``set_driver_available(driver_id, True)`` clamps ``is_available`` to False
    for a driver who went offline mid-offer (the ``is_available ⇒ is_online``
    invariant), so its returned row is the authoritative read of which case
    applies. Recording Period 1 unconditionally — the original behaviour at
    three of the five sites — asserted commercial cover over a driver on
    personal auto, in an append-only, regulator-facing table, and nothing would
    ever close that row. Recording *nothing* is not the fix either: it leaves
    the Period 2 open, which claims **primary** commercial cover — worse than
    the mislabel it replaces.

    The 0-vs-1 choice is delegated to :func:`derive_insurance_period` rather
    than re-derived inline, so the Period 0-3 table has one implementation.

    Args:
        driver_id: The ``drivers.id`` PK (NOT ``users.id``).
        reason: One of :data:`RELEASE_REASONS`. **A label, never a behaviour
            switch** — it reaches the log line and the metric so the five sites
            stay distinguishable in Grafana, and nothing else. The moment it
            changes what gets written, the five copies have been rebuilt inside
            one function.
        ride_id: The ride being released from, for the log line only. It is
            deliberately **not** written to the period row: a Period 0/1 row
            must not name a ride (only Period 2/3 may), so passing it through
            would produce a malformed audit record.

    Returns:
        The period recorded (0 or 1), or ``None`` when the driver row could not
        be read — in which case nothing is written, because a guessed row in an
        append-only regulatory log is worse than a missing one.

    Keyword-only on purpose, matching :func:`derive_insurance_period`: these are
    regulatory classifications and a transposed positional argument would
    silently misstate SGI commercial coverage.
    """
    if reason not in RELEASE_REASONS:
        # Programmer error, same class as derive_insurance_period's period
        # validation: surface it rather than emitting an unlabelled metric.
        raise ValueError(f"reason must be one of {sorted(RELEASE_REASONS)}, got {reason!r}")

    released = await db_supabase.set_driver_available(driver_id, True)
    if not isinstance(released, dict):
        # No row came back: either no Supabase client (_write_skipped) or the
        # update matched nothing (stale/deleted driver). "Offline" and "unknown"
        # are not the same thing, so write neither.
        logger.error(
            "insurance_periods: release returned no row, skipping period close driver_id=%s reason=%s ride_id=%s",
            driver_id,
            reason,
            ride_id,
        )
        _metric_inc(
            "spinr_insurance_period_release_skipped_total",
            {"reason": reason},
        )
        return None

    # `is_online` is the driver's own toggle and is what the Period table keys
    # on. Fall back to the clamped `is_available`, which can only be truthy if
    # `is_online` was true, for the case where the row comes back without it.
    _online = released.get("is_online")
    if _online is None:
        _online = released.get("is_available")

    period = derive_insurance_period(ride_status=None, is_online=bool(_online), has_live_offer=False)
    await record_period_transition(driver_id, period)

    _metric_inc(
        "spinr_insurance_period_release_total",
        {"reason": reason, "period": str(period)},
    )
    return period
