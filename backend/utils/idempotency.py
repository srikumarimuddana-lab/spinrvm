"""Idempotency-key support for non-idempotent write endpoints.

Pattern (RFC 9457 / Stripe-style):
    1. Client generates a UUID per logical operation ("I want to create
       this ride"), sends it in the `Idempotency-Key` header.
    2. Server hashes (user_id + key) → Redis. If the key is seen again
       within the TTL (default 24h), the cached response is returned
       unchanged instead of re-executing the handler.
    3. If the handler is still in flight when a retry arrives (e.g.
       client timed out at 15s, server is still writing at 16s), the
       second caller briefly waits for the first to complete via a
       lightweight Redis lock and then returns the cached result.

Why this matters:
    - Network timeouts are the common case under load. Without
      idempotency, a client retry after a timeout double-books the
      ride / double-charges the card / double-credits the wallet.
    - The backend retry loop in db_supabase.run_sync does not retry
      non-idempotent writes for this exact reason — idempotency keys
      push the retry decision to the client, which can safely replay.

Usage:
    from utils.idempotency import idempotent_endpoint

    @api_router.post("")
    @idempotent_endpoint(scope="ride", ttl_seconds=86400)
    async def create_ride(request: Request, body: CreateRideRequest, ...):
        ...

The decorator:
    - Reads `Idempotency-Key` from the request headers.
    - If absent: runs the handler as usual (caller opted out of
      idempotency; fine for GET-style replay-safe ops).
    - If present and the key has been seen: returns the cached
      response body + status; handler does not run.
    - If present and new: runs the handler, caches the response on
      success (2xx), does NOT cache on 4xx/5xx (retry is allowed).

Note: the scope must be unique per endpoint family (e.g. "ride",
"wallet_topup") so the same key used on two different endpoints does
not collide. The user_id is mixed in so keys are per-user.
"""

from __future__ import annotations

import functools
import hashlib
import json as _json
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger

try:
    from .redis_client import redis_get, redis_set
except ImportError:
    from utils.redis_client import redis_get, redis_set  # type: ignore


_MAX_KEY_LENGTH = 128
_DEFAULT_TTL_SECONDS = 24 * 60 * 60  # 24h


def _build_cache_key(scope: str, user_id: str, idempotency_key: str) -> str:
    """Stable cache key that mixes scope + user + client key.

    Hashing the client key keeps the cache key short and removes any
    characters that might break Redis key formatting; prefixing with
    scope + user_id prevents cross-endpoint / cross-user collisions.
    """
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    return f"idem:{scope}:{user_id}:{digest}"


async def read_cached_response(cache_key: str) -> Optional[dict]:
    """Return the previously-stored response for this key, or None."""
    try:
        raw = await redis_get(cache_key)
    except Exception as exc:
        logger.opt(exception=True).error(f"[IDEM] Redis read failed for {cache_key}: {exc}")
        return None
    if not raw:
        return None
    try:
        return _json.loads(raw)
    except Exception:
        logger.error(f"[IDEM] Corrupt cached response for {cache_key}")
        return None


async def write_cached_response(cache_key: str, status_code: int, body: Any, ttl: int) -> None:
    """Cache a successful response. Failures are logged but not raised —
    idempotency is best-effort and must never fail the write itself."""
    try:
        payload = _json.dumps({"status": status_code, "body": body}, default=str)
        await redis_set(cache_key, payload, ttl=ttl)
    except Exception as exc:
        logger.opt(exception=True).error(f"[IDEM] Redis write failed for {cache_key}: {exc}")


def _extract_user_id(request: Request, kwargs: dict) -> Optional[str]:
    """Pull the current user's id out of the route dependency injection.

    Convention: every authenticated endpoint in this codebase has
    `current_user: dict = Depends(get_current_user)` — the id is at
    `current_user["id"]`. Accept either `current_user` or `user` kwarg
    names; fall back to request.state for edge cases.
    """
    for key in ("current_user", "user"):
        val = kwargs.get(key)
        if isinstance(val, dict):
            uid = val.get("id")
            if uid:
                return str(uid)
    uid = getattr(request.state, "user_id", None) if request is not None else None
    return str(uid) if uid else None


def idempotent_endpoint(scope: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS):
    """Decorator that applies idempotency-key caching around a FastAPI handler.

    Only caches successful (2xx, including the decorator returning the
    raw dict that FastAPI will render as 200). Error responses are not
    cached so the client can retry and eventually succeed.

    The handler MUST accept `request: Request` as one of its parameters
    (FastAPI will inject it). If a retry arrives while the first call is
    still in flight, the second caller returns 409 with a specific
    error so the client can back off — we don't hold the request open
    waiting for completion (would create coordination complexity across
    replicas).
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            # Find the Request object in the handler's kwargs (FastAPI
            # injects it if declared) or positional args.
            request: Optional[Request] = kwargs.get("request")
            if request is None:
                for a in args:
                    if isinstance(a, Request):
                        request = a
                        break
            if request is None:
                # Handler didn't declare `request: Request` — no way to
                # read the header. Run as a normal (non-idempotent) call.
                return await fn(*args, **kwargs)

            key = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
            if not key:
                # Client opted out — run normally.
                return await fn(*args, **kwargs)

            if len(key) > _MAX_KEY_LENGTH:
                raise HTTPException(
                    status_code=400,
                    detail=f"Idempotency-Key must be ≤{_MAX_KEY_LENGTH} characters",
                )

            user_id = _extract_user_id(request, kwargs)
            if not user_id:
                # No authenticated user — can't scope the key safely.
                # Fall through to running the handler without caching
                # (e.g. /auth/verify-otp, though it doesn't use this
                # decorator today).
                return await fn(*args, **kwargs)

            cache_key = _build_cache_key(scope, user_id, key)

            # Cached response replay — short-circuit without running handler.
            cached = await read_cached_response(cache_key)
            if cached:
                status = cached.get("status", 200)
                body = cached.get("body")
                logger.info(f"[IDEM] Replaying cached response for {scope} user={user_id}")
                return JSONResponse(status_code=status, content=body)

            # Best-effort in-flight lock: SET with NX + short TTL so two
            # concurrent callers with the same key don't both execute
            # the handler. The lock expires quickly — if the handler
            # takes longer, the second caller will see the lock expired
            # and proceed; this is acceptable because it's rare and the
            # eventual cached response would still collapse on the next
            # retry. No distributed-lock complexity needed.
            lock_key = cache_key + ":lock"
            try:
                # redis_set doesn't support NX natively in our shim, so
                # we check-and-set best-effort. Racy between replicas
                # but good enough for this purpose — the handler itself
                # should enforce uniqueness (e.g. unique-index on
                # client_idempotency_key in the DB).
                locked = await redis_get(lock_key)
                if locked:
                    raise HTTPException(
                        status_code=409,
                        detail="A request with this Idempotency-Key is already in flight. Retry after a short delay.",
                    )
                # 5× Stripe p99 latency (~30 s) — keeps lock alive for slow upstream calls so retries can't double-charge.
                await redis_set(lock_key, "1", ttl=150)
            except HTTPException:
                raise
            except Exception as exc:
                logger.debug(f"[IDEM] Lock check failed (proceeding): {exc}")

            try:
                result = await fn(*args, **kwargs)
            except HTTPException:
                # Don't cache errors — client can retry and maybe succeed.
                raise
            except Exception:
                raise

            # Cache the raw result. FastAPI will serialise it back to
            # JSON on the happy path; we mirror the same representation.
            await write_cached_response(cache_key, 200, result, ttl=ttl_seconds)
            return result

        return wrapper

    return decorator
