"""Redis connectivity diagnostics (Upstash / Fly debugging).

``get_redis_stats`` in ``redis_client`` answers "is the default REDIS_URL
reachable and how full is it" — but the WebSocket fan-out that powers admin
live-monitoring rides on ``WS_REDIS_URL``/``RATE_LIMIT_REDIS_URL`` *and* on
pub/sub specifically, which INFO/DBSIZE never exercise. This module probes a
given Redis URL end to end:

  * parse + mask (never log the password)
  * classify obvious misconfigurations before dialing (Upstash REST URL,
    plaintext ``redis://`` to a TLS-only endpoint)
  * ``PING`` with latency
  * a real subscribe → publish → receive round-trip on a throwaway channel,
    because Upstash's serverless REST tier and some proxies accept PING but
    silently drop pub/sub — which is exactly the failure that leaves a
    driver online on one replica but invisible on another.

Used by the startup banner (``core/lifespan``) and the admin ``/redis``
endpoint so operators can tell "Redis is down" from "Redis is up but pub/sub
is broken" from "URL is the wrong kind" at a glance.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List
from urllib.parse import urlparse
from uuid import uuid4

from loguru import logger


def _classify_error(exc: Exception) -> str:
    """Map a raw connection exception to an operator-actionable hint."""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "wrong version number" in msg or "ssl" in msg or "eof occurred" in msg:
        return f"{name}: TLS handshake failed — Upstash requires rediss:// (TLS). Check the URL scheme."
    if "max" in msg and ("client" in msg or "connection" in msg or "request" in msg):
        return f"{name}: connection/request limit hit — Upstash plan cap exceeded across replicas."
    if "noauth" in msg or "wrongpass" in msg or "auth" in msg:
        return f"{name}: auth rejected — token/password in the URL is wrong."
    if "name or service not known" in msg or "nodename" in msg or "getaddrinfo" in msg:
        return f"{name}: DNS resolution failed — host unreachable from this replica."
    if "timed out" in msg or isinstance(exc, asyncio.TimeoutError):
        return f"{name}: timed out — host/port blocked or wrong (TLS port vs plaintext?)."
    return f"{name}: {exc}"


async def _pubsub_roundtrip(client: Any, timeout: float) -> Dict[str, Any]:
    """Subscribe to a throwaway channel, publish, and confirm receipt."""
    channel = f"spinr:diag:{uuid4().hex}"
    ps = client.pubsub()
    try:
        await asyncio.wait_for(ps.subscribe(channel), timeout=timeout)
        await asyncio.wait_for(client.publish(channel, "1"), timeout=timeout)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Bound each poll by the time actually remaining (capped at 1s
            # per iteration), not a fixed 1.0s — a hardcoded per-call timeout
            # let a single stalled iteration overshoot the caller's requested
            # `timeout` by up to ~1s.
            remaining = deadline - time.monotonic()
            msg = await ps.get_message(ignore_subscribe_messages=True, timeout=min(1.0, max(0.0, remaining)))
            if msg and msg.get("type") == "message":
                return {"ok": True}
        return {
            "ok": False,
            "error": "no message echoed back — pub/sub unsupported or blocked (Upstash REST tier? proxy?)",
        }
    except Exception as exc:
        return {"ok": False, "error": _classify_error(exc)}
    finally:
        try:
            await ps.unsubscribe(channel)
        except Exception:  # noqa: S110 - best effort cleanup
            pass
        try:
            await ps.aclose()
        except Exception:  # noqa: S110
            pass


async def probe_redis_url(label: str, url: str, *, timeout: float = 5.0) -> Dict[str, Any]:
    """End-to-end probe of one Redis URL. Never includes the password."""
    info: Dict[str, Any] = {"label": label, "configured": bool(url)}
    if not url:
        info["status"] = "unset"
        return info

    parsed = urlparse(url)
    info["scheme"] = parsed.scheme
    info["host"] = parsed.hostname or "?"
    info["port"] = parsed.port
    info["tls"] = parsed.scheme == "rediss"
    info["endpoint"] = f"{parsed.scheme}://{parsed.hostname or '?'}:{parsed.port or '?'}"

    # Catch the two most common Upstash-on-Fly mistakes before dialing.
    if parsed.scheme in ("http", "https"):
        info["status"] = "error"
        info["error"] = (
            "this is an Upstash REST URL (http/https) — redis.asyncio needs the "
            "TCP rediss://…:6379 endpoint; pub/sub does NOT work over REST."
        )
        return info
    if parsed.scheme == "redis":
        # Not fatal (some self-hosted Redis is plaintext), but Upstash is TLS-only.
        info["warning"] = "plaintext redis:// — Upstash requires rediss:// (TLS); will fail if this is Upstash."

    try:
        import redis.asyncio as redis_asyncio  # type: ignore
    except ImportError:
        info["status"] = "error"
        info["error"] = "redis package not installed"
        return info

    client = None
    try:
        client = redis_asyncio.from_url(url, decode_responses=True)
        t0 = time.perf_counter()
        await asyncio.wait_for(client.ping(), timeout=timeout)
        info["ping_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        info["pubsub"] = await _pubsub_roundtrip(client, timeout=timeout)
        info["status"] = "ok" if info["pubsub"].get("ok") else "degraded"
    except Exception as exc:
        info["status"] = "error"
        info["error"] = _classify_error(exc)
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: S110
                pass
    return info


async def diagnose_redis(urls: Dict[str, str], *, timeout: float = 5.0) -> List[Dict[str, Any]]:
    """Probe a {label: url} map. Deduplicates identical URLs so a shared
    Redis isn't dialed three times (matters under Upstash connection caps)."""
    seen: Dict[str, Dict[str, Any]] = {}
    results: List[Dict[str, Any]] = []
    for label, url in urls.items():
        key = url or f"__unset__:{label}"
        if url and key in seen:
            shared = dict(seen[key])
            shared["label"] = label
            shared["same_as"] = seen[key]["label"]
            results.append(shared)
            continue
        res = await probe_redis_url(label, url, timeout=timeout)
        if url:
            seen[key] = res
        results.append(res)
    return results


def log_diagnosis(results: List[Dict[str, Any]]) -> None:
    """Emit a compact, password-free banner for the boot logs / Fly logs."""
    logger.info("── Redis connectivity diagnosis ───────────────────────────")
    for r in results:
        label = r["label"]
        status = r.get("status", "?")
        if status == "unset":
            logger.info(f"  {label:22s} UNSET")
            continue
        endpoint = r.get("endpoint", "?")
        same = f" (== {r['same_as']})" if r.get("same_as") else ""
        if status == "ok":
            logger.info(f"  {label:22s} OK     {endpoint}  ping={r.get('ping_ms')}ms  pubsub=ok{same}")
        elif status == "degraded":
            logger.warning(
                f"  {label:22s} DEGRADED {endpoint}  ping={r.get('ping_ms')}ms  "
                f"pubsub FAILED: {r.get('pubsub', {}).get('error')}{same}"
            )
        else:
            logger.error(f"  {label:22s} ERROR  {endpoint}  {r.get('error')}{same}")
        if r.get("warning"):
            logger.warning(f"  {label:22s} note: {r['warning']}")
    logger.info("───────────────────────────────────────────────────────────")
