"""
Driver deactivation-appeal service.

Backs the process docs/legal/driver-deactivation-appeals-policy.md
describes: a driver whose account is on hold (suspended/banned/needs
review) can submit an appeal, and a reviewer looks at it. See migration
320 for the table shape and RLS rationale.

The actual account-reactivation side effect (what happens to drivers.status
when an appeal is approved) intentionally does NOT live here — it's handled
at the route layer (routes/admin/driver_appeals.py) by reusing the existing,
already-tested routes.admin.drivers.admin_driver_action function (the same
'unban'/'reactivate' logic the admin dashboard's driver-detail page already
uses), rather than duplicating driver-status-transition rules in a second
place. This module only owns the appeal record itself.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
    from ..db_supabase import DuplicateRecordError
except ImportError:  # pragma: no cover - direct module imports in tests
    import db_supabase  # type: ignore
    from db_supabase import DuplicateRecordError  # type: ignore

VALID_APPEAL_TYPES = ("suspension", "ban", "needs_review", "other")
VALID_STATUSES = ("pending", "approved", "denied")


class DuplicatePendingAppealError(Exception):
    """Raised when a driver already has a pending appeal — one at a time,
    matching the policy's "review, then respond" process rather than
    letting a driver flood the queue with resubmissions."""


async def get_pending_appeal(driver_id: str) -> Optional[Dict[str, Any]]:
    return await db_supabase.find_one("driver_appeals", {"driver_id": driver_id, "status": "pending"})


async def create_appeal(
    driver_id: str,
    *,
    appeal_type: str,
    driver_message: str,
    original_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if appeal_type not in VALID_APPEAL_TYPES:
        raise ValueError(f"invalid appeal_type: {appeal_type}")
    if not driver_message or not driver_message.strip():
        raise ValueError("driver_message is required")

    if await get_pending_appeal(driver_id) is not None:
        raise DuplicatePendingAppealError(driver_id)

    try:
        row = await db_supabase.insert_one(
            "driver_appeals",
            {
                "driver_id": driver_id,
                "appeal_type": appeal_type,
                "driver_message": driver_message.strip(),
                "original_reason": original_reason,
                "status": "pending",
            },
        )
    except DuplicateRecordError as e:
        # The check above is a friendlier pre-check, not the actual
        # guarantee — migration 320's partial unique index
        # (driver_appeals_one_pending_per_driver) is what actually prevents
        # two concurrent submissions (e.g. a double-tapped submit button)
        # from both landing as 'pending' rows. This is that race caught.
        raise DuplicatePendingAppealError(driver_id) from e
    logger.info("[DRIVER_APPEALS] created driver_id=%s appeal_type=%s", driver_id, appeal_type)
    return row or {}


async def get_appeal_history(driver_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    return (
        await db_supabase.get_rows(
            "driver_appeals", {"driver_id": driver_id}, order="created_at", desc=True, limit=limit
        )
        or []
    )


async def list_appeals(status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    filters: Dict[str, Any] = {}
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        filters["status"] = status
    return await db_supabase.get_rows("driver_appeals", filters, order="created_at", desc=False, limit=limit) or []


async def get_appeal_stats() -> Dict[str, int]:
    stats = {"pending": 0, "approved": 0, "denied": 0}
    for status in VALID_STATUSES:
        stats[status] = await db_supabase.count_documents("driver_appeals", {"status": status})
    return stats


async def get_appeal(appeal_id: str) -> Optional[Dict[str, Any]]:
    return await db_supabase.find_one("driver_appeals", {"id": appeal_id})


async def mark_resolved(
    appeal_id: str,
    *,
    status: str,
    admin_note: Optional[str],
    resolved_by: str,
) -> None:
    """Update the appeal record itself. Does not touch drivers.status — see
    module docstring for why that lives at the route layer instead."""
    if status not in ("approved", "denied"):
        raise ValueError(f"invalid resolution status: {status}")
    now = datetime.now(timezone.utc)
    await db_supabase.update_one(
        "driver_appeals",
        {"id": appeal_id},
        {
            "status": status,
            "admin_note": admin_note,
            "resolved_by": resolved_by,
            "resolved_at": now,
            "updated_at": now,
        },
    )
    logger.info("[DRIVER_APPEALS] resolved appeal_id=%s status=%s resolved_by=%s", appeal_id, status, resolved_by)
