"""Capacity saturation watchdog. 60 s tick. Read-only; alerts via webhook.

Supabase compute does not autoscale, so the database tier a burst arrives on is
the tier it gets handled with. Upgrading is a ~2-minute dashboard action — but
only if someone knows to do it *before* users feel it. This loop is that
tripwire.

The underlying signals already existed and were alerted nowhere (open item T10
in docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md: "mechanism is built; observability
isn't"). ADR-010's Grafana pipeline remains Proposed/unimplemented, so this
deliberately reuses the one alerting path that works in production today — the
same ALERT_WEBHOOK_URL that loop_watchdog posts to — rather than waiting on it.

Signals (all thresholds overridable by env, see the constants):

  1. DB thread pool saturation — spinr_db_thread_pool_queue_depth > 50 for 3
     consecutive ticks. 50 is the recorded breaking point in loadtest/README.md.
     Sustained rather than instantaneous because a brief queue during a burst is
     the pool doing its job; a persistent one means requests are losing.
  2. DB call rejections — split by reason. reason=circuit_open fires
     immediately on any increase (the DB is already failing). The other
     reasons (deadline_exhausted / deadline_timeout) are client-budget
     rejections — dominated by device clock skew and slow cellular transit,
     not DB capacity — so they only alert when the fleet sheds them in
     volume: > CAPACITY_DB_REJECTED_PER_MIN_THRESHOLD/min sustained for
     SUSTAIN_TICKS (0 restores the legacy any-increase immediate mode).
  3. Rate-limit pressure — spinr_rate_limit_violation_total rising faster than
     120/min for 3 ticks. Distinguishes "real burst" from "a limit set too low".

Replay safety (CLAUDE.md background-loop contract): this loop writes no
application state — no ride row, no Redis key, no user-visible notification.
Its only side effect is delivering an alert, which is idempotent-by-cooldown
rather than by claim flag.

The alert path is deliberately kept OFF the database. An earlier version sent
email through ``send_transactional_email``, which performs three DB operations
per send (load app_settings, query email_suppressions, insert email_send_log) —
so an alert reporting a saturated pool queued on that same saturated pool and
would fail exactly when it mattered. It now uses ``send_ops_alert_email``, which
runs on credentials cached at startup and touches no table. Startup priming is
the one DB read, and it happens at a moment of our choosing.

Alert scope is per-signal, because the underlying conditions have different
shapes:

* PER-REPLICA (db_pool_saturation, db_circuit_open) — INTENTIONAL, not an
  oversight. Thread-pool saturation is a per-process condition and metrics.py
  is explicitly per-process ("Each backend replica keeps its own counters"). A
  leader lock would mean the one elected replica reports its own healthy pool
  while seven saturated ones stay silent.
* FLEET-SCOPED (db_calls_rejected, rate_limit_pressure) — these count *user
  requests*, so every replica observes its own slice of one fleet-wide
  condition and they all alert about the same thing. With UVICORN_WORKERS=2 on
  an 8-machine pool that is up to 16 near-identical messages per cooldown
  window, two per FLY_MACHINE_ID (workers share a machine id, so they are not
  even attributable). These claim a shared Redis key so the fleet reports once.

The Redis claim FAILS OPEN: if Redis is unavailable the alert is sent anyway on
per-process cooldown alone. Suppressing an alert because the deduper is down
would reproduce the failure mode this whole module exists to prevent. Redis is
also not the subsystem being reported on — the DB-free guarantee below is
unaffected. Set CAPACITY_FLEET_DEDUP=off to disable dedup entirely (rollback).

Every message carries its FLY_MACHINE_ID so duplicates stay attributable.

See docs/runbooks/capacity-scaling.md §6-7 for thresholds and the response
playbook.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, Optional

import httpx

try:
    from ..repositories._base import get_db_pool_stats
    from .metrics import snapshot as _metrics_snapshot
except ImportError:  # pragma: no cover - exercised by the non-package entrypoint
    from repositories._base import get_db_pool_stats  # type: ignore
    from utils.metrics import snapshot as _metrics_snapshot  # type: ignore

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = int(os.environ.get("CAPACITY_WATCHDOG_INTERVAL_SECONDS", "60"))

# Sustained queue depth that means requests are queueing for DB threads rather
# than being served. loadtest/README.md records queued_calls>50 as the point
# where the pool became the bottleneck.
QUEUE_DEPTH_THRESHOLD = int(os.environ.get("CAPACITY_QUEUE_DEPTH_THRESHOLD", "50"))

# Rate-limit violations per minute above which the fleet is shedding user
# traffic in volume.
RATE_LIMIT_VIOLATIONS_PER_MIN_THRESHOLD = int(os.environ.get("CAPACITY_RATE_LIMIT_VIOLATIONS_THRESHOLD", "120"))

# Non-circuit DB rejections (deadline_exhausted / deadline_timeout) per minute
# above which the deadline-rejection signal trips. These rejections are counted
# per client request and are dominated by client-side conditions — a device
# clock ≥15 s behind the server expires every deadline it sends — so a single
# rejection per tick is not page-worthy; the 2026-08-24 alert storm was exactly
# that. 0 restores the legacy any-increase immediate behaviour (rollback is a
# config change + machine restart, no code deploy).
DB_REJECTED_PER_MIN_THRESHOLD = int(os.environ.get("CAPACITY_DB_REJECTED_PER_MIN_THRESHOLD", "30"))

# Consecutive ticks a sustained signal must hold before alerting. Guards against
# alerting on a single spike that the queue absorbs on its own.
SUSTAIN_TICKS = int(os.environ.get("CAPACITY_SUSTAIN_TICKS", "3"))

# Per-signal alert throttle. A burst lasts longer than one tick; without this a
# 30-minute incident would post ~30 messages per replica.
COOLDOWN_SECONDS = int(os.environ.get("CAPACITY_ALERT_COOLDOWN_SECONDS", "1800"))

# Signals whose underlying condition is fleet-wide rather than per-process, so
# every replica would otherwise alert about the same thing. See the module
# docstring on alert scope.
FLEET_SCOPED_SIGNALS = frozenset({"db_calls_rejected", "rate_limit_pressure"})

# Kill-switch for the shared-Redis dedup (rollback to pure per-replica
# alerting without a code deploy).
FLEET_DEDUP_ENABLED = os.environ.get("CAPACITY_FLEET_DEDUP", "on").strip().lower() not in {
    "off",
    "0",
    "false",
    "no",
}

_LOOP_NAME = "capacity_watchdog (60s)"

# In-process state. Lost on restart, which is correct: a fresh process has no
# sustained-signal history to speak of and should re-observe before alerting.
_consecutive: Dict[str, int] = {}
_last_alerted: Dict[str, float] = {}
_last_counters: Dict[str, float] = {}


def _machine_id() -> str:
    """Fly machine id, so a per-replica alert says which replica."""
    return os.environ.get("FLY_MACHINE_ID") or os.environ.get("HOSTNAME") or "unknown"


def _counter_total(
    snap: dict, metric: str, label_key: Optional[str] = None, label_value: Optional[str] = None
) -> float:
    """Sum a counter across its label sets, optionally filtered to one label.

    metrics.snapshot() keys each counter by a tuple of (label, value) pairs, so
    a metric split by `reason` or `path` arrives as several cells that have to
    be summed to get a fleet-visible total for this process.
    """
    bucket = snap.get("counters", {}).get(metric, {})
    total = 0.0
    for labels_tuple, value in bucket.items():
        if label_key is not None:
            labels = dict(labels_tuple)
            if labels.get(label_key) != label_value:
                continue
        total += value
    return total


def _rejection_reason_deltas(snap: dict) -> Dict[str, float]:
    """Per-reason increase of spinr_db_calls_rejected_total since last tick.

    Excludes circuit_open (signal 2a owns it) and drops reasons whose delta is
    zero or unknown, so the alert body names only what actually moved. Purely
    informational: the signal itself is computed from the totals so its
    baseline keys stay stable even if a new reason label appears mid-flight.
    """
    bucket = snap.get("counters", {}).get("spinr_db_calls_rejected_total", {})
    reasons = {dict(labels).get("reason", "unknown") for labels in bucket}
    deltas: Dict[str, float] = {}
    for reason in sorted(reasons - {"circuit_open"}):
        total = _counter_total(snap, "spinr_db_calls_rejected_total", "reason", reason)
        delta = _delta(f"db_calls_rejected_reason:{reason}", total)
        if delta:
            deltas[reason] = delta
    return deltas


def _delta(name: str, current: float) -> Optional[float]:
    """Increase since the previous tick. None on the first observation.

    Returning None the first time matters: without it, a replica that has been
    up for hours would alert on its entire accumulated history the moment this
    loop starts.
    """
    previous = _last_counters.get(name)
    _last_counters[name] = current
    if previous is None:
        return None
    delta = current - previous
    # Counters only rise in-process; a drop means a reset, so don't report it.
    return delta if delta >= 0 else None


def _sustained(signal: str, tripped: bool) -> bool:
    """True when `signal` has been tripped for SUSTAIN_TICKS consecutive ticks."""
    if not tripped:
        _consecutive[signal] = 0
        return False
    _consecutive[signal] = _consecutive.get(signal, 0) + 1
    return _consecutive[signal] >= SUSTAIN_TICKS


def _alert_recipients() -> list:
    """Parse ALERT_EMAIL_TO into a recipient list (comma-separated)."""
    try:
        from ..core.config import settings
    except ImportError:  # pragma: no cover - non-package entrypoint
        from core.config import settings  # type: ignore

    raw = getattr(settings, "ALERT_EMAIL_TO", None) or ""
    return [addr.strip() for addr in raw.split(",") if addr.strip()]


async def _send_webhook(signal: str, body: str, webhook_url: str) -> bool:
    """Post to the Slack-compatible webhook. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json={"text": body})
            resp.raise_for_status()
        logger.info("capacity_watchdog: posted %s webhook alert", signal)
        return True
    except Exception as exc:
        # Never swallow silently — a watchdog that cannot alert is worse than
        # no watchdog, because it looks like everything is fine.
        logger.error("capacity_watchdog: failed to post %s webhook: %s", signal, exc, exc_info=True)
        return False


async def _send_email(signal: str, subject: str, body: str, recipients: list) -> bool:
    """Email the alert via the same SES/Resend path receipts use.

    Returns True if at least one recipient was accepted. A per-recipient failure
    does not abort the rest — during an incident, reaching three of four people
    is worth more than an all-or-nothing send.

    Uses ``send_ops_alert_email``, NOT ``send_transactional_email``. The latter
    performs three DB operations per send (load app_settings, query
    email_suppressions, insert email_send_log), which would route an alert
    about a saturated database through that same saturated pool — failing
    precisely when it matters. The ops path uses credentials cached at startup
    and skips the suppression check and audit-log insert. See that function's
    docstring for the trade-offs accepted.
    """
    try:
        from .email_provider import send_ops_alert_email
    except ImportError:  # pragma: no cover - non-package entrypoint
        from utils.email_provider import send_ops_alert_email  # type: ignore

    any_sent = False
    for addr in recipients:
        try:
            sent = await send_ops_alert_email(
                to=addr,
                subject=subject,
                text=body,
                # log_id is a PII-safe correlation tag; the recipient address is
                # never logged in full by the provider.
                log_id="capacity",
            )
            any_sent = any_sent or bool(sent)
        except Exception as exc:
            logger.error("capacity_watchdog: failed to email %s alert: %s", signal, exc, exc_info=True)
    return any_sent


def _fleet_claim_key(signal: str) -> str:
    return f"spinr:capacity_alert:{signal}"


def _claim_token() -> str:
    """Identify this *process*, not just this machine.

    UVICORN_WORKERS=2 means two processes share one FLY_MACHINE_ID, so the
    machine id alone cannot tell two claim holders apart.
    """
    return f"{_machine_id()}:{os.getpid()}"


async def _try_claim_fleet_cooldown(signal: str) -> bool:
    """Claim the right to alert for `signal` on behalf of the whole fleet.

    Returns True if this replica should send. FAILS OPEN — on any Redis error
    we return True and let the per-process cooldown be the only bound, because
    a missed capacity alert costs more than a duplicate one.

    Note the in-process fallback in utils/redis_client.py makes this a no-op
    when REDIS_URL is unset (dev/test): the claim always succeeds locally, so
    behaviour matches the pre-dedup code path exactly.
    """
    if not FLEET_DEDUP_ENABLED or signal not in FLEET_SCOPED_SIGNALS:
        return True
    try:
        from .redis_client import redis_set_nx
    except ImportError:  # pragma: no cover - non-package entrypoint
        from utils.redis_client import redis_set_nx  # type: ignore
    try:
        return await redis_set_nx(_fleet_claim_key(signal), _claim_token(), COOLDOWN_SECONDS)
    except Exception as exc:
        logger.error(
            "capacity_watchdog: fleet dedup unavailable for %s, alerting anyway (fail-open): %s",
            signal,
            exc,
        )
        return True


async def _release_fleet_cooldown(signal: str) -> None:
    """Release a claim whose alert reached no channel, so another replica retries.

    Best-effort compare-and-delete: we only clear the key if it still holds our
    own token, so we cannot delete a claim another replica took in the interim.
    There is no CAS here — the worst case is one duplicate alert, which is the
    right way to be wrong for a watchdog.
    """
    if not FLEET_DEDUP_ENABLED or signal not in FLEET_SCOPED_SIGNALS:
        return
    try:
        from .redis_client import redis_delete, redis_get
    except ImportError:  # pragma: no cover - non-package entrypoint
        from utils.redis_client import redis_delete, redis_get  # type: ignore
    key = _fleet_claim_key(signal)
    try:
        if await redis_get(key) == _claim_token():
            await redis_delete(key)
    except Exception as exc:
        logger.error(
            "capacity_watchdog: failed to release fleet claim for %s (it will expire in %ss): %s",
            signal,
            COOLDOWN_SECONDS,
            exc,
        )


async def _post_alert(signal: str, text: str, webhook_url: Optional[str]) -> None:
    """Fan one alert out to every configured channel, respecting the cooldown.

    Webhook and email are attempted INDEPENDENTLY — a dead Slack workspace must
    not cost you the email, and vice versa. The cooldown is stamped only if at
    least one channel actually delivered, so a total delivery failure is retried
    on the next tick instead of being silently throttled away for 30 minutes.

    The "never alerted" case is an explicit ``None``, NOT a 0.0 default.
    ``time.monotonic()`` counts from an arbitrary origin that is near zero early
    in a process's life, so ``now - 0.0 < COOLDOWN_SECONDS`` is true for the
    first COOLDOWN_SECONDS of uptime — which would suppress every alert during
    the first 30 minutes after a deploy or a scale-up, exactly the window where
    a burst is most likely and this watchdog is most needed.
    """
    now = time.monotonic()
    last_sent = _last_alerted.get(signal)
    if last_sent is not None and now - last_sent < COOLDOWN_SECONDS:
        return

    recipients = _alert_recipients()
    if not webhook_url and not recipients:
        return  # no channel configured — stay quiet (dev/test)

    # Fleet-scoped signals dedupe across replicas. Claim AFTER the local
    # cooldown and channel checks so a throttled or channel-less replica never
    # burns the fleet's claim without sending anything.
    if not await _try_claim_fleet_cooldown(signal):
        # Another replica is reporting this window. Stamp the local cooldown so
        # this process stops re-attempting (and re-hitting Redis) every tick.
        _last_alerted[signal] = now
        logger.info("capacity_watchdog: %s already reported by another replica this window", signal)
        return

    body = f"{text}\nReplica: {_machine_id()}\nRunbook: docs/runbooks/capacity-scaling.md"

    delivered = False
    if webhook_url:
        delivered = await _send_webhook(signal, body, webhook_url) or delivered
    if recipients:
        subject = f"[Spinr capacity] {signal} on {_machine_id()}"
        delivered = await _send_email(signal, subject, body, recipients) or delivered

    if delivered:
        _last_alerted[signal] = now
    else:
        await _release_fleet_cooldown(signal)
        logger.error("capacity_watchdog: %s alert reached NO channel — will retry next tick", signal)


async def _tick(webhook_url: Optional[str]) -> None:
    """Sample the signals and alert on the ones that trip.

    Metrics are always refreshed even when no webhook is configured, so
    /metrics scrapes and local development still benefit from the fresh gauge.
    """
    stats = get_db_pool_stats()
    snap = _metrics_snapshot()

    queue_depth = stats["queue_depth"]
    breaker_state = stats["breaker_state"]

    rejected_total = _counter_total(snap, "spinr_db_calls_rejected_total")
    circuit_open_total = _counter_total(snap, "spinr_db_calls_rejected_total", "reason", "circuit_open")
    violations_total = _counter_total(snap, "spinr_rate_limit_violation_total")

    rejected_delta = _delta("db_calls_rejected", rejected_total)
    circuit_open_delta = _delta("db_calls_rejected_circuit_open", circuit_open_total)
    violations_delta = _delta("rate_limit_violations", violations_total)

    # --- Signal 1: sustained DB thread-pool saturation ---------------------
    pool_saturated = _sustained("db_pool_saturation", queue_depth > QUEUE_DEPTH_THRESHOLD)
    if pool_saturated:
        logger.warning(
            "capacity_watchdog: DB pool saturated",
            extra={"queue_depth": queue_depth, "max_workers": stats["max_workers"]},
        )
        await _post_alert(
            "db_pool_saturation",
            f":warning: *Spinr DB pool saturated*\n"
            f"Queue depth *{queue_depth}* (> {QUEUE_DEPTH_THRESHOLD}) for "
            f"{SUSTAIN_TICKS} consecutive ticks; pool {stats['threads']}/{stats['max_workers']} threads.\n"
            f"Likely the Supabase tier, which does not autoscale — consider upgrading it.",
            webhook_url,
        )

    # --- Signal 2a: circuit breaker rejecting calls ------------------------
    # Circuit-open is immediate: the breaker only opens after real failures, so
    # there is nothing to wait and see about.
    if circuit_open_delta:
        logger.error(
            "capacity_watchdog: DB circuit breaker rejecting calls",
            extra={"circuit_open_delta": circuit_open_delta, "breaker_state": breaker_state},
        )
        await _post_alert(
            "db_circuit_open",
            f":rotating_light: *Spinr DB circuit breaker OPEN*\n"
            f"*{int(circuit_open_delta)}* calls rejected since last tick "
            f"(breaker state: `{breaker_state}`).\n"
            f"The database is already failing — check Supabase status and tier.",
            webhook_url,
        )

    # --- Signal 2b: client-deadline rejections in volume -------------------
    # Everything that isn't circuit_open is a deadline rejection
    # (deadline_exhausted / deadline_timeout): the client's time budget ran
    # out, usually because of device clock skew or slow transit rather than
    # DB capacity. Alert only on sustained volume — a lone skewed handset
    # produces a steady trickle that is diagnosable from logs, not pages.
    non_circuit_delta = None
    if rejected_delta is not None:
        non_circuit_delta = max(rejected_delta - (circuit_open_delta or 0.0), 0.0)

    reason_deltas = _rejection_reason_deltas(snap)
    if non_circuit_delta:
        # Tick-level trail regardless of whether an alert fires — the alert
        # threshold must not cost us the forensic record.
        logger.warning(
            "capacity_watchdog: DB calls rejected on client deadline",
            extra={
                "rejected_delta": non_circuit_delta,
                "reasons": reason_deltas,
                "breaker_state": breaker_state,
                "queue_depth": queue_depth,
            },
        )

    per_min = None
    if non_circuit_delta is not None:
        per_min = non_circuit_delta * (60.0 / max(INTERVAL_SECONDS, 1))

    if DB_REJECTED_PER_MIN_THRESHOLD > 0:
        rejected_tripped = _sustained(
            "db_calls_rejected",
            per_min is not None and per_min > DB_REJECTED_PER_MIN_THRESHOLD,
        )
        threshold_line = f"Sustained > {DB_REJECTED_PER_MIN_THRESHOLD}/min for {SUSTAIN_TICKS} consecutive ticks.\n"
    else:
        # Legacy mode: any increase alerts immediately (pre-2026-08-24
        # behaviour, kept as the documented rollback switch).
        rejected_tripped = bool(non_circuit_delta)
        threshold_line = ""

    if rejected_tripped:
        breakdown = ", ".join(f"{reason}: {int(count)}" for reason, count in sorted(reason_deltas.items()))
        breakdown = f" — {breakdown}" if breakdown else ""
        await _post_alert(
            "db_calls_rejected",
            f":warning: *Spinr DB calls rejected (client deadline)*\n"
            f"*{int(non_circuit_delta or 0)}* rejected in the last tick{breakdown} "
            f"(breaker state: `{breaker_state}`, queue depth {queue_depth}).\n"
            f"{threshold_line}"
            f"`deadline_*` reasons usually mean device clock skew or slow transit, "
            f"not DB capacity — see runbook §7 before scaling anything.",
            webhook_url,
        )

    # --- Signal 3: users being rate-limited in volume ----------------------
    violations_per_min = None
    if violations_delta is not None:
        violations_per_min = violations_delta * (60.0 / max(INTERVAL_SECONDS, 1))
    rate_limit_pressure = _sustained(
        "rate_limit_pressure",
        violations_per_min is not None and violations_per_min > RATE_LIMIT_VIOLATIONS_PER_MIN_THRESHOLD,
    )
    if rate_limit_pressure:
        logger.warning(
            "capacity_watchdog: sustained rate-limit violations",
            extra={"violations_per_min": violations_per_min},
        )
        await _post_alert(
            "rate_limit_pressure",
            f":warning: *Spinr shedding traffic via rate limits*\n"
            f"~*{int(violations_per_min or 0)}* violations/min "
            f"(> {RATE_LIMIT_VIOLATIONS_PER_MIN_THRESHOLD}) for {SUSTAIN_TICKS} ticks.\n"
            f"Either a genuine burst, or a limit set too low — check which path in /metrics.",
            webhook_url,
        )


async def capacity_watchdog_loop() -> None:
    """Poll saturation signals every INTERVAL_SECONDS and alert on the tripped ones.

    Reads: DB executor state, in-process metrics. Writes: log lines, a refreshed
    queue-depth gauge, and alert deliveries. The alert path itself performs no
    DB access — see the module docstring.
    """
    try:
        from ..core.config import settings
    except ImportError:  # pragma: no cover - non-package entrypoint
        from core.config import settings  # type: ignore

    try:
        from .loop_monitor import record_heartbeat
    except ImportError:  # pragma: no cover - non-package entrypoint
        from utils.loop_monitor import record_heartbeat  # type: ignore

    recipients = _alert_recipients()

    # Cache email credentials now, while the DB is healthy — startup has just
    # passed its DB health check. Doing this here is what lets the alert path
    # stay DB-free later, when an outage is exactly what we need to report.
    if recipients:
        try:
            from .email_provider import prime_ops_email_settings
        except ImportError:  # pragma: no cover - non-package entrypoint
            from utils.email_provider import prime_ops_email_settings  # type: ignore
        try:
            primed = await prime_ops_email_settings()
            if not primed:
                logger.warning(
                    "capacity_watchdog: email credentials not cached at startup — "
                    "the first alert will need one DB read, which may fail during an outage"
                )
        except Exception:
            logger.error("capacity_watchdog: failed to prime email settings", exc_info=True)

    logger.info(
        "capacity_watchdog started",
        extra={
            "interval_seconds": INTERVAL_SECONDS,
            "queue_depth_threshold": QUEUE_DEPTH_THRESHOLD,
            "webhook_enabled": bool(getattr(settings, "ALERT_WEBHOOK_URL", None)),
            "email_recipients": len(recipients),
        },
    )

    while True:
        try:
            record_heartbeat(_LOOP_NAME)
            await _tick(getattr(settings, "ALERT_WEBHOOK_URL", None))
        except Exception:
            # A watchdog must never take the process down with it.
            logger.error("capacity_watchdog tick failed", exc_info=True)
        await asyncio.sleep(INTERVAL_SECONDS)
