from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
try:
    from ..dependencies import get_current_user
    from ..db import db
except ImportError:
    from dependencies import get_current_user
    from db import db
import uuid
import logging

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/payments", tags=["Payments"])

@api_router.post("/create-intent")
async def create_payment_intent(request: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    """Create a Stripe payment intent"""
    settings = await db.settings.find_one({'id': 'app_settings'})
    stripe_secret = settings.get('stripe_secret_key', '') if settings else ''
    
    if not stripe_secret:
        # Return mock response if Stripe not configured
        return {
            'client_secret': 'mock_secret_' + str(uuid.uuid4()),
            'payment_intent_id': 'pi_mock_' + str(uuid.uuid4()),
            'mock': True
        }
    
    try:
        import stripe
        stripe.api_key = stripe_secret
        
        amount = int(request.get('amount', 0) * 100)  # Convert to cents
        
        intent = stripe.PaymentIntent.create(
            amount=amount,
            currency='cad',
            automatic_payment_methods={'enabled': True},
            metadata={
                'user_id': current_user['id'],
                'ride_id': request.get('ride_id', '')
            }
        )
        
        return {
            'client_secret': intent.client_secret,
            'payment_intent_id': intent.id,
            'mock': False
        }
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/confirm")
async def confirm_payment(request: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    """Confirm payment was successful"""
    payment_intent_id = request.get('payment_intent_id')
    ride_id = request.get('ride_id')
    
    if payment_intent_id and payment_intent_id.startswith('pi_mock_'):
        # Mock payment
        if ride_id:
            await db.rides.update_one(
                {'id': ride_id},
                {'$set': {'payment_status': 'paid', 'payment_intent_id': payment_intent_id}}
            )
        return {'status': 'succeeded', 'mock': True}
    
    settings = await db.settings.find_one({'id': 'app_settings'})
    stripe_secret = settings.get('stripe_secret_key', '') if settings else ''
    
    if stripe_secret:
        try:
            import stripe
            stripe.api_key = stripe_secret
            intent = stripe.PaymentIntent.retrieve(payment_intent_id)
            
            if ride_id:
                await db.rides.update_one(
                    {'id': ride_id},
                    {'$set': {'payment_status': intent.status, 'payment_intent_id': payment_intent_id}}
                )
            
            return {'status': intent.status, 'mock': False}
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return {'status': 'unknown', 'mock': True}
