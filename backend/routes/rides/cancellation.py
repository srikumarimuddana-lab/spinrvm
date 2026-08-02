"""Rider-initiated cancellation of active and scheduled rides.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    EVENT_END,
    APIRouter,
    Decimal,
    Depends,
    ErrorCode,
    ErrorKeys,
    HTTPException,
    Optional,
    Query,
    Request,
    RideNotFoundException,
    RideStatus,
    SpinrException,
    calculate_cancellation_fee,
    cancel_ride_limit,
    datetime,
    get_current_user,
    log_user_action,
    logger,
    send_live_activity_update,
    timezone,
    uuid,
)
from ._shared import (  # noqa: F401
    _d,
    _f,
    _require_ride_in_state_rider,
    _round,
)

router = APIRouter()


@router.post("/{ride_id}/cancel")
@cancel_ride_limit
async def cancel_ride_rider(
    ride_id: str,
    reason: str = Query(""),
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Rider cancels the ride. Optional `reason` is captured for the admin
    Cancellation card (preset reason or free-text note from the rider app)."""
    try:
        from ...logging_utils import diag_logger  # type: ignore
    except ImportError:
        from logging_utils import diag_logger  # type: ignore

    diag_logger.info(f"[CANCEL] called ride_id={ride_id} user_id={current_user.get('id')}")

    # Prefer the reason from the JSON body — free-text notes must not ride in the
    # URL query string (proxy/access logs, crash breadcrumbs leak it). Fall back
    # to the legacy ?reason= for older app builds.
    _body_reason = None
    if request is not None:
        try:
            _b = await request.json()
            if isinstance(_b, dict):
                _body_reason = _b.get("reason")
        except Exception:
            _body_reason = None
    reason = (str(_body_reason).strip() if _body_reason else "") or reason

    _cancellable_states = (
        "requested",
        RideStatus.SEARCHING,
        RideStatus.DRIVER_ASSIGNED,
        RideStatus.DRIVER_ACCEPTED,
        "en_route",
        RideStatus.DRIVER_ARRIVED,
    )
    ride = await _require_ride_in_state_rider(ride_id, current_user["id"], _cancellable_states)
    diag_logger.info(
        f"[CANCEL] entry ride_id={ride_id} pre_status={ride.get('status')} driver_id={ride.get('driver_id')}"
    )

    # Atomically claim the cancel BEFORE charging any fee. _require_ride_in_state_rider
    # only read+validated the status; in the window before the write the driver could
    # call verify-otp/start and flip the ride to in_progress. A non-atomic cancel would
    # then overwrite in_progress -> cancelled (violating "never cancel after trip start")
    # AND charge a cancellation fee on a ride that actually began. The $in guard matches
    # zero rows once the ride has left the pre-trip states -> 409, nothing charged.
    _cancel_now = datetime.now(timezone.utc)
    _cancel_claim = await _deps.db_supabase.update_one(
        "rides",
        {"id": ride_id, "status": {"$in": list(_cancellable_states)}},
        {"status": RideStatus.CANCELLED, "cancelled_at": _cancel_now, "updated_at": _cancel_now},
    )
    if _cancel_claim is None:
        diag_logger.info(f"[CANCEL] claim rejected ride_id={ride_id} — ride left pre-trip state")
        raise HTTPException(
            status_code=409,
            detail="Ride can no longer be cancelled (it has started or already ended)",
        )

    driver_id = ride.get("driver_id")

    # WS-8 (finding 11): release the booking-time pre-auth hold so the
    # rider's card isn't blocked for up to 7 days. Must happen BEFORE
    # we overwrite any payment fields. Best-effort: if the hold is already
    # captured/expired, cancel_authorization returns False and we carry on.
    _booking_pi = ride.get("payment_intent_id")
    _auth = (ride.get("auth_status") or "").lower()
    if _booking_pi and _auth in ("authorized", "fare_only"):
        try:
            _released = await _deps.cancel_authorization(ride_id=ride_id, payment_intent_id=_booking_pi)
            if _released:
                logger.info("[CANCEL] released pre-auth hold ride_id=%s pi=%s", ride_id, _booking_pi)
        except Exception as _rel_exc:
            logger.error("[CANCEL] pre-auth release failed ride_id=%s: %s", ride_id, _rel_exc, exc_info=True)

    # The cancel is already persisted by the atomic claim above, so the
    # assigned driver MUST be released, transitioned back to Period 1, and
    # notified regardless of what happens while computing or charging the fee.
    # EVERYTHING from the settings/area lookup through the fee writes is
    # best-effort after the claim: the settings read, the service-area read,
    # the fee calculation, and the wallet/driver-payout writes can each raise,
    # and if any does we must not exit before the set_driver_available /
    # insurance / notification cleanup below — that would strand the driver as
    # unavailable and uninformed on a ride that is already cancelled. Surface
    # failures loudly (error + traceback, per repo policy) for reconciliation,
    # then fall through to driver cleanup. charged_* default to 0 so a failed
    # fee computation records no fee rather than a stale/partial one.
    charged_admin = charged_driver = Decimal("0")
    cancel_fee_payment_status: Optional[str] = None
    cancel_fee_payment_intent_id: Optional[str] = None
    cancel_fee_charge_attempted = False
    try:
        settings = await _deps.get_app_settings()
        area = None
        if ride.get("service_area_id"):
            area = await _deps.db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
        charged_admin, charged_driver = calculate_cancellation_fee(ride, settings, area)
        total_cancel_fee = _round(charged_admin + charged_driver)

        # Charge the rider the cancellation fee before paying the driver.
        if total_cancel_fee > 0:
            payment_method = (ride.get("payment_method") or "card").lower()
            if payment_method == "wallet":
                rider_wallet = await _deps.db_supabase.find_one("wallets", {"user_id": current_user["id"]})
                if rider_wallet:
                    # WS-6 (finding 10): atomic locked debit. This previously
                    # read the balance, computed max(balance - fee, 0) in
                    # Python, and wrote it back filtered on {id} only — a
                    # wallet top-up webhook or another ride's fee landing
                    # between the read and the write was silently lost.
                    # clamp_to_floor keeps the charge-what-they-have behaviour
                    # inside the lock; reference_id=ride_id makes a replayed
                    # cancellation idempotent rather than double-charging.
                    _fee_txn = await _deps.db_supabase.wallet_apply_delta(
                        wallet_id=rider_wallet["id"],
                        user_id=current_user["id"],
                        type_="cancellation_fee",
                        delta=-total_cancel_fee,
                        reference_id=ride_id,
                        description=f"Cancellation fee for ride {ride_id[:8]}",
                        metadata={"ride_id": ride_id},
                        floor=Decimal("0"),
                        clamp_to_floor=True,
                    )
                    # The RPC writes the ledger row itself, using the amount it
                    # actually took. Surface a short-collection so the gap
                    # between fee charged and driver payout is visible.
                    _charged = _round(abs(_d(str(_fee_txn.get("applied_delta") or 0))))
                    if _charged < total_cancel_fee:
                        logger.info(
                            "[CANCEL] partial cancellation fee collected ride_id=%s charged=%s of=%s",
                            ride_id,
                            _charged,
                            total_cancel_fee,
                        )
            elif payment_method == "card":
                # Mirrors settle_card's payment-method resolution: a card pinned
                # to the ride wins (e.g. the in-app "Change Card" escape), else
                # the rider's saved default. Company-allowance / corporate-paid
                # rides are intentionally excluded — that fee belongs on the
                # corporate wallet ledger, not a personal Stripe card, and isn't
                # wired up here.
                rider_user = await _deps.db_supabase.get_user_by_id(current_user["id"])
                stripe_customer_id = (rider_user or {}).get("stripe_customer_id")
                payment_method_id = ride.get("payment_method_id") or (rider_user or {}).get("default_payment_method")
                outcome = await _deps.charge_ancillary_fee(
                    ride=ride,
                    rider_id=current_user["id"],
                    amount=total_cancel_fee,
                    payment_method_id=payment_method_id,
                    stripe_customer_id=stripe_customer_id,
                    fee_type="cancellation_fee",
                )
                if outcome.status == "unconfigured":
                    # Stripe isn't wired up (dev/test) — no Stripe call was made
                    # at all, so leave payment_status/payment_intent_id untouched
                    # rather than mislabel a config gap as a decline.
                    logger.error(
                        "[CANCEL] cancellation fee charge skipped (stripe unconfigured) ride=%s amount=%s",
                        ride_id,
                        total_cancel_fee,
                    )
                else:
                    # A real charge attempt happened (success or decline) — always
                    # overwrite both fields together, even to None on a decline.
                    # Leaving a stale booking-time hold's payment_intent_id in
                    # place next to a fresh payment_status="failed" would make
                    # payment_retry.py's blind PI-status scan retry the wrong
                    # PaymentIntent. Mirrors settle_card's declined-branch write.
                    cancel_fee_charge_attempted = True
                    cancel_fee_payment_intent_id = outcome.payment_intent_id
                    if outcome.status == "succeeded":
                        cancel_fee_payment_status = "paid"
                        try:
                            await _deps.db_supabase.insert_one(
                                "financial_events",
                                {
                                    "event_type": "stripe_charge",
                                    "user_id": current_user["id"],
                                    "ride_id": ride_id,
                                    "delta_cents": int(_round(total_cancel_fee * Decimal("100"))),
                                    "ref": outcome.payment_intent_id,
                                    "metadata": {"source": "cancellation_fee", "driver_id": driver_id or ""},
                                    "created_at": datetime.now(timezone.utc).isoformat(),
                                },
                            )
                        except Exception:
                            # Never let a ledger-write failure block the cancel or
                            # mask that the card WAS actually charged — log loudly
                            # so ops can backfill the reconciliation row.
                            logger.error(
                                "[CANCEL] financial_events write failed for cancellation fee "
                                "ride=%s pi=%s amount=%s — charge succeeded but is unrecorded",
                                ride_id,
                                outcome.payment_intent_id,
                                total_cancel_fee,
                                exc_info=True,
                            )
                    else:
                        cancel_fee_payment_status = "failed"
                        logger.error(
                            "[CANCEL] cancellation fee card charge failed ride=%s rider=%s amount=%s "
                            "status=%s error=%s",
                            ride_id,
                            current_user["id"],
                            total_cancel_fee,
                            outcome.status,
                            outcome.error_message,
                        )

        if driver_id and charged_driver > 0:
            await _deps.pay_driver_cancellation_fee(
                ride_id=ride_id,
                driver_id=driver_id,
                fee=charged_driver,
                actor_user_id=current_user["id"],
                ride_status_at_cancel=ride.get("status"),
            )
    except Exception as _fee_exc:
        logger.error(
            "[CANCEL] cancellation-fee write failed after the cancel was "
            "persisted for ride %s; releasing the driver anyway — fee needs "
            "reconciliation: %s",
            ride_id,
            getattr(_fee_exc, "details", {}).get("original", _fee_exc) if hasattr(_fee_exc, "details") else _fee_exc,
            exc_info=True,
        )

    _now = datetime.now(timezone.utc)
    _base_update = {
        "status": RideStatus.CANCELLED,
        "cancelled_at": _now,
        "cancellation_fee_admin": _f(charged_admin),
        "cancellation_fee_driver": _f(charged_driver),
        "updated_at": _now,
    }
    # WS-8: mark the booking-time hold as released so reconcilers and the
    # preauth_capture sweeper skip this ride.
    if _booking_pi and _auth in ("authorized", "fare_only"):
        _base_update["auth_status"] = "released"
    if cancel_fee_charge_attempted:
        # WS-8: store the fee PI in its own column (migration 251) instead
        # of overwriting payment_intent_id — preserving the booking-time PI
        # for audit and preventing payment_retry from chasing the wrong PI.
        _base_update["payment_status"] = cancel_fee_payment_status
        _base_update["cancel_fee_payment_intent_id"] = cancel_fee_payment_intent_id
    # Migration 38 — attribution. Fall back to the legacy payload on
    # PGRST204 so the rider's cancel button never 503s if the column
    # isn't in prod yet.
    _reason = (reason or "").strip() or None
    try:
        await _deps.db_supabase.update_ride(
            ride_id,
            {
                **_base_update,
                "cancelled_by": "rider",
                "cancellation_type": "rider_cancel",
                "cancellation_reason": _reason,
            },
        )
    except Exception as _col_exc:
        logger.error(f"[CANCEL] attribution write failed ({_col_exc}); retrying minimal", exc_info=True)
        await _deps.db_supabase.update_ride(ride_id, _base_update)

    # Verify the cancel actually landed in the database. Same class of
    # silent-failure we hit with go-online and accept: the update_one wrapper
    # returns None when zero rows are affected and the handler would
    # otherwise return {success: true} while the ride is still in its prior
    # state — the rider then reloads and sees the ride still "searching".
    try:
        verify_ride = await _deps.db_supabase.get_ride(ride_id)
    except Exception as e:
        verify_ride = None
        diag_logger.info(f"[CANCEL] verify re-read failed: {e}")

    diag_logger.info(
        f"[CANCEL] post-update ride_id={ride_id} "
        f"post_status={verify_ride.get('status') if verify_ride else 'ROW_GONE'} "
        f"post_cancelled_at={verify_ride.get('cancelled_at') if verify_ride else 'ROW_GONE'}"
    )

    if not verify_ride or verify_ride.get("status") != RideStatus.CANCELLED:
        diag_logger.info(
            f"[CANCEL] SILENT NO-OP: ride_id={ride_id} did not flip to "
            f"'cancelled'. Likely a missing column in the rides table "
            f"(e.g. cancelled_at / cancellation_fee_admin / "
            f"cancellation_fee_driver) or a wrapper dispatching the "
            f"update to the wrong path. Rider will see the ride as still "
            f"active after reload."
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Cancel did not persist. Backend write returned successfully "
                "but the ride row is unchanged. Check backend logs for "
                "[CANCEL] lines."
            ),
        )

    if driver_id:
        await _deps.db_supabase.set_driver_available(driver_id, True)
        # M-5: SGI insurance period audit — rider-side cancel after the
        # driver was assigned releases the driver back to period 1. If
        # the ride had no driver_id we never left period 1, so no row.
        await _deps.record_period_transition(driver_id, 1)

        # Notify driver
        driver = await _deps.db_supabase.get_driver_by_id(driver_id)
        if driver and driver.get("user_id"):
            await _deps.manager.send_personal_message(
                {
                    "type": "ride_cancelled",
                    "ride_id": ride_id,
                    "reason": "Rider cancelled",
                },
                f"driver_{driver['user_id']}",
            )

    # Batch dispatch: cancel pending ride_offers and notify those drivers.
    # With batch dispatch driver_id is NOT set on the ride row — offers
    # live in ride_offers. Without this block, drivers keep showing a
    # stale offer panel for a ride the rider already cancelled.
    try:
        pending_offers = await _deps.db_supabase.run_sync(
            lambda: (
                _deps.db_supabase.supabase.table("ride_offers")
                .select("driver_id")
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )
        if pending_offers.data:
            _cancel_now = datetime.now(timezone.utc).isoformat()
            await _deps.db_supabase.run_sync(
                lambda: (
                    _deps.db_supabase.supabase.table("ride_offers")
                    .update({"status": "cancelled", "responded_at": _cancel_now})
                    .eq("ride_id", ride_id)
                    .eq("status", "pending")
                    .execute()
                )
            )
            for offer_row in pending_offers.data:
                _offer_did = offer_row["driver_id"]
                await _deps.db_supabase.set_driver_available(_offer_did, True)
                try:
                    _drv = await _deps.db_supabase.get_driver_by_id(_offer_did)
                    _uid = (_drv or {}).get("user_id")
                    if _uid:
                        await _deps.manager.send_personal_message(
                            {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
                            f"driver_{_uid}",
                        )
                except Exception as _e:
                    logger.warning(f"[CANCEL] failed to notify batch-offer driver {_offer_did}: {_e}")
    except Exception as _batch_exc:
        logger.error(f"[CANCEL] batch offer cleanup failed for ride {ride_id}: {_batch_exc}", exc_info=True)

    # Notify the rider's own connection — broadcast_ride_status only fans
    # out to the rider when rider_id is passed, but an explicit message
    # ensures clearRide() fires immediately in useRiderSocket without
    # waiting for the next poll cycle.
    await _deps.manager.send_personal_message(
        {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"},
        f"rider_{current_user['id']}",
    )
    await _deps.manager.broadcast_ride_status(
        ride_id,
        RideStatus.CANCELLED,
        rider_id=current_user["id"],
        reason="rider_cancelled",
    )
    # End the rider's live activity (Lock Screen / ongoing notification).
    _deps.spawn(send_live_activity_update({"id": ride_id, "status": RideStatus.CANCELLED}, EVENT_END))
    try:
        await _deps.manager.broadcast_to_admins(
            {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"}
        )
    except Exception as _exc:  # pragma: no cover - best effort
        logger.warning(f"rider cancel admin broadcast failed: {_exc}")

    _deps.spawn(
        log_user_action(
            current_user,
            "ride_cancelled",
            "rides",
            ride_id,
            {
                "reason": "rider_cancelled",
                "cancellation_fee": str(charged_admin + charged_driver),
            },
        )
    )
    return {"success": True, "cancellation_fee": charged_admin + charged_driver}


async def _charge_scheduled_cancel_notice_fee(ride: dict, rider_id: str) -> None:
    """Charge the notice-window fee for a pre-dispatch scheduled-ride
    cancellation (Finding #01, scheduled-rides gap review), if the flag is
    on and the cancellation happened inside the window. Rider-only — no
    driver exists pre-dispatch, so there's no payout branch here, unlike
    calculate_cancellation_fee's admin/driver split for dispatched rides.

    Mirrors cancel_ride_rider's card/wallet charging pattern. Must never
    raise past this function — a fee failure must not undo an already-
    persisted cancellation; the caller wraps this in its own try/except as
    an extra layer of safety, but this function guards itself too.
    """
    ride_id = ride["id"]
    try:
        settings = await _deps.get_app_settings()
        fee = _deps.calculate_scheduled_cancel_notice_fee(ride, settings)
        if fee <= 0:
            return

        payment_method = (ride.get("payment_method") or "card").lower()
        if payment_method == "wallet":
            rider_wallet = await _deps.db_supabase.find_one("wallets", {"user_id": rider_id})
            if rider_wallet:
                await _deps.db_supabase.wallet_apply_delta(
                    wallet_id=rider_wallet["id"],
                    user_id=rider_id,
                    type_="scheduled_cancel_notice_fee",
                    delta=-fee,
                    reference_id=ride_id,
                    description=f"Late-cancellation fee for scheduled ride {ride_id[:8]}",
                    metadata={"ride_id": ride_id},
                    floor=Decimal("0"),
                    clamp_to_floor=True,
                )
        elif payment_method == "card":
            rider_user = await _deps.db_supabase.get_user_by_id(rider_id)
            stripe_customer_id = (rider_user or {}).get("stripe_customer_id")
            payment_method_id = ride.get("payment_method_id") or (rider_user or {}).get("default_payment_method")
            outcome = await _deps.charge_ancillary_fee(
                ride=ride,
                rider_id=rider_id,
                amount=fee,
                payment_method_id=payment_method_id,
                stripe_customer_id=stripe_customer_id,
                fee_type="scheduled_cancel_notice_fee",
            )
            if outcome.status == "succeeded":
                try:
                    await _deps.db_supabase.insert_one(
                        "financial_events",
                        {
                            "event_type": "stripe_charge",
                            "user_id": rider_id,
                            "ride_id": ride_id,
                            "delta_cents": int(_round(fee * Decimal("100"))),
                            "ref": outcome.payment_intent_id,
                            "metadata": {"source": "scheduled_cancel_notice_fee"},
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                except Exception:
                    logger.error(
                        "[SCHED-CANCEL] financial_events write failed for notice-window fee "
                        "ride=%s pi=%s amount=%s — charge succeeded but is unrecorded",
                        ride_id,
                        outcome.payment_intent_id,
                        fee,
                        exc_info=True,
                    )
            elif outcome.status != "unconfigured":
                logger.error(
                    "[SCHED-CANCEL] notice-window fee card charge failed ride=%s rider=%s amount=%s "
                    "status=%s error=%s",
                    ride_id,
                    rider_id,
                    fee,
                    outcome.status,
                    outcome.error_message,
                )
        # Any other payment_method (e.g. company_allowance) is already
        # excluded by calculate_scheduled_cancel_notice_fee returning 0.
    except Exception as _fee_exc:
        logger.error(
            "[SCHED-CANCEL] notice-window fee charge failed for ride %s; cancellation already "
            "persisted and is not affected: %s",
            ride_id,
            _fee_exc,
            exc_info=True,
        )


@router.delete("/scheduled/{ride_id}")
@cancel_ride_limit
async def cancel_scheduled_ride(
    ride_id: str,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Cancel a scheduled ride.

    Only the pre-dispatch ``scheduled`` state is handled here, behind an
    atomic status-filtered claim. ``is_scheduled`` stays True after the
    dispatch loop flips the ride live, so an id-only cancel here would
    overwrite a searching/accepted/in_progress ride with no driver release,
    no insurance-period transition, no fee, and no WS event. Once dispatched
    the ride is a normal active ride — delegate to cancel_ride_rider, which
    owns the atomic pre-trip claim and full cleanup (and 409s once the trip
    is in_progress).
    """
    ride = (lambda _r: _r[0] if _r else None)(
        await _deps.db_supabase.get_rows(
            "rides",
            {"id": ride_id, "rider_id": current_user["id"], "is_scheduled": True},
            limit=1,
        )
    )
    if not ride:
        raise RideNotFoundException(
            ride_id=ride_id,
            message_key=ErrorKeys.RIDE_NOT_FOUND,
        )
    if ride.get("status") in RideStatus.terminal_statuses():
        raise SpinrException(
            message="Ride is already completed or cancelled",
            error_code=ErrorCode.RIDE_ALREADY_CANCELLED,
            status_code=400,
            message_key=ErrorKeys.RIDE_ALREADY_CANCELLED,
        )

    if ride.get("status") == RideStatus.SCHEDULED:
        _now = datetime.now(timezone.utc)
        _base = {
            "status": RideStatus.CANCELLED,
            "cancelled_at": _now,
            "cancellation_reason": "Cancelled by rider (scheduled)",
            "updated_at": _now,
        }
        _claim_filter = {
            "id": ride_id,
            "rider_id": current_user["id"],
            "status": RideStatus.SCHEDULED,
        }
        try:
            claimed = await _deps.db_supabase.update_one(
                "rides",
                _claim_filter,
                {**_base, "cancelled_by": "rider", "cancellation_type": "rider_cancel"},
            )
        except Exception as _col_exc:
            # Only a genuine missing-attribution-column error (migration 38
            # not applied yet) may fall back to the minimal payload. Anything
            # else is a real DB failure and must surface, not be retried as a
            # routine schema mismatch. The column-missing text lives in
            # details['original'] / __cause__, not str(_col_exc).
            _details_attr = getattr(_col_exc, "details", None)
            _detail = str(_details_attr.get("original") or "") if isinstance(_details_attr, dict) else ""
            _cause_text = str(getattr(_col_exc, "__cause__", "") or "")
            _combined = f"{_col_exc} {_detail} {_cause_text}".lower()
            if not any(col in _combined for col in ("cancelled_by", "cancellation_type", "pgrst204")):
                raise
            logger.warning(
                f"[SCHED-CANCEL] attribution column(s) missing; retrying minimal. original={_detail or _col_exc}"
            )
            claimed = await _deps.db_supabase.update_one("rides", _claim_filter, _base)
        if claimed is not None:
            # Pre-dispatch there is no driver, offer, or card hold to unwind;
            # notify the rider's own devices and any watching admin console.
            # Notice-window fee (Finding #01): flag-gated, defaulted off; a
            # failure here must never undo the cancellation above.
            await _charge_scheduled_cancel_notice_fee(ride, current_user["id"])
            await _deps.manager.send_personal_message(
                {"type": "ride_cancelled", "ride_id": ride_id, "reason": "rider_cancelled"},
                f"rider_{current_user['id']}",
            )
            await _deps.manager.broadcast_ride_status(
                ride_id,
                RideStatus.CANCELLED,
                rider_id=current_user["id"],
                reason="rider_cancelled",
                is_scheduled=True,
            )
            return {"success": True}
        # Zero rows: the dispatch loop (or a concurrent cancel) won the race
        # since the read above. Re-read and fall through to the live-ride
        # path so the outcome matches the ride's real state.
        ride = await _deps.db_supabase.get_ride(ride_id)
        if not ride or ride.get("status") in RideStatus.terminal_statuses():
            raise SpinrException(
                message="Ride is already completed or cancelled",
                error_code=ErrorCode.RIDE_ALREADY_CANCELLED,
                status_code=400,
                message_key=ErrorKeys.RIDE_ALREADY_CANCELLED,
            )

    # Dispatched (searching → driver_arrived): full rider-cancel path —
    # atomic pre-trip claim (409 once in_progress), cancellation fee,
    # driver + batch-offer release, period-1 insurance transition, WS fan-out.
    return await cancel_ride_rider(
        ride_id,
        reason="Cancelled by rider (scheduled)",
        request=request,
        current_user=current_user,
    )
