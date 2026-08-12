"""Driver cancellation, rider no-show, rating the rider.

Split from ``backend/routes/drivers.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    EVENT_END,
    APIRouter,
    Decimal,
    Depends,
    HTTPException,
    Query,
    Request,
    RideRatingRequest,
    RideStateError,
    RideStatus,
    datetime,
    db_supabase,
    get_current_user,
    logger,
    send_live_activity_update,
    spawn,
    timezone,
    uuid,
)

router = APIRouter()

# Case-insensitive substring match against the free-text cancellation_reason
# column. Matches the driver-app preset "Service animal — could not
# accommodate" (driver-app/components/CancelReasonSheet.tsx's
# DRIVER_CANCEL_REASONS) regardless of an appended free-text note. Keep in
# sync with that string.
_SERVICE_ANIMAL_CANCEL_MARKER = "service animal"


@router.post("/rides/{ride_id}/cancel")
async def cancel_ride(
    ride_id: str,
    reason: str = Query(""),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    # Prefer the reason from the JSON body so a free-text note never rides in the
    # URL (proxy/access logs leak query strings). Fall back to legacy ?reason=.
    _body_reason = None
    if request is not None:
        try:
            _b = await request.json()
            if isinstance(_b, dict):
                _body_reason = _b.get("reason")
        except Exception:
            _body_reason = None
    reason = (str(_body_reason).strip() if _body_reason else "") or reason

    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Ownership guard (findings 5/6/7): a driver may only cancel the ride they
    # are assigned to. Without this, any authenticated driver could cancel any
    # ride by id — and worse, the period-1 transition below (line ~132) would
    # be recorded against the *attacker* while the real driver's period-2 row
    # is left open forever, corrupting the SGI insurance audit trail. A ride in
    # `searching` has no assigned driver (driver_id is None), so a driver-side
    # cancel of it is never legitimate either. Mirrors mark_rider_noshow /
    # rate_rider in this same file.
    if ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Not your assigned ride")

    if ride.get("status") in (RideStatus.IN_PROGRESS, RideStatus.COMPLETED):
        raise RideStateError(f"Cannot cancel a ride in state '{ride.get('status')}'")

    now = datetime.now(timezone.utc)
    base_update = {
        "status": RideStatus.CANCELLED,
        "cancelled_at": now,
        "updated_at": now,
    }

    # C2: atomic, status-guarded cancel. The in-memory check above can race —
    # between get_ride and the write the rider/driver can call verify-otp/start
    # and flip the ride to in_progress. An unguarded update_ride (filters on id
    # only) would then overwrite in_progress -> cancelled, violating "never cancel
    # after trip start" (Period 3 left open, fare settlement skipped, regulatory
    # trip log corrupted). Filtering on the pre-trip states matches zero rows once
    # the ride has started/ended -> 409, nothing mutated. Mirrors the rider cancel
    # path in routes/rides.py.
    # driver_id scopes the atomic update to the assigned driver as well, so even
    # if the in-memory ownership check above raced a reassignment the write can
    # only ever touch this driver's own ride (findings 5/6).
    _cancel_filter = {
        "id": ride_id,
        "driver_id": driver["id"],
        "status": {
            "$in": [
                "requested",
                RideStatus.SEARCHING,
                RideStatus.DRIVER_ASSIGNED,
                RideStatus.DRIVER_ACCEPTED,
                "en_route",
                RideStatus.DRIVER_ARRIVED,
            ]
        },
    }

    # Try to persist cancelled_by / cancellation_reason for audit. These
    # columns may not exist in older Supabase schemas — PGRST204 on an
    # unknown column would crash the whole cancel. Fall back to the
    # minimal (still status-guarded) update so the cancellation still succeeds.
    try:
        _claim = await db_supabase.update_one(
            "rides",
            _cancel_filter,
            {
                **base_update,
                "cancelled_by": "driver",
                # Migration 38 — coarse attribution for admin filtering.
                "cancellation_type": "driver_cancel",
                "cancellation_reason": (reason or "").strip() or None,
            },
        )
    except Exception as exc:
        logger.warning(
            f"[CANCEL] cancelled_by/cancellation_reason write failed (likely PGRST204); retrying minimal update: {exc}"
        )
        _claim = await db_supabase.update_one("rides", _cancel_filter, base_update)

    if _claim is None:
        raise HTTPException(
            status_code=409,
            detail="Ride can no longer be cancelled (it has started or already ended)",
        )

    # Saskatchewan Regulatory / Accessibility: service animal accommodation
    # is mandatory and a driver refusal is a tracked terms violation subject
    # to account review (CLAUDE.md). Driver cancels don't otherwise write an
    # audit_logs row (only cancellation_reason free text on the ride itself),
    # so this specific reason gets a dedicated audit event to make it
    # queryable/reportable for trust & safety, plus an info log tagged
    # domain=safety per the Sentry-tag conventions for correlation. IDs
    # only — no PII. Automated account-review enforcement on repeated
    # refusals is a separate, deferred follow-up.
    if reason and _SERVICE_ANIMAL_CANCEL_MARKER in reason.lower():
        logger.info(
            "[CANCEL] driver reported inability to accommodate a service animal",
            extra={"domain": "safety", "surface": "backend", "ride_id": ride_id, "driver_id": driver["id"]},
        )
        try:
            await db_supabase.insert_one(
                "audit_logs",
                {
                    "id": str(uuid.uuid4()),
                    "action": "ride_cancel_service_animal_refusal",
                    "entity_type": "ride",
                    "entity_id": ride_id,
                    "actor_id": driver["id"],
                    "details": {"driver_id": driver["id"]},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as _exc:
            logger.error(f"Could not log service animal refusal to audit_logs: {_exc}", exc_info=True)

    # WS-8 (finding 11): release the booking-time pre-auth hold so the
    # rider's card isn't blocked for up to 7 days after a driver cancel.
    _booking_pi = ride.get("payment_intent_id")
    _auth = (ride.get("auth_status") or "").lower()
    if _booking_pi and _auth in ("authorized", "fare_only"):
        try:
            _released = await _deps.cancel_authorization(ride_id=ride_id, payment_intent_id=_booking_pi)
            if _released:
                logger.info("[CANCEL] released pre-auth hold ride_id=%s pi=%s", ride_id, _booking_pi)
                try:
                    await db_supabase.update_ride(ride_id, {"auth_status": "released"})
                except Exception:
                    logger.warning("[CANCEL] auth_status=released write failed ride_id=%s", ride_id)
        except Exception as _rel_exc:
            logger.error("[CANCEL] pre-auth release failed ride_id=%s: %s", ride_id, _rel_exc, exc_info=True)

    # Make driver available again
    await db_supabase.set_driver_available(driver["id"], True)
    # M-5: SGI insurance period audit — driver-side cancel after the
    # driver was assigned/accepted/arrived returns them to period 1.
    # If the ride was still in searching the driver was never in period
    # 2; skip to avoid a phantom 1→1 transition.
    if ride.get("status") in (
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED,
    ):
        await _deps.record_period_transition(driver["id"], 1)

    ride = await db_supabase.get_ride(ride_id)
    if ride and ride.get("rider_id"):
        await _deps.manager.send_personal_message(
            {"type": "ride_cancelled", "ride_id": ride_id, "reason": reason},
            f"rider_{ride['rider_id']}",
        )
        # Backgrounded like the accept/arrive/start pushes — the awaited WS
        # message above is what the open app reacts to.
        spawn(
            _deps.send_push_notification(
                ride["rider_id"],
                "Ride Cancelled ❌",
                "Your driver has cancelled the ride.",
                data={"type": "ride_cancelled", "ride_id": str(ride_id)},
            )
        )
    # Surfaced to admins so a scheduled ride's driver-cancel (unconditionally
    # terminal — no auto-requeue, for any ride type) can be told apart from
    # an on-demand one: the rider planned around this specific pickup and has
    # less slack to just re-hail (scheduled-rides gap review, Finding #12).
    _was_scheduled = bool((ride or {}).get("is_scheduled"))
    await _deps.manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=(ride or {}).get("rider_id"),
        reason="driver_cancelled",
        is_scheduled=_was_scheduled,
    )
    # End the rider's live activity on driver cancellation.
    spawn(send_live_activity_update(ride or {"id": ride_id, "status": RideStatus.CANCELLED}, EVENT_END))
    # Keep the specific ``ride_cancelled`` event on admin for dashboards
    # that switch on event name.
    try:
        await _deps.manager.broadcast_to_admins(
            {
                "type": "ride_cancelled",
                "ride_id": ride_id,
                "reason": "driver_cancelled",
                "is_scheduled": _was_scheduled,
            }
        )
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"driver cancel admin broadcast failed: {_exc}")

    return {"success": True}


@router.post("/rides/{ride_id}/noshow")
async def mark_rider_noshow(
    ride_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Driver marks rider as no-show after waiting at pickup.

    Requires: ride is in driver_arrived state, driver has waited at least
    noshow_wait_seconds (default 300 = 5 min) since arriving. Charges the
    rider a no-show fee and pays the driver.
    """
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    ride = await db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=403, detail="Not your assigned ride")
    if ride.get("status") != RideStatus.DRIVER_ARRIVED:
        raise RideStateError("No-show can only be marked when driver has arrived")

    driver_arrived_at = ride.get("driver_arrived_at")
    if not driver_arrived_at:
        raise HTTPException(status_code=400, detail="Arrival time not recorded")

    if isinstance(driver_arrived_at, str):
        arrived_dt = datetime.fromisoformat(driver_arrived_at.replace("Z", "+00:00"))
    else:
        arrived_dt = driver_arrived_at
    if arrived_dt.tzinfo is None:
        arrived_dt = arrived_dt.replace(tzinfo=timezone.utc)

    try:
        from ...settings_loader import get_app_settings
    except ImportError:
        from settings_loader import get_app_settings  # type: ignore
    settings = await get_app_settings() or {}
    area = None
    if ride.get("service_area_id"):
        area = await db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
    if area and area.get("noshow_wait_seconds") is not None:
        noshow_wait_seconds = int(area["noshow_wait_seconds"])
    else:
        noshow_wait_seconds = int(settings.get("noshow_wait_seconds", 300))

    waited = (datetime.now(timezone.utc) - arrived_dt).total_seconds()
    if waited < noshow_wait_seconds:
        remaining = int(noshow_wait_seconds - waited)
        raise HTTPException(
            status_code=400,
            detail=f"Must wait {remaining} more seconds before marking no-show",
        )

    # C2: atomically claim the no-show cancel (driver_arrived -> cancelled) BEFORE
    # charging anyone. No-show charges the rider and pays the driver; if the status
    # write were deferred until after the charge (and filtered on id only), a ride
    # that started in the race window would be BOTH charged a no-show fee AND
    # overwritten cancelled. The status-guarded claim matches zero rows once the
    # ride leaves driver_arrived -> 409, nothing charged. It also makes a duplicate
    # no-show call idempotent (no double charge).
    _noshow_now = datetime.now(timezone.utc)
    _noshow_claim = await db_supabase.update_one(
        "rides",
        {"id": ride_id, "status": RideStatus.DRIVER_ARRIVED},
        {"status": RideStatus.CANCELLED, "cancelled_at": _noshow_now, "updated_at": _noshow_now},
    )
    if _noshow_claim is None:
        raise HTTPException(
            status_code=409,
            detail="Ride can no longer be marked no-show (it has started or already ended)",
        )

    try:
        from ...services.cancellation_service import (
            calculate_noshow_fee,
            pay_driver_cancellation_fee,
        )
    except ImportError:
        from services.cancellation_service import calculate_noshow_fee, pay_driver_cancellation_fee  # type: ignore

    fee_admin, fee_driver = calculate_noshow_fee(ride, settings, area)
    total_fee = fee_admin + fee_driver

    # Charge rider
    if total_fee > 0:
        payment_method = (ride.get("payment_method") or "card").lower()
        if payment_method == "wallet":
            rider_id = ride.get("rider_id")
            if rider_id:
                rider_wallet = await db_supabase.find_one("wallets", {"user_id": rider_id})
                if rider_wallet:
                    # WS-6 (finding 8): atomic locked debit. This previously read
                    # the balance, computed max(balance - fee, 0) in Python, and
                    # wrote it back filtered on {id} only — a top-up webhook or
                    # another ride's fee landing in between was silently lost.
                    # clamp_to_floor reproduces the charge-what-they-have
                    # behaviour inside the lock, and reference_id=ride_id makes a
                    # replayed no-show idempotent instead of double-charging.
                    _noshow_txn = await db_supabase.wallet_apply_delta(
                        wallet_id=rider_wallet["id"],
                        user_id=rider_id,
                        type_="noshow_fee",
                        delta=-total_fee,
                        reference_id=ride_id,
                        description=f"No-show fee for ride {ride_id[:8]}",
                        metadata={"ride_id": ride_id},
                        floor=Decimal("0"),
                        clamp_to_floor=True,
                    )
                    # The RPC writes the ledger row itself, using the amount it
                    # actually took. Surface a short-collection so the gap
                    # between fee charged and driver payout is visible.
                    _charged = abs(Decimal(str(_noshow_txn.get("applied_delta") or 0)))
                    if _charged < total_fee:
                        logger.info(
                            "[NOSHOW] partial no-show fee collected ride_id=%s charged=%s of=%s",
                            ride_id,
                            _charged,
                            total_fee,
                        )

    # Pay driver
    if fee_driver > 0:
        await pay_driver_cancellation_fee(
            ride_id=ride_id,
            driver_id=driver["id"],
            fee=fee_driver,
            actor_user_id=current_user["id"],
            ride_status_at_cancel="noshow",
        )

    # Status was already flipped to cancelled by the atomic claim above; here we
    # only persist the fee attribution. Writing these columns onto an already-
    # terminal cancelled ride is safe (cancelled cannot transition away), so an
    # id-only update is fine. Optional columns may not exist on older schemas.
    _fee_now = datetime.now(timezone.utc)
    try:
        await db_supabase.update_ride(
            ride_id,
            {
                "cancelled_by": "driver",
                "cancellation_type": "noshow",
                "cancellation_fee_admin": float(fee_admin.quantize(Decimal("0.01"))),
                "cancellation_fee_driver": float(fee_driver.quantize(Decimal("0.01"))),
                "updated_at": _fee_now,
            },
        )
    except Exception as exc:
        logger.warning(f"[NOSHOW] extended fields write failed; retrying minimal: {exc}")
        await db_supabase.update_ride(ride_id, {"updated_at": _fee_now})

    await db_supabase.set_driver_available(driver["id"], True)
    await _deps.record_period_transition(driver["id"], 1)

    rider_id = ride.get("rider_id")
    if rider_id:
        await _deps.manager.send_personal_message(
            {
                "type": "ride_cancelled",
                "ride_id": ride_id,
                "reason": "noshow",
                "noshow_fee": float(total_fee.quantize(Decimal("0.01"))),
            },
            f"rider_{rider_id}",
        )
        # Backgrounded like the other ride-transition pushes.
        spawn(
            _deps.send_push_notification(
                rider_id,
                "Ride Cancelled",
                f"Your driver waited but you didn't show up. A ${float(total_fee.quantize(Decimal('0.01'))):.2f} no-show fee has been charged.",
                data={"type": "ride_noshow", "ride_id": str(ride_id)},
            )
        )
        # A charge the rider did not choose to make needs a written record they
        # can find later and dispute against — the push says the amount once and
        # then it is gone. total_fee stays a Decimal all the way to the email;
        # the float() above is only for the WS payload's JSON encoding.
        spawn(_deps.send_no_show_fee_email(rider_id, total_fee, ride=ride))

    await _deps.manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=rider_id,
        reason="noshow",
    )
    try:
        await _deps.manager.broadcast_to_admins(
            {
                "type": "ride_noshow",
                "ride_id": ride_id,
                "fee": float(total_fee.quantize(Decimal("0.01"))),
            }
        )
    except Exception as _exc:
        logger.warning(f"noshow admin broadcast failed: {_exc}")

    return {
        "success": True,
        "noshow_fee_total": float(total_fee.quantize(Decimal("0.01"))),
        "noshow_fee_driver": float(fee_driver.quantize(Decimal("0.01"))),
    }


@router.post("/rides/{ride_id}/rate-rider")
async def rate_rider(
    ride_id: str,
    rating_data: RideRatingRequest,
    current_user: dict = Depends(get_current_user),
):
    driver = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("drivers", {"user_id": current_user["id"]}, limit=1)
    )
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Authorization: a driver may only rate the rider on a ride they actually
    # drove. Without this guard any credentialed driver could overwrite the
    # rider_rating/rider_comment on any ride by supplying an arbitrary ride_id.
    # Return the SAME 404 for a ride owned by another driver as for a missing
    # ride, so a leaked/guessed ride_id can't be used to distinguish real rides
    # from nonexistent ones (matches the chat-status guard).
    ride = await db_supabase.get_ride(ride_id)
    if not ride or ride.get("driver_id") != driver["id"]:
        raise HTTPException(status_code=404, detail="Ride not found")

    # Update ride with rating
    await db_supabase.update_ride(
        ride_id,
        {
            "rider_rating": rating_data.rating,
            "rider_comment": rating_data.comment,
            "updated_at": datetime.now(timezone.utc),
        },
    )

    return {"success": True}
