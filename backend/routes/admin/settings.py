import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...settings_loader import get_app_settings
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from settings_loader import get_app_settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Credential fields that must never be returned verbatim in API responses.
_CREDENTIAL_FIELDS = frozenset(
    {
        "stripe_secret_key",
        "stripe_webhook_secret",
        "twilio_auth_token",
        "google_maps_api_key",
    }
)


def _mask_credentials(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of the settings dict with credential values masked."""
    result = {}
    for k, v in settings.items():
        if k in _CREDENTIAL_FIELDS and isinstance(v, str) and v:
            result[k] = v[:8] + "*****"
        else:
            result[k] = v
    return result


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
async def admin_get_settings(admin: dict = Depends(get_admin_user)):
    """Get all settings. Credential fields are masked — use the reveal endpoint to read a value."""
    raw = await get_app_settings()
    return _mask_credentials(raw)


@router.put("/settings")
async def admin_update_settings(settings: SettingsUpdateRequest, admin: dict = Depends(get_admin_user)):
    """Update settings (upsert single app_settings row). Writes an audit log entry."""
    # First check if settings row exists
    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": "app_settings"}, limit=1)
    )

    # Only persist fields the caller actually set (None = leave unchanged).
    update_fields = settings.model_dump(exclude_none=True)

    payload = {
        "id": "app_settings",
        **update_fields,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if existing:
        update_payload = {k: v for k, v in payload.items() if k != "id"}
        await db_supabase.update_one("settings", {"id": "app_settings"}, update_payload)
    else:
        await db_supabase.insert_one("settings", payload)

    # Audit log — record which keys changed, never the values.
    changed_keys = list(update_fields.keys())
    await db_supabase.insert_one(
        "audit_logs",
        {
            "id": str(uuid.uuid4()),
            "actor_id": admin["id"],
            "actor_role": admin.get("role"),
            "action": "settings_updated",
            "resource": "settings",
            "resource_id": "app_settings",
            "details": {"changed_keys": changed_keys},
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

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
async def admin_get_heatmap_settings(admin: dict = Depends(get_admin_user)):
    """Return heat-map display settings (single settings row)."""
    row = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": _HEATMAP_SETTINGS_ID}, limit=1)
    )
    if row:
        # Merge defaults with stored values so new keys always appear
        merged = {**_DEFAULT_HEATMAP_SETTINGS, **row}
        merged.pop("_id", None)
        return merged
    return {**_DEFAULT_HEATMAP_SETTINGS, "id": _HEATMAP_SETTINGS_ID}


@router.put("/settings/heatmap")
async def admin_update_heatmap_settings(data: Dict[str, Any], admin: dict = Depends(get_admin_user)):
    """Update heat-map display settings."""
    payload = {
        "id": _HEATMAP_SETTINGS_ID,
        **{k: v for k, v in data.items() if k in _DEFAULT_HEATMAP_SETTINGS},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    existing = (lambda _r: _r[0] if _r else None)(
        await db_supabase.get_rows("settings", {"id": _HEATMAP_SETTINGS_ID}, limit=1)
    )
    if existing:
        update_fields = {k: v for k, v in payload.items() if k != "id"}
        await db_supabase.update_one("settings", {"id": _HEATMAP_SETTINGS_ID}, update_fields)
    else:
        await db_supabase.insert_one("settings", payload)

    return {"message": "Heat map settings updated"}
