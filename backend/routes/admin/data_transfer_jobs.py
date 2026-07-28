"""List Data Transfer export batches and regenerate download links.

Closes a gap from the original module build: ``data_transfer_export_jobs``
(migration 262) was created specifically "so the Jobs tab can render
history" (its own table comment), but no endpoint ever read it back — every
completed export was written and then effectively invisible again once its
response left the browser. This is that endpoint.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

try:
    from ... import db_supabase
    from ...dependencies import get_admin_user
    from ...documents import _extract_signed_url
    from ...supabase_client import supabase
except ImportError:
    import db_supabase
    from dependencies import get_admin_user
    from documents import _extract_signed_url
    from supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter()

EXPORT_STORAGE_BUCKET = "data-transfer-exports"
_LIST_COLUMNS = (
    "id,requested_by_admin_id,entity_type,entity_ids,doc_type_filter,"
    "format,status,error_message,created_at,completed_at,expires_at"
)


@router.get("/data-transfer/jobs")
async def list_data_transfer_jobs(
    limit: int = Query(50, ge=1, le=200),
    admin: dict = Depends(get_admin_user),
):
    """Most recent export batches across all admins (this module is
    super_admin-gated already, so cross-admin visibility here is oversight,
    not a privilege escalation). Excludes soft-deleted rows (the purge loop
    marks `deleted_at` after removing the Storage object)."""
    rows = await db_supabase.get_rows(
        "data_transfer_export_jobs",
        {"deleted_at": None},
        order="created_at",
        desc=True,
        limit=limit,
        columns=_LIST_COLUMNS,
    )
    jobs = []
    for row in rows or []:
        entity_ids = row.get("entity_ids") or []
        jobs.append({**row, "entity_count": len(entity_ids)})
    return {"jobs": jobs}


@router.get("/data-transfer/jobs/{job_id}/download")
async def regenerate_job_download_link(
    job_id: str,
    admin: dict = Depends(get_admin_user),
):
    """Mint a fresh signed URL for a completed job's file. The original
    signed URL returned at export time is never stored (short-lived by
    design); this regenerates one from the tracked storage_path as long as
    the job hasn't been purged yet."""
    rows = await db_supabase.get_rows(
        "data_transfer_export_jobs", {"id": job_id}, limit=1, columns="id,status,storage_path,deleted_at"
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Job not found")
    job = rows[0]
    if job.get("deleted_at") or not job.get("storage_path"):
        raise HTTPException(status_code=410, detail="This export has expired and was purged")
    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Job status is {job.get('status')!r}, not completed")
    if not supabase:
        raise HTTPException(status_code=503, detail="Storage client not configured")

    import asyncio  # noqa: PLC0415

    loop = asyncio.get_running_loop()
    try:
        res = await loop.run_in_executor(
            None,
            lambda: supabase.storage.from_(EXPORT_STORAGE_BUCKET).create_signed_url(job["storage_path"], 3600),
        )
        signed_url = _extract_signed_url(res)
    except Exception:
        logger.error("data-transfer jobs: failed to regenerate signed URL for job %s", job_id, exc_info=True)
        raise HTTPException(status_code=502, detail="Could not regenerate download link") from None

    return {"download_url": signed_url}
