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
async def get_public_faqs(category: str | None = Query(None)):
    """List active FAQ entries, optionally filtered by category."""
    filters: dict = {"is_active": True}
    if category:
        filters["category"] = category

    faqs = await db_supabase.get_rows(
        "faqs",
        filters,
        order="created_at",
        desc=True,
        limit=500,
    )
    return faqs or []
