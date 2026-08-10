"""Centralized Stripe SDK configuration.

Called once at startup (lifespan.py) to set global defaults for timeout,
retries, and API version pinning. Individual calls still pass api_key
per-request via app_settings; this module only configures SDK-level
behaviour that applies to all calls.
"""

import logging

logger = logging.getLogger(__name__)

STRIPE_TIMEOUT = 30
STRIPE_MAX_RETRIES = 2
STRIPE_API_VERSION = "2025-04-30.basil"
CURRENCY = "cad"


def configure_stripe() -> None:
    """Set global Stripe SDK defaults. Safe to call multiple times."""
    try:
        import stripe
    except ImportError:
        logger.warning("stripe package not installed — skipping configuration")
        return

    stripe.max_network_retries = STRIPE_MAX_RETRIES
    stripe.api_version = STRIPE_API_VERSION

    if hasattr(stripe, "default_http_client") and stripe.default_http_client is None:
        stripe.default_http_client = stripe.new_default_http_client(
            timeout=STRIPE_TIMEOUT,
        )
    logger.info(
        "Stripe configured: timeout=%ds retries=%d api_version=%s",
        STRIPE_TIMEOUT,
        STRIPE_MAX_RETRIES,
        STRIPE_API_VERSION,
    )


def stripe_object_to_dict(obj) -> dict:
    """Convert a StripeObject to a plain dict, safely across SDK versions.

    NEVER use ``dict(obj)`` on a Stripe object. On SDK builds where
    StripeObject is not a Mapping (it exposes ``__getitem__`` over an internal
    ``_data`` without ``keys()``), ``dict()`` falls back to Python's integer-
    index sequence protocol and dies with ``KeyError: 0`` — observed in
    production on ``stripe.Account.list(...).auto_paging_iter()``. The
    documented ``to_dict_recursive`` accessor (private spelling on some
    versions) is what the battle-tested retrieve path in
    ``services/stripe_mapping_import_service`` already relies on; this makes
    that logic reusable everywhere we page Stripe listings.
    """
    if isinstance(obj, dict):
        return dict(obj)
    for attr in ("to_dict_recursive", "_to_dict_recursive", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                out = fn()
                if isinstance(out, dict):
                    return out
            except Exception:  # noqa: S112 - fall through to the next accessor
                continue
    # Last resort for mapping-like objects; may still raise for exotic shapes,
    # which is preferable to silently returning an empty dict for money data.
    return dict(obj)
