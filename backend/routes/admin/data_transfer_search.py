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


def _text_filter(q: str, table: str) -> dict:
    """Build an $or clause matching q against the real name/contact columns
    for `table`, case-insensitive substring — the same ILIKE shape
    driver_import_service and other admin search endpoints already rely on
    via $regex/$options.

    Regression (found via real staging schema, not assumed): this endpoint
    previously filtered/selected on columns that don't exist —
    - `full_name` doesn't exist on either table (`users` has first_name/
      last_name only; `drivers` has first_name/last_name plus a separate
      `name` column).
    - `drivers` has NO `email` column at all (email lives on `users`,
      joined via `drivers.user_id`) — so a driver-scoped search including
      `email` in the filter/select 500s too, independent of the full_name
      bug.
    - `drivers`' plate column is `license_plate`, not `vehicle_plate`.
    Every one of these made a real Postgres "column does not exist" error,
    surfaced to the admin as a generic 503 "Database operation failed" —
    only caught once a real admin exercised this route, since every test
    here mocks `db_supabase.get_rows` and never touches the real schema.
    Table-parameterized now so drivers (no email) and users (has email)
    never share a filter clause referencing a column the other lacks."""
    clause = {"$regex": q, "$options": "i"}
    if table == "drivers":
        return {"$or": [{"first_name": clause}, {"last_name": clause}, {"name": clause}, {"phone": clause}]}
    return {"$or": [{"first_name": clause}, {"last_name": clause}, {"email": clause}, {"phone": clause}]}


def _with_full_name(rows: list[dict]) -> list[dict]:
    """Synthesize `full_name` for the UI (EntitySearchTable.tsx reads
    `row.full_name`) since neither source table has that column. Prefers
    `drivers.name` when present (a real, populated column there — see the
    compliance module's insurance-period-audit fix); falls back to
    first_name + last_name for everyone else."""
    out = []
    for row in rows:
        concatenated = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        out.append({**row, "full_name": row.get("name") or concatenated or None})
    return out


def _build_filters(
    q: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    status: Optional[str] = None,
    table: str = "users",
) -> dict:
    filters: dict = {}
    if q:
        filters.update(_text_filter(q, table))
    if date_from or date_to:
        range_filter: dict = {}
        if date_from:
            range_filter["$gte"] = date_from
        if date_to:
            range_filter["$lte"] = date_to
        filters["created_at"] = range_filter
    if status:
        filters["status"] = status
    return filters


@router.get("/data-transfer/search")
@data_transfer_search_limit
async def search_entities(
    request: Request,
    q: Optional[str] = Query(None, description="Fuzzy match against name/email/phone"),
    entity_type: Optional[str] = Query(None, pattern="^(driver|rider)$"),
    date_from: Optional[str] = Query(None, description="ISO date, filters on created_at >="),
    date_to: Optional[str] = Query(None, description="ISO date, filters on created_at <="),
    # `drivers.status` (active/pending/needs_review/suspended/banned) and
    # `users.status` are the same column name on both tables — real values
    # differ per table (confirmed against the real schema; drivers has 5
    # distinct statuses, users effectively only "active" today), so this is
    # deliberately a free-text exact match rather than a shared enum that
    # would either over- or under-constrain one of the two tables.
    status: Optional[str] = Query(None, description="Exact match on status (e.g. active, suspended, banned)"),
    # `service_area_id` only exists on `drivers` (not `users`) — see the
    # blast-radius check in data_transfer_search.py's driver branch below;
    # applied there only, same pattern as the rider branch's `role` merge.
    service_area_id: Optional[str] = Query(None, description="Exact match on drivers.service_area_id"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=MAX_PAGE_SIZE),
    admin: dict = Depends(get_admin_user),
):
    """Search users (optionally scoped to drivers or riders) by fuzzy text +
    date range. Returns the current page plus a total_count for "select all
    matching filter" in the UI."""
    offset = (page - 1) * page_size

    if entity_type == "driver":
        # Drivers are a separate table keyed by user_id; search on the
        # drivers table's own name/phone columns (denormalized onto drivers
        # for exactly this kind of admin lookup — see drivers_router).
        # Drivers has no `email` column (email lives on `users`, joined via
        # user_id) — _build_filters(table="drivers") excludes it.
        table = "drivers"
        table_filters = _build_filters(q, date_from, date_to, status, table=table)
        if service_area_id:
            table_filters["service_area_id"] = service_area_id
        rows = await db_supabase.get_rows(
            table,
            table_filters,
            order="created_at",
            desc=True,
            limit=page_size,
            offset=offset,
            columns="id,user_id,name,first_name,last_name,phone,created_at,license_plate,status,service_area_id",
        )
        rows = _with_full_name(rows or [])
        # UI (EntitySearchTable.tsx) reads `vehicle_plate` — the real
        # column is `license_plate`; rename in the response rather than
        # coupling the frontend's field name to the DB's.
        #
        # Regression: `id` here was `drivers.id`, but every other consumer
        # of this module's entity_id (entity_export_service.gather_entity_bundle,
        # sgi_forms.py's driver lookup) treats entity_id as `users.id` —
        # selecting a driver from THIS search mode made export 404 even
        # though SGI forms happened to work (and vice versa for the default/
        # mixed search below, which already returned users.id). Standardize
        # on users.id here too: drop the drivers.id, expose user_id as `id`.
        rows = [
            {
                **{k: v for k, v in r.items() if k not in ("license_plate", "id", "user_id")},
                "id": r.get("user_id"),
                "vehicle_plate": r.get("license_plate"),
            }
            for r in rows
        ]
    elif entity_type == "rider":
        table = "users"
        table_filters = {**_build_filters(q, date_from, date_to, status, table=table), "role": "rider"}
        rows = await db_supabase.get_rows(
            table,
            table_filters,
            order="created_at",
            desc=True,
            limit=page_size,
            offset=offset,
            columns="id,first_name,last_name,email,phone,created_at,role,status",
        )
        rows = _with_full_name(rows or [])
    else:
        table = "users"
        table_filters = _build_filters(q, date_from, date_to, status, table=table)
        rows = await db_supabase.get_rows(
            table,
            table_filters,
            order="created_at",
            desc=True,
            limit=page_size,
            offset=offset,
            columns="id,first_name,last_name,email,phone,created_at,role,status",
        )
        rows = _with_full_name(rows or [])

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
