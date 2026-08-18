"""Regulatory guard-trigger integrity monitor — A35 defense-in-depth.

An ad-hoc, hand-written SQL script — run directly against Postgres (dashboard
SQL editor or `psql`, not through any code in this repo) — disabled the
append-only guard triggers on ``driver_insurance_periods``/``financial_events``/
``audit_logs`` in order to hard-delete rows those triggers exist specifically
to protect: the 7-year SGI insurance-period audit trail, the immutable
financial ledger, and the tamper-evident admin audit log
(``docs/audit/2026-08-16-legacy-ride-count-drop-investigation.md``, A35 in
``ACTION_ITEMS.md``). That instance targeted test accounts and was confirmed
benign, but the mechanism it used would do the same thing to a real driver's
regulated history if reused carelessly.

**Nothing in application code can prevent this.** `ALTER TABLE ... DISABLE
TRIGGER` is a DDL privilege exercised outside any request path this backend
controls — no `BEFORE INSERT/UPDATE/DELETE` trigger, no RLS policy, no
Python guard clause can intercept a direct database session. The only thing
code *can* do is detect it, loudly.

This loop is deliberately ALERT-ONLY — pure detection, zero mutation. It now
merges TWO independent sources every tick:

1. The read-only ``check_disabled_guard_triggers()`` RPC (migration 317, a
   dynamic scan over ``pg_trigger`` for every non-internal trigger matching
   the repo's append-only-guard naming convention — ``%_no_mutate`` /
   ``%_no_delete``) — catches a guard left disabled *right now*, at
   whatever moment this loop happens to run.
2. Recent rows written by the ``guard_trigger_ddl_audit`` event trigger
   (migration 318, ACTION_ITEMS.md A37) — catches a guard that was disabled
   and already re-enabled again *between* ticks, the gap described below
   that (1) alone structurally cannot see.

Either source finding a disabled/recently-disabled guard writes one
``audit_logs`` row (security-relevant event → audit table + log, per
CLAUDE.md's Observability Conventions) plus a CRITICAL log line and a Sentry
capture so on-call sees it, not from a routine daily digest.

**Formerly a known limitation, closed by A37 (2026-08-17)**: this loop used
to be a pure point-in-time poll of trigger *state*, not an event-based audit
of trigger *changes* — it could only catch a guard left disabled across a
check boundary, and structurally could not catch a disable →
mutate/delete → re-enable cycle completed within a single `psql`/dashboard
session (the exact shape of the incident that motivated A35). Migration
318's `ddl_command_end` event trigger closes that: it fires synchronously
the instant any `ALTER TABLE` finishes, so a same-session disable/re-enable
now leaves a permanent, timestamped `audit_logs` row the moment it happens,
which `_fetch_realtime_events()` below picks up on this loop's very next
tick. **What's still bounded by this loop's 6h cadence**: the actual
Sentry page / CRITICAL log. The *record* is now real-time; the *page* is
still worst-case ~6h behind, because this loop (not the event trigger
itself) is what calls Sentry — see migration 318's own comment for why a
synchronous page directly from SQL (`pg_notify` + a listener, or Supabase
Realtime) was deliberately not built in the same change. A future
enhancement could shrink that further; not done here to keep each change's
blast radius small and separately reviewable.

Cadence: every 6 hours — reasonable for how quickly a page needs to reach a
human once the underlying record is already real-time (see above).

Replay-safety (CLAUDE.md / ``spinr-background-loop`` skill): pure read + a
loud side effect (log/Sentry/one audit row), same shape as
``stale_in_progress_ride_alerter``. Repeat-alert suppression uses a Redis
``SET NX`` dedupe key per (table, trigger) pair so a still-disabled guard
re-pages once a day rather than every tick, without ever suppressing the
*first* alert on a genuinely new disable — and fails OPEN (alerts again) if
Redis itself is unavailable, because a missed security alert is worse than a
duplicate one.

No feature flag. Unlike a product-facing alert, this is a security control —
there is no "silence it, we're aware" posture that makes sense here, and
CLAUDE.md's audit_logs writer already fails soft (never blocks the caller) on
its own errors, so there is no operational reason to add an off switch.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timedelta, timezone

try:
    from utils.loop_monitor import record_heartbeat as _record_heartbeat
except ImportError:

    def _record_heartbeat(name: str) -> None:  # type: ignore[misc]
        pass


try:
    from .. import db_supabase as db
    from ..utils.audit_logger import log_admin_action
    from .metrics import inc as _metric_inc
    from .redis_client import redis_set_nx
except ImportError:
    import db_supabase as db  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore
    from utils.metrics import inc as _metric_inc  # type: ignore
    from utils.redis_client import redis_set_nx  # type: ignore

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 6 * 60 * 60  # 6 hours
# Re-alert cadence for a still-disabled guard: loud enough that an ignored
# page nags again within the day, quiet enough not to re-fire every tick.
_ALERT_DEDUPE_TTL_SECONDS = 24 * 60 * 60
_LOOP_NAME = "retention_guard_monitor (6h)"
_SYSTEM_ACTOR = {"id": "system", "role": "system"}


async def _already_alerted_recently(table_name: str, trigger_name: str) -> bool:
    """Redis dedupe. True iff an alert already fired for this exact
    (table, trigger) pair within ``_ALERT_DEDUPE_TTL_SECONDS``. Fails OPEN
    (returns False -> caller alerts) on a Redis error."""
    key = f"spinr:alert:disabled_guard_trigger:{table_name}:{trigger_name}"
    try:
        acquired = await redis_set_nx(key, "1", _ALERT_DEDUPE_TTL_SECONDS)
    except Exception as exc:
        logger.warning(
            f"retention_guard_monitor: dedupe check unavailable for {table_name}.{trigger_name}, "
            f"alerting anyway (fail-open): {exc}"
        )
        return False
    return not acquired


async def _escalate(disabled: list[dict]) -> None:
    """CRITICAL log + Sentry capture + one audit_logs row. Pure side effect —
    never touches the disabled trigger, never re-enables anything (a
    background loop silently "fixing" a DDL change an operator made on
    purpose, e.g. mid-migration, would be its own hazard — this is a page,
    not an auto-remediation)."""
    summary = ", ".join(f"{d['table_name']}.{d['trigger_name']}" for d in disabled)
    logger.critical(
        "REGULATORY GUARD TRIGGER DISABLED — append-only protection is currently OFF for: "
        f"{summary}. This table can be mutated/deleted right now with no application-layer "
        "block. If this is not an active, deliberate migration in progress, treat as a P0 "
        "incident: confirm who/why via pg_stat_statements (postgres_logs does not capture "
        "DML on this project — see A34/A35 in ACTION_ITEMS.md), and re-enable with "
        "`ALTER TABLE <table> ENABLE TRIGGER <trigger>` the moment it's safe to."
    )
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.capture_message(
            "REGULATORY GUARD TRIGGER DISABLED",
            level="fatal",
            tags={"spinr_alert": "disabled_guard_trigger", "domain": "admin", "surface": "backend"},
            contexts={"disabled_guard_triggers": {"triggers": disabled}},
        )
    except Exception as sentry_err:  # pragma: no cover - telemetry must never break the loop
        logger.debug(f"retention_guard_monitor: Sentry escalation unavailable: {sentry_err}")

    try:
        await log_admin_action(
            _SYSTEM_ACTOR,
            "regulatory_guard_trigger_disabled_detected",
            "database",
            "pg_trigger",
            details={"disabled_triggers": disabled, "detected_at": datetime.now(timezone.utc).isoformat()},
        )
    except Exception as exc:  # audit_logger already fails soft internally; belt-and-suspenders here
        logger.error(f"retention_guard_monitor: failed to write audit_logs row: {exc}", exc_info=True)

    _metric_inc("spinr_admin_disabled_guard_trigger_total", by=len(disabled))


_REALTIME_EVENT_ACTION = "regulatory_guard_trigger_disabled_realtime"
# How far back to look for A37 event-trigger rows each tick. Deliberately
# wider than CHECK_INTERVAL_SECONDS (not exactly equal to it) so a tick that
# runs late (jitter, a missed cycle, a restart) can't let a row silently age
# out of the lookback window before any tick ever sees it. Duplicate reads
# across overlapping windows are expected and harmless -- the same
# per-(table, trigger) Redis dedupe key used for the state-poll path below
# also covers rows surfaced this way, so a trigger already escalated this
# window doesn't re-page just because its audit_logs row is read twice.
_REALTIME_LOOKBACK_SECONDS = CHECK_INTERVAL_SECONDS * 2


async def _fetch_realtime_events() -> list[dict]:
    """Rows written synchronously by migration 318's ddl_command_end event
    trigger (ACTION_ITEMS.md A37) -- the disable/re-enable-within-one-session
    case the state poll above structurally cannot see, because by the time
    this poll runs the trigger may already be back to enabled. Returns the
    same {table_name, trigger_name, tgenabled} shape the RPC path uses, so
    both feed one escalation pipeline. Never raises -- a failure here must
    not prevent the state-poll half of this tick from still alerting.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=_REALTIME_LOOKBACK_SECONDS)).isoformat()
    try:
        rows = await db.get_rows(
            "audit_logs",
            filters={"action": _REALTIME_EVENT_ACTION, "created_at": {"$gte": cutoff}},
            order="created_at",
            desc=True,
            limit=200,
        )
    except Exception as exc:
        logger.error(f"retention_guard_monitor: realtime-event fetch failed: {exc}", exc_info=True)
        return []

    out: list[dict] = []
    for row in rows or []:
        raw_details = row.get("details")
        try:
            # audit_logs.details is TEXT (production schema) holding a JSON
            # string, not a native JSONB column -- see migration 318's own
            # comment on why this can't be assumed to deserialize for free.
            details = json.loads(raw_details) if isinstance(raw_details, str) else (raw_details or {})
        except (TypeError, ValueError) as exc:
            logger.warning(f"retention_guard_monitor: unparseable realtime-event details, skipping row: {exc}")
            continue
        for t in details.get("disabled_triggers") or []:
            out.append(
                {
                    "table_name": t.get("table_name") or "unknown",
                    "trigger_name": t.get("trigger_name") or "unknown",
                    "tgenabled": t.get("tgenabled"),
                    "source": "realtime_event",
                    "detected_at": details.get("detected_at"),
                }
            )
    return out


async def _check() -> dict[str, int]:
    """One tick. Returns counters for logging/tests.

    Two independent sources feed one escalation pipeline:
    - the state poll (RPC `check_disabled_guard_triggers`) -- catches a
      guard left disabled *right now*, at whatever cadence this loop runs.
    - the A37 realtime-event log (`_fetch_realtime_events`) -- catches a
      guard that was disabled and already re-enabled again *between* ticks,
      which the state poll alone cannot see (that's the entire reason A37
      exists; see migration 318 and its Change Impact Log).
    Both share the same per-(table, trigger) dedupe, so a trigger caught by
    both sources in the same window pages once, not twice.
    """
    stats = {"disabled": 0, "alerted": 0, "deduped": 0, "realtime_events": 0}

    try:
        state_rows = await db.rpc("check_disabled_guard_triggers", {}) or []
    except Exception as exc:
        logger.error(f"retention_guard_monitor: RPC check failed: {exc}", exc_info=True)
        state_rows = []

    realtime_rows = await _fetch_realtime_events()
    stats["realtime_events"] = len(realtime_rows)

    all_rows = list(state_rows) + realtime_rows
    stats["disabled"] = len(all_rows)
    if not all_rows:
        logger.info(f"retention_guard_monitor: {stats}")
        return stats

    to_alert = []
    for row in all_rows:
        table_name = row.get("table_name") or "unknown"
        trigger_name = row.get("trigger_name") or "unknown"
        if await _already_alerted_recently(table_name, trigger_name):
            stats["deduped"] += 1
            continue
        to_alert.append(row)

    if to_alert:
        await _escalate(to_alert)
        stats["alerted"] = len(to_alert)

    logger.info(f"retention_guard_monitor: {stats}")
    return stats


async def retention_guard_monitor_loop() -> None:
    """Every 6h: alert (never mutate) if any append-only regulatory guard
    trigger is found disabled. See module docstring for the full A35
    background and why this stays detect-only."""
    await asyncio.sleep(random.uniform(0, CHECK_INTERVAL_SECONDS))
    while True:
        try:
            await _check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"retention_guard_monitor: tick failed: {exc}", exc_info=True)
            _metric_inc("spinr_bgloop_errors_total", {"loop": "retention_guard_monitor"})
        _record_heartbeat(_LOOP_NAME)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS + random.uniform(-60, 60))
