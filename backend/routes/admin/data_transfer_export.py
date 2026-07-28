"""Admin Data Transfer export endpoint.

Bundles one or more users/drivers (profile, documents, ride history,
insurance-period audit trail) into a downloadable ZIP for onboarding the
same records into another Spinr environment the company operates. Full
fidelity, unredacted — the caller already has admin visibility into the
source data; see ``services/data_transfer/entity_export_service.py`` for why
this deliberately does NOT reuse the DSAR self-export redaction lists.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...documents import _extract_signed_url
    from ...services.data_transfer import bundle_zip_builder, entity_export_service
    from ...supabase_client import supabase
    from ...utils.audit_logger import log_admin_action
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from documents import _extract_signed_url
    from services.data_transfer import bundle_zip_builder, entity_export_service
    from supabase_client import supabase
    from utils.audit_logger import log_admin_action

logger = logging.getLogger(__name__)

router = APIRouter()

# A batch that's too large ties up the request thread building a big ZIP in
# memory; cap it the same way driver_import/rider_import cap CSV rows.
MAX_ENTITIES_PER_EXPORT = 100

EXPORT_STORAGE_BUCKET = "data-transfer-exports"
_EXPORT_LINK_TTL_SECONDS = 7 * 24 * 3600


class ExportEntityRef(BaseModel):
    entity_type: str = Field(..., pattern="^(driver|rider)$")
    entity_id: str


class ExportRequest(BaseModel):
    entities: list[ExportEntityRef]
    doc_types: Optional[list[str]] = None


def _entity_type_summary(entities: list[ExportEntityRef]) -> str:
    types = {e.entity_type for e in entities}
    return types.pop() if len(types) == 1 else "mixed"


async def _upload_bundle(admin_id: str, zip_bytes: bytes) -> tuple[str, str]:
    """Upload the ZIP to the private data-transfer-exports bucket and return
    (signed_url, storage_path). Mirrors _upload_export_zip in
    routes/drivers/tax_exports.py."""
    import asyncio  # noqa: PLC0415

    if not supabase:
        raise HTTPException(status_code=503, detail="Storage client not configured")

    storage_path = f"exports/{admin_id}/{uuid.uuid4()}.zip"
    loop = asyncio.get_running_loop()

    def _ensure_bucket() -> None:
        try:
            supabase.storage.create_bucket(EXPORT_STORAGE_BUCKET, options={"public": False})
        except Exception as exc:
            logger.debug("data-transfer-exports bucket ensure skipped: %s", exc)

    await loop.run_in_executor(None, _ensure_bucket)
    await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(EXPORT_STORAGE_BUCKET).upload(
            path=storage_path,
            file=zip_bytes,
            file_options={"content-type": "application/zip", "upsert": "true"},
        ),
    )
    res = await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(EXPORT_STORAGE_BUCKET).create_signed_url(storage_path, _EXPORT_LINK_TTL_SECONDS),
    )
    return _extract_signed_url(res), storage_path


@router.post("/data-transfer/export")
async def export_entities(
    body: ExportRequest,
    admin: dict = Depends(get_admin_user),
):
    """Gather + bundle the requested entities into a ZIP, upload it, and
    return a signed download link. Synchronous (not backgrounded) because the
    admin is waiting on the download — capped batch size keeps this fast
    enough for a single request/response cycle."""
    if not body.entities:
        raise HTTPException(status_code=400, detail="No entities selected")
    if len(body.entities) > MAX_ENTITIES_PER_EXPORT:
        raise HTTPException(
            status_code=422,
            detail=f"{len(body.entities)} entities requested; the limit is {MAX_ENTITIES_PER_EXPORT} per export",
        )

    pairs = [(e.entity_type, e.entity_id) for e in body.entities]
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    job_record: dict[str, Any] = {
        "id": job_id,
        "requested_by_admin_id": admin.get("id"),
        "entity_type": _entity_type_summary(body.entities),
        "entity_ids": [e.entity_id for e in body.entities],
        "doc_type_filter": body.doc_types,
        "format": "zip",
        "status": "pending",
        "created_at": now.isoformat(),
    }
    try:
        await db_supabase.insert_one("data_transfer_export_jobs", job_record)
    except Exception:
        logger.error("data-transfer export: failed to record job %s", job_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Could not record export job") from None

    try:
        bundles = await entity_export_service.gather_entity_bundles(pairs, body.doc_types)
        if not bundles:
            raise HTTPException(status_code=404, detail="None of the requested entities could be found")
        zip_bytes = bundle_zip_builder.build_export_zip(bundles)
        signed_url, storage_path = await _upload_bundle(admin.get("id", "unknown"), zip_bytes)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("data-transfer export: job %s failed", job_id, exc_info=True)
        await db_supabase.update_one(
            "data_transfer_export_jobs", {"id": job_id}, {"status": "failed", "error_message": str(e)}
        )
        raise HTTPException(status_code=502, detail="Export failed; no partial file was produced") from e

    expires_at = now + timedelta(seconds=_EXPORT_LINK_TTL_SECONDS)
    await db_supabase.update_one(
        "data_transfer_export_jobs",
        {"id": job_id},
        {
            "status": "completed",
            "storage_path": storage_path,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at.isoformat(),
        },
    )

    await log_admin_action(
        admin,
        "data_transfer_export",
        "data_transfer_export_jobs",
        job_id,
        {"entity_count": len(bundles), "requested": len(pairs), "doc_types": body.doc_types},
    )

    return {
        "job_id": job_id,
        "entity_count": len(bundles),
        "requested_count": len(pairs),
        "download_url": signed_url,
        "expires_at": expires_at.isoformat(),
    }
