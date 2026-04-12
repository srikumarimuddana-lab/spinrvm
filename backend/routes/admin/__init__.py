"""
routes/admin package — assembles admin_router from sub-routers.

server.py imports:
    from routes.admin import admin_router, admin_auth_router

Authentication
--------------
Every route mounted on ``admin_router`` is automatically gated behind
``Depends(get_admin_user)`` via the router-level ``dependencies`` arg.
Individual sub-routers (drivers.py, staff.py, rides.py, etc.) therefore
do NOT need to repeat the dependency on each handler, and new
sub-routers are auth-gated by default rather than opt-in — which is
exactly the opposite of how this package worked before 4d75c28-follow-up:
13 of 14 sub-routers had ZERO auth, so any unauthenticated caller
could `POST /api/admin/staff` to create themselves as a super-admin.

``admin_auth_router`` (login / session / logout) is mounted directly
by server.py as a separate router and stays public so the dashboard
can reach /api/admin/auth/login without a token.
"""

from fastapi import APIRouter, Depends

try:
    from ...dependencies import get_admin_user
except ImportError:
    from dependencies import get_admin_user

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

# Router-level dependency: every request that lands on an admin_router
# sub-route must carry a valid JWT whose payload resolves to a user
# with an admin role (see dependencies.get_admin_user).
admin_router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(get_admin_user)],
)

# Include all sub-routers (no prefix — /admin is already set above).
# `auth_router` is an empty placeholder re-exported from .auth for
# include-order symmetry; the real login/session/logout routes live
# on `admin_auth_router`, which server.py mounts separately so it
# stays public.
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
