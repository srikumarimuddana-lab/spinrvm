"""Background loop liveness monitor.

Thread-safe singleton that tracks the last successful tick timestamp for each
named background loop.  Loops call record_heartbeat(name) after every
successful iteration; the /health endpoint calls get_loop_status() to surface
stale loops to Railway health checks and Sentry alerts.

Staleness thresholds are intentionally generous — a loop that's 2× overdue is
worth flagging, but a single slow tick should not trip a health alert.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, Optional

# Default staleness thresholds in seconds.  Keyed by the loop name as
# registered in lifespan.py.  Any loop name NOT listed here gets the
# _DEFAULT_THRESHOLD fallback.
_DEFAULT_THRESHOLD = 7200  # 2 hours

LOOP_THRESHOLDS: Dict[str, float] = {
    "subscription_expiry (6h)": 6 * 3600 * 2,  # 12 h
    "surge_engine (2min)": 2 * 60 * 4,  # 8 min
    "scheduled_dispatcher (60s)": 60 * 4,  # 4 min
    "capacity_watchdog (60s)": 60 * 4,  # 4 min
    "payment_retry (5min)": 5 * 60 * 4,  # 20 min
    "document_expiry (12h)": 12 * 3600 * 2,  # 24 h
    "corporate_autotopup (10min)": 10 * 60 * 4,  # 40 min
    "corporate_low_balance (1h)": 1 * 3600 * 4,  # 4 h
    "allowance_reset (1h)": 1 * 3600 * 4,  # 4 h
    "retention_purge (24h)": 24 * 3600 * 2,  # 48 h
    "stripe_reconcile (24h)": 24 * 3600 * 2,  # 48 h
    "route_finalizer (15s)": 15 * 60,  # 15 min — 60× the tick, tolerates a long finalization
    "route_gap_monitor (15s)": 15 * 60,  # 15 min
    "period1_distance_finalizer (5min)": 5 * 60 * 4,  # 20 min
    "distance_reconciliation (daily 04:00 UTC)": 24 * 3600 * 2,  # 48 h
    "stale_p3_closer (15min)": 15 * 60 * 3,  # 45 min — 3 missed ticks
    "driver_daily_rollup (30min)": 30 * 60 * 3,  # 90 min — 3 missed ticks
    # Dedicated worker (worker.py) loops — see core/background_loop_registry.py.
    "outbox_poller (1-10s)": 5 * 60,  # 5 min — generous even against the 10 s idle-poll ceiling
    "push_retry (30s)": 30 * 4,  # 2 min
    "zoho_desk_sync (10min)": 10 * 60 * 4,  # 40 min
    "driver_onboarding_reminders (15min)": 15 * 60 * 3,  # 45 min — 3 missed ticks
    # --- REL-001 / ROADMAP N17 (2026-09-25): the ~24 entries below were
    # missing and fell back to _DEFAULT_THRESHOLD (2h) — far too loose for
    # the safety/dispatch loops (a hung 10-30s loop could go undetected for
    # up to 2h) and simply untuned for the rest. Sized to ~3x each loop's
    # real per-tick cadence (~3 missed ticks), verified against each loop's
    # own sleep/poll structure, not just its display-name suffix. Two
    # exceptions are called out inline where a flat 3x-of-cadence would
    # either false-alert (a slow single-heartbeat-per-day job) or leave a
    # fast-polling loop's real staleness window uncovered.
    # Safety and dispatch loops first (priority per ROADMAP N17).
    "safety_checkin (30s)": 30 * 6,  # 3 min — SOS-adjacent; a bit more margin than a strict 3x
    "route_deviation_alerter (30s)": 30 * 3,  # 90 s
    "offer_expiry_reaper (10s)": 10 * 3,  # 30 s — dispatch-SLA backstop for the <2s offer P95
    "driver_readiness_reconciler (20s)": 20 * 3,  # 60 s
    "driver_claim_reaper (60s)": 60 * 3,  # 3 min
    "stuck_ride_sweeper (60s)": 60 * 3,  # 3 min
    "insurance_period_reconciler (10min)": 10 * 60 * 3,  # 30 min
    "stale_in_progress_ride_alerter (5min)": 5 * 60 * 3,  # 15 min
    "stale_intent_reconciler (15min)": 15 * 60 * 3,  # 45 min
    # Money loops.
    "preauth_capture (5min)": 5 * 60 * 3,  # 15 min
    "referral_payout (5min)": 5 * 60 * 3,  # 15 min
    "orphaned_hold_reconciler (15m)": 15 * 60 * 3,  # 45 min
    # 3h: the loop polls hourly (auto_payout.py `interval = 3600`). The
    # Sunday batch records a progress heartbeat per driver
    # (run_weekly_auto_payout), so a long-but-healthy batch is not flagged.
    "auto_payout (1h, Sundays)": 3600 * 3,  # 3 h
    "support_sla_breach_sweep (5min)": 5 * 60 * 3,  # 15 min
    # Everything else.
    # 72h: single heartbeat per ~24h tick with no inner poll
    # (kyb_reverification.py:150, `asyncio.sleep(86400 * (0.9 + jitter))`);
    # 3x the cadence tolerates ordinary jitter without false-alerting.
    "kyb_reverification (24h)": 24 * 3600 * 3,  # 72 h
    # 18h: single heartbeat per 6h tick, no inner poll
    # (dispute_evidence_reminder.py `_TICK_INTERVAL_SECONDS`).
    "dispute_evidence_reminder (6h)": 6 * 3600 * 3,  # 18 h
    # 18h: single heartbeat per ~6h tick, no inner poll
    # (retention_guard_monitor.py `CHECK_INTERVAL_SECONDS` + jitter).
    "retention_guard_monitor (6h)": 6 * 3600 * 3,  # 18 h
    "data_export_purge (1h)": 3600 * 3,  # 3 h
    "ledger_projection (15min)": 15 * 60 * 3,  # 45 min
    "h3_index_reconciler (2min)": 2 * 60 * 3,  # 6 min
    "driver_statements (30min)": 30 * 60 * 3,  # 90 min
    "suspension_reactivation (10min)": 10 * 60 * 3,  # 30 min
    # 48h: daily-cadence job with a fast (~60s) inner poll (reconciliation.py
    # heartbeats every tick regardless of whether the daily job ran) — but
    # the one tick a day that *does* run real work can legitimately take a
    # while, so this follows the existing distance_reconciliation/
    # stripe_reconcile convention above (2x the *daily* cycle) rather than a
    # literal 3x of the 60s poll, to avoid flagging a slow-but-healthy daily
    # run as stale mid-run.
    "reconciliation (daily 02:00 UTC)": 24 * 3600 * 2,  # 48 h
    # 10 min: t4a_annual_job now heartbeats on its own 60s inner poll (added
    # in this change — see utils/t4a_annual_job.py); real work fires once a
    # year, but a crashed/hung loop should still surface promptly, so this
    # follows capacity_watchdog's pattern for a frequently-polling-but-
    # rarely-acting loop rather than a literal 3x-of-a-year. The annual batch
    # records a progress heartbeat per driver (_run_issuance), so a long run
    # is not flagged; a single driver's queries hanging >10 min still is.
    "t4a_annual_job (yearly Feb 28)": 10 * 60,  # 10 min
}

_lock = threading.Lock()
_heartbeats: Dict[str, float] = {}  # loop_name → monotonic timestamp of last tick
_failures: Dict[str, str] = {}  # loop_name → safe, operator-facing failure category


def record_heartbeat(loop_name: str) -> None:
    """Record a successful tick for the named loop.  Thread-safe; O(1)."""
    with _lock:
        _heartbeats[loop_name] = time.monotonic()
        _failures.pop(loop_name, None)


def record_dependency_failure(loop_name: str) -> None:
    """Mark a required dependency unavailable until the next successful tick."""
    with _lock:
        _failures[loop_name] = "required_dependency_unavailable"


def get_loop_status(registered_names: Optional[list] = None) -> Dict[str, object]:
    """Return a status dict suitable for embedding in the /health response.

    Args:
        registered_names: list of loop names known to lifespan.py.  If None,
                          loops that have ticked or reported a failure are included.

    Returns a dict:
      {
        "healthy": bool,               # False if any loop is stale or failed
        "loops": {
          "<name>": {
            "status": "ok" | "stale" | "never_ticked" | "unhealthy",
            "seconds_since_tick": float | None,
            "threshold_seconds": float,
          }
        }
      }
    """
    now = time.monotonic()
    with _lock:
        snapshot = dict(_heartbeats)
        failures = dict(_failures)

    names = registered_names or list(dict.fromkeys([*snapshot, *failures]))
    loops: Dict[str, object] = {}
    overall_healthy = True

    for name in names:
        threshold = LOOP_THRESHOLDS.get(name, _DEFAULT_THRESHOLD)
        last = snapshot.get(name)
        failure = failures.get(name)
        if failure is not None:
            loops[name] = {
                "status": "unhealthy",
                "failure": failure,
                "seconds_since_tick": round(now - last, 1) if last is not None else None,
                "threshold_seconds": threshold,
            }
            overall_healthy = False
        elif last is None:
            # Loop has not ticked yet since startup — could be waiting for its
            # first scheduled window (e.g. stripe_reconcile waits until 02:00 UTC).
            # Don't flag as unhealthy on startup; only flag if a loop was
            # registered more than 2× its threshold ago.
            loops[name] = {
                "status": "never_ticked",
                "seconds_since_tick": None,
                "threshold_seconds": threshold,
            }
            # Not flagged as unhealthy — startup wait is expected
        else:
            elapsed = now - last
            if elapsed > threshold:
                status = "stale"
                overall_healthy = False
            else:
                status = "ok"
            loops[name] = {
                "status": status,
                "seconds_since_tick": round(elapsed, 1),
                "threshold_seconds": threshold,
            }

    return {"healthy": overall_healthy, "loops": loops}
