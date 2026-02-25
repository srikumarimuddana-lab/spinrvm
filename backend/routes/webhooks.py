from fastapi import APIRouter, Request, HTTPException
try:
    from ..db import db
    from ..features import send_push_notification
except ImportError:
    from db import db
    from features import send_push_notification
import logging
from datetime import datetime

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
    sig_header = request.headers.get('stripe-signature')

    settings = await db.settings.find_one({'id': 'app_settings'})
    webhook_secret = settings.get('stripe_webhook_secret', '') if settings else ''
    stripe_secret = settings.get('stripe_secret_key', '') if settings else ''

    if not webhook_secret:
        logger.warning('stripe_webhook_secret not set in admin settings — webhook verification disabled')
        return {'received': True, 'verified': False}

    if not stripe_secret:
        logger.error('Stripe secret key not configured in app settings')
        raise HTTPException(status_code=500, detail='Stripe not configured')

    try:
        import stripe
        stripe.api_key = stripe_secret
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except ValueError:
        raise HTTPException(status_code=400, detail='Invalid payload')
    except Exception as e:
        logger.error(f'Stripe webhook signature verification failed: {e}')
        raise HTTPException(status_code=400, detail='Invalid signature')

    event_type = event.get('type', '')
    data_object = event.get('data', {}).get('object', {})

    if event_type == 'payment_intent.succeeded':
        ride_id = data_object.get('metadata', {}).get('ride_id')
        user_id = data_object.get('metadata', {}).get('user_id')
        payment_intent_id = data_object.get('id')

        if ride_id:
            await db.rides.update_one(
                {'id': ride_id},
                {'$set': {
                    'payment_status': 'paid',
                    'payment_intent_id': payment_intent_id,
                    'paid_at': datetime.utcnow()
                }}
            )
            logger.info(f'Payment confirmed via webhook for ride {ride_id}')

        if user_id:
            await send_push_notification(
                user_id,
                'Payment Confirmed ✅',
                'Your payment has been processed successfully.',
                {'type': 'payment_confirmed', 'ride_id': ride_id or ''}
            )

    elif event_type == 'payment_intent.payment_failed':
        ride_id = data_object.get('metadata', {}).get('ride_id')
        user_id = data_object.get('metadata', {}).get('user_id')
        payment_intent_id = data_object.get('id')
        failure_message = data_object.get('last_payment_error', {}).get('message', 'Payment failed')

        if ride_id:
            await db.rides.update_one(
                {'id': ride_id},
                {'$set': {
                    'payment_status': 'failed',
                    'payment_intent_id': payment_intent_id,
                    'payment_failure_reason': failure_message
                }}
            )
            logger.warning(f'Payment failed for ride {ride_id}: {failure_message}')

        if user_id:
            await send_push_notification(
                user_id,
                'Payment Failed ❌',
                f'Your payment could not be processed: {failure_message}',
                {'type': 'payment_failed', 'ride_id': ride_id or ''}
            )

    else:
        logger.info(f'Unhandled Stripe event type: {event_type}')

    return {'received': True}
