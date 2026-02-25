from fastapi import APIRouter
try:
    from ..db import db
except ImportError:
    from db import db

api_router = APIRouter(tags=["Settings"])

@api_router.get("/settings")
async def get_public_settings():
    settings = await db.settings.find_one({'id': 'app_settings'})
    if not settings:
        return {'google_maps_api_key': '', 'stripe_publishable_key': ''}
    return {
        'google_maps_api_key': settings.get('google_maps_api_key', ''),
        'stripe_publishable_key': settings.get('stripe_publishable_key', '')
    }

@api_router.get("/settings/legal")
async def get_legal_settings():
    settings = await db.settings.find_one({'id': 'app_settings'})
    if not settings:
        return {'terms_of_service_text': '', 'privacy_policy_text': ''}
    return {
        'terms_of_service_text': settings.get('terms_of_service_text', ''),
        'privacy_policy_text': settings.get('privacy_policy_text', '')
    }
