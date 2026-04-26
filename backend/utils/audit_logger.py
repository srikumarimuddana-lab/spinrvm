"""Shared helper for writing admin audit log entries."""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
except ImportError:
    import db_supabase


async def log_admin_action(
    admin: Dict[str, Any],
    action: str,
    resource: str,
    resource_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Write a single row to audit_logs.

    Failures are logged but never re-raised — an audit write failure must not
    roll back the underlying mutation.
    """
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": str(uuid.uuid4()),
                "actor_id": admin["id"],
                "actor_role": admin.get("role"),
                "action": action,
                "resource": resource,
                "resource_id": resource_id,
                "details": details or {},
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        logger.error(
            "audit_log write failed: action=%s resource=%s resource_id=%s actor=%s",
            action,
            resource,
            resource_id,
            admin.get("id"),
            exc_info=True,
        )
