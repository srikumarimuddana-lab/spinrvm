"""
Single source of truth for app settings.
Settings are stored as one row: id='app_settings' with flat keys.
All readers use get_app_settings() for consistent defaults and shape.
"""
from typing import Any, Dict

try:
    from .db import db
    from .schemas import AppSettings
except ImportError:
    from db import db
    from schemas import AppSettings


def _defaults_dict() -> Dict[str, Any]:
    """Return default values from AppSettings schema."""
    return AppSettings().model_dump()


async def get_app_settings() -> Dict[str, Any]:
    """
    Load app settings from DB (single row id='app_settings') and merge with
    schema defaults so every caller gets the same keys. Use this everywhere
    instead of db.settings.find_one({'id': 'app_settings'}).
    """
    defaults = _defaults_dict()
    row = await db.settings.find_one({"id": "app_settings"})
    if not row:
        return defaults
    # Merge: row overrides defaults; include any extra keys from admin (heat_map_*, app_name, etc.)
    out = {**defaults}
    for k, v in row.items():
        if k == "id":
            continue
        out[k] = v
    return out
