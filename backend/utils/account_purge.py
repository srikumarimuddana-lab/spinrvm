"""PIPEDA right-to-erasure purge — permanently deletes accounts past their 30-day grace period.

Runs every 24 hours. Finds users with status='pending_deletion' and
deletion_scheduled_at <= now(), then hard-deletes their ancillary records
and soft-deletes the user row (deleted_at stamp, id preserved for FK
integrity in ride/audit tables).

Replay-safety: the deletion is gated on status='pending_deletion'. Once
deleted_at is stamped the user row no longer matches status='pending_deletion'
so a second replica cannot re-process it.
"""

import asyncio
import logging
from datetime import datetime, timezone

try:
    from ..db_supabase import delete_many, get_rows, update_one
    from .datetime_utils import parse_iso_utc
except ImportError:
    from db_supabase import delete_many, get_rows, update_one
    from utils.datetime_utils import parse_iso_utc

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 86400  # 24 hours


async def purge_pending_deletions() -> None:
    """Permanently delete accounts whose 30-day grace period has expired."""
    now = datetime.now(timezone.utc)

    try:
        candidates = await get_rows("users", {"status": "pending_deletion"}, limit=500)
    except Exception as e:
        logger.error(f"Account purge: failed to fetch pending-deletion users: {e}", exc_info=True)
        return

    purged = 0
    for user in candidates:
        user_id = user.get("id")
        if not user_id:
            continue

        scheduled_at = parse_iso_utc(user.get("deletion_scheduled_at"))
        if scheduled_at is None or scheduled_at > now:
            continue

        try:
            await _purge_user(user_id, now)
            purged += 1
        except Exception as e:
            logger.error(f"Account purge: failed to purge user {user_id}: {e}", exc_info=True)

    if purged:
        logger.info(f"Account purge: permanently deleted {purged} account(s)")


async def _purge_user(user_id: str, now: datetime) -> None:
    """Hard-delete ancillary PII for one user, then stamp deleted_at on the user row.

    The user row is soft-deleted (not hard-deleted) to preserve FK references
    in ride and audit tables per Saskatchewan Transportation Act 7-year retention.
    All PII fields are cleared by the soft-delete plus earlier _anonymise_rides()
    call; what remains is an opaque row with only id and deleted_at.
    """
    now_iso = now.replace(microsecond=0).isoformat()

    # Hard-delete ancillary PII tables — no regulatory retention requirement.
    for table, field in [
        ("driver_documents", "driver_id"),
        ("emergency_contacts", "user_id"),
        ("saved_addresses", "user_id"),
        ("notifications", "user_id"),
        ("support_tickets", "user_id"),
    ]:
        try:
            await delete_many(table, {field: user_id})
        except Exception as e:
            logger.warning(f"Account purge: failed to delete from {table} for {user_id}: {e}")

    # Soft-delete driver record (already has deleted_at from the initial request,
    # but stamp again to be idempotent).
    try:
        await update_one("drivers", {"user_id": user_id}, {"deleted_at": now_iso})
    except Exception as e:
        logger.warning(f"Account purge: failed to soft-delete driver for {user_id}: {e}")

    # Soft-delete the user row: stamp deleted_at and clear PII fields.
    # status is set to 'deleted' so this row never re-enters the purge loop.
    await update_one(
        "users",
        {"id": user_id},
        {
            "deleted_at": now_iso,
            "status": "deleted",
            "email": None,
            "phone": None,
            "full_name": None,
            "profile_photo_url": None,
        },
    )

    logger.info(f"Account purge: permanently deleted user {user_id}")


async def account_purge_loop() -> None:
    """Background loop that runs the PIPEDA purge sweep every 24 hours."""
    logger.info("Account purge loop started (every 24h)")
    while True:
        try:
            await purge_pending_deletions()
        except Exception as e:
            logger.error(f"Account purge loop error: {e}", exc_info=True)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
