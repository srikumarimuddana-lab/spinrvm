# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + admin portal review, round 2 — "department/section budgets" — settlement hook slice |

## 1. Issue / gap identified

The round2-26 schema/RPC exist but nothing writes to
`corporate_section_spend` yet.

## 2. Root cause

Never built — see round2-26 for full background.

## 3. Fix / remediation

Added a single hook in `services/payment_service.py::settle_corporate`,
right after the existing `ride_payment_sources` insert and using the
**exact same pattern** as the "audit-only, never blocks or alters
settlement" blocks already in that function (company-inactive flag,
policy-changed flag): if the settling member has a `section_id`, call
`db_supabase.record_section_spend(section_id, month, amount)` with
`amount = allowance_debit + master_debit` (the actual settled total, not
the pre-settlement estimate), wrapped in `try/except` that only logs on
failure. Per the research done before this build, `settle_corporate` is
the single function both independent corporate booking paths
(`company_allowance` and `work_profile` in `routes/rides/booking.py`)
converge on at completion — via `routes/rides/payments.py::process_payment`
(member-initiated) and `auto_settle_guest_corporate` (guest bookings,
which itself calls `settle_corporate` directly) — so this one insertion
point covers every corporate ride regardless of which booking path
created it.

New `tests/test_corporate_settle_section_spend.py`, following the exact
`_patches()` + `contextlib.ExitStack` template already established in
`test_corporate_settle_suspended_audit_flag.py` for adding new
completion-time behavior to `settle_corporate` (that file's docstring
explicitly names this as the pattern to follow for a new flag). 3 tests:
records spend when the member has a section, skips entirely when they
don't (proving the existing suspended/closed-company tests — whose
`_member()` fixture has no `section_id` — are unaffected without needing
any change to that file), and confirms a recording failure never flips
`result.success` to `False`.

## 4. Risk & impact on existing functionality

- **Blast radius: one new block inserted into `settle_corporate`,
  wrapped in the same isolation pattern as its neighbors.** Every line
  before and after the insertion point is unchanged — confirmed by diff.
- **Zero risk to existing `test_corporate_settle_suspended_audit_flag.py`
  tests**: their `_member()` fixture has no `section_id` key, so
  `membership.get("section_id")` evaluates falsy and the new block is a
  complete no-op for every test in that file — verified by reading the
  fixture, not assumed.
- The section-spend recording can never cause `settle_corporate` to
  return `success=False` — it runs after every failure path in the
  function has already either returned early or been resolved, and its
  own `try/except` guarantees a failure inside it is logged, not raised.
- Grepped both call sites of `settle_corporate`
  (`routes/rides/payments.py`, `services/payment_service.py::auto_settle_guest_corporate`):
  neither needed any change — the hook lives entirely inside the shared
  function both already call.

## 5. User-experience effect

None yet — nothing surfaces this recorded spend in any UI or API
response in this commit (round2-28 exposes it on the section list
endpoint).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | New try/except block in `settle_corporate`, inserted after the existing `ride_payment_sources` insert | Record section spend at the one point both booking paths converge |
| `backend/tests/test_corporate_settle_section_spend.py` | New file: 3 tests | Cover the new hook's happy path, no-section skip, and failure isolation |

## 7. Rollback plan

`git revert` the commit. No migration involved — reverting removes the
recording call; `corporate_section_spend` rows already written stay as
historical data (harmless, matches how every other visibility/audit-only
feature in this codebase is rolled back — see `policy_changed_since_booking`'s
own "no flag at all" rollback posture in `domain-corporate.md`).

## 8. Verification performed

- [x] `ast.parse` syntax check on both files — clean.
- [x] Confirmed via direct read of `test_corporate_settle_suspended_audit_flag.py`'s
      `_member()` fixture that it lacks `section_id`, so this change adds
      zero risk to that file's existing three tests — not assumed, checked.
- [x] Manually traced every return path in `settle_corporate` (both
      failure returns and the final success return) to confirm the new
      block only ever executes on the path that already reaches the
      `ride_payment_sources` insert — i.e., after every failure case has
      already exited.
- [x] Confirmed `amount=allowance_debit + master_debit` matches the same
      total already written to `ride_payment_sources` two lines above
      (`allowance_debit_amount` + `master_fallback_amount`), so the
      recorded section spend and the existing per-ride ledger stay
      consistent with each other.
- [x] Did **not** run `pytest` for either file — per this round's
      explicit "don't run tests until everything is developed"
      instruction; deferred to the single end-of-round pass, which will
      also re-run `test_corporate_settle_suspended_audit_flag.py` to
      confirm the "zero risk" claim above empirically, not just by
      inspection.

## 9. Sign-off

- [x] Rollback plan is concrete — `git revert`; any already-recorded
      spend rows are harmless historical data, not something requiring
      cleanup
- [x] Blast radius is stated, not assumed — confirmed via diff and by
      reading the one existing test file whose fixture could have
      collided with this change
- [x] No silent behavior change to a working flow — `settle_corporate`'s
      existing success/failure outcomes and every existing DB write are
      byte-for-byte unchanged; the new block can only add a side effect,
      never alter the function's return value

## What was NOT verified

Did not run `pytest`, so the empirical claim that
`test_corporate_settle_suspended_audit_flag.py`'s existing tests are
unaffected is reasoned from reading its fixture, not confirmed by
execution — this is one of the higher-confidence "not run" claims this
round (the fixture genuinely has no `section_id` key, a simple fact to
verify by reading), but it is still unrun. Reversal/refund handling is
explicitly out of scope, consistent with round2-26's stated limitation —
a refunded ride's spend is not backed out of the section's running total.
