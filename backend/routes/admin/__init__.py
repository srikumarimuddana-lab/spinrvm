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

Auth coverage audit — 2026-05-06
----------------------------------
All admin API endpoints have been audited for authentication gating.
Coverage is provided by two complementary mechanisms:

1. Router-level dependency (this file):
   ``admin_router = APIRouter(dependencies=[Depends(get_admin_user)])``
   covers every sub-router included via ``admin_router.include_router()``.
   This is the primary gate for all sub-routers in this package.

2. Per-handler ``Depends(get_admin_user)`` in function signatures:
   Used by ``routes/admin/monitoring.py``, which is mounted directly on
   ``app`` (not via ``admin_router``) in server.py. Every endpoint in
   that file carries the dependency individually.

Excluded from ``admin_router`` by design (public):
   - ``admin_auth_router`` (login, session, logout, MFA, refresh, unlock)
     — mounted separately by server.py; login must be reachable pre-auth.

No unprotected admin API endpoints exist as of this audit.
"""

from fastapi import APIRouter, Depends

try:
    from ...dependencies import get_admin_user, require_module
except ImportError:
    from dependencies import get_admin_user, require_module

try:
    from ..disputes import admin_router as disputes_admin_router
except ImportError:
    from routes.disputes import admin_router as disputes_admin_router

from .ai_console import router as ai_console_router
from .analytics import api_router as analytics_router
from .auth import admin_auth_router
from .auth import router as auth_router
from .documents import router as documents_router
from .driver_import import router as driver_import_router
from .drivers import router as drivers_router
from .faqs import router as faqs_router
from .incentives import router as incentives_router
from .legal_documents import router as legal_documents_router
from .maintenance import router as maintenance_router
from .messaging import router as messaging_router
from .monitoring import router as monitoring_router
from .promotions import router as promotions_router
from .rides import router as rides_router
from .safety import router as safety_router
from .service_areas import router as service_areas_router
from .settings import router as settings_router
from .staff import router as staff_router
from .stripe_import import router as stripe_import_router
from .subscriptions import offer_analytics_router
from .subscriptions import router as subscriptions_router
from .support import router as support_router
from .support_tickets import router as support_tickets_router
from .users import router as users_router
from .vehicle_fleet import router as vehicle_fleet_router
from .venues import router as venues_router
from .wallet import router as wallet_router

# Router-level dependency: every request that lands on an admin_router
# sub-route must carry a valid JWT whose payload resolves to a user
# with an admin role (see dependencies.get_admin_user).
admin_router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(get_admin_user)],
)

# Include all sub-routers with module-level RBAC (audit [03-1]).
# require_module() raises 403 when the caller's JWT modules claim does not
# include the required module. super_admin always passes.
# `auth_router` is an empty placeholder; the real login/session/logout routes
# live on `admin_auth_router`, mounted separately by server.py (no auth gate).
admin_router.include_router(auth_router)
# No module gate — the routes enforce role == super_admin themselves
# (impersonation + chat-history reads are stricter than any module grant).
admin_router.include_router(ai_console_router)
admin_router.include_router(settings_router, dependencies=[Depends(require_module("settings"))])
admin_router.include_router(service_areas_router, dependencies=[Depends(require_module("service_areas"))])
admin_router.include_router(venues_router, dependencies=[Depends(require_module("service_areas"))])
admin_router.include_router(vehicle_fleet_router, dependencies=[Depends(require_module("vehicle_types"))])
admin_router.include_router(drivers_router, dependencies=[Depends(require_module("drivers"))])
admin_router.include_router(driver_import_router, dependencies=[Depends(require_module("drivers"))])
# Legacy Stripe mapping import (drivers + riders kinds) — migration ops
# tooling, gated like the bulk driver import it mirrors.
admin_router.include_router(stripe_import_router, dependencies=[Depends(require_module("drivers"))])
admin_router.include_router(rides_router, dependencies=[Depends(require_module("rides"))])
admin_router.include_router(users_router, dependencies=[Depends(require_module("users"))])
admin_router.include_router(promotions_router, dependencies=[Depends(require_module("promotions"))])
admin_router.include_router(support_router, dependencies=[Depends(require_module("support"))])
# support_tickets sub-router enforces require_module("support_tickets") per-handler
# (the dashboard/trends/ticket routes carry it explicitly); config routes use
# get_admin_user so any admin can connect the integration.
admin_router.include_router(support_tickets_router)
admin_router.include_router(safety_router, dependencies=[Depends(require_module("support"))])
admin_router.include_router(faqs_router, dependencies=[Depends(require_module("support"))])
admin_router.include_router(legal_documents_router, dependencies=[Depends(require_module("documents"))])
admin_router.include_router(documents_router, dependencies=[Depends(require_module("documents"))])
admin_router.include_router(staff_router, dependencies=[Depends(require_module("staff"))])
admin_router.include_router(subscriptions_router, dependencies=[Depends(require_module("earnings"))])
admin_router.include_router(offer_analytics_router, dependencies=[Depends(require_module("dashboard"))])
admin_router.include_router(messaging_router, dependencies=[Depends(require_module("notifications"))])
admin_router.include_router(maintenance_router, dependencies=[Depends(require_module("dashboard"))])
admin_router.include_router(analytics_router, dependencies=[Depends(require_module("dashboard"))])
admin_router.include_router(monitoring_router, dependencies=[Depends(require_module("dashboard"))])
admin_router.include_router(wallet_router, dependencies=[Depends(require_module("earnings"))])
admin_router.include_router(incentives_router, dependencies=[Depends(require_module("service_areas"))])
admin_router.include_router(disputes_admin_router, dependencies=[Depends(require_module("disputes"))])

__all__ = ["admin_router", "admin_auth_router"]
