from fastapi import APIRouter, HTTPException, Request

try:
    from .. import db_supabase
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
except ImportError:
    import db_supabase
    from features import send_push_notification
    from settings_loader import get_app_settings
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
# IMPORTANT: This router does NOT have a /api/ prefix in the original server.py
# In server.py: app.post("/webhooks/stripe")
# So we should probably mount it at root or handle it carefully.
# However, for consistency with other modules, let's define the router here.
# The user will need to mount it appropriately in server.py.
api_router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@api_router.post("/stripe")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events for server-side payment confirmation."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    settings = await get_app_settings()
    webhook_secret = settings.get("stripe_webhook_secret", "")
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

    try:
        import stripe

        stripe.api_key = stripe_secret
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload") from None
    except Exception as e:
        logger.error(f"Stripe webhook signature verification failed: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature") from e

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
        ride_id = data_object.get("metadata", {}).get("ride_id")
        user_id = data_object.get("metadata", {}).get("user_id")
        payment_intent_id = data_object.get("id")

        if ride_id:
            await db_supabase.update_ride(ride_id, { "payment_status": "paid", "payment_intent_id": payment_intent_id, "paid_at": datetime.utcnow(), })
            logger.info(f"Payment confirmed via webhook for ride {ride_id}")

        if user_id:
            await send_push_notification(
                user_id,
                "Payment Confirmed ✅",
                "Your payment has been processed successfully.",
                {"type": "payment_confirmed", "ride_id": ride_id or ""},
            )

    elif event_type == "payment_intent.payment_failed":
        ride_id = data_object.get("metadata", {}).get("ride_id")
        user_id = data_object.get("metadata", {}).get("user_id")
        payment_intent_id = data_object.get("id")
        failure_message = data_object.get("last_payment_error", {}).get("message", "Payment failed")

        if ride_id:
            await db_supabase.update_ride(ride_id, { "payment_status": "failed", "payment_intent_id": payment_intent_id, "payment_failure_reason": failure_message, })
            logger.warning(f"Payment failed for ride {ride_id}: {failure_message}")

        if user_id:
            await send_push_notification(
                user_id,
                "Payment Failed ❌",
                f"Your payment could not be processed: {failure_message}",
                {"type": "payment_failed", "ride_id": ride_id or ""},
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
            logger.info(
                f"[WEBHOOK] Spinr Pass activated via checkout.session.completed: "
                f"subscription={subscription_id} driver={driver_id} plan={plan_id}"
            )
        else:
            logger.info(
                f"[WEBHOOK] checkout.session.completed but payment not yet paid: "
                f"status={data_object.get('payment_status')} subscription={subscription_id}"
            )

    else:
        logger.info(f"Unhandled Stripe event type: {event_type}")

    # Success — stamp processed_at. Non-fatal if this fails (we've
    # already finished the side effects, and Stripe won't retry a 2xx).
    await mark_stripe_event_processed(event_id)

    return {"received": True, "event_id": event_id}
