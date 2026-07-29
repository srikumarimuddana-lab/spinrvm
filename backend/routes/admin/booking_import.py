"""Admin bulk legacy-booking import endpoints (4-CSV import).

Two endpoints power the admin dashboard's Legacy Booking Import tool:

- ``POST /api/admin/bookings/import/validate`` — parse + validate the four
  legacy CSVs and return a dry-run report (counts + warnings + errors). No
  writes.
- ``POST /api/admin/bookings/import/commit`` — re-parse + re-validate the same
  CSVs and, only if there are no errors, insert the rides, the offsetting
  payouts, and recount ``drivers.total_rides``.

Unlike the other importers this takes FOUR files in one multipart request
(bookings, customers, drivers, driver-earnings). All four are required: the
plan cannot be built from bookings alone — customers and drivers supply the
phone numbers used to match Spinr accounts, and driver-earnings supplies each
booking's actual driver payout, which is what the offsetting payout row must
cancel.

Money safety: driver payouts for these rides were already settled in the
previous app, and Spinr derives ``payable_balance`` live from completed rides.
The service writes one offsetting ``payouts`` row per matched driver so the
net payable delta is exactly $0 — see ``services/booking_import_service.py``
and ``docs/change-log/2026-07-29-legacy-booking-import.md``.

Super-admin only, matching the Data Transfer import and the Bulk Operations
page that hosts this tool: it writes to ``rides`` and ``payouts``, the two
tables that feed driver payouts.

Idempotency / re-commit safety: the service skips bookings already written by
this importer (matched on ``rides.legacy_import_metadata->>'old_booking_id'``,
backed by a unique index) and derives payout IDs deterministically from
(batch, driver), so a re-sent commit converges rather than duplicating. Pass
the same ``batch`` when resuming a partial run. Two *simultaneous* commits
could still race; the UI disables the button while a request is in flight,
which is acceptable for an internal ops tool run once.

Reports carry only ``row_num`` / ``booking_code`` / ``field`` / ``message`` —
never names, phones, or addresses — per the PIPEDA rules in CLAUDE.md.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services import booking_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        booking_import_commit_limit,
        booking_import_validate_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import booking_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        booking_import_commit_limit,
        booking_import_validate_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrails for a synchronous request handler. Deliberately larger than the
# rider/driver importers' 1 MB / 500 rows: this is a whole-app history export,
# not a staff roster. The real exports run ~1 MB and ~1,200 rows for bookings
# and ~1 MB / ~900 rows for drivers, so these caps leave headroom without
# letting a runaway upload sit in memory. Parsing and planning run in a worker
# thread, so a large file does not block the event loop.
MAX_CSV_BYTES = 12_000_000  # 12 MB per file
MAX_ROWS = 50_000  # per file


def _serialize_items(items: list[import_svc.ImportReportItem]) -> list[dict[str, Any]]:
    """Flat, PII-free report rows. booking_code is the legacy CB… reference."""
    return [
        {
            "row_num": i.row_num,
            "booking_code": i.booking_code,
            "field": i.field,
            "message": i.message,
        }
        for i in items
    ]


async def _read_csv_rows(upload: UploadFile, label: str) -> list[dict[str, str]]:
    """Read one uploaded CSV into rows, or raise a caller-fixable 4xx.

    ``label`` names the offending file in the error so an operator uploading
    four files at once knows which one to fix.
    """
    raw = await upload.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{label} CSV exceeds the {MAX_CSV_BYTES // 1_000_000} MB limit",
        )
    if not raw:
        raise HTTPException(status_code=422, detail=f"{label} CSV is empty")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail=f"{label} CSV must be UTF-8 encoded") from e
    try:
        rows = import_svc.read_csv_text(text)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=f"{label} CSV: {e}") from e
    if len(rows) > MAX_ROWS:
        raise HTTPException(
            status_code=422,
            detail=f"{label} CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import",
        )
    return rows


def _require_super_admin(admin: dict) -> None:
    """Re-check the role per handler, as the Stripe import does.

    The router is already included behind ``require_super_admin``; this is the
    same belt-and-braces the Stripe mapping import uses, so the guard survives
    a future re-mount under a weaker dependency.
    """
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Legacy booking import requires super_admin")


async def _build_plan(
    files: dict[str, list[dict[str, str]]],
    service_area_id: Optional[str],
    service_area_name: Optional[str],
    vehicle_type_id: Optional[str],
    vehicle_type_name: Optional[str],
    batch: str,
) -> Any:
    """Resolve service area + vehicle type and build the plan off the request thread.

    All three service calls are synchronous and hit Supabase, so they run in a
    worker thread to avoid blocking the event loop.
    """
    try:
        service_area = await asyncio.to_thread(
            import_svc.get_service_area, service_area_id, service_area_name or "Saskatoon"
        )
        vehicle_type = await asyncio.to_thread(
            import_svc.get_vehicle_type, vehicle_type_id, vehicle_type_name or "Economy"
        )
    except RuntimeError as e:
        # Unknown / ambiguous service area or vehicle type — caller-fixable.
        # The service's message mentions the CLI flag; rephrase for HTTP callers
        # so the admin UI does not tell an operator to pass "--service-area-id".
        detail = str(e).replace("pass --service-area-id", "select a specific service area")
        detail = detail.replace("pass --vehicle-type-id", "select a specific vehicle type")
        raise HTTPException(status_code=400, detail=detail) from e

    return await asyncio.to_thread(
        import_svc.build_plan,
        files["bookings"],
        files["customers"],
        files["drivers"],
        files["earnings"],
        service_area=service_area,
        vehicle_type=vehicle_type,
        batch=batch,
    )


def _report(plan: Any, batch: str) -> dict[str, Any]:
    """Dry-run report. ``plan.stats`` is already JSON-safe (ints and floats)."""
    return {
        "batch": batch,
        "can_commit": len(plan.errors) == 0 and len(plan.rides_to_insert) > 0,
        "counts": dict(plan.stats),
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }


async def _read_all(
    bookings_csv: UploadFile,
    customers_csv: UploadFile,
    drivers_csv: UploadFile,
    earnings_csv: UploadFile,
) -> dict[str, list[dict[str, str]]]:
    return {
        "bookings": await _read_csv_rows(bookings_csv, "bookings"),
        "customers": await _read_csv_rows(customers_csv, "customers"),
        "drivers": await _read_csv_rows(drivers_csv, "drivers"),
        "earnings": await _read_csv_rows(earnings_csv, "driver earnings"),
    }


@router.post("/bookings/import/validate")
@booking_import_validate_limit
async def validate_booking_import(
    request: Request,
    bookings_csv: UploadFile = File(...),
    customers_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    earnings_csv: UploadFile = File(...),
    service_area_id: Optional[str] = Form(None),
    service_area_name: Optional[str] = Form(None),
    vehicle_type_id: Optional[str] = Form(None),
    vehicle_type_name: Optional[str] = Form(None),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate the four CSVs and return the report. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    files = await _read_all(bookings_csv, customers_csv, drivers_csv, earnings_csv)
    plan = await _build_plan(files, service_area_id, service_area_name, vehicle_type_id, vehicle_type_name, batch)
    return _report(plan, batch)


@router.post("/bookings/import/commit")
@booking_import_commit_limit
async def commit_booking_import(
    request: Request,
    bookings_csv: UploadFile = File(...),
    customers_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    earnings_csv: UploadFile = File(...),
    service_area_id: Optional[str] = Form(None),
    service_area_name: Optional[str] = Form(None),
    vehicle_type_id: Optional[str] = Form(None),
    vehicle_type_name: Optional[str] = Form(None),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the CSVs and, only if clean, insert rides + offset payouts."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    files = await _read_all(bookings_csv, customers_csv, drivers_csv, earnings_csv)
    plan = await _build_plan(files, service_area_id, service_area_name, vehicle_type_id, vehicle_type_name, batch)

    report = _report(plan, batch)
    if not report["can_commit"]:
        # Data changed since the operator validated (or they skipped validate,
        # or every row was already imported). Refuse but return 200 with
        # committed=false and the full report so the UI can render it — the
        # shared api client discards non-2xx bodies, and pre-flight failures
        # (size/row caps) already 4xx above.
        return {**report, "committed": False}

    rides = len(plan.rides_to_insert)
    payouts = len(plan.payouts_to_insert)
    try:
        await asyncio.to_thread(import_svc.commit_plan, plan)
    except Exception as e:
        # Loud, not swallowed: this writes rides and payouts, so a partial
        # failure needs a real root cause, not a soft "please retry".
        logger.error(
            "legacy booking import commit failed",
            extra={"batch": batch, "rides": rides, "payouts": payouts},
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Import commit failed; some rows may have been written. Re-run validate to see current state.",
        ) from e

    # Audit trail carries counts + batch only — never CSV contents or PII.
    await log_admin_action(
        admin,
        "legacy_booking_import",
        "rides",
        batch,
        {
            "imported_rides": rides,
            "offset_payouts": payouts,
            "drivers_recounted": len(plan.driver_ids_to_recount),
            "sum_offset_payouts": plan.stats.get("sum_offset_payouts"),
        },
    )
    return {
        "batch": batch,
        "committed": True,
        "imported_rides": rides,
        "offset_payouts": payouts,
        "drivers_recounted": len(plan.driver_ids_to_recount),
        "counts": dict(plan.stats),
        "warnings": _serialize_items(plan.warnings),
    }
