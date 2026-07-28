"""Unified search across users+drivers for the Data Transfer module's
Search & Select tab.

Combines a fuzzy text search (name/email/phone, ILIKE-based via
db_supabase's $regex operator — no extension/index dependency beyond what
every other admin search in this codebase already uses) with an optional
created_at date range. Returns a total count separate from the page of rows
so the UI can offer "select all N matching this filter," not just the
visible page.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...utils.rate_limiter import data_transfer_search_limit
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from utils.rate_limiter import data_transfer_search_limit

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_PAGE_SIZE = 200


def _text_filter(q: str) -> dict:
    """Build an $or clause matching q against full_name/email/phone,
    case-insensitive substring — the same ILIKE shape driver_import_service
    and other admin search endpoints already rely on via $regex/$options."""
    clause = {"$regex": q, "$options": "i"}
    return {"$or": [{"full_name": clause}, {"email": clause}, {"phone": clause}]}


def _build_filters(q: Optional[str], date_from: Optional[str], date_to: Optional[str]) -> dict:
    filters: dict = {}
    if q:
        filters.update(_text_filter(q))
    if date_from or date_to:
        range_filter: dict = {}
        if date_from:
            range_filter["$gte"] = date_from
        if date_to:
            range_filter["$lte"] = date_to
        filters["created_at"] = range_filter
    return filters


@router.get("/data-transfer/search")
@data_transfer_search_limit
async def search_entities(
    request: Request,
    q: Optional[str] = Query(None, description="Fuzzy match against name/email/phone"),
    entity_type: Optional[str] = Query(None, pattern="^(driver|rider)$"),
    date_from: Optional[str] = Query(None, description="ISO date, filters on created_at >="),
    date_to: Optional[str] = Query(None, description="ISO date, filters on created_at <="),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    admin: dict = Depends(get_admin_user),
):
    """Search users (optionally scoped to drivers or riders) by fuzzy text +
    date range. Returns the current page plus a total_count for "select all
    matching filter" in the UI."""
    filters = _build_filters(q, date_from, date_to)
    offset = (page - 1) * page_size

    if entity_type == "driver":
        # Drivers are a separate table keyed by user_id; search on the
        # drivers table's own name/email/phone columns (denormalized onto
        # drivers for exactly this kind of admin lookup — see drivers_router).
        table, table_filters = "drivers", filters
        rows = await db_supabase.get_rows(
            table,
            table_filters,
            order="created_at",
            desc=True,
            limit=page_size,
            offset=offset,
            columns="id,user_id,full_name,email,phone,created_at,vehicle_plate",
        )
    elif entity_type == "rider":
        table, table_filters = "users", {**filters, "role": "rider"}
        rows = await db_supabase.get_rows(
            table,
            table_filters,
            order="created_at",
            desc=True,
            limit=page_size,
            offset=offset,
            columns="id,full_name,email,phone,created_at,role",
        )
    else:
        table, table_filters = "users", filters
        rows = await db_supabase.get_rows(
            table,
            table_filters,
            order="created_at",
            desc=True,
            limit=page_size,
            offset=offset,
            columns="id,full_name,email,phone,created_at,role",
        )

    # count_documents uses PostgREST's count="exact" head-count, not a full
    # row fetch — critical for "select all N matching filter" on a table with
    # thousands of rows (fetching every row just to len() it would violate
    # the "no unbounded reads on admin dashboards" anti-pattern in CLAUDE.md).
    total_count = await db_supabase.count_documents(table, table_filters)

    return {
        "rows": rows or [],
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
    }
