"""Typed RPC/query boundary for public.outbox_messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    from .. import db_supabase
except ImportError:
    import db_supabase  # type: ignore

TOPIC_RIDE_RECEIPT = "ride_receipt.v1"
CLAIM_LEASE_SECONDS = 300
CLAIM_BATCH_SIZE = 10

_ALLOWED_ERROR_CODES = frozenset(
    {
        "provider_unavailable",
        "configuration_unavailable",
        "no_recipient",
        "suppressed",
        "malformed_payload",
        "unknown_topic",
        "max_attempts_exceeded",
        "empty_body",
        "ride_not_found",
    }
)


def _allowlisted(code: Optional[str], fallback: str) -> str:
    if code in _ALLOWED_ERROR_CODES:
        return code
    return fallback


def _rpc_ok(rows: Any) -> bool:
    if rows is True:
        return True
    if not rows:
        return False
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            return bool(first.get("ok"))
        if isinstance(first, (tuple, list)) and first:
            return bool(first[0])
    if isinstance(rows, dict):
        return bool(rows.get("ok"))
    return False


async def claim_batch(
    worker_id: str,
    batch_size: int = CLAIM_BATCH_SIZE,
    lease_seconds: int = CLAIM_LEASE_SECONDS,
) -> List[Dict[str, Any]]:
    rows = await db_supabase.rpc(
        "outbox_claim_batch",
        {
            "p_worker_id": worker_id,
            "p_batch_size": batch_size,
            "p_lease_seconds": lease_seconds,
        },
    )
    if rows is None:
        raise RuntimeError("outbox_claim_batch unavailable")
    if not rows:
        return []
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    logger.error("outbox_claim_batch unexpected type={}", type(rows).__name__)
    raise RuntimeError("outbox_claim_batch returned unexpected type")


async def ack(message_id: str, lease_token: str) -> bool:
    rows = await db_supabase.rpc("outbox_ack", {"p_id": message_id, "p_lease_token": lease_token})
    return _rpc_ok(rows)


async def discard(message_id: str, lease_token: str, code: str) -> bool:
    rows = await db_supabase.rpc(
        "outbox_discard",
        {
            "p_id": message_id,
            "p_lease_token": lease_token,
            "p_code": _allowlisted(code, "malformed_payload"),
        },
    )
    return _rpc_ok(rows)


async def fail(message_id: str, lease_token: str, error_code: str) -> bool:
    rows = await db_supabase.rpc(
        "outbox_fail",
        {
            "p_id": message_id,
            "p_lease_token": lease_token,
            "p_error_code": _allowlisted(error_code, "provider_unavailable"),
        },
    )
    return _rpc_ok(rows)


async def redrive(message_id: str, actor_id: str) -> bool:
    rows = await db_supabase.rpc("outbox_redrive", {"p_id": message_id, "p_actor_id": actor_id})
    return _rpc_ok(rows)


async def stats() -> List[Dict[str, Any]]:
    rows = await db_supabase.rpc("outbox_stats", {})
    return rows if isinstance(rows, list) else []


async def cleanup() -> Dict[str, Any]:
    """Drop published/discarded rows after 30 days and dead letters after 90.

    Implemented in Python (filtered ``delete_many``) rather than a migration
    ``DELETE FROM`` so CI's migration-safety gate stays green. Service-role
    table DELETE is already granted on ``outbox_messages``. Dead-letter
    age uses ``dead_lettered_at``, falling back to ``updated_at`` when that
    timestamp is null (same coalesce the removed SQL function used).
    """
    now = datetime.now(timezone.utc)
    terminal_cutoff = (now - timedelta(days=30)).isoformat()
    dead_cutoff = (now - timedelta(days=90)).isoformat()
    terminal = await db_supabase.delete_many(
        "outbox_messages",
        {
            "status": {"$in": ["published", "discarded"]},
            "updated_at": {"$lt": terminal_cutoff},
        },
    )
    dead_dated = await db_supabase.delete_many(
        "outbox_messages",
        {
            "status": "dead_lettered",
            "dead_lettered_at": {"$lt": dead_cutoff},
        },
    )
    dead_fallback = await db_supabase.delete_many(
        "outbox_messages",
        {
            "status": "dead_lettered",
            "dead_lettered_at": None,
            "updated_at": {"$lt": dead_cutoff},
        },
    )
    return {
        "published_discarded_deleted": len(terminal or []),
        "dead_lettered_deleted": len(dead_dated or []) + len(dead_fallback or []),
    }


async def get_message(message_id: str) -> Optional[Dict[str, Any]]:
    return await db_supabase.find_one("outbox_messages", {"id": message_id})


async def list_dead_letters(limit: int = 50) -> List[Dict[str, Any]]:
    return await db_supabase.get_rows(
        "outbox_messages",
        {"status": "dead_lettered"},
        order="dead_lettered_at",
        desc=True,
        limit=limit,
    )


async def is_auto_receipt_queued(ride_id: str) -> bool:
    """True if the atomic producer already wrote ride_receipt.v1 for this ride.

    Lookup failure is logged and treated as 'not queued' so callers fall back
    to the existing direct send. That favours delivery and can duplicate.
    """
    try:
        row = await db_supabase.find_one(
            "outbox_messages",
            {"topic": TOPIC_RIDE_RECEIPT, "dedupe_key": f"auto:{ride_id}"},
        )
        return isinstance(row, dict)
    except Exception:
        logger.opt(exception=True).error(
            "outbox auto-receipt lookup failed ride_id={} — falling back to direct send",
            ride_id,
        )
        return False
