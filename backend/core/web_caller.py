"""Signed server-to-server calls from the spinr.ca website.

The marketing site (desktop_website, on Vercel) calls a handful of backend
routes from its OWN server — never from a visitor's browser: the public
assistant, driver signup, service areas, legal text. Those calls cannot carry
X-Firebase-AppCheck (App Check attests a registered mobile build; a Vercel
function is not one), so under enforcement every one of them 401s.

Exempting the paths outright would make them callable by anyone on the
internet, and would leave every website visitor sharing ONE rate-limit bucket:
the per-IP key resolves to Vercel's egress IP, not the visitor's.

Instead the website signs each request with a shared secret, and this module
verifies it:

    X-Spinr-Web-Timestamp:  unix seconds
    X-Spinr-Web-Client-IP:  the visitor's IP as the website saw it (may be "")
    X-Spinr-Web-Signature:  v1=<hex HMAC-SHA256(secret, canonical)>

    canonical = "\\n".join(["v1", timestamp, METHOD, path, raw_query,
                            sha256_hex(body), client_ip])

A verified request skips App Check for the allow-listed route it signed, and
its X-Spinr-Web-Client-IP becomes the rate-limit key (see
utils/rate_limiter.get_real_client_ip). The client IP is inside the signature,
so it cannot be swapped to rotate buckets.

What the signature does NOT do: it grants no identity. /drivers/register still
requires the applicant's JWT; the assistant still runs at the anonymous "web"
audience. It only answers "did this request come from our website's server?".

Replay: a captured request can be replayed unchanged for MAX_SKEW_S. Every
allow-listed route is safe to repeat (reads, an anonymous chat turn, an
upsert behind a JWT), and changing any byte of method/path/query/body/IP
breaks the signature. No nonce store — it would need shared state across
instances for no real gain on these routes.

Config: WEB_CALLER_SIGNING_SECRETS — comma-separated, each ≥32 chars. Any one
verifying is enough, which is what makes rotation zero-downtime (add new,
deploy website, drop old). Unset = feature off: requests are handled exactly
as before this module existed.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from fastapi import Request

from core.config import settings

TIMESTAMP_HEADER = "x-spinr-web-timestamp"
CLIENT_IP_HEADER = "x-spinr-web-client-ip"
SIGNATURE_HEADER = "x-spinr-web-signature"

SIGNATURE_VERSION = "v1"
MAX_SKEW_S = 300
MIN_SECRET_LEN = 32
MAX_CLIENT_IP_LEN = 64

# Exact (method, path) pairs the website calls. Exact match, not prefix: a
# signature is never a key to anything the website does not use.
WEB_CALLER_ROUTES = frozenset(
    {
        ("POST", "/api/v1/ai/public-chat"),
        ("GET", "/api/v1/service-areas"),
        ("GET", "/api/v1/vehicle-types"),
        ("GET", "/api/v1/legal-documents"),
        ("POST", "/api/v1/auth/send-otp"),
        ("POST", "/api/v1/auth/verify-otp"),
        ("POST", "/api/v1/drivers/register"),
    }
)


def configured_secrets() -> list[bytes]:
    """Usable secrets, in order. Too-short entries are dropped, never trusted."""
    raw = settings.WEB_CALLER_SIGNING_SECRETS or ""
    return [s.strip().encode() for s in raw.split(",") if len(s.strip()) >= MIN_SECRET_LEN]


def is_web_caller_route(method: str, path: str) -> bool:
    return (method.upper(), path) in WEB_CALLER_ROUTES


def has_signature_headers(request: Request) -> bool:
    return SIGNATURE_HEADER in request.headers


def canonical_string(timestamp: str, method: str, path: str, query: str, body: bytes, client_ip: str) -> str:
    return "\n".join(
        [SIGNATURE_VERSION, timestamp, method.upper(), path, query, hashlib.sha256(body).hexdigest(), client_ip]
    )


def sign(secret: bytes, canonical: str) -> str:
    return f"{SIGNATURE_VERSION}=" + hmac.new(secret, canonical.encode(), hashlib.sha256).hexdigest()


async def verify(request: Request, *, now: Optional[float] = None) -> Optional[str]:
    """Return None if the request is validly signed, else a short reason code.

    Reason codes are for logs only — never echo them to the caller, who learns
    nothing but "rejected".
    """
    secrets_ = configured_secrets()
    if not secrets_:
        return "not_configured"

    method = request.method.upper()
    path = request.url.path
    if not is_web_caller_route(method, path):
        return "route_not_allowed"

    ts = request.headers.get(TIMESTAMP_HEADER, "")
    sig = request.headers.get(SIGNATURE_HEADER, "")
    client_ip = request.headers.get(CLIENT_IP_HEADER, "")
    if not ts.isdigit() or not sig:
        return "malformed"
    if len(client_ip) > MAX_CLIENT_IP_LEN:
        return "malformed"
    if abs((now if now is not None else time.time()) - int(ts)) > MAX_SKEW_S:
        return "stale"

    # Starlette caches the body, so the route handler can still read it.
    body = await request.body()
    canonical = canonical_string(ts, method, path, request.url.query, body, client_ip)
    if any(hmac.compare_digest(sign(secret, canonical), sig) for secret in secrets_):
        return None
    return "bad_signature"
