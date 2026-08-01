"""Wallet & Stripe repository — atomic wallet RPCs, promo, fare-split, Stripe events.

Extracted from db_supabase.py (Phase 4 of god-object decomposition).
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

try:
    from ._base import (
        DatabaseError,
        _serialize_for_api,
        run_sync,
        supabase,
    )
except ImportError:
    from repositories._base import (  # type: ignore
        DatabaseError,
        _serialize_for_api,
        run_sync,
        supabase,
    )


# ============ Atomic Wallet RPCs (P0-4, P0-5, P0-6) ============


async def wallet_increment_balance(wallet_id: str, amount: "Decimal") -> "Decimal":
    """Atomically increment a wallet balance. Returns the new balance."""
    from decimal import Decimal as _Decimal  # noqa: PLC0415

    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        res = supabase.rpc(
            "wallet_increment_balance",
            {"p_wallet_id": wallet_id, "p_amount": str(amount)},
        ).execute()
        data = getattr(res, "data", None)
        if data is None:
            raise DatabaseError(details={"original": "wallet_increment_balance: no data returned"})
        return _Decimal(str(data))

    return await run_sync(_fn)


async def wallet_apply_credit(
    *,
    wallet_id: str,
    user_id: str,
    type_: str,
    amount: "Decimal",
    reference_id: Optional[str],
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Idempotently credit a consumer wallet via the wallet_apply_credit RPC (C6).

    Atomic: locks the wallet row, dedups on (wallet_id, reference_id, type) so a
    Stripe webhook retried after a crash cannot double-credit, then writes the
    balance and the ledger row together. Returns the RPC row
    ``{"transaction_id", "balance_after", "deduped"}``.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    params = {
        "p_wallet_id": wallet_id,
        "p_user_id": user_id,
        "p_type": type_,
        "p_amount": str(amount),
        "p_reference_id": reference_id,
        "p_description": description,
        "p_metadata": metadata or {},
    }

    def _fn():
        res = supabase.rpc("wallet_apply_credit", params).execute()
        data = getattr(res, "data", None) or []
        if not data:
            raise DatabaseError(details={"original": "wallet_apply_credit: no row returned"})
        return data[0]

    return await run_sync(_fn)


async def wallet_apply_delta(
    *,
    wallet_id: str,
    user_id: str,
    type_: str,
    delta: "Decimal",
    reference_id: Optional[str],
    description: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    floor: Optional["Decimal"] = None,
    clamp_to_floor: bool = False,
) -> Dict[str, Any]:
    """Apply a SIGNED delta to a consumer wallet atomically (WS-6).

    Replaces the read-modify-write pattern used by admin credit/debit and the
    cancellation / no-show fee paths, where a concurrent mutation landing between
    the read and the write was silently lost. Locks the wallet row, dedups on
    (wallet_id, reference_id, type) inside the lock, then writes balance +
    ledger row together.

    ``clamp_to_floor=True`` charges only what is available down to ``floor``
    (the fee paths' existing ``max(balance - fee, 0)`` behaviour); otherwise a
    delta that would breach the floor raises. Returns the RPC row
    ``{"transaction_id", "balance_after", "applied_delta", "deduped"}`` —
    prefer ``applied_delta`` over the requested amount when recording what was
    actually charged.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    params = {
        "p_wallet_id": wallet_id,
        "p_user_id": user_id,
        "p_type": type_,
        "p_delta": str(delta),
        "p_reference_id": reference_id,
        "p_description": description,
        "p_metadata": metadata or {},
        "p_floor": str(floor) if floor is not None else None,
        "p_clamp_to_floor": clamp_to_floor,
    }

    def _fn():
        res = supabase.rpc("wallet_apply_delta", params).execute()
        data = getattr(res, "data", None) or []
        if not data:
            raise DatabaseError(details={"original": "wallet_apply_delta: no row returned"})
        return data[0]

    return await run_sync(_fn)


async def wallet_pay_for_ride(
    wallet_id: str,
    ride_id: str,
    amount: "Decimal",
    tip_amount: "Decimal" = Decimal("0"),
) -> "Optional[Decimal]":
    """Atomically debit wallet, mark ride paid, and credit tip to driver_earnings.

    tip_amount is written to rides.tip_amount and added (delta-style) to
    rides.driver_earnings inside the same Postgres transaction as the wallet
    debit, so a post-settlement Python crash cannot leave tip money collected
    but missing from driver earnings.

    Returns the new balance after debit, or None if the ride was already paid
    (idempotent no-op — the RPC returned NULL, no money moved, no ledger entry
    should be written by the caller).

    Raises ValueError('insufficient_funds') if balance < amount.
    """
    from decimal import Decimal as _Decimal  # noqa: PLC0415

    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn() -> "Optional[_Decimal]":
        try:
            res = supabase.rpc(
                "wallet_pay_for_ride",
                {
                    "p_wallet_id": wallet_id,
                    "p_ride_id": ride_id,
                    "p_amount": str(amount),
                    "p_tip_amount": str(tip_amount),
                },
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "insufficient_funds" in msg:
                raise ValueError("insufficient_funds") from exc
            if "wallet not found" in msg:
                raise ValueError("wallet_not_found") from exc
            if "fare_underpaid" in msg:
                raise ValueError("fare_underpaid") from exc
            if "ride_not_payable" in msg:
                raise ValueError("ride_not_payable") from exc
            raise
        data = getattr(res, "data", None)
        if data is None:
            # NULL from the RPC means ride already paid — idempotent no-op.
            return None
        return _Decimal(str(data))

    return await run_sync(_fn)


async def wallet_transfer(sender_id: str, recipient_id: str, amount: "Decimal") -> "tuple[Decimal, Decimal]":
    """Atomically transfer between two wallets. Returns (sender_balance, recipient_balance).

    Raises ValueError('insufficient_funds') if sender balance < amount.
    """
    from decimal import Decimal as _Decimal  # noqa: PLC0415

    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        try:
            res = supabase.rpc(
                "wallet_transfer",
                {
                    "p_sender_id": sender_id,
                    "p_recipient_id": recipient_id,
                    "p_amount": str(amount),
                },
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "insufficient_funds" in msg:
                raise ValueError("insufficient_funds") from exc
            if "wallet not found" in msg:
                raise ValueError("wallet_not_found") from exc
            raise
        data = getattr(res, "data", None)
        if not data:
            raise DatabaseError(details={"original": "wallet_transfer: no data returned"})
        row = data[0] if isinstance(data, list) else data
        return (
            _Decimal(str(row["sender_balance"])),
            _Decimal(str(row["recipient_balance"])),
        )

    return await run_sync(_fn)


async def increment_promo_uses(promo_id: str, max_uses: int) -> bool:
    """Atomically increment promo uses if uses < max_uses. Returns True if
    the promo still had capacity (row updated), False if exhausted.
    Callers should raise HTTP 409 on False.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        res = supabase.rpc(
            "increment_promo_uses",
            {"p_promo_id": promo_id, "p_max_uses": max_uses},
        ).execute()
        data = getattr(res, "data", None)
        return data is True or data == 1 or (isinstance(data, list) and len(data) > 0)

    return await run_sync(_fn)


async def claim_promo_user_slot(promo_id: str, user_id: str, max_per_user: int) -> bool:
    """Atomically claim one per-user promo redemption slot (migration 257).

    Returns True if the user was still under max_per_user (slot taken), False if
    already at the cap. This is the AUTHORITATIVE per-user gate — the
    count_documents() check in the validation path is only a friendly early
    rejection and is racy on its own. Callers raise HTTP 400 on False.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        res = supabase.rpc(
            "claim_promo_user_slot",
            {"p_promo_id": promo_id, "p_user_id": user_id, "p_max_per_user": max_per_user},
        ).execute()
        data = getattr(res, "data", None)
        return data is True or data == 1 or (isinstance(data, list) and len(data) > 0 and data[0] in (True, 1))

    return await run_sync(_fn)


async def release_promo_user_slot(promo_id: str, user_id: str) -> None:
    """Release a per-user promo slot claimed by claim_promo_user_slot when a
    later step of the same redemption fails (migration 257). Best-effort."""
    if not supabase:
        return

    def _fn():
        supabase.rpc(
            "release_promo_user_slot",
            {"p_promo_id": promo_id, "p_user_id": user_id},
        ).execute()
        return True

    await run_sync(_fn)


async def fare_split_pay_share(wallet_id: str, participant_id: str, amount: "Decimal") -> "Decimal":
    """Atomically deduct `amount` from `wallet_id` and mark `participant_id`
    as paid in a single Postgres transaction. Returns the new wallet balance.
    Raises ValueError('insufficient_funds') when balance is insufficient.
    """
    if not supabase:
        raise DatabaseError(details={"original": "supabase not initialised"})

    def _fn():
        try:
            res = supabase.rpc(
                "fare_split_pay_share",
                {
                    "p_wallet_id": wallet_id,
                    "p_participant_id": participant_id,
                    "p_amount": str(amount),
                },
            ).execute()
        except Exception as exc:
            msg = str(exc).lower()
            if "insufficient_funds" in msg:
                raise ValueError("insufficient_funds") from exc
            raise
        data = getattr(res, "data", None)
        if data is None:
            raise DatabaseError(details={"original": "fare_split_pay_share returned no data"})
        return data

    try:
        raw = await run_sync(_fn)
        return Decimal(str(raw))
    except Exception as exc:
        msg = str(exc)
        if "insufficient_funds" in msg:
            raise ValueError("insufficient_funds") from exc
        raise


# ── Stripe webhook idempotency ────────────────────────────────────────
# See migration 22_stripe_events.sql. These helpers back
# routes/webhooks.py's dedup path: Stripe retries every event until we
# return 2xx within 20s, so we MUST treat a replay of the same event.id
# as a no-op — otherwise we double-mark rides paid, double-credit
# wallets, and double-activate subscriptions.

# PostgreSQL unique_violation SQLSTATE — raised as part of the error
# string by postgrest-py when an INSERT conflicts with the PK.
_PG_UNIQUE_VIOLATION = "23505"


async def claim_stripe_event(event_id: str, event_type: str, payload: Dict[str, Any]) -> bool:
    """Atomically claim a Stripe webhook event for processing.

    Returns True if this call inserted the event row (caller should
    proceed to process it). Returns False if the event_id is already
    present (a retry — caller should return 200 without doing work).

    Raises if Supabase is unreachable or the error is not a unique
    violation — in that case the caller should return 5xx so Stripe
    retries later.
    """
    if not supabase:
        raise RuntimeError("Supabase client not configured — cannot persist stripe event")

    serialized_payload = _serialize_for_api(payload)

    def _fn() -> bool:
        try:
            supabase.table("stripe_events").insert(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "payload": serialized_payload,
                }
            ).execute()
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if _PG_UNIQUE_VIOLATION in msg or "duplicate key" in msg or "already exists" in msg:
                # Check if the previous claim was actually completed. A row with
                # processed_at=NULL means a prior handler crashed mid-way and Stripe
                # is retrying — log a CRITICAL so the reconciliation alert fires, but
                # still return False (do not re-process automatically to avoid
                # double-charging). The ops team can replay via the admin endpoint.
                existing = (
                    supabase.table("stripe_events").select("processed_at").eq("event_id", event_id).limit(1).execute()
                )
                if existing.data and existing.data[0].get("processed_at") is None:
                    logger.critical(
                        "Stripe event %s is STUCK: claimed but never marked processed. Manual reconciliation required.",
                        event_id,
                    )
                else:
                    logger.info("Stripe event %s already processed — deduplicating", event_id)
                return False
            raise

    return await run_sync(_fn)


async def mark_stripe_event_processed(event_id: str) -> None:
    """Stamp processed_at=now() on a previously claimed stripe event row.

    Called after the handler has finished the business-logic work for an
    event. Stripe will not retry since we already returned 2xx, so a
    failure here cannot self-heal via the retry path — the row is left
    stuck at processed_at=NULL. `utils/stripe_reconcile.py`'s daily tick
    (`_reconcile_stuck_stripe_events`, ACTION_ITEMS.md C10) surfaces rows
    like this to the audit log for manual review — detection only, it
    deliberately does not re-run business logic (this row's side effects
    already happened; replaying risks double-processing). The
    `logger.error` below is still the fast/loud signal (Sentry-bridged);
    the daily sweep is the backstop in case that signal is ever missed.
    """
    if not supabase:
        return

    def _fn():
        supabase.table("stripe_events").update({"processed_at": datetime.now(timezone.utc).isoformat()}).eq(
            "event_id", event_id
        ).execute()

    try:
        await run_sync(_fn)
    except Exception as e:  # noqa: BLE001
        logger.error(
            f"Failed to stamp processed_at on stripe event {event_id}: {e!r}. "
            "This event will remain stuck at processed_at=NULL until the "
            "daily stripe_reconcile sweep surfaces it for manual review "
            "(Stripe already got a 2xx and will not retry) -- "
            "see ACTION_ITEMS.md C10.",
            extra={"domain": "payments", "event_id": event_id},
        )


async def unclaim_stripe_event(event_id: str) -> bool:
    """Delete a claimed-but-unprocessed stripe event row so Stripe's retry
    of the same event_id can genuinely re-process it.

    claim_stripe_event dedupes retries even when processed_at is NULL, so a
    handler that fails on a TRANSIENT error (e.g. the ride row is not yet
    visible) must release its claim before returning 5xx — otherwise the
    retry is acknowledged as a duplicate and the event is lost until manual
    replay. Only call this when NO side effects have been performed for the
    event.

    Returns True when the claim was released (retry path restored), False
    when the delete failed — the row stays claimed-unprocessed, Stripe's
    retry will be deduped, and the caller must escalate (the event needs a
    manual replay via the admin endpoint).
    """
    if not supabase:
        return False

    def _fn():
        supabase.table("stripe_events").delete().eq("event_id", event_id).is_("processed_at", "null").execute()

    try:
        await run_sync(_fn)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to unclaim stripe event {event_id}: {e}")
        return False
