"""Admin bulk import for legacy Mongo driver profiles (Phase 1 of the
2026-08-27 migration plan).

Two endpoints, mirroring ``routes/admin/driver_import.py``'s validate/commit
shape exactly for a different population and a different source CSV:

- ``POST /api/admin/legacy-drivers/import/validate`` — parse + validate the
  raw Mongo-export ``drivers.csv`` and return a dry-run report. No writes.
- ``POST /api/admin/legacy-drivers/import/commit`` — re-parse + re-validate
  the same CSV and, only if there are no errors, create/link/enrich the
  user + driver rows.

Separate router from ``driver_import.py`` (a different CSV shape, a
different service-layer plan/commit pair, a different rate-limit bucket) --
not an extension of it. See ``services/driver_import_service.py``'s
``build_mongo_driver_import_plan`` section-header comment for the full
design/safety reasoning (existing-match link/enrich policy, blank-name
placeholder policy, forced needs_review/offline/unverified).

Idempotency / re-commit safety: the service's resume check
(``_mongo_driver_already_linked``) matches a previously created, linked, or
enriched row on ``old_driver_id`` (checked against both the direct-creation
shape and the ``mongo_driver_history`` list shape) and skips it with a
warning, so a re-sent commit converges rather than duplicating or
double-appending history. Two *simultaneous* commits of the same CSV could
still double-write; same accepted posture as ``driver_import.py`` for an
internal ops tool with a UI that disables the button mid-request.

Reports carry only ``old_driver_id`` / ``field`` / ``message`` — never raw
PII (names, phones) — per the PIPEDA rules in CLAUDE.md.
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

try:
    from ...dependencies import get_admin_user
    from ...services import driver_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.driver_import_token import (
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from ...utils.rate_limiter import legacy_mongo_driver_import_commit_limit
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import driver_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.driver_import_token import (  # noqa: F401
        DriverImportTokenError,
        sign_driver_import_token,
        verify_driver_import_token,
    )
    from utils.rate_limiter import legacy_mongo_driver_import_commit_limit  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()

# Guardrails for a synchronous request handler. The real 2026-08-22 export is
# 925 rows / 869 KB -- these leave real headroom above that for the Oct 30
# batch without letting a runaway upload block the event loop indefinitely.
MAX_CSV_BYTES = 2_000_000  # 2 MB
MAX_ROWS = 2_000


def _serialize_items(items: list[import_svc.ImportErrorItem]) -> list[dict[str, str]]:
    return [{"old_driver_id": i.old_driver_id, "field": i.field, "message": i.message} for i in items]


async def _read_csv_rows(drivers_csv: UploadFile) -> tuple[list[dict[str, str]], str]:
    """Return the parsed rows plus a sha256 of the raw CSV bytes.

    The hash binds a validate-minted token to this exact file content,
    mirroring ``driver_import.py``'s own gap #45 fix -- re-parsing alone
    can't tell the commit call whether the CSV changed since validate.

    Uses ``read_mongo_export_csv_text``, NOT ``read_csv_text`` -- the latter
    normalizes headers in a way that corrupts this CSV's own ``_id`` column
    (see that function's docstring and ``read_mongo_export_csv``'s).
    """
    raw = await drivers_csv.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_CSV_BYTES // 1_000_000} MB limit")
    csv_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from e
    try:
        rows = import_svc.read_mongo_export_csv_text(text)
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

    ``get_service_area`` and ``build_mongo_driver_import_plan`` are
    synchronous and hit Supabase, so they run in a worker thread to avoid
    blocking the event loop -- same reasoning as ``driver_import.py``'s own
    ``_build_plan``, and the same reasoning again applies more sharply to
    ``commit`` below: at real scale this plan's ``users_to_update``/
    ``drivers_to_enrich`` loops are hundreds of sequential UPDATE calls
    (same per-row-loop shape ``build_plan``'s own ``drivers_to_update``
    already uses, not a new pattern), so offloading the whole call keeps
    the event loop free while it runs.
    """
    try:
        service_area = await asyncio.to_thread(
            import_svc.get_service_area, service_area_id, service_area_name or "Saskatoon"
        )
    except RuntimeError as e:
        # Unknown / ambiguous service area — a caller-fixable 400, not a 500.
        raise HTTPException(status_code=400, detail=str(e)) from e
    return await asyncio.to_thread(
        import_svc.build_mongo_driver_import_plan, rows, service_area=service_area, import_batch=batch
    )


def _report(plan: Any, batch: str, total_rows: int, validation_token: Optional[str] = None) -> dict[str, Any]:
    skipped_resume = sum(1 for w in plan.warnings if w.field == "resume")
    report: dict[str, Any] = {
        "batch": batch,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "rows": total_rows,
            "new_users": len(plan.users_to_insert),
            "new_drivers": len(plan.drivers_to_insert),
            "linked_accounts": len(plan.users_to_update),
            "enriched_drivers": len(plan.drivers_to_enrich),
            "skipped_resume": skipped_resume,
        },
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }
    if validation_token is not None:
        report["validation_token"] = validation_token
    return report


@router.post("/legacy-drivers/import/validate")
async def validate_legacy_driver_import(
    drivers_csv: UploadFile = File(...),
    service_area_id: Optional[str] = Form(None),
    service_area_name: Optional[str] = Form(None),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate the CSV and return the report. No writes.

    The response carries a validation_token bound to (batch, sha256(csv),
    admin.id) — /commit requires it, same gap-#45-shaped guarantee as
    ``driver_import.py``'s own validate/commit pair.
    """
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows, csv_sha256 = await _read_csv_rows(drivers_csv)
    plan = await _build_plan(rows, service_area_id, service_area_name, batch)
    validation_token = sign_driver_import_token(batch=batch, csv_sha256=csv_sha256, admin_id=admin["id"])
    return _report(plan, batch, len(rows), validation_token)


@router.post("/legacy-drivers/import/commit")
@legacy_mongo_driver_import_commit_limit
async def commit_legacy_driver_import(
    request: Request,
    drivers_csv: UploadFile = File(...),
    service_area_id: Optional[str] = Form(None),
    service_area_name: Optional[str] = Form(None),
    batch: Optional[str] = Form(None),
    validation_token: str = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the CSV and, only if clean, create/link/enrich the rows.

    Requires validation_token from a prior /validate call for this exact
    (batch, CSV content, admin). A missing/expired/mismatched token means
    either validate was never called or the file changed since, and is
    refused before any plan-building or writes happen.
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
        await asyncio.to_thread(import_svc.commit_mongo_driver_import_plan, plan)
    except Exception as e:
        logger.error("legacy Mongo driver-import commit failed", extra={"batch": batch}, exc_info=True)
        raise HTTPException(status_code=502, detail="Import commit failed; no changes may have been applied") from e

    new_users = len(plan.users_to_insert)
    new_drivers = len(plan.drivers_to_insert)
    linked_accounts = len(plan.users_to_update)
    enriched_drivers = len(plan.drivers_to_enrich)
    # Audit trail carries counts + batch only — never CSV contents or PII.
    await log_admin_action(
        admin,
        "legacy_mongo_driver_import",
        "drivers",
        batch,
        {
            "new_users": new_users,
            "new_drivers": new_drivers,
            "linked_accounts": linked_accounts,
            "enriched_drivers": enriched_drivers,
        },
    )
    return {
        "batch": batch,
        "committed": True,
        "new_users": new_users,
        "new_drivers": new_drivers,
        "linked_accounts": linked_accounts,
        "enriched_drivers": enriched_drivers,
        "warnings": _serialize_items(plan.warnings),
    }


class BackfillOrphanedDriversRequest(BaseModel):
    service_area_id: Optional[str] = None
    service_area_name: Optional[str] = None
    # Default False — a preview-only pass. The operator must explicitly
    # ask for a write, matching this admin surface's existing dry-run-first
    # convention (see /legacy-drivers/import/validate above).
    apply: bool = False


@router.post("/legacy-drivers/backfill-orphaned")
async def backfill_orphaned_legacy_drivers(
    body: BackfillOrphanedDriversRequest,
    admin: dict = Depends(get_admin_user),
):
    """One-time data repair for the 2026-08-29 production incident: two
    commits made before commit_mongo_driver_import_plan's atomicity fix
    left users flagged is_driver=True via the existing-account-link path
    with no matching drivers row at all. Finds every such user and (only
    if apply=True) creates the missing drivers row from that user's own
    surviving mongo_driver_history entry. See
    docs/change-log/2026-08-29-legacy-driver-import-orphan-fix.md.

    Idempotent: a user only shows up if it still has no drivers row, so
    re-running this after a partial apply only touches what's still
    missing, never a duplicate.
    """
    try:
        service_area = await asyncio.to_thread(
            import_svc.get_service_area, body.service_area_id, body.service_area_name or "Saskatoon"
        )
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    result = await asyncio.to_thread(
        import_svc.backfill_orphaned_legacy_driver_rows, service_area=service_area, apply=body.apply
    )
    if body.apply and result["fixed"]:
        await log_admin_action(
            admin,
            "legacy_driver_orphan_backfill",
            "drivers",
            "bulk",
            {"scanned": result["scanned"], "fixed": result["fixed"], "service_area_id": service_area["id"]},
        )
    return result


class BackfillDriverCreatedAtRequest(BaseModel):
    apply: bool = False


@router.post("/legacy-drivers/backfill-created-at")
async def backfill_driver_created_at(
    body: BackfillDriverCreatedAtRequest,
    admin: dict = Depends(get_admin_user),
):
    """One-time repair (2026-08-30): backfill_orphaned_legacy_driver_rows()
    stamps the repaired drivers row's `created_at` as the repair run's own
    time, but the driver's real join date already sits correctly on their
    linked `users.created_at`. Finds (and, if apply=True, fixes) every
    backfilled drivers row whose `created_at` doesn't already match its
    user's. See docs/change-log/2026-08-30-rider-created-at-legacy-date-fix.md.
    """
    mismatches = await asyncio.to_thread(import_svc.find_backfilled_driver_created_at_mismatches)
    result = {"scanned": len(mismatches), "applied": body.apply, "fixed": len(mismatches)}
    if body.apply and mismatches:
        await asyncio.to_thread(import_svc.apply_driver_created_at_corrections, mismatches)
        await log_admin_action(
            admin,
            "driver_created_at_backfill",
            "drivers",
            "bulk",
            {"fixed": len(mismatches)},
        )
    return result
