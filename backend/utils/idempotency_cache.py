"""Idempotency cache for payment deduplication using Redis."""

import json
import logging
from typing import Any

from .redis_client import redis_client

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY_PREFIX = "spinr:idempotency:"
IDEMPOTENCY_TTL_SECONDS = 86400  # 24 hours


def get_cached_payment(idempotency_key: str) -> dict[str, Any] | None:
    """Check if payment with this idempotency key was already processed.

    Returns cached response if found, None otherwise.
    """
    if not idempotency_key:
        return None

    cache_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"

    try:
        cached = redis_client.get(cache_key)
        if cached:
            logger.info(f"Idempotency cache hit: {idempotency_key}")
            return json.loads(cached)
        return None
    except Exception as e:
        logger.error(f"Idempotency cache lookup failed: {e}")
        return None


def cache_payment_result(idempotency_key: str, result: dict[str, Any]) -> None:
    """Cache the payment result for future duplicate attempts."""
    if not idempotency_key:
        return

    cache_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"

    try:
        redis_client.setex(
            cache_key,
            IDEMPOTENCY_TTL_SECONDS,
            json.dumps(result)
        )
        logger.info(f"Cached payment result: {idempotency_key}")
    except Exception as e:
        logger.error(f"Idempotency cache write failed: {e}")


def clear_idempotency_cache(idempotency_key: str) -> None:
    """Clear cached result for a payment (rarely needed, e.g., on refund)."""
    if not idempotency_key:
        return

    cache_key = f"{IDEMPOTENCY_KEY_PREFIX}{idempotency_key}"

    try:
        redis_client.delete(cache_key)
        logger.info(f"Cleared idempotency cache: {idempotency_key}")
    except Exception as e:
        logger.error(f"Idempotency cache clear failed: {e}")
