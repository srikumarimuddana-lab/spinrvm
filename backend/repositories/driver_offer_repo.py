"""Repository wrapper for the v3 dispatch claim RPC."""

from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from ._base import invalidate_driver_cache, run_sync, supabase
except ImportError:  # pragma: no cover - top-level backend import mode
    from repositories._base import invalidate_driver_cache, run_sync, supabase  # type: ignore


async def claim_offers(
    ride_id: str,
    candidates: list[dict[str, Any]],
    *,
    max_offers: int,
    offer_ttl_seconds: int,
    require_subscription: bool = False,
    mode: str = "automatic",
) -> dict[str, Any]:
    """Call ``dispatch_claim_offers_v3`` and invalidate driver caches.

    Arguments are validated here before reaching the RPC so callers get a
    clear ``ValueError`` rather than a Postgres 22023.  On success the
    driver cache is invalidated for every attempted driver (claimed or
    not) because the RPC may have changed ``is_available`` /
    ``availability_claimed_at``.
    """
    if not ride_id:
        raise ValueError("ride_id is required")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty list")
    if len(candidates) > 50:
        raise ValueError("candidates exceeds 50 entries")
    if not isinstance(max_offers, int) or max_offers < 1 or max_offers > 10:
        raise ValueError("max_offers must be 1..10")
    if not isinstance(offer_ttl_seconds, int) or offer_ttl_seconds < 5 or offer_ttl_seconds > 120:
        raise ValueError("offer_ttl_seconds must be 5..120")
    if mode not in ("automatic", "admin_direct"):
        raise ValueError("mode must be 'automatic' or 'admin_direct'")
    if mode == "admin_direct" and (len(candidates) != 1 or max_offers != 1):
        raise ValueError("admin_direct requires exactly one candidate and max_offers=1")

    if not supabase:
        raise RuntimeError("Supabase client unavailable for dispatch claim")

    params = {
        "p_ride_id": ride_id,
        "p_candidates": json.dumps(candidates),
        "p_max_offers": max_offers,
        "p_offer_ttl_seconds": offer_ttl_seconds,
        "p_require_subscription": require_subscription,
        "p_mode": mode,
    }

    def _call():
        response = supabase.rpc("dispatch_claim_offers_v3", params).execute()
        return getattr(response, "data", None)

    value = await run_sync(_call, retry_policy="write")

    if not isinstance(value, dict):
        raise TypeError("dispatch_claim_offers_v3 returned non-dict JSON")

    # Invalidate driver cache for every attempted driver
    attempted_ids: list[str] = []
    for entry in value.get("results") or []:
        if isinstance(entry, dict) and entry.get("driver_id"):
            attempted_ids.append(entry["driver_id"])
    for driver_id in attempted_ids:
        try:
            user_id = None
            for entry in value.get("results") or []:
                if isinstance(entry, dict) and entry.get("driver_id") == driver_id:
                    user_id = entry.get("user_id")
                    break
            await invalidate_driver_cache(driver_id=driver_id, user_id=user_id)
        except Exception:  # noqa: S110 — best-effort cache invalidation
            pass

    return value


RESOLVE_ACTIONS = frozenset({"accept", "decline", "expire", "cancel_unaccepted"})
_USER_ACTIONS = frozenset({"accept", "decline"})


def _require_uuid(value: str, name: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{name} must be a UUID") from None


async def _call_rpc(name: str, params: dict[str, Any]) -> dict[str, Any]:
    if not supabase:
        raise RuntimeError(f"Supabase client unavailable for {name}")

    def _call():
        response = supabase.rpc(name, params).execute()
        return getattr(response, "data", None)

    value = await run_sync(_call, retry_policy="idempotent_write")
    if not isinstance(value, dict):
        raise TypeError(f"{name} returned non-dict JSON")
    return value


async def _invalidate(driver_id: Any, user_id: Any) -> None:
    if not driver_id:
        return
    try:
        await invalidate_driver_cache(driver_id=driver_id, user_id=user_id)
    except Exception:  # noqa: S110 — best-effort cache invalidation
        pass


async def resolve_offer(
    offer_id: str,
    claim_id: str,
    *,
    action: str,
    request_id: str,
    expected_epoch: int | None = None,
    actor_session_id: str | None = None,
    miss_threshold: int = 3,
) -> dict[str, Any]:
    """Call ``resolve_driver_offer`` for one v2 offer decision.

    Safe to retry: the RPC stores every mutating result under
    ``(offer_id, request_id)`` and replays it with ``replayed: true``.
    accept/decline need the caller's epoch and user session; expire and
    cancel_unaccepted are system decisions and take neither.
    """
    if action not in RESOLVE_ACTIONS:
        raise ValueError(f"unsupported offer action: {action}")
    offer_id = _require_uuid(offer_id, "offer_id")
    claim_id = _require_uuid(claim_id, "claim_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise ValueError("request_id must be 1..128 characters")
    if type(miss_threshold) is not int or not 1 <= miss_threshold <= 20:
        raise ValueError("miss_threshold must be 1..20")
    if action in _USER_ACTIONS:
        if type(expected_epoch) is not int or expected_epoch < 0:
            raise ValueError(f"{action} needs a non-negative integer expected_epoch")
        if not isinstance(actor_session_id, str) or not actor_session_id.strip():
            raise ValueError(f"{action} needs the caller's session id")
        if actor_session_id.startswith("system:"):
            raise ValueError(f"{action} cannot run as a system actor")
    elif expected_epoch is not None or actor_session_id is not None:
        raise ValueError(f"{action} takes no expected_epoch or actor_session_id")

    value = await _call_rpc(
        "resolve_driver_offer",
        {
            "p_offer_id": offer_id,
            "p_claim_id": claim_id,
            "p_expected_epoch": expected_epoch,
            "p_actor_session_id": actor_session_id,
            "p_action": action,
            "p_request_id": request_id,
            "p_miss_threshold": miss_threshold,
        },
    )
    await _invalidate(value.get("driver_id"), value.get("driver_user_id"))
    return value


async def finalize_deferred_availability(driver_id: str, *, request_id: str) -> dict[str, Any]:
    """Finish a deferred stop/pause once the driver has no obligation left."""
    if not driver_id:
        raise ValueError("driver_id is required")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
        raise ValueError("request_id must be 1..128 characters")
    value = await _call_rpc(
        "finalize_deferred_driver_availability",
        {"p_driver_id": driver_id, "p_request_id": request_id},
    )
    if value.get("finalized"):
        await _invalidate(driver_id, None)
    return value
