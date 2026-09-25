"""Signed website calls (core/web_caller.py) under enforced App Check.

The spinr.ca website calls the backend from its own server, which can never
mint an App Check token. A valid HMAC signature is its way past App Check —
for the exact routes it uses and nothing else — and the visitor IP inside the
signature becomes the rate-limit key, so every website visitor does not share
Vercel's egress-IP bucket.
"""

import hashlib
import hmac
import time

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from core import web_caller
from core.config import settings
from core.middleware import FirebaseAppCheckMiddleware
from utils.rate_limiter import get_real_client_ip

SECRET = "s" * 48
OTHER_SECRET = "o" * 48
PUBLIC_CHAT = "/api/v1/ai/public-chat"
LEGAL = "/api/v1/legal-documents"
NOT_LISTED = "/api/v1/ai/chat"  # signed-in app route: a signature must never open it


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "WEB_CALLER_SIGNING_SECRETS", SECRET)

    async def handler(request: Request):
        body = await request.body()
        return JSONResponse({"body": body.decode(), "rate_key": get_real_client_ip(request)})

    app = Starlette(routes=[Route(p, handler, methods=["GET", "POST"]) for p in (PUBLIC_CHAT, LEGAL, NOT_LISTED)])
    app.add_middleware(FirebaseAppCheckMiddleware, enforcement_enabled=True)
    return TestClient(app)


def _signed(method, path, *, body=b"", query="", client_ip="203.0.113.7", secret=SECRET, ts=None):
    ts = str(int(time.time()) if ts is None else ts)
    canonical = web_caller.canonical_string(ts, method, path, query, body, client_ip)
    return {
        "X-Spinr-Web-Timestamp": ts,
        "X-Spinr-Web-Client-IP": client_ip,
        "X-Spinr-Web-Signature": web_caller.sign(secret.encode(), canonical),
    }


BODY = b'{"message":"How do refunds work?","visitor_type":"rider"}'


def _post(client, headers, body=BODY, path=PUBLIC_CHAT):
    return client.post(path, content=body, headers={"Content-Type": "application/json", **headers})


def test_signed_call_passes_app_check_and_handler_still_reads_body(client):
    resp = _post(client, _signed("POST", PUBLIC_CHAT, body=BODY))

    assert resp.status_code == 200, resp.text
    assert resp.json()["body"] == BODY.decode()


def test_signed_client_ip_becomes_the_rate_limit_key(client):
    resp = _post(client, _signed("POST", PUBLIC_CHAT, body=BODY, client_ip="198.51.100.23"))

    assert resp.json()["rate_key"] == "198.51.100.23"


def test_signed_get_with_query_string(client):
    query = "audience=rider&type=terms"
    resp = client.get(f"{LEGAL}?{query}", headers=_signed("GET", LEGAL, query=query))

    assert resp.status_code == 200, resp.text


def test_unsigned_call_is_still_app_check_enforced(client):
    resp = _post(client, {})

    assert resp.status_code == 401
    assert resp.json() == {"detail": "App Check token required"}


@pytest.mark.parametrize(
    "tamper",
    [
        pytest.param(lambda h: {**h, "X-Spinr-Web-Signature": "v1=" + "0" * 64}, id="forged-signature"),
        pytest.param(lambda h: {**h, "X-Spinr-Web-Client-IP": "192.0.2.99"}, id="swapped-client-ip"),
        pytest.param(lambda h: {**h, "X-Spinr-Web-Timestamp": str(int(h["X-Spinr-Web-Timestamp"]) + 1)}, id="moved-ts"),
    ],
)
def test_tampered_headers_get_no_bypass(client, tamper):
    resp = _post(client, tamper(_signed("POST", PUBLIC_CHAT, body=BODY)))

    assert resp.status_code == 401


def test_tampered_body_gets_no_bypass(client):
    headers = _signed("POST", PUBLIC_CHAT, body=BODY)

    resp = _post(client, headers, body=BODY.replace(b"refunds", b"payouts"))

    assert resp.status_code == 401


def test_tampered_query_gets_no_bypass(client):
    headers = _signed("GET", LEGAL, query="audience=rider&type=terms")

    resp = client.get(f"{LEGAL}?audience=driver&type=terms", headers=headers)

    assert resp.status_code == 401


def test_wrong_secret_gets_no_bypass(client):
    resp = _post(client, _signed("POST", PUBLIC_CHAT, body=BODY, secret=OTHER_SECRET))

    assert resp.status_code == 401


def test_stale_timestamp_gets_no_bypass(client):
    old = int(time.time()) - web_caller.MAX_SKEW_S - 5

    resp = _post(client, _signed("POST", PUBLIC_CHAT, body=BODY, ts=old))

    assert resp.status_code == 401


def test_valid_signature_on_unlisted_route_gets_no_bypass(client):
    resp = _post(client, _signed("POST", NOT_LISTED, body=BODY), path=NOT_LISTED)

    assert resp.status_code == 401
    assert resp.json() == {"detail": "App Check token required"}


def test_method_is_part_of_the_allowlist(client):
    # GET /public-chat is not a route the website uses, even though POST is.
    resp = client.get(PUBLIC_CHAT, headers=_signed("GET", PUBLIC_CHAT))

    assert resp.status_code == 401


def test_no_secret_configured_means_no_bypass(client, monkeypatch):
    monkeypatch.setattr(settings, "WEB_CALLER_SIGNING_SECRETS", "")

    resp = _post(client, _signed("POST", PUBLIC_CHAT, body=BODY))

    assert resp.status_code == 401


def test_rotation_accepts_either_configured_secret(client, monkeypatch):
    monkeypatch.setattr(settings, "WEB_CALLER_SIGNING_SECRETS", f"{OTHER_SECRET}, {SECRET}")

    resp = _post(client, _signed("POST", PUBLIC_CHAT, body=BODY))

    assert resp.status_code == 200, resp.text


def test_short_secret_is_never_trusted(monkeypatch):
    monkeypatch.setattr(settings, "WEB_CALLER_SIGNING_SECRETS", "short-secret")

    assert web_caller.configured_secrets() == []


def test_golden_vector_matches_website_signer():
    # The website computes this in lib/spinr-api.js; both sides must agree
    # byte-for-byte. If this changes, change desktop_website in the same breath.
    canonical = web_caller.canonical_string(
        "1790000000", "post", "/api/v1/ai/public-chat", "", b'{"message":"hi"}', "203.0.113.7"
    )
    expected = hmac.new(SECRET.encode(), canonical.encode(), hashlib.sha256).hexdigest()

    assert canonical == (
        "v1\n1790000000\nPOST\n/api/v1/ai/public-chat\n\n"
        + hashlib.sha256(b'{"message":"hi"}').hexdigest()
        + "\n203.0.113.7"
    )
    assert web_caller.sign(SECRET.encode(), canonical) == "v1=" + expected
    assert expected == "4d5fe08dfe1a4670303b28748f8eb133d73c9df720cbed6c2305487b5db79863"
