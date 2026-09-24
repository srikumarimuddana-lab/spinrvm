"""Repository wrapper for the v3 dispatch claim RPC."""

from __future__ import annotations

import json
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
