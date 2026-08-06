"""Third-party dependency health for ``/health/dependencies`` and ``/metrics``.

Spinr's plain ``/health`` endpoint checks Postgres only, so a Stripe, Twilio,
Google Maps, Redis, or FCM problem is invisible until it surfaces as a failed
ride, a failed charge, or a support ticket. This module gives one cached,
timeout-bounded view of every external dependency.

Design rules, mirroring ``utils/safety_paging.py``:

1. **Never raises.** Every probe is individually wrapped. A dependency that
   times out or explodes is reported as unhealthy, never propagated — a health
   endpoint that 500s because a vendor is down is worse than useless, because
   the prober then cannot distinguish "Stripe is down" from "Spinr is down".
2. **Never leaks.** Results carry a status and a short machine-readable reason
   only. No credentials, no internal hostnames, no raw upstream error bodies,
   no URLs. Vendor error text routinely embeds account identifiers and request
   payload fragments, so it is dropped at the boundary rather than forwarded
   (PIPEDA; ``CLAUDE.md`` "What can NEVER appear in logs").
3. **Cached.** Results are memoised for ``_CACHE_TTL_SECONDS`` so an external
   prober on a 30–60 s interval, plus a Prometheus agent on 15 s, cannot
   stampede the probes.

Why vendors are NOT actively called
-----------------------------------
Only infrastructure we own (Postgres, Redis) is actively probed. Stripe,
Twilio, Google Maps and FCM are reported from **configuration state**, not a
synthetic API call, because:

- A scrape endpoint that calls a paid third-party API turns monitoring into a
  traffic generator: one prober at 60 s is ~1 440 needless Stripe calls a day,
  against rate limits shared with real settlements.
- A synthetic probe answers "can we reach the vendor", which is not the
  question that matters. Real vendor degradation already surfaces through the
  actual code paths — ``spinr_payment_settlement_total{outcome="failed"}``,
  the DB circuit breaker, push-retry counters — and those reflect production
  traffic instead of a health-check side channel.

So a vendor here reports ``not_configured`` (credentials absent — actionable,
and the single most common staging/production misconfiguration) or
``configured``. Turning ``configured`` into a genuine liveness signal means
recording real call outcomes at the call sites, which is a separate change to
those modules and is deliberately out of scope here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Tuple

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from .redis_client import get_redis_stats
except ImportError:  # pragma: no cover - exercised by `python -m` vs top-level
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.redis_client import get_redis_stats  # type: ignore

logger = logging.getLogger(__name__)

# Per-probe ceiling. Deliberately tight: this runs on a scrape path, and a slow
# dependency must be reported as slow rather than hold the response open.
_PROBE_TIMEOUT_SECONDS = 3.0

# Long enough that a 15 s Prometheus scrape and a 30 s prober share one probe;
# short enough that an outage is visible within a minute.
_CACHE_TTL_SECONDS = 30.0

# Actively probed — infrastructure we operate.
INFRA_DEPENDENCIES: Tuple[str, ...] = ("supabase", "redis")

# Reported from configuration state — see module docstring.
# name -> app_settings key that must be non-empty for the vendor to be usable.
VENDOR_SETTING_KEYS: Dict[str, str] = {
    "stripe": "stripe_secret_key",
    "twilio": "twilio_account_sid",
    "google_maps": "google_maps_api_key",
}

# Status values, ordered worst-first for aggregation.
STATUS_DOWN = "down"
STATUS_DEGRADED = "degraded"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_OK = "ok"

# Only `down` means "the platform cannot do its job". An unconfigured optional
# vendor is a warning for an operator, not a page — surfacing it as `down`
# would make every dev and staging environment page continuously.
_UNHEALTHY = {STATUS_DOWN}

_cache: Dict[str, Any] = {}
_cache_at: float = 0.0
_cache_lock = asyncio.Lock()


def _result(status: str, reason: str = "", **extra: Any) -> Dict[str, Any]:
    """Build one dependency result. ``reason`` must be a short stable code."""
    out: Dict[str, Any] = {"status": status}
    if reason:
        out["reason"] = reason
    out.update(extra)
    return out


async def _probe_supabase() -> Dict[str, Any]:
    """Reuse db_supabase.ping() — it already reports latency + circuit state."""
    try:
        detail = await asyncio.wait_for(db_supabase.ping(), timeout=_PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return _result(STATUS_DOWN, "timeout")
    except Exception as exc:
        # Log the real exception (CLAUDE.md: never swallow a DB error) but do
        # not put it in the response body — it can carry connection strings.
        logger.error("Supabase dependency probe failed: %s", type(exc).__name__, exc_info=True)
        return _result(STATUS_DOWN, "probe_failed")

    if not isinstance(detail, dict):
        return _result(STATUS_OK)

    circuit = detail.get("circuit_state")
    # An open breaker means calls are being rejected right now even though this
    # ping happened to succeed — that is degraded, not ok.
    if circuit == "open":
        return _result(STATUS_DOWN, "circuit_open", circuit_state=circuit)
    if circuit == "half_open":
        return _result(STATUS_DEGRADED, "circuit_half_open", circuit_state=circuit)

    out = _result(STATUS_OK)
    if detail.get("ping_ms") is not None:
        out["latency_ms"] = detail["ping_ms"]
    if circuit:
        out["circuit_state"] = circuit
    return out


async def _probe_redis() -> Dict[str, Any]:
    """Redis is optional by design — utils/redis_client falls back in-process.

    That fallback is silent and lossy (rate-limit and OTP-lockout state are lost
    on restart, per CLAUDE.md "Redis transparency"), so a disconnected Redis is
    reported as `degraded` rather than `ok`: the app still serves, but a
    security control has quietly weakened and an operator should know.
    """
    try:
        stats = await asyncio.wait_for(get_redis_stats(), timeout=_PROBE_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return _result(STATUS_DOWN, "timeout")
    except Exception as exc:
        logger.error("Redis dependency probe failed: %s", type(exc).__name__, exc_info=True)
        return _result(STATUS_DOWN, "probe_failed")

    if not isinstance(stats, dict) or not stats.get("connected"):
        return _result(STATUS_DEGRADED, "using_in_process_fallback")

    out = _result(STATUS_OK)
    used_pct = stats.get("used_memory_percent")
    if isinstance(used_pct, (int, float)):
        out["used_memory_percent"] = used_pct
        # Approaching maxmemory means evictions are imminent, which silently
        # drops rate-limit and lockout keys.
        if used_pct >= 90:
            out["status"] = STATUS_DEGRADED
            out["reason"] = "memory_pressure"
    return out


async def _probe_vendors() -> Dict[str, Dict[str, Any]]:
    """Report configuration presence for the credential-driven vendors."""
    try:
        settings = await asyncio.wait_for(get_app_settings(), timeout=_PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        logger.error("Vendor settings load failed: %s", type(exc).__name__, exc_info=True)
        return {name: _result(STATUS_DEGRADED, "settings_unavailable") for name in VENDOR_SETTING_KEYS}

    out: Dict[str, Dict[str, Any]] = {}
    for name, key in VENDOR_SETTING_KEYS.items():
        value = (settings or {}).get(key) or ""
        # Presence only. The value itself is never read into the response, never
        # logged, and never length-reported — a length leaks key format.
        out[name] = _result(STATUS_OK) if str(value).strip() else _result(STATUS_NOT_CONFIGURED, "missing_credentials")
    return out


async def _probe_firebase() -> Dict[str, Any]:
    """Firebase credentials come from env, not app_settings (see CLAUDE.md)."""
    import os

    configured = bool((os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON") or "").strip())
    return _result(STATUS_OK) if configured else _result(STATUS_NOT_CONFIGURED, "missing_credentials")


async def probe_dependencies(force: bool = False) -> Dict[str, Any]:
    """Return {"healthy": bool, "dependencies": {name: {...}}}, cached.

    Never raises. ``force=True`` bypasses the cache (used by tests and by an
    operator debugging a live incident).
    """
    global _cache, _cache_at

    async with _cache_lock:
        now = time.monotonic()
        if not force and _cache and (now - _cache_at) < _CACHE_TTL_SECONDS:
            return _cache

        deps: Dict[str, Dict[str, Any]] = {}
        try:
            supabase_res, redis_res, vendor_res, firebase_res = await asyncio.gather(
                _probe_supabase(),
                _probe_redis(),
                _probe_vendors(),
                _probe_firebase(),
                return_exceptions=True,
            )

            deps["supabase"] = supabase_res if isinstance(supabase_res, dict) else _result(STATUS_DOWN, "probe_failed")
            deps["redis"] = redis_res if isinstance(redis_res, dict) else _result(STATUS_DOWN, "probe_failed")
            if isinstance(vendor_res, dict):
                deps.update(vendor_res)
            else:
                deps.update({name: _result(STATUS_DOWN, "probe_failed") for name in VENDOR_SETTING_KEYS})
            deps["firebase"] = firebase_res if isinstance(firebase_res, dict) else _result(STATUS_DOWN, "probe_failed")
        except Exception as exc:
            # Belt and braces: gather(return_exceptions=True) should make this
            # unreachable, but a health endpoint must not be the thing that
            # takes the process down.
            logger.error("Dependency probe sweep failed: %s", type(exc).__name__, exc_info=True)
            deps = {"supabase": _result(STATUS_DOWN, "probe_failed")}

        healthy = not any(d.get("status") in _UNHEALTHY for d in deps.values())
        _cache = {"healthy": healthy, "dependencies": deps}
        _cache_at = now
        return _cache


def reset_cache() -> None:
    """Drop the memoised result. For tests and post-config-change refreshes."""
    global _cache, _cache_at
    _cache = {}
    _cache_at = 0.0


def gauge_value(status: str) -> float:
    """Map a status to the spinr_dependency_up gauge value.

    1 = serving, 0 = not serving, 0.5 = serving degraded. `not_configured` is 0
    because an unconfigured vendor genuinely cannot serve — the distinction from
    `down` lives in the endpoint body and the `reason` label, not in the gauge.
    """
    return {STATUS_OK: 1.0, STATUS_DEGRADED: 0.5, STATUS_DOWN: 0.0, STATUS_NOT_CONFIGURED: 0.0}.get(status, 0.0)
