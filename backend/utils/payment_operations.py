"""Durable idempotent records for provider operations on a ride."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

try:
    from .. import db_supabase as db
except ImportError:  # pragma: no cover - dual import
    import db_supabase as db  # type: ignore

TABLE = "ride_payment_operations"
MAX_ATTEMPTS = 8
RETRY_MINUTES = (1, 5, 15, 60, 240, 720, 1440, 1440)


async def record_operation(
    *, operation_type: str, ride_id: str, idempotency_key: str,
    amount_cents: int = 0, payment_intent_id: Optional[str] = None,
    payment_method: Optional[str] = None, status: str = "requested",
    collected_cents: int = 0, metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert an operation once; on a uniqueness race, return its winner."""
    existing = await db.find_one(TABLE, {"idempotency_key": idempotency_key})
    if existing:
        return existing
    row = await db.insert_one(TABLE, {
        "operation_type": operation_type, "ride_id": ride_id,
        "idempotency_key": idempotency_key, "payment_intent_id": payment_intent_id,
        "amount_cents": int(amount_cents), "collected_cents": int(collected_cents),
        "payment_method": payment_method, "status": status,
        "attempt_count": 0, "next_attempt_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
    })
    if row:
        return row
    # Insert can lose a cross-replica unique-key race. Re-read the winner;
    # if it is absent, the database failure is real and must reach the caller.
    existing = await db.find_one(TABLE, {"idempotency_key": idempotency_key})
    if existing:
        return existing
    raise RuntimeError("Payment operation could not be durably recorded")


async def prepare_refund_operation(*, ride_id: str, payment_intent_id: str, amount_cents: int) -> Dict[str, Any]:
    """Return the live attempt or durably create the next retry before Stripe."""
    prior = await db.get_rows(TABLE, {
        "operation_type": "refund", "ride_id": ride_id,
        "payment_intent_id": payment_intent_id, "amount_cents": int(amount_cents),
    }, order="created_at", desc=True, limit=20)
    prior = prior or []
    for operation in prior:
        if operation.get("status") in {"requested", "processing", "pending", "requires_action", "succeeded"}:
            return operation
    attempt = len(prior) + 1
    # Each terminal failed attempt has its own stable key. Replaying a
    # requested attempt reuses its key; a confirmed failure can safely advance.
    key = f"ride-cancelrefund-{ride_id}-{amount_cents}-a{attempt}"
    return await record_operation(
        operation_type="refund", ride_id=ride_id, payment_intent_id=payment_intent_id,
        amount_cents=amount_cents, idempotency_key=key, status="requested",
    )


async def update_operation(operation_id: str, **changes: Any) -> Optional[Dict[str, Any]]:
    changes["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.update_one(TABLE, {"id": operation_id}, changes)
    if result is None:
        raise RuntimeError("Payment operation update was not persisted")
    return result


async def claim_due_operation(operation: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """CAS claim by state and attempt count so concurrent workers do one call."""
    attempt = int(operation.get("attempt_count") or 0)
    if attempt >= MAX_ATTEMPTS:
        await update_operation(str(operation["id"]), status="exhausted", next_attempt_at=None)
        return None
    claimed = await db.update_one(TABLE, {
        "id": operation["id"], "attempt_count": attempt,
        "status": operation.get("status"),
    }, {
        "status": "processing", "attempt_count": attempt + 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return claimed


async def schedule_retry(operation_id: str, *, attempt_count: int, error: str) -> None:
    if attempt_count >= MAX_ATTEMPTS:
        await update_operation(operation_id, status="exhausted", next_attempt_at=None, last_error=error[:1000])
        return
    delay = RETRY_MINUTES[min(attempt_count - 1, len(RETRY_MINUTES) - 1)]
    next_at = datetime.now(timezone.utc) + timedelta(minutes=delay)
    await update_operation(operation_id, status="failed", next_attempt_at=next_at.isoformat(), last_error=error[:1000])
