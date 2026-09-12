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
from datetime import datetime, timedelta, timezone
from typing import Literal

try:
    from .redis_client import redis_delete, redis_expire, redis_get, redis_incr, redis_incrby, redis_mget
except ImportError:  # pragma: no cover - dual import path
    from utils.redis_client import (  # type: ignore
        redis_delete,
        redis_expire,
        redis_get,
        redis_incr,
        redis_incrby,
    )

try:
    from ..core.config import settings
except ImportError:  # pragma: no cover - dual import path
    from core.config import settings  # type: ignore

logger = logging.getLogger(__name__)

Sku = Literal[
    "autocomplete",
    "autocomplete_session",
    "details",
    "geocode",
    "directions",
    "text_search_new",
    "distance_matrix",
]

# USD per call. Source: Google Maps Platform pricing 2026.
# Places API (New) charges autocomplete requests separately for sessions that
# terminate in Place Details Essentials; `autocomplete_session` remains for
# historical counters that may still exist in today's Redis bucket.
# `directions` is Routes/Directions Essentials (no traffic) — used only as the
# live-route fallback when self-hosted OSRM is down.
# `text_search_new` is Places API (New) Text Search Pro — used by the AI
# booking tool's named-place lookup (ai/tools_booking.py). B5: this SKU did
# not previously exist here at all — the AI tool called
# record_call("places_text_search"), a string outside this Literal, so every
# such call silently miscounted against no bucket and was invisible to
# estimate_today_usd()'s budget total.
# `distance_matrix` is Distance Matrix API, called from utils/maps_eta.py's
# Google fallback (OSRM is tried first, at zero metered cost). This call
# site requests `departure_time`/`traffic_model` (traffic-aware), which is
# priced as the Advanced tier, not the $0.005 Essentials rate `directions`/
# `geocode` above use — R4 (docs/audit/ride-experience/ROADMAP.md): this SKU
# did not previously exist in this Literal at all, so estimate_today_usd()
# structurally could not total it regardless of call volume. NOTE: live
# Google Maps Platform pricing could not be fetched when this constant was
# set (developers.google.com is EGRESS_BLOCKED from this environment, same
# limitation the audit itself hit) — re-verify against
# https://developers.google.com/maps/billing-and-pricing/pricing before
# relying on this figure for a real dollar budget decision. Deliberately
# set to the higher, traffic-aware estimate rather than reusing the
# Essentials rate: for a circuit breaker, overestimating spend trips early
# (safe); underestimating lets real spend hide past the ceiling (the exact
# failure this SKU registration exists to close).
_PRICE_USD: dict[Sku, float] = {
    "autocomplete": 0.00283,
    "autocomplete_session": 0.017,
    "details": 0.005,
    "geocode": 0.005,
    "text_search_new": 0.032,
    "directions": 0.005,
    "distance_matrix": 0.010,
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


def _session_autocomplete_key(session_token: str, day: str | None = None) -> str:
    return f"maps:places:new:session:{day or _today_utc()}:{session_token}:autocomplete"


def _yesterday_utc() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


async def record_autocomplete_request(session_token: str | None) -> None:
    """Record a Places API (New) autocomplete request as paid until proven free.

    Incomplete/abandoned sessions are billed per Autocomplete Requests, so every
    successful autocomplete must immediately count toward the circuit breaker. If
    the session later closes with this proxy's Place Details Essentials call,
    ``close_autocomplete_session`` reconciles requests 13+ back out because those
    completed-session requests become no-charge session usage.
    """
    await record_call("autocomplete")

    if not session_token:
        return

    try:
        key = _session_autocomplete_key(session_token)
        count = await redis_incr(key)
        if count == 1:
            await redis_expire(key, _SESSION_TTL_SECONDS)
    except Exception:
        logger.warning(
            "[maps_budget] record_autocomplete_request failed; request counted without session tracking",
            exc_info=False,
        )


async def _reconcile_closed_session_day(session_token: str, day: str) -> None:
    session_key = _session_autocomplete_key(session_token, day)
    raw_count = await redis_get(session_key)
    if raw_count is None:
        return

    try:
        request_count = int(raw_count)
    except (TypeError, ValueError):
        request_count = 0

    free_count = max(0, request_count - _SESSION_AUTOCOMPLETE_PAID_LIMIT)
    if free_count:
        autocomplete_key = _key("autocomplete", day)
        raw_daily = await redis_get(autocomplete_key)
        try:
            daily_count = int(raw_daily) if raw_daily is not None else 0
        except (TypeError, ValueError):
            daily_count = 0
        decrement = min(free_count, daily_count)
        if decrement:
            new_daily_count = await redis_incrby(autocomplete_key, -decrement)
            if new_daily_count == 0:
                await redis_expire(autocomplete_key, _BUCKET_TTL_SECONDS)

    await redis_delete(session_key)


async def close_autocomplete_session(session_token: str | None) -> None:
    """Reconcile and forget a tokenized session after details closes it.

    A session may begin before UTC midnight and close shortly after, so reconcile
    both today's and yesterday's short-lived session counters.
    """
    if not session_token:
        return
    try:
        await _reconcile_closed_session_day(session_token, _today_utc())
        await _reconcile_closed_session_day(session_token, _yesterday_utc())
    except Exception:
        logger.warning(
            "[maps_budget] close_autocomplete_session failed; skipping reconciliation",
            exc_info=False,
        )


async def estimate_today_usd() -> float:
    """Sum today's estimated spend across every tracked SKU.

    Batched via a single ``redis_mget`` round-trip rather than one
    ``redis_get`` per SKU — this is called from ``check_budget()``, which
    ``batch_get_etas`` (R4, docs/audit/ride-experience/ROADMAP.md) now reaches
    from the dispatch-matching hot path's purpose-built 1.2s timeout window
    (``routes/rides/matching.py``), where N sequential round-trips eat
    directly into that budget. ``redis_mget`` raises on a Redis error (unlike
    ``redis_get``'s own per-key fail-open) — caught here to preserve this
    function's documented "errors fail open" contract.
    """
    skus = list(_PRICE_USD.items())
    try:
        raw_values = await redis_mget([_key(sku) for sku, _price in skus])
    except Exception:
        logger.warning("[maps_budget] redis_mget failed; assuming 0 for all SKUs", exc_info=False)
        raw_values = [None] * len(skus)

    total = 0.0
    for (_sku, price), raw in zip(skus, raw_values, strict=True):
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
