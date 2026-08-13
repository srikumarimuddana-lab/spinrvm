"""Stripe Connect KYC mirror.

Maps a Stripe Express ``account.updated`` payload (or a live
``Account.retrieve()`` response) into the cache columns on the
``drivers`` row added by migration 92. The SIN itself is never stored —
we keep only ``id_number_provided`` (boolean). The ``id_number_last4``
column exists but is always NULL: Stripe returns no digits of an ID number
in any form, so the slideout's "On file · ••••1234" shows only the on-file
state. Spinr's own SIN — the one T4A is filed from — is a separate,
Vault-encrypted column (migration 289) and has nothing to do with this
mirror; see the note above ``get_legal_name_and_address_from_stripe``.

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

    await _notify_payouts_transition(driver, updates, event_id=event_id)

    return {**driver, **updates}


async def _notify_payouts_transition(
    driver: Dict[str, Any], updates: Dict[str, Any], *, event_id: Optional[str] = None
) -> None:
    """Notify the driver on a genuine ``stripe_payouts_enabled`` edge only.

    Stripe redelivers ``account.updated`` freely (retries, replays, or the
    event just carries an unrelated field change on the same account) — most
    deliveries do not change ``payouts_enabled`` at all. Firing on every
    delivery of an already-blocked account would spam a driver stuck blocked
    for days with the same push over and over, so this keys off the *edge*
    (comparing the pre-update ``driver`` row, already in scope, against the
    freshly computed ``updates``) rather than the level.

    ``updates["stripe_payouts_enabled"]`` is always a real bool — see
    ``_kyc_mirror_fields``'s ``bool(account.get("payouts_enabled"))`` — never
    ``None``. ``driver.get("stripe_payouts_enabled")`` (the pre-update value)
    genuinely can be ``None`` though: a drivers row that predates this mirror
    column, or a driver's very first ``account.updated`` since starting
    onboarding. That first-ever observation must never read as a transition
    in either direction — a new driver who starts out blocked (``None`` ->
    ``False``) has not been "newly blocked", and one whose account starts
    enabled (``None`` -> ``True``) has not "recovered" from anything. Only an
    explicit ``True`` <-> ``False`` edge on a row synced at least once before
    counts as either transition.
    """
    was_enabled = driver.get("stripe_payouts_enabled")
    now_enabled = updates["stripe_payouts_enabled"]

    if was_enabled is True and now_enabled is False:
        await _send_payouts_notice(
            driver,
            title="Your account needs attention",
            body=("Stripe has paused payouts on your account pending verification. Open the app to see what's needed."),
            data_type="stripe_payouts_blocked",
            priority="account",
            event_id=event_id,
        )
    elif was_enabled is False and now_enabled is True:
        # Recovery case: symmetric, separate condition — deliberately not
        # merged with the block above into a single "changed" check, so each
        # direction's copy/priority stays independently correct. Uses the
        # "normal" tier (informational good news, not the guaranteed-delivery
        # "account" tier reserved for a driver who can no longer earn).
        await _send_payouts_notice(
            driver,
            title="Payouts are back on",
            body="Your Stripe verification is complete — payouts have resumed.",
            data_type="stripe_payouts_recovered",
            priority="normal",
            event_id=event_id,
        )


async def _send_payouts_notice(
    driver: Dict[str, Any],
    *,
    title: str,
    body: str,
    data_type: str,
    priority: str,
    event_id: Optional[str],
) -> None:
    """Best-effort push for a payouts-enabled transition. Never raises —
    a notification failure must not block or undo the mirror write that
    already committed above (matches the subscription-cancelled push and
    every other best-effort side-effect in routes/webhooks.py).
    """
    user_id = driver.get("user_id")
    if not user_id:
        logger.warning(
            "[STRIPE-KYC] payouts transition for driver=%s has no user_id on the drivers row — cannot notify",
            driver.get("id"),
            extra={"event_id": event_id, "domain": "drivers"},
        )
        return

    try:
        try:
            from ..features import send_push_notification
        except ImportError:
            from features import send_push_notification  # type: ignore

        await send_push_notification(
            user_id,
            title,
            body,
            data={"type": data_type, "deeplink": "/driver/payout"},
            priority=priority,
            target_app="driver",
        )
    except Exception:
        logger.warning(
            "[STRIPE-KYC] payouts transition push failed for driver=%s (%s)",
            driver.get("id"),
            data_type,
            exc_info=True,
            extra={"event_id": event_id, "domain": "drivers"},
        )


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


# ── Why there is no SIN reveal here ──────────────────────────────────────
# There used to be a `reveal_sin_from_stripe`. It could not work, and no
# amount of retrying, re-keying or re-onboarding would have made it work:
#
#   `individual.id_number` is WRITE-ONLY on Stripe Connect. In the SDK
#   (generated from Stripe's API spec) it appears in six request-parameter
#   modules and in ZERO response models. Stripe returns `id_number_provided`
#   and `ssn_last_4_provided` — booleans, never digits. Asking for it with
#   `expand=["individual.id_number"]` earns "This property cannot be
#   expanded", which is Stripe saying no such response property exists.
#
# Spinr now collects its own Vault-encrypted copy (migration 289) and the
# reveal lives in `routes/admin/drivers.py::admin_reveal_driver_sin`, which
# decrypts our column under a super_admin gate with an audit row.
#
# If you are here because you want the SIN: it is not obtainable from Stripe.
# Do not add an expand back.
#
# `stripe_id_number_provided`, mirrored below, remains meaningful for exactly
# one thing — whether STRIPE has what IT needs to enable payouts. It says
# nothing about whether Spinr can file a T4A.


async def get_legal_name_and_address_from_stripe(driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the driver's Stripe-verified legal name and mailing address,
    for handoff to a third-party tax filer (T4A / Reportable Platform
    Operator reporting) — never the SIN.

    Does NOT expand ``individual.id_number`` (which is impossible anyway,
    see the note above) — ``individual.first_name``/``last_name``/
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
