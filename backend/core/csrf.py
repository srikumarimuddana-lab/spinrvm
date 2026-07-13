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
        # Session resume from the browser: the csrf cookie is short-lived
        # (access-token TTL) while the refresh cookie lives 30 days, so a
        # returning browser has no csrf token to echo. Safe to exempt —
        # the refresh cookie is SameSite=Strict (never sent cross-site) and
        # the rotated tokens in the response are unreadable cross-origin.
        "/api/v1/auth/refresh",
        "/api/portal/auth/refresh",
        "/api/admin/auth/login",
        # Stripe webhooks are server-to-server — no browser CSRF risk
        "/api/v1/stripe/webhook",
    }
)

CSRF_EXEMPT_PREFIXES = ("/ws/",)


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
