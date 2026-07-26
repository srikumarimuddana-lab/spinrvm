"""Stripe Connect KYC mirror.

Maps a Stripe Express ``account.updated`` payload (or a live
``Account.retrieve()`` response) into the cache columns on the
``drivers`` row added by migration 92. The SIN itself is never stored —
we keep only ``id_number_provided`` (boolean) and ``id_number_last4`` so
the admin slideout can display "On file · ••••1234" without us holding
the regulated data.

Sources:
  - routes/webhooks.py  — fires apply_account_update on the webhook event
  - routes/admin/drivers.py  — admin "Refresh from Stripe" button calls
    refresh_driver_kyc, which retrieves the account live and reuses the
    same mapping
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from .. import db_supabase
    from ..settings_loader import get_app_settings
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore

logger = logging.getLogger(__name__)


def _kyc_mirror_fields(account: Dict[str, Any]) -> Dict[str, Any]:
    """Pure function: Stripe Account object → drivers.* mirror columns.

    Defensive on every nested path because Stripe trims fields based on
    the account's progress (an incomplete onboarding has no
    ``individual``, no ``business_profile``, etc.).
    """
    individual = account.get("individual") or {}
    business_profile = account.get("business_profile") or {}
    requirements = account.get("requirements") or {}
    verification = (individual.get("verification") or {}) if individual else {}
    tos = account.get("tos_acceptance") or {}

    # Stripe reports id_number_provided + id_number_last_4 on the
    # `individual` object when the SIN has been collected. The actual
    # number stays on Stripe's side.
    id_provided = bool(individual.get("id_number_provided"))
    id_last4_raw = individual.get("id_number_last_4")
    id_last4: Optional[str] = None
    if id_last4_raw and isinstance(id_last4_raw, str) and len(id_last4_raw) == 4 and id_last4_raw.isdigit():
        id_last4 = id_last4_raw

    # business_profile.tax_id is what Express captures for the Canadian
    # GST/HST registration. Not PII (it's a public business identifier).
    # Persisted into the canonical `gst_bn` column from migration 58 —
    # NOT a new gst_hst_number column, which would compete with the
    # driver-app's "Edit GST" form that writes gst_bn via /drivers/me.
    gst_bn = business_profile.get("tax_id") if business_profile else None

    # ToS acceptance timestamp comes as a Unix timestamp.
    tos_ts = tos.get("date") if tos else None
    tos_iso: Optional[str] = None
    if isinstance(tos_ts, (int, float)) and tos_ts > 0:
        tos_iso = datetime.fromtimestamp(tos_ts, tz=timezone.utc).isoformat()

    verification_status = verification.get("status") if verification else None

    return {
        "stripe_details_submitted": bool(account.get("details_submitted")),
        "stripe_charges_enabled": bool(account.get("charges_enabled")),
        "stripe_payouts_enabled": bool(account.get("payouts_enabled")),
        "stripe_id_number_provided": id_provided,
        "stripe_id_number_last4": id_last4,
        "stripe_requirements_due": list(requirements.get("currently_due") or []),
        "stripe_requirements_past_due": list(requirements.get("past_due") or []),
        "stripe_disabled_reason": requirements.get("disabled_reason"),
        "stripe_verification_status": verification_status,
        "stripe_business_type": account.get("business_type"),
        # Write the canonical gst_bn column (migration 58). Also flip
        # gst_registered when Stripe reports a tax_id, so the driver's
        # profile reflects their registered status without needing the
        # driver to retype anything they already gave Stripe.
        "gst_bn": gst_bn,
        **({"gst_registered": True} if gst_bn else {}),
        "stripe_tos_accepted_at": tos_iso,
        "stripe_last_synced_at": datetime.now(timezone.utc).isoformat(),
        # Real onboarded gate: only true once Stripe says details_submitted.
        # Replaces the old optimistic flag flipped at AccountLink creation.
        "stripe_account_onboarded": bool(account.get("details_submitted")),
    }


async def apply_account_update(account: Dict[str, Any], *, event_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Persist the KYC mirror for an ``account.updated`` payload.

    Returns the updated driver row (or None if no matching driver was
    found, which is logged at warning level — the same Stripe account
    can briefly exist before our drivers row is created).
    """
    account_id = account.get("id")
    if not account_id:
        logger.warning(
            "[STRIPE-KYC] account.updated event has no id — skipping",
            extra={"event_id": event_id},
        )
        return None

    rows = await db_supabase.get_rows("drivers", {"stripe_account_id": account_id}, limit=1)
    if not rows:
        logger.warning(
            "[STRIPE-KYC] no driver row for stripe_account_id=%s — webhook arrived before onboarding completed?",
            account_id,
            extra={"event_id": event_id},
        )
        return None

    driver = rows[0]
    updates = _kyc_mirror_fields(account)

    try:
        await db_supabase.update_one("drivers", {"id": driver["id"]}, updates)
    except Exception:
        logger.error(
            "[STRIPE-KYC] failed to persist mirror for driver=%s account=%s",
            driver["id"],
            account_id,
            exc_info=True,
            extra={"event_id": event_id},
        )
        raise

    logger.info(
        "[STRIPE-KYC] mirrored driver=%s payouts_enabled=%s details_submitted=%s id_provided=%s",
        driver["id"],
        updates["stripe_payouts_enabled"],
        updates["stripe_details_submitted"],
        updates["stripe_id_number_provided"],
        extra={"event_id": event_id, "domain": "drivers"},
    )
    return {**driver, **updates}


async def refresh_driver_kyc(driver: Dict[str, Any]) -> Dict[str, Any]:
    """Admin "Refresh from Stripe" path.

    Retrieves the live Account from Stripe and runs the same mapping the
    webhook uses. Caller is responsible for the audit_log entry — this
    function only touches Stripe + the drivers row.
    """
    account_id = driver.get("stripe_account_id")
    if not account_id:
        return {"status": "no_stripe_account"}

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        return {"status": "stripe_not_configured"}

    import stripe

    try:
        # to_thread: the Stripe SDK is blocking; batch callers (legacy Stripe
        # mapping import) refresh many drivers concurrently on the event loop.
        account = await asyncio.to_thread(stripe.Account.retrieve, account_id, api_key=stripe_secret)
    except Exception:
        logger.error(
            "[STRIPE-KYC] refresh: Account.retrieve failed for %s",
            account_id,
            exc_info=True,
        )
        return {"status": "stripe_error"}

    try:
        payload = account.to_dict_recursive()  # type: ignore[attr-defined]
    except AttributeError:
        payload = dict(account)

    updates = _kyc_mirror_fields(payload)
    await db_supabase.update_one("drivers", {"id": driver["id"]}, updates)
    return {"status": "ok", "updates": updates}


async def reveal_sin_from_stripe(driver: Dict[str, Any]) -> Optional[str]:
    """Return the plaintext SIN once, for a single audit-logged admin reveal.

    Uses Stripe's expand mechanism to retrieve ``individual.id_number``,
    which Stripe surfaces only to the platform owner and not in normal
    Account.retrieve responses. **The returned value is NEVER persisted
    anywhere on our side.** Caller MUST write the audit_log entry around
    this call so we have an authoritative record of who saw it and when.
    """
    account_id = driver.get("stripe_account_id")
    if not account_id:
        return None

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        return None

    import stripe

    try:
        account = stripe.Account.retrieve(
            account_id,
            api_key=stripe_secret,
            expand=["individual.id_number"],
        )
    except Exception:
        # B-P3-leak-cleanup: full traceback to logs (server-side only),
        # no Stripe error detail in the return.
        logger.error(
            "[STRIPE-KYC] reveal-sin: Account.retrieve failed for %s",
            account_id,
            exc_info=True,
        )
        return None

    sin = (account.get("individual") or {}).get("id_number")
    if not sin:
        return None
    # Defensive: SIN must be 9 digits. Anything else is a Stripe-side
    # data quality issue we don't want to surface as-is.
    sin_str = str(sin).strip()
    if not (len(sin_str) == 9 and sin_str.isdigit()):
        logger.error(
            "[STRIPE-KYC] reveal-sin: Stripe returned non-canonical SIN format for %s",
            account_id,
        )
        return None
    return sin_str
