"""Admin Data Transfer export endpoint.

Bundles one or more users/drivers (profile, documents, ride history,
insurance-period audit trail) into a downloadable ZIP for onboarding the
same records into another Spinr environment the company operates. Full
fidelity, unredacted — the caller already has admin visibility into the
source data; see ``services/data_transfer/entity_export_service.py`` for why
this deliberately does NOT reuse the DSAR self-export redaction lists.

Backgrounded (``BackgroundTasks.add_task``), following the same pattern as
the DSAR self-export in ``routes/drivers/tax_exports.py``: the route inserts
a ``pending`` job row and returns immediately; gather + build + upload runs
after the response, and the Jobs & History tab (``data_transfer_jobs.py``)
is how the admin discovers completion and gets the download link. This
replaced an earlier synchronous version that gathered/zipped/uploaded
inline — a real risk for the max 100-entity batch (up to 500 rides ×
documents per entity) tying up a request/response cycle.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...services.data_transfer import bundle_zip_builder, entity_export_service, observability, tabular_writer
    from ...supabase_client import supabase
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import data_transfer_export_limit
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from services.data_transfer import bundle_zip_builder, entity_export_service, observability, tabular_writer
    from supabase_client import supabase
    from utils.audit_logger import log_admin_action
    from utils.rate_limiter import data_transfer_export_limit

logger = logging.getLogger(__name__)

router = APIRouter()

# A batch that's too large would tie up the background task for a long time;
# cap it the same way driver_import/rider_import cap CSV rows. Backgrounding
# removes the request-timeout pressure this cap originally guarded against,
# but a very large batch is still a lot of work for one task — keeping the
# same cap for now rather than raising it as a side effect of this change.
MAX_ENTITIES_PER_EXPORT = 100

EXPORT_STORAGE_BUCKET = "data-transfer-exports"
# How long a completed export bundle stays in Storage before the purge loop
# (utils/data_export_purge.py) deletes it — NOT a signed-URL TTL; no signed
# URL is minted at export time (see _upload_bundle's docstring). Download
# links are always minted fresh, 1-hour-lived, on demand via
# GET /data-transfer/jobs/{job_id}/download.
_EXPORT_RETENTION_SECONDS = 7 * 24 * 3600

# format -> (builder(bundles) -> bytes, file extension, content-type)
_FORMAT_BUILDERS: dict[str, tuple[Any, str, str]] = {
    "zip": (bundle_zip_builder.build_export_zip, "zip", "application/zip"),
    "csv": (tabular_writer.write_csv, "csv", "text/csv"),
    "json": (tabular_writer.write_json, "json", "application/json"),
    "excel": (tabular_writer.write_excel, "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


class ExportEntityRef(BaseModel):
    entity_type: str = Field(..., pattern="^(driver|rider)$")
    entity_id: str


class ExportRequest(BaseModel):
    entities: list[ExportEntityRef]
    doc_types: Optional[list[str]] = None
    format: str = Field("zip", pattern="^(zip|csv|json|excel)$")
    # PIA recommendation R-C (docs/privacy/2026-07-28-pia-data-transfer-export.md,
    # ACTION_ITEMS.md B11): a short business-justification string, required on
    # every export request, stored on the job row for accountability — see
    # migration 264. Bounds match the PIA's own stated range (10-200 chars).
    reason: str = Field(..., min_length=10, max_length=200)
    # PIA recommendation R-B: optional data-minimization scope flags. Default
    # True (current full-fidelity behavior) for backward compatibility — a
    # lower-sensitivity export can opt OUT of the two highest-sensitivity
    # field groups (exact GPS, raw document bytes) rather than needing a
    # separate low-fidelity export path.
    include_ride_gps: bool = True
    include_document_bytes: bool = True


def _entity_type_summary(entities: list[ExportEntityRef]) -> str:
    types = {e.entity_type for e in entities}
    return types.pop() if len(types) == 1 else "mixed"


async def _upload_bundle(admin_id: str, file_bytes: bytes, ext: str, content_type: str) -> str:
    """Upload the export file to the private data-transfer-exports bucket and
    return storage_path. Mirrors _upload_export_zip in
    routes/drivers/tax_exports.py.

    Does NOT mint a signed URL here — this route is fully backgrounded (see
    export_entities' 202 response), so there is no request left to hand a URL
    back to by the time the upload completes. The Jobs & History tab
    (data_transfer_jobs.py) always mints a fresh one on demand from
    storage_path when the admin asks for it, so generating one eagerly here
    was pure waste (computed, then immediately discarded)."""
    import asyncio  # noqa: PLC0415

    if not supabase:
        raise RuntimeError("Storage client not configured")

    storage_path = f"exports/{admin_id}/{uuid.uuid4()}.{ext}"
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
            file=file_bytes,
            file_options={"content-type": content_type, "upsert": "true"},
        ),
    )
    return storage_path


async def _run_export_job(
    job_id: str,
    admin: dict,
    pairs: list[tuple[str, str]],
    doc_types: Optional[list[str]],
    fmt: str,
    reason: str,
    include_ride_gps: bool,
    include_document_bytes: bool,
) -> None:
    """Background task: gather, build, upload, and update the job row.
    Any failure here is recorded on the job row (status=failed,
    error_message) rather than raised — there is no request left to raise
    to by the time this runs, so the Jobs & History tab (plus the Sentry
    capture below) is the only way a failure surfaces to anyone."""
    import time  # noqa: PLC0415

    builder, ext, content_type = _FORMAT_BUILDERS[fmt]
    t0 = time.monotonic()
    try:
        bundles = await entity_export_service.gather_entity_bundles(
            pairs, doc_types, include_ride_gps, include_document_bytes
        )
        if not bundles:
            raise RuntimeError("None of the requested entities could be found")
        file_bytes = builder(bundles)
        storage_path = await _upload_bundle(admin.get("id", "unknown"), file_bytes, ext, content_type)
    except Exception as e:
        duration_ms = (time.monotonic() - t0) * 1000.0
        logger.error("data-transfer export: job %s failed", job_id, exc_info=True)
        observability.record_export_result("failed", fmt, duration_ms)
        observability.capture_failure(
            "Data Transfer export job failed",
            "data_transfer_export_failed",
            {
                "job_id": job_id,
                "admin_id": admin.get("id"),
                "format": fmt,
                "requested_count": len(pairs),
                "error": str(e),
            },
        )
        await db_supabase.update_one(
            "data_transfer_export_jobs", {"id": job_id}, {"status": "failed", "error_message": str(e)}
        )
        return

    observability.record_export_result("completed", fmt, (time.monotonic() - t0) * 1000.0)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=_EXPORT_RETENTION_SECONDS)
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
        {
            "entity_count": len(bundles),
            "requested": len(pairs),
            "doc_types": doc_types,
            "reason": reason,
            "include_ride_gps": include_ride_gps,
            "include_document_bytes": include_document_bytes,
        },
    )


@router.post("/data-transfer/export", status_code=202)
@data_transfer_export_limit
async def export_entities(
    body: ExportRequest,
    background_tasks: BackgroundTasks,
    request: Request = None,
    admin: dict = Depends(get_admin_user),
):
    """Validate the request, record a pending job, and hand the actual
    gather/build/upload work to a background task. Returns immediately with
    the job_id — check its status via the Jobs & History tab or
    GET /data-transfer/jobs/{job_id}.

    Rate-limited (@data_transfer_export_limit, 10/hour) — each call exports
    full-fidelity, unredacted PII for up to 100 other users at once; see
    utils/rate_limiter.py for the threat model this guards against. SlowAPI
    needs a parameter named ``request`` typed as starlette Request; do not
    remove it (same requirement as dsar_export_limit in tax_exports.py)."""
    if not body.entities:
        raise HTTPException(status_code=400, detail="No entities selected")
    if len(body.entities) > MAX_ENTITIES_PER_EXPORT:
        raise HTTPException(
            status_code=422,
            detail=f"{len(body.entities)} entities requested; the limit is {MAX_ENTITIES_PER_EXPORT} per export",
        )

    pairs = [(e.entity_type, e.entity_id) for e in body.entities]
    job_id = str(uuid.uuid4())
    job_record: dict[str, Any] = {
        "id": job_id,
        "requested_by_admin_id": admin.get("id"),
        "entity_type": _entity_type_summary(body.entities),
        "entity_ids": [e.entity_id for e in body.entities],
        "doc_type_filter": body.doc_types,
        "format": body.format,
        "reason": body.reason,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db_supabase.insert_one("data_transfer_export_jobs", job_record)
    except Exception:
        logger.error("data-transfer export: failed to record job %s", job_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Could not record export job") from None

    background_tasks.add_task(
        _run_export_job,
        job_id,
        admin,
        pairs,
        body.doc_types,
        body.format,
        body.reason,
        body.include_ride_gps,
        body.include_document_bytes,
    )

    return {
        "job_id": job_id,
        "status": "pending",
        "requested_count": len(pairs),
    }
