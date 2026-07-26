"""Admin endpoints for the legacy Stripe mapping import.

Maps old-app Stripe IDs onto already-imported rows so drivers keep their
Connect Express account (no re-onboarding) and riders keep their saved cards.
Mirrors the bulk driver-import contract (``driver_import.py``):

- ``POST /api/admin/stripe/import/validate`` — parse + validate the mapping
  CSV (including live Stripe retrieval per row) and return a dry-run report.
  No writes.
- ``POST /api/admin/stripe/import/commit`` — re-parse + re-validate the same
  CSV and, only if there are no errors, fill the NULL Stripe columns. For
  drivers, a background task then mirrors real KYC state from Stripe.
- ``GET /api/admin/stripe/import/status?batch=…`` — DB-only aggregation of a
  batch's KYC-sync convergence (drivers kind only).

Operator flow, CSV formats, and the same-account vs. new-account (Stripe
support migration) scenarios live in docs/runbooks/stripe-legacy-migration.md.

Reports carry only ``row_ref`` / ``field`` / ``message`` — never raw PII —
per the PIPEDA rules in CLAUDE.md.

Access: strictly **super_admin** (checked per-handler, like the AI console).
Module grants are not enough — writing ``drivers.stripe_account_id`` redirects
a driver's payout destination, a money-moving capability no other admin screen
exposes. The router mount in routes/admin/__init__.py adds
``require_module("drivers")`` on top, but the super_admin check is the gate.

Scope: driver rows are matchable ONLY when stamped by the legacy driver
importer (``legacy_import_metadata.source``) — enforced in the service's
prefetch, so a mapping CSV cannot touch natively-onboarded drivers at all.
"""

import asyncio
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services import stripe_mapping_import_service as import_svc
    from ...settings_loader import get_app_settings
    from ...utils.audit_logger import log_admin_action
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import stripe_mapping_import_service as import_svc  # type: ignore
    from settings_loader import get_app_settings  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_CSV_BYTES = 1_000_000  # 1 MB
# Lower than driver import's 500: commit re-validates every row live against
# Stripe inline (bounded by MAX_STRIPE_CONCURRENCY). Split larger sets.
MAX_ROWS = 200


def _require_super_admin(admin: dict) -> None:
    """403 unless super_admin. Payout-destination writes are above any module
    grant — the "drivers" module also covers read-only ops screens."""
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Stripe mapping import requires super_admin")


def _serialize_items(items: list[import_svc.StripeMappingErrorItem]) -> list[dict[str, str]]:
    return [{"row_ref": i.row_ref, "field": i.field, "message": i.message} for i in items]


async def _read_rows(mapping_csv: UploadFile, kind: str) -> list[dict[str, str]]:
    raw = await mapping_csv.read()
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(status_code=413, detail=f"CSV exceeds the {MAX_CSV_BYTES // 1000} KB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from e
    try:
        rows = import_svc.parse_mapping_rows(text, kind)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if len(rows) > MAX_ROWS:
        raise HTTPException(status_code=422, detail=f"CSV has {len(rows)} rows; the limit is {MAX_ROWS} per import")
    return rows


async def _build_plan(kind: str, rows: list[dict[str, str]], batch: str) -> Any:
    settings = await get_app_settings()
    stripe_secret = settings.get("stripe_secret_key", "")
    # build_plan runs its own DB phase off the loop and Stripe phase bounded
    # by a semaphore; a missing key surfaces as a stripe_not_configured error
    # and per-row Stripe failures become plan errors. Anything that still
    # escapes (DB outage during prefetch) → clean 503 so the operator retries.
    try:
        return await import_svc.build_plan(kind, rows, stripe_secret, batch)
    except Exception as e:
        logger.error("stripe mapping plan build failed", extra={"batch": batch, "kind": kind}, exc_info=True)
        raise HTTPException(
            status_code=503, detail="Could not validate the mapping (database/Stripe error); retry"
        ) from e


def _report(plan: Any, kind: str, batch: str, total_rows: int) -> dict[str, Any]:
    skipped = sum(1 for w in plan.warnings if w.field == "already_mapped")
    return {
        "batch": batch,
        "kind": kind,
        "can_commit": len(plan.errors) == 0,
        "counts": {
            "rows": total_rows,
            "to_map": len(plan.driver_updates) + len(plan.user_updates),
            "skipped_already_mapped": skipped,
        },
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }


@router.post("/stripe/import/validate")
async def validate_stripe_import(
    mapping_csv: UploadFile = File(...),
    kind: str = Form(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse, match, and live-validate the mapping CSV. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows = await _read_rows(mapping_csv, kind)
    plan = await _build_plan(kind, rows, batch)
    return _report(plan, kind, batch, len(rows))


@router.post("/stripe/import/commit")
async def commit_stripe_import(
    mapping_csv: UploadFile = File(...),
    kind: str = Form(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate and, only if clean, fill the NULL Stripe ID columns."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rows = await _read_rows(mapping_csv, kind)
    plan = await _build_plan(kind, rows, batch)

    if plan.errors:
        # Data drifted since the operator validated (or validate was skipped).
        # Refuse but return 200 with committed=false + the full report, same
        # contract as the bulk driver import.
        return {**_report(plan, kind, batch, len(rows)), "committed": False}

    driver_ids = [u["driver_id"] for u in plan.driver_updates]
    try:
        result = await asyncio.to_thread(import_svc.commit_plan, plan)
    except Exception as e:
        logger.error("stripe mapping import commit failed", extra={"batch": batch}, exc_info=True)
        # Per-row writes are not transactional: some rows may already be
        # written. Safe to re-send the same CSV — written rows converge as
        # already_mapped skips, thanks to the NULL-only guard.
        raise HTTPException(
            status_code=502,
            detail="Import commit failed partway; re-send the same CSV — already-written rows are skipped",
        ) from e

    kyc_sync = "not_applicable"
    if kind == import_svc.KIND_DRIVERS and driver_ids:
        # Never inline: up to MAX_ROWS Stripe retrieves + DB writes. The
        # status endpoint reports convergence per batch.
        asyncio.create_task(import_svc.sync_kyc_after_commit(driver_ids, batch))
        kyc_sync = "started"

    # Audit trail carries counts + batch only — never CSV contents or PII.
    await log_admin_action(
        admin,
        "stripe_mapping_import",
        "drivers" if kind == import_svc.KIND_DRIVERS else "users",
        batch,
        {
            "updated_drivers": result["updated_drivers"],
            "updated_users": result["updated_users"],
            "conflicts": len(result["conflicts"]),
        },
    )
    return {
        "batch": batch,
        "kind": kind,
        "committed": True,
        **result,
        "kyc_sync": kyc_sync,
        "warnings": _serialize_items(plan.warnings),
    }


def _status_rows(batch: str) -> list[dict[str, Any]]:
    return (
        import_svc.supabase.table("drivers")
        .select("id,stripe_payouts_enabled,stripe_details_submitted,legacy_import_metadata")
        .eq("legacy_import_metadata->stripe_migration->>batch", batch)
        .execute()
        .data
        or []
    )


@router.get("/stripe/import/status")
async def stripe_import_status(batch: str, admin: dict = Depends(get_admin_user)):
    """Aggregate a drivers batch's KYC-sync convergence. DB-only, no Stripe."""
    _require_super_admin(admin)
    rows = await asyncio.to_thread(_status_rows, batch)
    kyc = Counter(
        (r.get("legacy_import_metadata") or {}).get("stripe_migration", {}).get("kyc_sync", "pending") for r in rows
    )
    return {
        "batch": batch,
        "drivers": len(rows),
        "kyc_sync": dict(kyc),
        "payouts_enabled": sum(1 for r in rows if r.get("stripe_payouts_enabled")),
        "details_submitted": sum(1 for r in rows if r.get("stripe_details_submitted")),
    }
