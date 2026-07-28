"""Admin Data Transfer import endpoints.

Two endpoints mirror the existing driver/rider bulk-import contract
(``driver_import.py``): ``/validate`` parses + dry-runs, no writes;
``/commit`` re-parses + re-validates the same ZIP and, only if clean,
creates the new rows.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

try:
    from ...dependencies import get_admin_user
    from ...services.data_transfer import entity_import_service as import_svc
    from ...utils.audit_logger import log_admin_action
except ImportError:
    from dependencies import get_admin_user
    from services.data_transfer import entity_import_service as import_svc
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_ZIP_BYTES = 200_000_000  # 200 MB — bundles carry document files, unlike the 1MB CSV imports


async def _read_zip_bytes(bundle_zip: UploadFile) -> bytes:
    raw = await bundle_zip.read()
    if len(raw) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=413, detail=f"ZIP exceeds the {MAX_ZIP_BYTES // 1_000_000} MB limit")
    return raw


def _serialize_items(items: list[import_svc.ImportReportItem]) -> list[dict[str, str]]:
    return [{"entity_id": i.entity_id, "field": i.field, "message": i.message} for i in items]


def _report(plan: import_svc.ImportPlan) -> dict:
    counts = {"new": 0, "existing_match": 0, "conflict": 0}
    for resolution in plan.resolutions.values():
        counts[resolution] = counts.get(resolution, 0) + 1
    return {
        "can_commit": plan.can_commit,
        "counts": {"entities": len(plan.entities), **counts},
        "warnings": _serialize_items(plan.warnings),
        "errors": _serialize_items(plan.errors),
    }


@router.post("/data-transfer/import/validate")
async def validate_bundle_import(
    bundle_zip: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """Dry-run: parse the bundle ZIP and report new/existing/conflicting
    entities. No writes."""
    raw = await _read_zip_bytes(bundle_zip)
    try:
        entities = import_svc.parse_bundle_zip(raw)
    except import_svc.BundleParseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    plan = await import_svc.build_plan(entities)
    return _report(plan)


@router.post("/data-transfer/import/commit")
async def commit_bundle_import(
    bundle_zip: UploadFile = File(...),
    batch: Optional[str] = Form(None),
    admin: dict = Depends(get_admin_user),
):
    """Re-validate the same ZIP and, only if clean, create the new user +
    driver profile rows. Document re-upload and insurance-period replay are
    wired in a follow-up subtask (bundle_document_uploader)."""
    raw = await _read_zip_bytes(bundle_zip)
    try:
        entities = import_svc.parse_bundle_zip(raw)
    except import_svc.BundleParseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    plan = await import_svc.build_plan(entities)

    if not plan.can_commit:
        return {**_report(plan), "committed": False}

    try:
        counts = await import_svc.commit_plan(plan)
    except Exception as e:
        logger.error("data-transfer bundle import commit failed", exc_info=True)
        raise HTTPException(status_code=502, detail="Import commit failed; some rows may have been created") from e

    await log_admin_action(
        admin,
        "data_transfer_import",
        "users",
        batch or "",
        counts,
    )
    return {**_report(plan), "committed": True, **counts}
