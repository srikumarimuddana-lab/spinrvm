"""Shared helper for writing admin audit log entries.

**The `details` blob is deliberately NOT PII-scrubbed.** This is a considered
decision, not an oversight, and it is recorded here because the T2 sink guard
(`utils/log_guard.py`) scrubs the loguru sinks and the obvious next step looks like
"scrub this too". Do not.

`audit_logs` is not a log sink in the PIPEDA sense. CLAUDE.md's prohibition names
"logs, Sentry events, or analytics payloads" — egress paths that leave the trust
boundary or land in a third party's storage. This table is primary storage in the
same Canadian-region Supabase project as `users`, under the same RLS, subject to the
same retention and deletion rules. Recording that an admin changed a rider's phone
number, including the before/after value, is the entire point of an audit trail;
redacting it produces rows that prove something happened but not what, which is
worse than no audit trail because it looks like one.

It also has regulatory weight pulling the other way: the Saskatchewan retention
rules require driver/vehicle linkage at trip time kept for 7 years, and insurance
period transitions for commercial-coverage audit for 7 years. An audit row scrubbed
to `[REDACTED]` cannot satisfy an SGI or Privacy Commissioner request.

What *does* apply here: the `details` blob must not be echoed into a log line or a
Sentry event. That boundary is enforced at those sinks, not by weakening this one.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
    from .log_context import get_request_id
except ImportError:
    from log_context import get_request_id

    import db_supabase


async def log_user_action(
    user: Dict[str, Any],
    action: str,
    resource: str,
    resource_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Write an audit log row for a rider/driver-initiated action.

    Identical schema to log_admin_action; actor_role defaults to the
    user's role claim so the audit trail distinguishes rider vs driver.
    """
    await log_admin_action(user, action, resource, resource_id, details)


async def log_admin_action(
    admin: Dict[str, Any],
    action: str,
    resource: str,
    resource_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Write a single row to audit_logs. Returns the audit log ID on success,
    None on failure.

    Failures are logged but never re-raised — an audit write failure must not
    roll back the underlying mutation.

    Column mapping (production schema from migration 06 + 51 + 278):
      entity_type = resource (e.g. "users", "rides")
      entity_id   = resource_id
      details     = JSON blob including actor_id + actor_role
      request_id  = the request-scoped correlation ID (utils/log_context.py),
                    the same one already on this request's log lines and
                    Sentry event tags -- lets a SOC investigation join an
                    audit_logs row to its Sentry event / log lines. Empty
                    string (log_context's ContextVar default) when this call
                    happens outside a request (e.g. a background loop) --
                    stored as NULL, not "", so the "$is not null" index stays
                    meaningful.
    """
    audit_id = str(uuid.uuid4())
    request_id = get_request_id() or None
    try:
        await db_supabase.insert_one(
            "audit_logs",
            {
                "id": audit_id,
                "action": action,
                "entity_type": resource,
                "entity_id": resource_id,
                "actor_id": admin["id"],
                "request_id": request_id,
                "details": {
                    "actor_id": admin["id"],
                    "actor_role": admin.get("role"),
                    **(details or {}),
                },
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        return audit_id
    except Exception:
        logger.error(
            "audit_log write failed: action=%s resource=%s resource_id=%s actor=%s",
            action,
            resource,
            resource_id,
            admin.get("id"),
            exc_info=True,
        )
        return None
