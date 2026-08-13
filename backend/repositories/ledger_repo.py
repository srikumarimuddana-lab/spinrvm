"""Ledger repository — atomic settlement RPC wrapper.

Bespoke wrapper (not the generic ``_base.rpc``) for the same reason
``wallet_repo`` wraps its money RPCs individually: the generic helper
returns ``None`` silently when Supabase is unconfigured and does no
error translation, both of which are unacceptable on a money path.
"""

from decimal import Decimal
from typing import Any, Dict, Optional

try:
    from ._base import run_sync, supabase
except ImportError:
    from repositories._base import run_sync, supabase  # type: ignore


class SettleRpcUnavailable(ValueError):
    """The settle_ride_card_payment function is not callable — either
    migration 288 has not been applied to this database, or Supabase is
    unconfigured. Callers fall back to the legacy two-write path.

    Subclasses ValueError deliberately: ``run_sync`` wraps every exception
    except ValueError/ServiceUnavailableException into a generic
    DatabaseError, which would erase this signal (the same reason
    wallet_repo translates its RPC errors to ValueError)."""


def _is_missing_function(exc: Exception) -> bool:
    text = str(exc)
    return "PGRST202" in text or "does not exist" in text or "Could not find the function" in text


async def settle_ride_card_payment(
    *,
    ride_id: str,
    event_id: str,
    user_id: str,
    amount_cents: int,
    payment_intent_id: str,
    tip_amount: Decimal,
    metadata: Dict[str, Any],
    auth_status: Optional[str] = None,
) -> Optional[str]:
    """Atomically flip the ride to paid and insert the tax-ledger header.

    Returns the event id on success, or ``None`` when the ride was already
    paid (idempotent no-op — the caller must NOT write a second header or
    re-send receipts).

    Raises ``SettleRpcUnavailable`` when the function is absent (migration
    288 not applied) or Supabase is unconfigured — the caller's cue to use
    the legacy two-write path. Any other exception propagates as-is: the
    caller cannot know whether the transaction committed and must re-read
    the ride before deciding (ambiguous-transport-error recovery).
    """
    if not supabase:
        raise SettleRpcUnavailable("supabase not initialised")

    def _fn() -> Optional[str]:
        try:
            res = supabase.rpc(
                "settle_ride_card_payment",
                {
                    "p_ride_id": ride_id,
                    "p_event_id": event_id,
                    "p_user_id": user_id,
                    # int is JSON-safe for bigint; Decimals go as str, never float.
                    "p_amount_cents": int(amount_cents),
                    "p_payment_intent_id": payment_intent_id,
                    "p_tip_amount": str(tip_amount),
                    "p_metadata": metadata or {},
                    "p_auth_status": auth_status,
                },
            ).execute()
        except Exception as exc:
            if _is_missing_function(exc):
                raise SettleRpcUnavailable(str(exc)) from exc
            raise
        data = getattr(res, "data", None)
        if data is None:
            # NULL from the RPC: ride already paid — idempotent no-op.
            return None
        return str(data)

    # idempotent_write: a transport-level retry re-sends the SAME p_event_id,
    # which the RPC dedupes (paid-gate + ON CONFLICT(id)) — so one automatic
    # retry on an H2 GOAWAY is safe and shrinks the ambiguous-error surface
    # the caller otherwise has to resolve by re-reading the ride.
    return await run_sync(_fn, retry_policy="idempotent_write")
