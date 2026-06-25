import secrets

from fastapi import Response

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"

CSRF_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Paths with no established session — no CSRF cookie exists yet
CSRF_EXEMPT_EXACT = frozenset(
    {
        "/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        # Pre-auth: no session cookie to protect
        "/api/v1/auth/send-otp",
        "/api/v1/auth/verify-otp",
        "/api/v1/auth/firebase",
        "/api/admin/auth/login",
    }
)

# Inbound webhooks (Stripe, AWS SES/SNS) are server-to-server — no browser CSRF
# risk. Covered by prefix so /api/v1/webhooks/stripe + /ses are both exempt.
# NB: live CSRF enforcement uses core.middleware._CSRF_EXEMPT_* — this module's
# copy is currently consumed only for the helper functions below, but is kept in
# sync so it never becomes a stale trap (it previously listed the never-matched
# path "/api/v1/stripe/webhook").
CSRF_EXEMPT_PREFIXES = ("/ws/", "/api/v1/webhooks/")


def generate_csrf_token() -> str:
    """Generate a cryptographically secure 32-byte URL-safe CSRF token."""
    return secrets.token_urlsafe(32)


def set_csrf_cookie(response: Response, token: str, *, secure: bool, max_age: int) -> None:
    """Set the double-submit CSRF cookie on a response.

    Intentionally NOT HttpOnly — the JS client must be able to read the value
    and echo it back as the X-CSRF-Token request header. SameSite=Strict
    prevents the cookie from being sent on cross-site navigations, which is
    the primary defence; the header-echo step is the second layer.
    """
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        httponly=False,
        samesite="strict",
        secure=secure,
        max_age=max_age,
        path="/",
    )


def clear_csrf_cookie(response: Response) -> None:
    """Remove the CSRF cookie (call on logout)."""
    response.delete_cookie(key=CSRF_COOKIE, path="/", samesite="strict")
