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


READINESS_REASONS = frozenset({"still_ready", "trip_completed"})
RECONCILE_KINDS = frozenset({"contact_gap", "readiness"})


async def _rpc_object(name: str, params: dict[str, Any], retry_policy: str) -> dict[str, Any]:
    if not supabase:
        raise RuntimeError(f"Supabase client unavailable for {name}")

    def _call():
        response = supabase.rpc(name, params).execute()
        return getattr(response, "data", None)

    value = await run_sync(_call, retry_policy=retry_policy)
    if isinstance(value, list):
        value = value[0] if value else None
    if isinstance(value, dict):
        return value
    raise TypeError(f"{name} returned non-object JSON")


async def confirm_driver_ready(
    driver_id: str,
    expected_epoch: int | None,
    authenticated_session_id: str,
    request_id: str,
    *,
    reason: str = "still_ready",
) -> dict[str, Any]:
    """Refresh ``ready_until`` via ``confirm_driver_ready`` (migration 462).

    ``expected_epoch`` may be None only for ``reason="trip_completed"``.
    Never bumps the epoch; a late ``still_ready`` returns READINESS_EXPIRED.
    """
    if reason not in READINESS_REASONS:
        raise ValueError(f"unsupported readiness reason: {reason}")
    if not driver_id or not authenticated_session_id or not request_id or len(request_id) > 128:
        raise ValueError("driver_id, authenticated_session_id and request_id (<=128) are required")
    if authenticated_session_id.startswith(SYSTEM_ACTOR_PREFIX):
        raise ValueError("a system actor cannot confirm readiness")
    if expected_epoch is None:
        if reason != "trip_completed":
            raise ValueError("expected_epoch is required for still_ready")
    elif type(expected_epoch) is not int or expected_epoch < 0:
        raise ValueError("expected_epoch must be a non-negative integer")
    value = await _rpc_object(
        "confirm_driver_ready",
        {
            "p_driver_id": driver_id,
            "p_expected_epoch": expected_epoch,
            "p_authenticated_session_id": authenticated_session_id,
            "p_request_id": request_id,
            "p_reason": reason,
        },
        "idempotent_write",
    )
    if value.get("code") in ("OK", "READINESS_EXPIRED") and not value.get("replayed"):
        await invalidate_driver_cache(driver_id=driver_id)
    return value


async def list_availability_reconcile_candidates(limit: int = 100) -> dict[str, Any]:
    """Read contact_gap / readiness_due / prompt_due candidates (DB clock)."""
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    return await _rpc_object("list_driver_availability_reconcile_candidates", {"p_limit": limit}, "read")


async def reconcile_driver_readiness(driver_id: str, expected_epoch: int, kind: str, request_id: str) -> dict[str, Any]:
    """Pause one driver for a contact gap or expired readiness (SKIP LOCKED)."""
    if kind not in RECONCILE_KINDS:
        raise ValueError(f"unsupported reconcile kind: {kind}")
    if not driver_id or not request_id or len(request_id) > 128:
        raise ValueError("driver_id and request_id (<=128) are required")
    if type(expected_epoch) is not int or expected_epoch < 0:
        raise ValueError("expected_epoch must be a non-negative integer")
    value = await _rpc_object(
        "reconcile_driver_readiness",
        {"p_driver_id": driver_id, "p_expected_epoch": expected_epoch, "p_kind": kind, "p_request_id": request_id},
        "idempotent_write",
    )
    if value.get("code") == "OK" and not value.get("replayed"):
        await invalidate_driver_cache(driver_id=driver_id, user_id=value.get("user_id"))
    return value


async def claim_readiness_prompt(driver_id: str, expected_epoch: int, ready_until: str) -> str | None:
    """Claim the one prompt for this ``ready_until``; returns user_id or None."""
    if not driver_id or not ready_until:
        raise ValueError("driver_id and ready_until are required")
    if type(expected_epoch) is not int or expected_epoch < 0:
        raise ValueError("expected_epoch must be a non-negative integer")
    if not supabase:
        raise RuntimeError("Supabase client unavailable for claim_readiness_prompt")
    params = {"p_driver_id": driver_id, "p_expected_epoch": expected_epoch, "p_ready_until": ready_until}

    def _call():
        response = supabase.rpc("claim_readiness_prompt", params).execute()
        return getattr(response, "data", None)

    # A conditional UPDATE; a retried call after a lost response returns None.
    value = await run_sync(_call, retry_policy="write")
    if isinstance(value, list):
        value = value[0] if value else None
    return value if isinstance(value, str) and value else None
