"""Append-only logging for Claude Code / agentic-engineering actions.

Distinct from utils/audit_logger.py (rider/driver/admin actions against the
product's own data) and backend/ai/tools.py's ai_tool_audit (the rider/driver-
facing AI assistant's runtime tool calls). This module logs engineering
automation taken against the repo/infra itself -- security scans, generated
reports, code changes, decisions escalated to a human owner -- to
agent_action_log (migration 429).

PIPEDA-safe by design: agent identifiers and short free-text summaries only.
Never pass raw PII, GPS, payment data, or full request/response bodies in
`outcome_detail`.

A write failure here must never break the calling task -- mirrors
ai_tool_audit's _schedule_tool_audit: log loudly, never re-raise.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from .. import db_supabase
except ImportError:
    import db_supabase

VALID_ACTION_TYPES = {"scan", "report", "code_change", "decision_request", "escalation", "approval", "other"}
VALID_OUTCOMES = {"success", "failed", "blocked", "needs_human_review"}


async def log_agent_action(
    agent_name: str,
    task_description: str,
    action_type: str,
    target_surface: str,
    outcome: str,
    risk_domain: Optional[str] = None,
    files_touched: Optional[List[str]] = None,
    outcome_detail: Optional[str] = None,
    session_ref: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
) -> Optional[str]:
    """Write a single row to agent_action_log. Returns the row id on success,
    None on failure. Never raises -- an audit write failure must not break
    the security scan / report / code-change task that's being logged.
    """
    if action_type not in VALID_ACTION_TYPES:
        raise ValueError(f"log_agent_action: invalid action_type {action_type!r}, expected one of {VALID_ACTION_TYPES}")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"log_agent_action: invalid outcome {outcome!r}, expected one of {VALID_OUTCOMES}")

    record: Dict[str, Any] = {
        "agent_name": agent_name,
        "task_description": task_description,
        "action_type": action_type,
        "target_surface": target_surface,
        "risk_domain": risk_domain,
        "files_touched": files_touched or None,
        "outcome": outcome,
        "outcome_detail": outcome_detail,
        "session_ref": session_ref,
        "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
        "completed_at": completed_at.isoformat() if completed_at else None,
    }

    try:
        result = await db_supabase.insert_one("agent_action_log", record)
        return (result or {}).get("id")
    except Exception:
        logger.error(
            "agent action-log write failed",
            exc_info=True,
            extra={"agent_name": agent_name, "action_type": action_type},
        )
        return None
