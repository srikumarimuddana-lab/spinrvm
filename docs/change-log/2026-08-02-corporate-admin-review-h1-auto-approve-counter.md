# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — High #1 |

## 1. Issue / gap identified

The `auto_approve_monthly_count` spend control — meant to cap how many
allowance top-up requests a member can have auto-approved per billing
period — was a permanent no-op. A member could auto-approve unlimited
top-up requests per period, one under-the-cap request at a time.

## 2. Root cause

`routes/corporate_rider.py::submit_request` reads
`allowance.get("auto_approved_this_period")` and compares it against the
configured monthly cap before auto-approving a request. The counter is only
ever written to `0` — at allowance-row creation and at period rollover
(`corporate_repo.py::reset_allowance_period`) — and is never incremented
anywhere in the codebase. The comparison `used_auto < auto_monthly` was
therefore always `0 < auto_monthly`, true for any positive cap, forever.

## 3. Fix / remediation

Added an `upsert_member_allowance(member_id=..., patch={"auto_approved_this_period":
used_auto + 1})` call immediately after the grant succeeds in the
auto-approval branch of `submit_request` — the only code path that performs
an auto-approval, so the increment belongs exactly here. Reused the existing
`upsert_member_allowance` repository function rather than adding a new one.

This is a non-atomic read-then-write increment (using the `used_auto` value
already read earlier in the same request), consistent with how the rest of
this function already reads allowance state non-atomically before deciding
whether to auto-approve. A tighter, database-level atomic increment (e.g. a
dedicated RPC) was considered but scoped out: the counter is a soft
per-period request-count limiter, not the primary money-safety control —
the master wallet itself is now floor-protected end-to-end (see the
Critical #2 fix earlier in this review), so a rare race letting one extra
auto-approval through under concurrent requests is a much lower-consequence
failure than the wallet floor being bypassable, which is not the case here.

## 4. Risk & impact on existing functionality

- **Blast radius: one function, `submit_request`'s auto-approval branch.**
  Grepped `auto_approved_this_period` across the whole backend — the only
  other references are the schema default, the reset-to-0 write on period
  rollover, and three test fixtures that all set it to `0` as a starting
  state (none assert a specific non-zero value that this change would
  contradict).
- **Two existing tests didn't mock the new call and would have failed**:
  `test_submit_request_auto_approved_applies_grant` and
  `test_allowance_request_auto_approves_within_cap` in
  `test_corporate_rider_routes.py` both exercise the auto-approval branch
  and patch `routes.corporate_rider.apply_grant` but not
  `routes.corporate_rider.upsert_member_allowance` — added the missing mock
  to both. The third auto-approval-adjacent test in the same file,
  `test_submit_request_over_auto_cap_goes_pending`, falls through to the
  plain-pending branch before `apply_grant` (and now `upsert_member_allowance`)
  is ever reached, so it needed no change.
- No interaction with C1/C2/C3 — this is a separate function in the
  self-serve top-up-request flow, not the ride-booking or ride-settlement
  path those fixes touched.

## 5. User-experience effect

**Corporate riders with an `auto_approve_monthly_count` configured on their
allowance**: after this fix, a rider who has already had that many requests
auto-approved within the current billing period will have their next
request fall through to the plain `pending` (manual-approval) path instead
of being auto-approved again — matching what the feature was always
supposed to do. **No effect** on riders whose company hasn't configured
auto-approval, or who stay under their period's auto-approve count.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/corporate_rider.py` | Auto-approval branch now increments `auto_approved_this_period` via `upsert_member_allowance` after the grant succeeds | Close the permanent-no-op gap in the auto-approve spend control |
| `backend/tests/test_corporate_rider_routes.py` | Added `upsert_member_allowance` mock to 2 existing auto-approval tests; added an explicit assertion that the counter advances from 0 to 1 | Cover the new call; prevent the fixture gap from silently masking a regression |

## 7. Before / after

```python
# Before
wallet = await get_corporate_wallet_by_company(company_id)
if wallet and allowance.get("id"):
    await apply_grant(...)
return row
```

```python
# After
wallet = await get_corporate_wallet_by_company(company_id)
if wallet and allowance.get("id"):
    await apply_grant(...)
    await upsert_member_allowance(
        member_id=membership["id"],
        patch={"auto_approved_this_period": used_auto + 1},
    )
return row
```

## 8. Rollback plan

Plain code change, no migration, no schema change — the
`auto_approved_this_period` column already existed and was already read;
this fix only adds the missing write. `git revert` fully restores the prior
(no-op) behavior. No feature flag — this closes a control that was already
supposed to be active; there's no meaningful dark-ship version of "make an
existing, documented spend-control setting actually take effect."

## 9. Verification performed

- [x] Automated tests: `test_corporate_rider_routes.py`, 23 tests (2
      modified with the new mock + assertion) — all passed via the
      session's `/tmp/spinr_venv` venv.
- [x] `ruff check` on both touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4): every reference to
      `auto_approved_this_period` in the codebase.
- [x] Dry-run scenario: a member's allowance has
      `auto_approve_monthly_count=2`. They submit 3 top-up requests within
      the same billing period, each under the auto-approve cap amount.
      Before this fix: all 3 auto-approve. After this fix: the first 2
      auto-approve (counter goes 0→1→2), the 3rd falls through to
      `pending` since `2 < 2` is false.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — one function, three
      grep-confirmed references, two dependent tests identified and fixed
- [x] No silent behavior change to a working flow — riders under their
      period's auto-approve count see no difference; only riders who would
      have exceeded the (previously non-functional) cap are affected, which
      is the fix's entire purpose

## What was NOT verified

Not tested against a live/staging Supabase instance — only mocked repo
responses. Did not implement database-level atomicity for the increment
(a concurrent-request race could theoretically let one extra auto-approval
through); reasoned above as an acceptable, explicitly-scoped trade-off given
the master wallet's own floor protection is the real backstop, but flagging
it here rather than letting the omission be silent.
