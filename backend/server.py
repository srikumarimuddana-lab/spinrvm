from fastapi import FastAPI, APIRouter
from .core.config import settings
from .core.database import db, init_db
from .core.middleware import init_middleware
from .core.lifespan import lifespan
from .core.security import init_firebase
from .api.v1 import api_router as v1_api_router
from .api.v1.admin import api_router as v1_admin_router
from .api.legacy import api_router as legacy_api_router

# Initialize Firebase
init_firebase()

app = FastAPI(title="Spinr API", version="1.0.0", lifespan=lifespan)

# Initialize middleware
init_middleware(app)

# Initialize database
init_db()

# Include API routers
app.include_router(v1_api_router, prefix="/api/v1")
app.include_router(v1_admin_router, prefix="/api/v1/admin")
app.include_router(legacy_api_router, prefix="/api")

# Configure structured logging with Loguru
from loguru import logger
import sys

# Remove default handler and add custom JSON handler
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} | {message}",
    serialize=True  # This enables JSON formatting
)

# Add file logging for production
logger.add(
    "logs/app.log",
    rotation="500 MB",
    retention="7 days",
    level="INFO",
    serialize=True
)

# Configure Sentry for error monitoring
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteMiddleware
from sentry_sdk.integrations.logging import LoggingIntegration

sentry_dsn = settings.sentry_dsn if hasattr(settings, 'sentry_dsn') and settings.sentry_dsn else None

if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[
            FastApiIntegration(
                transaction_style="url"
            ),
            StarletteMiddleware(),
            LoggingIntegration(
                event_level="ERROR",
                breadcrumb_level="WARNING"
            )
        ],
        traces_sample_rate=0.1,  # Capture 10% of transactions for performance monitoring
        profiles_sample_rate=0.1,  # Profile 10% of transactions
        environment=settings.environment if hasattr(settings, 'environment') else 'production',
        send_default_pii=True  # Include user info (Firebase authenticated users)
    )
    logger.info("Sentry SDK initialized for error monitoring")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
