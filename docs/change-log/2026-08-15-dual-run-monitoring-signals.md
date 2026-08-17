# Change Impact & Risk Log — Dual-run cutover monitoring signals (A34/P3.1)

**Date:** 2026-08-15 · **PR:** #3954 (branch `claude/subscription-bandwidth-optimization-i3uwqz`) · **Surfaces:** backend (drivers go-online, driver payouts) — live-tested surface, log required.

## Issue/gap identified
Nothing in the codebase would surface a dual-run collision live: an imported driver's first go-online in the new app, or a settled payout to a legacy-imported driver (whose Stripe Connect account the old platform may also pay into), looks identical to routine traffic in every log, metric, and audit trail.

## Root cause
The system was never designed to share a driver/customer base with a second live platform; `legacy_import_metadata` was only ever wired into retrospective earnings exclusions, never into runtime observability.

## Fix/remediation
New helper `backend/utils/dual_run_monitor.py` emitting three observation-only signals, called from two existing code paths:
1. `audit_logs` row `legacy_driver_first_go_online` — once per imported driver, on their first actual offline→online flip (once-only via a `first_go_online_at` stamp merged into `drivers.legacy_import_metadata` — additive key, no existing key touched).
2. Counter `spinr_drivers_go_online_total{is_legacy_import}` — every actual flip to online, all drivers, labeled.
3. Counter `spinr_payments_legacy_driver_payout_total` — every irreversibly-disbursed payout to a legacy-imported driver (standard path: after the terminal write; instant path: after Step 2 `Payout.create` succeeds — deliberately **not** at Step 1, whose transfer can still be reversed by a failed Step 2; placement per the manual money-auditor review of this PR).

**Feature flag:** `dual_run_monitoring_enabled` in the `app_settings` DB row, read via the cached `settings_loader.get_app_settings()`. Default **enabled** when unset (these are pure observation signals wanted for launch week; nothing user-visible ships dark). Kill switch requires no redeploy.

## Risk & impact on existing functionality
Blast radius (grep-verified):
- `routes/drivers/status.py` — one addition after the existing `record_period_transition` call, gated on `status_flipped and is_online`. No other consumer of that block. The handler's return value, invariants (`is_available ⇒ is_online`), and the post-write claim re-check are untouched.
- `routes/drivers/payouts.py` — two additions, each strictly **after** the terminal/persist DB write succeeds (standard payout: after `final_status` write; instant payout: after `transfer_completed` write, before Step 2 payout). Money math, Stripe calls, idempotency keys, and reversal logic untouched.
- `drivers.legacy_import_metadata` writers/readers: import services (write at import time, won't run again for existing rows), `utils/legacy_rides.py` + earnings/admin exclusions (read via `$eq {}` emptiness test — adding a key to an already-non-empty dict cannot change any row's legacy/organic classification), admin drivers views (display-only). The stamp is merged into a copy of the existing dict; all existing keys preserved (test-asserted).
- Failure containment: every helper entry point is wrapped — a monitoring failure logs `logger.exception` and returns; it can never fail a go-online or a settled payout. This mirrors `utils/audit_logger`'s documented never-re-raise contract and is called out in the module docstring as a deliberate exception to the don't-soften-errors rule (the guarded mutation has already succeeded when these run).
- Driver-row cache: `update_one` on `drivers` invalidates the repo cache (`repositories/_base.py`), so the once-only guard reads fresh state on subsequent flips.

## User experience effect
None visible to riders, drivers, or corporate admins. Internal-admin effect only: new audit-log rows and two new Prometheus series become available. No behavior of any shipped screen changes mid-session.

## Files modified
| File | What changed | Why |
|---|---|---|
| `backend/utils/dual_run_monitor.py` | new module (flag check, legacy detection, 3 signal emitters) | single home for dual-run observability; callers stay 5-line additions |
| `backend/routes/drivers/status.py` | +9 lines after period-transition call | signal 1+2 hook at the only actual-flip site |
| `backend/routes/drivers/payouts.py` | +8/+8 lines after the two settled-transfer writes | signal 3 on both payout paths |
| `backend/tests/test_dual_run_monitor.py` | new — 8 unit tests | once-only stamp, flag off, legacy/organic split, fail-open settings, never-raises |

## Before/after snippet (behavior-changing site, status.py)
Before:
```python
    if status_flipped:
        await _deps.record_period_transition(driver_id, 1 if is_online else 0)
```
After:
```python
    if status_flipped:
        await _deps.record_period_transition(driver_id, 1 if is_online else 0)

    if status_flipped and is_online:
        ...
        await record_go_online_flip(driver, current_user)   # never raises
```
(payouts.py additions are purely additive after existing terminal writes — no existing line changed.)

## Rollback plan
Set `dual_run_monitoring_enabled = false` in `app_settings` via the admin dashboard — no redeploy. Signals stop immediately (settings cache TTL applies, ~seconds). Already-written `first_go_online_at` stamps and audit rows are inert data (nothing reads them at runtime) and need no cleanup. Full code revert is additionally git-revert-safe since no existing behavior was modified.

## Verification performed
- `pytest tests/test_dual_run_monitor.py` — 8/8 pass.
- Regression: `test_go_online_availability.py`, `test_instant_payout.py`, `test_auto_payout.py`, `test_driver_status_notifications.py`, `test_drivers_shared_status_profile_coverage.py` — 172/172 pass.
- `ruff check` + `ruff format --check` clean on all changed files.
- No `admin-dashboard`/`rider-app`/`driver-app` change → no frontend production build applicable (backend-only diff).

## What was NOT verified
- Not exercised against live Supabase — unit tests mock the module's own bindings per repo convention; the settings-flag read and the metadata merge were not integration-tested against a real `settings`/`drivers` row.
- ~~The `auto_payout` background loop was NOT instrumented~~ — closed in a follow-up commit on this PR (user-requested): `utils/auto_payout.py` now emits `record_legacy_payout` at both completed-transfer sites (weekly pass and retry/sweep path). Auto-payout uses a single `Transfer.create` with no second Stripe step, so completed == disbursed — same semantics as the standard path. Since the loop runs replay-safe on every replica and the signal fires only on the `completed` outcome (guarded by the loop's own idempotent transfer taxonomy), a retried row can only be counted when it actually completes. Admin-initiated one-off payout paths (if any outside these flows) remain uninstrumented.
- Prometheus scrape/dashboard for the two new series not set up here — the metrics exist in `/metrics` exposition once emitted; alerting is a separate ops task (E4 remains open).
- Old-app-side visibility: these signals observe only this system's half of a collision, by construction.
