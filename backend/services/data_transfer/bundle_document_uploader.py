"""Re-upload a bundle entity's documents to the target environment's storage
and re-attach them to its newly-created driver_id.

The ZIP bundle carries raw document bytes (gathered by
entity_export_service._fetch_document_bytes); this module writes those bytes
to *this* environment's driver-documents bucket (a fresh storage key — the
source environment's key is meaningless here) and inserts a fresh
driver_documents row pointing at it, mirroring the upload shape in
documents.py's bulk-upload path.
"""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from ... import db_supabase
    from ...documents import ALLOWED_EXTENSIONS, _extract_signed_url, _validate_file_type
    from ...supabase_client import supabase
except ImportError:
    import db_supabase
    from documents import ALLOWED_EXTENSIONS, _extract_signed_url, _validate_file_type
    from supabase_client import supabase

logger = logging.getLogger(__name__)

DOCUMENT_STORAGE_BUCKET = "driver-documents"


async def _upload_bytes(content: bytes, ext: str, content_type: str) -> str:
    """Upload raw bytes under a fresh storage key; returns the signed URL.
    Mirrors the raw-bytes upload shape in documents.py's bulk-upload path."""
    import asyncio  # noqa: PLC0415

    storage_key = f"{uuid.uuid4()}{ext}"
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: supabase.storage.from_(DOCUMENT_STORAGE_BUCKET).upload(
            file=content, path=storage_key, file_options={"content-type": content_type}
        ),
    )
    signed_res = await loop.run_in_executor(
        None, lambda: supabase.storage.from_(DOCUMENT_STORAGE_BUCKET).create_signed_url(storage_key, 3600)
    )
    return _extract_signed_url(signed_res)


async def replay_documents(
    new_driver_id: str, documents: list[dict[str, Any]], document_files: dict[str, bytes]
) -> int:
    """Re-upload each document that has bytes in the bundle and insert a fresh
    driver_documents row for it, attached to new_driver_id. Documents whose
    bytes weren't included in the bundle (see entity_export_service's
    _content=None fallback) are skipped — their metadata-only row would point
    at nothing, which is worse than not creating it. Returns the count of
    documents actually replayed."""
    if not supabase:
        logger.error("data-transfer import: storage client not configured, skipping document replay")
        return 0

    replayed = 0
    for doc in documents:
        doc_id = doc.get("id", "")
        content = document_files.get(doc_id)
        if content is None:
            # Fall back to matching by filename prefix if the caller keyed
            # document_files by original filename instead of id.
            matches = [v for k, v in document_files.items() if doc_id and doc_id in k]
            content = matches[0] if matches else None
        if not content:
            continue

        ext = Path(doc.get("_storage_key") or "").suffix.lower() or ".bin"
        if ext not in ALLOWED_EXTENSIONS:
            logger.warning("data-transfer import: skipping document with disallowed extension %s", ext)
            continue
        try:
            _validate_file_type(content, "application/octet-stream")
        except Exception:
            logger.warning("data-transfer import: skipping document that failed type validation (doc_id=%s)", doc_id)
            continue

        try:
            url = await _upload_bytes(content, ext, "application/octet-stream")
        except Exception:
            logger.error("data-transfer import: failed to re-upload document doc_id=%s", doc_id, exc_info=True)
            continue

        now_iso = datetime.now(timezone.utc).isoformat()
        record = {
            "id": str(uuid.uuid4()),
            "driver_id": new_driver_id,
            "requirement_id": doc.get("requirement_id"),
            "requirement_key": doc.get("requirement_key"),
            "document_type": doc.get("document_type"),
            "document_url": url,
            "side": doc.get("side"),
            "status": doc.get("status", "pending"),
            "expiry_date": doc.get("expiry_date"),
            "uploaded_at": now_iso,
            "updated_at": now_iso,
        }
        try:
            await db_supabase.insert_one("driver_documents", record)
            replayed += 1
        except Exception:
            logger.error(
                "data-transfer import: failed to insert driver_documents row for doc_id=%s", doc_id, exc_info=True
            )

    return replayed


async def replay_insurance_periods(new_driver_id: str, periods: list[dict[str, Any]]) -> int:
    """Append-only replay of driver_insurance_periods rows under the new
    driver_id. Never updates or deletes an existing row — matches the
    regulatory append-only convention in CLAUDE.md. ride_id is dropped since
    the source ride doesn't exist in the target environment; the period,
    started_at, and ended_at (the regulatory-audit-relevant fields) are kept."""
    replayed = 0
    for period in periods:
        record = {
            "id": str(uuid.uuid4()),
            "driver_id": new_driver_id,
            "period": period.get("period"),
            "started_at": period.get("started_at"),
            "ended_at": period.get("ended_at"),
            "ride_id": None,
        }
        try:
            await db_supabase.insert_one("driver_insurance_periods", record)
            replayed += 1
        except Exception:
            logger.error(
                "data-transfer import: failed to replay insurance period for driver_id=%s", new_driver_id, exc_info=True
            )
    return replayed
