"""Process-role inventory for startup background loops.

Every name passed to ``_spawn()`` in ``core/lifespan.py`` must appear here
exactly once as ``api``, ``worker_wave1``, or ``deferred``.

- ``api``: stay on the API process (dispatch, safety, insurance, watchdog).
- ``worker_wave1``: first dedicated-worker wave; omitted when
  ``SPINR_PROCESS_ROLE=api``.
- ``deferred``: still run on the API today; not part of worker wave 1
  (money loops, route/H3/statements, etc.).

Default role ``all`` preserves current behaviour (every loop runs on the API).
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

Placement = str  # "api" | "worker_wave1" | "deferred"

PROCESS_ROLES = frozenset({"all", "api", "worker"})
LOOP_WATCHDOG_NAME = "loop_watchdog (5min)"

WORKER_WAVE1_LOOP_NAMES: Tuple[str, ...] = (
    "push_retry (30s)",
    "zoho_desk_sync (10min)",
    "driver_onboarding_reminders (15min)",
)

# Ordered to match core/lifespan.py _spawn() order (including the watchdog).
LOOP_CATALOG: Tuple[Tuple[str, Placement], ...] = (
    ("subscription_expiry (6h)", "deferred"),
    ("surge_engine (2min)", "deferred"),
    ("scheduled_dispatcher (60s)", "api"),
    ("payment_retry (5min)", "deferred"),
    ("preauth_capture (5min)", "deferred"),
    ("referral_payout (5min)", "deferred"),
    ("driver_claim_reaper (60s)", "api"),
    ("document_expiry (12h)", "deferred"),
    ("driver_onboarding_reminders (15min)", "worker_wave1"),
    ("corporate_autotopup (10min)", "deferred"),
    ("corporate_low_balance (1h)", "deferred"),
    ("allowance_reset (1h)", "deferred"),
    ("kyb_reverification (24h)", "deferred"),
    ("route_finalizer (15s)", "deferred"),
    ("route_gap_monitor (15s)", "deferred"),
    ("stale_intent_reconciler (15min)", "api"),
    ("safety_checkin (30s)", "api"),
    ("retention_purge (24h)", "deferred"),
    ("data_export_purge (1h)", "deferred"),
    ("reconciliation (daily 02:00 UTC)", "deferred"),
    ("stripe_reconcile (24h)", "deferred"),
    ("dispute_evidence_reminder (6h)", "deferred"),
    ("ledger_projection (15min)", "deferred"),
    ("distance_reconciliation (daily 04:00 UTC)", "deferred"),
    ("period1_distance_finalizer (5min)", "api"),
    ("driver_daily_rollup (30min)", "deferred"),
    ("stale_p3_closer (15min)", "api"),
    ("h3_index_reconciler (2min)", "deferred"),
    ("t4a_annual_job (yearly Feb 28)", "deferred"),
    ("driver_statements (30min)", "deferred"),
    ("stuck_ride_sweeper (60s)", "api"),
    ("stale_in_progress_ride_alerter (5min)", "api"),
    ("retention_guard_monitor (6h)", "api"),
    ("orphaned_hold_reconciler (15m)", "deferred"),
    ("offer_expiry_reaper (10s)", "api"),
    ("suspension_reactivation (10min)", "deferred"),
    ("push_retry (30s)", "worker_wave1"),
    ("zoho_desk_sync (10min)", "worker_wave1"),
    ("support_sla_breach_sweep (5min)", "deferred"),
    ("capacity_watchdog (60s)", "api"),
    ("auto_payout (1h, Sundays)", "deferred"),
    (LOOP_WATCHDOG_NAME, "api"),
)

LOOP_PLACEMENT: Dict[str, Placement] = dict(LOOP_CATALOG)

_WORKER_WAVE1 = frozenset(WORKER_WAVE1_LOOP_NAMES)


def resolve_process_role(raw: str | None, *, env: str) -> str:
    """Return a known process role. Unknown values fail in production."""
    role = (raw or "all").strip().lower() or "all"
    if role in PROCESS_ROLES:
        return role
    if (env or "").lower() == "production":
        raise RuntimeError(f"Unknown SPINR_PROCESS_ROLE={raw!r}. Expected one of: {sorted(PROCESS_ROLES)}")
    raise RuntimeError(f"Unknown SPINR_PROCESS_ROLE={raw!r}. Expected one of: {sorted(PROCESS_ROLES)}")


def should_spawn_on_api(loop_name: str, process_role: str) -> bool:
    """Whether the API lifespan should start this loop for the given role."""
    role = (process_role or "all").strip().lower() or "all"
    if role == "all":
        return True
    if role == "api":
        return LOOP_PLACEMENT.get(loop_name) != "worker_wave1"
    # Dedicated worker is a separate process (backend/worker.py). An API
    # process must not silently run as an empty worker.
    return False


def active_api_loop_names(process_role: str | None = "all") -> List[str]:
    """Watchdog names for this API role (every spawned loop except the watchdog)."""
    role = (process_role or "all").strip().lower() or "all"
    names: List[str] = []
    for name, placement in LOOP_CATALOG:
        if name == LOOP_WATCHDOG_NAME:
            continue
        if role == "all" or placement != "worker_wave1":
            names.append(name)
    return names


def worker_loop_names() -> List[str]:
    """Loops the dedicated worker owns, excluding the outbox poller."""
    return list(WORKER_WAVE1_LOOP_NAMES)


def classified_names() -> Iterable[str]:
    return (name for name, _placement in LOOP_CATALOG)
