# Change Impact & Risk Log — `cancel_ride_rider` reads stale hold state before its own atomic claim

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code session (proactive edge-case/error-handling sweep, on behalf of ittalenthire.ca@gmail.com) |
| Surface(s) | backend (rides, payments) |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch, `fix/cancel-ride-claim-race-c133`) |
| Related issue or gap ID | ACTION_ITEMS.md C133 |

## 1. Issue / gap identified

`backend/routes/rides/cancellation.py::cancel_ride_rider` reads `ride.get("auth_status")`,
`ride.get("payment_intent_id")`, and `ride.get("authorized_amount")` from the `ride` dict
fetched by `_require_ride_in_state_rider` **before** this function's own atomic
`status -> cancelled` claim UPDATE, and never re-reads them afterward. If a concurrent capture
(e.g. `payment_retry.py`'s `requires_capture` branch) lands on the hold in the window between
that initial read and the claim, `_hold_is_live` still evaluates `True` on the stale snapshot.
`capture_cancellation_fee` then attempts to partially capture an already-fully-captured
PaymentIntent, fails, and the existing fallback-on-failure path charges a **fresh**
PaymentIntent for the fee on top of money already taken — a real double-charge.

## 2. Root cause

Found by `spinr-money-auditor` during review of the 2026-09-21 already-captured-hold refund fix
(`docs/change-log/2026-09-21-cancellation-refund-already-captured-hold.md`), filed as
ACTION_ITEMS.md C133 and deferred at the time (narrower in scope than that fix, would need
re-reading payment fields after the claim). Picked up now via a proactive edge-case/
error-handling sweep, not a live incident — no evidence this race has ever actually fired
(requires a concurrent capture landing in a specific narrow window).

## 3. Fix / remediation

`backend/repositories/_base.py`'s `update_one()` already returns PostgREST's full updated row
via `_single_row_from_res(res)` — for free, no extra DB round-trip. The atomic claim
(`_cancel_claim = await _deps.db_supabase.update_one(...)`) was already capturing this return
value but only checking it for `is None` (claim-lost race -> 409). The fix reads
`payment_intent_id`/`auth_status`/`authorized_amount` from that claim's own returned row
instead of the pre-claim `ride` snapshot — the row PostgREST returns reflects the DB state at
the moment of the UPDATE, so it already carries forward any capture that happened before this
function's claim landed.

```python
_claim_row = _cancel_claim if isinstance(_cancel_claim, dict) else ride
_booking_pi = _claim_row.get("payment_intent_id")
_auth = (_claim_row.get("auth_status") or "").lower()
_held_amount = _round(_d(_claim_row.get("authorized_amount") or 0))
```

The `isinstance(..., dict) else ride` fallback exists only for test-mock safety (some existing
tests mock `update_one` as a bare `AsyncMock()`, which returns a non-dict `MagicMock` by
default) — in real Supabase usage `_cancel_claim` is always a dict once the preceding
`is None` check has passed, so this fallback is never exercised in production.

**Adversarial-review finding, fixed in this diff:** two existing tests
(`test_preauth_release_failure_is_non_fatal`, `test_preauth_release_success_marks_auth_released`
in `test_ride_cancellation_branches.py`) mocked `update_one`'s return with a fabricated
"post-claim" row that reset `auth_status` to `None` or to a value ("released") the real claim
UPDATE — which only touches `status`/`cancelled_at`/`updated_at` — would never actually
produce. This silently masked wrong behavior once the fix was in place (one test failed
loudly; the other would have stopped exercising `cancel_authorization`'s failure path entirely
without ever failing). Both corrected to return a realistic row carrying forward the same
`auth_status`/`payment_intent_id` as the pre-claim row.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one function, 3 field reads.** Grepped `cancel_ride_rider`'s
  entire body (lines 44-709) for every other read of `payment_intent_id`/`auth_status` off the
  stale `ride` dict — none found; this is the only place these three fields are read for
  hold-handling decisions.
- No other reader/writer of `rides.auth_status`/`rides.payment_intent_id` was touched — this
  changes which snapshot three local variables are read from, not any write path, RPC, or
  state-machine transition.
- Only changes behavior in the narrow race window this bug describes (a concurrent capture
  between the initial read and the claim); when no such race occurs, `_cancel_claim`'s
  returned row and the pre-claim `ride` dict have identical values for these three fields, so
  behavior is unchanged in the common case.

## 5. User-experience effect

Rider-facing: a rider who cancels a ride during the exact window a concurrent capture is
landing on their booking hold will no longer be double-charged (once for the already-captured
hold, again for a fresh cancellation-fee charge). Not visible mid-session as a UI change —
this closes a money-correctness edge case, not a new feature or altered happy-path behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/cancellation.py` | 3 field reads (`payment_intent_id`/`auth_status`/`authorized_amount`) switched from the pre-claim `ride` dict to the atomic claim's own returned row | Close the double-charge race described above |
| `backend/tests/test_cancel_already_captured_refund.py` | Added `test_capture_race_between_initial_read_and_claim_uses_post_claim_state` | Regression guardrail — verified via `git stash` to fail without the fix, pass with it |
| `backend/tests/test_ride_cancellation_branches.py` | Corrected two tests' `update_one` mocks to return a realistic post-claim row (unchanged `auth_status`/`payment_intent_id`, since the claim UPDATE doesn't touch those columns) instead of a fabricated one that reset them | The old mocks silently masked wrong behavior under the fix; one failed loudly, the other would have stopped testing what it claimed to test |

## 7. Before / after

```python
# Before
_booking_pi = ride.get("payment_intent_id")
_auth = (ride.get("auth_status") or "").lower()
_held_amount = _round(_d(ride.get("authorized_amount") or 0))

# After
_claim_row = _cancel_claim if isinstance(_cancel_claim, dict) else ride
_booking_pi = _claim_row.get("payment_intent_id")
_auth = (_claim_row.get("auth_status") or "").lower()
_held_amount = _round(_d(_claim_row.get("authorized_amount") or 0))
```

## 8. Rollback plan

`git revert` is a complete rollback — no data migration, no config/flag, no schema change.
Reverting returns to the prior (already-racy, not newly-broken) behavior of reading the
pre-claim snapshot.

## 9. Verification performed

- [x] Automated tests run — `pytest tests/test_cancel_already_captured_refund.py
  tests/test_ride_cancellation_branches.py tests/test_cancellation_fee_card_charge.py
  tests/test_cancel_fee_from_hold.py tests/test_ride_state_machine.py
  tests/test_ride_state_transition_metrics.py` (64 passed); broader sweep
  `pytest tests/ -k "cancel or cancellation" --ignore=tests/rls` (440 passed, 10 skipped)
- [x] Regression proof — the new test was confirmed to FAIL without the fix (via `git stash`
  on just `cancellation.py`) and PASS with it, demonstrating it actually catches the bug
- [ ] Manual repro steps followed in staging — not performed; no staging/live Supabase access
  in this environment
- [x] Blast-radius grep performed — every read of these 3 fields within `cancel_ride_rider`
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — ride state machine / money surface;
  dispatched `spinr-money-auditor` before commit (see PR for verdict)
- [x] State-machine/money dry run — exercised against `mock_supabase_client`-style fixtures
  with a concrete before/after scenario (the new regression test itself)

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in

## What was NOT verified

- Not tested against a real Supabase/Stripe integration — verified via mocked unit tests only.
- The race window itself (a concurrent capture landing between the initial read and the claim)
  has never been observed in production per the original C133 finding — this fix closes a
  theoretical gap found by review, not a confirmed live incident.
- The residual two-write design noted in C77's own entry (ride update + ledger insert as
  separate application-layer writes, rather than a single RPC) still applies here too — not
  addressed by this fix, out of scope.
