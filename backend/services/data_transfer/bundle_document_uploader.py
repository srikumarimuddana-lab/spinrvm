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

# documents.py's _validate_file_type rejects any declared type not in its
# own ALLOWED_MIME_TYPES allowlist — "application/octet-stream" is not a
# member, so passing it always raised and was silently swallowed by this
# module's own `except Exception: continue`, meaning every document in
# every bundle replay was skipped. Map the bundle's known-safe extension
# (already checked against ALLOWED_EXTENSIONS above this lookup) to its
# real MIME type instead.
_EXT_TO_MIME_TYPE = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


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

        # bundled_file (added alongside file_export_status in
        # bundle_zip_builder) is the manifest's public pointer at the file
        # inside the ZIP and carries the same extension; _storage_key is the
        # original source, kept as the fallback so bundles exported before
        # those columns existed still import. Either way an unresolvable
        # extension lands on ".bin" and is rejected below rather than guessed.
        ext = Path(doc.get("bundled_file") or doc.get("_storage_key") or "").suffix.lower() or ".bin"
        if ext not in ALLOWED_EXTENSIONS:
            logger.warning("data-transfer import: skipping document with disallowed extension %s", ext)
            continue
        content_type = _EXT_TO_MIME_TYPE.get(ext, "application/octet-stream")
        try:
            _validate_file_type(content, content_type)
        except Exception:
            logger.warning("data-transfer import: skipping document that failed type validation (doc_id=%s)", doc_id)
            continue

        try:
            url = await _upload_bytes(content, ext, content_type)
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


async def replay_new_documents(
    driver_id: str, documents: list[dict[str, Any]], document_files: dict[str, bytes]
) -> int:
    """Update-on-reimport variant of replay_documents: skips any bundle
    document whose (document_type, side) already has a row on driver_id, so
    re-importing the same bundle in update mode doesn't pile up duplicate
    driver_documents rows every run. Does not update/replace an existing
    document's content — a changed document must be re-uploaded through the
    normal driver document flow, not through this bulk-replay path."""
    existing = await db_supabase.get_rows("driver_documents", {"driver_id": driver_id})
    existing_keys = {(row.get("document_type"), row.get("side")) for row in existing or []}

    new_documents = [doc for doc in documents if (doc.get("document_type"), doc.get("side")) not in existing_keys]
    skipped = len(documents) - len(new_documents)
    if skipped:
        logger.info(
            "data-transfer import update: skipped %d already-present document(s) for driver_id=%s", skipped, driver_id
        )
    return await replay_documents(driver_id, new_documents, document_files)


async def replay_new_insurance_periods(driver_id: str, periods: list[dict[str, Any]]) -> int:
    """Update-on-reimport variant of replay_insurance_periods: skips any
    bundle period whose (period, started_at) already has a row on
    driver_id. Still append-only (never updates/deletes an existing row,
    per the regulatory audit convention) — this only prevents re-inserting
    the exact same period on a repeat "update" import."""
    existing = await db_supabase.get_rows("driver_insurance_periods", {"driver_id": driver_id})
    existing_keys = {(row.get("period"), row.get("started_at")) for row in existing or []}

    new_periods = [p for p in periods if (p.get("period"), p.get("started_at")) not in existing_keys]
    skipped = len(periods) - len(new_periods)
    if skipped:
        logger.info(
            "data-transfer import update: skipped %d already-present insurance period(s) for driver_id=%s",
            skipped,
            driver_id,
        )
    return await replay_insurance_periods(driver_id, new_periods)
