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
    from ..geo_utils import get_service_area_polygon
except ImportError:
    import db_supabase
    from geo_utils import get_service_area_polygon

logger = logging.getLogger(__name__)

api_router = APIRouter(tags=["Service Areas"])

# Fields safe to expose to unauthenticated clients.
# surge_multiplier is already visible to riders before booking, so it is not a
# pricing secret — drivers see it here to decide when to go online.
# polygon is the service-area boundary as [{lat, lng}, ...] — needed by all map
# surfaces (driver idle map, rider booking map, ride offer overlay).
_PUBLIC_FIELDS = (
    "id",
    "name",
    "city",
    "is_active",
    "is_airport",
    "search_radius_km",
    "surge_multiplier",
    "surge_active",
    "surge_enabled",
    "polygon",
)


def _project_public(r: dict) -> dict:
    out = {k: r[k] for k in _PUBLIC_FIELDS if k in r}
    # Normalize polygon to [{lat, lng}, ...] regardless of storage format
    # (admin dashboard saves GeoJSON dicts; older rows may have plain arrays).
    out["polygon"] = get_service_area_polygon(r)
    # Surge is per-area admin-gated. A stale surge_active flag or multiplier > 1
    # must never surface to clients (or drive rider/driver surge UI) unless an
    # operator has enabled surge for this area via the admin panel.
    if not r.get("surge_enabled"):
        out["surge_active"] = False
        out["surge_multiplier"] = 1.0
    return out


@api_router.get("/service-areas")
async def get_service_areas():
    """List active top-level service areas for driver registration and ride booking.

    Airport zones are excluded two ways, because either modeling can occur in
    the data and a picker must never show an airport regardless:
      - ``parent_service_area_id IS NULL`` drops *correctly* modeled airport
        zones, which are sub-regions of a city (the admin route enforces that
        ``is_airport`` may only be set on a child row).
      - ``is_airport != true`` drops any row flagged as an airport even if it
        was hand-created/legacy-imported as a top-level row. The admin guard
        only prevents *new* top-level airports; older parent rows like
        "Regina Airport" can still exist and would otherwise leak in.
    """
    rows = await db_supabase.get_rows(
        "service_areas",
        {
            "is_active": True,
            "parent_service_area_id": None,
            "is_airport": {"$ne": True},
        },
        order="name",
        limit=200,
    )
    return [_project_public(r) for r in (rows or [])]


_AIRPORT_FIELDS = ("id", "name", "is_airport", "airport_fee", "polygon")


def _project_airport(r: dict) -> dict:
    out = {k: r[k] for k in _AIRPORT_FIELDS if k in r}
    out["polygon"] = get_service_area_polygon(r)
    return out


@api_router.get("/service-areas/{area_id}/airport-zones")
async def get_airport_zones(area_id: str):
    """Return active airport sub-zones for a parent service area.

    Used by the driver idle map to overlay airport zone polygons (HM-21).
    """
    rows = await db_supabase.get_rows(
        "service_areas",
        {
            "parent_service_area_id": area_id,
            "is_airport": True,
            "is_active": True,
        },
        order="name",
        limit=20,
    )
    return [_project_airport(r) for r in (rows or [])]
