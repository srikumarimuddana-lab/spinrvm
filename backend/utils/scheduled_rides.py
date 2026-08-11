"""Scheduled ride dispatcher — background task that dispatches scheduled rides
at the appropriate time and sends reminder notifications.

Flow:
1. Every 60 seconds, check for scheduled rides due in the next 10 minutes
2. Send a reminder notification to the rider 10 minutes before
3. When the scheduled time arrives, dispatch the ride (set status to 'searching')
4. Match a driver using the existing match_driver_to_ride() logic
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from ..db import db
    from ..features import send_push_notification
    from ..socket_manager import manager
    from .datetime_utils import parse_iso_utc
    from .redis_client import redis_delete, redis_expire, redis_incr, redis_set_nx
except ImportError:
    from db import db
    from features import send_push_notification
    from socket_manager import manager  # type: ignore[no-redef]
    from utils.datetime_utils import parse_iso_utc
    from utils.redis_client import redis_delete, redis_expire, redis_incr, redis_set_nx  # type: ignore[no-redef]

try:
    from .metrics import inc as _metric_inc
except ImportError:
    from utils.metrics import inc as _metric_inc  # type: ignore[no-redef]

try:
    from ..settings_loader import get_app_settings
except ImportError:
    from settings_loader import get_app_settings  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Candidates-per-tick cap in check_scheduled_rides(). Ordered ascending by
# scheduled_time, so hitting this cap defers the *latest*-due rows to the next
# tick rather than causing incorrect dispatch order — but it's still a signal
# worth watching as scheduled-ride volume grows.
_SCHEDULED_RIDES_TICK_LIMIT = 100

# Escalation threshold for a scheduled ride stuck deferring on the
# rides_one_active_per_rider conflict (see _dispatch_scheduled_ride). This is
# a tick count, not a wall-clock guarantee — with multiple replicas each
# running their own ~60s-jittered loop, the same ride can be deferred (and
# counted) more than once per real minute, so this fires at or before
# _SCHEDULE_DEFER_ESCALATE_AFTER minutes of real elapsed time, not exactly at it.
# Deliberately does NOT cancel the ride — the conflict may still resolve on
# its own once the rider's other trip ends; this only makes a stuck ride
# visible instead of retrying forever in silence.
_SCHEDULE_DEFER_ESCALATE_AFTER = 20
_SCHEDULE_DEFER_COUNT_TTL = 3600  # well past the escalation threshold; a
# ride that eventually dispatches or gets cancelled just lets this expire.


async def _notify_schedule_delayed(ride_id: str, rider_id, ride: dict, *, escalated: bool = False) -> None:
    """Tell the rider their scheduled ride is waiting on their current trip.

    De-duped via a Redis NX key (1h TTL) so a rider on a long trip isn't pinged
    every 60-second dispatcher tick. Best-effort — a notification failure must
    never break the retry loop. ``escalated=True`` sends a distinct,
    more actionable message once the defer count crosses
    _SCHEDULE_DEFER_ESCALATE_AFTER, using its own dedupe key so it can fire
    even after the routine "waiting" notice already has.
    """
    if not rider_id:
        return
    dedupe_key = f"spinr:sched_delay_notified:{ride_id}" + (":escalated" if escalated else "")
    try:
        # redis_set_nx returns True only for the first caller within the TTL.
        if not await redis_set_nx(dedupe_key, "1", ttl=3600):
            return
    except Exception as dedup_err:
        # Redis unavailable — fall through and notify (worst case: a duplicate
        # push), rather than swallow the alert entirely.
        logger.debug(f"scheduled dispatch: delay-notice dedup check failed for {ride_id}: {dedup_err}")
    try:
        if escalated:
            await send_push_notification(
                rider_id,
                "Still working on your scheduled ride",
                "Your ride is taking longer than expected to start because your other trip "
                "hasn't finished yet. We'll keep trying — contact support if you'd like to cancel or rebook.",
                data={"type": "scheduled_ride_delayed_escalated", "ride_id": ride_id},
            )
        else:
            await send_push_notification(
                rider_id,
                "Your scheduled ride is waiting",
                "We'll start finding a driver as soon as your current trip ends.",
                data={"type": "scheduled_ride_delayed", "ride_id": ride_id},
            )
    except Exception as e:
        logger.warning(f"scheduled dispatch: delayed-notice push failed for {ride_id}: {e}")


async def _track_defer_and_maybe_escalate(ride_id: str, rider_id, ride: dict) -> None:
    """Count consecutive active-ride-conflict deferrals for this ride and,
    past the threshold, escalate: an error-level log, a metric, an
    admin-visible broadcast, and a distinct rider notification — instead of
    retrying forever with only a per-tick warning log as the only trace.
    """
    defer_count = 0
    try:
        counter_key = f"spinr:sched_defer_count:{ride_id}"
        defer_count = await redis_incr(counter_key)
        await redis_expire(counter_key, _SCHEDULE_DEFER_COUNT_TTL)
    except Exception as counter_err:
        logger.debug(f"scheduled dispatch: defer-count tracking failed for {ride_id}: {counter_err}")

    if defer_count < _SCHEDULE_DEFER_ESCALATE_AFTER:
        await _notify_schedule_delayed(ride_id, rider_id, ride)
        return

    # redis_incr is atomic, so exactly one caller observes the threshold
    # value first even with multiple replicas polling concurrently — this
    # branch runs once per escalation, not once per tick thereafter.
    if defer_count == _SCHEDULE_DEFER_ESCALATE_AFTER:
        logger.error(
            f"scheduled dispatch: ride {ride_id} has been deferred {defer_count} times on an "
            "active-ride conflict with no resolution — escalating"
        )
        _metric_inc("spinr_dispatch_scheduled_defer_exhausted_total")
        try:
            await manager.broadcast_to_admins(
                {
                    "type": "scheduled_ride_stuck",
                    "ride_id": ride_id,
                    "rider_id": rider_id,
                    "defer_count": defer_count,
                }
            )
        except Exception as admin_err:
            logger.warning(f"scheduled dispatch: stuck-ride admin broadcast failed for {ride_id}: {admin_err}")
    await _notify_schedule_delayed(ride_id, rider_id, ride, escalated=True)


# Driver heads-up nudge (Finding #06, scheduled-rides gap review). Spinr
# already knows about this demand ahead of time — an on-demand-only
# competitor structurally can't do this. Deliberately conservative: a single
# best-effort push to already-online drivers nearby, not a driver-facing
# schedule/reservation feature (that's a bigger, separate design — see
# Finding #04 in the gap review).
_DRIVER_NUDGE_LEAD_MINUTES = 60
_DRIVER_NUDGE_RADIUS_KM = 10
_DRIVER_NUDGE_MAX_RECIPIENTS = 20


async def _maybe_nudge_nearby_drivers(ride: dict) -> None:
    """Best-effort heads-up push to already-online, already-available drivers
    near an upcoming scheduled pickup, roughly an hour out. Never blocks
    dispatch or the reminder flow — every failure here is logged and
    swallowed, and a missing pickup location is a silent no-op rather than
    an error (older/partial rows may lack it).
    """
    ride_id = ride["id"]
    try:
        settings = await get_app_settings()
        if not settings.get("scheduled_ride_driver_nudge_enabled", False):
            return
    except Exception as settings_err:
        # Unlike the dispatcher kill switch, this is a new, non-critical
        # notification feature — fail CLOSED (skip) on a settings-lookup
        # hiccup rather than risk an unreviewed feature going live because
        # the flag couldn't be read.
        logger.debug(f"scheduled dispatch: driver-nudge settings lookup failed for {ride_id}: {settings_err}")
        return

    pickup_lat = ride.get("pickup_lat")
    pickup_lng = ride.get("pickup_lng")
    if pickup_lat is None or pickup_lng is None:
        return

    dedupe_key = f"spinr:sched_nudge_sent:{ride_id}"
    try:
        # 6h TTL: comfortably past the ~1h nudge window, so a claim can't
        # accidentally persist past this ride's relevance without also
        # meaning "already nudged for this ride, don't do it twice."
        if not await redis_set_nx(dedupe_key, "1", ttl=21600):
            return
    except Exception as dedup_err:
        logger.debug(f"scheduled dispatch: driver-nudge dedup check failed for {ride_id}: {dedup_err}")

    try:
        from services.dispatch_service import dispatch_geo_bounds
    except ImportError:
        from ..services.dispatch_service import dispatch_geo_bounds

    try:
        candidates = await db.get_rows(
            "drivers",
            {
                "is_online": True,
                "is_available": True,
                "$and": dispatch_geo_bounds(float(pickup_lat), float(pickup_lng), _DRIVER_NUDGE_RADIUS_KM),
            },
            columns="user_id",
            limit=_DRIVER_NUDGE_MAX_RECIPIENTS,
        )
    except Exception as query_err:
        logger.warning(f"scheduled dispatch: driver-nudge candidate query failed for {ride_id}: {query_err}")
        return

    for row in candidates:
        driver_user_id = row.get("user_id")
        if not driver_user_id:
            continue
        try:
            await send_push_notification(
                driver_user_id,
                "Scheduled ride coming up nearby",
                "A rider has a pickup scheduled in your area within the hour. Stay online for first chance at it.",
                data={"type": "scheduled_ride_nudge", "ride_id": ride_id},
            )
        except Exception as push_err:
            logger.debug(f"scheduled dispatch: driver-nudge push failed for {ride_id} -> {driver_user_id}: {push_err}")


async def _corporate_policy_still_allows_dispatch(ride: dict) -> bool:
    """Re-check corporate company-active/membership-active/policy state for a
    corporate-paid scheduled ride right before dispatch (Finding #17,
    scheduled-rides gap review; company-active + membership-active gates
    added by the 2026-08-11 corporate scheduled-ride audit — see
    docs/change-log/2026-08-11-corporate-scheduled-dispatch-recheck.md).

    This snapshot is otherwise only ever checked once, at booking time,
    against state that can be days stale by dispatch time (a suspended
    company or removed member the suspension/offboarding sweep didn't catch
    in time, tightened policy, exhausted allowance). Mirrors why card
    pre-auth was deliberately moved to dispatch time for the same "things
    change over days" reason, and mirrors the exact fail-closed checks
    routes/rides/booking.py runs at booking time (require_company_bookable,
    then policy rules, then active-membership) — this function previously
    only ran the middle one, so a company suspended or a member removed
    between booking and dispatch sailed through dispatch and only failed at
    settlement, stuck at payment_status='pending' with no valid payer.

    Non-corporate rides are unaffected (returns True immediately). Every
    gate fails OPEN only on a lookup/evaluation error (DB hiccup, settings
    fetch failure) — never on a confirmed inactive company, failed policy
    rule, or inactive membership — so a transient outage can't strand every
    corporate scheduled ride in the fleet, but a real "this shouldn't
    dispatch" verdict is never silently ignored.
    """
    if (ride.get("payment_method") or "").lower() != "company_allowance":
        return True
    corporate_account_id = ride.get("corporate_account_id")
    if not corporate_account_id:
        return True

    ride_id = ride["id"]
    try:
        from ..services.corporate_policy_service import (
            evaluate_policy_for_ride,
            require_company_bookable,
        )
    except ImportError:
        from services.corporate_policy_service import (  # type: ignore
            evaluate_policy_for_ride,
            require_company_bookable,
        )

    try:
        settings = await get_app_settings() or {}
    except Exception as settings_err:
        logger.error(
            f"scheduled dispatch: could not load app_settings for corporate re-check on {ride_id}, "
            f"skipping company/membership gates this tick (fail-open on lookup error): {settings_err}",
            exc_info=True,
        )
        settings = None

    # ── Gate 1: company still bookable. This gate did not exist before the
    # 2026-08-11 audit, despite this function's docstring having claimed all
    # along to cover "a suspended company the suspension sweep didn't catch
    # in time" — it never actually called require_company_bookable. Mirrors
    # routes/rides/booking.py's require_company_bookable call exactly.
    if settings is not None:
        try:
            from fastapi import HTTPException

            await require_company_bookable(corporate_account_id, settings=settings)
        except HTTPException:
            logger.warning(f"scheduled dispatch: company no longer bookable, blocking dispatch for ride {ride_id}")
            _metric_inc("spinr_dispatch_scheduled_corporate_policy_blocked_total")
            await _notify_corporate_dispatch_blocked(ride, corporate_account_id, ["company_inactive"])
            return False
        except Exception as company_err:
            logger.error(
                f"scheduled dispatch: company-active re-check failed for {ride_id}, dispatching anyway "
                f"(fail-open on lookup error, not on a confirmed-inactive company): {company_err}",
                exc_info=True,
            )

    # ── Gate 2: fare/time-window/allowance policy rules (pre-existing).
    try:
        from decimal import Decimal

        grand_total = ride.get("grand_total")
        if grand_total is None:
            grand_total = ride.get("total_fare") or 0
        result = await evaluate_policy_for_ride(
            corporate_account_id=corporate_account_id,
            rider_id=ride.get("rider_id"),
            estimated_fare=Decimal(str(grand_total)),
            ride_type="standard",
            pickup_time=datetime.now(timezone.utc),
        )
    except Exception as policy_err:
        logger.error(
            f"scheduled dispatch: corporate policy re-check failed for {ride_id}, dispatching anyway "
            f"(fail-open, matching evaluate_policy_for_ride's own contract): {policy_err}",
            exc_info=True,
        )
        return True

    if not result.passed:
        logger.warning(f"scheduled dispatch: corporate policy re-check blocked ride {ride_id}: {result.failed_rules}")
        _metric_inc("spinr_dispatch_scheduled_corporate_policy_blocked_total")
        await _notify_corporate_dispatch_blocked(ride, corporate_account_id, result.failed_rules)
        return False

    # ── Gate 3: rider still an active company member. evaluate_policy_for_ride
    # (gate 2) only degrades `allowance` to {} when the membership lookup
    # finds nothing — it never fails the ride unless the company also has an
    # allowed_payment_source == "allowance_only" policy, so a removed member
    # at a company without that policy sailed through both gates above.
    # Mirrors booking.py's fail-closed membership check (corporate module
    # review gap #3) at dispatch time too.
    if settings is not None and settings.get("corporate_member_removal_blocks_booking", True):
        try:
            from ..db_supabase import list_active_memberships_for_user
        except ImportError:
            from db_supabase import list_active_memberships_for_user  # type: ignore

        rider_id = ride.get("rider_id")
        corporate_member_id = ride.get("corporate_member_id")
        member_still_active = True
        try:
            memberships = await list_active_memberships_for_user(rider_id)
            # A company_allowance-booked ride stamps the specific membership
            # it was booked under (routes/rides/booking.py); a work_profile
            # ride does not, so fall back to "any active membership at this
            # company" — matching that path's own booking-time check.
            if corporate_member_id:
                member_still_active = any(m.get("id") == corporate_member_id for m in memberships)
            else:
                member_still_active = any(m.get("company_id") == corporate_account_id for m in memberships)
        except Exception as membership_err:
            logger.error(
                f"scheduled dispatch: membership re-check failed for {ride_id}, dispatching anyway "
                f"(fail-open on lookup error): {membership_err}",
                exc_info=True,
            )

        if not member_still_active:
            logger.warning(
                f"scheduled dispatch: rider membership no longer active, blocking dispatch for ride {ride_id}"
            )
            _metric_inc("spinr_dispatch_scheduled_corporate_policy_blocked_total")
            await _notify_corporate_dispatch_blocked(ride, corporate_account_id, ["membership_inactive"])
            return False

    return True


async def _notify_corporate_dispatch_blocked(ride: dict, corporate_account_id: str, failed_rules: list) -> None:
    """Shared notify/escalate for a scheduled corporate ride blocked from
    dispatch by _corporate_policy_still_allows_dispatch, regardless of which
    gate (company-active, fare/policy rules, or membership-active) blocked
    it. Notifies/escalates once per ride (Redis NX, 24h TTL) — the blocked
    ride stays in 'scheduled' and the caller re-checks every tick, so
    without this dedupe guard it would re-notify every ~60s for as long as
    the ride stays blocked.
    """
    ride_id = ride["id"]
    try:
        if not await redis_set_nx(f"spinr:sched_corp_policy_blocked:{ride_id}", "1", ttl=86400):
            return
    except Exception as dedup_err:
        logger.debug(f"scheduled dispatch: corp-policy-block dedup check failed for {ride_id}: {dedup_err}")

    rider_id = ride.get("rider_id")
    if rider_id:
        try:
            await send_push_notification(
                rider_id,
                "Your scheduled ride is on hold",
                "Your company's booking policy no longer allows this ride. Contact your company admin for help.",
                data={"type": "scheduled_ride_policy_blocked", "ride_id": ride_id},
            )
        except Exception as push_err:
            logger.warning(f"scheduled dispatch: policy-blocked push failed for {ride_id}: {push_err}")
    try:
        await manager.broadcast_to_admins(
            {
                "type": "scheduled_ride_policy_blocked",
                "ride_id": ride_id,
                "corporate_account_id": corporate_account_id,
                "failed_rules": failed_rules,
            }
        )
    except Exception as admin_err:
        logger.warning(f"scheduled dispatch: policy-blocked admin broadcast failed for {ride_id}: {admin_err}")


async def _dispatch_scheduled_ride(ride: dict):
    """Transition a scheduled ride from 'scheduled' to 'searching' and start driver matching.

    The scheduled→searching transition is an atomic DB claim: the update filters
    on ``status='scheduled'`` so exactly one caller (across replicas and loop
    ticks) wins. A claim returning no row means the ride was already dispatched,
    cancelled, or otherwise moved on — we return without acting. This satisfies
    the Background Loop Recipe replay-safety contract (atomic DB claim) without
    relying on the Redis leader lock, which may be unavailable in dev fallback.
    """
    ride_id = ride["id"]
    rider_id = ride.get("rider_id")
    try:
        # Finding #17: gate BEFORE the atomic claim, not after — a corporate
        # ride that fails re-check must stay in 'scheduled' for a later
        # retry, not get claimed into 'searching' and then have to be
        # unwound. A soft skip here, not an exception: the ride is left
        # exactly as check_scheduled_rides() found it.
        if not await _corporate_policy_still_allows_dispatch(ride):
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        # Atomic claim: only the caller that flips scheduled→searching proceeds.
        try:
            claimed = await db.update_one(
                "rides",
                {"id": ride_id, "status": "scheduled"},
                {
                    "$set": {
                        "status": "searching",
                        "scheduled_dispatched": True,
                        # Mark the moment dispatch actually began so downstream
                        # latency metrics measure from real request time, not booking.
                        "ride_requested_at": now_iso,
                        "updated_at": now_iso,
                    }
                },
            )
        except Exception as claim_exc:
            # rides_one_active_per_rider (migration 53) is a partial unique
            # index over the active statuses. 'scheduled' sits outside that set,
            # so if the rider already has a live trip when their scheduled
            # pickup time arrives, flipping to 'searching' collides with it and
            # the UPDATE raises. That is an expected, recoverable conflict — not
            # a dispatch failure: leave the ride in 'scheduled' so a later tick
            # retries once the rider is free, and tell the rider once (Redis-
            # deduped) so the delay isn't silent. Any other error bubbles up.
            msg = str(claim_exc).lower()
            if "rides_one_active_per_rider" in msg or "23505" in msg or "duplicate" in msg or "unique" in msg:
                logger.warning(
                    f"scheduled dispatch deferred: rider has an active ride; ride {ride_id} stays 'scheduled' for retry"
                )
                await _track_defer_and_maybe_escalate(ride_id, rider_id, ride)
                return
            raise
        if not claimed:
            # Another replica/tick won the claim, or the ride was cancelled or
            # already dispatched. Nothing to do.
            return

        logger.info(f"Dispatched scheduled ride {ride_id}: scheduled → searching")

        # Pre-authorize the card hold NOW (at dispatch), not at booking: a
        # scheduled ride may be booked days out, beyond Stripe's ~7-day auth
        # lifetime, so the hold is placed when the ride actually goes live.
        # Card source only, and only if no hold exists yet. The rider isn't
        # present, so a decline must NOT strand the ride (block_on_decline=False)
        # — it degrades to post-trip settlement like any un-held ride.
        try:
            if claimed.get("payment_method") == "card" and not claimed.get("auth_status"):
                from decimal import Decimal as _Decimal

                try:
                    from ..routes.rides import booking as _rides_booking
                except ImportError:
                    from routes.rides import booking as _rides_booking  # type: ignore

                _rider_row = await db.get_user_by_id(rider_id) if rider_id else None
                _grand = claimed.get("grand_total")
                if _grand is None:
                    _grand = claimed.get("total_fare") or 0
                _preauth = await _rides_booking._preauthorize_ride_card(
                    ride_id=ride_id,
                    rider_id=rider_id,
                    grand_total=_Decimal(str(_grand)),
                    stripe_customer_id=(_rider_row or {}).get("stripe_customer_id"),
                    payment_method_id=claimed.get("payment_method_id"),
                    block_on_decline=False,
                )
                if _preauth.fields:
                    await db.update_one("rides", {"id": ride_id}, {"$set": _preauth.fields})
        except Exception as _auth_err:
            # Never let a pre-auth hiccup block dispatch; post-trip settlement
            # remains the safety net. Surface loudly — it's a payment path.
            logger.error(
                "scheduled dispatch: pre-auth at dispatch failed for %s: %s",
                ride_id,
                _auth_err,
                exc_info=True,
            )

        # Mandatory state-change WS event (rider + admins). Drives the rider
        # app's status update and patches any admin dashboard that already has
        # this ride in its map.
        try:
            await manager.broadcast_ride_status(
                ride_id,
                "searching",
                rider_id=rider_id,
                is_scheduled=True,
            )
        except Exception as ws_err:
            logger.warning(f"scheduled dispatch: WS broadcast failed for {ride_id}: {ws_err}")

        # Surface the now-live ride to admin monitoring as a NEW row. The
        # dashboard's ride_status_changed handler only patches an existing
        # entry; ride_requested is the path that calls applyRide() to add a
        # row. The create_ride path skips ride_requested for deferred rides,
        # so without this a scheduled ride going live is invisible to an
        # already-open dashboard until the next snapshot refresh. Payload must
        # match the MonitoringRide contract (nested ``ride`` object).
        try:
            try:
                from ..routes.admin.monitoring import build_monitoring_ride
            except ImportError:
                from routes.admin.monitoring import build_monitoring_ride
            rider = None
            if rider_id:
                rider = await db.get_user_by_id(rider_id)
            await manager.broadcast_to_admins(
                {
                    "type": "ride_requested",
                    "ride": build_monitoring_ride(claimed, rider=rider),
                }
            )
        except Exception as admin_err:
            logger.warning(f"scheduled dispatch: admin ride_requested broadcast failed for {ride_id}: {admin_err}")

        # Import and run driver matching. We do NOT pass ride= so the dispatch
        # path re-fetches the freshly-claimed 'searching' row.
        try:
            from routes.rides import matching as _rides_matching
        except ImportError:
            from ..routes.rides import matching as _rides_matching

        # match_driver_to_ride documents itself as "never raises" — recovery
        # (retry ladder, then the stuck-ride sweeper) is owned internally.
        # The ride is genuinely in 'searching' status the moment the claim
        # above succeeded, though, so the timeout safety net below must arm
        # regardless of whether that no-raise contract holds — a violation
        # here must not silently fall back to only the 5-minute sweeper.
        try:
            await _rides_matching.match_driver_to_ride(ride_id)
        except Exception as match_err:
            logger.error(
                "scheduled dispatch: match_driver_to_ride raised for %s despite its no-raise contract: %s",
                ride_id,
                match_err,
                exc_info=True,
            )

        # Arm the no-drivers-found timeout exactly as the live booking path does,
        # so a scheduled ride that finds no driver auto-cancels instead of
        # hanging in 'searching' indefinitely. Armed unconditionally (see above).
        asyncio.create_task(_rides_matching.ride_search_timeout(ride_id))

        # Notify rider
        if rider_id:
            await send_push_notification(
                rider_id,
                "Your scheduled ride is starting!",
                f"We're finding a driver for your ride to {ride.get('dropoff_address', 'your destination')}.",
                data={"type": "scheduled_ride_dispatched", "ride_id": ride_id},
            )

    except Exception as e:
        logger.error(f"Failed to dispatch scheduled ride {ride_id}: {e}", exc_info=True)


async def _send_reminder(ride: dict):
    """Send a 10-minute reminder notification for an upcoming scheduled ride.

    The push send and the reminder_sent DB flag write are two separate
    operations that can fail independently. A Redis NX claim (1h TTL) guards
    the push itself, decoupled from the flag:
      - push fails: the claim is released so the next tick retries the send
        (unchanged from before this fix).
      - push succeeds but the flag write fails: the claim stays in place, so
        the next tick skips re-sending the push but still retries the flag
        write — previously this case re-sent a duplicate push every tick
        until the write finally succeeded.
    """
    ride_id = ride["id"]
    try:
        # Check if reminder already sent
        if ride.get("reminder_sent"):
            return

        rider_id = ride.get("rider_id")
        dedupe_key = f"spinr:sched_reminder_pushed:{ride_id}"

        should_push = True
        try:
            # redis_set_nx returns True only for the first caller within the TTL.
            should_push = await redis_set_nx(dedupe_key, "1", ttl=3600)
        except Exception as dedup_err:
            # Redis unavailable — fall through and send (worst case: a
            # duplicate push), rather than silently skip the reminder.
            logger.debug(f"scheduled dispatch: reminder dedup check failed for {ride_id}: {dedup_err}")

        if rider_id and should_push:
            try:
                await send_push_notification(
                    rider_id,
                    "Ride reminder - 10 minutes",
                    f"Your ride to {ride.get('dropoff_address', 'your destination')} is scheduled soon. A driver will be assigned shortly.",
                    data={"type": "scheduled_ride_reminder", "ride_id": ride_id},
                )
            except Exception:
                try:
                    await redis_delete(dedupe_key)
                except Exception as release_err:
                    logger.debug(f"scheduled dispatch: reminder dedup release failed for {ride_id}: {release_err}")
                raise

        await db.update_one(
            "rides",
            {"id": ride_id},
            {"$set": {"reminder_sent": True}},
        )
        logger.info(f"Sent reminder for scheduled ride {ride_id}")

    except Exception as e:
        logger.error(f"Failed to send reminder for ride {ride_id}: {e}", exc_info=True)


async def check_scheduled_rides() -> Optional[bool]:
    """Check for scheduled rides that need dispatching or reminders.

    Returns ``True`` if the candidate fetch succeeded (rides may or may not
    have needed action), ``False`` if the fetch itself failed, or ``None`` if
    this tick was skipped because another replica holds the leader lock. The
    caller (scheduled_ride_dispatcher_loop) uses this to track consecutive
    fetch failures across ticks — a skip is neither a success nor a failure,
    so it must not reset or advance that counter. A disabled kill switch
    (ACTION_ITEMS.md E5) also returns None for the same reason: an admin
    pause is not a failure, and must not count toward the sustained-failure
    alert in Finding #13.
    """
    try:
        settings = await get_app_settings()
        if not settings.get("scheduled_dispatch_enabled", True):
            return None
    except Exception as settings_err:
        # Never let a settings-lookup hiccup silently disable dispatch —
        # fail open (proceed as if enabled) and log loudly, mirroring the
        # rest of this file's "surface loudly, don't let it block dispatch"
        # convention for non-dispatch-critical failures.
        logger.warning(f"scheduled_rides: app_settings lookup failed ({settings_err}), proceeding as enabled")

    # Lock TTL (45s) is deliberately BELOW the loop's ~54-66s jittered
    # interval (60±6s) and is also released explicitly in the `finally`
    # below on every exit path. Previously the TTL was 90s with no explicit
    # release: since 90s > the loop interval, the replica that won the lock
    # would fail to re-acquire its OWN still-live lock on the very next
    # tick and skip it, halving the real dispatch cadence to ~120s on every
    # replica, always — not just under contention. The short TTL is a
    # crash-safety net (self-heals within well under one cycle if a replica
    # dies mid-tick before reaching the release); the explicit release is
    # what makes the common case (tick completes normally) free the lock
    # immediately instead of waiting out the TTL.
    _holds_lock = False
    try:
        if not await redis_set_nx("spinr:scheduled_rides:lock", "1", ttl=45):
            return None
        _holds_lock = True
    except Exception as _lock_err:
        logger.warning(f"scheduled_rides: Redis leader lock unavailable ({_lock_err}), proceeding without lock")

    try:
        now = datetime.now(timezone.utc)
        ten_min_from_now = now + timedelta(minutes=10)
        nudge_window_end = now + timedelta(minutes=_DRIVER_NUDGE_LEAD_MINUTES)

        try:
            # Get all pending scheduled rides. These sit in status 'scheduled'
            # (set by create_ride for deferred bookings) until their scheduled_time
            # arrives — querying 'searching' here meant the loop never saw a
            # correctly-stored scheduled ride (CR-2).
            scheduled = await db.get_rows(
                "rides",
                {
                    "is_scheduled": True,
                    "status": "scheduled",
                },
                limit=_SCHEDULED_RIDES_TICK_LIMIT,
                order="scheduled_time",
                columns=(
                    "id,rider_id,scheduled_time,scheduled_dispatched,reminder_sent,dropoff_address,"
                    "pickup_lat,pickup_lng,payment_method,corporate_account_id,grand_total,total_fare"
                ),
            )
        except Exception as e:
            original = getattr(e, "details", {}).get("original") if hasattr(e, "details") else None
            logger.error(f"Failed to fetch scheduled rides: {e} | original={original}", exc_info=True)
            return False

        if len(scheduled) >= _SCHEDULED_RIDES_TICK_LIMIT:
            # Every row this tick will still dispatch in scheduled_time order, but
            # anything beyond the cap is deferred to the next tick — worth knowing
            # about before it turns into a real dispatch-latency regression.
            logger.warning(
                f"scheduled_rides: tick hit the {_SCHEDULED_RIDES_TICK_LIMIT}-row candidate cap; "
                "some due/near-due scheduled rides are deferred to the next tick"
            )
            _metric_inc("spinr_dispatch_scheduled_candidates_capped_total")

        for ride in scheduled:
            scheduled_time_str = ride.get("scheduled_time")
            if not scheduled_time_str:
                continue

            scheduled_time = parse_iso_utc(scheduled_time_str)
            if scheduled_time is None:
                continue

            already_dispatched = ride.get("scheduled_dispatched", False)
            already_reminded = ride.get("reminder_sent", False)

            # Send reminder 10 minutes before (if not already sent)
            if not already_reminded and now <= scheduled_time and scheduled_time <= ten_min_from_now:
                await _send_reminder(ride)

            # Heads-up nudge to nearby online drivers ~60 minutes before pickup.
            # Best-effort and self-deduped (Redis NX inside the function) — safe
            # to call every tick within the window.
            if now <= scheduled_time and scheduled_time <= nudge_window_end:
                await _maybe_nudge_nearby_drivers(ride)

            # Dispatch when it's time (or past time)
            if not already_dispatched and now >= scheduled_time:
                await _dispatch_scheduled_ride(ride)

        return True
    finally:
        if _holds_lock:
            try:
                await redis_delete("spinr:scheduled_rides:lock")
            except Exception as _release_err:
                # Not fatal — the short TTL above self-heals this within
                # well under one loop cycle either way.
                logger.warning(
                    f"scheduled_rides: failed to release leader lock ({_release_err}), will self-heal via TTL"
                )


# Consecutive check_scheduled_rides() fetch failures (per replica, in-process
# — reset on process restart, which is fine: this tracks a live outage, not a
# durable record) before it's treated as sustained rather than a one-off blip.
_FETCH_FAILURE_ALERT_THRESHOLD = 5


async def scheduled_ride_dispatcher_loop():
    """Background loop that checks scheduled rides every 60 seconds."""
    logger.info("Scheduled ride dispatcher started")
    consecutive_fetch_failures = 0
    while True:
        try:
            result = await check_scheduled_rides()
            if result is False:
                consecutive_fetch_failures += 1
                if consecutive_fetch_failures == _FETCH_FAILURE_ALERT_THRESHOLD:
                    # Fires once per outage, not every tick thereafter — mirrors
                    # the escalate-once pattern in _track_defer_and_maybe_escalate.
                    logger.error(
                        f"scheduled_rides: {consecutive_fetch_failures} consecutive candidate-fetch "
                        "failures — the entire scheduled-ride book may be going dark"
                    )
                    _metric_inc("spinr_dispatch_scheduled_fetch_failures_sustained_total")
            elif result is True:
                consecutive_fetch_failures = 0
            # result is None (another replica holds the leader lock this
            # tick) — leave the counter untouched, it says nothing about DB health.
        except Exception as e:
            logger.error(f"Scheduled ride dispatcher error: {e}", exc_info=True)
        _record_heartbeat("scheduled_dispatcher (60s)")
        # B-P3-2: ±6 s jitter on the 60 s interval so replicas don't
        # contend for the same scheduled-ride row on every minute boundary.
        await asyncio.sleep(60 + random.uniform(-6, 6))
