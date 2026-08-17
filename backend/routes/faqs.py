"""Public FAQ endpoint.

Drivers and riders read FAQs without authentication. Admin CRUD lives
at /admin/faqs. Only active entries are exposed here; admins toggle
`is_active` to hide drafts from clients.

NOTE (found 2026-08-17, not resolved here — flagged to the user instead of
unilaterally deleting/reordering anything): `features.py`'s `support_router`
also defines a public `GET /faqs` handler, and is included into
`v1_api_router` *before* this module's `faqs_router` (see `backend/server.py`).
Starlette matches the first-registered route for a given path+method, so this
module's `get_public_faqs` never actually runs in production —
`features.py::get_faqs` is the live handler for `GET /api/v1/faqs` (its
sort_order ordering was fixed separately, alongside this file's). This file's
own ordering fix below is harmless but currently inert. Kept here for
correctness/consistency rather than removed, in case the shadowing is
intentionally resolved later (e.g. by deleting whichever implementation is
meant to be retired) — that's a genuine two-implementations situation, not a
decision to make unilaterally inside an unrelated fix.
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
    visible = [f for f in faqs if not f.get("service_area_ids") or (set(f["service_area_ids"]) & scope)]

    # sort_order asc is the display order within a category (both apps group
    # client-side by category and otherwise trust this array's order); rows
    # sharing a sort_order (e.g. the default 0) keep the created_at desc order
    # already fetched above, since list.sort() is stable.
    visible.sort(key=lambda f: f.get("sort_order") or 0)
    return visible
