"""
routes/admin package — assembles admin_router from sub-routers.

server.py imports:
    from routes.admin import admin_router, admin_auth_router
"""

from fastapi import APIRouter

from .auth import admin_auth_router
from .auth import router as auth_router
from .documents import router as documents_router
from .drivers import router as drivers_router
from .faqs import router as faqs_router
from .maintenance import router as maintenance_router
from .messaging import router as messaging_router
from .promotions import router as promotions_router
from .rides import router as rides_router
from .service_areas import router as service_areas_router
from .settings import router as settings_router
from .staff import router as staff_router
from .subscriptions import router as subscriptions_router
from .support import router as support_router
from .users import router as users_router
from .vehicle_fleet import router as vehicle_fleet_router

admin_router = APIRouter(prefix="/admin", tags=["Admin"])

# Include all sub-routers (no prefix — /admin is already set above)
admin_router.include_router(auth_router)
admin_router.include_router(settings_router)
admin_router.include_router(service_areas_router)
admin_router.include_router(vehicle_fleet_router)
admin_router.include_router(drivers_router)
admin_router.include_router(rides_router)
admin_router.include_router(users_router)
admin_router.include_router(promotions_router)
admin_router.include_router(support_router)
admin_router.include_router(faqs_router)
admin_router.include_router(documents_router)
admin_router.include_router(staff_router)
admin_router.include_router(subscriptions_router)
admin_router.include_router(messaging_router)
admin_router.include_router(maintenance_router)

__all__ = ["admin_router", "admin_auth_router"]
