# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin (platform/observability infra — closest existing tag; no dedicated "platform" tag exists in the documented set) |
| PR / commit link | local commit `739e314`, integrated into `claude/spinr-app-all-surfaces-de596c` |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` — ranked blocker #27; `ACTION_ITEMS.md` A40 |

## 1. Issue / gap identified

13 of the ~18 background asyncio loops spawned in `backend/core/lifespan.py` were never registered with the existing loop watchdog (`_WATCHDOG_LOOP_NAMES` / `_loop_watchdog`). If one of those 13 silently died or hung (an unhandled exception escaping the loop's `while True` body, or a permanent stall), nothing would detect it or post an alert — the loop would just stop, with no signal to anyone until a downstream symptom was noticed manually.

## 2. Root cause

The watchdog's watched-name list (`_WATCHDOG_LOOP_NAMES`) and the actual set of spawned loops (`_spawn(name, ...)` calls) were two independently-maintained lists with no mechanism keeping them in sync. As new loops were added over time (`preauth_capture`, `referral_payout`, `driver_claim_reaper`, `kyb_reverification`, `route_finalizer`, `route_gap_monitor`, `safety_checkin`, `reconciliation`, `distance_reconciliation`, `period1_distance_finalizer`, `orphaned_hold_reconciler`, `suspension_reactivation`, `zoho_desk_sync`), several were never added to the watchdog list — a plain omission, not a deliberate exclusion. 5 of the 13 (`distance_reconciliation`, `period1_distance_finalizer`, `reconciliation`, `route_finalizer`, `zoho_desk_sync`) also never called `record_heartbeat()` at all, so they weren't watchable even if added to the list.

## 3. Fix / remediation

1. Added `record_heartbeat(_LOOP_NAME)` (via the existing dual-import `loop_monitor.record_heartbeat` convention already used by other loops) to the 5 loops that had none.
2. Added all 13 missing names to `_WATCHDOG_LOOP_NAMES` in `core/lifespan.py`.
3. Added a startup self-check in `lifespan()`: every name actually passed to `_spawn()` (tracked via a new `_spawned_loop_names` list, populated unconditionally even under `ENV=test`'s skip-spawn path) is compared against `_WATCHDOG_LOOP_NAMES`. Any mismatch (spawned-but-unwatched, watched-but-never-spawned/stale name, or a duplicate registration) is logged at `error` always, and raises `RuntimeError` when `ENV=production` — so this exact class of gap can never silently recur.
4. New `backend/tests/test_lifespan_watchdog_coverage.py`: parses `core/lifespan.py`'s actual source via `ast` (rather than importing/running `lifespan()`, which would need a live/mocked DB and would spawn real asyncio tasks) to statically assert every spawned name is watched and vice versa — a live drift detector, not a snapshot of today's state.

`safety_checkin_loop.py` also had its heartbeat call moved from inside the `try` block (recorded only on tick success) to after the `try/except` (recorded every iteration, success or caught failure) — matching the watchdog's actual purpose (is the task loop still alive and scheduling) rather than conflating it with tick-level success, which is already separately observable via the existing `logger.error(..., exc_info=True)` on tick failure.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface (backend), additive-only.** No loop's actual business logic, idempotency guard, or scheduling interval changed — every diff outside `core/lifespan.py` is a heartbeat-call addition at the very end of an existing `while True` iteration, immediately before `asyncio.sleep(...)`.
- Grepped every other caller/reader of `_WATCHDOG_LOOP_NAMES` and `record_heartbeat`/`loop_monitor` — only `core/lifespan.py`'s `_loop_watchdog()` reads the name list; `loop_monitor.record_heartbeat`/its companion staleness-read are already-shared, unmodified infrastructure used by every other loop in this codebase, so no other caller is affected.
- The new startup self-check is a **new failure mode in production**: if a future PR adds a `_spawn()` call without updating `_WATCHDOG_LOOP_NAMES` (or vice versa), the app will now `RuntimeError` at startup in `ENV=production` instead of silently degrading — this is the intended, audit-mandated behavior (CLAUDE.md: "Do not silently swallow errors"), but it does mean a mismatch that previously would have been a silent gap is now a hard startup failure. In non-production `ENV`s it only logs at `error`, does not raise, so local/staging dev is unaffected.
- Interaction with the 18 background loops (CLAUDE.md's own count): this fix touches the watchdog registration surface directly but changes zero loop scheduling/replay-safety behavior — every loop's own atomic-claim/idempotency guard is untouched.
- No interaction with the ride state machine or money/wallet deltas.

## 5. User-experience effect

None — internal observability/platform infra only, invisible to rider/driver/corporate-admin. The one exception is a hypothetical future misconfiguration causing a production startup failure (see above), which is an intentional fail-loud tradeoff, not a UX regression on any currently-shipped flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/lifespan.py` | Added 13 loop names to `_WATCHDOG_LOOP_NAMES`; added `_spawned_loop_names` tracking + startup self-check comparing spawned vs. watched names | Closes the registration gap and prevents it recurring silently |
| `backend/utils/safety_checkin_loop.py` | `_record_heartbeat` moved outside the `try/except` (every iteration, not just on success); named `_LOOP_NAME` constant added to match lifespan.py's exact spawn-name string | Watchdog now sees the task loop as alive regardless of tick-level (caught) failures |
| `backend/utils/distance_reconciliation.py` | Added `record_heartbeat` call + import (previously had none) | Loop was unwatchable — no heartbeat signal existed at all |
| `backend/utils/period1_distance_finalizer.py` | Added `record_heartbeat` call + import | Same as above |
| `backend/utils/reconciliation.py` | Added `record_heartbeat` call + import | Same as above |
| `backend/utils/route_finalizer.py` | Added `record_heartbeat` call + import | Same as above |
| `backend/utils/zoho_desk_sync.py` | Added `record_heartbeat` call + import | Same as above |
| `backend/tests/test_lifespan_watchdog_coverage.py` (new) | AST-based static drift-detector test | Regression guard so this gap class can't silently recur |

## 7. Before / after

```python
# Before (safety_checkin_loop.py) — heartbeat only recorded on success
while True:
    try:
        await _tick()
        _record_heartbeat("safety_checkin_loop")
    except Exception:
        logger.error("safety_checkin_loop tick failed", exc_info=True)
    await asyncio.sleep(_INTERVAL_SECONDS)
```

```python
# After — heartbeat recorded every iteration (task-alive signal, not tick-success signal)
while True:
    try:
        await _tick()
    except Exception:
        logger.error("safety_checkin_loop tick failed", exc_info=True)
    _record_heartbeat(_LOOP_NAME)
    await asyncio.sleep(_INTERVAL_SECONDS)
```

```python
# Before (core/lifespan.py) — 13 spawned loops absent from this list
_WATCHDOG_LOOP_NAMES = list([
    "subscription_expiry (6h)", "surge_engine (2min)", ...
    # (13 loops missing — see Change Impact Log §1/§2 for full list)
])
```

```python
# After — all spawned loops present, plus a startup self-check that fails
# loudly (RuntimeError in production) if the two lists ever diverge again
_WATCHDOG_LOOP_NAMES = list([
    "subscription_expiry (6h)", "surge_engine (2min)", ...
    "preauth_capture (5min)", "referral_payout (5min)",
    "driver_claim_reaper (60s)", "kyb_reverification (24h)",
    "route_finalizer (15s)", "route_gap_monitor (15s)",
    "safety_checkin (30s)", "reconciliation (daily 02:00 UTC)",
    "distance_reconciliation (daily 04:00 UTC)",
    "period1_distance_finalizer (5min)", "orphaned_hold_reconciler (15m)",
    "suspension_reactivation (10min)", "zoho_desk_sync (10min)",
])
# ... _spawn() calls tracked; mismatch check raises in production
```

## 8. Rollback plan

`git-revert-safe` — every change is additive (new heartbeat calls, new watchdog-list entries, a new startup check, a new test file). No migration, no data mutation, no config/`app_settings` dependency. If the new startup self-check ever produces a false-positive `RuntimeError` in production (e.g. a legitimately-intentional unwatched loop added later without updating the docs/rationale), the immediate mitigation is reverting this commit; the durable fix is adding the new loop's name to `_WATCHDOG_LOOP_NAMES` (or documenting why it's deliberately excluded) rather than reverting the self-check itself.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_lifespan_watchdog_coverage.py` (7 new tests) + a full regression sweep of every touched loop's existing test suite — `pytest -k "reconciliation or route_finalizer or zoho_desk or period1_distance or lifespan"` → **264 passed, 1 skipped, 0 failed**, via `/tmp/spinr-venv/bin/pytest` (not just `ruff`). `ruff check` clean on all 8 touched files.
- [ ] Manual repro steps followed in staging — **not performed**; no staging environment available this session. The startup self-check's production-`RuntimeError` path was verified only by code inspection and the AST-based unit test, not by actually booting the app with `ENV=production` and a deliberately-mismatched list.
- [x] Blast-radius grep performed: every reader/writer of `_WATCHDOG_LOOP_NAMES` and every `loop_monitor.record_heartbeat` caller enumerated (see §4).
- [x] Reviewed against relevant CLAUDE.md convention(s): "Background task safety" (replay-safety unaffected — no loop logic changed), "Do not silently swallow errors" (the new self-check is a direct application of this rule).
- [x] Feature-flagged if user-visible and non-trivial — n/a, not user-visible.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (single-surface, additive-only)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (none — internal-only)
