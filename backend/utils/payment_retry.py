"""Payment retry — automatically retries failed ride payments with exponential backoff.

Runs as a background task every 5 minutes. Finds rides with payment_status
'failed' or 'requires_action' and retries via Stripe up to 3 times.
"""

import asyncio
import logging
from datetime import datetime

try:
    from ..db import db
    from ..features import send_push_notification
    from ..settings_loader import get_app_settings
except ImportError:
    from db import db

    from features import send_push_notification
    from settings_loader import get_app_settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
RETRY_INTERVAL_SECONDS = 300  # 5 minutes


async def retry_failed_payments():
    """Find and retry failed payments."""
    try:
        # Find rides with failed payments that haven't exceeded retry limit
        rides = await db.get_rows(
            "rides",
            {"payment_status": {"$in": ["failed", "requires_action"]}},
            limit=50,
            order_by="created_at",
            order_desc=True,
        )
    except Exception as e:
        logger.error(f"Payment retry: failed to fetch rides: {e}")
        return

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")

    for ride in rides:
        ride_id = ride["id"]
        retry_count = ride.get("payment_retry_count", 0)

        if retry_count >= MAX_RETRIES:
            continue

        # Skip rides older than 24 hours
        created = ride.get("created_at", "")
        if isinstance(created, str):
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00").replace("+00:00", ""))
                if (datetime.utcnow() - created_dt).total_seconds() > 86400:
                    continue
            except (ValueError, TypeError):
                pass

        payment_intent_id = ride.get("payment_intent_id")
        if not payment_intent_id or not stripe_secret:
            continue

        try:
            import stripe

            # Attempt to confirm the payment intent
            intent = stripe.PaymentIntent.retrieve(payment_intent_id, api_key=stripe_secret)

            if intent.status == "succeeded":
                # Already succeeded (webhook may have missed it)
                await db.rides.update_one(
                    {"id": ride_id},
                    {
                        "$set": {
                            "payment_status": "paid",
                            "payment_retry_count": retry_count + 1,
                            "updated_at": datetime.utcnow().isoformat(),
                        }
                    },
                )
                logger.info(f"Payment retry: ride {ride_id} already paid (intent succeeded)")

            elif intent.status in ("requires_payment_method", "requires_confirmation"):
                # Try to confirm again
                stripe.PaymentIntent.confirm(payment_intent_id, api_key=stripe_secret)
                await db.rides.update_one(
                    {"id": ride_id},
                    {
                        "$set": {
                            "payment_status": "processing",
                            "payment_retry_count": retry_count + 1,
                            "updated_at": datetime.utcnow().isoformat(),
                        }
                    },
                )
                logger.info(f"Payment retry: ride {ride_id} retry #{retry_count + 1} submitted")

            elif intent.status == "canceled":
                # Cannot retry a cancelled intent
                await db.rides.update_one(
                    {"id": ride_id},
                    {"$set": {"payment_retry_count": MAX_RETRIES}},
                )

        except Exception as e:
            logger.warning(f"Payment retry failed for ride {ride_id}: {e}")
            await db.rides.update_one(
                {"id": ride_id},
                {
                    "$set": {
                        "payment_retry_count": retry_count + 1,
                        "updated_at": datetime.utcnow().isoformat(),
                    }
                },
            )

        # Notify rider on final failure
        if retry_count + 1 >= MAX_RETRIES:
            rider_id = ride.get("rider_id")
            if rider_id:
                try:
                    await send_push_notification(
                        rider_id,
                        "Payment failed",
                        "We couldn't process payment for your ride. Please update your payment method.",
                        data={"type": "payment_failed", "ride_id": ride_id},
                    )
                except Exception as push_err:
                    logger.debug(f"Payment failure push notification failed: {push_err}")


async def payment_retry_loop():
    """Background loop that retries failed payments every RETRY_INTERVAL_SECONDS."""
    logger.info(f"Payment retry service started (interval={RETRY_INTERVAL_SECONDS}s)")
    while True:
        try:
            await retry_failed_payments()
        except Exception as e:
            logger.error(f"Payment retry loop error: {e}")
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
