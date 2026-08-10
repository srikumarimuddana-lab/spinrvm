"""Stripe Connect KYC mirror.

Maps a Stripe Express ``account.updated`` payload (or a live
``Account.retrieve()`` response) into the cache columns on the
``drivers`` row added by migration 92. The SIN itself is never stored —
we keep only ``id_number_provided`` (boolean). The ``id_number_last4``
column exists but is always NULL: Stripe returns no digits of an ID number
in any form, so the slideout's "On file · ••••1234" shows only the on-file
state. See :class:`SinNotRevealable`.

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
    from ..utils.stripe_mode import is_missing_on_key, key_mode, stale_by_mode
except ImportError:
    import db_supabase  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.stripe_mode import is_missing_on_key, key_mode, stale_by_mode  # type: ignore

logger = logging.getLogger(__name__)

# Every mirror column below describes the Connect account named by
# drivers.stripe_account_id. When that account stops being reachable on the
# running key, the mirror is not merely stale — it is a lie about a payout
# destination that no longer exists, and it is what the admin slideout and
# the driver app's "bank linked" state both read. Retiring the account must
# therefore reset the mirror in the same write.
_KYC_MIRROR_RESET: Dict[str, Any] = {
    "stripe_account_onboarded": False,
    "stripe_details_submitted": False,
    "stripe_charges_enabled": False,
    "stripe_payouts_enabled": False,
    "stripe_id_number_provided": False,
    "stripe_id_number_last4": None,
    "stripe_requirements_due": [],
    "stripe_requirements_past_due": [],
    "stripe_disabled_reason": None,
    "stripe_verification_status": None,
    "stripe_business_type": None,
    # ToS was accepted on the retired account; the replacement needs its own.
    "stripe_tos_accepted_at": None,
}


async def retire_stripe_account(driver: Dict[str, Any], stale_account_id: str, reason: str) -> None:
    """Detach a Connect account that is not reachable on the running key.

    Called only with positive proof (an explicit mode disagreement, or
    ``resource_missing`` / ``PermissionError`` from Stripe for this exact
    account) — never on an auth or transient error. See utils/stripe_mode.py.

    Deliberately does NOT create a replacement. A Connect account is a payout
    destination carrying bank details and verified identity; the driver must
    go through onboarding again, which the existing in-app "Set up payouts"
    flow does. Resetting the mirror is what makes the app offer that flow
    instead of showing a bank that is not there.

    The write filters on the stale id so a concurrent retire or a completed
    re-onboarding is never clobbered.
    """
    await db_supabase.update_one(
        "drivers",
        {"id": driver["id"], "stripe_account_id": stale_account_id},
        {
            "stripe_account_id": None,
            "stripe_account_id_mode": None,
            "stripe_account_id_superseded": stale_account_id,
            "stripe_account_id_superseded_at": datetime.now(timezone.utc).isoformat(),
            "stripe_last_synced_at": datetime.now(timezone.utc).isoformat(),
            **_KYC_MIRROR_RESET,
        },
    )
    # Degraded-but-recovered → warning, per CLAUDE.md's observability rules.
    # Stripe account IDs are operational identifiers, not PII.
    logger.warning(
        "Retired unreachable Stripe Connect account; driver must re-onboard payouts",
        extra={
            "driver_id": driver["id"],
            "reason": reason,
            "superseded_account_id": stale_account_id,
        },
    )


def account_is_stale_by_mode(driver: Dict[str, Any], stripe_secret: str) -> bool:
    """True when the driver's stamped account mode disagrees with the key.

    Cheap pre-check: no Stripe call needed. Unstamped rows (everything
    predating migration 286) return False and are caught by the
    ``resource_missing`` path instead.
    """
    return stale_by_mode(driver.get("stripe_account_id_mode"), key_mode(stripe_secret))


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

    # `id_number_provided` is real and works. `id_number_last_4` is NOT a
    # Stripe field — it does not exist anywhere in the API (verified against
    # the installed SDK: zero occurrences). Stripe exposes only booleans about
    # the ID number: `id_number_provided` and, for US SSNs,
    # `ssn_last_4_provided` — the *digits* are never returned in any form.
    #
    # So `stripe_id_number_last4` is always NULL and the admin slideout's
    # "On file · ••••1234" has never rendered digits. Kept reading the absent
    # key (a harmless None) rather than deleting the column, because the fix
    # is a product decision — Spinr must collect the last 4 itself or drop the
    # display — not a rename. See the SIN section of the Stripe runbook.
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


async def refresh_driver_kyc(
    driver: Dict[str, Any],
    *,
    retire_if_unreachable: bool = False,
) -> Dict[str, Any]:
    """Admin "Refresh from Stripe" path.

    Retrieves the live Account from Stripe and runs the same mapping the
    webhook uses. Caller is responsible for the audit_log entry — this
    function only touches Stripe + the drivers row.

    ``retire_if_unreachable`` opts in to detaching an account the running key
    cannot see (the test→live rotation case). It defaults to **False** because
    the destructive reading is not always the right one: the legacy Stripe
    mapping import calls this immediately after committing an
    ``stripe_account_id``, and a Scenario-B account living on the old Connect
    platform answers ``PermissionError`` — which would null the mapping the
    import just wrote, and null it again on every re-import. Only callers whose
    job is repair (the driver's own sync, the admin refresh button) pass True.
    """
    account_id = driver.get("stripe_account_id")
    if not account_id:
        return {"status": "no_stripe_account"}

    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    if not stripe_secret:
        return {"status": "stripe_not_configured"}

    # Cheap pre-check: a stamped account whose mode disagrees with the running
    # key is unreachable by definition — retire it without spending a call.
    if retire_if_unreachable and account_is_stale_by_mode(driver, stripe_secret):
        await retire_stripe_account(driver, account_id, reason="mode_mismatch")
        return {"status": "account_not_on_key", "retired": True}

    import stripe

    try:
        # to_thread: the Stripe SDK is blocking; batch callers (legacy Stripe
        # mapping import) refresh many drivers concurrently on the event loop.
        account = await asyncio.to_thread(stripe.Account.retrieve, account_id, api_key=stripe_secret)
    except Exception as e:
        if is_missing_on_key(e, account_id):
            # The account does not exist on this key (the classic symptom of a
            # test→live rotation) — distinct from a Stripe outage.
            if not retire_if_unreachable:
                logger.error(
                    "[STRIPE-KYC] refresh: account %s not reachable on the current key "
                    "(not retiring — caller did not opt in)",
                    account_id,
                )
                return {"status": "account_not_on_key", "retired": False}
            # Retire so the mirror stops advertising payouts the platform
            # cannot make, and the driver is offered onboarding, not a dead end.
            logger.warning(
                "[STRIPE-KYC] refresh: account %s not reachable on the current key — retiring",
                account_id,
            )
            await retire_stripe_account(driver, account_id, reason="resource_missing")
            return {"status": "account_not_on_key", "retired": True}
        logger.error(
            "[STRIPE-KYC] refresh: Account.retrieve failed for %s",
            account_id,
            exc_info=True,
        )
        return {"status": "stripe_error"}

    fn = getattr(account, "_to_dict_recursive", None) or getattr(account, "to_dict_recursive", None)
    if callable(fn):
        try:
            payload = fn()
        except Exception:
            payload = dict(account)
    else:
        payload = dict(account)

    updates = _kyc_mirror_fields(payload)
    await db_supabase.update_one("drivers", {"id": driver["id"]}, updates)
    return {"status": "ok", "updates": updates}


class SinNotRevealable(Exception):
    """Stripe will not return ``individual.id_number``. Ever, for any account.

    ``id_number`` is **write-only** on Stripe Connect. Verified against the
    installed SDK (generated from Stripe's own API spec): it appears solely as
    a request parameter — ``params/_account_create_params.py``,
    ``_account_update_params.py``, ``_account_{create,modify}_person_params``
    and the two ``_account_person_*_params`` — and **never** as a response
    attribute on ``Account`` or ``Person``. What the response does carry is
    ``id_number_provided: bool`` and, for US SSNs, ``ssn_last_4_provided:
    bool`` — booleans, not digits. Stripe returns no part of the number.

    "This property cannot be expanded" is therefore Stripe stating there is no
    such response property, not withholding one. It is not affected by the
    account's Connect type, the key's permissions, or the API version, so the
    error deliberately carries no diagnostic fields — there is nothing to
    diagnose and offering a knob implies a fix that does not exist.
    """

    def __init__(self) -> None:
        super().__init__("Stripe Connect never returns individual.id_number; the field is write-only")


def _expansion_refused(exc: BaseException) -> bool:
    """True for Stripe's "This property cannot be expanded (…)" response.

    Matched on the message rather than a code because Stripe sends this as a
    generic ``InvalidRequestError`` with no distinguishing ``code``. Narrowed
    by the exception type so an unrelated invalid request cannot be mistaken
    for a permanent refusal and hide a real, retryable fault.
    """
    import stripe

    if not isinstance(exc, stripe.error.InvalidRequestError):
        return False
    return "cannot be expanded" in f"{getattr(exc, 'user_message', None) or ''} {exc}".lower()


async def reveal_sin_from_stripe(driver: Dict[str, Any]) -> Optional[str]:
    """Return the plaintext SIN once, for a single audit-logged admin reveal.

    **This cannot currently succeed.** It asks Stripe to expand
    ``individual.id_number``, which is write-only on Connect — see
    :class:`SinNotRevealable`. The function is kept, rather than deleted,
    because removing the endpoint is a T4A/compliance decision (Spinr holds no
    other copy of the SIN) and because it fails loudly and correctly: Stripe's
    refusal raises :class:`SinNotRevealable`, which the route reports as a
    permanent 409.

    Every *other* failure still returns ``None`` and is reported as a
    retryable 502, so a genuine Stripe outage is never mistaken for the
    permanent refusal. If the returned value is ever non-empty it is **NEVER
    persisted anywhere on our side**, and the caller MUST write the audit_log
    entry around this call.
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
    except Exception as exc:
        # B-P3-leak-cleanup: full traceback to logs (server-side only),
        # no Stripe error detail in the return.
        logger.error(
            "[STRIPE-KYC] reveal-sin: Account.retrieve failed for %s",
            account_id,
            exc_info=True,
        )
        if _expansion_refused(exc):
            raise SinNotRevealable from exc
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


async def get_legal_name_and_address_from_stripe(driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the driver's Stripe-verified legal name and mailing address,
    for handoff to a third-party tax filer (T4A / Reportable Platform
    Operator reporting) — never the SIN.

    Unlike ``reveal_sin_from_stripe``, this does NOT expand
    ``individual.id_number`` — ``individual.first_name``/``last_name``/
    ``address`` are already present on an ordinary ``Account.retrieve()``
    response for the platform owner, no special expand permission needed.
    Deliberately kept as a separate function from the SIN reveal (not a
    shared "get everything" helper) so a future caller can never
    accidentally pull the SIN into a bulk export path by using the wrong
    function — see ACTION_ITEMS.md's SIN-collection decision: SIN stays
    entirely on Stripe's side and is only ever surfaced one driver at a
    time via the audited reveal-sin admin endpoint, never in a bulk
    export.

    Returns None if the driver has no Stripe account, Stripe isn't
    configured, or the API call fails — callers should treat that as
    "address unavailable" for this driver, not fail the whole export.
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
        account = stripe.Account.retrieve(account_id, api_key=stripe_secret)
    except Exception:
        logger.error(
            "[STRIPE-KYC] legal-name-address: Account.retrieve failed for %s",
            account_id,
            exc_info=True,
        )
        return None

    individual = account.get("individual") or {}
    address = individual.get("address") or {}
    legal_name = f"{individual.get('first_name', '') or ''} {individual.get('last_name', '') or ''}".strip()
    if not legal_name and not address:
        return None

    return {
        "legal_name": legal_name or None,
        "address_line1": address.get("line1"),
        "address_line2": address.get("line2"),
        "city": address.get("city"),
        "province": address.get("state"),
        "postal_code": address.get("postal_code"),
        "country": address.get("country"),
    }
