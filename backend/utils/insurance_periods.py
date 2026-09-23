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
from datetime import datetime, timedelta, timezone
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
        elif status == "stale_ride":
            logger.error(
                "insurance_periods: refused Period 2 for a stale ride driver_id=%s ride_id=%s",
                driver_id,
                ride_id,
            )
            _metric_inc("spinr_insurance_period_write_failed_total", {"reason": "stale_ride"})
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
        "driver_cancelled",  # the driver cancelled after assignment (routes/drivers/ride_cancel.py)
        "rider_noshow",  # the driver marked the rider a no-show (routes/drivers/ride_cancel.py)
        "ride_completed",  # driver ended the trip; closes the open Period 3 (routes/drivers/ride_complete.py)
        "rider_completed",  # rider ended the trip (routes/rides/lifecycle.py)
        "admin_cancelled",  # admin cancel (routes/admin/rides.py)
        "admin_completed",  # admin force-complete (routes/admin/rides.py)
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
    return await close_period_after_release(driver_id, released, reason=reason, ride_id=ride_id)


async def close_period_after_release(
    driver_id: str,
    released: object,
    *,
    reason: str,
    ride_id: Optional[str] = None,
) -> Optional[int]:
    """Close the driver's open Period 2/3 given the row an already-performed
    ``set_driver_available(driver_id, True, ...)`` returned.

    The second half of :func:`release_driver_and_close_period`, split out for
    callers that must issue the release write themselves — ``complete_ride``
    also increments ``total_rides`` in that same write, and ``cancel_ride``
    releases the driver unconditionally but only closes a period when the
    ride was past assignment. Same contract as the parent helper: the
    returned row is the authoritative read of 0-vs-1, and an unreadable row
    writes nothing. Before this existed both of those callers recorded
    Period 1 unconditionally — the exact defect the parent's docstring
    describes (2026-09-22 insurance-period audit, HIGH #2).
    """
    if reason not in RELEASE_REASONS:
        raise ValueError(f"reason must be one of {sorted(RELEASE_REASONS)}, got {reason!r}")

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


# Why a driver was forced offline by the system/an admin. Label only, like
# RELEASE_REASONS — it reaches the log line and the metric, nothing else.
FORCED_OFFLINE_REASONS = frozenset(
    {
        "document_expired",  # utils/document_expiry.py auto-suspension
        "admin_suspend",  # routes/admin/drivers.py admin_driver_action
        "admin_ban",
        "admin_reject",
        "admin_status_override",  # routes/admin/drivers.py admin_override_driver_status
    }
)

_OBLIGATED_RIDE_STATUSES = sorted(s.value for s in (_EN_ROUTE_STATUSES | {RideStatus.IN_PROGRESS}))


async def has_active_ride_obligation(driver_id: str) -> Optional[bool]:
    """True if the driver currently has an obligated ride (assigned/accepted/
    arrived/in_progress) or a pending ``ride_offers`` row.

    For any caller about to force a driver offline (or close their coverage
    period) outside the driver's own voluntary Go Offline path — the same
    question :func:`close_period_for_forced_offline` answers internally, but
    exposed here for callers that must skip the offline flip itself, not just
    the period write, while the obligation holds (2026-09-23 follow-up audit,
    BLOCKER: `routes/drivers/profile.py`'s vehicle/document-edit path and
    `utils/spinr_pass.py`'s quota enforcement forced a driver offline and
    recorded Period 0 mid-trip with no check at all).

    Returns ``None`` if the lookup itself failed — never raises. Callers
    should treat ``None`` the same as ``True`` (defer / do not force
    offline): guessing "safe to disrupt" is worse than deferring one tick.
    """
    try:
        rides = await db_supabase.get_rows(
            "rides",
            {"driver_id": driver_id, "status": {"$in": _OBLIGATED_RIDE_STATUSES}},
            columns="id",
            limit=1,
        )
        if rides:
            return True
        offers = await db_supabase.get_rows(
            "ride_offers",
            {"driver_id": driver_id, "status": "pending"},
            columns="ride_id",
            limit=1,
        )
        return bool(offers)
    except Exception:
        logger.error(
            "insurance_periods: has_active_ride_obligation lookup FAILED driver_id=%s",
            driver_id,
            exc_info=True,
        )
        return None


async def close_period_for_forced_offline(driver_id: str, *, reason: str) -> Optional[int]:
    """Re-classify a driver who was just forced ``is_online=False`` by the
    system or an admin (suspend / ban / reject / document expiry).

    Must be called AFTER the ``drivers`` write that set ``is_online=False``
    succeeded. The driver's own Go Offline is refused while a ride or live
    offer is theirs (``routes/drivers/status.py``); these paths have no such
    refusal, and before this helper they wrote no period row at all — an
    open Period 1 stayed open for an offline driver (claiming TNC contingent
    cover over personal-auto time) and the reconciler could never heal it,
    because its idle-driver scan only looks at ``is_online = True``.

    Why the period is derived from ride state, not blanket Period 0
    ---------------------------------------------------------------
    Suspending a driver changes their account, not the physical situation.
    A passenger already in the car is still in the car: the ride stays
    ``in_progress`` (nothing here cancels it), and CLAUDE.md says to derive
    the period from ride state, not the driver UI. Writing Period 0 there
    would close the open Period 3 and assert personal-auto-only cover while
    a passenger is aboard — the most harmful misclassification possible,
    and a downgrade the reconciler's ``_in_progress_candidates`` scan would
    immediately fight. So:

    * ride ``in_progress``                       → Period 3 (left open, no write)
    * ride assigned/accepted/arrived, or a
      pending ``ride_offers`` row                → Period 2 (left open, no write)
    * none of the above                          → Period 0 (closes a stale 1)

    Every later end of that ride/offer releases through a path that reads the
    now-offline row and closes the 2/3 to Period 0: driver ``complete_ride`` /
    ``cancel_ride`` / ``mark_rider_noshow``, rider-side ``complete_ride``,
    admin cancel / force-complete (all via :func:`close_period_after_release`),
    and rider cancel / offer decline / offer expiry (via
    :func:`release_driver_and_close_period` or the batch-offer release RPC).

    Any pending offer counts as live — the same conservative rule the
    reconciler's ``_pending_offer_candidates`` uses; a stale one is closed by
    the claim reaper, never downgraded here.

    Returns the derived period (0 written; 2/3 left open), or ``None`` when
    the ride/offer lookup failed — nothing is written then, because guessing
    0 could close a real Period 3. Never raises except for a programmer-error
    ``reason``.
    """
    if reason not in FORCED_OFFLINE_REASONS:
        raise ValueError(f"reason must be one of {sorted(FORCED_OFFLINE_REASONS)}, got {reason!r}")

    try:
        rides = await db_supabase.get_rows(
            "rides",
            {"driver_id": driver_id, "status": {"$in": _OBLIGATED_RIDE_STATUSES}},
            columns="id,status",
            limit=1,
        )
        ride = rides[0] if rides else None
        offer = None
        if ride is None:
            offers = await db_supabase.get_rows(
                "ride_offers",
                {"driver_id": driver_id, "status": "pending"},
                columns="ride_id",
                limit=1,
            )
            offer = offers[0] if offers else None
    except Exception:
        logger.error(
            "insurance_periods: forced-offline ride/offer lookup FAILED, period left unchanged driver_id=%s reason=%s",
            driver_id,
            reason,
            exc_info=True,
        )
        _metric_inc("spinr_insurance_period_forced_offline_skipped_total", {"reason": reason})
        return None

    period = derive_insurance_period(
        ride_status=ride.get("status") if ride else None,
        is_online=False,
        has_live_offer=offer is not None,
    )
    if period != 0:
        # The open Period 2/3 row, opened at claim / trip start, is already
        # the right one — so write nothing. Re-asserting it here would only
        # add a stale-read race: if the ride completed between the lookup
        # above and the write, a re-asserted Period 3 would re-open primary
        # cover on a finished trip after complete_ride had closed it.
        logger.warning(
            "insurance_periods: driver forced offline while holding an obligation; "
            "leaving open Period %s for the ride-end path to close driver_id=%s ride_id=%s reason=%s",
            period,
            driver_id,
            (ride or {}).get("id") or (offer or {}).get("ride_id"),
            reason,
        )
        _metric_inc("spinr_insurance_period_forced_offline_total", {"reason": reason, "period": str(period)})
        return period

    await record_period_transition(driver_id, 0)
    _metric_inc("spinr_insurance_period_forced_offline_total", {"reason": reason, "period": "0"})
    return 0


async def release_batch_offer_driver_and_close_period(driver_id: str, *, ride_id: str) -> Optional[int]:
    """Release a batch-offer claim only while this ride still owns the driver.

    The database RPC serializes against claims on the driver row, verifies the
    open Period-2 ride identity and other active obligations, then updates
    availability and closes the period in one transaction. On any RPC error
    or ownership mismatch this fails closed: it never falls back to the
    unscoped ``set_driver_available`` helper.
    """
    try:
        offers = await db_supabase.get_rows(
            "ride_offers",
            {"driver_id": driver_id, "ride_id": ride_id, "status": "cancelled"},
            limit=1,
            columns="claim_id",
        )
    except Exception:
        logger.error(
            "insurance_periods: cancelled-offer identity lookup failed driver_id=%s ride_id=%s; leaving claim unchanged",
            driver_id,
            ride_id,
            exc_info=True,
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": "claim_identity_lookup_error"})
        return None

    offer_claim_id = offers[0].get("claim_id") if offers else None
    rpc_name = (
        "release_batch_offer_driver_and_close_period_v2"
        if offer_claim_id
        else "release_batch_offer_driver_legacy_guard_v2"
    )
    params = {"p_driver_id": driver_id, "p_ride_id": ride_id}

    def _rpc_call():
        sb = db_supabase.supabase
        if sb is None:
            return None
        response = sb.rpc(rpc_name, params).execute()
        data = getattr(response, "data", None)
        return data[0] if isinstance(data, list) and data else data

    try:
        result = await db_supabase.run_sync(_rpc_call, retry_policy="write")
    except Exception:
        logger.error(
            "insurance_periods: batch-offer release RPC %s failed driver_id=%s ride_id=%s; "
            "leaving driver claim unchanged",
            rpc_name,
            driver_id,
            ride_id,
            exc_info=True,
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": "rpc_error"})
        return None

    if not isinstance(result, dict) or result.get("status") != "released":
        reason = result.get("status", "no_result") if isinstance(result, dict) else "no_result"
        logger.warning(
            "insurance_periods: batch-offer release skipped via %s driver_id=%s ride_id=%s reason=%s",
            rpc_name,
            driver_id,
            ride_id,
            reason,
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": reason})
        return None

    if result.get("period_missing"):
        logger.error(
            "insurance_periods: recovered claim with missing open period driver_id=%s ride_id=%s; "
            "opened current Period %s without fabricating historical Period 2",
            driver_id,
            ride_id,
            result.get("period"),
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": "period_missing_recovered"})

    period = result.get("period")
    # The RPC changes the driver row outside the repository write helpers;
    # evict both cached lookup keys so availability reads see the release.
    try:
        try:
            from ..repositories._base import invalidate_driver_cache
        except ImportError:  # pragma: no cover - dual-import mode
            from repositories._base import invalidate_driver_cache  # type: ignore
        await invalidate_driver_cache(driver_id=driver_id, user_id=result.get("user_id"))
    except Exception:
        logger.warning(
            "insurance_periods: driver cache invalidation failed after batch-offer release driver_id=%s",
            driver_id,
            exc_info=True,
        )
    _metric_inc(
        "spinr_insurance_period_release_total",
        {"reason": "rider_cancelled", "period": str(period)},
    )
    return period


_BENIGN_REAP_SKIPS = frozenset(
    {
        "claim_too_recent",
        "offer_active",
        "ride_active",
        "offer_or_ride_active",
        "not_claimed",
        "driver_missing",
        "legacy_claim_changed",
        "claim_identity_changed",
    }
)


async def reap_stale_driver_claim(
    driver_id: str, claim_id: Optional[str] = None, claimed_at: Optional[str] = None
) -> Optional[dict]:
    """Ask Postgres to recover one claim using a locked identity or legacy CAS."""
    rpc_name = "reap_stale_driver_claim_v2" if claim_id else "reap_stale_legacy_driver_claim_v2"
    params = {"p_driver_id": driver_id}
    if claim_id:
        params["p_expected_claim_id"] = claim_id
    else:
        params["p_expected_claimed_at"] = claimed_at
        params["p_stale_before"] = (datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat()

    def _rpc_call():
        sb = db_supabase.supabase
        if sb is None:
            return None
        response = sb.rpc(rpc_name, params).execute()
        data = getattr(response, "data", None)
        return data[0] if isinstance(data, list) and data else data

    try:
        result = await db_supabase.run_sync(_rpc_call, retry_policy="write")
    except Exception:
        logger.error(
            "insurance_periods: stale claim recovery RPC failed driver_id=%s; claim remains unchanged",
            driver_id,
            exc_info=True,
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": "rpc_error"})
        return None

    if not isinstance(result, dict) or result.get("status") != "released":
        status = result.get("status", "no_result") if isinstance(result, dict) else "no_result"
        # Busy drivers and claims that changed under us are normal, not errors
        # (logging them at ERROR paged Sentry every tick per busy driver).
        if status not in _BENIGN_REAP_SKIPS:
            logger.error("insurance_periods: stale claim recovery skipped driver_id=%s reason=%s", driver_id, status)
            _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": status})
        return result if isinstance(result, dict) else None

    if result.get("stale_period_closed"):
        logger.warning(
            "insurance_periods: stale claim recovery closed a Period 2/3 left open by an "
            "interrupted release driver_id=%s",
            driver_id,
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": "stale_period_recovered"})

    if result.get("period_missing"):
        logger.error(
            "insurance_periods: recovered stale claim with missing open period driver_id=%s; "
            "opened current Period %s without fabricating historical Period 2",
            driver_id,
            result.get("period"),
        )
        _metric_inc("spinr_insurance_period_release_skipped_total", {"reason": "period_missing_recovered"})

    try:
        try:
            from ..repositories._base import invalidate_driver_cache
        except ImportError:  # pragma: no cover - dual-import mode
            from repositories._base import invalidate_driver_cache  # type: ignore
        await invalidate_driver_cache(driver_id=driver_id, user_id=result.get("user_id"))
    except Exception:
        logger.warning(
            "insurance_periods: driver cache invalidation failed after stale claim recovery driver_id=%s",
            driver_id,
            exc_info=True,
        )
    _metric_inc("spinr_insurance_period_release_total", {"reason": "orphan_claim", "period": str(result.get("period"))})
    return result
