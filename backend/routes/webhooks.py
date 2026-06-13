from decimal import ROUND_HALF_UP, Decimal

from fastapi import APIRouter, HTTPException, Request

try:
    from .. import db_supabase
    from ..db_supabase import claim_stripe_event, mark_stripe_event_processed
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
    from ..utils.money import cents_to_dollars
except ImportError:
    import db_supabase
    from db_supabase import claim_stripe_event, mark_stripe_event_processed
    from features import send_push_notification
    from settings_loader import get_app_settings
    from utils.money import cents_to_dollars
import logging
from datetime import datetime, timezone

_TWO_PLACES = Decimal("0.01")

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# B-P2-2: Explicit allowlist of Stripe event types we process. Any event type
# NOT in this set is logged and acknowledged (200) without processing.
# Return 200 (not 400) for unknown events — 400 causes Stripe to retry for
# 3 days and creates noise; we want Stripe to stop re-sending them.
_STRIPE_HANDLED_EVENTS = frozenset(
    {
        "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "checkout.session.completed",
        "charge.refunded",
        "charge.dispute.created",
        "charge.dispute.closed",
        "customer.subscription.deleted",
        # Connect Express KYC mirror — fired whenever the driver progresses
        # through Stripe's hosted onboarding (uploads ID, accepts ToS,
        # links bank), or whenever Stripe's verification team updates
        # the account's status. Drives the Payouts tab's Tax & Identity
        # section in the admin slideout.
        "account.updated",
        # Connected-account bank settlement of a driver payout. Lets us
        # reconcile the payouts row to the true outcome — an instant payout
        # marked "completed" synchronously can still be rejected by the bank
        # days later (closed account, etc.); payout.failed is how we learn.
        "payout.paid",
        "payout.failed",
    }
)
# Public alias exported for tests
ALLOWED_STRIPE_EVENTS = _STRIPE_HANDLED_EVENTS


@api_router.post("/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for server-side payment confirmation."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    settings = await get_app_settings()
    webhook_secret = settings.get("stripe_webhook_secret", "")
    connect_webhook_secret = settings.get("stripe_connect_webhook_secret", "")
    stripe_secret = settings.get("stripe_secret_key", "")

    if not webhook_secret:
        logger.error("stripe_webhook_secret not set — rejecting unverified webhook")
        raise HTTPException(
            status_code=500,
            detail="Webhook signature verification not configured",
        )

    if not stripe_secret:
        logger.error("Stripe secret key not configured in app settings")
        raise HTTPException(status_code=500, detail="Stripe not configured")

    import stripe

    # Both the platform endpoint and the Connected-accounts endpoint POST to
    # this same URL, but Stripe signs each with that endpoint's own whsec_.
    # We can't tell them apart before verifying, so try the platform secret
    # first, then the connect secret (if configured). A SignatureVerification
    # failure on one is expected for events from the other — only reject when
    # BOTH fail. ValueError = malformed payload, reject immediately.
    candidate_secrets = [webhook_secret]
    if connect_webhook_secret:
        candidate_secrets.append(connect_webhook_secret)

    event = None
    last_sig_error: Exception | None = None
    for secret in candidate_secrets:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, secret)
            break
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid payload") from None
        except stripe.error.SignatureVerificationError as e:
            last_sig_error = e
            continue
        except Exception as e:
            last_sig_error = e
            continue

    if event is None:
        logger.error(f"Stripe webhook signature verification failed: {last_sig_error}")
        raise HTTPException(status_code=400, detail="Invalid signature") from last_sig_error

    event_id = event.get("id", "")
    event_type = event.get("type", "")
    data_object = event.get("data", {}).get("object", {})

    if not event_id:
        # Should never happen for real Stripe events, but guard anyway —
        # we cannot dedup without a stable key.
        logger.error("Stripe webhook event missing id — cannot dedup")
        raise HTTPException(status_code=400, detail="Missing event id")

    # ── Idempotency gate ─────────────────────────────────────────────
    # Stripe retries every event (network blip, >20s handler, any non-2xx)
    # so we MUST treat a replay of the same event.id as a no-op. The
    # stripe_events table (migration 22) has event_id as PRIMARY KEY;
    # claim_stripe_event returns False on a unique-violation replay.
    # Stripe objects are dict subclasses but nested values (e.g. data.object)
    # remain as StripeObject instances. to_dict_recursive() flattens the whole
    # tree into plain dicts so it can be stored in jsonb without surprises.
    try:
        event_payload = event.to_dict_recursive()  # type: ignore[attr-defined]
    except AttributeError:
        event_payload = dict(event)

    try:
        is_new = await claim_stripe_event(event_id, event_type, event_payload)
    except Exception as e:
        logger.error(f"Failed to persist stripe event {event_id}: {e}")
        # Let Stripe retry — 5xx keeps the event in their queue.
        raise HTTPException(status_code=500, detail="Event persistence failed") from e

    if not is_new:
        return {"received": True, "duplicate": True, "event_id": event_id}

    # ── Dispatch ─────────────────────────────────────────────────────
    # Any exception raised below propagates as 5xx, leaving processed_at
    # NULL so either (a) Stripe retries, or (b) the nightly reconciliation
    # job replays the event from the persisted payload.
    if event_type == "payment_intent.succeeded":
        meta = data_object.get("metadata") or {}

        if meta.get("scope") == "corporate_topup":
            try:
                from ..services.corporate_wallet_service import apply_topup  # type: ignore
            except ImportError:
                from services.corporate_wallet_service import apply_topup  # type: ignore

            amount_cents = data_object.get("amount_received") or data_object.get("amount", 0)
            # Decimal-safe cents→dollars (2-dp HALF_UP). Float division
            # ``cents / 100`` drifts for arbitrary cent values; quantize
            # first, then hand the float to apply_topup (Postgres NUMERIC
            # rounds to column scale).
            amount_cad = cents_to_dollars(amount_cents)
            await apply_topup(
                wallet_id=meta["wallet_id"],
                amount=amount_cad,
                stripe_payment_intent_id=data_object["id"],
                actor_user_id=meta.get("initiated_by"),
                notes=f"Stripe top-up via {event_id}",
            )
            await mark_stripe_event_processed(event_id)
            return {"received": True, "scope": "corporate_topup", "event_id": event_id}

        if meta.get("scope") == "wallet_topup":
            try:
                from ..db_supabase import wallet_increment_balance  # type: ignore
            except ImportError:
                from db_supabase import wallet_increment_balance  # type: ignore

            wallet_id = meta.get("wallet_id")
            user_id = meta.get("user_id")
            amount_cad_str = meta.get("amount_cad", "0")
            amount = Decimal(amount_cad_str).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            new_balance = await wallet_increment_balance(wallet_id, amount)

            try:
                from ..db import db  # type: ignore
            except ImportError:
                from db import db  # type: ignore

            await db.insert_one(
                "wallet_transactions",
                {
                    "id": str(__import__("uuid").uuid4()),
                    "wallet_id": wallet_id,
                    "user_id": user_id,
                    "type": "top_up",
                    "amount": str(amount),
                    "balance_after": str(new_balance),
                    "reference_id": data_object["id"],
                    "description": f"Wallet top-up ${amount}",
                    "metadata": {"stripe_payment_intent_id": data_object["id"]},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

            logger.info(
                f"Wallet top-up confirmed: wallet={wallet_id} amount={amount} new_balance={new_balance}",
                extra={"domain": "payments", "event_id": event_id},
            )
            await mark_stripe_event_processed(event_id)

            if user_id:
                try:
                    await send_push_notification(
                        user_id,
                        title="Wallet Topped Up",
                        body=f"${amount} has been added to your wallet.",
                        data={"type": "wallet_topup", "amount": str(amount)},
                    )
                except Exception:
                    logger.warning("Push notification failed for wallet_topup", exc_info=True)

            return {"received": True, "scope": "wallet_topup", "event_id": event_id}

        ride_id = meta.get("ride_id")
        user_id = meta.get("user_id")
        payment_intent_id = data_object.get("id")

        if ride_id:
            updated = await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "paid",
                    "payment_intent_id": payment_intent_id,
                    "paid_at": datetime.now(timezone.utc),
                },
            )
            if updated is None:
                logger.error(
                    f"Webhook payment_intent.succeeded: ride {ride_id} not found or 0 rows "
                    f"updated — payment {payment_intent_id} unlinked",
                    extra={
                        "domain": "payments",
                        "event_id": event_id,
                        "ride_id": ride_id,
                    },
                )
                raise HTTPException(status_code=500, detail="Ride update failed — Stripe will retry")
            else:
                logger.info(f"Payment confirmed via webhook for ride {ride_id}")

        if user_id:
            # Wrap push notification so a Firebase outage does not cause Stripe
            # to retry the webhook for days (which would re-process the payment event).
            try:
                await send_push_notification(
                    user_id,
                    "Payment Confirmed ✅",
                    "Your payment has been processed successfully.",
                    {"type": "payment_confirmed", "ride_id": ride_id or ""},
                )
            except Exception as _push_err:
                logger.error(f"Webhook: push notification failed for user {user_id}: {_push_err}")

    elif event_type == "payment_intent.payment_failed":
        ride_id = data_object.get("metadata", {}).get("ride_id")
        user_id = data_object.get("metadata", {}).get("user_id")
        payment_intent_id = data_object.get("id")
        failure_message = data_object.get("last_payment_error", {}).get("message", "Payment failed")

        if ride_id:
            updated = await db_supabase.update_ride(
                ride_id,
                {
                    "payment_status": "failed",
                    "payment_intent_id": payment_intent_id,
                    "payment_failure_reason": failure_message,
                },
            )
            if updated is None:
                logger.error(
                    f"Webhook payment_intent.payment_failed: ride {ride_id} not found or 0 rows "
                    f"updated — payment failure {payment_intent_id} unlinked",
                    extra={
                        "domain": "payments",
                        "event_id": event_id,
                        "ride_id": ride_id,
                    },
                )
                raise HTTPException(status_code=500, detail="Ride update failed — Stripe will retry")
            logger.warning(f"Payment failed for ride {ride_id}: {failure_message}")

        if user_id:
            try:
                await send_push_notification(
                    user_id,
                    "Payment Failed ❌",
                    f"Your payment could not be processed: {failure_message}",
                    {"type": "payment_failed", "ride_id": ride_id or ""},
                )
            except Exception as _push_err:
                logger.error(f"Webhook: push notification failed for user {user_id}: {_push_err}")

        # Notify the driver so they know the rider's payment failed (13-10)
        if ride_id:
            try:
                ride_row = await db_supabase.get_ride(ride_id)
                driver_user_id = None
                if ride_row:
                    driver_id = ride_row.get("driver_id")
                    if driver_id:
                        driver_rows = await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1)
                        if driver_rows:
                            driver_user_id = driver_rows[0].get("user_id")
            except Exception as lookup_err:
                logger.error(f"Driver payment-failed lookup error: {lookup_err}", exc_info=True)
                driver_user_id = None
            if driver_user_id:
                try:
                    await send_push_notification(
                        driver_user_id,
                        "Rider payment failed",
                        "The payment for your last ride could not be collected.",
                        {
                            "type": "payment_failed",
                            "ride_id": ride_id,
                            "deeplink": "/driver/earnings",
                        },
                    )
                except Exception:
                    logger.warning(
                        "Push notification failed for payment_failed event; continuing",
                        exc_info=True,
                    )

    elif event_type == "checkout.session.completed":
        # ── Spinr Pass subscription payment confirmed ──────────
        # The /drivers/subscription/subscribe endpoint creates a pending
        # subscription row and a Stripe Checkout Session with the
        # subscription_id in the metadata. This webhook fires after the
        # driver completes payment — we activate the subscription here.
        metadata = data_object.get("metadata", {})
        subscription_id = metadata.get("subscription_id")
        plan_id = metadata.get("plan_id")
        driver_id = metadata.get("driver_id")

        if subscription_id and data_object.get("payment_status") == "paid":
            try:
                from ..routes.drivers import _activate_subscription  # type: ignore
            except ImportError:
                from routes.drivers import _activate_subscription  # type: ignore

            await _activate_subscription(subscription_id, plan_id)

            # Recurring plans (mode="subscription") return a Stripe
            # Subscription id on the session — persist it so the renewal /
            # dunning / cancellation webhooks can match this row.
            stripe_subscription_id = data_object.get("subscription")
            if stripe_subscription_id:
                await db_supabase.update_one(
                    "driver_subscriptions",
                    {"id": subscription_id},
                    {"stripe_subscription_id": stripe_subscription_id},
                )

            logger.info(
                f"[WEBHOOK] Spinr Pass activated via checkout.session.completed: "
                f"subscription={subscription_id} driver={driver_id} plan={plan_id} "
                f"stripe_sub={stripe_subscription_id}"
            )
        else:
            logger.info(
                f"[WEBHOOK] checkout.session.completed but payment not yet paid: "
                f"status={data_object.get('payment_status')} subscription={subscription_id}"
            )

    elif event_type == "charge.refunded":
        charge = data_object
        payment_intent_id = charge.get("payment_intent")
        if payment_intent_id:
            rides = await db_supabase.get_rows(
                "rides",
                {"payment_intent_id": payment_intent_id},
                limit=1,
            )
            if rides:
                ride = rides[0]
                ride_id = ride["id"]
                refunded_amount = Decimal(str(charge.get("amount_refunded", 0))) / Decimal("100")
                refunded_amount = refunded_amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
                await db_supabase.update_one(
                    "rides",
                    {"id": ride_id},
                    {
                        "payment_status": "refunded",
                        "refund_amount": str(refunded_amount),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                logger.info(
                    f"Stripe refund: ride {ride_id} marked refunded (${refunded_amount:.2f})",
                    extra={
                        "domain": "payments",
                        "ride_id": ride_id,
                        "event_id": event_id,
                    },
                )
                rider_id = ride.get("rider_id")
                if rider_id:
                    try:
                        await send_push_notification(
                            rider_id,
                            "Refund processed",
                            f"Your refund of ${refunded_amount:.2f} has been processed by your bank.",
                            data={"type": "refund_processed", "ride_id": ride_id},
                        )
                    except Exception as _e:
                        logger.debug(f"Refund push failed: {_e}")
            else:
                logger.warning(
                    f"charge.refunded: no ride found for payment_intent {payment_intent_id}",
                    extra={"domain": "payments", "event_id": event_id},
                )
        else:
            logger.warning(
                "charge.refunded: charge has no payment_intent — skipping ride update",
                extra={"domain": "payments", "event_id": event_id},
            )

    elif event_type == "charge.dispute.created":
        dispute_id_stripe = data_object.get("id", "")
        payment_intent_id = data_object.get("payment_intent") or ""
        dispute_amount_cents = data_object.get("amount", 0)
        dispute_reason = data_object.get("reason", "unknown")
        dispute_status = data_object.get("status", "")

        ride = None
        ride_id = None
        if payment_intent_id:
            rides = await db_supabase.get_rows(
                "rides",
                {"payment_intent_id": payment_intent_id},
                limit=1,
            )
            if rides:
                ride = rides[0]
                ride_id = ride["id"]

        await db_supabase.insert_one(
            "stripe_disputes",
            {
                "id": str(__import__("uuid").uuid4()),
                "stripe_dispute_id": dispute_id_stripe,
                "payment_intent_id": payment_intent_id,
                "ride_id": ride_id,
                "amount_cents": dispute_amount_cents,
                "reason": dispute_reason,
                "status": dispute_status,
                "stripe_event_id": event_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        if ride_id:
            await db_supabase.update_one(
                "rides",
                {"id": ride_id},
                {
                    "payment_status": "disputed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        try:
            try:
                from ..socket_manager import manager  # type: ignore
            except ImportError:
                from socket_manager import manager  # type: ignore

            await manager.broadcast_to_admins(
                {
                    "type": "charge_dispute_created",
                    "ride_id": ride_id,
                    "dispute_reason": dispute_reason,
                    "amount_cents": dispute_amount_cents,
                    "payment_intent_id": payment_intent_id,
                }
            )
        except Exception as ws_err:
            logger.warning("Dispute WS broadcast failed: %s", ws_err)

        logger.error(
            "CHARGEBACK: dispute opened reason=%s amount_cents=%d ride=%s pi=%s",
            dispute_reason,
            dispute_amount_cents,
            ride_id,
            payment_intent_id,
            extra={
                "domain": "payments",
                "event_id": event_id,
                "ride_id": ride_id or "",
            },
        )

    elif event_type == "charge.dispute.closed":
        payment_intent_id = data_object.get("payment_intent") or ""
        dispute_status = data_object.get("status", "")

        existing = await db_supabase.find_one(
            "stripe_disputes",
            {"payment_intent_id": payment_intent_id},
        )
        if existing:
            await db_supabase.update_one(
                "stripe_disputes",
                {"id": existing["id"]},
                {
                    "status": dispute_status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        ride_id = existing.get("ride_id") if existing else None
        if not ride_id and payment_intent_id:
            rides = await db_supabase.get_rows(
                "rides",
                {"payment_intent_id": payment_intent_id},
                limit=1,
            )
            if rides:
                ride_id = rides[0]["id"]

        if ride_id:
            new_payment_status = "paid" if dispute_status == "won" else "dispute_lost"
            await db_supabase.update_one(
                "rides",
                {"id": ride_id},
                {
                    "payment_status": new_payment_status,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )

        logger.info(
            "Dispute closed: status=%s ride=%s pi=%s",
            dispute_status,
            ride_id,
            payment_intent_id,
            extra={"domain": "payments", "event_id": event_id},
        )

    elif event_type == "customer.subscription.deleted":
        subscription = data_object
        stripe_customer_id = subscription.get("customer")
        if stripe_customer_id:
            # Look up the user by their Stripe customer ID, then find the linked driver
            user_row = await db_supabase.find_one("users", {"stripe_customer_id": stripe_customer_id})
            if user_row:
                user_id = user_row["id"]
                driver_row = await db_supabase.find_one("drivers", {"user_id": user_id})
                if driver_row:
                    driver_id = driver_row["id"]
                    # Cancel the active driver_subscriptions row for this driver
                    active_sub = await db_supabase.find_one(
                        "driver_subscriptions",
                        {"driver_id": driver_id, "status": "active"},
                    )
                    if active_sub:
                        await db_supabase.update_one(
                            "driver_subscriptions",
                            {"id": active_sub["id"]},
                            {
                                "status": "cancelled",
                                "cancelled_at": datetime.now(timezone.utc).isoformat(),
                                "updated_at": datetime.now(timezone.utc).isoformat(),
                            },
                        )
                        logger.info(
                            f"Stripe subscription cancelled for driver {driver_id} "
                            f"(subscription row {active_sub['id']})",
                            extra={
                                "domain": "drivers",
                                "driver_id": driver_id,
                                "event_id": event_id,
                            },
                        )
                    else:
                        logger.info(
                            f"customer.subscription.deleted: no active subscription row for driver {driver_id}",
                            extra={
                                "domain": "drivers",
                                "driver_id": driver_id,
                                "event_id": event_id,
                            },
                        )
                    try:
                        await send_push_notification(
                            user_id,
                            "Subscription cancelled",
                            "Your Spinr subscription has been cancelled. Renew to continue accepting rides.",
                            data={
                                "type": "subscription_cancelled",
                                "deeplink": "/driver/subscription",
                            },
                        )
                    except Exception as _e:
                        logger.debug(f"Subscription cancel push failed: {_e}")
                else:
                    logger.warning(
                        f"customer.subscription.deleted: no driver found for user {user_id}",
                        extra={"domain": "drivers", "event_id": event_id},
                    )
            else:
                logger.warning(
                    "customer.subscription.deleted: no user found for stripe_customer_id",
                    extra={"domain": "drivers", "event_id": event_id},
                )
        else:
            logger.warning(
                "customer.subscription.deleted: event has no customer field — skipping",
                extra={"domain": "drivers", "event_id": event_id},
            )

    elif event_type == "account.updated":
        # Stripe Connect Express KYC mirror. Fires whenever the driver
        # progresses through Stripe-hosted onboarding (uploads SIN,
        # accepts ToS, links bank, etc.) or whenever Stripe's verification
        # team flips a status. We persist only the cache columns added
        # by migration 92 — never the SIN itself.
        try:
            from ..services.stripe_kyc_sync import apply_account_update
        except ImportError:
            from services.stripe_kyc_sync import apply_account_update
        await apply_account_update(data_object, event_id=event_id)

    elif event_type in ("payout.paid", "payout.failed"):
        # Connected-account bank settlement of a driver payout. Instant
        # payouts (routes/drivers.py request_instant_payout) do an explicit
        # Payout.create and store its po_ id in payouts.stripe_payout_id, so
        # those reconcile here. Standard payouts only do a Transfer and store
        # a tr_ id — those won't match a Payout event, and Stripe's automatic
        # scheduled payouts of the connected balance also fire here with no
        # tracked row. A no-match is therefore expected: log and ack, never
        # 5xx (a 5xx would make Stripe retry a payout we don't track for days).
        stripe_payout_id = data_object.get("id")
        payout_row = None
        if stripe_payout_id:
            payout_row = await db_supabase.find_one("payouts", {"stripe_payout_id": stripe_payout_id})

        if not payout_row:
            logger.info(
                "payout webhook %s: no tracked payout row for %s "
                "(auto-scheduled connected-account payout or standard transfer) — acking",
                event_type,
                stripe_payout_id,
                extra={"domain": "payments", "event_id": event_id},
            )
        elif event_type == "payout.paid":
            await db_supabase.update_one(
                "payouts",
                {"id": payout_row["id"]},
                {
                    "status": "completed",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "Payout settled to bank: payout_row=%s po=%s",
                payout_row["id"],
                stripe_payout_id,
                extra={"domain": "payments", "event_id": event_id},
            )
        else:  # payout.failed
            failure_message = (
                data_object.get("failure_message") or data_object.get("failure_code") or "Bank payout failed"
            )
            await db_supabase.update_one(
                "payouts",
                {"id": payout_row["id"]},
                {
                    "status": "failed",
                    "failure_reason": str(failure_message)[:500],
                    "requires_manual_review": True,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.error(
                "PAYOUT FAILED at bank: payout_row=%s po=%s reason=%s",
                payout_row["id"],
                stripe_payout_id,
                failure_message,
                extra={
                    "domain": "payments",
                    "event_id": event_id,
                    "driver_id": payout_row.get("driver_id") or "",
                },
            )
            # Notify the driver their money didn't land so they can fix their
            # bank details and retry.
            driver_row = await db_supabase.find_one("drivers", {"id": payout_row.get("driver_id")})
            if driver_row and driver_row.get("user_id"):
                try:
                    await send_push_notification(
                        driver_row["user_id"],
                        "Payout failed",
                        "Your payout could not be deposited. Please check your bank details and try again.",
                        data={"type": "payout_failed", "deeplink": "/driver/earnings"},
                    )
                except Exception:
                    logger.warning(
                        "Push notification failed for payout_failed event; continuing",
                        exc_info=True,
                    )

    else:
        if event_type in _STRIPE_HANDLED_EVENTS:
            logger.error(
                "[WEBHOOK] Event type %r matched allowlist but fell through dispatch — "
                "handler logic gap; check for missing elif branch",
                event_type,
                extra={"domain": "payments", "event_id": event_id},
            )
        else:
            logger.warning(
                "[WEBHOOK] Unhandled Stripe event type %r — not in _STRIPE_HANDLED_EVENTS. "
                "Update Stripe dashboard to send only subscribed events.",
                event_type,
                extra={"domain": "payments", "event_id": event_id},
            )
        # Leave processed_at NULL for unknown/unhandled events so the nightly
        # reconciliation job can replay them if they later become actionable.
        # Return 200 to Stripe so it does not retry indefinitely.
        return {"received": True, "unhandled": True, "event_id": event_id}

    # Success — stamp processed_at. Non-fatal if this fails (we've
    # already finished the side effects, and Stripe won't retry a 2xx).
    await mark_stripe_event_processed(event_id)

    return {"received": True, "event_id": event_id}
