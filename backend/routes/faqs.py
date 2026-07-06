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


@api_router.get("/faqs")
async def get_public_faqs(
    category: str | None = Query(None),
    audience: str | None = Query(None, pattern="^(rider|driver)$"),
):
    """List active FAQ entries, optionally filtered by category and audience.

    When `audience` is given, returns rows tagged for that audience plus rows
    tagged 'both'. When omitted, returns all active rows (legacy behavior).
    """
    filters: dict = {"is_active": True}
    if category:
        filters["category"] = category

    if audience:
        # Match rows tagged for this audience plus rows tagged 'both'.
        filters["audience"] = {"$in": ["both", audience]}

    faqs = await db_supabase.get_rows(
        "faqs",
        filters,
        order="created_at",
        desc=True,
        limit=500,
        # Exclude the semantic-search embedding vector (JSONB, thousands of
        # floats) from public FAQ responses — clients only need display fields.
        columns="id,question,answer,category,sort_order,is_active,created_at,updated_at,audience",
    )
    return faqs or []
