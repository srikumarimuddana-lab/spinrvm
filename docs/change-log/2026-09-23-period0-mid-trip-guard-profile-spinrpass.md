# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code (session_01L8WBp4c4HHd7Pq8iysPZuQ), for ittalenthire.ca@gmail.com |
| Surface(s) | backend |
| Domain (Sentry tag) | safety / drivers |
| PR / commit link | branch `fix/period0-mid-trip-guard-profile-spinrpass` (not yet pushed) |
| Related issue or gap ID | 2026-09-23 follow-up audit (`spinr-insurance-period-auditor`), commissioned after this morning's insurance-period close-gap fix (PR #5731) explicitly flagged these two sites as the *opposite*-direction risk it did not fix. |

## 1. Issue / gap identified

Two code paths force a driver `is_online=False` and write insurance-period **Period 0** ("personal auto only") with **no check** for whether the driver currently has an obligated ride or a pending offer:

- **`routes/drivers/profile.py::update_my_driver`** — editing any vehicle/document field (license plate, vehicle make/model, license/insurance/inspection expiry dates, etc.) while `status == "active"` unconditionally flips the driver offline and records Period 0, even if a passenger is in the car (`in_progress`) or the driver is en route/assigned.
- **`utils/spinr_pass.py::force_offline_if_exhausted`** — called right after a ride completes; if the driver's daily Spinr Pass ride quota is exhausted, it unconditionally forces them offline and records Period 0, with no check for whether dispatch has already re-claimed the same driver for a new ride/offer in the race window between "made available again" and this check running.

## 2. Root cause

Both sites were written as plain "flip offline + log Period 0" actions, the same class of bug this morning's PR (#5731) fixed at five *other* sites (admin suspend/ban/reject, document expiry, admin status override) — but that PR's own blast-radius grep found these two as look-alikes still unguarded, and explicitly scoped them out as follow-up rather than silently leaving them unremarked. `subscriptions.py`'s Spinr Pass sub-expiry loop already carries the correct guard (added under an earlier ticket, #4597) — the pattern was proven there but never propagated to these two siblings, the exact "fixed once, not in the sibling" failure class CLAUDE.md's 2026-09-12 audit calls out.

## 3. Fix / remediation

New shared helper `has_active_ride_obligation(driver_id) -> Optional[bool]` in `backend/utils/insurance_periods.py`: looks up whether the driver has an obligated ride (`driver_assigned`/`driver_accepted`/`driver_arrived`/`in_progress`) or a pending `ride_offers` row. Returns `True`/`False`, or `None` if the lookup itself failed — callers must treat `None` the same as `True` (defer), since guessing "safe to disrupt" is worse than deferring one tick. This does not replace `close_period_for_forced_offline` (which still needs the richer ride/offer *row*, not just a boolean, for its own classification and logging) — it's a lighter-weight check for callers that must gate the **entire** forced-offline action, not just the period write.

- **`profile.py`**: before flipping `is_online`/`is_available` to `False` on a vehicle/document edit, calls `has_active_ride_obligation`. If obligated (or lookup failed), `status = "needs_review"` still applies immediately (safe — it's account-facing, not coverage-facing), but the offline flip and the Period 0 write are both skipped. The ride-end release path (`close_period_after_release`, already shipped this morning) closes the period normally once the trip ends, exactly as it does for every other forced-offline case.
- **`spinr_pass.py`**: `force_offline_if_exhausted` now checks `has_active_ride_obligation` first thing, before any write. If obligated (or lookup failed), it returns `None` (as if not exhausted) and does nothing — no offline flip, no period write, no presence clear, no activity log. Enforcement is deferred to the driver's next ride completion, the same trade-off `subscriptions.py`'s loop already accepts (it defers, at a much coarser 6h cadence, rather than force offline mid-trip).

**Alternative considered:** refactor `close_period_for_forced_offline`'s internal ride/offer lookup into this new helper too, so there's exactly one implementation instead of two similar queries. Declined for this PR: `close_period_for_forced_offline` already shipped this morning with its own tests passing; touching its internals same-day for a cosmetic dedup risks regressing already-verified, tested code for no behavior change. Left as a documented follow-up in the new helper's own docstring instead — consistent with the surgical-changes principle (touch only what the task requires).

## 4. Risk & impact on existing functionality

- **New function, no existing behavior changed by its addition.** `has_active_ride_obligation` is net-new; nothing previously called it.
- **`profile.py` behavior change:** only when `changed_vehicle and driver.status == "active"` AND the driver has an obligated ride/offer (or the lookup fails). In that case only, `is_online`/`is_available` are no longer included in the `drivers` write, and `record_period_transition` is not called. `status = "needs_review"` is unaffected in either branch. Blast radius: grepped for other callers of `update_my_driver`'s helpers — none; this is the sole vehicle-edit endpoint.
- **`spinr_pass.py` behavior change:** only when the driver has been re-claimed for a new ride/offer (or the lookup fails) in the narrow window between their last ride's completion and this check running. In that case, quota enforcement for that specific ride-completion event is skipped entirely (offline flip, period write, presence clear, and activity log all skipped) and re-evaluated on their next completion. Blast radius: `force_offline_if_exhausted` has 2 callers — `routes/drivers/ride_complete.py` and `routes/rides/lifecycle.py` (rider-side complete) — both already tolerate a `None` return (their existing contract: "returns the quota status dict when it forced the driver offline... else `None`", already the not-exhausted case), so no caller needed a change.
- **Tables read:** `has_active_ride_obligation` adds up to 2 more indexed reads (`rides`, `ride_offers`) at both call sites — negligible: a driver-initiated profile edit and a once-per-ride-completion quota check, neither an SLA-timed hot path.
- **Ride state machine / money / wallet:** untouched. No `rides.status` write, no Stripe call, no wallet delta.
- **Not a race-free fix:** like `close_period_for_forced_offline` and the existing `subscriptions.py`/`users.py` guards, the obligation check and the subsequent write are two separate steps, not one DB transaction. A dispatch claim landing in that exact gap is the same class of narrow, already-accepted residual risk documented for those existing guards.

## 5. User-experience effect

- **Drivers:** a driver mid-trip who edits a vehicle/document field is still flagged `needs_review` immediately (unchanged UX for that part), but is no longer force-logged-offline or shown as needing to re-enable Go Online until their trip actually ends — this removes a disruption that could previously happen mid-ride. A driver whose Spinr Pass quota is exhausted right as they're handed a new ride keeps that ride instead of being force-offline mid-handoff; enforcement catches up on their next completion.
- Not visible to riders or corporate. No copy/notification change — `notify_driver_status_change` in `profile.py` still fires exactly when it did before (only the offline-flip and period-write inputs changed, not the notification call site or its trigger condition).
- No admin-dashboard change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/insurance_periods.py` | New `has_active_ride_obligation(driver_id)` helper | Shared obligation check for callers that must skip the whole forced-offline action, not just the period write |
| `backend/routes/drivers/_deps.py` | Re-export `has_active_ride_obligation` (both dual-import branches) | Drivers package reaches the helper via `_deps`, matching existing convention |
| `backend/routes/drivers/profile.py` | `update_my_driver`'s vehicle-edit path checks the obligation before flipping offline / writing Period 0 | REAL RISK #1 from the follow-up audit |
| `backend/utils/spinr_pass.py` | `force_offline_if_exhausted` checks the obligation first and defers entirely if held (or lookup fails) | REAL RISK #2 from the follow-up audit |
| `backend/tests/test_insurance_period_close_helpers.py` | New `TestHasActiveRideObligation` (4 tests) | Unit coverage for the new helper |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | Existing needs_review test updated to mock the new check; 2 new tests (obligated defers, lookup-failure defers) | Regression coverage; old-code-would-fail confirmed by test design |
| `backend/tests/test_spinr_pass_quota.py` | 2 new tests (reclaimed-driver defers, lookup-failure defers) | Regression coverage |

Incidental, forced by the pre-commit hook's whole-file lint (not a scope expansion of the fix itself): `routes/drivers/profile.py:197` (unused loop variable `label`→`_label`) and `tests/test_drivers_shared_status_profile_coverage.py` (removed an unused `now_iso` in an unrelated test) — both pre-existing on `origin/main`, one-line, no logic change.

## 7. Before / after

```python
# Before — routes/drivers/profile.py
if changed_vehicle and driver.get("status") == "active":
    updates["status"] = "needs_review"
    updates["is_online"] = False
    updates["is_available"] = False
...
if changed_vehicle and driver.get("status") == "active" and driver.get("is_online"):
    await _deps.record_period_transition(driver["id"], 0)
```

```python
# After
if changed_vehicle and driver.get("status") == "active":
    updates["status"] = "needs_review"
    _obligated = await _deps.has_active_ride_obligation(driver["id"])
    if _obligated is False:
        updates["is_online"] = False
        updates["is_available"] = False
        _forced_offline_for_review = True
    # else: obligated (or lookup failed) — defer the offline/period change
...
if changed_vehicle and driver.get("status") == "active" and driver.get("is_online") and _forced_offline_for_review:
    await _deps.record_period_transition(driver["id"], 0)
```

```python
# Before — utils/spinr_pass.py::force_offline_if_exhausted
status = await quota_status(driver_id, ...)
if not status or not status.get("exhausted"):
    return None
# ... unconditionally flips offline, writes Period 0
```

```python
# After
status = await quota_status(driver_id, ...)
if not status or not status.get("exhausted"):
    return None
if await has_active_ride_obligation(driver_id) is not False:
    return None  # defer to next completion
# ... only then flips offline, writes Period 0
```

**Concrete before/after scenario (gate #4), exercised in the new tests:**
- **Before:** a driver mid-trip (`in_progress`) edits their vehicle color in the app. The request forces them offline and writes a Period 0 row while the passenger is still in the car.
- **After:** the same edit still sets `status = "needs_review"`, but the driver stays online/available and no period row is written; the trip continues normally, and completing it closes Period 3 → Period 0 as usual (via the already-shipped `close_period_after_release`).

## 8. Rollback plan

`git-revert-safe`. Pure code change: no migration, no schema change, no data mutation. Rows this change *prevents* writing were the incorrect ones; a revert simply restores the prior (unguarded) behavior at both sites. No feature flag — this corrects a real, unconditional mid-trip misclassification with no plausible reason to want the old behavior back; a flag would only preserve the option to re-introduce a known regulatory defect.

## 9. Verification performed

- [x] Automated tests run: `test_insurance_period_close_helpers.py`, `test_insurance_period_forced_offline_sites.py`, `test_insurance_period_release_sites.py`, `test_drivers_shared_status_profile_coverage.py`, `test_spinr_pass_quota.py` — 192 passed. Broader blast-radius sweep: `test_document_expiry.py`, `test_admin_rides_cancel_state.py`, `test_c2_driver_cancel_atomic.py`, `test_ride_complete_coverage.py`, `test_ride_accept_flow.py` — 98 passed. `test_loguru_call_conventions.py` — 8 passed (both touched files use stdlib `logging`, not loguru, confirmed).
- [x] `ruff check` / `ruff format --check` clean on every touched non-test and test file. Two pre-existing, unrelated lint findings (an unused loop variable `label`→`_label` in `profile.py:197`, an unused `now_iso` in the coverage test file) were confirmed present on unmodified `origin/main`; the repo's pre-commit hook lints the whole staged file rather than just the diff, so both had to be fixed (one-line, no logic change) to get a commit through rather than bypassing the hook with `--no-verify`.
- [x] Blast-radius grep performed: `force_offline_if_exhausted` callers (2, both already tolerate `None`); `update_my_driver`'s only caller is the `PUT /drivers/me` route itself; confirmed `has_active_ride_obligation` had zero existing callers before this change.
- [x] Reviewed against CLAUDE.md conventions: dual-import pattern in every new import; "derive period from ride state, not the driver UI" (the whole point of the fix); no swallowed DB errors (`has_active_ride_obligation` logs at ERROR with `exc_info=True` on lookup failure, and callers explicitly treat the resulting `None` as "defer," never silently proceeding as if nothing were wrong).
- [ ] Manual repro steps followed in staging — not done (no staging access from this session).
- [ ] Feature-flagged — not flagged; see §8 for why.

**Adversarial review (gate #10).** Alternative considered and declined: refactoring `close_period_for_forced_offline`'s internal lookup to share code with the new helper (see §3) — deferred as a documented follow-up rather than risking same-day regression of already-shipped, tested code. `/code-review` was not re-run as a separate step for this smaller, two-site fix; the diff was self-reviewed against the exact same checklist the originating `spinr-insurance-period-auditor` audit used (fail-safe direction of `None`, no re-assertion race, ride/offer obligation scoped identically to `close_period_for_forced_offline`'s own `_OBLIGATED_RIDE_STATUSES`).

**What was NOT verified:**
- Not tested against a real Postgres/Supabase; both new tests and the affected existing tests use `mock_supabase_client`/hand-rolled fakes only.
- The race window itself (dispatch claiming a driver in the exact gap between the obligation check and the forced-offline write) was not exercised under real concurrency — the fix narrows an existing, already-accepted class of narrow race (same as `users.py`'s `_assert_deletable`/`_tombstone_driver_row` two-step, noted in the originating audit) rather than eliminating it with a transaction.
- No production build applies (backend-only; no admin-dashboard/rider-app/driver-app change).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data to clean up)
- [x] Blast radius is stated, not assumed (both call sites' only callers grepped and confirmed)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 states the — narrow, safety-positive — visible effect explicitly)
