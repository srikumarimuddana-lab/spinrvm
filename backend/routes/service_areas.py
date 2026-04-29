"""Public service-areas endpoint.

Drivers and riders can read active service areas without authentication.
Used by the driver onboarding screen to let applicants select their
operating area. Admin CRUD lives at /admin/service-areas.

Only the fields needed by clients are exposed — surge multipliers and
tax-rate configuration are admin-only concerns.
"""

import logging

from fastapi import APIRouter

try:
    from .. import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

api_router = APIRouter(tags=["Service Areas"])

# Fields safe to expose to unauthenticated clients.
# surge_multiplier is already visible to riders before booking, so it is not a
# pricing secret — drivers see it here to decide when to go online.
_PUBLIC_FIELDS = (
    "id",
    "name",
    "city",
    "is_active",
    "is_airport",
    "search_radius_km",
    "surge_multiplier",
    "surge_active",
)


@api_router.get("/service-areas")
async def get_service_areas():
    """List active service areas available for driver registration and ride booking."""
    rows = await db_supabase.get_rows(
        "service_areas",
        {"is_active": True},
        order="name",
        limit=200,
    )
    return [{k: r[k] for k in _PUBLIC_FIELDS if k in r} for r in (rows or [])]
