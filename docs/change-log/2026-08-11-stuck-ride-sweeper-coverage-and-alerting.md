# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (local commits — not pushed; see commit SHAs below) |
| Related issue or gap ID | P2 task #16 |

## 1. Issue / gap identified

`backend/utils/stuck_ride_sweeper.py` only recovers rides stuck in
`status == "searching"` (5-minute timeout), but two other modules
(`driver_claim_reaper.py`, `stale_intent_reconciler.py`) had comments that
falsely implied it also covered stuck `in_progress` rides. In reality, an
`in_progress` ride abandoned by a force-killed driver app has **no
automated recovery path** — the rider stays locked out of booking a new
ride (`in_progress` is in `active_statuses`) and the driver's Period-3
insurance audit row (`driver_insurance_periods`) stays open indefinitely,
until a rider complaint prompts a manual `admin_complete_ride`.

## 2. Root cause

`stuck_ride_sweeper.py`'s atomic claim filters `.eq("status", "searching")`
by design (it exists specifically to recover a lost in-process
searching-timeout after a pod restart). No loop was ever added to cover the
analogous gap for `in_progress` — the two other modules' comments describing
"the stuck-ride sweeper's job" were written loosely and never corrected as
the sweeper's actual scope stayed narrow.

## 3. Fix / remediation

1. **Doc fix (no behavior change):** corrected the misleading comments in
   `driver_claim_reaper.py` and `stale_intent_reconciler.py` to state
   precisely what the sweeper covers (`searching` only) and to point at the
   new alerter for the `in_progress` gap.
2. **New alert-only detection loop:**
   `backend/utils/stale_in_progress_ride_alerter.py` — a new background loop
   (`stale_in_progress_ride_alert_loop`, 5-minute interval, registered in
   `core/lifespan.py`) that finds `in_progress` rides whose driver has
   produced no location write (`drivers.updated_at`) for ≥ 10 minutes and
   escalates via `sentry_sdk.capture_message` (`domain=dispatch`,
   `surface=backend`, `ride_id`, `driver_id` — no PII) plus a
   `logger.error` structured log. **It never mutates `rides.status`, any
   `drivers` column, or `driver_insurance_periods`** — detection only, by
   design (see CLAUDE.md's "Transitions from in_progress are completed
   only" invariant: an automated force-complete here would itself be an
   unreviewed, higher-risk state mutation).
3. Repeat-alert suppression via a Redis `SET NX` dedupe key per ride
   (1-hour TTL), following the same idiom `driver_claim_reaper.py` uses for
   its leader lock. Fails **open** (proceeds to alert) on a Redis error,
   per the 2026-08-11 `redis_set_nx`-raises fix already in this repo's
   history — missing a dedupe is far cheaper than a silent multi-hour
   alerting gap.
4. New kill-switch flag `stale_in_progress_ride_alert_enabled`
   (`app_settings`, default `True`) added to `schemas.py`'s `AppSettings`.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** This is a brand-new module with no existing
callers, plus two comment-only edits (no code change) in
`driver_claim_reaper.py` and `stale_intent_reconciler.py`, plus one new
loop registration + one watchdog-list entry in `core/lifespan.py`.

- **Zero ride-state-mutation risk.** The new loop performs exactly two
  read queries per tick (`rides` then `drivers`, both via `db_supabase.get_rows`)
  and, on a match, one Redis `SET NX` plus a Sentry/log call. It holds no
  reference to `record_period_transition`, `set_driver_available`,
  `update_ride`, or `update_one` — enforced by a static test
  (`test_module_never_imports_insurance_period_or_driver_mutation_helpers`)
  and by a `FakeDB` test double that only implements `get_rows`, so any
  future accidental write attempt fails the test suite immediately rather
  than at runtime.
- **Who else reads `rides.status == "in_progress"` / `drivers.updated_at`**
  (grepped for blast radius): dispatch matching (`services/dispatch_service.py`),
  `stale_intent_reconciler.py` (active-ride guard), `routes/rides/*` (active-ride
  lookups), `routes/admin/rides.py` (`admin_complete_ride`,
  `admin_cancel_ride`), `routes/drivers/location.py` (writer of
  `drivers.updated_at`). This change is read-only against all of them —
  no writer semantics change, so none of these paths can regress.
- **Loop-registry impact:** adds one new entry to `core/lifespan.py`'s
  spawn list and to `_WATCHDOG_LOOP_NAMES` (so the loop itself is
  heartbeat-monitored). No existing loop's registration, ordering, or
  interval changed.
- **Query load:** two additional read queries every 5 minutes, bounded to
  200 rows per table per tick (`CANDIDATE_LIMIT`). Negligible relative to
  the existing 60s/15min sweepers already scanning `rides`/`drivers`.
- **Alert-noise risk (not a correctness risk):** a driver legitimately
  backgrounded for >10 minutes mid-trip (rare, but possible on a long
  highway leg with a dead zone) would page on-call. This is the intended
  tradeoff at this threshold (see rationale in file 6) and is mitigated by
  the hourly re-alert cadence rather than continuous paging, plus the kill
  switch below if it proves too noisy in practice.

## 5. User-experience effect

**Nobody.** This is a backend-only, invisible-to-clients alerting loop —
no API response, WebSocket event, push notification, or UI element changes
for rider, driver, corporate admin, or internal admin. The only observable
effect is a new Sentry event class reaching on-call.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stale_in_progress_ride_alerter.py` | New file: alert-only detection loop | Closes the gap; see §3 |
| `backend/utils/driver_claim_reaper.py` | Comment-only: corrected false claim about sweeper coverage | Doc accuracy |
| `backend/utils/stale_intent_reconciler.py` | Comment-only: corrected false claim about sweeper coverage | Doc accuracy |
| `backend/core/lifespan.py` | Registered new loop (`_spawn`) + added its name to `_WATCHDOG_LOOP_NAMES` | Wire up the new loop per the standard registration pattern |
| `backend/schemas.py` | Added `stale_in_progress_ride_alert_enabled: bool = True` to `AppSettings` | Flag-without-redeploy kill switch (CLAUDE.md convention) |
| `backend/tests/test_stale_in_progress_ride_alerter.py` | New file: 17 tests | Coverage for detection, dedupe, flag, no-mutation invariant, loop wrapper |

## 7. Before / after

Not applicable in the usual sense (no existing behavior-changing diff — this
is additive-only: a new loop plus two comment corrections with zero code
behavior change). The one existing-behavior-adjacent change is what the two
corrected comments *claim*, not what any code *does*:

```
# Before (utils/driver_claim_reaper.py, misleading)
# ... The stuck-ride sweeper recovers the ride, not the driver flag.

# After
# ... The stuck-ride sweeper (`utils/stuck_ride_sweeper.py`) recovers the
# ride ONLY when it is in `searching` ... A stuck `in_progress` ride has no
# automated recovery today; `utils/stale_in_progress_ride_alerter.py`
# alerts (never mutates) on that gap.
```

## 8. Rollback plan

- **Primary (no redeploy):** flip `app_settings.stale_in_progress_ride_alert_enabled`
  to `false` via the admin dashboard / DB. Takes effect within the existing
  60-second settings cache TTL. This stops every alert; it never touches
  ride state or insurance periods either way, so there is no data to repair.
- **Secondary (redeploy):** if the loop itself needs to be pulled (e.g. a
  bug in the query logic causing excessive DB load), remove/comment out its
  `_spawn(...)` call in `core/lifespan.py` and redeploy — a `git revert` of
  this change is safe and complete, unlike a change that touches ride
  state, because nothing here was ever applied to live data.
- No migration, no feature-flagged UI, no Stripe/wallet/ride-state data to
  reconcile on rollback in either case.

## 9. Verification performed

- [x] Automated tests run (unit): `pytest -q --no-cov` on
  `tests/test_stale_in_progress_ride_alerter.py` (17 passed) and the three
  adjacent suites that touch the same modules —
  `tests/test_stuck_ride_sweeper.py`, `tests/test_stuck_ride_sweeper_coverage.py`,
  `tests/test_driver_claim_reaper.py`, `tests/test_driver_claim_reaper_coverage.py`,
  `tests/test_stale_intent_reconciler.py` (80 passed total, 0 failed).
- [x] `ruff check` and `ruff format --check` on every file touched — all
  clean (pre-existing repo-wide ruff findings in unrelated files were left
  untouched, out of scope for this change).
- [x] Import sanity: `core.lifespan` imports cleanly with the new loop
  wired in (`_spawn` registration + watchdog list entry), confirming no
  circular-import or syntax issue was introduced.
- [x] Blast-radius grep performed (see §4): dispatch, rides routes, admin
  rides routes, location write endpoint, stale_intent_reconciler — all
  read-only against this change.
- [x] Reviewed against CLAUDE.md conventions: ride state machine
  (deliberately never transitions state), Sentry tag conventions
  (domain/surface/ride_id/driver_id, no PII), Redis dedupe pattern
  (`redis_set_nx`, fail-open per the documented 2026-08-11 behavior change),
  `spinr-background-loop` skill's replay-safety contract, PIPEDA (no raw
  GPS/PII in the alert — only IDs and a duration).
- [x] Feature-flagged (see §3/§8), though as an alert-only, zero-mutation
  change it does not strictly require CLAUDE.md gate #3's "ship dark, flip
  on later" sequencing (nothing user-visible or behavior-changing is
  gated) — the flag is provided as an operational kill switch, and defaults
  **on** rather than dark, a deliberate choice explained in §3/schemas.py's
  comment: missing an alert for a stuck trip is a worse failure mode than
  one extra Sentry event.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip, no data to repair)
- [x] Blast radius is stated, not assumed (see §4's explicit reader list)
- [x] No silent behavior change to an already-shipped flow — nothing
  user-visible changed; §5 states this explicitly rather than leaving it
  implied

## What was NOT verified

- **Not tested against a live Supabase or Redis instance** — only against
  `mock_supabase_client`-style fakes and monkeypatched `redis_set_nx`/
  `get_app_settings`. The real PostgREST `$lt`/`$in` filter translation for
  the new `rides`/`drivers` queries was reasoned about (same filter shapes
  already used elsewhere, e.g. `stale_intent_reconciler.py`) but not
  exercised against a real database in this session.
- **No staging/canary soak.** The loop has not run against real production
  or staging traffic; the 10-minute threshold and 1-hour re-alert TTL are
  reasoned estimates (see the module docstring's cadence math), not
  empirically tuned against real driver-app background-throttling
  behavior. If early production alerts show a high false-positive rate
  (e.g. iOS background location throttling routinely exceeding 10 minutes
  in some conditions), the threshold should be revisited — the kill switch
  covers the interim.
- **No visual/UI regression tooling applies** — this change has no UI
  surface, so that gap (tracked generally in `ACTION_ITEMS.md`) is not
  applicable here, stated explicitly rather than left silent.
- **Sentry delivery itself was not verified end-to-end** (no real
  `SENTRY_DSN` in this environment) — `sentry_sdk.capture_message` is
  called with the same tag shape used by `refresh_tokens.py` and
  `services/ledger_service.py`'s existing, presumably-verified escalation
  call sites, and the call is wrapped in its own try/except so a Sentry
  failure cannot break the loop, but the actual PagerDuty/on-call routing
  on `domain=dispatch` + `spinr_alert=stale_in_progress_ride` was not
  confirmed to exist or fire.
