"""Admin bulk legacy-wallet-balance import endpoints (3-CSV import).

Two endpoints power the admin dashboard's Legacy Wallet Import tool:

- ``POST /api/admin/wallets/import/validate`` — parse + validate the three
  legacy CSVs and return a dry-run report (counts + warnings + errors). No
  writes.
- ``POST /api/admin/wallets/import/commit`` — re-parse + re-validate the same
  CSVs and, only if there are no errors, apply every planned delta via
  ``wallet_import_service.commit_plan`` (the row-locked ``wallet_apply_delta``
  RPC, one call per legacy entry — never a plain balance UPDATE).

Three files in one multipart request (wallets, customers, drivers) — the
wallets CSV alone can't be matched to a Spinr account; customers/drivers
supply the phone numbers ``build_plan`` uses to resolve each row's owner.

Money safety: every delta goes through ``wallet_apply_delta`` (migration
249), which locks the wallet row, dedups on ``(wallet_id, reference_id,
type)``, and enforces the $0 floor. See ``services/wallet_import_service.py``
and ``docs/change-log/2026-08-24-wallet-import-service-built.md``.

**Column-name caveat** (see the service module's own docstring in full):
the expected CSV columns are inferred from this same Mongo export's sibling
collections, not confirmed against a real ``wallets.csv`` header row. The
first real ``/validate`` call is the first time that assumption is actually
tested — read its ``errors`` before ever calling ``/commit``.

Super-admin only, matching the legacy booking importer: it applies real
money deltas to riders'/drivers' wallets.

Idempotency / re-commit safety: ``wallet_apply_delta``'s own dedup on
``(wallet_id, reference_id, type)`` (inside its row lock) makes a re-sent
commit converge rather than double-applying — see the service module's
docstring for why this importer, unlike the booking importer, needs no
separate "already imported" prefetch of its own.

Reports carry only ``row_num`` / ``old_id`` / ``field`` / ``message`` — never
names, phones, or addresses — per the PIPEDA rules in CLAUDE.md.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services import wallet_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import (
        wallet_import_commit_limit,
        wallet_import_validate_limit,
    )
except ImportError:
    from dependencies import get_admin_user  # noqa: F401
    from services import wallet_import_service as import_svc  # type: ignore
    from utils.audit_logger import log_admin_action  # noqa: F401
    from utils.rate_limiter import (  # type: ignore
        wallet_import_commit_limit,
        wallet_import_validate_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter()

# Same caps as the booking importer — the reference export is 13 rows, so
# these leave generous headroom without letting a runaway upload sit in
# memory. Parsing and planning run in a worker thread.
MAX_CSV_BYTES = 12_000_000  # 12 MB per file
MAX_ROWS = 50_000  # per file


def _serialize_items(items: list[import_svc.ImportReportItem]) -> list[dict[str, Any]]:
    """Flat, PII-free report rows. old_id is the legacy wallet-entry _id."""
    return [
        {
            "row_num": i.row_num,
            "old_id": i.old_id,
            "field": i.field,
            "message": i.message,
        }
        for i in items
    ]


async def _read_csv_rows(upload: UploadFile, label: str) -> list[dict[str, str]]:
    """Read one uploaded CSV into rows, or raise a caller-fixable 4xx."""
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
    """Re-check the role per handler, matching the booking/Stripe importers.

    The router is already included behind ``require_super_admin``; this is
    belt-and-braces so the guard survives a future re-mount under a weaker
    dependency.
    """
    if admin.get("role") != "super_admin":
        raise HTTPException(status_code=403, detail="Legacy wallet import requires super_admin")


async def _read_all(
    wallets_csv: UploadFile,
    customers_csv: UploadFile,
    drivers_csv: UploadFile,
) -> dict[str, list[dict[str, str]]]:
    return {
        "wallets": await _read_csv_rows(wallets_csv, "wallets"),
        "customers": await _read_csv_rows(customers_csv, "customers"),
        "drivers": await _read_csv_rows(drivers_csv, "drivers"),
    }


async def _build_plan(files: dict[str, list[dict[str, str]]], batch: str) -> Any:
    """build_plan does its own Supabase reads (phone lookups) synchronously,
    so it runs in a worker thread to avoid blocking the event loop — same
    reasoning as the booking importer's ``_build_plan``."""
    return await asyncio.to_thread(
        import_svc.build_plan,
        files["wallets"],
        files["customers"],
        files["drivers"],
        batch=batch,
    )


def _report(plan: Any, batch: str, results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Dry-run (or post-commit) report. ``plan.stats`` is already JSON-safe."""
    out: dict[str, Any] = {
        "batch": batch,
        "can_commit": len(plan.errors) == 0 and len(plan.deltas_to_apply) > 0,
        "counts": dict(plan.stats),
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }
    if results is not None:
        out["results"] = results
    return out


@router.post("/wallets/import/validate")
@wallet_import_validate_limit
async def validate_wallet_import(
    request: Request,
    wallets_csv: UploadFile = File(...),
    customers_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse + validate the three CSVs and return the report. No writes."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    files = await _read_all(wallets_csv, customers_csv, drivers_csv)
    plan = await _build_plan(files, batch)
    return _report(plan, batch)


@router.post("/wallets/import/commit")
@wallet_import_commit_limit
async def commit_wallet_import(
    request: Request,
    wallets_csv: UploadFile = File(...),
    customers_csv: UploadFile = File(...),
    drivers_csv: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the CSVs and, only if clean, apply every planned wallet delta."""
    _require_super_admin(admin)
    batch = batch or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    files = await _read_all(wallets_csv, customers_csv, drivers_csv)
    plan = await _build_plan(files, batch)

    report = _report(plan, batch)
    if not report["can_commit"]:
        # Data changed since the operator validated (or they skipped
        # validate, or every row was already matched elsewhere). Refuse but
        # return 200 with committed=false and the full report, same
        # convention as the booking importer.
        return {**report, "committed": False}

    deltas = len(plan.deltas_to_apply)
    try:
        results = await asyncio.to_thread(import_svc.commit_plan, plan)
    except Exception as e:
        # Loud, not swallowed: this applies real money deltas, so a partial
        # failure needs a real root cause, not a soft "please retry".
        logger.error(
            "legacy wallet import commit failed",
            extra={"batch": batch, "deltas": deltas},
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="Import commit failed; some deltas may have been applied. Re-run validate to see current state.",
        ) from e

    applied = sum(1 for r in results if r["status"] == "applied")
    deduped = sum(1 for r in results if r["status"] == "deduped")
    failed = sum(1 for r in results if r["status"] == "failed")

    # Audit trail carries counts + batch only — never CSV contents or PII.
    await log_admin_action(
        admin,
        "legacy_wallet_import",
        "wallets",
        batch,
        {
            "deltas_planned": deltas,
            "applied": applied,
            "deduped": deduped,
            "failed": failed,
            "sum_net": plan.stats.get("sum_net"),
        },
    )
    return {
        **_report(plan, batch, results),
        "committed": True,
        "applied": applied,
        "deduped": deduped,
        "failed": failed,
    }
