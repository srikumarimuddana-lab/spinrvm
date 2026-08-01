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
    from ...dependencies import get_admin_user, require_module, require_super_admin
except ImportError:
    from dependencies import get_admin_user, require_module, require_super_admin

try:
    from ..disputes import admin_router as disputes_admin_router
except ImportError:
    from routes.disputes import admin_router as disputes_admin_router

from .ai_console import router as ai_console_router
from .analytics import api_router as analytics_router
from .auth import admin_auth_router
from .auth import router as auth_router
from .booking_import import router as booking_import_router
from .compliance import api_router as compliance_router
from .data_transfer_export import router as data_transfer_export_router
from .data_transfer_import import router as data_transfer_import_router
from .data_transfer_jobs import router as data_transfer_jobs_router
from .data_transfer_search import router as data_transfer_search_router
from .documents import router as documents_router
from .driver_import import router as driver_import_router
from .driver_statements import router as driver_statements_router
from .drivers import router as drivers_router
from .export_approvals import router as export_approvals_router
from .faqs import router as faqs_router
from .incentives import router as incentives_router
from .legal_documents import router as legal_documents_router
from .maintenance import router as maintenance_router
from .messaging import router as messaging_router
from .monitoring import router as monitoring_router
from .promotions import router as promotions_router
from .rider_import import router as rider_import_router
from .rides import router as rides_router
from .safety import router as safety_router
from .sentry import router as sentry_router
from .service_areas import router as service_areas_router
from .settings import router as settings_router
from .sgi_forms import router as sgi_forms_router
from .staff import router as staff_router
from .stripe_import import router as stripe_import_router
from .stripe_payout_sync import router as stripe_payout_sync_router
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
# Driver earnings statements (payout section: date-filter -> download / email
# to driver). Read-only + driver-addressed email; drivers module grant.
admin_router.include_router(driver_statements_router, dependencies=[Depends(require_module("drivers"))])
# Legacy Stripe mapping import (drivers + riders kinds) — migration ops
# tooling, gated like the bulk driver import it mirrors.
admin_router.include_router(stripe_import_router, dependencies=[Depends(require_module("drivers"))])
# Stripe payout-history sync (legacy migration: rebuild payouts from Stripe
# Transfer truth). Writes to payouts, so it takes the booking-import posture:
# require_super_admin at the mount AND re-checked inside each handler.
admin_router.include_router(stripe_payout_sync_router, dependencies=[Depends(require_super_admin)])
# Data Transfer module (export/import users+drivers with docs/history between
# Spinr's own environments) — gated on require_super_admin, not a module flag.
# Previously gated on require_module("bulk_operations"); that module string
# is not in AVAILABLE_MODULES/ALL_MODULES (routes/admin/staff.py,
# routes/admin/auth.py) or any ROLE_PRESETS, and the "custom" role grant path
# filters against AVAILABLE_MODULES — so no non-super_admin could actually
# ever hold it. Effective access was already super_admin-only, but only by
# omission: a future engineer adding "bulk_operations" to the grantable list
# for an unrelated feature would have silently reopened this full-fidelity,
# unredacted PII export/import surface with no signal that they'd done
# anything sensitive. require_super_admin makes the boundary explicit and
# independent of what's in the grantable module list. See
# docs/privacy/2026-07-28-pia-data-transfer-export.md R-A and
# ACTION_ITEMS.md B11.
admin_router.include_router(data_transfer_export_router, dependencies=[Depends(require_super_admin)])
admin_router.include_router(data_transfer_import_router, dependencies=[Depends(require_super_admin)])
admin_router.include_router(data_transfer_search_router, dependencies=[Depends(require_super_admin)])
admin_router.include_router(data_transfer_jobs_router, dependencies=[Depends(require_super_admin)])
admin_router.include_router(sgi_forms_router, dependencies=[Depends(require_super_admin)])
# ACTION_ITEMS.md B10 -- approving/denying an export request is at least as
# sensitive as the export routes it gates; same require_super_admin posture.
admin_router.include_router(export_approvals_router, dependencies=[Depends(require_super_admin)])
# Legacy booking import (previous-app ride history -> rides + offsetting
# payouts). Same require_super_admin boundary as Data Transfer, for the same
# reason plus one more: it writes to rides and payouts, the two tables
# get_driver_balance reads to bound a Stripe payout Transfer. The handlers
# re-check the role themselves so the guard survives a future re-mount.
admin_router.include_router(booking_import_router, dependencies=[Depends(require_super_admin)])
admin_router.include_router(rides_router, dependencies=[Depends(require_module("rides"))])
admin_router.include_router(users_router, dependencies=[Depends(require_module("users"))])
admin_router.include_router(rider_import_router, dependencies=[Depends(require_module("users"))])
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
admin_router.include_router(compliance_router, dependencies=[Depends(require_module("compliance"))])
# Live Sentry issue viewer (read production errors across surfaces + resolve
# them). Raw production error data is super_admin-only — same posture as the
# data-transfer / booking-import surfaces above.
admin_router.include_router(sentry_router, dependencies=[Depends(require_super_admin)])

__all__ = ["admin_router", "admin_auth_router"]
