"""Durable idempotent records for provider operations on a ride."""
from __future__ import annotations

import asyncio
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
    if len(prior) >= MAX_ATTEMPTS:
        last = prior[0]
        if last.get("status") != "exhausted":
            await update_operation(str(last["id"]), status="exhausted", next_attempt_at=None,
                                   last_error="Refund operation retry limit reached")
            last = {**last, "status": "exhausted"}
        return last
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


async def reconcile_due_operations() -> int:
    """Reconcile bounded due operations using provider reads before retries."""
    now = datetime.now(timezone.utc).isoformat()
    due = await db.get_rows(TABLE, {
        "status": {"$in": ["requested", "pending", "failed", "processing"]},
        "next_attempt_at": {"$lte": now},
    }, order="created_at", limit=50)
    processed = 0
    for candidate in due or []:
        prior_status = candidate.get("status")
        operation = await claim_due_operation(candidate)
        if not operation:
            continue
        processed += 1
        op_id = str(operation["id"])
        try:
            ride_id = str(operation["ride_id"])
            if operation.get("operation_type") == "authorization_release":
                try:
                    from .stripe_charge import cancel_authorization
                except ImportError:  # pragma: no cover
                    from utils.stripe_charge import cancel_authorization  # type: ignore
                ok = await cancel_authorization(
                    ride_id=ride_id, payment_intent_id=str(operation.get("payment_intent_id") or "")
                )
                if ok:
                    await update_operation(op_id, status="succeeded", next_attempt_at=None, last_error=None)
                else:
                    await schedule_retry(op_id, attempt_count=int(operation["attempt_count"]), error="Authorization release not confirmed")
                continue

            if operation.get("operation_type") == "scheduled_notice_fee":
                # A provider read is the only recovery action. Never charge a
                # different card or create a second fee during reconciliation.
                if not operation.get("payment_intent_id"):
                    metadata = operation.get("metadata") or {}
                    outcome = metadata.get("outcome_status")
                    if outcome:
                        amount = int(metadata.get("collected_cents") or 0)
                        await db.update_one("rides", {"id": ride_id}, {
                            "scheduled_notice_fee_amount": str(__import__("decimal").Decimal(amount) / 100),
                            "scheduled_notice_fee_status": "paid" if outcome == "succeeded" else outcome,
                            "scheduled_notice_fee_payment_intent_id": None,
                        })
                        await update_operation(op_id, status=outcome, next_attempt_at=None)
                    else:
                        await update_operation(op_id, status="requires_action", next_attempt_at=None,
                                               last_error="Fee has no provider reference; manual review required")
                    continue
                try:
                    from .stripe_charge import _resolve_stripe_secret, stripe
                except ImportError:  # pragma: no cover
                    from utils.stripe_charge import _resolve_stripe_secret, stripe  # type: ignore
                secret = await _resolve_stripe_secret(ride_id)
                if stripe is None or not secret:
                    raise RuntimeError("Stripe is not configured for fee reconciliation")
                pi = await asyncio.to_thread(stripe.PaymentIntent.retrieve, operation["payment_intent_id"], api_key=secret)
                pi_status = str(getattr(pi, "status", "") or "")
                if pi_status == "succeeded":
                    amount = int(getattr(pi, "amount_received", 0) or 0)
                    await db.update_one("rides", {"id": ride_id}, {
                        "scheduled_notice_fee_amount": str(__import__("decimal").Decimal(amount) / 100),
                        "scheduled_notice_fee_status": "paid",
                        "scheduled_notice_fee_payment_intent_id": operation["payment_intent_id"],
                    })
                    await update_operation(op_id, status="succeeded", collected_cents=amount, next_attempt_at=None)
                elif pi_status in {"requires_action", "requires_payment_method"}:
                    await db.update_one("rides", {"id": ride_id}, {
                        "scheduled_notice_fee_amount": "0.00",
                        "scheduled_notice_fee_status": "requires_action" if pi_status == "requires_action" else "failed",
                        "scheduled_notice_fee_payment_intent_id": operation["payment_intent_id"],
                    })
                    await update_operation(op_id, status="requires_action" if pi_status == "requires_action" else "failed",
                                           next_attempt_at=None, last_error=f"PaymentIntent status: {pi_status}")
                else:
                    await schedule_retry(op_id, attempt_count=int(operation["attempt_count"]), error=f"PaymentIntent status: {pi_status}")
                continue

            # Refund: retrieve the saved object first. If the create response
            # was lost, the stable key makes repeating create safe. A terminal
            # failed attempt advances to a new persisted attempt.
            if operation.get("operation_type") == "refund":
                try:
                    from .stripe_charge import _resolve_stripe_secret, stripe
                except ImportError:  # pragma: no cover
                    from utils.stripe_charge import _resolve_stripe_secret, stripe  # type: ignore
                if prior_status == "failed":
                    await update_operation(op_id, status="failed", next_attempt_at=now)
                    operation = await prepare_refund_operation(
                        ride_id=ride_id, payment_intent_id=str(operation.get("payment_intent_id")),
                        amount_cents=int(operation.get("amount_cents") or 0),
                    )
                    op_id = str(operation["id"])
                    if operation.get("status") == "exhausted":
                        continue
                secret = await _resolve_stripe_secret(ride_id)
                if stripe is None or not secret:
                    raise RuntimeError("Stripe is not configured for refund reconciliation")
                refund = None
                if operation.get("provider_object_id"):
                    refund = await asyncio.to_thread(stripe.Refund.retrieve, operation["provider_object_id"], api_key=secret)
                else:
                    refunds = await asyncio.to_thread(
                        stripe.Refund.list,
                        payment_intent=operation["payment_intent_id"], limit=100, api_key=secret,
                    )
                    for possible in getattr(refunds, "data", None) or []:
                        metadata = getattr(possible, "metadata", {}) or {}
                        if metadata.get("ride_payment_operation_id") == op_id:
                            refund = possible
                            break
                    if refund is None:
                        refund = await asyncio.to_thread(
                            stripe.Refund.create,
                            payment_intent=operation["payment_intent_id"],
                            amount=int(operation["amount_cents"]), reason="requested_by_customer",
                            metadata={"ride_id": ride_id, "ride_payment_operation_id": op_id},
                            api_key=secret, idempotency_key=operation["idempotency_key"],
                        )
                status = str(getattr(refund, "status", "pending") or "pending")
                refund_id = getattr(refund, "id", None)
                amount = int(getattr(refund, "amount", operation["amount_cents"]) or operation["amount_cents"])
                stored_status = status if status in {"pending", "succeeded", "failed", "canceled", "requires_action"} else "pending"
                await update_operation(op_id, provider_object_id=refund_id, status=stored_status,
                                       collected_cents=amount,
                                       next_attempt_at=None if stored_status == "succeeded" else
                                       (datetime.now(timezone.utc) + timedelta(minutes=RETRY_MINUTES[0])).isoformat())
                await db.update_one("rides", {"id": ride_id}, {
                    "refund_id": refund_id, "refund_status": stored_status,
                })
                continue
            await update_operation(op_id, status="exhausted", next_attempt_at=None,
                                   last_error="Unsupported operation type")
        except Exception as exc:
            await schedule_retry(op_id, attempt_count=int(operation.get("attempt_count") or 1), error=str(exc))
    return processed
