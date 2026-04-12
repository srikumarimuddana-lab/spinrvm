from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi.errors import RateLimitExceeded

from core.config import settings
from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

_INSECURE_JWT_DEFAULTS = {
    "your-strong-secret-key",  # core/config.py default
    "spinr-dev-secret-key-NOT-FOR-PRODUCTION",  # previous dependencies.py fallback
}
_MIN_JWT_SECRET_LENGTH = 32

# Bootstrap credentials that must be overridden before the dashboard
# is exposed publicly — otherwise anyone who reads the source can log
# in as the super-admin. These are the defaults shipped in
# core/config.py; production deploys must set real values via env vars.
_INSECURE_ADMIN_EMAILS = {"admin@spinr.ca", "admin@example.com"}
_INSECURE_ADMIN_PASSWORDS = {"admin123", "replace-me", "changeme", "password"}


def _validate_production_config():
    """Fail fast on misconfigured production deploys.

    Called at the top of init_middleware so the server never actually
    starts serving requests with a known-insecure configuration. All
    checks only fire when ``ENV=production``; dev/local environments
    get usable defaults.
    """
    if settings.ENV.lower() != "production":
        return

    errors: list[str] = []

    # 1. JWT signing secret
    secret = settings.JWT_SECRET or ""
    if secret in _INSECURE_JWT_DEFAULTS:
        errors.append(
            "JWT_SECRET is set to a well-known default. Generate a strong "
            "secret (python -c 'import secrets; print(secrets.token_urlsafe(64))') "
            "and set JWT_SECRET in the environment."
        )
    elif len(secret) < _MIN_JWT_SECRET_LENGTH:
        errors.append(
            f"JWT_SECRET is shorter than {_MIN_JWT_SECRET_LENGTH} characters. "
            "Use a longer, random secret."
        )

    # 2. Supabase credentials — the entire backend is Supabase-backed,
    #    so an unset URL or service role key means the server comes up
    #    but every DB call hits a NoneType client.
    if not settings.SUPABASE_URL:
        errors.append("SUPABASE_URL is not set.")
    if not settings.SUPABASE_SERVICE_ROLE_KEY:
        errors.append("SUPABASE_SERVICE_ROLE_KEY is not set.")

    # 3. Admin bootstrap credentials — the super-admin login path in
    #    routes/admin/auth.py compares directly against these strings.
    admin_email = (settings.ADMIN_EMAIL or "").lower().strip()
    admin_password = settings.ADMIN_PASSWORD or ""
    if admin_email in _INSECURE_ADMIN_EMAILS:
        errors.append(
            "ADMIN_EMAIL is set to a well-known default. Set a real "
            "admin email in the environment before exposing the dashboard."
        )
    if admin_password in _INSECURE_ADMIN_PASSWORDS:
        errors.append(
            "ADMIN_PASSWORD is set to a well-known default. Set a strong "
            "password in the environment before exposing the dashboard."
        )
    elif len(admin_password) < 12:
        errors.append(
            "ADMIN_PASSWORD is shorter than 12 characters. Use a stronger password."
        )

    # 4. Firebase service account — required for Firebase Auth verify
    #    and for FCM push delivery. Missing means get_current_user can't
    #    verify Firebase-issued tokens and send_push_notification no-ops.
    #    Warn rather than fail because a deploy MIGHT be intentionally
    #    running without Firebase (e.g. SMS-only auth via Twilio + a
    #    local push transport).
    if not settings.FIREBASE_SERVICE_ACCOUNT_JSON:
        logger.warning(
            "FIREBASE_SERVICE_ACCOUNT_JSON is not set — Firebase ID token "
            "verification and FCM push delivery will no-op. Set it if you "
            "want drivers to receive push notifications."
        )

    if errors:
        formatted = "\n  - ".join(errors)
        raise RuntimeError(
            f"Refusing to start: production configuration has {len(errors)} "
            f"problem(s).\n  - {formatted}"
        )


def init_middleware(app):
    """Initialize all middleware components"""
    # Fail-fast on misconfigured production deploys BEFORE any routes
    # or middleware are attached. See _validate_production_config.
    _validate_production_config()

    is_production = settings.ENV.lower() == "production"

    # CORS Middleware
    origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",") if origin.strip()]

    # Always allow the admin and default apps explicitly regardless of env variables
    always_allowed = ["https://spinr-admin.vercel.app", "http://localhost:3000", "http://localhost:3001"]
    origins.extend(always_allowed)
    # Remove empty strings and duplicates (preserve order for determinism)
    origins = list(dict.fromkeys(o for o in origins if o))

    wildcard = "*" in origins

    if wildcard and is_production:
        # Fail fast: refuse to start with wide-open CORS in production.
        # Set ALLOWED_ORIGINS in the environment to a comma-separated list.
        raise RuntimeError(
            "CORS is configured with wildcard '*' while ENV=production. "
            "Set ALLOWED_ORIGINS to an explicit comma-separated list of origins."
        )

    # CORS spec forbids credentials with wildcard origin — browsers will drop
    # the Access-Control-Allow-Credentials header if origin is '*'. Disable
    # credentials in that case so dev requests fail loudly rather than silently.
    allow_credentials = not wildcard
    if wildcard:
        logger.warning(
            "CORS: wildcard '*' in ALLOWED_ORIGINS — allow_credentials disabled. "
            "This is acceptable for local dev only."
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # FIX: Add CORS headers to exception responses (FastAPI bug fix)
    @app.exception_handler(Exception)
    async def cors_exception_handler(request: Request, exc: Exception):
        origin = request.headers.get("origin")

        # Handle standard HTTP exceptions
        if hasattr(exc, "status_code") and hasattr(exc, "detail"):
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        else:
            # Handle unhandled exceptions
            logger.error(f"Unhandled exception: {exc}")
            response = JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

        # Add CORS headers if origin is allowed
        if origin:
            if origin in origins:
                # Explicit match — safe to allow credentials
                response.headers["Access-Control-Allow-Origin"] = origin
                if allow_credentials:
                    response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "*"
                response.headers["Vary"] = "Origin"
            elif wildcard:
                # Wildcard (dev only) — credentials already disabled above
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Methods"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "*"

        return response

    # Rate Limiting Middleware
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    logger.info("Middleware initialized: CORS and Rate Limiting")
