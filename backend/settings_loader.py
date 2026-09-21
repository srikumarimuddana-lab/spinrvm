"""
Single source of truth for app settings.
Settings are stored as one row: id='app_settings' with flat keys.
All readers use get_app_settings() for consistent defaults and shape.
"""

import time
from typing import Any, Dict, Optional, Tuple

from loguru import logger

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
    if not row and _settings_cache is not None:
        # A ROWLESS read is not an error — get_rows returns [] without raising
        # (repositories/_base.py) when the row is missing, RLS hides it, the
        # schema cache blips, or the client is uninitialised on a replica. The
        # old code overwrote the cache with schema defaults in that case, which
        # for a kill switch means its default (True, "not killing anything")
        # silently replaces an operator's pause — the same hole the raise path
        # was fixed for, but quieter, because nothing raised and nothing logged.
        #
        # So: once this process has ever loaded settings successfully, a rowless
        # read keeps the last known values rather than reverting to defaults,
        # and says so. A cold process with no cache still falls back to
        # defaults, which is the only thing it can do.
        # ERROR, not warning: this branch can only fire when settings loaded
        # successfully at least once and then stopped returning a row, which is
        # a real anomaly rather than a cold start. Bounded to roughly one event
        # per process per TTL (60s), so it cannot flood Sentry.
        logger.bind(domain="admin").error(
            "settings row 'app_settings' came back empty — keeping the last known values "
            "rather than reverting to schema defaults (a kill switch would silently un-pause)"
        )
        _settings_cache = (now, _settings_cache[1])
        return _settings_cache[1]
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


def get_last_known_app_settings() -> Optional[Dict[str, Any]]:
    """The last settings this process loaded successfully, **however old**, or
    ``None`` if it has never completed one.

    For callers that must still make a decision when :func:`get_app_settings`
    *raises*. ``get_app_settings`` only writes ``_settings_cache`` after a
    successful read, so whatever is in there is by construction the last known
    good value — a failed read never overwrites it.

    Deliberately ignores ``_SETTINGS_TTL``, which is the whole point and the
    only difference from :func:`get_cached_app_settings`. The TTL exists to
    decide *when to refresh*; it is not a claim that a value older than 60 s is
    worthless. When the refresh itself is failing, a minute-old flag is a far
    better basis for a decision than a hardcoded default — in particular for an
    incident kill switch, where the hardcoded default (``True``, "not killing
    anything") is exactly the wrong answer during the incident the switch was
    flipped for.

    Returns the live cached dict, not a copy, matching
    :func:`get_cached_app_settings` and :func:`get_app_settings` — every reader
    in this codebase treats settings as read-only.
    """
    if _settings_cache is None:
        return None
    return _settings_cache[1]


def get_cached_app_settings() -> Optional[Dict[str, Any]]:
    """The last-loaded settings if still within the TTL, else None. Never loads.

    Sync counterpart to :func:`get_app_settings`, for the PDF/Excel/Word report
    builders. fpdf2 and openpyxl are synchronous, so those render paths cannot
    await a settings read — which is why company identity used to be hardcoded
    there, and why changing the company address meant editing code.

    Deliberately never triggers a load and never blocks: a sync render must not
    do I/O it cannot await. Returning None lets the caller fall back.

    In a live process the cache is effectively always warm — every ride, fare,
    email and dispatch call reads settings, all sharing this 60 s cache — so a
    report generated by a running server sees current values. The cold case is
    real but narrow (first request after a restart), and it degrades to the
    module constants rather than to anything wrong.
    """
    if _settings_cache is None:
        return None
    cached_at, cached_val = _settings_cache
    if time.monotonic() - cached_at >= _SETTINGS_TTL:
        return None
    return cached_val
