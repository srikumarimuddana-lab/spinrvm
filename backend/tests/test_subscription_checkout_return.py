"""Public Stripe Checkout return bounce (driver app deep-link handoff).

Stripe requires an https success/cancel URL; this endpoint receives that https
redirect and bounces the in-app browser to the app's custom scheme. Tests:
happy redirect, open-redirect allowlist, XSS/charset rejection, session_id
sanitisation, no-auth-required.
"""

from __future__ import annotations


def test_bounce_redirects_to_app_scheme_with_session(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "spinr-driver://subscription/success", "session_id": "cs_test_123"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "spinr-driver://subscription/success?session_id=cs_test_123" in body


def test_bounce_allows_expo_scheme(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "exp://127.0.0.1:8081/--/subscription/cancel"},
    )
    assert resp.status_code == 200
    assert "exp://127.0.0.1:8081/--/subscription/cancel" in resp.text


def test_bounce_rejects_open_redirect(test_client):
    # A non-allowlisted target must fall back to the default app scheme, never echo it.
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "https://evil.example.com/phish"},
    )
    assert resp.status_code == 200
    assert "evil.example.com" not in resp.text
    assert "spinr-driver://subscription/success" in resp.text


def test_bounce_rejects_html_injection_in_to(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": 'spinr-driver://x"><script>alert(1)</script>'},
    )
    assert resp.status_code == 200
    # The raw injection must not appear unescaped; it fails the charset check and
    # falls back to the default target.
    assert "<script>alert(1)</script>" not in resp.text
    assert "spinr-driver://subscription/success" in resp.text


def test_bounce_drops_malformed_session_id(test_client):
    resp = test_client.get(
        "/api/v1/drivers/subscription/checkout-return",
        params={"to": "spinr-driver://subscription/success", "session_id": "bad id<>"},
    )
    assert resp.status_code == 200
    assert "bad id" not in resp.text
    # No session_id appended when it fails sanitisation.
    assert "session_id=" not in resp.text
