"""Data-export object purge — hourly background loop.

Deletes the personal-data export ZIPs uploaded to the private `data-exports`
Storage bucket once their 7-day signed-link TTL has lapsed. Without this, every
driver's full DSAR export accumulates in Storage forever (PIPEDA data
minimization + a fat breach blast-radius). Tracking rows are written by
`_upload_export_zip` in routes/drivers.py into the `data_export_objects` table
(migration 200).

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


async def data_export_purge_loop() -> None:
    """Hourly: delete expired data-export objects. See module docstring."""
    while True:
        try:
            await _tick()
        except Exception:
            logger.error("data_export_purge tick failed", exc_info=True)
        _record_heartbeat("data_export_purge (1h)")
        await asyncio.sleep(_INTERVAL_SECONDS)


async def _tick() -> None:
    if supabase is None:
        return
    now_iso = datetime.now(timezone.utc).isoformat()

    def _query():
        return (
            supabase.table("data_export_objects")
            .select("id,storage_path")
            .is_("deleted_at", "null")
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
            await run_sync(lambda p=path: supabase.storage.from_(_BUCKET).remove([p]))
            await run_sync(
                lambda rid=row_id: (
                    supabase.table("data_export_objects").update({"deleted_at": now_iso}).eq("id", rid).execute()
                )
            )
            purged += 1
        except Exception:
            logger.error("Failed to purge data-export object id=%s", row_id, exc_info=True)

    if purged:
        logger.info("data_export_purge removed %d expired export object(s)", purged)
