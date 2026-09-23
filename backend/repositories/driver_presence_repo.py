"""Backend-only repository for session and online-epoch fenced presence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from ._base import run_sync, supabase
except ImportError:  # pragma: no cover - top-level backend import mode
    from repositories._base import run_sync, supabase  # type: ignore


async def renew_driver_presence(
    driver_id: str,
    authenticated_session_id: str,
    online_epoch: int,
    location_captured_at: datetime | None = None,
) -> dict[str, Any]:
    """Renew authenticated network contact and optional bounded GPS evidence.

    Session identity is supplied by the authenticated token dependency; this
    repository never substitutes the database's current session for it.
    """
    if not driver_id or not authenticated_session_id:
        raise ValueError("driver_id and authenticated_session_id are required")
    if type(online_epoch) is not int or online_epoch < 0:
        raise ValueError("online_epoch must be a non-negative integer")
    if not supabase:
        raise RuntimeError("Supabase client unavailable for driver presence renewal")

    params = {
        "p_driver_id": driver_id,
        "p_authenticated_session_id": authenticated_session_id,
        "p_online_epoch": online_epoch,
        "p_location_captured_at": location_captured_at.isoformat() if location_captured_at else None,
    }

    def _call():
        response = supabase.rpc("renew_driver_presence", params).execute()
        return getattr(response, "data", None)

    value = await run_sync(_call, retry_policy="idempotent_write")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return value
    raise TypeError("renew_driver_presence returned non-object JSON")
