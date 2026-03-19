import sys
import os
# Add the current directory to Python path to allow absolute imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, APIRouter
from core.config import settings
from core.middleware import init_middleware
from core.lifespan import lifespan
from core.security import init_firebase
from routes.rides import api_router as rides_router
from routes.drivers import api_router as drivers_router
from routes.admin import admin_router as admin_router, admin_auth_router
from routes.corporate_accounts import router as corporate_accounts_router
from routes.auth import api_router as auth_router

# Initialize Firebase
init_firebase()

app = FastAPI(title="Spinr API", version="1.0.0", lifespan=lifespan)

# Initialize middleware
init_middleware(app)

# Create v1 API router
v1_api_router = APIRouter()
v1_api_router.include_router(rides_router)
v1_api_router.include_router(drivers_router)
v1_api_router.include_router(admin_router)
v1_api_router.include_router(admin_auth_router)
v1_api_router.include_router(corporate_accounts_router)

# Include API routers
app.include_router(v1_api_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")

# Mount admin routes under /api so the admin dashboard can reach them at /api/admin/...
app.include_router(admin_router, prefix="/api")
app.include_router(admin_auth_router, prefix="/api")
app.include_router(corporate_accounts_router, prefix="/api")

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
try:
    from sentry_sdk.integrations.starlette import StarletteMiddleware
except ImportError:
    StarletteMiddleware = None
from sentry_sdk.integrations.logging import LoggingIntegration

sentry_dsn = settings.sentry_dsn if hasattr(settings, 'sentry_dsn') and settings.sentry_dsn else None

if sentry_dsn:
    integrations = [
        FastApiIntegration(
            transaction_style="url"
        ),
        LoggingIntegration(
            event_level="ERROR",
            breadcrumb_level="WARNING"
        )
    ]
    # Add StarletteMiddleware if available
    if StarletteMiddleware is not None:
        integrations.append(StarletteMiddleware())
    
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=integrations,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=settings.ENV if hasattr(settings, 'ENV') else 'production',
        send_default_pii=True
    )
    logger.info("Sentry SDK initialized for error monitoring")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
