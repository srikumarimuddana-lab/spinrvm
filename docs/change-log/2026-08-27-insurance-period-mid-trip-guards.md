# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 (updated 2026-08-31) |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | fix/insurance-period-mid-trip-guards |
| Related issue or gap ID | #4597 (Finding 3) |

## 0. Scope correction (2026-08-31)

This log originally covered two findings from #4597. **Finding 1** (subscription-expiry loop force-offlining a driver mid-trip, `backend/routes/drivers/subscriptions.py`) was independently fixed and merged to `main` in commit `54310fe` (PR #4732) while this branch was in flight — same root cause, same guard shape, same file. On merging `main` into this branch, `subscriptions.py` and its test file were resolved to `main`'s version and this branch's now-duplicate Finding-1 changes were dropped rather than reintroduced. **Finding 3** (rider-cancel writing Period 1 unconditionally, `backend/routes/rides/cancellation.py`) was not touched by #4732 and remains this PR's sole content. Everything below now describes Finding 3 only; the original Finding-1 sections are removed rather than left stale.

## 1. Issue / gap identified

**Finding 3 (P2):** rider-initiated ride cancellation writes Period 1 unconditionally after releasing the driver, without checking whether the release actually succeeded.

## 2. Root cause

`backend/routes/rides/cancellation.py`'s rider-cancel handler predates the `released.get("is_available")` guard pattern that was added to `routes/rides/matching.py` and `routes/drivers/ride_flow.py` on 2026-08-18 for the equivalent hazard (driver went offline between dispatch and a later event). The rider-cancel path was never updated to match.

Surfaced by a read-only swarm audit (2026-08-27, see #4597); confirmed directly against the current code before implementing (not taken on faith from the audit's own text).

## 3. Fix / remediation

Capture `set_driver_available`'s return value and only call `record_period_transition(driver_id, 1)` when `released.get("is_available")` is true — mirroring `matching.py`'s existing guard verbatim.

## 4. Risk & impact on existing functionality

Blast-radius grep performed across `backend/`: `record_period_transition` has 25 call sites across 13 files (`routes/users.py`, `routes/admin/rides.py`, `routes/drivers/{ride_flow,subscriptions,ride_cancel,profile,status}.py`, `routes/rides/{matching,cancellation,lifecycle}.py`, `utils/{stale_intent_reconciler,spinr_pass}.py`); `set_driver_available` has 7 callers. This change touches exactly **1** of those 25 call sites (`cancellation.py`'s rider-cancel Period-1 write) — every other call site, including `subscriptions.py`'s (already fixed upstream in #4732), is untouched by this PR.

- **Blast radius: isolated.** No shared helper, table schema, or background-loop registration changed — only the condition under which this one call site fires.
- **Could this regress a working flow?** Two tests cover this directly: one confirming the guard skips the write when the driver was already offline, one confirming it still writes Period 1 in the normal case (regression guard against over-suppression).
- **Interaction with ride state machine:** none — no ride-status write is added, changed, or removed. Only a `driver_insurance_periods` write is gated.
- **Interaction with money/wallet deltas:** none.

## 5. User-experience effect

- **Driver:** no visible change — this only affects an internal audit-trail write (`driver_insurance_periods`), not anything driver-facing (the driver in question already went offline before this cancel landed).
- **Rider:** no visible change.
- **Admin:** no visible change to admin-dashboard flows.
- Not feature-flagged: this is a bug fix aligning one enforcement path with an already-shipped, unflagged guard pattern (`matching.py`'s release-guard) — introducing a flag would mean deliberately keeping the false-Period-1 bug live behind a toggle, which the regulatory stakes here (SGI insurance-period audit integrity) argue against.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/cancellation.py` | Gate the rider-cancel Period-1 write on `set_driver_available`'s actual return value | Finding 3 — prevent false Period-1 write for an already-offline driver |
| `backend/tests/test_ride_cancellation_branches.py` | 2 new tests: offline driver skips Period-1 write; online driver still gets it (regression guard) | Cover Finding 3's fix and its boundary |

`backend/routes/drivers/subscriptions.py` and `backend/tests/test_subscriptions_coverage.py` are **not** part of this PR's diff — see §0.

## 7. Before / after

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

`git-revert-safe`. Pure code-path guard with no schema, migration, or data write beyond the existing `driver_insurance_periods` append-only table (never mutated, only conditionally not-written). Reverting restores the prior (buggy) behavior exactly; no data-level remediation needed since no incorrect rows were retroactively touched. Per CLAUDE.md's own rule, `driver_insurance_periods` rows already written under the old buggy behavior are never deleted or mutated regardless of this fix.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_ride_cancellation_branches.py` (2 new tests + pre-existing unaffected). `ruff check` clean on `cancellation.py` and its test file.
- [ ] Manual repro steps followed in staging — **not performed**; no staging/Supabase access in this session. Verified against `mock_supabase_client`-style fixtures only, per this repo's standard unit-test convention.
- [x] Blast-radius grep performed: see §4 — `record_period_transition` (25 call sites, 13 files) and `set_driver_available` (7 callers) enumerated; only the 1 intended call site touched by this PR.
- [x] Reviewed against relevant CLAUDE.md convention(s): the insurance-period state table/invariants (Period 2 starts on `driver_assigned`, driver cannot be Period 3 without `ride_id`, append-only), and the "never delete or mutate period rows" rule (respected — this fix only prevents *new* incorrect writes).
- [x] Feature-flagged if user-visible and non-trivial, or justify why not: justified in §5 — a regulatory-integrity bug fix, not a product change; flagging it would mean keeping the bug live behind a toggle.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (git revert)
- [x] Blast radius is stated, not assumed (§4, backed by a real grep)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5)

**What was NOT verified:** not exercised against real Supabase/staging data, only mocked fixtures.
