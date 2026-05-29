"""
Single source of truth for app settings.
Settings are stored as one row: id='app_settings' with flat keys.
All readers use get_app_settings() for consistent defaults and shape.
"""

import time
from typing import Any, Dict, Optional, Tuple

try:
    from . import db_supabase
    from .schemas import AppSettings
except ImportError:
    import db_supabase
    from schemas import AppSettings

_SETTINGS_TTL = 60  # seconds — allows admin changes to propagate within 1 minute
_settings_cache: Optional[Tuple[float, Dict[str, Any]]] = None


def _defaults_dict() -> Dict[str, Any]:
    """Return default values from AppSettings schema."""
    return AppSettings().model_dump()


async def get_app_settings() -> Dict[str, Any]:
    """
    Load app settings from DB (single row id='app_settings') and merge with
    schema defaults so every caller gets the same keys. Use this everywhere
    instead of (lambda _r: _r[0] if _r else None)(db_supabase.get_rows("settings", {'id': 'app_settings'}, limit=1)).

    Results are cached in-process for _SETTINGS_TTL seconds to avoid a DB
    round-trip on every ride creation and dispatch call.
    """
    global _settings_cache
    now = time.monotonic()
    if _settings_cache is not None:
        cached_at, cached_val = _settings_cache
        if now - cached_at < _SETTINGS_TTL:
            return cached_val

    defaults = _defaults_dict()
    row = (lambda _r: _r[0] if _r else None)(await db_supabase.get_rows("settings", {"id": "app_settings"}, limit=1))
    if not row:
        result = defaults
    else:
        # Merge: row overrides defaults; include any extra keys from admin (heat_map_*, app_name, etc.)
        result = {**defaults}
        for k, v in row.items():
            if k == "id":
                continue
            result[k] = v

    _settings_cache = (now, result)
    return result
