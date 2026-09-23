# Change Impact & Risk Log — insurance-period close gaps (forced offline + unconditional Period 1)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code (agent session), for review by the orchestrating session |
| Surface(s) | backend |
| Domain (Sentry tag) | safety / drivers / rides / admin |
| PR / commit link | Branch `fix/insurance-period-close-gaps` (not yet pushed; the orchestrating session opens the PR) |
| Related issue or gap ID | 2026-09-22 `spinr-insurance-period-auditor` fleet audit, BLOCKER #1 and HIGH #2 |

## 1. Issue / gap identified

**BLOCKER #1.** Five code paths force a driver to `is_online=False` and never write an insurance-period row:
- the document-expiry sweep (`utils/document_expiry.py`)
- the document-expiry suspension on the ride-accept path (`routes/drivers/_shared.py`)
- admin suspend/ban/reject (`routes/admin/drivers.py::admin_driver_action`)
- admin status override (`admin_override_driver_status`)

The driver's open period row is left dangling. An idle driver's open Period 1 therefore claims TNC contingent cover over personal-auto time, with no end date.

**HIGH #2.** Ride-end paths recorded **Period 1 unconditionally** after releasing the driver: driver `cancel_ride` and driver `complete_ride`, plus the sibling rider-side `complete_ride` in `routes/rides/lifecycle.py` that the adversarial review found. They ignored the clamped row `set_driver_available` returns, so a driver who had been forced offline mid-ride got a Period 1 row where Period 0 is correct. Three more siblings had the opposite defect: `mark_rider_noshow`, admin cancel, and admin force-complete. They skip Period 1 for an offline driver but write nothing, which leaves the open Period 2/3 (primary commercial cover) open forever.

Source: 2026-09-22 proactive `spinr-insurance-period-auditor` audit, confirmed by reading the current code. The adversarial `/code-review` pass on this diff found the extra sibling sites.

## 2. Root cause

- Each forced-offline writer was built as a plain `drivers` row update. None of them treated going offline as a period boundary. The driver's own Go Offline (`routes/drivers/status.py`) does treat it that way, but those writers never reused it.
- `insurance_period_reconciler.py` cannot self-heal these cases. Its idle scan (`_online_idle_candidates`) only reads `is_online = True` drivers, so a driver forced offline drops out of every candidate set on the next tick.
- The ride-end paths hand-copied the release-then-record pattern. It had already been consolidated into `release_driver_and_close_period` for five other sites (2026-09-20). But `complete_ride` couldn't call that helper, because it increments `total_rides` in the same release write. The other sites were never migrated. Some kept "always write 1"; others kept "write 1 only if available, otherwise nothing". Both are wrong for an offline driver.
- Admin cancel/complete carried a comment that "their go-offline already logged Period 0". That was never true for a driver taken offline by suspension or document expiry.

## 3. Fix / remediation

Two helpers in `backend/utils/insurance_periods.py`. They reuse the existing single source of truth (`derive_insurance_period`) instead of new classification logic.

1. **`close_period_after_release(driver_id, released_row, *, reason, ride_id)`** is the 0-vs-1 close split out of `release_driver_and_close_period`, which now delegates to it (behavior unchanged). It is for callers that issue their own `set_driver_available` write:
   - An online row closes to Period 1; an offline row closes to Period 0.
   - An unreadable row writes nothing. This matches the existing contract: a guessed row in an append-only regulatory log is worse than a missing one.
   - Six ride-end sites now use it: driver `cancel_ride`, driver `complete_ride`, `mark_rider_noshow`, rider `complete_ride`, admin cancel, and admin force-complete.
2. **`close_period_for_forced_offline(driver_id, *, reason)`** is called after the offline write succeeds at all five forced-offline writers. It looks up the driver's obligated ride (`driver_assigned`/`driver_accepted`/`driver_arrived`/`in_progress`) and any pending `ride_offers` row, then derives the period with `is_online=False`:
   - **No ride and no offer → record Period 0.** This closes the stale Period 1.
   - **Ride `in_progress` → leave the open Period 3 alone (no write).**
   - **Ride en route, or a pending offer → leave the open Period 2 alone (no write).**
   - **Lookup error → write nothing.** It logs at ERROR, increments `spinr_insurance_period_forced_offline_skipped_total`, and never raises into the suspension/admin flow.

### Decision: how is a forced-offline driver's period classified? (explicit, per the task brief)

The brief asked whether a blanket "close whatever's open as Period 0" is correct, given the driver is being suspended regardless of ride state. **I decided it is not.** I classify from ride state instead:

- **Suspension changes the account, not the physical situation.** A suspend/ban/document-expiry write does not cancel or end the ride: `rides.status` stays `in_progress`, and the passenger is still in the car. CLAUDE.md says to "derive period from ride state, not from the driver UI". A blanket Period 0 would close a live Period 3 and assert *personal-auto-only* cover while a passenger is aboard. That is the most harmful misclassification possible: it understates cover exactly when a claim is most likely. It would also contradict the reconciler, whose `_in_progress_candidates` scan expects Period 3 for that driver.
- **Why no write at all for 2/3, rather than re-asserting it.** The row opened at claim or trip start is already the right one. The first version of this fix re-asserted it, and the adversarial review found a real race in that: if `complete_ride` closed Period 3 between our ride lookup and our write, re-asserting Period 3 would re-open primary cover on a finished trip. Writing nothing removes the race. It also makes the unordered `limit=1` ride pick irrelevant, since we only need to know whether *any* obligation exists.
- **Who closes the kept 2/3.** Every ride-end path now closes it from the clamped, now-offline driver row, so it becomes Period 0. That covers:
  - driver complete, cancel and no-show
  - rider complete
  - admin cancel and force-complete
  - rider cancel, decline and offer expiry, which already went through `release_driver_and_close_period` or the batch-offer release RPC

  This is why HIGH #2's sibling sites had to be fixed in the same change. Without them, keeping 2/3 open would leave those rows open forever.
- **A pending offer counts as live** even if it may be stale. This is the same conservative rule as the reconciler's `_pending_offer_candidates`. A stale offer is expired by the claim reaper and closed by the release path; it is never downgraded here.

## 4. Risk & impact on existing functionality

- **Shared table:** `driver_insurance_periods` is written only through the existing `record_insurance_period_transition` RPC via `record_period_transition`. The write mechanism is unchanged, as is append-only behavior (the RPC closes `ended_at` on the open row and inserts a new one; migration 64's trigger is untouched). No schema or migration change.
- **Shared helpers changed:**
  - `release_driver_and_close_period` now delegates its second half to `close_period_after_release`, with identical behavior. It has five existing callers (`ride_flow.py` ×2, `matching.py` ×2, `cancellation.py`), and its test file `test_insurance_release_helper.py` passes unchanged.
  - `RELEASE_REASONS` gained six labels. They are label-only: they reach the log and the metric and nothing else.
- **Call sites whose behavior changed:**

  | Site | Before | After |
  |---|---|---|
  | `routes/drivers/ride_cancel.py::cancel_ride` | unconditional P1 | derived 0/1 |
  | `routes/drivers/ride_cancel.py::mark_rider_noshow` | P1 or nothing | derived 0/1 |
  | `routes/drivers/ride_complete.py::complete_ride` | unconditional P1 | derived 0/1 |
  | `routes/rides/lifecycle.py::rider_complete_ride` | unconditional P1 | derived 0/1 |
  | `routes/admin/rides.py::admin_cancel_ride` | P1 or nothing | derived 0/1 |
  | `routes/admin/rides.py::admin_complete_ride` | P1 or nothing | derived 0/1 |
  | `utils/document_expiry.py::check_expiring_documents` | nothing | P0 or keep 2/3 |
  | `routes/drivers/_shared.py::_suspend_driver_for_expired_documents` | nothing | P0 or keep 2/3 |
  | `routes/admin/drivers.py::admin_driver_action` (reject/suspend/ban) | nothing | P0 or keep 2/3 |
  | `routes/admin/drivers.py::admin_override_driver_status` (any non-active) | nothing | P0 or keep 2/3 |

- **What can regress:**
  - (a) An online driver whose released row somehow comes back without `is_online` or `is_available` would now close to Period 0 instead of 1. `set_driver_available` always selects `is_online`, and every current test and prod path returns the full row, so I consider this low risk.
  - (b) A `set_driver_available` that returns `None` (no Supabase client, or no row matched) now writes **nothing** at the six ride-end sites, where before it wrote Period 1. The open 2/3 then stays open until `stale_p3_closer` / `stuck_ride_sweeper` or the reconciler touches it. This is the helper's existing, documented contract, and a guessed row is worse than a missing one.
  - (c) Admin suspend/ban/reject and document-expiry suspension now make one or two more indexed reads (`rides` by `driver_id` + status, and `ride_offers` by `driver_id` + status), plus possibly one RPC write. That's negligible latency on an admin action or a 12-hour sweep.
- **Background loops:** the `document_expiry` loop gains the call inside its existing atomic suspension claim, so a lost claim writes nothing, and the helper never raises into the loop. `insurance_period_reconciler` and `stale_p3_closer` are unchanged and still agree with the new behavior: for a suspended in-progress driver the reconciler expects Period 3, which is what we now keep.
- **Ride state machine:** no `rides.status` transition changed. Money/wallet: not touched.
- **Blast radius:** single-surface (backend), cross-module. Two route packages (`drivers`, `rides`), the admin routes, one background loop, and the shared helper module.
- **Known forks:** none of the touched files appear in `docs/known-forks.md` (grep returned no match).

## 5. User-experience effect

- **Nobody sees a UI change.** Backend audit rows only: no response shape, notification, copy or status code changed.
- The one behavior difference is in `driver_insurance_periods` rows, which are regulator/insurer-facing, not app-facing. A driver suspended mid-trip keeps Period 3 until the trip ends, then gets Period 0 instead of Period 1. An idle driver suspended while online now gets their Period 1 closed immediately.
- Not visible mid-session to riders or drivers. Internal admins who read period rows (period reports, reconciler dashboards) will see fewer never-closed Period 1/2/3 rows going forward.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/insurance_periods.py` | Added `close_period_after_release` (split from `release_driver_and_close_period`, which now delegates), `close_period_for_forced_offline`, `FORCED_OFFLINE_REASONS`; six new `RELEASE_REASONS` labels | One shared derivation for every release/forced-offline site |
| `backend/routes/drivers/_deps.py` | Re-export both new helpers | Drivers package reaches helpers via `_deps` |
| `backend/routes/drivers/ride_cancel.py` | `cancel_ride` and `mark_rider_noshow` close via `close_period_after_release` | HIGH #2 unconditional P1 / no-close for offline driver |
| `backend/routes/drivers/ride_complete.py` | `complete_ride` closes via `close_period_after_release` (keeps `total_rides_inc=1` in the same release write) | HIGH #2 |
| `backend/routes/drivers/_shared.py` | `_suspend_driver_for_expired_documents` calls the forced-offline close when its suspension claim wins | BLOCKER #1 (accept-path twin of the sweep) |
| `backend/routes/rides/_deps.py` | Re-export `close_period_after_release` | Rides package reaches helpers via `_deps` |
| `backend/routes/rides/lifecycle.py` | rider-side `complete_ride` closes via `close_period_after_release` | HIGH #2 sibling (found by review) |
| `backend/routes/admin/rides.py` | admin cancel / force-complete close via `close_period_after_release` | Sibling: offline driver's 2/3 was never closed |
| `backend/routes/admin/drivers.py` | reject/suspend/ban and non-active status override call the forced-offline close after the write | BLOCKER #1 |
| `backend/utils/document_expiry.py` | Suspension claim calls the forced-offline close | BLOCKER #1 |
| `backend/tests/test_insurance_period_close_helpers.py` | New: helper unit tests | Derivation coverage |
| `backend/tests/test_insurance_period_release_sites.py` | New: driver cancel + rider complete regression tests | HIGH #2 |
| `backend/tests/test_insurance_period_forced_offline_sites.py` | New: document-expiry sweep, accept-path suspension, admin action/override wiring tests | BLOCKER #1 |
| `backend/tests/test_ride_complete_coverage.py` | New `TestCompletionInsurancePeriodClose` | HIGH #2 |
| `backend/tests/test_c2_driver_cancel_atomic.py` | No-show offline case now expects Period 0 (was "no write"); both no-show tests also capture the helper's call | Intended behavior change |
| `backend/tests/test_ride_accept_flow.py` | The admin-cancel test also patches the helper's `record_period_transition` binding | Patch target moved; asserted behavior (online → Period 1) unchanged |
| `backend/tests/test_admin_rides_cancel_state.py` | Admin cancel/complete offline cases now expect Period 0 (was "no write"), renamed accordingly; patch the helper's record binding too | Intended behavior change |

## 7. Before / after

```
# Before — routes/drivers/ride_complete.py (same shape in ride_cancel.cancel_ride, rides/lifecycle.py)
await db_supabase.set_driver_available(driver["id"], available=True, total_rides_inc=1)
await _deps.record_period_transition(driver["id"], 1)          # always Period 1
```

```
# After
_complete_released = await db_supabase.set_driver_available(driver["id"], available=True, total_rides_inc=1)
await _deps.close_period_after_release(driver["id"], _complete_released, reason="ride_completed", ride_id=ride_id)
# → Period 1 if the row is online, Period 0 if clamped offline, nothing if no row
```

```
# Before — routes/admin/drivers.py admin_driver_action (suspend)
updates["is_online"] = False; updates["is_available"] = False
await db_supabase.update_one("drivers", {"id": driver_id}, updates)
# (no period write — open Period 1 dangles forever)
```

```
# After
await db_supabase.update_one("drivers", {"id": driver_id}, updates)
if updates.get("is_online") is False:
    await close_period_for_forced_offline(driver_id, reason=f"admin_{req.action}")
# → Period 0 if no ride/offer; open Period 2/3 left untouched otherwise
```

Concrete dry-run scenarios (gate #4), all exercised against mocked Supabase in the tests listed in §9:

| Scenario | Before | After |
|---|---|---|
| Idle online driver (open P1) suspended by admin | P1 stays open forever | P1 closed, P0 opened |
| Driver mid-trip (open P3) has licence expire; trip later completed by driver | Expiry: nothing. Completion: P3 → **P1** (driver is offline) | Expiry: P3 kept. Completion: P3 → **P0** |
| Driver `driver_arrived` (open P2) banned; driver marks rider no-show | P2 stays open forever (no write for offline driver) | P2 → P0 |
| Driver `driver_assigned` (open P2) suspended; admin cancels the ride | P2 stays open forever | P2 → P0 |
| Online driver completes a trip normally | P3 → P1 | P3 → P1 (unchanged) |

## 8. Rollback plan

- **No feature flag.** This is backend-only, audit-row-only, and not user-visible, so gate #3 does not require one. A flag that restores the old behavior would restore a known regulatory defect: open Period 1 over personal-auto time, and a false Period 1 for an offline driver.
- **Code:** a `git revert` of the commits restores the previous behavior at every call site. There is no schema, migration or config change to undo.
- **Data (append-only, so a revert does not undo rows already written):**
  - The rows this change writes are Period 0 closes, plus Period 1 rows only for online drivers, which matches prior behavior. If one of them turns out wrong, the sanctioned remedy is an append-only correction in `driver_insurance_period_corrections` (migration 355), which references the original row without mutating it.
  - To identify affected rows: filter the new log lines (`insurance_periods: driver forced offline ...`, `forced-offline ride/offer lookup FAILED`) and the metrics `spinr_insurance_period_forced_offline_total{reason,period}` / `spinr_insurance_period_release_total{reason=driver_cancelled|rider_noshow|ride_completed|rider_completed|admin_cancelled|admin_completed}` over the deploy window, then join to `driver_insurance_periods` by `driver_id` and time.
- **Backfill:** rows left dangling *before* this deploy are not touched by this change. Closing historical dangling Period 1/2/3 rows for currently-offline drivers is a separate, manual data remediation (see "What was NOT verified").

## 9. Verification performed

- [x] Automated tests run (list which — unit / integration / e2e)
  - New unit/route tests: `test_insurance_period_close_helpers.py`, `test_insurance_period_release_sites.py`, `test_insurance_period_forced_offline_sites.py`, `test_ride_complete_coverage.py::TestCompletionInsurancePeriodClose`. Updated: `test_c2_driver_cancel_atomic.py`, `test_admin_rides_cancel_state.py`.
  - **Old-code check:** with the four original call-site files restored from `origin/main` (new helper kept), every "bug" test failed. That included the offline cancel and complete cases (old code recorded Period 1), the document-expiry cases and all admin cases. The online-driver guard tests passed on both old and new code, as intended.
  - **Targeted run.** The affected suites passed: 1,397 tests covering insurance periods, the reconciler, cancel/complete/no-show, rides lifecycle and coverage, document expiry, admin drivers/rides, driver status, e2e cancellation, the state machine, spinr-pass and the loguru conventions. They use `mock_supabase_client`-style mocks. The one failure is `test_drivers.py::TestDriverLocation::test_update_driver_location`, which also fails on unmodified `origin/main`.
  - **Full non-slow backend suite** (`pytest -m "not slow" --ignore=tests/rls`): 15,738 passed, 23 failed, 92 skipped. Every one of the 23 is independent of this diff:
    - **13 fail identically with this diff's source files swapped back to `origin/main`:**
      - `test_admin_secdef_fn_revokes` (migration SQL guard)
      - `test_coverage_boost::TestGetRedisDirect`
      - the `update_driver_location` tests in `test_db.py` and `test_drivers.py`
      - `test_idle_location_batch`
      - `test_logout_all::test_takes_idle_driver_offline`
      - `test_no_raw_exception_in_4xx_detail`
      - 3 × `test_p1_multi_stop`
      - 3 × `test_period1_accumulation_endpoint`
    - **The other 10 pass in isolation both with and without this diff:** 4 × `test_redis_client_coverage` and 6 × `test_verify_otp_login_flow`. They only fail under full-suite ordering (Redis/auth module state pollution), and neither module is touched here.
    - **Not raised as a `[CR]` from this session**, because these failures are unrelated to this diff. The orchestrating session should decide whether they warrant one under CLAUDE.md gate #8.
  - `ruff check` and `ruff format --check` are clean on every touched file.
  - **Two unrelated formatting-only hunks are included, and this is disclosed deliberately.** `origin/main` already had ruff format drift in `routes/admin/drivers.py` around line 3266 (a generator expression in the payout summary, collapsed onto one line) and in `routes/drivers/ride_complete.py` around line 401 (the `progress_already_started` expression). I first reverted them under the surgical-change rule, but the repo's pre-commit gate (check 7/11) blocks any commit whose staged backend files are not ruff-formatted. Using `--no-verify` is emergency-only, so I accepted the formatter's output. Both hunks are whitespace/line-joining only: the AST is unchanged and there is no behavior change.
- [ ] Manual repro steps followed in staging. **Not done**; no staging access from this session.
- [x] Blast-radius grep performed (list what was searched)
  - `record_period_transition|release_driver_and_close_period|set_driver_available` across `backend/`: every period writer and release caller.
  - `"is_online": False` / `is_online"] = False` across `routes/ utils/ services/`: every forced-offline writer.
  - Result: the five forced-offline writers fixed here. The ones that already write a (blanket) Period 0 are listed as follow-ups: `auth.py` logout, `users.py` delete, `profile.py` needs_review, `subscriptions.py` expiry, `stale_intent_reconciler.py`, `spinr_pass.py`. Import-time writers in `driver_import_service.py` create new drivers with no period.
  - `docs/known-forks.md`: no touched file is listed.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) (state machine / money / RLS / PIPEDA / observability)
  - Insurance-period table: derived from ride state; Period 3 is never written without `ride_id` (the helper never writes 2/3); append-only.
  - Dual-import pattern preserved in every new import.
  - Errors are not silently swallowed. Lookup failures log at ERROR with a traceback and a metric; this uses the module's documented compliance-audit exception, where the write must never block the state machine.
  - Stdlib `logging` modules use `%s`/`exc_info`; the loguru call site in `lifecycle.py` was untouched apart from the call it wraps.
  - No PII in logs: IDs only.
- [x] Feature-flagged if user-visible and non-trivial (or justify why not). Not user-visible (see §5 and §8).

Adversarial review (gate #10): there is no Agent tool in this session, so CLAUDE.md's named alternative, `/code-review` at high effort, was run against the actual diff, with the `spinr-insurance-period-auditor` checklist applied by hand. It found these, all acted on:

1. Rider-side `complete_ride` in `lifecycle.py` had the same unconditional Period 1. **Fixed.**
2. `mark_rider_noshow` wrote nothing for an offline driver, leaving Period 2 open. **Fixed.**
3. Admin cancel and force-complete had the same no-close. **Fixed.**
4. A stale-read race: re-asserting Period 3 after a concurrent completion. **Fixed** by never writing 2/3 from the forced-offline helper.
5. The accept-path document suspension in `_shared.py` was missed. **Fixed.**
6. An unordered `limit=1` ride pick. **Moot after fix 4.**
7. The edit hook's formatter had collapsed an unrelated expression in `ride_complete.py`. I first reverted it, then **re-applied it**, because the pre-commit gate requires formatted staged files (see the formatting note above).

Two findings were left as follow-ups (below): extend the reconciler itself to heal offline drivers, and share the candidate queries with the reconciler.

### What was NOT verified

- **Not tested against a real Postgres or Supabase.** All tests mock `db_supabase` and `record_period_transition`. The `record_insurance_period_transition` RPC's close-and-open semantics are unchanged but were not exercised end to end here.
- **No staging repro** of an admin suspending a driver mid-trip.
- **Historical dangling rows are not backfilled.** Any open Period 1/2/3 left behind by forced-offline events *before* this deploy stays open. Finding and correcting them (via `driver_insurance_period_corrections`) needs a production read and is out of scope.
- **Out-of-scope writers that still use a blanket Period 0:** `routes/auth.py` logout, `routes/users.py` account delete, `routes/drivers/profile.py` needs_review, `routes/drivers/subscriptions.py` expiry, `utils/stale_intent_reconciler.py` and `utils/spinr_pass.py` all write `record_period_transition(driver_id, 0)` without checking for an active ride. Whether any of them can fire mid-trip, and so close a live Period 3, was not audited. That is the opposite risk class and should get its own review.
- **Reconciler still blind to offline drivers.** `insurance_period_reconciler._online_idle_candidates` still only scans online drivers. Healing an open 1/2/3 for an offline driver there would be a defense-in-depth backstop for all of the above, and is a recommended follow-up.
- **`set_driver_available` returning `None` at ride end** now writes no period row where before it wrote Period 1. I reasoned this is correct per the helper's contract; it was not observed in production.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
