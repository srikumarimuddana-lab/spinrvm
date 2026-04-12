import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

try:
    from ...db import db
    from ...settings_loader import get_app_settings
except ImportError:
    from db import db
    from settings_loader import get_app_settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------- Settings (single row id='app_settings', flat keys) ----------


class SettingsUpdateRequest(BaseModel):
    """Strict schema for admin settings updates. Rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")

    google_maps_api_key: Optional[str] = None
    stripe_publishable_key: Optional[str] = None
    stripe_secret_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_from_number: Optional[str] = None
    driver_matching_algorithm: Optional[str] = None
    min_driver_rating: Optional[float] = None
    search_radius_km: Optional[float] = None
    cancellation_fee_admin: Optional[float] = None
    cancellation_fee_driver: Optional[float] = None
    platform_fee_percent: Optional[float] = None
    require_driver_subscription: Optional[bool] = None
    terms_of_service_text: Optional[str] = None
    privacy_policy_text: Optional[str] = None


@router.get("/settings")
async def admin_get_settings():
    """Get all settings (normalized single app_settings row as dict)."""
    return await get_app_settings()


@router.put("/settings")
async def admin_update_settings(settings: SettingsUpdateRequest):
    """Update settings (upsert single app_settings row)."""
    # Only include fields that were explicitly set (not None)
    update_fields = settings.model_dump(exclude_none=True)
    if not update_fields:
        return {"message": "No settings to update"}

    existing = await db.settings.find_one({"id": "app_settings"})

    payload = {
        "id": "app_settings",
        **update_fields,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if existing:
        update_payload = {k: v for k, v in payload.items() if k != "id"}
        await db.settings.update_one({"id": "app_settings"}, {"$set": update_payload})
    else:
        await db.settings.insert_one(payload)

    return {"message": "Settings updated"}


# ---------- Heat Map Settings ----------

_HEATMAP_SETTINGS_ID = "heatmap_settings"

_DEFAULT_HEATMAP_SETTINGS = {
    "heat_map_enabled": True,
    "heat_map_default_range": "month",
    "heat_map_intensity": "medium",
    "heat_map_radius": 25,
    "heat_map_blur": 15,
    "heat_map_gradient_start": "#00ff00",
    "heat_map_gradient_mid": "#ffff00",
    "heat_map_gradient_end": "#ff0000",
    "heat_map_show_pickups": True,
    "heat_map_show_dropoffs": True,
    "corporate_heat_map_enabled": True,
    "regular_rider_heat_map_enabled": True,
}


@router.get("/settings/heatmap")
async def admin_get_heatmap_settings():
    """Return heat-map display settings (single settings row)."""
    row = await db.settings.find_one({"id": _HEATMAP_SETTINGS_ID})
    if row:
        # Merge defaults with stored values so new keys always appear
        merged = {**_DEFAULT_HEATMAP_SETTINGS, **row}
        merged.pop("_id", None)
        return merged
    return {**_DEFAULT_HEATMAP_SETTINGS, "id": _HEATMAP_SETTINGS_ID}


@router.put("/settings/heatmap")
async def admin_update_heatmap_settings(data: Dict[str, Any]):
    """Update heat-map display settings."""
    payload = {
        "id": _HEATMAP_SETTINGS_ID,
        **{k: v for k, v in data.items() if k in _DEFAULT_HEATMAP_SETTINGS},
        "updated_at": datetime.utcnow().isoformat(),
    }

    existing = await db.settings.find_one({"id": _HEATMAP_SETTINGS_ID})
    if existing:
        update_fields = {k: v for k, v in payload.items() if k != "id"}
        await db.settings.update_one({"id": _HEATMAP_SETTINGS_ID}, {"$set": update_fields})
    else:
        await db.settings.insert_one(payload)

    return {"message": "Heat map settings updated"}
