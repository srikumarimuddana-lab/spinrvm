"""Public Stripe Checkout return bounce (driver app deep-link handoff).

Stripe requires an https success/cancel URL; this endpoint receives that https
redirect and bounces the in-app browser to the app's custom scheme via a
server-side **302** (a JS/meta redirect to a custom scheme is blocked by Chrome
Custom Tabs, stranding the driver after paying). Tests: happy redirect, expo
scheme, open-redirect allowlist, injection rejection, session_id sanitisation.
"""

from __future__ import annotations


def test_bounce_302s_to_app_scheme_with_session(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "spinr-driver://subscription/success", "session_id": "cs_test_123"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "spinr-driver://subscription/success?session_id=cs_test_123"


def test_bounce_allows_expo_scheme(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "exp://127.0.0.1:8081/--/subscription/cancel"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "exp://127.0.0.1:8081/--/subscription/cancel"


def test_bounce_rejects_open_redirect(test_client):
    # A non-allowlisted target must fall back to the default app scheme, never echo it.
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "https://evil.example.com/phish"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "evil.example.com" not in loc
    assert loc.startswith("spinr-driver://subscription/success")


def test_bounce_rejects_injection_in_to(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": 'spinr-driver://x"><script>alert(1)</script>'},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    # Fails the charset check → falls back to the default target, never echoed.
    assert "<script>" not in loc
    assert loc.startswith("spinr-driver://subscription/success")


def test_bounce_drops_malformed_session_id(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "spinr-driver://subscription/success", "session_id": "bad id<>"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert "bad id" not in loc
    # No session_id appended when it fails sanitisation.
    assert "session_id=" not in loc
