"""Corporate Stripe customer identity — creation, mode drift, and repair.

`corporate_accounts.stripe_customer_id` suffers the same test→live drift as the
rider and driver identities (see `utils/stripe_mode.py`): Stripe scopes object
IDs per mode, so rotating `app_settings.stripe_secret_key` across modes leaves
every stored corporate customer pointing at something the new key cannot see,
and wallet top-ups / subscription renewals start failing `resource_missing`.

The corporate surface differs from the rider one in a way that decides the
whole design: **some of these paths run with nobody present.** The auto-topup
loop charges a company's stored card on a schedule. So this module deliberately
offers two different behaviours rather than one:

- :func:`with_corporate_customer_repair` — for paths driven by a Spinr admin or
  a company billing admin. Mints a replacement customer, exactly like the rider
  path. Safe because a fresh customer has no card attached, so nothing can be
  charged until someone deliberately adds one.

- :func:`retire_corporate_customer` — for background loops. Archives the dead ID
  and nulls the active one, and **never creates a replacement**. That is not
  timidity: a new customer would have no payment method, so the charge the loop
  wanted to make cannot succeed either way. Retiring converts an endless
  10-minutely Stripe failure into a single clean "no Stripe customer" state that
  the next admin-present path re-provisions, and that the admin dashboard's
  existing 409 already reports.

Both honour the `stripe_reprovision_stale_ids` kill switch and both are only
ever reached with positive proof the ID is unusable — an explicit mode
disagreement, or `resource_missing` from Stripe for that exact object. Never on
an auth or transient error; a revoked key makes every customer look missing.

A company's legal name and billing email ARE sent to Stripe here, unlike the
rider path. That is deliberate and not a PIPEDA divergence: those identify a
business entity, not a natural person, and Stripe needs them for the invoices
and receipts a corporate account expects.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import stripe

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from ..utils.error_handling import ErrorCode, SpinrException
    from ..utils.stripe_mode import is_missing_on_key, key_mode, object_mode, stale_by_mode
except ImportError:  # pragma: no cover - dual-import pattern, see CLAUDE.md
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.error_handling import ErrorCode, SpinrException  # type: ignore
    from utils.stripe_mode import is_missing_on_key, key_mode, object_mode, stale_by_mode  # type: ignore

logger = logging.getLogger(__name__)

# Same kill switch as the rider/driver paths — one flag turns off every
# identity mutation across all three surfaces (routes/payments.py owns the
# canonical comment).
_REPROVISION_FLAG = "stripe_reprovision_stale_ids"


class CorporateCustomerUnavailable(SpinrException):
    """The company's Stripe customer is unusable and was not replaced.

    Raised when the kill switch is off, or when a repair lost a race. Callers
    must surface this, never swallow it — a corporate billing failure that is
    logged and forgotten drains a company's wallet until their riders start
    being refused.

    A ``SpinrException`` rather than a bare ``RuntimeError`` so the registered
    global handler turns it into a deliberate 503 with a usable message. As a
    plain RuntimeError it reached the three corporate routes uncaught and
    became an opaque 500, which made the documented kill-switch rollback look
    like a crash instead of the intended "temporarily unavailable".
    """

    def __init__(self, message: str = "Corporate payment profile is unavailable"):
        super().__init__(
            message=message,
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
        )


async def _reprovision_enabled() -> bool:
    settings = await get_app_settings()
    return bool(settings.get(_REPROVISION_FLAG, True))


def corporate_customer_is_stale(company: Dict[str, Any], stripe_secret: str) -> bool:
    """True when the stamped customer mode disagrees with the running key.

    Cheap: no Stripe call. Unstamped rows (everything predating migration 286)
    return False and are caught by the ``resource_missing`` path instead.
    """
    return stale_by_mode(company.get("stripe_customer_id_mode"), key_mode(stripe_secret))


async def retire_corporate_customer(company: Dict[str, Any], stale_customer_id: str, reason: str) -> None:
    """Archive an unusable corporate customer WITHOUT creating a replacement.

    The background-safe half of this module. See the module docstring for why
    a background caller must not mint a new payment identity.

    The write filters on the stale ID so a concurrent retire, or an
    admin-present path that already re-provisioned, is never clobbered.
    """
    if not await _reprovision_enabled():
        logger.error(
            "Stale corporate Stripe customer detected but %s is off — not retiring",
            _REPROVISION_FLAG,
            extra={"company_id": company.get("id"), "reason": reason},
        )
        raise CorporateCustomerUnavailable(f"customer {stale_customer_id} unusable; repair disabled by flag")

    await db_supabase.update_one(
        "corporate_accounts",
        {"id": company["id"], "stripe_customer_id": stale_customer_id},
        {
            "stripe_customer_id": None,
            "stripe_customer_id_mode": None,
            "stripe_customer_id_superseded": stale_customer_id,
            "stripe_customer_id_superseded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    # Corporate billing is money-moving and this leaves the company unable to
    # top up until an admin acts, so it is an actionable failure (error), not a
    # recovered degradation (warning) — cf. CLAUDE.md's observability rules.
    logger.error(
        "Retired unreachable corporate Stripe customer; company cannot be charged until "
        "an admin re-adds a billing card",
        extra={
            "company_id": company.get("id"),
            "reason": reason,
            "superseded_customer_id": stale_customer_id,
        },
    )


async def _create_corporate_customer(
    company: Dict[str, Any],
    stripe_secret: str,
    *,
    superseded: Optional[str] = None,
) -> str:
    """Create the company's Stripe customer and persist it with its mode."""
    company_id = company["id"]
    metadata: Dict[str, str] = {"corporate_account_id": company_id}
    if superseded:
        metadata["superseded_customer"] = superseded

    # Replacing a retired customer must NOT reuse the first-time key: replaying
    # it inside Stripe's 24 h window would hand back the very customer we just
    # proved unusable. Keying on the dead ID also makes concurrent callers and
    # retries converge on ONE replacement.
    idem = f"cus-create-corp-{company_id}" if not superseded else f"cus-reprov-corp-{company_id}-{superseded}"

    customer = await asyncio.to_thread(
        lambda: stripe.Customer.create(
            email=company.get("billing_email"),
            name=company.get("legal_name") or company.get("name"),
            metadata=metadata,
            api_key=stripe_secret,
            idempotency_key=idem,
        )
    )

    # Stamp from the object's own `livemode`, not the key we sent — evidence
    # over inference, and what lets the next rotation be detected for free.
    update: Dict[str, Any] = {
        "stripe_customer_id": customer.id,
        "stripe_customer_id_mode": object_mode(customer) or key_mode(stripe_secret),
    }
    if superseded:
        # Record the provenance the migration-286 columns exist for. Without
        # this a repaired company is indistinguishable from one that simply
        # never had a customer, which is exactly the distinction finance needs
        # when reconciling against the old Stripe account.
        update["stripe_customer_id_superseded"] = superseded
        update["stripe_customer_id_superseded_at"] = datetime.now(timezone.utc).isoformat()

    # Conditional on the value we set out to replace, so a concurrent retire
    # (the auto-topup loop) or another admin's repair is never clobbered. The
    # first-time create is unconditional because there is nothing to race with.
    filters: Dict[str, Any] = {"id": company_id}
    if superseded:
        filters["stripe_customer_id"] = superseded

    await db_supabase.update_one("corporate_accounts", filters, update)

    if not superseded:
        return customer.id

    # The conditional write may have matched zero rows — the auto-topup loop
    # retired the customer, or another admin repaired it, between our read and
    # this write. Returning our own id then would charge a customer the row
    # does not point at and leak an orphan. Re-read and defer to the winner;
    # our customer is unused but harmless (no card is ever attached to it).
    fresh = await db_supabase.find_one("corporate_accounts", {"id": company_id})
    winner = (fresh or {}).get("stripe_customer_id")
    if winner and winner != customer.id:
        logger.warning(
            "Concurrent corporate customer repair — deferring to the persisted id",
            extra={"company_id": company_id, "ours": customer.id, "persisted": winner},
        )
        return winner
    if not winner:
        # Someone retired it out from under us and nothing replaced it. Do not
        # hand back an id the row does not carry.
        raise CorporateCustomerUnavailable(f"corporate customer for {company_id} was retired concurrently; retry")
    return winner


async def get_or_create_corporate_customer(company: Dict[str, Any], stripe_secret: str) -> str:
    """Return the company's Stripe customer id, creating or repairing as needed.

    Admin-present paths only (KYB approval, subscription assignment, manual
    top-up). Adds no Stripe round-trip for a healthy customer: a stamped row
    whose mode disagrees with the key is repaired from the stamp alone, and an
    unstamped stale row is caught by :func:`with_corporate_customer_repair` on
    the call that actually fails.
    """
    customer_id = company.get("stripe_customer_id")
    if customer_id:
        if not corporate_customer_is_stale(company, stripe_secret):
            return customer_id
        if not await _reprovision_enabled():
            raise CorporateCustomerUnavailable(f"customer {customer_id} unusable; repair disabled by flag")
        logger.warning(
            "Re-provisioning corporate Stripe customer after mode drift",
            extra={"company_id": company.get("id"), "superseded_customer_id": customer_id},
        )
        return await _create_corporate_customer(company, stripe_secret, superseded=customer_id)
    return await _create_corporate_customer(company, stripe_secret)


async def with_corporate_customer_repair(company: Dict[str, Any], stripe_secret: str, op):
    """Run ``await op(customer_id)``, repairing a stranded customer once.

    Admin-present paths only. On ``resource_missing`` the customer is replaced
    and ``op`` re-runs against the new one — which for a top-up means the retry
    surfaces the honest "no payment method on file" rather than an opaque
    Stripe error, because the company's card lived on the customer that is gone.

    Any other error — auth, rate limit, connection, card decline — propagates
    untouched and writes nothing.
    """
    customer_id = await get_or_create_corporate_customer(company, stripe_secret)
    try:
        return customer_id, await op(customer_id)
    except Exception as e:
        if not is_missing_on_key(e, customer_id):
            raise
        if not await _reprovision_enabled():
            raise CorporateCustomerUnavailable(f"customer {customer_id} unusable; repair disabled by flag") from e
        logger.warning(
            "Corporate Stripe customer not reachable on the current key — re-provisioning",
            extra={"company_id": company.get("id"), "superseded_customer_id": customer_id},
        )
        repaired = await _create_corporate_customer(company, stripe_secret, superseded=customer_id)
        # One retry only. If the fresh customer also 404s, the key or the
        # account is wrong, not this row — let it surface.
        return repaired, await op(repaired)
