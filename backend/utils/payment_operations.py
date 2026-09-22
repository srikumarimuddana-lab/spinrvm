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
CLAIM_LEASE_MINUTES = 10
PENDING_POLL_MINUTES = 15
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


async def prepare_refund_operation(*, ride_id: str, payment_intent_id: str, amount_cents: int,
                                  allow_terminal_advance: bool = False) -> Dict[str, Any]:
    """Return the live attempt or durably create a retry before Stripe.

    Terminal attempts advance only when the reconciliation worker opts in
    after verifying the provider object and the complete Refund page.
    """
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
    if prior and prior[0].get("status") in {"failed", "canceled"} and not allow_terminal_advance:
        # User-facing requests may replay a terminal object, but only the
        # reconciler may advance after retrieving Stripe and checking that no
        # active Refund exists for the PaymentIntent.
        return prior[0]
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
    """CAS claim by state, attempt budget, and due time; poll count is unlimited."""
    attempt = int(operation.get("attempt_count") or 0)
    claimed = await db.update_one(TABLE, {
        "id": operation["id"], "attempt_count": attempt,
        "status": operation.get("status"),
        "next_attempt_at": operation.get("next_attempt_at"),
    }, {
        "status": "processing",
        "next_attempt_at": (datetime.now(timezone.utc) + timedelta(minutes=CLAIM_LEASE_MINUTES)).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return claimed


async def schedule_retry(operation: Dict[str, Any], *, error: str) -> None:
    """Back off retryable exceptions; only exceptions spend the error budget."""
    operation_id = str(operation["id"])
    metadata = dict(operation.get("metadata") or {})
    error_attempt = int(metadata.get("error_attempt_count") or 0) + 1
    metadata["error_attempt_count"] = error_attempt
    if error_attempt >= MAX_ATTEMPTS:
        await update_operation(operation_id, status="exhausted", next_attempt_at=None,
                               last_error=error[:1000], metadata=metadata)
        return
    delay = RETRY_MINUTES[min(error_attempt - 1, len(RETRY_MINUTES) - 1)]
    next_at = datetime.now(timezone.utc) + timedelta(minutes=delay)
    # A retryable provider/transport error is ambiguous. Keep the same durable
    # operation and idempotency key, then reconcile its provider object first.
    await update_operation(operation_id, status="pending", next_attempt_at=next_at.isoformat(),
                           last_error=error[:1000], metadata=metadata)


async def schedule_poll(operation: Dict[str, Any], *, delay_minutes: int = PENDING_POLL_MINUTES,
                        **changes: Any) -> None:
    """Schedule ordinary provider-pending status polling without spending retries."""
    next_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
    metadata = dict(operation.get("metadata") or {})
    metadata["error_attempt_count"] = 0
    await update_operation(str(operation["id"]), status="pending", next_attempt_at=next_at.isoformat(),
                           last_error=None, metadata=metadata, **changes)


async def finalize_refund_success(operation: Dict[str, Any]) -> None:
    """Rebuild ride refund totals and ledger from Stripe's confirmed aggregate.

    The ride update and append-only ledger are separate writes. Re-entry repairs
    either crash window: it CASes the ride total, then compares ledger cents to
    the total and writes only the missing amount under the canonical cumulative
    Stripe dedupe key.
    """
    ride_id = str(operation["ride_id"])
    payment_intent_id = str(operation["payment_intent_id"])
    try:
        from .stripe_charge import read_capture_state
    except ImportError:  # pragma: no cover
        from utils.stripe_charge import read_capture_state  # type: ignore
    capture = await read_capture_state(ride_id=ride_id, payment_intent_id=payment_intent_id)
    if not capture:
        raise RuntimeError("Refund aggregate is unavailable or paginated; keep operation retryable")
    cumulative = int(capture.get("refunded_cents") or 0)
    provider_refund_id = operation.get("provider_object_id")
    if cumulative <= 0 or (provider_refund_id and
                           provider_refund_id not in (capture.get("succeeded_refund_ids") or [])):
        raise RuntimeError("Stripe refund list has not confirmed this succeeded refund yet")
    try:
        from ..services.payment_service import apply_confirmed_stripe_refund
    except ImportError:  # pragma: no cover
        from services.payment_service import apply_confirmed_stripe_refund  # type: ignore
    result = await apply_confirmed_stripe_refund(
        ride_id=ride_id, payment_intent_id=payment_intent_id,
        cumulative_refunded_cents=cumulative,
        captured_cents=int(capture.get("captured_cents") or 0),
    )
    if result.get("outcome") == "stale":
        raise RuntimeError("Refund accounting is ahead of Stripe; manual review required")


async def reconcile_due_operations() -> int:
    """Reconcile bounded due operations using provider reads before retries."""
    now = datetime.now(timezone.utc).isoformat()
    due = await db.get_rows(TABLE, {
        "status": {"$in": ["requested", "pending", "failed", "canceled", "processing"]},
        "next_attempt_at": {"$lte": now},
    }, order="created_at", limit=50)
    processed = 0
    for candidate in due or []:
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
                    await schedule_retry(operation, error="Authorization release not confirmed")
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
                            "scheduled_notice_fee_payment_intent_id": metadata.get("provider_reference"),
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
                    await schedule_poll(operation)
                continue

            # Refund: retrieve the saved object first. If the create response
            # was lost, the stable key makes repeating create safe. A terminal
            # failed attempt advances to a new persisted attempt.
            if operation.get("operation_type") == "refund":
                try:
                    from .stripe_charge import _resolve_stripe_secret, stripe
                except ImportError:  # pragma: no cover
                    from utils.stripe_charge import _resolve_stripe_secret, stripe  # type: ignore
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
                    if refund is None and getattr(refunds, "has_more", False):
                        raise RuntimeError("Refund history exceeds one page; refusing unsafe create")
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
                if status in {"failed", "canceled"}:
                    if not operation.get("provider_object_id") and getattr(refunds, "has_more", False):
                        await update_operation(op_id, status="requires_action", next_attempt_at=None,
                                               provider_object_id=refund_id,
                                               last_error="Refund history exceeds one page; manual review required")
                        continue
                    # Advance only after Stripe has confirmed the previous
                    # object is terminal. A retrieve/list exception leaves the
                    # same operation pending via schedule_retry().
                    await update_operation(op_id, provider_object_id=refund_id, status=status,
                                           collected_cents=amount, next_attempt_at=None,
                                           last_error=f"Stripe confirmed refund {status}")
                    operation = await prepare_refund_operation(
                        ride_id=ride_id, payment_intent_id=str(operation.get("payment_intent_id")),
                        amount_cents=int(operation.get("amount_cents") or 0), allow_terminal_advance=True,
                    )
                    continue
                stored_status = status if status in {"pending", "succeeded", "failed", "canceled", "requires_action"} else "pending"
                if stored_status == "succeeded":
                    metadata = dict(operation.get("metadata") or {})
                    metadata["error_attempt_count"] = 0
                    await update_operation(op_id, provider_object_id=refund_id, status="pending",
                                           collected_cents=amount,
                                           next_attempt_at=datetime.now(timezone.utc).isoformat(), metadata=metadata)
                    operation = {**operation, "provider_object_id": refund_id, "metadata": metadata}
                    await finalize_refund_success(operation)
                    await update_operation(op_id, status="succeeded", next_attempt_at=None,
                                           collected_cents=amount, last_error=None)
                    continue
                if stored_status == "pending":
                    await schedule_poll(operation, provider_object_id=refund_id, collected_cents=amount)
                    operation = {**operation, "provider_object_id": refund_id,
                                 "metadata": {**(operation.get("metadata") or {}), "error_attempt_count": 0}}
                else:
                    metadata = dict(operation.get("metadata") or {})
                    metadata["error_attempt_count"] = 0
                    await update_operation(op_id, provider_object_id=refund_id, status=stored_status,
                                           collected_cents=amount, next_attempt_at=None, metadata=metadata)
                await db.update_one("rides", {"id": ride_id}, {
                    "refund_id": refund_id, "refund_status": stored_status,
                })
                continue
            await update_operation(op_id, status="exhausted", next_attempt_at=None,
                                   last_error="Unsupported operation type")
        except Exception as exc:
            await schedule_retry(operation, error=str(exc))
    return processed
