# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session), user request: "c45" |
| Surface(s) | backend (test only — no production code changed) |
| Domain (Sentry tag) | payments, auth |
| PR / commit link | commit following this log |
| Related issue or gap ID | `ACTION_ITEMS.md` C45 (failure 2) |

## 1. Issue / gap identified

`tests/test_payments_coverage_gap_closure.py::test_confirm_payment_real_ride_ownership_mismatch`
was failing on `main`, asserting a ride-ownership mismatch should 403 but getting a 500
instead. C45 originally logged this as "money/auth-adjacent... worth treating as a real
regression, not a flaky test" and flagged it for real investigation.

## 2. Root cause

**Not a production bug — a stale test mock, broken by a legitimate prior optimization.**
Commit `081ab91e7` ("P0 #6: Deduplicate confirm_payment ride fetches (3 to 1 DB call)",
landed directly on `main` the day before this fix, not from a Claude session) changed
`routes/payments.py`'s `confirm_payment` to fetch and ownership-check the ride **exactly
once**, immediately (`if ride_id: ride = await db_supabase.get_ride(ride_id); if
ride["rider_id"] != current_user["id"]: raise HTTPException(403, ...)`), reusing that same
already-validated `ride` object in every later branch instead of re-fetching it (mock-path
and non-mock-path alike used to each call `get_ride` again separately — three calls became
one).

The failing test's mock predates that optimization: it set `mock_db.get_ride =
AsyncMock(side_effect=[ride_matching, ride_mismatched])`, expecting a **second** `get_ride`
call deep inside the non-mock branch to return the mismatched ride. That second call no
longer exists. Since `get_ride` is now called once and the mock's first (matching) value
satisfies it, the real, unconditional ownership check at the top of the function passes
(no mismatch is ever presented to it), execution falls through the rest of the flow, and
crashes later on an unrelated, incidentally-unconfigured mock
(`db_supabase.update_ride` as a bare `MagicMock`, not `AsyncMock` — `TypeError: object
MagicMock can't be used in 'await' expression`), caught by `confirm_payment`'s generic
`except Exception` handler and turned into a 500.

**Production behavior itself is correct and, if anything, more secure now**: ownership is
enforced by a single, unconditional, fail-fast gate before any Stripe interaction, rather
than three redundant per-path re-checks. Verified by reading `routes/payments.py` directly
(lines 622-627 for the gate; lines 663-671 and 690-695 for the two now-defensive, currently
unreachable re-checks that reuse the same validated `ride` object) and by the sibling test
`test_confirm_payment_mock_ride_ownership_mismatch` (same file, mock-path equivalent),
which already used the correct single-`get_ride` pattern and was passing the whole time —
confirming the mock-path ownership gate was never at risk, only this one stale test.

## 3. Fix / remediation

Test-only change. Replaced the stale two-value `side_effect` with a single
`return_value={"id": "ride-1", "rider_id": _OTHER_USER_ID, "payment_status": "pending"}`,
mirroring the already-correct sibling test's pattern. Removed the now-unreachable
Stripe-retrieve/`get_app_settings` mocking (the 403 fires before either would ever be
called with the current single-fetch code, so keeping them would misleadingly imply the
test still exercises that path). Added `mock_db.get_ride.assert_awaited_once()` as a
regression guard — if a future change reintroduces a second `get_ride` call in this path,
this test will now fail loudly on the call count rather than silently drifting out of sync
with production code again. Rewrote the docstring to explain the P0 #6 dedup and point at
the sibling test's pattern, so the next person to touch this doesn't have to re-derive the
same root cause from scratch.

**No production code changed** — `routes/payments.py` is untouched by this commit.

## 4. Risk & impact on existing functionality

- **Blast radius: one test file, one test function.** Grepped
  `tests/test_payments_coverage_gap_closure.py` for other multi-value `get_ride` mocks —
  none found; this was the only test broken by the P0 #6 dedup.
- **What else reads/writes the same code path:** `confirm_payment` itself is unchanged.
  Ran the full payments test suite (`test_coverage_payments.py`,
  `test_payments_coverage_gap_closure.py`, `test_payments_stripe_error_specificity.py` —
  60 tests) to confirm no other test relies on the old three-call pattern; all pass.
- **Not fixed, deliberately, and named rather than silently left**: the two now-unreachable
  ownership re-checks inside `confirm_payment` (mock-path line ~663-671, non-mock-path line
  ~690-695) are dead code given the current single-fetch flow — harmless
  defense-in-depth, not a bug, and outside this fix's scope (removing them is unrelated to
  why the test was failing). Worth a follow-up cleanup someday, not urgent.

## 5. User-experience effect

None. Test-only change; no rider/driver/admin-facing behavior changed. The real ownership
enforcement this test verifies was already correct in production before this fix — riders
could not and still cannot confirm payment for a ride they don't own.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_payments_coverage_gap_closure.py` | `test_confirm_payment_real_ride_ownership_mismatch`: single-value `get_ride` mock instead of stale two-value `side_effect`; removed now-unreachable Stripe/settings mocking; added an await-count regression guard; rewrote docstring with real root cause. | The test's mock no longer matched the current (correctly optimized) production code shape. |

## 7. Before / after

```python
# Before -- mocked a second get_ride call that no longer happens
mock_db.get_ride = AsyncMock(
    side_effect=[
        {"id": "ride-1", "rider_id": _USER["id"], "payment_status": "pending"},
        {"id": "ride-1", "rider_id": _OTHER_USER_ID, "payment_status": "pending"},
    ]
)
```

```python
# After -- single mock matching the current single-fetch code
mock_db.get_ride = AsyncMock(
    return_value={"id": "ride-1", "rider_id": _OTHER_USER_ID, "payment_status": "pending"}
)
...
mock_db.get_ride.assert_awaited_once()
```

## 8. Rollback plan

`git revert` is complete and sufficient — test-only change, no data or production-code
component.

## 9. Verification performed

- [x] **Automated tests**: the fixed test + its mock-path sibling (2/2 pass); full test file
      (14/14 pass); full payments suite across 3 files (60/60 pass).
- [x] **Root cause read directly from production code**, not inferred from the stack trace
      alone — traced `routes/payments.py`'s exact control flow line by line to confirm the
      ownership gate fires once, early, unconditionally, and that the later reuses are
      provably unreachable given `ride_id` is a single fixed value for the whole request.
- [x] **Git-archaeology performed**: `git log`/`git show` on `081ab91e7` to find the exact
      commit and PR that changed this code shape, confirming the test broke from a real,
      deliberate, already-reviewed optimization rather than an accidental regression.
- [x] **Blast-radius grep performed** (§4) — no other test in this file uses the stale
      multi-call pattern.
- [x] Reviewed against `CLAUDE.md`'s testing conventions (auth-adjacent branch needs both
      the allowed and denied path tested — the denied path is what this fix restores real
      coverage for) and "do not silently swallow errors" (the production `except Exception`
      handler's generic 500 is what disguised this as a payments bug rather than a test bug;
      noted but not changed, since it's working as designed for genuinely unexpected errors).

## 10. Sign-off

- [x] Rollback plan is concrete (plain revert, no data-layer component).
- [x] Blast radius is stated, not assumed — one test file, one function, verified via full
      suite run.
- [x] No silent behavior change to an already-shipped flow — production code is completely
      untouched by this commit; the real ownership enforcement was already correct.

## What was NOT verified

- The GST/PST compliance-report timeout failures (C45 failures 1 and 3) are a separate,
  unrelated root cause — not investigated or fixed in this commit. Still open.
- The two now-dead ownership re-checks inside `confirm_payment` were confirmed unreachable
  by reading the code, not by adding coverage instrumentation to prove it at runtime — a
  belt-and-suspenders check, not exercised as a live test assertion.
