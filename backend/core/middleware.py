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


def init_middleware(app):
    """Initialize all middleware components"""
    # JWT secret validation (production fail-fast).
    # Same pattern as the CORS wildcard check below: refuse to start a
    # production server with an insecure signing secret instead of
    # log-and-continue. Dev environments still get a usable default.
    is_production = settings.ENV.lower() == "production"
    if is_production:
        secret = settings.JWT_SECRET or ""
        if secret in _INSECURE_JWT_DEFAULTS:
            raise RuntimeError(
                "JWT_SECRET is set to a well-known default while ENV=production. "
                "Generate a strong secret (python -c 'import secrets; print(secrets.token_urlsafe(64))') "
                "and set JWT_SECRET in the environment."
            )
        if len(secret) < _MIN_JWT_SECRET_LENGTH:
            raise RuntimeError(
                f"JWT_SECRET is shorter than {_MIN_JWT_SECRET_LENGTH} characters while "
                "ENV=production. Use a longer, random secret."
            )

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
