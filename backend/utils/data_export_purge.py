"""Data-export object purge — hourly background loop.

Deletes the personal-data export ZIPs uploaded to the private `data-exports`
Storage bucket once their 7-day signed-link TTL has lapsed. Without this, every
driver's full DSAR export accumulates in Storage forever (PIPEDA data
minimization + a fat breach blast-radius). Tracking rows are written by
`_upload_export_zip` in routes/drivers.py into the `data_export_objects` table
(migration 200).

Also sweeps the admin Data Transfer module's `data-transfer-exports` bucket /
`data_transfer_export_jobs` table (migration 262) on the same schedule — same
shape, same TTL convention, different bucket/table because that export is
admin-to-admin full-fidelity data rather than a single user's self-export.

Replay-safety: runs on every replica. Each tick deletes the Storage object
first (Supabase `remove()` is idempotent — re-removing a gone object is a no-op)
and only then marks the row `deleted_at`, with per-row error isolation. Two
replicas processing the same row in the same tick is therefore harmless, and a
failed Storage delete leaves the row unclaimed so the next tick retries it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

try:
    from ..db_supabase import run_sync, supabase  # type: ignore
except ImportError:
    from db_supabase import run_sync, supabase  # type: ignore

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


logger = logging.getLogger(__name__)

_BUCKET = "data-exports"
_INTERVAL_SECONDS = 60 * 60  # hourly
_BATCH = 200

_DATA_TRANSFER_BUCKET = "data-transfer-exports"
_DATA_TRANSFER_TABLE = "data_transfer_export_jobs"


async def data_export_purge_loop() -> None:
    """Hourly: delete expired data-export objects. See module docstring."""
    while True:
        try:
            await _tick("data_export_objects", _BUCKET)
        except Exception:
            logger.error("data_export_purge tick failed", exc_info=True)
        try:
            await _tick(_DATA_TRANSFER_TABLE, _DATA_TRANSFER_BUCKET)
        except Exception:
            logger.error("data_transfer_export_jobs purge tick failed", exc_info=True)
        _record_heartbeat("data_export_purge (1h)")
        await asyncio.sleep(_INTERVAL_SECONDS)


async def _tick(table: str, bucket: str) -> None:
    if supabase is None:
        return
    now_iso = datetime.now(timezone.utc).isoformat()

    def _query():
        return (
            supabase.table(table)
            .select("id,storage_path")
            .is_("deleted_at", "null")
            .not_.is_("expires_at", "null")
            .lt("expires_at", now_iso)
            .limit(_BATCH)
            .execute()
        )

    res = await run_sync(_query)
    rows = getattr(res, "data", None) or []
    if not rows:
        return

    purged = 0
    for row in rows:
        path = row.get("storage_path")
        row_id = row.get("id")
        if not path or not row_id:
            continue
        try:
            # Idempotent delete first, then mark — a failure here leaves the row
            # unclaimed so the next tick retries instead of orphaning the object.
            await run_sync(lambda p=path: supabase.storage.from_(bucket).remove([p]))
            await run_sync(
                lambda rid=row_id: supabase.table(table).update({"deleted_at": now_iso}).eq("id", rid).execute()
            )
            purged += 1
        except Exception:
            logger.error("Failed to purge %s object id=%s", table, row_id, exc_info=True)

    if purged:
        logger.info("data_export_purge removed %d expired object(s) from %s", purged, table)
