from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
try:
    from ..dependencies import get_current_user
    from ..db import db
    from ..settings_loader import get_app_settings
except ImportError:
    from dependencies import get_current_user
    from db import db
    from settings_loader import get_app_settings
import uuid
import logging
import stripe

logger = logging.getLogger(__name__)
api_router = APIRouter(prefix="/payments", tags=["Payments"])

async def get_or_create_stripe_customer(user_id: str):
    user = await db.users.find_one({'id': user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    stripe_customer_id = user.get('stripe_customer_id')
    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
    if not stripe_secret:
        return None
        
    stripe.api_key = stripe_secret
    
    if not stripe_customer_id:
        # Create a new Stripe customer
        customer = stripe.Customer.create(
            email=user.get('email'),
            name=f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            metadata={'user_id': user_id}
        )
        stripe_customer_id = customer.id
        await db.users.update_one({'id': user_id}, {'$set': {'stripe_customer_id': stripe_customer_id}})
        
    return stripe_customer_id


@api_router.post("/create-intent")
async def create_payment_intent(request: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    """Create a Stripe payment intent"""
    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
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
        
        # Get or create customer for saved payments
        stripe_customer_id = await get_or_create_stripe_customer(current_user['id'])
        
        intent_params = {
            'amount': amount,
            'currency': 'cad',
            'automatic_payment_methods': {'enabled': True},
            'metadata': {
                'user_id': current_user['id'],
                'ride_id': request.get('ride_id', '')
            }
        }
        
        if stripe_customer_id:
            intent_params['customer'] = stripe_customer_id
            
        payment_method_id = request.get('payment_method_id')
        if payment_method_id:
            intent_params['payment_method'] = payment_method_id
            
        intent = stripe.PaymentIntent.create(**intent_params)
        
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

    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
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

@api_router.post("/setup-intent")
async def create_setup_intent(current_user: dict = Depends(get_current_user)):
    """Create a SetupIntent to save a new payment method"""
    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
    if not stripe_secret:
        return {'client_secret': 'mock_setup_secret', 'mock': True}
        
    try:
        stripe.api_key = stripe_secret
        customer_id = await get_or_create_stripe_customer(current_user['id'])
        
        if not customer_id:
            raise HTTPException(status_code=400, detail="Could not create Stripe customer")
            
        setup_intent = stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=['card'],
        )
        
        return {
            'client_secret': setup_intent.client_secret,
            'setup_intent_id': setup_intent.id,
            'customer_id': customer_id,
            'mock': False
        }
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/methods")
async def get_payment_methods(current_user: dict = Depends(get_current_user)):
    """Get saved payment methods for the user"""
    settings = await get_app_settings()
    stripe_secret = settings.get('stripe_secret_key', '')
    
    if not stripe_secret:
        return {'methods': [], 'mock': True}
        
    try:
        stripe.api_key = stripe_secret
        user = await db.users.find_one({'id': current_user['id']})
        stripe_customer_id = user.get('stripe_customer_id') if user else None
        
        if not stripe_customer_id:
            return {'methods': [], 'mock': False}
            
        methods = stripe.PaymentMethod.list(
            customer=stripe_customer_id,
            type='card',
        )
        
        return {
            'methods': [
                {
                    'id': m.id,
                    'brand': m.card.brand,
                    'last4': m.card.last4,
                    'exp_month': m.card.exp_month,
                    'exp_year': m.card.exp_year,
                } for m in methods.data
            ],
            'mock': False
        }
    except Exception as e:
        logger.error(f"Stripe error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
