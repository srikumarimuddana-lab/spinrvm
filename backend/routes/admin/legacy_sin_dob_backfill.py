"""Admin HTTP wrapper for the legacy SIN/DOB backfill (Phase 2 of the
2026-08-27 migration plan; sibling of the vehicle-history backfill).

Two endpoints, mirroring ``routes/admin/legacy_driver_import.py``'s
validate/commit-token/rate-limit shape for a two-CSV-upload flow instead of
one:

- ``POST /api/admin/legacy-drivers/sin-dob-backfill/validate`` — parse +
  validate ``banks.csv`` + ``drivers.csv`` (both raw exports from the
  previous app's MongoDB dump) and return a dry-run report. No writes.
- ``POST /api/admin/legacy-drivers/sin-dob-backfill/commit`` — re-parse +
  re-validate the same two files and, only if there are no errors, write
  SIN/DOB onto the matched drivers.

This is a thin HTTP wrapper only. All matching/validation/write logic is
``services/driver_import_service.py``'s ``plan_legacy_sin_dob_import`` /
``apply_legacy_sin_dob_import`` — the exact pair
``scripts/backfill_legacy_driver_sin_dob.py`` already calls from the CLI.
Nothing here re-implements that logic.

CSV parsing: uses ``driver_import_service.py``'s
``read_mongo_export_csv_text`` (the same raw-column-preserving, in-memory
Mongo-export reader ``routes/admin/legacy_driver_import.py`` already uses)
— never ``read_csv_text``/``normalize_header``, which would mangle this
file's join key, ``_id``.

Idempotency / re-commit safety: ``plan_legacy_sin_dob_import`` already skips
anything already on file (a driver's existing ``sin``/``date_of_birth``
always wins), and ``apply_legacy_sin_dob_import`` re-guards each write with
``.is_(<column>, "null")`` at commit time to catch a self-entry race between
plan and apply. A re-sent commit of the same CSV converges rather than
clobbering.

PII: this backfill writes a government ID (SIN) and a date of birth.
Neither value is ever logged, printed, or returned by this route — reports
carry only ``old_driver_id`` / ``field`` / ``message``, exactly like
``print_sin_dob_report`` (the CLI's own report) and
``legacy_driver_import.py``'s ``_serialize_items``. The commit response's
``conflicts`` list is old_driver_id references only (already used
elsewhere in warnings) — never a SIN or DOB value.
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
    from ...utils.pii import client_safe_detail, redact_client_text
    from ...utils.rate_limiter import legacy_sin_dob_backfill_commit_limit
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import driver_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.driver_import_token import (  # noqa: F401
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from utils.pii import client_safe_detail, redact_client_text
    from utils.rate_limiter import legacy_sin_dob_backfill_commit_limit  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrails for a synchronous request handler, one per uploaded file. The
# real export's banks.csv/drivers.csv are small (hundreds of rows) — these
# leave real headroom without letting a runaway upload block the event loop.
MAX_CSV_BYTES = 2_000_000  # 2 MB, per file
MAX_ROWS = 2_000  # per file


def _serialize_items(items: list[import_svc.ImportErrorItem]) -> list[dict[str, str]]:
    return [{"old_driver_id": i.old_driver_id, "field": i.field, "message": i.message} for i in items]


async def _read_one_csv(upload: UploadFile) -> tuple[list[dict[str, str]], bytes]:
    """Read + size-guard one uploaded CSV. Returns (rows, raw bytes)."""
    raw = await upload.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_CSV_BYTES // 1_000_000} MB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from e
    try:
        rows = import_svc.read_mongo_export_csv_text(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=client_safe_detail(e, fallback="CSV could not be parsed")) from e
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import")
    return rows, raw


async def _read_csv_pair(
    banks_csv: UploadFile, drivers_csv: UploadFile
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    """Read both uploaded CSVs and bind a single hash to their combined bytes.

    The commit token is bound to ``sha256(banks_bytes + b"|" + drivers_bytes)``
    so swapping EITHER file between validate and commit invalidates the
    token — same gap-#45-shaped guarantee ``legacy_driver_import.py`` has for
    its single file, extended to two.
    """
    bank_rows, banks_raw = await _read_one_csv(banks_csv)
    driver_rows, drivers_raw = await _read_one_csv(drivers_csv)
    combined_sha256 = hashlib.sha256(banks_raw + b"|" + drivers_raw).hexdigest()
    return bank_rows, driver_rows, combined_sha256


async def _build_plan(bank_rows: list[dict[str, str]], driver_rows: list[dict[str, str]]) -> Any:
    """Build the plan off the request thread — ``plan_legacy_sin_dob_import``
    is synchronous and hits Supabase (batched ``.in_()`` phone lookups), same
    reasoning as every other admin import route in this package.
    """
    return await asyncio.to_thread(import_svc.plan_legacy_sin_dob_import, bank_rows, driver_rows)


def _report(plan: Any, batch: str, total_rows: int, validation_token: Optional[str] = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "rows": total_rows,
            "to_update": len(plan.updates),
            "skipped_unmatched": plan.skipped_unmatched,
            "skipped_not_legacy_driver": plan.skipped_not_legacy_driver,
            "skipped_already_on_file": plan.skipped_already_on_file,
            "skipped_duplicate_match": plan.skipped_duplicate_match,
        },
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }
    if validation_token is not None:
        report["validation_token"] = validation_token
    return report


@router.post("/legacy-drivers/sin-dob-backfill/validate")
async def validate_legacy_sin_dob_backfill(
    banks_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate banks.csv + drivers.csv and return the
    report. No writes.

    The response carries a validation_token bound to (batch,
    sha256(banks_bytes + b"|" + drivers_bytes), admin.id) — /commit requires
    it, same gap-#45-shaped guarantee as the other admin import routes.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bank_rows, driver_rows, csv_sha256 = await _read_csv_pair(banks_csv, drivers_csv)
    plan = await _build_plan(bank_rows, driver_rows)
    validation_token = sign_driver_import_token(batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    return _report(plan, batch, len(bank_rows), validation_token)


@router.post("/legacy-drivers/sin-dob-backfill/commit")
@legacy_sin_dob_backfill_commit_limit
async def commit_legacy_sin_dob_backfill(
    request: Request,
    banks_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the two CSVs and, only if clean, write SIN/DOB onto the
    matched drivers via ``apply_legacy_sin_dob_import``.

    Requires validation_token from a prior /validate call for this exact
    (batch, combined CSV bytes, admin). A missing/expired/mismatched token
    means either validate was never called or a file changed since, and is
    refused before any plan-building or writes happen.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    bank_rows, driver_rows, csv_sha256 = await _read_csv_pair(banks_csv, drivers_csv)
    try:
        verify_driver_import_token(validation_token, batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    except DriverImportTokenError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Validate these CSVs before committing (or re-validate — a file or batch changed): "
            f"{redact_client_text(e)}",
        ) from e
    plan = await _build_plan(bank_rows, driver_rows)

    if plan.errors:
        # Data changed since the operator validated (or they skipped validate).
        # Refuse but return 200 with committed=false and the full report so the
        # UI can render the errors — the shared api client throws away non-2xx
        # bodies, and pre-flight failures (size/row caps) already 4xx above.
        return {**_report(plan, batch, len(bank_rows)), "committed": False}

    try:
        conflicts = await asyncio.to_thread(import_svc.apply_legacy_sin_dob_import, plan, batch=batch)
    except Exception as e:
        logger.error("legacy SIN/DOB backfill commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Backfill commit failed; no changes may have been applied") from e

    updated = len(plan.updates) - len(conflicts)
    # Audit trail carries counts + batch + old_driver_id conflict refs only —
    # never a SIN or DOB value.
    await log_admin_action(
        admin,
        "legacy_sin_dob_backfill",
        "drivers",
        batch,
        {"updated": updated, "conflicts": len(conflicts)},
    )
    return {
        "batch": batch,
        "committed": True,
        "updated": updated,
        "conflicts": conflicts,
        "warnings": _serialize_items(plan.warnings),
    }
