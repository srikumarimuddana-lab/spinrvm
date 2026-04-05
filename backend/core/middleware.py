from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from loguru import logger
from core.config import settings
from utils.rate_limiter import default_limiter, rate_limit_exceeded_handler

def init_middleware(app):
    """Initialize all middleware components"""
    # CORS Middleware
    origins = [origin.strip() for origin in settings.ALLOWED_ORIGINS.split(",") if origin.strip()]
    
    # Always allow the admin and default apps explicitly regardless of env variables
    always_allowed = [
        "https://spinr-admin.vercel.app",
        "http://localhost:3000",
        "http://localhost:3001"
    ]
    origins.extend(always_allowed)
    # Remove empty strings
    origins = list(set([o for o in origins if o]))
    # Validate origins in production
    if settings.DEBUG == False:  # Production mode
        if "*" in origins:
            logger.warning(
                "WARNING: CORS allows all origins (*) in production! "
                "This is a security risk. Configure specific allowed origins."
            )
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Rate Limiting Middleware
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    
    logger.info("Middleware initialized: CORS and Rate Limiting")
