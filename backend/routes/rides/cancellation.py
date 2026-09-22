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
    # 2026-08-18 fleet audit: ride-state-transition metric — the one write
    # here that actually flips status; the fee/attribution update further
    # below re-writes the same already-cancelled row and must NOT double-count.
    _deps._metric_inc("spinr_rides_state_transition_total", {"to_status": "cancelled"})

    driver_id = ride.get("driver_id")

    # Fee computation is hoisted ABOVE the hold handling because the hold can now
    # PAY the fee rather than being released and replaced by a fresh charge — so
    # what we do with the hold depends on whether a fee is owed. Safe to hoist:
    # this block only reads settings/service-area and runs a pure calculation, no
    # writes. Failures default the fee to 0 (no fee) rather than a partial one,
    # matching the pre-existing best-effort contract described below.
    charged_admin = charged_driver = Decimal("0")
    total_cancel_fee = Decimal("0")
    settings = None
    area = None
    try:
        settings = await _deps.get_app_settings()
        if ride.get("service_area_id"):
            area = await _deps.db_supabase.find_one("service_areas", {"id": ride["service_area_id"]})
        charged_admin, charged_driver = calculate_cancellation_fee(ride, settings, area)
        total_cancel_fee = _round(charged_admin + charged_driver)
    except Exception as _fee_exc:
        logger.opt(exception=True).error(
            "[CANCEL] cancellation fee computation failed ride_id={}: {}", ride_id, _fee_exc
        )
        charged_admin = charged_driver = Decimal("0")
        total_cancel_fee = Decimal("0")

    cancel_fee_payment_status: Optional[str] = None
    cancel_fee_payment_intent_id: Optional[str] = None
    cancel_fee_charge_attempted = False
    # How much of the fee was taken straight out of the booking hold. Non-zero
    # means the fee is already collected and the card-charge path below must be
    # skipped, or the rider is billed twice.
    fee_taken_from_hold = Decimal("0")
    # True only when cancel_authorization actually released the hold, so the
    # auth_status write below can tell 'released' apart from 'left open after a
    # failed capture'.
    _hold_released_in_full = False
    # Non-zero only when the ALREADY-captured-hold branch below issues a real
    # Stripe refund. Feeds refund_amount / payment_status in the final update.
    _excess_refunded = Decimal("0")
    _refund_lifecycle_status: Optional[str] = None
    _refund_provider_id: Optional[str] = None
    # An already-captured hold must never fall through to another fee charge
    # until its refund outcome is known. Keep this separate from the amount
    # retained: zero retained money does not prove that a refund was rejected.
    _captured_refund_unresolved = False

    # WS-8 (finding 11): the booking-time hold must not sit on the rider's card
    # for up to 7 days after a cancel. Two ways out, and which one we take
    # depends on whether a fee is owed:
    #
    #   fee owed, card ride -> PARTIAL CAPTURE. Take the fee from the hold and
    #     let Stripe release the remainder. Strictly better than the old
    #     cancel-then-charge-a-fresh-PI flow, because the funds are already
    #     reserved: this fee cannot be declined for insufficient funds, whereas
    #     a new charge against the same card can, and did whenever a rider's
    #     balance moved between booking and cancelling.
    #
    #   no fee, or a non-card ride -> FULL RELEASE, exactly as before.
    #
    # Either way this happens BEFORE any payment field is overwritten.
    _booking_pi = ride.get("payment_intent_id")
    _auth = (ride.get("auth_status") or "").lower()
    _held_amount = _round(_d(ride.get("authorized_amount") or 0))
    _hold_is_live = bool(_booking_pi) and _auth in ("authorized", "fare_only")
    _is_card_ride = (ride.get("payment_method") or "card").lower() == "card"

    if _hold_is_live:
        if total_cancel_fee > 0 and _is_card_ride and _held_amount > 0:
            try:
                _fee_outcome = await _deps.capture_cancellation_fee(
                    ride_id=ride_id,
                    payment_intent_id=_booking_pi,
                    fee=total_cancel_fee,
                    authorized_amount=_held_amount,
                )
            except Exception as _cap_exc:  # pragma: no cover — helper never raises
                logger.opt(exception=True).error("[CANCEL] fee capture raised ride_id={}: {}", ride_id, _cap_exc)
                _fee_outcome = None

            if _fee_outcome is not None and _fee_outcome.status == "captured":
                fee_taken_from_hold = _round(_d(_fee_outcome.charged_amount))
                cancel_fee_charge_attempted = True
                cancel_fee_payment_status = "paid"
                cancel_fee_payment_intent_id = _fee_outcome.payment_intent_id
                logger.info(
                    "[CANCEL] fee taken from hold ride_id={} captured={} of fee={} (remainder released)",
                    ride_id,
                    fee_taken_from_hold,
                    total_cancel_fee,
                )
                # Durable ledger write, mirroring the fresh-charge path below.
                # delta is what was ACTUALLY captured, not the computed fee —
                # a capped capture must not book revenue we never took.
                # Never raises; the money has already moved either way.
                await _deps.record_ledger_event(
                    event_type="stripe_charge",
                    user_id=current_user["id"],
                    ride_id=ride_id,
                    delta_cents=_deps.ledger_to_cents(fee_taken_from_hold),
                    ref=_fee_outcome.payment_intent_id,
                    metadata={
                        "source": "cancellation_fee",
                        "collection": "hold_partial_capture",
                        "driver_id": driver_id or "",
                        "fee_admin": str(_round(charged_admin)),
                        "fee_driver": str(_round(charged_driver)),
                    },
                )
            else:
                # Do NOT cancel the hold here. The fee is still owed, and the
                # fallback below charges a fresh PaymentIntent — releasing now
                # would drop our only reserved funds before knowing whether that
                # fallback succeeds. An uncaptured hold expires on its own, and
                # the orphaned-hold reconciler sweeps it.
                logger.error(
                    "[CANCEL] fee capture from hold failed ride_id={} status={} — falling back to a fresh charge",
                    ride_id,
                    getattr(_fee_outcome, "status", "raised"),
                )
        else:
            try:
                _released = await _deps.cancel_authorization(ride_id=ride_id, payment_intent_id=_booking_pi)
                if _released:
                    _hold_released_in_full = True
                    logger.info("[CANCEL] released pre-auth hold ride_id={} pi={}", ride_id, _booking_pi)
            except Exception as _rel_exc:
                logger.opt(exception=True).error("[CANCEL] pre-auth release failed ride_id={}: {}", ride_id, _rel_exc)
    elif _auth == "captured" and bool(_booking_pi):
        # _hold_is_live only covers a LIVE (uncaptured) hold. This is its
        # already-captured counterpart: the booking hold was captured before
        # this cancellation ran (e.g. the payment-retry loop's requires_capture
        # auto-capture raced this same cancellation — 2026-09-21 fix,
        # docs/change-log/2026-09-21-payment-retry-requires-capture-pre-trip-guard.md).
        # Refund whatever was taken beyond the fee actually owed here, which
        # may be the full amount on a free cancellation. Without this branch
        # the rider keeps paying full fare for a ride the fee calculation says
        # should cost them nothing — confirmed via a real test ride.
        try:
            _refund_outcome = await _deps.refund_excess_capture(
                ride_id=ride_id,
                payment_intent_id=_booking_pi,
                fee_owed=total_cancel_fee,
            )
        except Exception as _refund_exc:  # pragma: no cover — helper never raises
            logger.opt(exception=True).error(
                "[CANCEL] excess-capture refund raised ride_id={}: {}", ride_id, _refund_exc
            )
            _refund_outcome = None

        if _refund_outcome is not None and _refund_outcome.status == "refunded":
            _refund_lifecycle_status = "succeeded"
            _refund_provider_id = (_refund_outcome.raw or {}).get("refund_id")
            _excess_refunded = _round(_d(_refund_outcome.charged_amount))
            # The fee (if any) was already retained from the capture — the
            # fresh-charge fallback below must NOT also bill it.
            fee_taken_from_hold = total_cancel_fee if total_cancel_fee > 0 else _excess_refunded
            logger.info(
                "[CANCEL] refunded excess capture ride_id={} refunded={} fee_owed={}",
                ride_id,
                _excess_refunded,
                total_cancel_fee,
            )
            _refund_cents = _deps.ledger_to_cents(_excess_refunded)
            # F1 replay-safety (matches routes/webhooks.py's two record_refund_event
            # call sites): keyed on PI + amount so a retried cancellation call
            # books this refund's ledger row exactly once.
            _refund_ledger_id = await _deps.record_refund_event(
                ride_id=ride_id,
                user_id=current_user["id"],
                refund_cents=_refund_cents,
                payment_intent_id=_refund_outcome.payment_intent_id,
                ride=ride,
                dedupe_key=f"stripe_refund|{_refund_outcome.payment_intent_id}|{_refund_cents}",
            )
            if _refund_ledger_id is None:
                logger.error(
                    "[CANCEL] excess-capture refund succeeded on Stripe but the ledger write "
                    "failed ride_id={} refunded={} — money moved, needs reconciliation",
                    ride_id,
                    _excess_refunded,
                )
        elif _refund_outcome is not None and _refund_outcome.status == "not_needed":
            # Whatever was captured already covers (or falls short of) the fee
            # owed — nothing to give back. Still mark the fee as covered by
            # the capture so the fresh-charge fallback below doesn't bill it
            # again for money already sitting captured.
            if total_cancel_fee > 0:
                fee_taken_from_hold = total_cancel_fee
            else:
                # Anomalous: nothing owed AND nothing was captured to refund,
                # despite auth_status=="captured" implying money should be
                # sitting there. Log for visibility rather than pass silently.
                logger.warning(
                    "[CANCEL] auth_status is 'captured' but nothing was received on the "
                    "PaymentIntent to refund ride_id={} pi={}",
                    ride_id,
                    _booking_pi,
                )
        else:
            if _refund_outcome is not None and _refund_outcome.status in {
                "pending", "failed", "canceled", "requires_action"
            }:
                _refund_lifecycle_status = _refund_outcome.status
                _refund_provider_id = (_refund_outcome.raw or {}).get("refund_id")
            # Money is still sitting captured and un-refunded. Never silently
            # swallow a payment-path failure (CLAUDE.md) — surface loudly so
            # this gets reconciled rather than lost. Deliberately do NOT set
            # fee_taken_from_hold here: with the refund unresolved, falling
            # through to a fresh fee charge would double-bill on top of an
            # already-captured, un-refunded hold.
            logger.error(
                "[CANCEL] excess-capture refund failed ride_id={} status={} — rider may be owed a refund from "
                "an already-captured hold; needs manual reconciliation",
                ride_id,
                getattr(_refund_outcome, "status", "raised"),
            )
            _captured_refund_unresolved = True

    # The cancel is already persisted by the atomic claim above, so the
    # assigned driver MUST be released, transitioned back to Period 1, and
    # notified regardless of what happens while computing or charging the fee.
    # EVERYTHING from the fee computation through the fee writes is best-effort
    # after the claim: the settings read, the service-area read, the fee
    # calculation (all hoisted above the hold handling now, with their own
    # try/except), and the wallet/driver-payout writes can each raise, and if any
    # does we must not exit before the set_driver_available /
    # insurance / notification cleanup below — that would strand the driver as
    # unavailable and uninformed on a ride that is already cancelled. Surface
    # failures loudly (error + traceback, per repo policy) for reconciliation,
    # then fall through to driver cleanup. charged_* default to 0 so a failed
    # fee computation records no fee rather than a stale/partial one — see the
    # hoisted computation above, which owns that defaulting now.
    try:
        # Charge the rider the cancellation fee before paying the driver.
        # ``fee_taken_from_hold > 0`` means the partial capture above already
        # collected it, so this whole block is skipped — charging again here
        # would bill the rider twice for one cancellation. That also covers the
        # capped case (fee larger than the hold): the shortfall is deliberately
        # written off rather than chased with a second charge.
        if total_cancel_fee > 0 and fee_taken_from_hold <= 0 and not _captured_refund_unresolved:
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
                            "[CANCEL] partial cancellation fee collected ride_id={} charged={} of={}",
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
                        "[CANCEL] cancellation fee charge skipped (stripe unconfigured) ride={} amount={}",
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
                        # Durable ledger write (retries + Sentry escalation on
                        # exhaustion, never raises) — the charge already
                        # succeeded, so this must not block the cancel either
                        # way. fee_admin/fee_driver ride in metadata so the
                        # double-entry projection can decompose the fee without
                        # re-deriving the split.
                        await _deps.record_ledger_event(
                            event_type="stripe_charge",
                            user_id=current_user["id"],
                            ride_id=ride_id,
                            delta_cents=_deps.ledger_to_cents(total_cancel_fee),
                            ref=outcome.payment_intent_id,
                            metadata={
                                "source": "cancellation_fee",
                                "driver_id": driver_id or "",
                                "fee_admin": str(_round(charged_admin)),
                                "fee_driver": str(_round(charged_driver)),
                            },
                        )
                    else:
                        cancel_fee_payment_status = "failed"
                        logger.error(
                            "[CANCEL] cancellation fee card charge failed ride={} rider={} amount={} "
                            "status={} error={}",
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
        logger.opt(exception=True).error(
            "[CANCEL] cancellation-fee write failed after the cancel was "
            "persisted for ride {}; releasing the driver anyway — fee needs "
            "reconciliation: {}",
            ride_id,
            getattr(_fee_exc, "details", {}).get("original", _fee_exc) if hasattr(_fee_exc, "details") else _fee_exc,
        )

    _now = datetime.now(timezone.utc)
    _base_update = {
        "status": RideStatus.CANCELLED,
        "cancelled_at": _now,
        "cancellation_fee_admin": _f(charged_admin),
        "cancellation_fee_driver": _f(charged_driver),
        "updated_at": _now,
    }
    # WS-8: record what actually happened to the booking-time hold. This used to
    # write "released" whenever a live hold existed, which was true when the only
    # outcome was cancel-the-hold. There are now three outcomes and they must not
    # be conflated — `auth_status` is a strict 4-state contract (migration 156)
    # that reconcilers read as the source of truth:
    #
    #   captured  — the fee was partially captured from the hold. Money moved.
    #               Writing "released" here would claim nothing was taken.
    #   released  — the hold was cancelled in full. Nothing taken.
    #   unchanged — the capture FAILED and we deliberately left the hold open
    #               (see the else-branch above). It must stay "authorized"/
    #               "fare_only", because orphaned_hold_reconciler and
    #               card_hold_release both select on OPEN_AUTH_STATES. Marking it
    #               released would hide a genuinely open hold from the very
    #               sweepers that exist to catch it, stranding the rider's funds
    #               until Stripe's ~7-day expiry.
    if _hold_is_live:
        if fee_taken_from_hold > 0:
            _base_update["auth_status"] = "captured"
        elif _hold_released_in_full:
            _base_update["auth_status"] = "released"
    if cancel_fee_charge_attempted:
        # WS-8: store the fee PI in its own column (migration 251) instead
        # of overwriting payment_intent_id — preserving the booking-time PI
        # for audit and preventing payment_retry from chasing the wrong PI.
        _base_update["payment_status"] = cancel_fee_payment_status
        _base_update["cancel_fee_payment_intent_id"] = cancel_fee_payment_intent_id
    if _excess_refunded > 0:
        # An already-captured hold (the elif branch above) got a real Stripe
        # refund. "refunded" when nothing was owed (a fully free cancel),
        # "partially_refunded" when a fee was legitimately kept out of it —
        # matches the vocabulary routes/webhooks.py already uses for
        # charge.refunded / charge.refund.updated.
        _base_update["refund_amount"] = _f(_excess_refunded)
        _base_update["payment_status"] = "refunded" if total_cancel_fee <= 0 else "partially_refunded"
    if _refund_lifecycle_status:
        _base_update["refund_status"] = _refund_lifecycle_status
        if _refund_provider_id:
            _base_update["refund_id"] = _refund_provider_id
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
        logger.opt(exception=True).error(f"[CANCEL] attribution write failed ({_col_exc}); retrying minimal")
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
        # M-5: SGI insurance-period audit. A rider-side cancel after the driver
        # was assigned releases them and closes the Period 2 that assignment
        # opened, with whatever they actually are now. See the helper for why
        # this is neither a blanket Period 1 (#4597 Finding 3) nor silence.
        await _deps.release_driver_and_close_period(driver_id, reason="rider_cancelled", ride_id=ride_id)

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
            # N5 (ACTION_ITEMS.md): the WS message above only reaches a
            # foreground app. A driver already assigned to this ride is en
            # route to (or waiting at) pickup -- if their app is
            # backgrounded, locked, or killed, they'd keep driving toward a
            # rider who is gone with zero indication. priority="dispatch"
            # mirrors the new-offer push (matching.py) for the same reason:
            # this is as time-critical as an offer, bypasses the push
            # opt-out, and falls back to the retry queue on a transient
            # failure instead of being silently lost. Backgrounded via
            # spawn() so a slow FCM/Expo round-trip doesn't hold up the
            # rider's own cancel response.
            _deps.spawn(
                _deps.send_push_notification(
                    driver["user_id"],
                    "Ride Cancelled",
                    "The rider cancelled this ride.",
                    data={"type": "ride_cancelled", "ride_id": str(ride_id)},
                    priority="dispatch",
                    target_app="driver",
                )
            )

    # Batch dispatch: cancel pending ride_offers and notify those drivers.
    # With batch dispatch driver_id is NOT set on the ride row — offers
    # live in ride_offers. Without this block, drivers keep showing a
    # stale offer panel for a ride the rider already cancelled.
    try:
        _cancel_now = datetime.now(timezone.utc).isoformat()
        # The UPDATE is the claim: only drivers whose pending offers this
        # request actually changed to cancelled may be released. In particular,
        # a concurrent accept flips its offer out of pending and retains its
        # Period 2 obligation. RETURNING avoids a read-then-release race.
        cancelled_offers = await _deps.db_supabase.run_sync(
            lambda: (
                _deps.db_supabase.supabase.table("ride_offers")
                .update({"status": "cancelled", "responded_at": _cancel_now}, returning="representation")
                .eq("ride_id", ride_id)
                .eq("status", "pending")
                .execute()
            )
        )
        for offer_row in (getattr(cancelled_offers, "data", None) or []):
            _offer_did = offer_row["driver_id"]
            try:
                await _deps.release_batch_offer_driver_and_close_period(_offer_did, ride_id=ride_id)
            except Exception:
                # A failed period close must be visible, but should not leave
                # the driver app displaying an offer for an already-cancelled ride.
                logger.opt(exception=True).error(
                    "[CANCEL] batch-offer insurance release failed driver_id={} ride_id={}",
                    _offer_did,
                    ride_id,
                )
            try:
                _drv = await _deps.db_supabase.get_driver_by_id(_offer_did)
                _uid = (_drv or {}).get("user_id")
                if _uid:
                    await _deps.manager.send_personal_message(
                        {"type": "ride_cancelled", "ride_id": ride_id, "reason": "Rider cancelled"},
                        f"driver_{_uid}",
                    )
                    # N5 follow-up (ACTION_ITEMS.md): same WS-only gap as
                    # the assigned-driver case above, for the pending-offer
                    # (batch dispatch) path. A driver with a pending offer
                    # for this ride is actively deciding whether to accept
                    # -- if their app is backgrounded when the rider
                    # cancels, the WS message never reaches them and the
                    # stale offer panel keeps showing a ride that's gone.
                    # Same priority/target_app/backgrounding rationale as
                    # the assigned-driver push above.
                    _deps.spawn(
                        _deps.send_push_notification(
                            _uid,
                            "Ride Cancelled",
                            "The rider cancelled this ride.",
                            data={"type": "ride_cancelled", "ride_id": str(ride_id)},
                            priority="dispatch",
                            target_app="driver",
                        )
                    )
            except Exception as _e:
                logger.warning(f"[CANCEL] failed to notify batch-offer driver {_offer_did}: {_e}")
    except Exception as _batch_exc:
        logger.opt(exception=True).error(f"[CANCEL] batch offer cleanup failed for ride {ride_id}: {_batch_exc}")

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
            {
                "type": "ride_cancelled",
                "ride_id": ride_id,
                "reason": "rider_cancelled",
                # Same "flag scheduled cancellations for follow-up" signal the
                # driver-cancel path already sends (routes/drivers/ride_cancel.py) —
                # was missing here, so a rider cancelling their own scheduled
                # ride showed the generic "Ride cancelled" alert with no
                # follow-up flag, silently defeating that feature for this path.
                "is_scheduled": bool((ride or {}).get("is_scheduled")),
            }
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
        amount_cents = _deps.ledger_to_cents(fee)
        try:
            from ...utils.payment_operations import record_operation, update_operation
        except ImportError:  # pragma: no cover - dual import
            from utils.payment_operations import record_operation, update_operation  # type: ignore
        operation = await record_operation(
            operation_type="scheduled_notice_fee", ride_id=ride_id,
            idempotency_key=f"scheduled-notice-fee-{ride_id}-{amount_cents}-{payment_method}",
            amount_cents=amount_cents, payment_method=payment_method,
            metadata={"rider_id": rider_id},
        )
        actual = Decimal("0")
        outcome_status = "failed"
        payment_intent_id = None
        if payment_method == "wallet":
            rider_wallet = await _deps.db_supabase.find_one("wallets", {"user_id": rider_id})
            if rider_wallet:
                wallet_result = await _deps.db_supabase.wallet_apply_delta(
                    wallet_id=rider_wallet["id"],
                    user_id=rider_id,
                    type_="scheduled_cancel_notice_fee",
                    delta=-fee,
                    reference_id=ride_id,
                    description=f"Late-cancellation fee for scheduled ride {ride_id[:8]}",
                    metadata={"ride_id": ride_id, "ride_payment_operation_id": operation["id"]},
                    floor=Decimal("0"),
                    clamp_to_floor=True,
                )
                actual = abs(_d((wallet_result or {}).get("applied_delta") or "0"))
                outcome_status = "succeeded" if actual > 0 else "failed"
                payment_intent_id = (wallet_result or {}).get("transaction_id")
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
                extra_metadata={"ride_payment_operation_id": str(operation["id"])},
            )
            if outcome.status == "succeeded":
                reported_amount = getattr(outcome, "charged_amount", None)
                try:
                    actual = _round(_d(reported_amount)) if reported_amount is not None else fee
                except Exception:
                    # Older/mocked ChargeOutcome producers omit the amount;
                    # the helper's contract guarantees a successful ancillary
                    # fee captured the requested amount in that case.
                    actual = fee
                outcome_status = "succeeded"
                payment_intent_id = outcome.payment_intent_id
                # Durable ledger write (retries + Sentry escalation, never
                # raises). Rider-only pre-dispatch fee: no driver, no tax —
                # the double-entry projection books it all to platform_revenue.
                await _deps.record_ledger_event(
                    event_type="stripe_charge",
                    user_id=rider_id,
                    ride_id=ride_id,
                    delta_cents=_deps.ledger_to_cents(fee),
                    ref=outcome.payment_intent_id,
                    metadata={"source": "scheduled_cancel_notice_fee"},
                )
            elif outcome.status == "requires_action":
                outcome_status = "requires_action"
                payment_intent_id = outcome.payment_intent_id
            elif outcome.status != "unconfigured":
                logger.error(
                    "[SCHED-CANCEL] notice-window fee card charge failed ride={} rider={} amount={} status={} error={}",
                    ride_id,
                    rider_id,
                    fee,
                    outcome.status,
                    outcome.error_message,
                )
            else:
                outcome_status = "failed"
        # Any other payment_method (e.g. company_allowance) is already
        # excluded by calculate_scheduled_cancel_notice_fee returning 0.
        # Keep the operation due/pending until the ride-facing summary is
        # persisted. If that write fails, the existing worker can rebuild it
        # from this durable outcome metadata without charging again.
        await update_operation(
            str(operation["id"]), status="pending",
            payment_intent_id=payment_intent_id, provider_object_id=payment_intent_id,
            collected_cents=_deps.ledger_to_cents(actual),
            next_attempt_at=datetime.now(timezone.utc).isoformat(),
            metadata={"outcome_status": outcome_status, "collected_cents": _deps.ledger_to_cents(actual)},
        )
        await _deps.db_supabase.update_one("rides", {"id": ride_id}, {
            "scheduled_notice_fee_amount": str(actual),
            "scheduled_notice_fee_status": "paid" if outcome_status == "succeeded" else outcome_status,
            "scheduled_notice_fee_payment_intent_id": payment_intent_id,
        })
        await update_operation(
            str(operation["id"]), status=outcome_status, next_attempt_at=None,
        )
    except Exception as _fee_exc:
        logger.opt(exception=True).error(
            "[SCHED-CANCEL] notice-window fee charge failed for ride {}; cancellation already "
            "persisted and is not affected: {}",
            ride_id,
            _fee_exc,
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
            # 2026-08-18 fleet audit: ride-state-transition metric.
            _deps._metric_inc("spinr_rides_state_transition_total", {"to_status": "cancelled"})
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
            # broadcast_ride_status (above) sends admins a "ride_status_changed"
            # event, which the monitoring dashboard's cancellation alert-feed
            # does not read (it specifically listens for "ride_cancelled" — see
            # admin-dashboard/src/app/dashboard/monitoring/page.tsx). Without
            # this, a rider cancelling a scheduled ride BEFORE it dispatches
            # never surfaced the "⚠ Scheduled ride cancelled" admin alert at
            # all — not just missing the is_scheduled flag, unlike the
            # dispatched-ride path in cancel_ride_rider() below, which does
            # send this event type (now with is_scheduled included too).
            try:
                await _deps.manager.broadcast_to_admins(
                    {
                        "type": "ride_cancelled",
                        "ride_id": ride_id,
                        "reason": "rider_cancelled",
                        "is_scheduled": True,
                    }
                )
            except Exception as _exc:  # pragma: no cover - best effort
                logger.warning(f"scheduled rider cancel admin broadcast failed: {_exc}")
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
