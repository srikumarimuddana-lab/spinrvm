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
