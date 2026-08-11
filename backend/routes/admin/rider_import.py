"""Admin bulk rider-import endpoints (CSV).

Two endpoints power the admin dashboard's Bulk Rider Import section:

- ``POST /api/admin/riders/import/validate`` — parse + validate the CSV
  and return a dry-run report (counts + warnings + errors + duplicates). No writes.
- ``POST /api/admin/riders/import/commit`` — re-parse + re-validate the same
  CSV and, only if there are no errors, create/update the user rows.

Reports carry only row numbers + field + message — never raw PII.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services import rider_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
except ImportError:
    from dependencies import get_admin_user
    from services import rider_import_service as import_svc
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CSV_BYTES = 1_000_000  # 1 MB
MAX_ROWS = 500


def _serialize_items(items: list[import_svc.ImportReportItem]) -> list[dict[str, Any]]:
    return [{"row_num": i.row_num, "field": i.field, "message": i.message} for i in items]


def _serialize_duplicates(dupes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "row": d["row"],
            "phone": d.get("phone", ""),
            "match_type": d.get("match_type", "rider"),
            "is_driver": d.get("is_driver", False),
            "existing_user_id": d.get("existing_user_id", ""),
        }
        for d in dupes
    ]


async def _read_csv_rows(riders_csv: UploadFile) -> list[dict[str, str]]:
    raw = await riders_csv.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from e
    try:
        rows = import_svc.read_csv_text(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import")
    return rows


def _report(plan: import_svc.RiderImportPlan, batch: str, total_rows: int) -> dict[str, Any]:
    return {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "rows": total_rows,
            "to_create": len(plan.users_to_create),
            "to_update": len(plan.users_to_update),
            "duplicates": len(plan.duplicates),
            "duplicate_drivers": sum(1 for d in plan.duplicates if d.get("match_type") == "driver"),
            # P0-C (docs/audit/2026-08-11-driver-rider-migration-audit.md):
            # rows matched to a pending_deletion/deleted account — PII was
            # not touched, flagged here for manual admin review.
            "protected_skips": sum(1 for d in plan.duplicates if d.get("match_type") == "protected_skip"),
        },
        "duplicates": _serialize_duplicates(plan.duplicates),
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }


@router.post("/riders/import/validate")
async def validate_rider_import(
    riders_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate the CSV and return the report. No writes."""
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows = await _read_csv_rows(riders_csv)
    plan = await asyncio.to_thread(import_svc.build_plan, rows, batch)
    return _report(plan, batch, len(rows))


@router.post("/riders/import/commit")
async def commit_rider_import(
    riders_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the CSV and, only if clean, create/update the user rows."""
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows = await _read_csv_rows(riders_csv)
    plan = await asyncio.to_thread(import_svc.build_plan, rows, batch)

    if plan.errors:
        return {**_report(plan, batch, len(rows)), "committed": False}

    try:
        await asyncio.to_thread(import_svc.commit_plan, plan)
    except Exception as e:
        logger.error("rider bulk-import commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Import commit failed; no changes may have been applied") from e

    created = len(plan.users_to_create)
    updated = len(plan.users_to_update)
    await log_admin_action(
        admin,
        "rider_bulk_import",
        "users",
        batch,
        {"created_users": created, "updated_users": updated, "duplicates": len(plan.duplicates)},
    )
    return {
        "batch": batch,
        "committed": True,
        "created_users": created,
        "updated_users": updated,
        "duplicates": _serialize_duplicates(plan.duplicates),
        "warnings": _serialize_items(plan.warnings),
    }
