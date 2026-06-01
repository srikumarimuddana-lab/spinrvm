"""Google Maps Platform daily-spend circuit breaker.

Tracks per-SKU call counts in Redis under date-keyed buckets. Estimates daily
USD spend by multiplying counts by per-call pricing constants. When the daily
estimate exceeds ``MAPS_DAILY_BUDGET_USD`` the breaker opens and proxy routes
should respond 503 instead of forwarding to Google.

This is defence in depth on top of GCP-side budget alerts: it stops the bleed
within seconds rather than after the next billing cycle, and works even when
the GCP project has no hard cap configured.

Replay-safe: Redis ``INCR`` is atomic. The TTL on a daily bucket is 26 hours
so the bucket survives clock skew across replicas at the day boundary.

Failure mode: if Redis is unavailable, ``record_call`` and ``check_budget`` log
and return permissively (allow the call). Rationale: a Redis outage should not
take Maps offline for users; the GCP-side budget alert is still our safety net.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

try:
    from .redis_client import redis_delete, redis_expire, redis_get, redis_incr
except ImportError:  # pragma: no cover - dual import path
    from utils.redis_client import redis_delete, redis_expire, redis_get, redis_incr  # type: ignore

try:
    from ..core.config import settings
except ImportError:  # pragma: no cover - dual import path
    from core.config import settings  # type: ignore

logger = logging.getLogger(__name__)

Sku = Literal["autocomplete", "autocomplete_session", "details", "geocode"]

# USD per call. Source: Google Maps Platform pricing 2026.
# Places API (New) charges autocomplete requests separately for sessions that
# terminate in Place Details Essentials; `autocomplete_session` remains for
# historical counters that may still exist in today's Redis bucket.
_PRICE_USD: dict[Sku, float] = {
    "autocomplete": 0.00283,
    "autocomplete_session": 0.017,
    "details": 0.005,
    "geocode": 0.005,
}

_BUCKET_TTL_SECONDS = 26 * 3600
_SESSION_TTL_SECONDS = 30 * 60
_SESSION_AUTOCOMPLETE_PAID_LIMIT = 12


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _key(sku: Sku, day: str | None = None) -> str:
    return f"maps:budget:{day or _today_utc()}:{sku}"


def _daily_budget_usd() -> float:
    return float(getattr(settings, "MAPS_DAILY_BUDGET_USD", 5.0))


async def record_call(sku: Sku) -> None:
    """Increment today's counter for the given SKU.

    Soft-fails on Redis errors so a transient Redis outage does not 500 the
    Maps proxy. Counter loss biases the breaker permissive, not punitive.
    """
    try:
        count = await redis_incr(_key(sku))
        if count == 1:
            await redis_expire(_key(sku), _BUCKET_TTL_SECONDS)
    except Exception:
        logger.warning("[maps_budget] record_call(%s) failed; skipping count", sku, exc_info=False)


def _session_autocomplete_key(session_token: str) -> str:
    return f"maps:places:new:session:{session_token}:autocomplete"


async def record_autocomplete_request(session_token: str | None) -> None:
    """Record the paid part of a Places API (New) autocomplete request.

    For sessions that terminate in Place Details Essentials, Google bills only
    the first 12 autocomplete requests at the Autocomplete Requests SKU. Later
    requests in the same tokenized session are session-usage at no charge, so
    do not include them in the daily-spend estimate.
    """
    if not session_token:
        await record_call("autocomplete")
        return

    try:
        key = _session_autocomplete_key(session_token)
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, _SESSION_TTL_SECONDS)
        if count <= _SESSION_AUTOCOMPLETE_PAID_LIMIT:
            await record_call("autocomplete")
    except Exception:
        logger.warning(
            "[maps_budget] record_autocomplete_request failed; skipping count",
            exc_info=False,
        )


async def close_autocomplete_session(session_token: str | None) -> None:
    """Forget the per-session autocomplete counter after details closes it."""
    if not session_token:
        return
    try:
        await redis_delete(_session_autocomplete_key(session_token))
    except Exception:
        logger.warning(
            "[maps_budget] close_autocomplete_session failed; skipping cleanup",
            exc_info=False,
        )


async def estimate_today_usd() -> float:
    total = 0.0
    for sku, price in _PRICE_USD.items():
        try:
            raw = await redis_get(_key(sku))
        except Exception:
            logger.warning("[maps_budget] redis_get(%s) failed; assuming 0", sku, exc_info=False)
            raw = None
        if raw is not None:
            try:
                total += int(raw) * price
            except (TypeError, ValueError):
                continue
    return total


async def check_budget() -> tuple[bool, float, float]:
    """Return ``(allowed, spent_usd, budget_usd)``.

    ``allowed`` is ``False`` only when we have a confident estimate above the
    daily budget. Errors fail open.
    """
    budget = _daily_budget_usd()
    spent = await estimate_today_usd()
    return spent < budget, spent, budget
