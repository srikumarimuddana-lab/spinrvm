"""Admin HTTP route for the legacy vehicle-history backfill (Phase 2 of the
2026-08-27 migration plan, docs/migration/2026-08-27-legacy-data-full-
migration-approach.md §4).

Two endpoints, mirroring ``routes/admin/legacy_driver_import.py``'s
validate/commit-token shape for a two-CSV input instead of one:

- ``POST /api/admin/legacy-drivers/vehicle-history-backfill/validate`` —
  parse + validate ``vehicle_details.csv`` (VIN/plate/make/model/colour/year,
  Mongo ObjectId-keyed) plus ``drivers.csv`` (the crosswalk used only to
  resolve that ObjectId to a phone number) and return a dry-run report. No
  writes.
- ``POST /api/admin/legacy-drivers/vehicle-history-backfill/commit`` —
  re-parse + re-validate the same two files and, only if there are no
  errors, insert the planned ``driver_vehicle_history`` rows.

This is a thin wrapper — all matching/diffing logic lives in
``services/driver_import_service.py``'s ``plan_legacy_vehicle_history_backfill``/
``apply_legacy_vehicle_history_backfill`` (shared with
``scripts/backfill_legacy_vehicle_history.py``, the existing CLI). Nothing in
this file re-implements or alters that logic.

Two-file commit-token binding: the validate/commit contract in
``utils/driver_import_token.py`` binds to a single ``csv_sha256``. Here that
hash is ``sha256(vehicle_details_bytes + b"|" + drivers_bytes)`` — a change to
either file between validate and commit changes the combined hash and the
token fails to verify, same as the single-file importers' "file changed
since validate" guard.

Data sensitivity: ``driver_vehicle_history`` (migration 157) is an
append-only regulatory audit table — this route never mutates or deletes an
existing row, and it never mutates any live vehicle/driver field. Reports
carry only ``old_driver_id`` / ``old_vehicle_id``-derived identifiers via
``field`` / ``message`` — never a raw plate/VIN/make/model value — per the
PIPEDA rules in CLAUDE.md and matching
``services/driver_import_service.py``'s own ``print_vehicle_history_report``.
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
    from ...utils.rate_limiter import legacy_vehicle_history_backfill_commit_limit
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import driver_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.driver_import_token import (  # noqa: F401
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from utils.rate_limiter import legacy_vehicle_history_backfill_commit_limit  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrails for a synchronous request handler, mirroring legacy_driver_import.py's
# MAX_CSV_BYTES/MAX_ROWS. vehicle_details.csv can carry more than one row per
# driver (a real before/after history), so its row cap is set higher than
# drivers.csv's -- the latter is the same ~900-row crosswalk Phase 1 already
# bounds at 2,000.
MAX_CSV_BYTES = 2_000_000  # 2 MB, per file
MAX_VEHICLE_ROWS = 5_000
MAX_DRIVER_ROWS = 2_000


def _serialize_items(items: list[import_svc.ImportErrorItem]) -> list[dict[str, str]]:
    return [{"old_driver_id": i.old_driver_id, "field": i.field, "message": i.message} for i in items]


async def _read_csv(upload: UploadFile, *, max_rows: int, label: str) -> tuple[list[dict[str, str]], bytes]:
    """Return (parsed rows, raw bytes) for one uploaded Mongo-export CSV.

    Uses ``driver_import_service.py``'s ``read_mongo_export_csv_text`` (the
    same raw-column-preserving, in-memory Mongo-export reader
    ``routes/admin/legacy_driver_import.py`` already uses), NOT
    ``read_csv_text`` -- the latter's header normalization corrupts this
    export's own ``_id``/``name`` columns. See that function's docstring and
    ``read_mongo_export_csv``'s.
    """
    raw = await upload.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} exceeds the {MAX_CSV_BYTES // 1_000_000} MB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail=f"{label} must be UTF-8 encoded") from e
    try:
        rows = import_svc.read_mongo_export_csv_text(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"{label}: {e}") from e
    if len(rows) > max_rows:
        raise HTTPException(status_code=422, detail=f"{label} has {len(rows)} rows; the limit is {max_rows}")
    return rows, raw


async def _read_csv_pair(
    vehicle_details_csv: UploadFile, drivers_csv: UploadFile
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    """Read both uploads and return (vehicle_rows, driver_rows, combined_sha256).

    The combined hash binds a validate-minted token to this EXACT pair of
    files -- swapping either one between validate and commit changes the
    hash and the token fails to verify (see module docstring).
    """
    vehicle_rows, vehicle_raw = await _read_csv(
        vehicle_details_csv, max_rows=MAX_VEHICLE_ROWS, label="vehicle_details.csv"
    )
    driver_rows, drivers_raw = await _read_csv(drivers_csv, max_rows=MAX_DRIVER_ROWS, label="drivers.csv")
    combined_sha256 = hashlib.sha256(vehicle_raw + b"|" + drivers_raw).hexdigest()
    return vehicle_rows, driver_rows, combined_sha256


async def _build_plan(vehicle_rows: list[dict[str, str]], driver_rows: list[dict[str, str]]) -> Any:
    """Build the plan off the request thread.

    ``plan_legacy_vehicle_history_backfill`` is synchronous and hits
    Supabase (a phone-batched driver lookup plus an existing-history-row
    lookup), so it runs in a worker thread to avoid blocking the event loop
    -- same reasoning as ``legacy_driver_import.py``'s own ``_build_plan``.
    """
    return await asyncio.to_thread(import_svc.plan_legacy_vehicle_history_backfill, vehicle_rows, driver_rows)


def _report(plan: Any, batch: str, total_vehicle_rows: int, validation_token: Optional[str] = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "vehicle_rows": total_vehicle_rows,
            "history_rows_to_insert": len(plan.rows_to_insert),
            "skipped_unmatched": plan.skipped_unmatched,
            "skipped_not_legacy_driver": plan.skipped_not_legacy_driver,
            "skipped_already_backfilled": plan.skipped_already_backfilled,
        },
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }
    if validation_token is not None:
        report["validation_token"] = validation_token
    return report


@router.post("/legacy-drivers/vehicle-history-backfill/validate")
async def validate_legacy_vehicle_history_backfill(
    vehicle_details_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate both CSVs and return the report. No writes.

    The response carries a validation_token bound to
    (batch, sha256(vehicle_details_csv + "|" + drivers_csv), admin.id) --
    /commit requires it, same guarantee as the single-file importers'
    validate/commit pair.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    vehicle_rows, driver_rows, csv_sha256 = await _read_csv_pair(vehicle_details_csv, drivers_csv)
    plan = await _build_plan(vehicle_rows, driver_rows)
    validation_token = sign_driver_import_token(batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    return _report(plan, batch, len(vehicle_rows), validation_token)


@router.post("/legacy-drivers/vehicle-history-backfill/commit")
@legacy_vehicle_history_backfill_commit_limit
async def commit_legacy_vehicle_history_backfill(
    request: Request,
    vehicle_details_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate both CSVs and, only if clean, insert the planned
    ``driver_vehicle_history`` rows.

    Requires validation_token from a prior /validate call for this exact
    (batch, both files' content, admin). A missing/expired/mismatched token
    means either validate was never called or a file changed since, and is
    refused before any plan-building or writes happen.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    vehicle_rows, driver_rows, csv_sha256 = await _read_csv_pair(vehicle_details_csv, drivers_csv)
    try:
        verify_driver_import_token(validation_token, batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    except DriverImportTokenError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Validate these CSVs before committing (or re-validate — a file or batch changed): {e}",
        ) from e
    plan = await _build_plan(vehicle_rows, driver_rows)

    if plan.errors:
        # Data changed since the operator validated (or they skipped validate).
        # Refuse but return 200 with committed=false and the full report so the
        # UI can render the errors — the shared api client throws away non-2xx
        # bodies, and pre-flight failures (size/row caps) already 4xx above.
        return {**_report(plan, batch, len(vehicle_rows)), "committed": False}

    try:
        await asyncio.to_thread(import_svc.apply_legacy_vehicle_history_backfill, plan)
    except Exception as e:
        logger.error("legacy vehicle-history backfill commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Backfill commit failed; no changes may have been applied") from e

    history_rows_inserted = len(plan.rows_to_insert)
    # Audit trail carries counts + batch only — never CSV contents or PII.
    await log_admin_action(
        admin,
        "legacy_vehicle_history_backfill",
        "drivers",
        batch,
        {"history_rows_inserted": history_rows_inserted},
    )
    return {
        "batch": batch,
        "committed": True,
        "history_rows_inserted": history_rows_inserted,
        "warnings": _serialize_items(plan.warnings),
    }
