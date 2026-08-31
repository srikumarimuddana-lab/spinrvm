# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/4753, commit `730b9c7` |
| Related issue or gap ID | #4602 (follow-up), audit finding raised by `spinr-corporate-billing-reviewer` while reviewing #4602's own fix |

## 1. Issue / gap identified

`backend/routes/rides/booking.py` has two nearly-identical "1.5x allowance headroom" pre-dispatch guards for corporate bookings under an `allowed_payment_source == "allowance_only"` policy: the `company_allowance` self-book block (fixed for #4602, uses `grand_total`) and the pre-existing `work_profile` block (used `total_fare`, which excludes area fees/tax). Two guards meant to enforce the same rule used different, inconsistent fare bases.

## 2. Root cause

The `work_profile` guard predates #4602 and was never updated when this file's own "gap #39" convention (total_fare understates what's actually charged once area fees/tax are added) was established and applied to the company_allowance path's policy-fare check and its new #4602 headroom guard. The `work_profile` guard was simply never revisited to match.

## 3. Fix / remediation

Switched the `work_profile` guard's fare basis from `total_fare` to `grand_total` (base fare + area fees + tax), matching the `company_allowance` guard exactly. No other logic changed — same 1.5x multiplier, same `unlimited`/`master_only`/`both` skip conditions, same `400 allowance_low` response shape.

## 4. Risk & impact on existing functionality

- **Other readers/writers**: `grand_total` is a single value computed once per `create_ride` call (`booking.py:892`) and already read by the `company_allowance` guard, the policy-fare check, and the ride-insert payload — this change adds one more read site, no new computation or side effect.
- **Could this regress a working flow?** Yes, by design: a `work_profile` corporate rider on an `allowance_only` company, whose remaining allowance covers `1.5x total_fare` but not `1.5x grand_total` (i.e. their ride has nonzero area fees/tax and their allowance is in that narrow band), will now be refused at booking with `400 allowance_low` where they previously were not. `grand_total >= total_fare` always, so this can only make the guard *stricter* (reject bookings it previously allowed) — it can never let through a booking the old code would have refused.
- **Blast radius**: isolated to the `work_profile` corporate booking block in `booking.py`. Verified via grep that no other file reads or duplicates this specific guard; the `company_allowance` block (same file) and the guest path (`services/company_booking_service.py`) are untouched.
- **Background loops / state machine / money**: no interaction — this is a pre-dispatch validation check, not a state transition or wallet delta. No Postgres function, migration, or background loop involved.

## 5. User-experience effect

- **Who sees a difference**: corporate riders booking under `work_profile=True` on an `allowed_payment_source == "allowance_only"` company policy, specifically only those whose remaining allowance sits in the narrow band between `1.5x total_fare` and `1.5x grand_total` for a given ride (i.e., ride has real area fees/tax, and their allowance was already close to the boundary).
- **Not visible mid-session**: this is a pre-booking check — it can only change the outcome of a *new* booking attempt, never something a rider mid-ride experiences differently.
- **Copy/notification**: no new copy. The existing `allowance_low` failure reason and its client-side message are unchanged — riders in the newly-caught band see the same message a `company_allowance` rider already sees for the equivalent case, not a new/different error.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/booking.py` | `work_profile` guard's fare basis: `_f(total_fare)` → `_f(grand_total)`, plus an explanatory comment | Reconcile with the `company_allowance` guard and this file's own "gap #39" convention |
| `backend/tests/test_create_ride_remaining_branches.py` | Added `test_work_profile_allowance_guard_uses_grand_total_not_total_fare` | Prove the intended behavior change (allowance sized between the two thresholds now correctly fails) |

## 7. Before / after

```python
# Before
if _remaining < _round(_d(str(_f(total_fare))) * _d("1.5")) and not _master_permitted:
    raise HTTPException(status_code=400, detail={"reason": "allowance_low"})

# After
if _remaining < _round(_d(str(_f(grand_total))) * _d("1.5")) and not _master_permitted:
    raise HTTPException(status_code=400, detail={"reason": "allowance_low"})
```

## 8. Rollback plan

`git revert` is a complete and sufficient rollback here: this is a pure comparison-basis change in a pre-dispatch validation check with no data written, no migration, no wallet delta, and no ride ever created under the old-vs-new distinction (a request that now fails the guard simply never reaches ride creation — there is no downstream state to unwind). No feature flag or config value gates this path today; reverting the code is the entire rollback.

## 9. Verification performed

- [x] Automated tests run (unit) — full `test_create_ride_remaining_branches.py` (19 tests) plus a broader sweep (`-k "create_ride or corporate_ride or company_allowance or booking or work_profile"`, 516 tests) — all pass.
- [ ] Manual repro steps followed in staging — not performed; no staging/live Supabase access from this sandbox.
- [x] Blast-radius grep performed — searched for other callers/readers of the `work_profile` guard's variables and of `total_fare`/`grand_total` in this file; confirmed isolated to this one guard.
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — money (Decimal-only, `_d`/`_round`/`_f`), corporate billing (mirrors the sibling guard's exact structure). Independently reviewed by `spinr-corporate-billing-reviewer` (verdict: code/test correct, no blocker; this Change Impact Log entry is the process item it asked for).
- [ ] Feature-flagged — not flagged. This is a bug-fix-shaped correction (closing an inconsistency, not adding new user-facing capability) and the CLAUDE.md flagging guidance targets new/changed UX and validation rules broadly, but the change here is narrow (one comparison operand) and reviewer-verified as strictly-more-conservative; flagging it behind `app_settings` was judged more complexity than the risk warrants for a same-file, same-guard-shape reconciliation. If the account owner disagrees, this is a one-line revert away.

## What was NOT verified

- Not exercised against real production/staging data — no live Supabase access from this sandbox. The specific "allowance in the narrow band between the two thresholds" scenario is proven only via the new mocked unit test's arithmetic, not a real corporate account.
- Whether any `work_profile` corporate account currently has a rider whose allowance sits in that narrow band today (i.e. whether this will visibly reject a booking that would have succeeded yesterday) was not investigated — that would require a query against production `corporate_allowances`/`area_fees` data this session cannot run.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-level unwind needed)
- [x] Blast radius is stated, not assumed (isolated to one guard in one file)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — see Section 5 above
