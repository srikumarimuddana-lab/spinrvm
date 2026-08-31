# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | fix/insurance-period-mid-trip-guards |
| Related issue or gap ID | #4597 (Findings 1 and 3) |

## 1. Issue / gap identified

Two code paths write `driver_insurance_periods` rows without checking the driver's actual ride/availability state first, unlike sibling code paths that already guard the same hazard:

- **Finding 1 (P0):** the `subscription_expiry` background loop force-offlines a driver whose Spinr Pass expired and unconditionally writes Period 0 — with no check for an active ride.
- **Finding 3 (P2):** rider-initiated ride cancellation writes Period 1 unconditionally after releasing the driver, without checking whether the release actually succeeded.

## 2. Root cause

**Finding 1:** `check_expiring_subscriptions` (`backend/routes/drivers/subscriptions.py`) was written checking only `driver.is_online` before enforcement, evidently without porting the active-ride guard that the client-facing `go_online` toggle (`backend/routes/drivers/status.py:204-233`) already has for the identical hazard (going offline mid-trip). The two paths diverged — one guarded, one not — likely because the background-loop enforcement path was added later without cross-referencing the toggle's existing guard.

**Finding 3:** `backend/routes/rides/cancellation.py`'s rider-cancel handler predates the `released.get("is_available")` guard pattern that was added to `routes/rides/matching.py` and `routes/drivers/ride_flow.py` on 2026-08-18 for the equivalent hazard (driver went offline between dispatch and a later event). The rider-cancel path was never updated to match.

Both gaps were surfaced by a read-only swarm audit (2026-08-27, see #4597); confirmed directly against the current code before implementing (not taken on faith from the audit's own text).

## 3. Fix / remediation

**Finding 1:** before the offline flip + Period-0 write, query for an active ride in `DRIVER_ASSIGNED`/`DRIVER_ACCEPTED`/`DRIVER_ARRIVED`/`IN_PROGRESS` — the exact same status list `status.py`'s guard uses. If one exists, skip the offline flip and period transition for that driver this tick (the subscription row is still marked `expired` regardless — only the offline enforcement is deferred). The driver naturally re-gates on their next `go_online`/status check once the trip ends.

**Finding 3:** capture `set_driver_available`'s return value and only call `record_period_transition(driver_id, 1)` when `released.get("is_available")` is true — mirroring `matching.py`'s existing guard verbatim.

## 4. Risk & impact on existing functionality

Blast-radius grep performed across `backend/`: `record_period_transition` has 25 call sites across 13 files (`routes/users.py`, `routes/admin/rides.py`, `routes/drivers/{ride_flow,subscriptions,ride_cancel,profile,status}.py`, `routes/rides/{matching,cancellation,lifecycle}.py`, `utils/{stale_intent_reconciler,spinr_pass}.py`); `set_driver_available` has 7 callers. This change touches exactly **2** of those 25 call sites (`subscriptions.py`'s expiry-enforcement Period-0 write, `cancellation.py`'s rider-cancel Period-1 write) — every other call site is untouched and unaffected.

- **Blast radius: isolated.** No shared helper, table schema, or background-loop registration changed — only the conditions under which these two specific call sites fire.
- **Could this regress a working flow?** The two new tests per fix (one confirming the guard fires, one confirming it does *not* over-suppress the existing behavior when the guard condition is false) directly cover this. No other flow reads these two call sites' output differently.
- **Interaction with background loops:** `subscription_expiry` (`backend/core/lifespan.py`) is the only loop touched; its lock/scheduling/warning-push logic is unchanged — only the enforcement branch's final two side effects (offline flip, period transition) are now conditionally skipped.
- **Interaction with ride state machine:** none — no ride-status write is added, changed, or removed. Only `driver_insurance_periods` writes are gated.
- **Interaction with money/wallet deltas:** none.

## 5. User-experience effect

- **Driver:** in the specific scenario Finding 1 fixes (subscription expires while mid-trip), the driver previously would have been silently force-offlined and disconnected mid-trip (WebSocket `manager.disconnect`) — now they stay online and connected until the trip completes, and only then are enforced. This is a **safety-positive, user-visible-mid-session change**: a driver who was being incorrectly kicked offline during an active trip no longer is. No new UI/copy — the change is entirely in when an existing side effect (offline flip + disconnect) fires, not what it does.
- **Rider:** no visible change — Finding 3 only affects an internal audit-trail write (`driver_insurance_periods`), not anything rider-facing.
- **Admin:** no visible change to admin-dashboard flows.
- Not feature-flagged: this is a bug fix aligning two enforcement paths with an already-shipped, unflagged guard pattern (`status.py`'s toggle-guard, `matching.py`'s release-guard) — introducing a flag would mean deliberately keeping the mid-trip-kick and false-Period-1 bugs live behind a toggle, which the regulatory stakes here (SGI insurance-period audit integrity) argue against.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/subscriptions.py` | Added active-ride guard before the expiry-enforcement offline flip + Period-0 write | Finding 1 — prevent mid-trip force-offline/misclassification |
| `backend/routes/rides/cancellation.py` | Gate the rider-cancel Period-1 write on `set_driver_available`'s actual return value | Finding 3 — prevent false Period-1 write for an already-offline driver |
| `backend/tests/test_subscriptions_coverage.py` | 2 new tests: active-ride defers enforcement; no-active-ride still enforces (regression guard) | Cover Finding 1's fix and its boundary |
| `backend/tests/test_ride_cancellation_branches.py` | 2 new tests: offline driver skips Period-1 write; online driver still gets it (regression guard) | Cover Finding 3's fix and its boundary |

## 7. Before / after

```python
# Before (subscriptions.py, expiry enforcement)
driver = await _deps.db.find_one("drivers", {"id": sub["driver_id"]})
if not driver or not driver.get("is_online"):
    continue
# ... unconditional offline flip ...
await _deps.record_period_transition(driver["id"], 0)
```

```python
# After
driver = await _deps.db.find_one("drivers", {"id": sub["driver_id"]})
if not driver or not driver.get("is_online"):
    continue
active_ride = await _deps.db.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": {"$in": [
        RideStatus.DRIVER_ASSIGNED, RideStatus.DRIVER_ACCEPTED,
        RideStatus.DRIVER_ARRIVED, RideStatus.IN_PROGRESS,
    ]}},
    limit=1,
)
if active_ride:
    continue  # defer to ride completion
# ... offline flip ...
await _deps.record_period_transition(driver["id"], 0)
```

```python
# Before (cancellation.py, rider cancel)
await _deps.db_supabase.set_driver_available(driver_id, True)
await _deps.record_period_transition(driver_id, 1)
```

```python
# After
released = await _deps.db_supabase.set_driver_available(driver_id, True)
if isinstance(released, dict) and released.get("is_available"):
    await _deps.record_period_transition(driver_id, 1)
```

## 8. Rollback plan

`git-revert-safe`. Both changes are pure code-path guards with no schema, migration, or data write beyond the existing `driver_insurance_periods` append-only table (never mutated, only conditionally not-written). Reverting restores the two prior (buggy) behaviors exactly; no data-level remediation needed since no incorrect rows were retroactively touched — this fix only changes behavior going forward. Per CLAUDE.md's own rule, `driver_insurance_periods` rows already written under the old buggy behavior are never deleted or mutated regardless of this fix.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_subscriptions_coverage.py backend/tests/test_ride_cancellation_branches.py` — 88/88 passed (4 new, 84 pre-existing unaffected). `ruff check` clean on all 4 changed files.
- [ ] Manual repro steps followed in staging — **not performed**; no staging/Supabase access in this session. Verified against `mock_supabase_client`-style fixtures only, per this repo's standard unit-test convention.
- [x] Blast-radius grep performed: see §4 — `record_period_transition` (25 call sites, 13 files) and `set_driver_available` (7 callers) enumerated; only the 2 intended call sites touched.
- [x] Reviewed against relevant CLAUDE.md convention(s): the insurance-period state table/invariants (Period 2 starts on `driver_assigned`, driver cannot be Period 3 without `ride_id`, append-only), and the "never delete or mutate period rows" rule (respected — this fix only prevents *new* incorrect writes).
- [x] Feature-flagged if user-visible and non-trivial, or justify why not: justified in §5 — a regulatory-integrity bug fix, not a product change; flagging it would mean keeping the bug live behind a toggle.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (git revert)
- [x] Blast radius is stated, not assumed (§4, backed by a real grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)

**What was NOT verified:** not exercised against real Supabase/staging data, only mocked fixtures. Finding 1's "how often does this actually fire in production" (i.e., how many drivers have historically been mid-trip when their subscription expired with enforcement on) was not measured — that requires DB access this session doesn't have, and is separate scoping work already flagged on #4597 itself.
