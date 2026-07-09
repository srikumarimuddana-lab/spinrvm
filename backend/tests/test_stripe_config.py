# backend/tests/test_stripe_config.py
"""Regression: configure_stripe() must not crash app startup.

utils/stripe_config.py runs during core/lifespan.py's startup, wrapped
inside FastAPI's merged_lifespan chain across ~25 routers. Any exception
here propagates all the way up and kills the entire app boot -- not just
Stripe. A prior version called the now-removed stripe.http_client.RequestsClient,
which crash-looped every production machine on restart.
"""

from utils.stripe_config import configure_stripe


def test_configure_stripe_does_not_raise():
    configure_stripe()


def test_configure_stripe_sets_a_real_http_client():
    import stripe

    configure_stripe()
    assert stripe.default_http_client is not None
