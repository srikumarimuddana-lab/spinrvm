# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude, at user request ("look at the payments test failure directly and identify root cause" → "push the minimal fix through the normal payments-review path") |
| Surface(s) | backend |
| Domain (Sentry tag) | payments, auth |
| PR / commit link | commit following this log |
| Related issue or gap ID | `ACTION_ITEMS.md` C45 (already closed by PR #4622, which fixed the actual CI failure); this is the follow-up cleanup that PR's own change-log explicitly deferred |

## 1. Issue / gap identified

While investigating why `tests/test_payments_coverage_gap_closure.py::test_confirm_payment_real_ride_ownership_mismatch`
was returning 500 instead of 403 on `main`, root-caused independently (in parallel with — and
reaching the same conclusion as — PR #4622, which landed the actual test fix first) that
`routes/payments.py`'s `confirm_payment` contains **two identical, provably unreachable
ownership re-checks** (one in the mock-payment branch, one in the non-mock/Stripe branch),
left over from before commit `081ab91e7` ("P0 #6: dedupe confirm_payment ride fetches, 3 to 1
DB call") collapsed three separate `get_ride` calls into one early, unconditional gate.

PR #4622's own change-log for the test fix explicitly named this: *"the two now-unreachable
ownership re-checks inside confirm_payment (mock-path line ~663-671, non-mock-path line
~690-695) are dead code given the current single-fetch flow — harmless defense-in-depth, not
a bug, and outside this fix's scope... Worth a follow-up cleanup someday, not urgent."* This
commit is that follow-up.

## 2. Root cause

`ride` is fetched exactly once (`routes/payments.py:623`) and ownership-checked exactly once,
immediately (`if ride["rider_id"] != current_user["id"]: raise HTTPException(403)`, line
626-627) — before either the mock or non-mock branch is entered. Both branches then reused
that same already-validated `ride` object (`_ride = ride`) instead of re-fetching, but each
still carried a leftover re-check against that same object
(`if not _ride or _ride.get("rider_id") != current_user["id"]: raise HTTPException(403, ...)`).
Since `_ride` is provably the same object that already passed the check a few lines above
(and is a local Python variable, never reassigned or mutated in between — verified: no
`await` between the two points touches `ride`), this condition can never evaluate true. It is
dead code, not defense-in-depth against anything that can actually happen — confirmed by
grepping `routes/`, `services/`, `repositories/`, `db_supabase.py`, and every
`backend/migrations/*.sql` file for any write path that could mutate `rides.rider_id` after
creation. None exists; `rider_id` is set once, at ride insert, and never updated.

Leaving this dead code in place is not neutral: it previously masked the real bug in
`test_confirm_payment_real_ride_ownership_mismatch` (a test that scripted a *second*
`get_ride` response for a code path that no longer makes a second call — see PR #4622 for
that fix) and, going forward, misleads a reader auditing this money path into believing
there is a live re-check protecting against a mid-request ownership change that cannot
occur.

## 3. Fix / remediation

Removed both dead ownership re-check blocks (mock-path and non-mock-path), replacing each
with a comment explaining why the removal is safe and pointing at the still-live check
earlier in the function that actually enforces ownership. The non-mock branch still needs
`_ride = ride` (used later for the PI-to-ride amount-binding checks); the mock branch didn't
use `_ride` for anything else, so that assignment is removed there too.

No behavior changes: every scenario the removed code claimed to guard against is still
caught by the single, unconditional ownership check at line 626-627, which fires before
either branch is ever entered.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `confirm_payment`'s two branches, no other reader.** Grepped for
  other callers/consumers of `confirm_payment` — none call into these specific lines
  independently; `confirm_payment` is only reachable via `POST /payments/confirm`.
- **What else reads/writes the same code path:** the PI-to-ride amount-binding logic just
  below the removed non-mock block (`pi_ride_id != ride_id` check, `amount_received >=
  owed_cents` underpayment check) is unaffected — both read only `_ride` and `intent`, neither
  of which changed. Confirmed via a spinr-money-auditor review pass on this exact diff: "the
  binding/amount logic below is untouched and still runs in the same order."
- Ran two independent review passes on this diff before pushing:
  - `spinr-money-auditor`: confirmed `rider_id` is never mutated after creation (grepped
    migrations 216/228/285/289/296/321/323/324/335 and all PII-retention logic too — the
    PIPEDA "attributable retention" model explicitly never touches `rides.rider_id`);
    confirmed the removal is safe for this function's control flow; confirmed the
    amount-binding logic is unaffected; confirmed no other money-arithmetic/idempotency/
    Stripe concern in the diff.
  - `spinr-security-auditor`: confirmed (REFUTED as a risk) that `_ride`'s ownership cannot
    differ from the earlier check within one request — `ride` is a fresh per-call dict held
    in a private coroutine-local variable, not shared/cached, and no `await` between the two
    points reassigns or mutates it; confirmed the PI-to-caller and PI-to-ride binding checks
    (the real IDOR mitigations in this function) are unaffected; found no other auth/IDOR
    issue.
  - Both agents also independently caught the same transient mid-session git-merge-conflict
    state (from resolving an unrelated stash conflict with PR #4622's already-landed test
    fix) and correctly refused to bless the diff until it was verified resolved — verified
    clean (`git status`, no conflict markers, `pytest --collect-only` succeeds, full test
    suite green) before this commit.
- **Not fixed here, and no longer applicable**: the actual CI-failing test
  (`test_confirm_payment_real_ride_ownership_mismatch`) needed no changes in this commit —
  PR #4622 already fixed it on `main` before this branch was rebased onto it. This commit
  contains only the deferred dead-code cleanup that PR named but didn't do.

## 5. User-experience effect

None. No rider/driver/corporate-admin-facing behavior changes — the ownership enforcement a
rider experiences (cannot confirm payment for a ride they don't own) was already correct
before this commit and is unchanged after it; only unreachable code was removed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/payments.py` | Removed two identical, unreachable ownership re-check blocks in `confirm_payment` (mock-path and non-mock-path), each replaced with an explanatory comment | Dead code masked a real test bug once already (PR #4622) and misleads future readers about what this money path actually re-verifies |
| `docs/change-log/2026-08-27-confirm-payment-dead-ownership-recheck-removal.md` | This log | Required for a change touching a live-tested payments surface |

## 7. Before / after

```python
# Before — non-mock branch (mock branch had the identical pattern)
if ride_id:
    _ride = ride  # reuse already-fetched & validated ride (was: get_ride round-trip)
    if not _ride or _ride.get("rider_id") != current_user["id"]:
        raise HTTPException(
            status_code=403,
            detail="Not authorized to confirm payment for this ride",
        )
```

```python
# After
if ride_id:
    # Reuse the ride already fetched & ownership-checked above (line
    # ~626) instead of a second get_ride round-trip. A re-check here
    # against that same object can never fire — it would need
    # rider_id to have changed since the fetch a few lines up, and
    # nothing in this codebase ever updates rides.rider_id after ride
    # creation (grepped routes/services/repositories to confirm)...
    _ride = ride
```

## 8. Rollback plan

`git revert` is complete and sufficient. This is a pure code-removal with no data,
migration, or Stripe-integration component — the removed condition never fired in
production, so restoring it changes nothing observable either way.

## 9. Verification performed

- [x] **Automated tests**: `test_payments_coverage_gap_closure.py` + `test_coverage_payments.py`
      (57/57 pass); full payment-related sweep across the backend test suite
      (`pytest -k "payment or confirm_payment"`, 493 passed, 1 skipped, unrelated).
- [x] `ruff check` and `ruff format --check` on `routes/payments.py` — clean.
- [x] **Two independent subagent reviews** (`spinr-money-auditor`, `spinr-security-auditor`)
      per this repo's payments-review policy (Codex auto-review is currently off) — both
      REFUTED any bypass/regression risk; both flagged (and this commit resolves) the
      mock-path/non-mock-path inconsistency of removing only one of the two dead blocks.
- [x] Grepped `routes/`, `services/`, `repositories/`, `db_supabase.py`, and every
      `backend/migrations/*.sql` for any `rider_id` mutation path — none found.
- [ ] Not run against a real Supabase/Stripe instance — reasoned from code + mocked tests +
      the two review passes, not an end-to-end manual repro against live services.

## What was NOT verified

- Did not manually exercise `POST /payments/confirm` against a running backend with a real
  or sandbox Stripe key — verified via static analysis, the existing (and PR #4622-fixed)
  test suite, and two independent agent reviews, not a live manual repro.
- Did not audit every other function in `routes/payments.py` for similar dead-code patterns
  beyond `confirm_payment` — scoped this cleanup to the two blocks directly implicated in the
  original test failure and named by PR #4622's change-log.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — isolated to `confirm_payment`, verified by two
      independent review passes plus a full payments test sweep.
- [x] No silent behavior change to an already-shipped flow — the ownership enforcement this
      touches was already correct and is unchanged; only unreachable code was removed.
