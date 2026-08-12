"""Admin bulk driver-import endpoints (CSV metadata import).

Two endpoints power the admin dashboard's Bulk Import page:

- ``POST /api/admin/drivers/import/validate`` — parse + validate the drivers CSV
  and return a dry-run report (counts + warnings + errors). No writes.
- ``POST /api/admin/drivers/import/commit`` — re-parse + re-validate the same
  CSV and, only if there are no errors, create the user + driver rows.

Document *files* are NOT part of this flow (build_plan is called with
files_root=None); they are uploaded per-driver afterwards via the manual upload
endpoint. The heavy lifting lives in ``services/driver_import_service.py`` and
is shared with the Saskatoon CLI script.

Idempotency / re-commit safety: the service's resume path matches previously
imported drivers on ``legacy_import_metadata.old_driver_id + source`` and skips
them with a warning, so a re-sent commit converges rather than duplicating. Two
*simultaneous* commits of the same CSV could still double-insert; the UI guards
against this by disabling the button while a request is in flight, which is
acceptable for an internal ops tool.

Reports carry only ``old_driver_id`` / ``field`` / ``message`` — never raw PII
(names, phones, DOBs) — per the PIPEDA rules in CLAUDE.md.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services import driver_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.driver_import_token import (
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from ...utils.rate_limiter import driver_import_commit_limit
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import driver_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.driver_import_token import (  # noqa: F401
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from utils.rate_limiter import driver_import_commit_limit  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrails for a synchronous request handler. The commit is two batch inserts
# so it stays fast; the cap keeps a runaway upload from blocking the event loop.
MAX_CSV_BYTES = 1_000_000  # 1 MB
MAX_ROWS = 500


def _serialize_items(items: list[import_svc.ImportErrorItem]) -> list[dict[str, str]]:
    return [{"old_driver_id": i.old_driver_id, "field": i.field, "message": i.message} for i in items]


async def _read_csv_rows(drivers_csv: UploadFile) -> tuple[list[dict[str, str]], str]:
    """Return the parsed rows plus a sha256 of the raw CSV bytes.

    The hash binds a validate-minted token to this exact file content (gap
    #45) — re-parsing alone can't tell the commit call whether the CSV
    changed since validate, since two different files can parse to
    superficially similar row counts.
    """
    raw = await drivers_csv.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit")
    csv_sha256 = hashlib.sha256(raw).hexdigest()
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
    return rows, csv_sha256


async def _build_plan(
    rows: list[dict[str, str]],
    service_area_id: Optional[str],
    service_area_name: Optional[str],
    batch: str,
) -> Any:
    """Resolve the service area and build the plan off the request thread.

    ``get_service_area`` and ``build_plan`` are synchronous and hit Supabase, so
    they run in a worker thread to avoid blocking the event loop.
    """
    try:
        service_area = await asyncio.to_thread(
            import_svc.get_service_area, service_area_id, service_area_name or "Saskatoon"
        )
    except RuntimeError as e:
        # Unknown / ambiguous service area — a caller-fixable 400, not a 500.
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await asyncio.to_thread(import_svc.build_plan, rows, [], None, service_area, batch)


def _report(plan: Any, batch: str, total_rows: int, validation_token: Optional[str] = None) -> dict[str, Any]:
    skipped_resume = sum(1 for w in plan.warnings if w.field == "resume")
    report: dict[str, Any] = {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "rows": total_rows,
            "users": len(plan.users_to_insert),
            "drivers": len(plan.drivers_to_insert),
            "updated": len(plan.drivers_to_update),
            "skipped_resume": skipped_resume,
        },
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }
    if validation_token is not None:
        report["validation_token"] = validation_token
    return report


@router.post("/drivers/import/validate")
async def validate_driver_import(
    drivers_csv: UploadFile = File(...),
    service_area_id: Optional[str] = Form(None),
    service_area_name: Optional[str] = Form(None),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate the CSV and return the report. No writes.

    The response carries a validation_token bound to (batch, sha256(csv),
    admin.id) — /commit requires it, closing gap #45 (commit previously
    accepted any CSV with no proof a prior validate call ever happened).
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows, csv_sha256 = await _read_csv_rows(drivers_csv)
    plan = await _build_plan(rows, service_area_id, service_area_name, batch)
    validation_token = sign_driver_import_token(batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    return _report(plan, batch, len(rows), validation_token)


@router.post("/drivers/import/commit")
@driver_import_commit_limit
async def commit_driver_import(
    request: Request,
    drivers_csv: UploadFile = File(...),
    service_area_id: Optional[str] = Form(None),
    service_area_name: Optional[str] = Form(None),
    batch: Optional[str] = Form(None),
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the CSV and, only if clean, create the user + driver rows.

    Requires validation_token from a prior /validate call for this exact
    (batch, CSV content, admin) — gap #45. A missing/expired/mismatched
    token means either validate was never called or the file changed
    since, and is refused before any plan-building or writes happen.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows, csv_sha256 = await _read_csv_rows(drivers_csv)
    try:
        verify_driver_import_token(validation_token, batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    except DriverImportTokenError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Validate this CSV before committing (or re-validate — the file or batch changed): {e}",
        ) from e
    plan = await _build_plan(rows, service_area_id, service_area_name, batch)

    if plan.errors:
        # Data changed since the operator validated (or they skipped validate).
        # Refuse but return 200 with committed=false and the full report so the
        # UI can render the errors — the shared api client throws away non-2xx
        # bodies, and pre-flight failures (size/row caps) already 4xx above.
        return {**_report(plan, batch, len(rows)), "committed": False}

    try:
        await asyncio.to_thread(import_svc.commit_plan, plan)
    except Exception as e:
        logger.error("driver bulk-import commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Import commit failed; no changes may have been applied") from e

    imported_users = len(plan.users_to_insert)
    imported_drivers = len(plan.drivers_to_insert)
    updated_drivers = len(plan.drivers_to_update)
    # Audit trail carries counts + batch only — never CSV contents or PII.
    await log_admin_action(
        admin,
        "driver_bulk_import",
        "drivers",
        batch,
        {
            "imported_users": imported_users,
            "imported_drivers": imported_drivers,
            "updated_drivers": updated_drivers,
        },
    )
    return {
        "batch": batch,
        "committed": True,
        "imported_users": imported_users,
        "imported_drivers": imported_drivers,
        "updated_drivers": updated_drivers,
        "warnings": _serialize_items(plan.warnings),
    }
