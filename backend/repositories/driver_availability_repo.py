"""Backend repository for epoch-fenced driver availability transitions."""

from __future__ import annotations

from typing import Any

try:
    from ._base import invalidate_driver_cache, run_sync, supabase
except ImportError:  # pragma: no cover - top-level backend import mode
    from repositories._base import invalidate_driver_cache, run_sync, supabase  # type: ignore


SUPPORTED_ACTIONS = frozenset(
    {
        "go_online",
        "go_offline",
        "stop_requests",
        "pause_policy",
        "pause_unreachable",
        "pause_idle",
        "pause_misses",
        "displace_controller",
    }
)

# Trusted backend actors. Migration 457 accepts exactly these for stop and
# pause actions only, and skips the user-session and controller checks.
SYSTEM_ACTOR_PREFIX = "system:"
SYSTEM_ACTOR_SOURCES = frozenset(
    {"policy", "contact_gap", "readiness", "missed_offers", "finalize", "stale_intent", "logout"}
)


def system_actor(source: str) -> str:
    """Session argument for a trusted backend actor; never a user session."""
    if source not in SYSTEM_ACTOR_SOURCES:
        raise ValueError(f"unsupported system availability actor: {source}")
    return SYSTEM_ACTOR_PREFIX + source


async def get_driver_availability_snapshot(user_id: str) -> dict[str, Any]:
    """Read driver, obligations, feature gate, and DB time in one SQL snapshot."""
    if not user_id:
        raise ValueError("user_id is required")
    if not supabase:
        raise RuntimeError("Supabase client unavailable for driver availability snapshot")

    def _call():
        response = supabase.rpc("get_driver_availability_snapshot", {"p_user_id": user_id}).execute()
        return getattr(response, "data", None)

    value = await run_sync(_call, retry_policy="read")
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return value
    raise TypeError("get_driver_availability_snapshot returned non-object JSON")


async def transition_driver_availability(
    driver_id: str,
    expected_epoch: int,
    authenticated_session_id: str,
    action: str,
    request_id: str,
) -> dict[str, Any]:
    """Commit one availability command through the backend-only SQL RPC.

    The authenticated session ID must come from the already-validated request
    context, or be ``system_actor(...)`` for a trusted backend stop or pause.
    The repository calls the existing Supabase RPC transport and
    never falls back to another database path. The function returns the
    committed JSON snapshot, including ``code``, ``online_epoch``,
    ``controller_session_id``, ``is_online``, ``accepting_requests``,
    ``is_available``, ``last_contact_at``, ``ready_until``,
    ``availability_reason``, ``state_version``, ``has_trip``,
    ``has_pending_offer``, and ``pending_reason`` on successful transitions.
    ``request_id`` is required for safe retries after a lost response.
    """
    if not driver_id or not authenticated_session_id or not request_id:
        raise ValueError("driver_id, authenticated_session_id, and request_id are required")
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported driver availability action: {action}")
    if type(expected_epoch) is not int or expected_epoch < 0:
        raise ValueError("expected_epoch must be a non-negative integer")

    if not supabase:
        raise RuntimeError("Supabase client unavailable for driver availability transition")

    params = {
        "p_driver_id": driver_id,
        "p_expected_epoch": expected_epoch,
        "p_authenticated_session_id": authenticated_session_id,
        "p_action": action,
        "p_request_id": request_id,
    }

    def _call():
        response = supabase.rpc("transition_driver_availability", params).execute()
        return getattr(response, "data", None)

    value = await run_sync(_call, retry_policy="idempotent_write")
    if isinstance(value, dict):
        if value.get("code") == "OK":
            await invalidate_driver_cache(driver_id=driver_id)
        return value
    # Keep the transport contract strict rather than silently returning an
    # unexpected scalar or list shape.
    raise TypeError("transition_driver_availability returned non-object JSON")
