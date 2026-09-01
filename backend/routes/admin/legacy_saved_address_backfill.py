"""Admin HTTP route for the legacy saved-address backfill (Phase 4 of the
2026-08-27 migration plan, docs/migration/2026-08-27-legacy-data-full-
migration-approach.md §4).

Two endpoints, mirroring ``routes/admin/legacy_vehicle_history_backfill.py``'s
validate/commit-token shape for a two-CSV input:

- ``POST /api/admin/riders/saved-address-backfill/validate`` — parse +
  validate ``customer_addresses.csv`` (Mongo ObjectId-keyed) plus
  ``customers.csv`` (the crosswalk used only to resolve that ObjectId to a
  phone number) and return a dry-run report. No writes.
- ``POST /api/admin/riders/saved-address-backfill/commit`` — re-parse +
  re-validate the same two files and, only if there are no errors, insert
  the planned ``saved_addresses`` rows.

This is a thin wrapper — all matching/filtering logic lives in
``services/saved_address_import_service.py``'s
``build_saved_address_import_plan``/``commit_saved_address_import_plan``.
Nothing in this file re-implements or alters that logic.

Two-file commit-token binding: same as the vehicle-history backfill's
combined-hash approach — a change to either file between validate and
commit changes the hash and the token fails to verify.

Data sensitivity: reports carry only row numbers / field / message — never
a raw address, lat/lng, or phone number — per the PIPEDA rules in
CLAUDE.md.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services import driver_import_service as driver_import_svc
    from ...services import saved_address_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.driver_import_token import (
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from ...utils.pii import redact_client_text
    from ...utils.rate_limiter import legacy_saved_address_backfill_commit_limit
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import driver_import_service as driver_import_svc  # type: ignore
    from services import saved_address_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.driver_import_token import (  # noqa: F401
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from utils.pii import redact_client_text
    from utils.rate_limiter import legacy_saved_address_backfill_commit_limit  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrails for a synchronous request handler, mirroring the other
# two-CSV backfills. The real export is 301 address rows / ~1,200 customer
# rows -- these leave real headroom above that.
MAX_CSV_BYTES = 2_000_000  # 2 MB, per file
MAX_ADDRESS_ROWS = 5_000
MAX_CUSTOMER_ROWS = 5_000


def _serialize_items(items: list[import_svc.ImportReportItem]) -> list[dict[str, Any]]:
    return [{"row_num": i.row_num, "field": i.field, "message": i.message} for i in items]


async def _read_csv(upload: UploadFile, *, max_rows: int, label: str) -> tuple[list[dict[str, str]], bytes]:
    """Return (parsed rows, raw bytes) for one uploaded Mongo-export CSV.

    Uses ``driver_import_service.read_mongo_export_csv_text`` — the same
    raw-column-preserving reader the vehicle-history/legacy-driver routes
    already use, reused rather than duplicated (unlike the small per-module
    helpers, this parser is already treated as shared across importers —
    see ``legacy_vehicle_history_backfill.py``'s identical import).
    """
    raw = await upload.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} exceeds the {MAX_CSV_BYTES // 1_000_000} MB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail=f"{label} must be UTF-8 encoded") from e
    try:
        rows = driver_import_svc.read_mongo_export_csv_text(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"{label}: {redact_client_text(e)}") from e
    if len(rows) > max_rows:
        raise HTTPException(status_code=422, detail=f"{label} has {len(rows)} rows; the limit is {max_rows}")
    return rows, raw


async def _read_csv_pair(
    addresses_csv: UploadFile, customers_csv: UploadFile
) -> tuple[list[dict[str, str]], list[dict[str, str]], str]:
    address_rows, address_raw = await _read_csv(
        addresses_csv, max_rows=MAX_ADDRESS_ROWS, label="customer_addresses.csv"
    )
    customer_rows, customer_raw = await _read_csv(customers_csv, max_rows=MAX_CUSTOMER_ROWS, label="customers.csv")
    combined_sha256 = hashlib.sha256(address_raw + b"|" + customer_raw).hexdigest()
    return address_rows, customer_rows, combined_sha256


async def _build_plan(address_rows: list[dict[str, str]], customer_rows: list[dict[str, str]], batch: str) -> Any:
    return await asyncio.to_thread(import_svc.build_saved_address_import_plan, address_rows, customer_rows, batch=batch)


def _report(plan: Any, batch: str, total_address_rows: int, validation_token: Optional[str] = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "address_rows": total_address_rows,
            "addresses_to_insert": len(plan.rows_to_insert),
            "skipped_out_of_province": plan.skipped_out_of_province,
            "skipped_unmatched_customer": plan.skipped_unmatched_customer,
            "skipped_no_rider": plan.skipped_no_rider,
            "skipped_already_imported": plan.skipped_already_imported,
        },
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }
    if validation_token is not None:
        report["validation_token"] = validation_token
    return report


@router.post("/riders/saved-address-backfill/validate")
async def validate_saved_address_backfill(
    addresses_csv: UploadFile = File(...),
    customers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate both CSVs and return the report. No writes."""
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    address_rows, customer_rows, csv_sha256 = await _read_csv_pair(addresses_csv, customers_csv)
    plan = await _build_plan(address_rows, customer_rows, batch)
    validation_token = sign_driver_import_token(batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    return _report(plan, batch, len(address_rows), validation_token)


@router.post("/riders/saved-address-backfill/commit")
@legacy_saved_address_backfill_commit_limit
async def commit_saved_address_backfill(
    request: Request,
    addresses_csv: UploadFile = File(...),
    customers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate both CSVs and, only if clean, insert the planned
    saved_addresses rows.

    Requires validation_token from a prior /validate call for this exact
    (batch, both files' content, admin) — same guard as the other two-file
    backfills.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    address_rows, customer_rows, csv_sha256 = await _read_csv_pair(addresses_csv, customers_csv)
    try:
        verify_driver_import_token(validation_token, batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    except DriverImportTokenError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Validate these CSVs before committing (or re-validate — a file or batch changed): "
            f"{redact_client_text(e)}",
        ) from e
    plan = await _build_plan(address_rows, customer_rows, batch)

    if plan.errors:
        return {**_report(plan, batch, len(address_rows)), "committed": False}

    try:
        await asyncio.to_thread(import_svc.commit_saved_address_import_plan, plan)
    except Exception as e:
        logger.error("legacy saved-address backfill commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Backfill commit failed; no changes may have been applied") from e

    addresses_inserted = len(plan.rows_to_insert)
    await log_admin_action(
        admin,
        "legacy_saved_address_backfill",
        "saved_addresses",
        batch,
        {"addresses_inserted": addresses_inserted},
    )
    return {
        "batch": batch,
        "committed": True,
        "addresses_inserted": addresses_inserted,
        "warnings": _serialize_items(plan.warnings),
    }
