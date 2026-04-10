from fastapi.middleware.cors import CORSMiddleware
from fastapi import Request
from fastapi.responses import JSONResponse
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

    # FIX: Add CORS headers to exception responses (FastAPI bug fix)
    @app.exception_handler(Exception)
    async def cors_exception_handler(request: Request, exc: Exception):
        origin = request.headers.get("origin")
        
        # Handle standard HTTP exceptions
        if hasattr(exc, 'status_code') and hasattr(exc, 'detail'):
            response = JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail}
            )
        else:
            # Handle unhandled exceptions
            logger.error(f"Unhandled exception: {exc}")
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"}
            )
        
        # Add CORS headers if origin is allowed
        if origin:
            if origin in origins:
                # Explicit match — safe to allow credentials
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Access-Control-Allow-Methods"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "*"
                response.headers["Vary"] = "Origin"
            elif "*" in origins:
                # Wildcard (dev only) — no credentials to avoid browser rejection
                response.headers["Access-Control-Allow-Origin"] = "*"
                response.headers["Access-Control-Allow-Methods"] = "*"
                response.headers["Access-Control-Allow-Headers"] = "*"
        
        return response
    
    # Rate Limiting Middleware
    app.state.limiter = default_limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    
    logger.info("Middleware initialized: CORS and Rate Limiting")
