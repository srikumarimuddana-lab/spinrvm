"""Stripe Tax recorder for the platform's share of ride GST.

Spinr remits the GST collected on its own ride revenue — the booking fee,
airport fee, and area fees (``tax_split.platform_*`` on the ride, migration
233). This module mirrors that platform portion into Stripe Tax as tax
transactions so the Stripe Tax report is the filing source for Spinr's
ride-side GST return. The driver's share is NEVER recorded here — drivers
are the CRA registrants for the GST on their fare share and remit it
themselves (see routes/drivers/payouts._require_gst_for_payout).

Hourly backlog scan (replay-safe per the CLAUDE.md loop recipe):
  1. Query completed+paid rides with a tax_split and NULL
     stripe_tax_transaction_id (served by the partial index from
     migration 233).
  2. For each, create a Stripe Tax Calculation for the platform taxable
     base and a Transaction from it. The Transaction ``reference`` is
     unique per ride, so a replica race or replay is rejected by Stripe
     rather than double-recorded.
  3. Stamp rides.stripe_tax_transaction_id. Zero-platform-tax rides are
     stamped with a sentinel so they leave the backlog.

Refunds: routes/webhooks charge.refunded → payment_service.record_refund_event
calls ``reverse_platform_ride_tax``. Full refunds create a mode="full" tax
reversal; partial refunds only set ``stripe_tax_reversal_pending`` in the
financial_events metadata (Stripe partial reversals need line-item
allocation that a proportional rider refund does not map onto cleanly).
NOTE: nothing consumes that flag automatically yet — finance must query
financial_events for it until the reconcile-loop surfacing ships (tracked
in docs/stripe-tax-cra-compliance.md §4 Phase 2).

Gated on the ``stripe_tax_enabled`` app setting (default OFF): enabling it
requires Spinr's GST/PST registrations to exist in the Stripe Tax dashboard
first, otherwise Stripe computes $0 tax and the recorded transactions are
useless for filing.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

import stripe

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
    from .money import dollars_to_cents, to_decimal
except ImportError:
    import db_supabase
    from settings_loader import get_app_settings
    from utils.money import dollars_to_cents, to_decimal

logger = logging.getLogger(__name__)

_SETTING_ENABLED = "stripe_tax_enabled"
_SETTING_TAX_CODE = "stripe_tax_code"
_SETTING_PROVINCE = "stripe_tax_province"

# General services. Overridable via the stripe_tax_code app setting if the
# accountant classifies platform fees under a different product tax code.
_DEFAULT_TAX_CODE = "txcd_20030000"
_DEFAULT_PROVINCE = "SK"

_SCAN_INTERVAL_SECONDS = 3600
_SCAN_BATCH = 200

# Sentinel stamped on rides whose tax_split carries no platform tax (e.g.
# tax disabled for the area) so they leave the recording backlog without a
# Stripe call. Prefixed so reconciliation can tell it from a real txn id.
NO_PLATFORM_TAX = "none:no_platform_tax"
# Stamped when Stripe rejects the reference as already used but we lost the
# txn id (crash between create and stamp). Daily reconciliation resolves it.
RECORDED_UNLINKED = "error:recorded_unlinked"


def _platform_amounts(ride: Dict[str, Any]) -> tuple[Decimal, Decimal]:
    """(platform_taxable_base, platform_tax_total) from the ride's tax_split."""
    split = ride.get("tax_split") or {}
    base = to_decimal(split.get("platform_taxable_base") or 0)
    tax = to_decimal(split.get("platform_total") or 0)
    return base, tax


async def record_platform_ride_tax(ride: Dict[str, Any]) -> Optional[str]:
    """Record the platform's tax share for one ride into Stripe Tax.

    Returns the value stamped on rides.stripe_tax_transaction_id, or None
    when recording failed and the ride should stay in the backlog.
    """
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key")
    if not settings.get(_SETTING_ENABLED) or not stripe_secret:
        return None

    ride_id = ride.get("id")
    base, expected_tax = _platform_amounts(ride)
    if base <= 0 or expected_tax <= 0:
        await db_supabase.update_one(
            "rides",
            {"id": ride_id, "stripe_tax_transaction_id": None},
            {"stripe_tax_transaction_id": NO_PLATFORM_TAX},
        )
        return NO_PLATFORM_TAX

    tax_code = settings.get(_SETTING_TAX_CODE) or _DEFAULT_TAX_CODE
    province = settings.get(_SETTING_PROVINCE) or _DEFAULT_PROVINCE
    reference = f"spinr-ride-{ride_id}-platform-tax"

    try:
        calculation = stripe.tax.Calculation.create(
            api_key=stripe_secret,
            currency="cad",
            line_items=[
                {
                    "amount": dollars_to_cents(base),
                    "reference": f"ride-{ride_id}-platform-fees",
                    "tax_behavior": "exclusive",
                    "tax_code": tax_code,
                }
            ],
            customer_details={
                "address": {"country": "CA", "state": province},
                "address_source": "billing",
            },
        )
        txn = stripe.tax.Transaction.create_from_calculation(
            api_key=stripe_secret,
            calculation=calculation.id,
            reference=reference,
        )
    except stripe.StripeError as e:
        # Unique-reference rejection = a prior attempt recorded the txn but
        # the stamp write was lost. Stamp the sentinel so it leaves the
        # backlog; daily reconciliation links it back up by reference.
        if "reference" in str(e).lower() and "exist" in str(e).lower():
            logger.error(
                "stripe_tax: reference %s already recorded but unlinked — stamping for reconciliation",
                reference,
            )
            await db_supabase.update_one(
                "rides",
                {"id": ride_id, "stripe_tax_transaction_id": None},
                {"stripe_tax_transaction_id": RECORDED_UNLINKED},
            )
            return RECORDED_UNLINKED
        logger.error("stripe_tax: recording failed for ride %s: %s", ride_id, e, exc_info=True)
        return None

    # Divergence check: Stripe's computed tax vs our persisted platform
    # portion. Cent-level drift (rounding regime differences) is logged so
    # the filing source and the 7-year DB ledger reconcile explicitly.
    stripe_tax_cents = int(getattr(calculation, "tax_amount_exclusive", 0) or 0)
    expected_cents = dollars_to_cents(expected_tax)
    if stripe_tax_cents != expected_cents:
        logger.warning(
            "stripe_tax: computed tax mismatch for ride %s: stripe=%s cents, app=%s cents",
            ride_id,
            stripe_tax_cents,
            expected_cents,
        )

    await db_supabase.update_one(
        "rides",
        {"id": ride_id, "stripe_tax_transaction_id": None},
        {"stripe_tax_transaction_id": txn.id},
    )
    logger.info("stripe_tax: recorded %s for ride %s (%s cents)", txn.id, ride_id, stripe_tax_cents)
    return txn.id


async def reverse_platform_ride_tax(ride: Dict[str, Any], refund_cents: int) -> Optional[str]:
    """Reverse the recorded platform tax transaction after a rider refund.

    Full refunds reverse the whole transaction. Partial refunds return
    ``None`` after logging — the proportional rider refund does not map onto
    Stripe's line-item partial reversal, so the daily reconciliation surfaces
    those for manual adjustment. Never raises (webhook-path helper).
    """
    txn_id = ride.get("stripe_tax_transaction_id")
    if not txn_id or txn_id in (NO_PLATFORM_TAX, RECORDED_UNLINKED):
        return None

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key")
    if not stripe_secret:
        return None

    ride_id = ride.get("id")
    total = to_decimal(ride.get("grand_total") or ride.get("total_fare") or 0)
    refund = to_decimal(refund_cents) / Decimal("100")
    if total <= 0 or refund < total:
        logger.warning(
            "stripe_tax: partial refund on ride %s (refund=%s, total=%s) — tax txn %s left for manual reversal",
            ride_id,
            refund,
            total,
            txn_id,
        )
        return None

    try:
        reversal = stripe.tax.Transaction.create_reversal(
            api_key=stripe_secret,
            original_transaction=txn_id,
            mode="full",
            reference=f"spinr-ride-{ride_id}-platform-tax-reversal",
        )
    except stripe.StripeError as e:
        if "reference" in str(e).lower() and "exist" in str(e).lower():
            return None  # already reversed (webhook replay)
        logger.error("stripe_tax: reversal failed for ride %s txn %s: %s", ride_id, txn_id, e, exc_info=True)
        return None
    logger.info("stripe_tax: reversed %s for ride %s → %s", txn_id, ride_id, reversal.id)
    return reversal.id


async def _tick() -> None:
    settings = await get_app_settings()
    if not settings.get(_SETTING_ENABLED) or not settings.get("stripe_secret_key"):
        return
    rides = await db_supabase.get_rows(
        "rides",
        {
            "status": "completed",
            "payment_status": "paid",
            "stripe_tax_transaction_id": None,
            "tax_split": {"$notnull": True},
        },
        order="completed_at",
        limit=_SCAN_BATCH,
        columns="id,tax_split,grand_total,total_fare,stripe_tax_transaction_id",
    )
    for ride in rides or []:
        try:
            await record_platform_ride_tax(ride)
        except Exception:
            # One bad ride must not starve the rest of the backlog; it is
            # retried next tick because the stamp stays NULL.
            logger.error("stripe_tax: unexpected recorder failure for ride %s", ride.get("id"), exc_info=True)


async def stripe_tax_recorder_loop() -> None:
    """Hourly: mirror the platform share of ride GST into Stripe Tax.

    Reads completed+paid rides with an unrecorded tax_split; writes
    rides.stripe_tax_transaction_id. Replay-safe via the unique Stripe
    reference per ride + the NULL-stamp claim filter.
    """
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("stripe_tax_recorder tick failed", exc_info=True)
        await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
