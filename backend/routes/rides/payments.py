"""Tips and fare settlement endpoints.

Split from ``backend/routes/rides.py`` (god-file refactor). Pure code
motion — no behaviour changes. See docs/refactors/god-file-split.md.
"""

from . import _deps
from ._deps import (  # noqa: F401
    ROUND_HALF_UP,
    APIRouter,
    BaseModel,
    Decimal,
    Depends,
    Field,
    HTTPException,
    Optional,
    Request,
    RideStatus,
    _metric_inc,
    _metric_observe,
    _time_mod,
    build_earnings_snapshot,
    datetime,
    first_name_only,
    get_current_user,
    idempotent_endpoint,
    logger,
    payment_action_limit,
    send_ride_receipt,
    settle_card,
    settle_corporate,
    settle_wallet,
    timezone,
)
from ._shared import (  # noqa: F401
    _build_fare_breakdown,
    _d,
    _f,
    _money_str,
    _push_in_background,
    _round,
    _sum_fare_breakdown,
)

router = APIRouter()


class TipRequest(BaseModel):
    amount: Decimal = Field(..., gt=0, le=500, description="Tip in CAD (max $500)")


@router.post("/{ride_id}/tip")
@payment_action_limit
@idempotent_endpoint(scope="ride_tip")
async def add_tip(
    ride_id: str,
    req: TipRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    # Money arithmetic uses Decimal per CLAUDE.md. The old `float(req.amount)`
    # path drifted when summed with existing driver_earnings.
    tip_amount = _round(_d(req.amount))
    if tip_amount <= 0:
        raise HTTPException(status_code=400, detail="Tip amount must be greater than zero")

    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")

    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized to tip this ride")

    if ride.get("status") != RideStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Can only tip completed rides")

    # R-P1-20: Block duplicate tips — one tip per ride.
    existing_tip = _d(ride.get("tip_amount") or 0)
    if existing_tip > 0:
        raise HTTPException(status_code=400, detail="ERR_TIP_DUPLICATE")

    existing_earnings = _d(ride.get("driver_earnings") or 0)
    new_tip = _round(existing_tip + tip_amount)
    new_driver_earnings = _round(existing_earnings + tip_amount)

    update_payload = {"tip_amount": _f(new_tip), "driver_earnings": _f(new_driver_earnings)}

    # Update fare_breakdown_snapshot to include the tip so invoices and
    # ride details reflect the final charged amount.
    snapshot = ride.get("fare_breakdown_snapshot")
    if snapshot and isinstance(snapshot, dict) and snapshot.get("lines") is not None:
        updated_lines = [ln for ln in snapshot["lines"] if ln.get("type") != "tip"]
        updated_lines.append({"label": "Tip", "amount": _f(new_tip), "type": "tip"})
        snapshot["lines"] = updated_lines
        snapshot["grand_total"] = _sum_fare_breakdown(updated_lines)
        update_payload["fare_breakdown_snapshot"] = snapshot

    # Update driver_earnings_snapshot with the tip — rebuild via the Decimal
    # builder so the frozen total stays an exact component sum (feeds T4A).
    des = ride.get("driver_earnings_snapshot")
    if des and isinstance(des, dict):
        des.update(
            build_earnings_snapshot(
                fare=des.get("fare") or 0,
                tip=new_tip,
                incentive=des.get("incentive") or 0,
                tax=des.get("tax") or 0,
                cancel_fee=des.get("cancel_fee") or 0,
            )
        )
        update_payload["driver_earnings_snapshot"] = des

    await _deps.db_supabase.update_ride(ride_id, update_payload)

    # Notify the assigned driver so the tip shows up immediately instead
    # of only after the next earnings refresh. Best-effort — the tip has
    # already been persisted, so a failed notification must not surface
    # as a rider-facing error.
    driver_user_id = None
    driver_row_id = ride.get("driver_id")
    if driver_row_id:
        try:
            driver = await _deps.db_supabase.get_driver_by_id(driver_row_id)
            driver_user_id = driver.get("user_id") if driver else None
        except Exception as exc:
            logger.error(
                f"[TIP] Could not resolve driver user_id for ride {ride_id}: {exc}",
                exc_info=True,
            )

    if driver_user_id:
        rider = await _deps.db_supabase.get_user_by_id(ride["rider_id"]) or {}
        # PIPEDA (C5): first name only to the driver (WS); never the surname.
        rider_name = first_name_only(rider, "Your rider")
        payload = {
            "type": "tip_received",
            "ride_id": str(ride_id),
            "amount": _money_str(tip_amount),
            "new_total": _money_str(new_tip),
            "rider_name": rider_name,
        }
        try:
            await _deps.manager.send_personal_message(payload, f"driver_{driver_user_id}")
        except Exception as exc:
            logger.warning(f"[TIP] WS notify driver {driver_user_id} failed: {exc}")
        _push_in_background(
            driver_user_id,
            "You got a tip! 💸",
            # PIPEDA (C5): no rider name in the push body (cleartext to Google).
            f"You received a ${tip_amount:.2f} tip",
            data={
                "type": "tip_received",
                "ride_id": str(ride_id),
                "amount": f"{tip_amount:.2f}",
            },
            _ctx=f"[TIP] driver {driver_user_id}",
        )

    return {"success": True, "tip_amount": _money_str(new_tip)}


class ProcessPaymentRequest(BaseModel):
    tip_amount: Decimal = Field(default=Decimal("0"), ge=0, le=500)
    # In-app "Change Card" escape: when set, charge THIS card (fresh charge on
    # a card the rider picked after a decline / no-card failure) instead of the
    # booking-time card or hold. Card rides only; ignored for wallet/corporate.
    payment_method_id: Optional[str] = None


def _record_settlement_metrics(payment_method: str, result, duration_ms: float) -> None:
    """KPI: spinr_payment_settlement_total{method,outcome} + duration histogram.

    Outcome mapping: already_paid is split out from success because no money
    moved (idempotent replay) — counting it as success would mask retry storms
    behind a healthy-looking settlement rate.
    """
    method = {"wallet": "wallet", "company_allowance": "corporate"}.get(payment_method, "card")
    outcome = "already_paid" if result.already_paid else ("success" if result.success else "failed")
    _metric_inc("spinr_payment_settlement_total", {"method": method, "outcome": outcome})
    _metric_observe("spinr_payment_settlement_duration_ms", duration_ms, {"method": method})


def _fire_purchase_conversion(ride: dict, user: dict, charged_amount) -> None:
    """Queue the Meta Purchase/FirstRide send. Never raises into settlement.

    A conversion-tracking failure must not surface to a rider who has just
    been charged, and must not roll back a settled payment, so this is spawned
    rather than awaited and every error is contained here.

    A $0 charge still fires. A fully-subsidised ride (100% promo) is exactly
    the acquisition the promo_code property exists to measure — suppressing it
    would hide the most expensive acquisitions from the analyst.
    """
    try:
        from ...services import meta_conversions_service as _meta
    except ImportError:
        try:
            from services import meta_conversions_service as _meta  # type: ignore
        except ImportError:
            logger.error("meta: conversions service unavailable — skipping Purchase", exc_info=True)
            return
    try:
        _deps.spawn(_meta.send_ride_purchase(ride, user, charged_amount))
    except Exception:
        logger.error("meta: failed to queue Purchase for ride %s", ride.get("id"), exc_info=True)


@router.post("/{ride_id}/process-payment")
@payment_action_limit
async def process_payment(
    ride_id: str,
    req: ProcessPaymentRequest,
    request: Request = None,
    current_user: dict = Depends(get_current_user),
):
    """Process payment for completed ride. Charges rider's card for fare + tip."""
    tip_amount = req.tip_amount

    ride = await _deps.db_supabase.get_ride(ride_id)
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    if ride.get("rider_id") != current_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized")

    _ride_status = ride.get("status", "")
    if _ride_status != RideStatus.COMPLETED:
        raise HTTPException(
            status_code=409,
            detail=f"Ride is in status '{_ride_status}'; payment requires completed state.",
        )

    def _charged(r: dict) -> str:
        _g = r.get("grand_total")
        if _g is None:
            _g = r.get("total_fare", 0)
        return _money_str(_d(_g) + _d(r.get("tip_amount", 0) or 0))

    _pstatus = ride.get("payment_status")
    _pmethod = (ride.get("payment_method") or "card").lower()

    if _pstatus == "paid":
        logger.info(f"[PAYMENT] Ride {ride_id} already paid — skipping duplicate charge")
        return {"success": True, "charged_amount": _charged(ride), "already_paid": True}

    # 'processing' for a CARD/corporate ride may mean the charge was CAPTURED
    # with the DB write lost (settle_card's captured-but-unconfirmed path), so
    # reporting already-paid avoids a double charge; the reconcile/retry loop
    # reconciles the truth. WALLET 'processing' is different: settlement is a
    # single atomic RPC (wallet_pay_for_ride debits AND marks paid in one txn,
    # migration 50/107), so a wallet ride still at 'processing' was provably
    # NEVER debited — it's a crashed/timed-out settle that must be RE-DRIVEN,
    # not reported as paid. The RPC is idempotent (107: no-op if already paid),
    # so re-driving cannot double-charge even under a concurrent retry.
    if _pstatus == "processing" and _pmethod != "wallet":
        logger.info(f"[PAYMENT] Ride {ride_id} processing ({_pmethod}) — skipping duplicate charge")
        return {"success": True, "charged_amount": _charged(ride), "already_paid": True}

    # An admin has emailed (or is creating) a payable Stripe invoice for this
    # ride — collection has moved to the hosted invoice (settled by the
    # invoice.paid webhook). Charging in-app while ANY invoice claim is on the
    # row would collect a second time, and the later invoice.paid would see the
    # ride already paid and skip (no refund of the extra). Block on any non-null
    # value: a finalized invoice (in_*) and a 'pending:' creation claim alike.
    # We never unblock by age here — a stuck claim is recovered admin-side (which
    # creates invoices crash-safely), not by silently re-opening in-app charging.
    if ride.get("stripe_invoice_id"):
        # Structured code so the rider app shows the "pay via emailed invoice"
        # instruction instead of the generic Change Card/Support alert (which would
        # just loop back into this same guard on every retry).
        raise HTTPException(
            status_code=409,
            detail={
                "code": "invoice_issued",
                "message": "An invoice has been emailed for this ride. Please pay using the link in your email.",
            },
        )

    # Validate the tip BEFORE the atomic claim — raising after the claim would
    # leave payment_status stuck at 'processing' with no charge ever attempted.
    if tip_amount < 0:
        raise HTTPException(status_code=400, detail="Tip amount cannot be negative")
    if tip_amount > 500:
        raise HTTPException(status_code=400, detail="Tip amount exceeds maximum ($500)")

    # Atomic claim. 'pending' is the normal first payment; 'failed' lets a retry
    # after a decline re-drive; for WALLET we also re-claim 'processing' so a
    # crashed/stuck settlement is recovered on the rider's next attempt (the
    # idempotent RPC is the double-charge guard). We do NOT gate this on
    # updated_at — /rate bumps updated_at immediately before /process-payment,
    # so an age filter never fires and the ride stays stuck forever.
    _wallet_redrive_lock_key: str | None = None
    _redis_del = None
    _claim_states = ["pending", "failed"]
    if _pmethod == "wallet" and _pstatus == "processing":
        # Recovery re-drive of a stuck wallet 'processing' ride.
        #
        # DO NOT add 'processing' to _claim_states unconditionally — that makes
        # the claim non-exclusive. A double-tap on a first payment (ride at
        # 'pending') would have Call B see 'processing' after Call A commits and
        # still match the WHERE clause, so both calls would enter settlement.
        #
        # Instead: gate the 'processing' claim state behind a Redis NX lock so
        # exactly one re-drive holds the gate. Only after acquiring the lock is
        # 'processing' added to _claim_states, making the DB update a no-op
        # marker (the row is already 'processing') rather than an exclusive
        # transition. Subsequent concurrent re-drives get 409 before the DB.
        try:
            from ...utils.redis_client import redis_delete as _redis_del
            from ...utils.redis_client import redis_set_nx as _redis_nx
        except ImportError:
            from utils.redis_client import redis_delete as _redis_del  # type: ignore
            from utils.redis_client import redis_set_nx as _redis_nx  # type: ignore
        _wallet_redrive_lock_key = f"spinr:wallet_settle:{ride_id}"
        try:
            _wallet_redrive_acquired = await _redis_nx(_wallet_redrive_lock_key, "1", 30)
        except Exception as _lock_err:
            # redis_set_nx now raises on a real (Redis-configured-but-
            # unavailable) error instead of silently falling back per-replica
            # (2026-08-11 P1 fix). Unlike the background-loop throttle locks,
            # this one IS the exclusivity guard for a concurrent re-drive of
            # a stuck 'processing' wallet settlement — fail CLOSED (503, ask
            # the client to retry) rather than silently proceeding as if
            # exclusivity didn't matter on a money path.
            logger.error(f"[PAYMENT] wallet re-drive lock unavailable for ride {ride_id}: {_lock_err}")
            raise HTTPException(
                status_code=503,
                detail="Payment retry temporarily unavailable — please try again in a moment.",
            ) from _lock_err
        if not _wallet_redrive_acquired:
            raise HTTPException(
                status_code=409,
                detail="Payment retry already in progress. Please try again in a moment.",
            )
        _claim_states.append("processing")  # only added after the lock is held
    guard_row = await _deps.db_supabase.update_one(
        "rides",
        # stripe_invoice_id=NULL is asserted atomically (mirrors admin send-invoice
        # asserting payment_status NOT IN processing/paid/...): if an admin claimed
        # the ride for an invoice between our pre-read invoice-guard above and this
        # claim, 0 rows match and the rider is not charged in-app alongside it.
        {"id": ride_id, "payment_status": {"$in": _claim_states}, "stripe_invoice_id": None},
        {
            "payment_status": "processing",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    if guard_row is not None and _pstatus == "processing":
        logger.warning(f"[PAYMENT] Re-driving stuck wallet 'processing' ride {ride_id} for re-settlement")

    if guard_row is None:
        # Couldn't claim. Re-read the real state — only report already-paid when
        # the ride is genuinely paid (or a captured-card 'processing'); a wallet
        # state we couldn't claim returns a retryable 409 rather than a false
        # "Paid".
        fresh = await _deps.db_supabase.get_ride(ride_id) or ride
        if fresh.get("payment_status") == "paid":
            return {"success": True, "already_paid": True, "charged_amount": _charged(fresh)}
        if fresh.get("payment_status") == "processing" and _pmethod != "wallet":
            return {"success": True, "already_paid": True, "charged_amount": _charged(fresh)}
        raise HTTPException(status_code=409, detail="Payment is processing; please retry in a moment.")

    def _q(v) -> Decimal:
        return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Explicit None check, not truthiness: a legitimate $0 grand_total (comp /
    # fully-covered ride) must charge $0, not fall through to a non-zero
    # total_fare and overcharge. Fall back to total_fare only when grand_total
    # was never written (legacy rides predating the column).
    _grand = ride.get("grand_total")
    if _grand is None:
        _grand = ride.get("total_fare", 0)
    tip_rounded = _q(tip_amount)  # canonical 2dp value passed to all settle fns
    total_charge = _q(_grand) + tip_rounded
    payment_method = (ride.get("payment_method") or "card").lower()

    # _tip_db_update carries only the fare_breakdown_snapshot (cosmetic display
    # update, written best-effort after settlement). tip_amount and
    # driver_earnings are written atomically:
    #   - wallet:    inside wallet_pay_for_ride RPC (migration 110)
    #   - card/corp: inside settle_card / settle_corporate via _tip_ride_update
    # The in-memory ride dict is still updated so the receipt email sees the
    # correct totals without a DB re-fetch.
    _tip_db_update: dict = {}
    if tip_rounded > 0:
        tip_d = tip_rounded
        existing_tip = _d(ride.get("tip_amount") or 0)
        tip_delta = tip_d - existing_tip
        snapshot = ride.get("fare_breakdown_snapshot")
        if snapshot and isinstance(snapshot, dict) and snapshot.get("lines") is not None:
            updated_lines = [ln for ln in snapshot["lines"] if ln.get("type") != "tip"]
            updated_lines.append({"label": "Tip", "amount": _f(tip_d), "type": "tip"})
            snapshot["lines"] = updated_lines
            snapshot["grand_total"] = _sum_fare_breakdown(updated_lines)
            _tip_db_update["fare_breakdown_snapshot"] = snapshot
        ride["tip_amount"] = _f(tip_d)
        if tip_delta > 0:
            ride["driver_earnings"] = _f(_round(_d(ride.get("driver_earnings") or 0) + tip_delta))

    _snap = ride.get("fare_breakdown_snapshot")
    _snap_lines = (_snap.get("lines") if isinstance(_snap, dict) else None) if _snap else None

    _settle_started = _time_mod.monotonic()
    if payment_method == "wallet":
        result = await settle_wallet(
            ride,
            ride_id,
            current_user["id"],
            total_charge,
            tip_rounded,
            fare_breakdown=_snap_lines or _build_fare_breakdown(ride),
        )
    elif payment_method == "company_allowance":
        result = await settle_corporate(ride, ride_id, total_charge, tip_rounded)
    else:
        result = await settle_card(
            ride,
            ride_id,
            current_user["id"],
            total_charge,
            tip_rounded,
            payment_method_id_override=req.payment_method_id,
        )

    _record_settlement_metrics(payment_method, result, (_time_mod.monotonic() - _settle_started) * 1000.0)

    if not result.success:
        detail = result.error or "Payment failed"
        if result.error_code:
            detail = {"code": result.error_code, "message": result.error}
            if result.decline_code:
                detail["decline_code"] = result.decline_code
            if result.extra:
                detail.update(result.extra)
        raise HTTPException(status_code=result.status_code, detail=detail)

    # Release the wallet re-drive lock now that settlement is complete so any
    # subsequent legitimate retry sees the paid status immediately rather than
    # waiting 30 s for the TTL to expire.
    if _wallet_redrive_lock_key:
        try:
            await _redis_del(_wallet_redrive_lock_key)
        except Exception as _lock_err:
            logger.debug("wallet redrive lock release failed (TTL will expire): %s", _lock_err)

    # Skip tip persistence and receipt on already_paid: no money moved, so we
    # must not mutate tip_amount/driver_earnings in the DB or send a duplicate
    # receipt. The ledger write was also skipped by settle_wallet on this path.
    email_sent = False
    if not result.already_paid:
        if _tip_db_update:
            try:
                await _deps.db_supabase.update_ride(ride_id, _tip_db_update)
            except Exception as _snap_err:
                logger.error(
                    "[PAYMENT] fare_breakdown_snapshot write failed for ride %s — "
                    "payment succeeded, snapshot will be stale: %s",
                    ride_id,
                    _snap_err,
                )
        # Receipt email backgrounded off the payment path (<1s settlement
        # SLA): send_ride_receipt logs and swallows its own failures, and no
        # client surface reads the delivery outcome. email_sent now means
        # "queued" (field kept for API-shape compatibility; the explicit
        # resend endpoint still awaits delivery and reports it honestly).
        _deps.spawn(send_ride_receipt(ride, current_user["id"], tip_rounded))
        email_sent = True

        # Meta Purchase (+ FirstRide on the rider's first). Fired here rather
        # than at ride completion because this is the point money actually
        # moved — a completed ride whose settlement failed is not a purchase.
        # Guarded by `not result.already_paid`, so an idempotent replay of a
        # ride that was already settled does not emit a second conversion.
        #
        # result.charged_amount is the authoritative figure: post-promo, tip
        # included, as actually charged. Reading a fare column instead would
        # let the reported value drift from the real charge.
        _fire_purchase_conversion(ride, current_user, result.charged_amount)
    return {
        "success": True,
        "charged_amount": _money_str(result.charged_amount),
        "email_sent": email_sent,
    }
