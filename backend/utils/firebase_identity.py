"""Firebase identity eligibility policy (F01).

Why this exists
---------------
``firebase_admin.auth.verify_id_token`` answers one question: *did Firebase
sign this token, for this project, and is it unexpired?* It deliberately does
not answer the question Spinr's customer-only guarantee actually depends on:
*is the principal behind this token a verified phone/email customer?*

Those are different policies. Firebase supports anonymous authentication, which
mints a real UID and a perfectly valid, correctly-audienced ID token for a
caller who never proved ownership of a phone number or email address. F01 of
the AI security assessment on PR #5138 reproduced the gap
offline: an anonymous payload was accepted by ``/auth/firebase``, provisioned a
rider row with an empty phone, and the resulting Spinr JWT authenticated
against the shared AI dependency.

The two checks below are deliberately separate, and ordered:

1. ``sign_in_provider`` must not be ``anonymous`` and must be on the configured
   allowlist. This is an *identity-provider* policy.
2. The token must carry a verified contact — a phone number, or an email with
   ``email_verified`` true. This is an *identity-verification* policy.

Check 1 alone is not sufficient: a custom-token provider could also carry no
contact claim. Check 2 alone is not sufficient either: it would silently start
admitting whatever new provider someone enables in the Firebase console, as
long as that provider happened to populate an email field. Both must pass.

``ANONYMOUS_PROVIDER`` is rejected before the allowlist is read, so widening
``FIREBASE_ALLOWED_SIGN_IN_PROVIDERS`` — the ops-side rollback lever for this
gate — can never re-open the finding this module exists to close.

App Check is not a substitute for either check: it attests that the *client
binary* is genuine, not that the *human* verified a contact.
"""

import logging
from typing import Any, Dict, Optional, Tuple

try:
    from ..core.config import settings
except ImportError:  # python -m backend.server vs top-level
    from core.config import settings  # type: ignore

logger = logging.getLogger(__name__)

# Firebase's own spelling for the anonymous provider, as it appears in the
# `firebase.sign_in_provider` claim. Hard-coded rather than configurable: this
# is the exact identity class F01 exists to exclude.
ANONYMOUS_PROVIDER = "anonymous"


class FirebaseIdentityRejected(Exception):
    """Raised when a verified Firebase token fails Spinr's eligibility policy.

    Carries a stable ``reason`` code for logs/metrics. Callers translate this
    into their own transport error (401) — this module deliberately does not
    import FastAPI so it stays usable from the WS handshake and offline probes.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
        self.message = message


def allowed_sign_in_providers() -> frozenset:
    """Parse the configured provider allowlist.

    The anonymous provider is stripped unconditionally, so a misconfiguration
    (or a well-meaning "just add anonymous for the demo" edit) cannot disable
    the check. An empty/whitespace-only setting falls back to the field default
    rather than admitting everything — failing open here would be the whole
    finding again.

    The fallback is "phone" only, matching the config default: it is the sole
    provider the apps use, and admitting a provider that cannot satisfy the
    verified-contact check below just converts a clean sign-in rejection into
    a permanent post-provisioning 401.
    """
    raw = getattr(settings, "FIREBASE_ALLOWED_SIGN_IN_PROVIDERS", "") or ""
    parsed = {p.strip().lower() for p in raw.split(",") if p.strip()}
    parsed.discard(ANONYMOUS_PROVIDER)
    if not parsed:
        logger.error(
            "FIREBASE_ALLOWED_SIGN_IN_PROVIDERS is empty or anonymous-only — "
            "falling back to the secure default allowlist"
        )
        parsed = {"phone"}
    return frozenset(parsed)


def sign_in_provider(payload: Dict[str, Any]) -> str:
    """Extract ``firebase.sign_in_provider``, lowercased, or "" when absent.

    A token with no ``firebase`` claim at all is treated as an unknown provider
    (empty string), which fails the allowlist below — not as an exemption.
    """
    firebase_claim = payload.get("firebase")
    if not isinstance(firebase_claim, dict):
        return ""
    provider = firebase_claim.get("sign_in_provider")
    return str(provider).strip().lower() if provider else ""


def verified_contact(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(phone, email)`` limited to *verified* contact claims.

    ``phone_number`` is only ever set by Firebase after an SMS verification, so
    its presence is the verification. ``email`` is not — Firebase populates it
    for unverified password signups too — so it counts only alongside
    ``email_verified``.
    """
    phone = payload.get("phone_number") or None
    email = payload.get("email") or None
    if email and not payload.get("email_verified"):
        email = None
    return (phone or None), (email or None)


def enforce_customer_eligibility(payload: Dict[str, Any], *, surface: str) -> None:
    """Reject a verified-but-ineligible Firebase identity. Raises on rejection.

    ``surface`` is a short label for the log line only ("auth_exchange",
    "get_current_user", ...) — never a policy input, so no caller can weaken
    the gate by passing a different one.

    Never logs the phone/email itself (CLAUDE.md: no full phone numbers or
    email addresses in logs), only whether each was present.
    """
    provider = sign_in_provider(payload)

    if not getattr(settings, "FIREBASE_IDENTITY_POLICY_ENFORCED", True):
        # Operator kill switch (see core/config.py). Logged at error level on
        # every call, deliberately: a disabled identity gate is an incident
        # state to be noticed and reverted, not a quiet configuration.
        logger.error(
            "firebase identity policy DISABLED by configuration — accepting identity unchecked",
            extra={"domain": "auth", "surface": surface, "provider": provider or "<absent>"},
        )
        return

    if provider == ANONYMOUS_PROVIDER:
        logger.error(
            "firebase identity rejected: anonymous sign-in provider",
            extra={"domain": "auth", "surface": surface, "provider": provider},
        )
        raise FirebaseIdentityRejected(
            "anonymous_provider",
            "Anonymous sign-in is not permitted",
        )

    if provider not in allowed_sign_in_providers():
        logger.error(
            "firebase identity rejected: sign-in provider not on the allowlist",
            extra={"domain": "auth", "surface": surface, "provider": provider or "<absent>"},
        )
        raise FirebaseIdentityRejected(
            "provider_not_allowed",
            "Sign-in provider is not permitted",
        )

    phone, email = verified_contact(payload)
    if not phone and not email:
        logger.error(
            "firebase identity rejected: no verified phone or email claim",
            extra={
                "domain": "auth",
                "surface": surface,
                "provider": provider,
                "has_phone": False,
                "has_verified_email": False,
            },
        )
        raise FirebaseIdentityRejected(
            "no_verified_contact",
            "A verified phone number or email address is required",
        )
