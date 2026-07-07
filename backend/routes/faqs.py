"""Public FAQ endpoint.

Drivers and riders read FAQs without authentication. Admin CRUD lives
at /admin/faqs. Only active entries are exposed here; admins toggle
`is_active` to hide drafts from clients.
"""

import logging

from fastapi import APIRouter, Query

try:
    from .. import db_supabase
except ImportError:
    import db_supabase

logger = logging.getLogger(__name__)

api_router = APIRouter(tags=["FAQs"])


async def _resolve_area_scope(service_area_id: str | None, lat: float | None, lng: float | None) -> set:
    """Service-area ids the caller is in (the area plus its ancestors). Explicit
    service_area_id wins; otherwise resolve from lat/lng. Empty set when neither
    is usable (→ only global FAQs are returned)."""
    try:
        from .fares import resolve_area_scope, resolve_service_area_for_point
    except ImportError:
        from routes.fares import resolve_area_scope, resolve_service_area_for_point
    try:
        area_id = service_area_id
        if not area_id and lat is not None and lng is not None:
            area = await resolve_service_area_for_point(float(lat), float(lng))
            area_id = area.get("id") if area else None
        return await resolve_area_scope(area_id)
    except Exception:
        logger.error("public faq service-area resolve failed", exc_info=True)
        return set()


@api_router.get("/faqs")
async def get_public_faqs(
    category: str | None = Query(None),
    audience: str | None = Query(None, pattern="^(rider|driver)$"),
    service_area_id: str | None = Query(None),
    lat: float | None = Query(None),
    lng: float | None = Query(None),
):
    """List active FAQ entries, optionally filtered by category, audience and
    location.

    When `audience` is given, returns rows tagged for that audience plus rows
    tagged 'both'. Location (explicit `service_area_id`, or `lat`+`lng` resolved
    to an area) scopes results to global FAQs plus those tagged for that area;
    without location context only global FAQs are returned.
    """
    filters: dict = {"is_active": True}
    if category:
        filters["category"] = category

    if audience:
        # Match rows tagged for this audience plus rows tagged 'both'.
        filters["audience"] = {"$in": ["both", audience]}

    faqs = (
        await db_supabase.get_rows(
            "faqs",
            filters,
            order="created_at",
            desc=True,
            limit=500,
            # Exclude the semantic-search embedding vector (JSONB, thousands of
            # floats) from public FAQ responses — clients only need display fields.
            columns="id,question,answer,category,sort_order,is_active,created_at,updated_at,audience,service_area_ids",
        )
        or []
    )

    # Global FAQs (no service areas) always show; area-tagged FAQs only when the
    # caller's area (or an ancestor) is in the tag list. No location → global only.
    scope = await _resolve_area_scope(service_area_id, lat, lng)
    return [f for f in faqs if not f.get("service_area_ids") or (set(f["service_area_ids"]) & scope)]
