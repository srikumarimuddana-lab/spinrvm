"""Support-ticket P1 SLA breach detection (ACTION_ITEMS.md G8).

CLAUDE.md's KPI table states a P1 support-ticket response target (< 2h) but
nothing in code tracked or enforced it before this module: no deadline field,
no breach sweep, no `spinr_support_*` metric. This closes the code-side half
only — see ACTION_ITEMS.md G8 for why the Zoho-console SLA policy config is a
separate, still-open action this module does not (and cannot) perform.

Assumption (stated per CLAUDE.md's "surface assumptions" rule, not silently
resolved): Zoho Desk's ticket priority is one of Low/Medium/High/Urgent
(`admin-dashboard/.../tickets/page.tsx` PRIORITIES); nothing in this codebase
names a "P1" priority value directly. This module maps CLAUDE.md's "P1" KPI
row to Zoho's "Urgent" priority — the only single tier under CLAUDE.md's own
red-flag treatment in the dashboard's `priorityClass()` (which groups
High+Urgent visually, but Urgent is the sole top-of-stack value an Urgent/P1
equivalence reads naturally onto). If the actual intended mapping differs
(e.g. P1 should also include "High"), change `P1_PRIORITIES` below — it is
the single source of truth this module reads from.

Deadline anchor: `created_time` (ticket creation), not a "first response"
timestamp — the mirror (`zoho_desk_tickets`, migration 123) and the live Zoho
ticket payload both carry `createdTime`/`created_time`; neither surfaces a
distinct first-response timestamp today (confirmed: no `firstResponseAt`/
`respondedTime`-shaped field anywhere in zoho_desk_service.py or the mirror
schema). CLAUDE.md's KPI wording ("Support ticket response") most naturally
reads as time-to-first-response, but absent that field, creation time is the
only anchor available; if Zoho's due-date/SLA feature (G8's suggested
Zoho-console fix) is configured later, its `due_date` mirror column could
become the deadline source without changing this module's public shape.

Replay-safety: the sweep claims each breached ticket atomically via
`UPDATE zoho_desk_tickets SET sla_breach_alerted_at = now()
 WHERE zoho_id = ? AND sla_breach_alerted_at IS NULL` (migration 377) before
incrementing the metric/logging — so N replicas running the loop concurrently
each try the claim, but only one ever wins per ticket, matching this repo's
"claim flag column" replay-safety pattern (see .claude/skills/
spinr-background-loop).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from .. import db_supabase
    from .metrics import inc as _metric_inc
except ImportError:  # pragma: no cover - direct module import in tests
    import db_supabase
    from utils.metrics import inc as _metric_inc

try:
    from .loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:  # pragma: no cover
    try:
        from utils.loop_monitor import record_heartbeat as _record_heartbeat  # type: ignore
    except ImportError:  # pragma: no cover

        def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
            pass


logger = logging.getLogger(__name__)

_MIRROR_TABLE = "zoho_desk_tickets"

# See module docstring for why "Urgent" is the P1 mapping.
P1_PRIORITIES = frozenset({"Urgent"})

SLA_HOURS = 2
SLA_METRIC = "spinr_support_ticket_sla_breach_total"

SWEEP_INTERVAL_SECONDS = 300  # 5 minutes
_LOOP_NAME = "support_sla_breach_sweep (5min)"


# A ticket's `status_type` (migration 123 comment: "Open / Closed meta-status")
# is compared case-insensitively; some accounts also use "closed" inside a
# custom `status` label (e.g. "Resolved") without setting status_type — mirror
# the same check zoho_desk_sync.py already uses for closed-ticket detection so
# this file doesn't invent a second definition of "closed".
def _is_closed(row: Dict[str, Any]) -> bool:
    status_type = (row.get("status_type") or "").lower()
    status = (row.get("status") or "").lower()
    return status_type == "closed" or "closed" in status


def sla_deadline(created_time: Any, priority: Optional[str]) -> Optional[datetime]:
    """The P1 SLA deadline for a ticket, or None if it isn't a P1 ticket.

    `created_time` accepts a datetime, an ISO string, or None/empty (Zoho's
    format, e.g. "2026-06-01T12:00:00.000Z").
    """
    if (priority or "") not in P1_PRIORITIES:
        return None
    created = _parse_time(created_time)
    if created is None:
        return None
    return created + timedelta(hours=SLA_HOURS)


def is_breached(row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """True if `row` (a zoho_desk_tickets mirror row, or an equivalent dict
    with created_time/priority/status[_type]) is an open P1 ticket past its
    SLA deadline."""
    if _is_closed(row):
        return False
    deadline = sla_deadline(row.get("created_time"), row.get("priority"))
    if deadline is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now >= deadline


def _parse_time(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _candidates() -> List[Dict[str, Any]]:
    """Open P1 tickets whose SLA deadline has passed and that haven't been
    claimed by a prior sweep yet. Filters as much as possible server-side
    (priority, un-claimed, created before the deadline cutoff); the closed-
    status check runs in Python since `status`/`status_type` labels vary."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=SLA_HOURS)).isoformat()
    filters = {
        "priority": {"$in": sorted(P1_PRIORITIES)},
        "created_time": {"$lt": cutoff},
        "sla_breach_alerted_at": None,
    }
    rows = await db_supabase.get_rows(_MIRROR_TABLE, filters, limit=500)
    return rows or []


async def _claim(zoho_id: str) -> bool:
    """Atomically claim one ticket for alerting. Returns True iff this call
    won the claim (0 rows updated means another replica already claimed it,
    or it doesn't exist)."""
    result = await db_supabase.update_one(
        _MIRROR_TABLE,
        {"zoho_id": zoho_id, "sla_breach_alerted_at": None},
        {"sla_breach_alerted_at": datetime.now(timezone.utc).isoformat()},
    )
    return bool(result)


async def run_sweep() -> Dict[str, int]:
    """One sweep pass: find open, breached P1 tickets not yet alerted, claim
    each atomically, and for every ticket this replica wins, increment the
    breach metric and log a warning. Returns {"breached": N} for tests/callers."""
    candidates = await _candidates()
    breached = 0
    for row in candidates:
        if _is_closed(row) or not is_breached(row):
            continue
        zoho_id = row.get("zoho_id")
        if not zoho_id:
            continue
        if not await _claim(zoho_id):
            continue
        breached += 1
        # A P1 SLA breach means a real rider/driver has waited past the
        # published 2h response target with nothing done — this is a
        # user-visible failure of the support flow, not a recoverable
        # transient (contrast with the "degraded-but-recovered -> warning,
        # never error" guidance in CLAUDE.md's Observability Conventions):
        # nothing here self-heals, a human has to act. Logged at `warning`
        # rather than `error` because it is not a code/infra failure (no
        # exception, nothing crashed) — it is an operational/staffing signal,
        # which CLAUDE.md's log-level guidance reserves `error` for
        # "actionable failures" in the DB/auth/payment sense. The metric
        # below is what makes the breach durably visible/alertable; this log
        # line is the human-readable trail for on-call.
        logger.warning(
            "Support ticket SLA breach: P1 ticket %s created_time=%s priority=%s "
            "past %sh SLA deadline with no qualifying resolution",
            zoho_id,
            row.get("created_time"),
            row.get("priority"),
            SLA_HOURS,
        )
        _metric_inc(SLA_METRIC, {"priority": row.get("priority") or "Urgent"})
    return {"breached": breached}


async def support_sla_breach_sweep_loop() -> None:
    """Background loop: sweep every SWEEP_INTERVAL_SECONDS for open P1 tickets
    past their SLA deadline. Replay-safe: the atomic per-ticket claim
    (sla_breach_alerted_at IS NULL) in _claim() means every replica running
    this loop concurrently only ever fires the metric/log once per ticket."""
    while True:
        try:
            await run_sweep()
        except Exception:
            logger.error("support_sla_breach_sweep tick failed", exc_info=True)
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
