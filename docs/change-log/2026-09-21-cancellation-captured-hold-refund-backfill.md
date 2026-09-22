# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session_01L8WBp4c4HHd7Pq8iysPZuQ) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (added in this branch's PR) |
| Related issue or gap ID | Historical counterpart to `docs/change-log/2026-09-21-cancellation-refund-already-captured-hold.md` — that entry closed the forward-going gap; this one clears the rides already stuck in it |

## 1. Issue / gap identified

The 2026-09-21 cancellation fix added a refund branch for a ride whose booking hold is already `auth_status='captured'` at cancel time — but it only runs for rides cancelled **after** it deploys. Rides already sitting in that state (`status='cancelled'`, `auth_status='captured'`, `refund_amount` unset, PaymentIntent `succeeded` with the full hold received and no refunds) keep the rider's money, and nothing in the system will ever return it. Two real production rides in that state were confirmed by the orchestrating session via a read-only Supabase + Stripe check (both internal test accounts).

## 2. Root cause

The route-level fix is triggered by the cancel request itself, so it can only ever act on a cancellation happening now. The stuck rows were cancelled before it existed; there is no loop, sweeper, or webhook that revisits a cancelled ride's captured hold. `orphaned_hold_reconciler` looks deliberately disjoint (`OPEN_AUTH_STATES = ("authorized","fare_only")` — live holds only, never a captured one), so the captured-and-cancelled population has no owner at all.

## 3. Fix / remediation

Two additions, both additive — no existing code path changes behaviour:

1. **`read_capture_state()`** in `backend/utils/stripe_charge.py`: a read-only Stripe probe returning `{"captured_cents", "refunded_cents"}` for a PaymentIntent, or `None` for "unknown — do not refund". It exists because `amount_received` does **not** decrease when a refund is issued, so it cannot on its own distinguish "nothing refunded yet" from "already refunded". Inside the cancel flow that is harmless (runs once per cancellation, Stripe's idempotency key dedupes a retry), but a human re-running a backfill more than 24h after a first pass is past that key's expiry — summing the intent's existing refunds is the only guard that still holds there.
2. **`backend/scripts/reconcile_cancelled_captured_refunds.py`**: an operator-run, **read-only-by-default** backfill. It selects `status='cancelled' AND auth_status='captured'` rides with no `refund_amount`, and with `--apply` refunds the excess over the fee actually owed using the same `refund_excess_capture` + `record_refund_event` + `refund_amount`/`payment_status` path the live route now uses — so a row it fixes is indistinguishable from one the route handled itself.

**Deliberately NOT a background loop.** This is a closed historical backlog (the route fix closes the source), and every action moves real money out of the platform account. Per CLAUDE.md's "escalate, don't silently ship" gate it is human-triggered, defaults to a dry run, and supports `--ride-id` so a reviewed row can be refunded one at a time. **No refund has been issued against the two known production rides — that is left for a human to trigger deliberately.**

**Adversarial alternative considered:** add the sweep to the existing `orphaned_hold_reconciler` loop (it already walks cancelled rides). Rejected: that loop's whole safety argument rests on only ever touching *uncaptured* holds, where the worst case is releasing money that was never taken. Refunding captured money is a categorically different risk, and auto-executing it on a 15-minute cadence against a backlog nobody has reviewed is exactly what the "escalate before shipping to a live-tested money surface" rule exists to prevent.

## 4. Risk & impact on existing functionality

- **Blast radius of the shipped code: near-zero.** `read_capture_state` is a brand-new function with exactly one caller (the new script) — confirmed by grep; nothing else in the repo references it. The script is not imported by the backend at all (`backend/scripts/` is not on the app's import path) and runs only when a human runs it. No route, loop, or webhook behaviour changes.
- **Blast radius of *running* it with `--apply`: real money.** It issues Stripe refunds and writes `rides.refund_amount` / `rides.payment_status`. Readers of those fields: `routes/rides/queries.py` and `routes/rides/receipts.py` (display), `routes/admin/rides.py` and admin-dashboard rides screens, `routes/webhooks.py` (`charge.refunded` handling). `"refunded"`/`"partially_refunded"` are pre-existing values every one of those already handles — no new vocabulary.
- **Interaction with the `charge.refunded` webhook:** Stripe will fire one for a refund this script issues. The ledger write uses the same `stripe_refund|{pi}|{cents}` dedupe key shape `routes/webhooks.py` uses, so the movement books exactly once regardless of which side lands first.
- **Interaction with background loops:** none. `orphaned_hold_reconciler` / `card_hold_release` select on `OPEN_AUTH_STATES = ("authorized","fare_only")`, disjoint from this script's `auth_status='captured'` precondition.
- **Could it regress a working flow?** The only way is by refunding money that was legitimately kept. Two such traps were found in adversarial review and are now explicitly guarded (see the review section below): a fee captured *out of the booking hold itself* stamps `cancel_fee_payment_intent_id` with that same booking PI, and an *attempted-but-failed* fee charge stamps one too. The fee is treated as already collected only when it was charged on a **different, successfully paid** PaymentIntent.

## 5. User-experience effect

Nothing changes for anyone until a human runs the script. When they do, affected riders are refunded to their original payment method for a cancelled ride they were wrongly charged for, and the ride's payment status in the admin dashboard changes to `refunded` / `partially_refunded`. Not visible mid-session — every affected ride is long since cancelled. No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stripe_charge.py` | Added `read_capture_state()` (read-only; PaymentIntent `amount_received` + summed non-failed refunds, `None` on unknown) | `amount_received` does not drop on refund, so a re-run past Stripe's 24h idempotency window needs the refunded side read separately |
| `backend/scripts/reconcile_cancelled_captured_refunds.py` (new) | Operator-run backfill: dry-run default, `--apply`, `--ride-id`, `--batch`/`--offset` paging, per-outcome reporting, non-zero exit whenever money is unaccounted for | Clears the historical backlog the route fix cannot reach, without auto-executing against live money |
| `backend/tests/test_reconcile_cancelled_captured_refunds.py` (new) | 19 tests, fully mocked Stripe + DB: the probe's branches, the dry-run default, all three idempotency guards, both fee-double-count traps, unknown-state handling, outcome bucketing, candidate selection | Regression cover for a money path with no production dry run available |

## 7. Before / after

Behaviour change is "a stuck ride has no owner" → "a human can clear it". The load-bearing before/after is inside the new fee rule, which adversarial review caught mid-implementation:

```python
# Before (my first draft — WRONG, refunds legitimately collected fees)
if ride.get("cancel_fee_payment_intent_id"):
    return Decimal("0")          # "fee was collected elsewhere"
```

```python
# After — both halves load-bearing
fee_pi = ride.get("cancel_fee_payment_intent_id")
collected_elsewhere = (
    bool(fee_pi)
    and fee_pi != ride.get("payment_intent_id")      # partial capture stamps the BOOKING pi
    and (ride.get("payment_status") or "") == "paid"  # stamped on failed attempts too
)
```

## 8. Rollback plan

- **The code**: pure addition with no production caller — deleting the script and the helper is a no-op for the running system. No migration, no flag, no schema change.
- **A refund already issued**: **not revertible by a deploy.** A `git revert` does not claw back a Stripe refund. This is why the script defaults to a dry run, supports `--ride-id` for one row at a time, and is not wired into any loop: the rollback plan for the *action* is "don't run it until the dry-run output has been read", not "revert afterwards". If a refund is issued in error, recovery is a manual re-charge through Stripe plus a ledger correction — treat that as the real cost of a mistake here.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_reconcile_cancelled_captured_refunds.py tests/test_refund_excess_capture.py tests/test_cancel_already_captured_refund.py tests/test_stripe_charge.py tests/test_stripe_charge_coverage.py tests/test_cancel_fee_from_hold.py tests/test_preauth_release_on_cancel.py tests/test_ride_cancellation_branches.py tests/test_e2e_cancellation.py tests/test_c2_driver_cancel_atomic.py tests/test_cancellation_fee_card_charge.py tests/test_loguru_call_conventions.py tests/test_payment_retry.py` — **200 passed**. Mocked Stripe SDK and mocked DB throughout; no real Stripe or Supabase call in any test.
- [x] `ruff check` / `ruff format` clean on all three touched files. (`ruff check .` reports 29 pre-existing findings repo-wide, all in files this change does not touch.)
- [x] CLI smoke test: `--help` renders; running without `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` refuses with exit 2.
- [x] Blast-radius grep: every reader of `refund_amount`/`payment_status`, every caller of the new helper (one), `OPEN_AUTH_STATES` sweepers, and both writers of `cancel_fee_payment_intent_id` (`routes/rides/cancellation.py`, `routes/drivers/ride_cancel.py`).
- [x] Adversarial review run twice against the actual diff (see below); every finding either fixed or explicitly answered.
- [x] Decimal-only money math throughout — `to_decimal`/`dollars_to_cents`/`cents_to_dollars`, integer cents for all comparisons, no float anywhere in the diff.
- [ ] Not feature-flagged — the code is inert until a human runs it, which is a stronger gate than a flag.
- [ ] No production build applies (backend-only, no frontend surface touched).

## 10. What was NOT verified

- **The script has never been run against a real database or real Stripe account** — not in production, not in staging, not in Stripe test mode. Every behaviour above is proven only against mocks. The first real execution should be a dry run, read by a human, before any `--apply`.
- **The two known stuck production rides were deliberately not touched.** No refund was issued against `pi_3UGNkeFXFgLO2LdO04ITR7Ta` (`SPR-RKYCJM`) or `pi_3UG17IFXFgLO2LdO13kygOEw` (`SPR-XY55VL`); the script's behaviour against those specific rows has not been observed even in dry-run form, because this session has no Stripe/Supabase access (both MCP connectors are unauthenticated here).
- **The true size of the backlog is unknown.** Nobody has run the candidate query; "two rides" is what a targeted lookup found, not a count.
- **`spinr-money-auditor` was not run** — no Agent tool in this session's toolset. The `/code-review` skill at high effort was used instead (CLAUDE.md's named alternative), twice. That is a real substitute but not the domain-specific money auditor.
- **PostgREST paging behaviour** (`order="id"` + `--offset`) is asserted at the call-site level in tests, not exercised against a real PostgREST instance.

## Adversarial review (`/code-review`, high effort, before commit)

Run twice against the actual working-tree diff. **Round 1** returned 9 findings, all addressed; the most serious was a HIGH: the first draft subtracted the recorded cancellation fee from the captured hold even when that fee had already been charged on its own PaymentIntent — which is precisely what the pre-fix code did for this population — under-refunding the rider and collecting the fee twice.

**Round 2** re-reviewed the fixes and caught that my fix for that HIGH was itself half wrong, as a CRITICAL: on the partial-capture path `cancel_fee_payment_intent_id` holds the *booking* PI, not a second one, so treating a merely-truthy column as "collected elsewhere" would have handed the fee back on every fee-bearing cancel — after the driver had already been paid their share. A related HIGH: the column is also stamped on *failed* fee attempts (`requires_action`), so a never-collected fee would have been treated as paid. Both are now guarded by the different-PI **and** `payment_status == "paid"` condition, each with its own regression test. Round 2's three remaining findings (anomalous `captured_cents == 0` silently bucketed as "nothing to do"; `--offset` paging over a non-unique `created_at` key able to skip a row; an over-long `--ride-id` list hitting an opaque PostgREST failure) are all fixed in this commit.

Worth recording plainly: the round-1 fix looked obviously correct and was wrong in the opposite direction, on the same line. The second review pass is what caught it.
