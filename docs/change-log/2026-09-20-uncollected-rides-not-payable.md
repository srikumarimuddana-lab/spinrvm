# Change Impact & Risk Log — uncollected fares are no longer driver-payable

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 full-repo review, Phase 0 item C1) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments / drivers |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C1 |

## 1. Issue / gap identified

Every driver-payable surface summed `status='completed'` rides with **no `payment_status` predicate**, so a completed ride whose card charge failed still counted toward `payable_balance` — the number that bounds the Stripe Transfer in `routes/drivers/payouts.py` and that the weekly `utils/auto_payout.py` batch pays out with no human action. The same rides were reported as income on the CRA-facing T4A slip. Spinr takes no commission, so an uncollected fare paid out is 100% platform loss.

## 2. Root cause

`utils/payment_retry.py` gives up after `MAX_RETRIES` and parks the ride at `payment_status='failed'` without touching `driver_earnings` (deliberately — earnings attribution is separate from collection). Nothing downstream ever re-checked collection: `routes/drivers/earnings.py`, `utils/auto_payout.py`, `utils/driver_statement.py` and `routes/drivers/tax_exports.py` contained zero occurrences of `payment_status` (confirmed by grep before the change).

## 3. Fix / remediation

> **Revised 2026-09-20 after PR review** (see §11). The filter is now **flag-gated and OFF by default**, the T4A change is withdrawn, and three defects the review found are fixed. The original write-up follows, corrected in place.

New `backend/utils/payment_collection.py` defines the collected set and two helpers: a spreadable query filter `ONLY_COLLECTED_RIDES` and a post-fetch `drop_uncollected_rides()` for callers whose repository helper takes no filter dict (same pair of shapes as `utils/legacy_rides.py`). The three money queries spread the filter next to the existing `EXCLUDE_LEGACY_RIDES`; the T4A endpoints wrap `get_rides_for_driver` with the post-fetch drop. A ride that later settles re-enters the sums on its own once its status flips; nothing needs a backfill. Activity counts (`total_rides`) are untouched.

**Collected set:** `paid`, `succeeded`, `waived_admin`, `refunded`, `partially_refunded`, `disputed`, `dispute_lost` — every status a ride can hold *after* the charge succeeded. It is also now the single definition of "money came in": `routes/webhooks.py` imports it as `_SETTLED_PAYMENT_STATUSES` instead of keeping its own narrower tuple.

**Flag:** `app_settings.uncollected_rides_excluded_from_payable`, default **off**. With it off, nothing about any driver's balance changes — the mechanism ships dark. See §11 for why.

- `refunded` / `partially_refunded` stay payable: the driver keeps their pay and the platform absorbs the refund — the policy `services/ledger_service.py:190-205` and `routes/webhooks.py`'s `charge.refunded` handler already state.
- `disputed` / `dispute_lost` stay payable **on purpose** (money-auditor finding during review): a chargeback opens on a charge that *succeeded*, usually after the driver was already paid out. Because `payable_balance` is a live recompute (`total_earnings − total_payouts`), excluding these would silently pull an already-paid ride out of `total_earnings` while its payout stayed in `total_payouts` — the driver's balance would drop mid-dispute with no ledger row and no human decision. Disputes are triaged by support via `stripe_disputes`; a query filter must not auto-resolve them against the driver. **This keeps today's behaviour for disputed rides exactly**; whether Spinr should ever recover a lost chargeback from a driver is a finance/product decision, flagged here, not made here.

**Alternative considered:** an exclusion list (`NOT IN ('failed', ...)`). Rejected — an inclusion list fails in the recoverable direction (a new/unknown status temporarily under-pays, never over-pays), which is the same rule the payouts-side filter in `earnings.py` already documents for itself. `rides.payment_status` is `NOT NULL DEFAULT 'pending'`, so nothing is dropped by a NULL.

## 4. Risk & impact on existing functionality

Blast radius — every reader of driver-payable money, grepped. **All "Changed" rows below take effect only when the flag is on; with it off (the default) none of them move.**

| Caller | Effect |
|---|---|
| `routes/drivers/earnings.py::get_driver_balance` (`/drivers/balance`) | `payable_balance` / `total_earnings` / `total_tax` / incentives drop uncollected rides. **Changed.** |
| `utils/auto_payout.py::_compute_payable_balance` + `_compute_payable_balances_batch` (weekly batch, admin "Weekly Payouts" preflight) | same, via shared `_balance_from_rows`. **Changed.** |
| `utils/driver_statement.py::_build` (weekly/monthly/custom statements, admin statement PDFs, `backfill_statement_totals.py`) | ride earnings / trips / tax on a statement exclude uncollected rides. **Changed.** Re-generating an already-emailed period can show lower totals than the emailed copy if that period had a failed-charge ride. |
| `routes/drivers/tax_exports.py::get_t4a_years` / `get_t4a_summary` | **Not changed — withdrawn after review, see §11.2.** The slip would have retroactively under-reported income drivers actually received. |
| `routes/drivers/payouts.py::request_instant_payout` | reads the balance above — automatically bounded. Not edited. |
| `routes/drivers/earnings.py` display endpoints (`/earnings`, daily/weekly/monthly/trip) | **NOT changed** — they still show every completed ride, so a driver can see "earned $500 / payable $455" for a period containing a failed charge. Deliberate: those read pre-aggregated `driver_daily_stats` in places and widening the change there was out of scope. Follow-up if the divergence confuses drivers. |
| `utils/referral_payout.py` ride-count thresholds | **NOT changed** — an uncollected ride still counts toward a referral-bonus threshold. Bounded (flat bonus, not the fare); follow-up. |
| `routes/webhooks.py::_SETTLED_PAYMENT_STATUSES` | **Changed (unconditionally, not flagged)** — now imports the shared set. Widens the already-settled guard so a stale `payment_failed` can no longer relabel a `partially_refunded`/`disputed`/`dispute_lost` ride as `failed`. See §11.4. |
| `routes/payments.py::confirm_payment` | **Changed (unconditionally)** — persists `paid` instead of the raw Stripe `succeeded`. See §11.3. |
| `routes/admin/rides.py` terminal-state invoice guard | **Not changed** — a third spelling of the same set; widening it would stop admins invoicing disputed rides. Follow-up. |
| Rides paid by wallet (`settle_wallet` RPC), corporate allowance/master wallet (`settle_corporate`), card (`settle_card` / `_settle_against_hold` / `_finalize_card_settlement`), admin waive (`routes/admin/rides.py`), invoice/PI-succeeded webhooks | all land on `paid` / `waived_admin` on success (each write verified during review), so collected rides are unaffected. |

Money/state-machine: no wallet deltas, no Stripe calls, no ride-state writes. Background loops: `auto_payout` (weekly) and `driver_earnings_statements` read the changed formula; no loop is added or re-timed.

Known interaction: a card charge that succeeded but whose `payment_status='paid'` write failed sits at `processing` (outside the set) until `utils/stripe_reconcile.py`'s stuck-`processing` detector heals it — and auto-heal (`stripe_auto_heal_processing`) is default-off, detection only. Before this change such a ride was (wrongly) payable immediately; now it is payable once healed. Fails in the recoverable direction.

Regression risk: a legitimately collected ride carrying a `payment_status` outside the set would become non-payable. Grep of every `"payment_status": "<literal>"` write to **rides** found only `pending`, `paid`, `failed`, `processing`, `held_for_review`, `waived_admin`, `retrying`, `disputed`, `dispute_lost`, `refunded`, `partially_refunded` (`past_due` is written to `driver_subscriptions` only); none of the excluded ones represent collected money.

## 5. User-experience effect

- **Driver:** **none while the flag is off**, which is how this ships — no balance, payout amount or statement changes on deploy. With the flag on: `payable_balance`, the weekly auto-payout amount and statement totals drop by the value of any completed ride whose fare was never collected, visible mid-session on the next `/drivers/balance` poll. A driver already paid out for such a ride would go negative — see §11.1 for what must happen before enabling. The T4A slip is unaffected either way (§11.2). No copy change.
- **Rider / corporate admin:** none.
- **Internal admin:** "Weekly Payouts" preflight amounts and driver statement PDFs change accordingly.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/payment_collection.py` | new: `COLLECTED_PAYMENT_STATUSES`, `ONLY_COLLECTED_RIDES`, `is_collected`, `drop_uncollected_rides` | one definition of "collected" for every payable surface |
| `backend/routes/drivers/earnings.py` | spread `ONLY_COLLECTED_RIDES` into the `/balance` money query | stop paying uncollected fares |
| `backend/utils/auto_payout.py` | same, in both the single-driver and batched ride fetch | weekly batch + admin preflight |
| `backend/utils/driver_statement.py` | same, in the statement ride fetch | statements report payable income only |
| `backend/routes/drivers/tax_exports.py` | `drop_uncollected_rides(...)` around both `get_rides_for_driver` results | T4A reports income received only |
| `backend/tests/test_uncollected_rides_not_payable.py` | new: mock `get_rows` enforces the predicate; asserts balance / batch / statement / T4A exclude `failed`/`pending` rides and that the filter is sent | regression pin |
| `backend/scripts/reconcile_uncollected_ride_payouts.sql` | new: read-only per-driver report of payouts not backed by collected fares | finance hand-off; no claw-back |
| `docs/change-log/2026-09-20-uncollected-rides-not-payable.md` | this file | |

## 7. Before / after

```python
# Before — routes/drivers/earnings.py (auto_payout.py / driver_statement.py identical shape)
rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES},
    limit=10000,
)
```

```python
# After
rides = await db_supabase.get_rows(
    "rides",
    {"driver_id": driver["id"], "status": RideStatus.COMPLETED, **EXCLUDE_LEGACY_RIDES, **ONLY_COLLECTED_RIDES},
    limit=10000,
)
# ONLY_COLLECTED_RIDES == {"payment_status": {"$in": [
#     "paid", "waived_admin", "refunded", "partially_refunded", "disputed", "dispute_lost"]}}
```

Concrete scenario: driver has one paid ride ($42.50 + $3.40 GST) and one ride whose card declined three times (`failed`, $45.00 + $3.60). Before: `payable_balance = 94.50`, auto-payout transfers $94.50. After: `payable_balance = 45.90`; the $48.60 becomes payable only if the rider's charge later succeeds. A ride that was paid, paid out, and then charged back (`disputed`) is unchanged before/after.

## 8. Rollback plan

**Primary lever is the flag, no deploy:** set `app_settings.uncollected_rides_excluded_from_payable` to false via the admin dashboard and every balance returns to its previous value on the next 60s settings-cache expiry. The unflagged parts (the `webhooks.py` guard widening and the `payments.py` status mapping) are code-only with no data written: revert the commit. Balances already *not* paid out because of the filter are still owed and pay out on the next cycle after revert, so the rollback direction is the safe one. Payouts made **before** this change for uncollected rides are not touched by either direction — that is what the reconciliation report is for.

## 9. Verification performed

- `ruff check` + `ruff format --check` clean on every Python file touched; `python -m py_compile` clean.
- Static review of every settlement path's terminal write (`services/payment_service.py`, `routes/webhooks.py`, `routes/admin/rides.py`) confirming collected rides land inside the set.
- `spinr-money-auditor` run against the diff before commit; its two blockers (dispute states, T4A path) are folded in above; its warnings (referral thresholds, `stripe_reconcile` interaction, `past_due` scoping) are recorded in §4.
- New tests written to the repo's existing patch conventions (`test_earnings_coverage.py`, `test_auto_payout.py`, `test_driver_statement.py`, `test_p2_payout_t4a.py`). The mock `get_rows` applies the `$in` predicate, so dropping the spread from any call site fails the dollar assertion, not just a dict-shape check.

## 10. What was NOT verified

- **The pytest suite was not run in this session**: the remote sandbox's network policy returns 403 for PyPI, so `pip install -r requirements.txt` fails and `pytest`/`fastapi` are absent. CI (`ci.yml`, real Postgres) runs on the PR — treat the new test file, `test_p2_payout_t4a.py`, and the parity tests in `test_auto_payout.py` as the gate; do not merge on a red run.
- The reconciliation SQL was not executed against any database (no production access from this session); it was written against `supabase_schema.sql` column names and should be dry-run on a read replica first.
- No load/perf check: the added predicate is an `IN` on a text column inside the same query, so no new round trip.


---

## 11. Revision after PR review (2026-09-20)

Review on [#5599](https://github.com/srikumarimuddana-lab/spinrvm/pull/5599) found six issues in this change. All six were verified against the code before acting. Four are fixed here; two are withdrawn scope.

### 11.1 Fixed — the filter is now flag-gated (was: silent retroactive clawback)

`payable_balance` is a live recompute (`total_earnings − total_payouts`) with **no floor**. Filtering uncollected rides shrinks `total_earnings`, but a payout already sent for such a ride stays in `total_payouts`. Every driver paid out under the old behaviour would have gone negative the moment this deployed, and each future collected fare would silently net against that overpayment until they climbed back to zero — an automatic clawback with no ledger row and no human decision. `driver-app/app/driver/payout.tsx` renders the value through `formatCurrency` with no clamp, so a live driver would have seen `$-3.10` on the payout screen.

That is exactly what this change's own `scripts/reconcile_uncollected_ride_payouts.sql` refuses to build, and the same argument this module makes for keeping `disputed` rides payable — applied to disputes but not to this change's own migration cohort.

Fix: `payable_ride_filter()` gates the predicate behind `app_settings.uncollected_rides_excluded_from_payable`, default off, failing **closed** (no filter) if the settings read fails. Ships dark; also removes the "there is no flag" weakness in §8's rollback plan — the lever is now a DB toggle, no deploy.

**Before enabling, in order:** run the reconciliation report against a read replica to size the affected cohort; decide how the existing overpayment is handled (absorb, or recover per-case — a finance decision, not a query-filter side effect); decide whether to clamp the reported balance at 0 and expose the shortfall as its own field; and resolve §11.5.

### 11.2 Withdrawn — the T4A change is reverted

The slip was made to report collected fares only. For the cohort already paid out for an uncollected ride that is **backwards**: a real Stripe Transfer reached their bank, so it is their income regardless of whether Spinr collected from the rider, and `get_t4a_summary` is generate-on-fetch with no versioning, so it would have retroactively rewritten closed tax periods. Two sub-findings confirmed alongside it: `total_trips` would have dropped uncollected trips (contradicting this change's own rule that activity counts stay whole), and `get_t4a_years` would have hidden a year entirely when all its rides were uncollected.

The correct predicate for a tax slip is **what the driver was paid**, not what was collected — those coincide going forward but not for the existing cohort, and linking payouts to rides does not exist today. `routes/drivers/tax_exports.py` is reverted to its pre-PR state; this needs tax advice, not a query filter.

### 11.3 Fixed — `succeeded` was written to `rides.payment_status`

`routes/payments.py::confirm_payment` wrote the raw Stripe `PaymentIntent.status`, and that write sits *outside* the `if intent.status == "succeeded"` block (which only guards the underpay 402), so it ran unconditionally. `succeeded` is a value no reader knew. Latent — no first-party caller of `POST /payments/confirm` exists (grepped across all four client surfaces) — but the route is mounted and reachable, and `tests/routes/test_payments.py` pinned `"succeeded"` as *correct*, so nothing would have caught it drifting into use.

Fixed on both sides: the write maps to the canonical `paid`, and the collected set carries `succeeded` so any row written before the fix still reads as collected. The test assertion is inverted with a comment explaining it had pinned the bug open.

Method note for §4's completeness claim: the original sweep matched only the *literal* form `"payment_status": "<value>"`, so it structurally could not see `"payment_status": intent.status`. A dynamic-write sweep confirms line 749 was the only unmapped one.

### 11.4 Fixed — the "mirrors `_SETTLED_PAYMENT_STATUSES`" claim was false

`routes/webhooks.py` defined its own `("paid", "waived_admin", "refunded")` — three values, not this module's set. The divergence was load-bearing: that tuple is the guard deciding whether an incoming `payment_intent.payment_failed` is a stale redelivery, so a ride at `partially_refunded`, `disputed` or `dispute_lost` missed it, fell through to the `else`, and was CAS'd to `payment_status="failed"` — turning a collected ride into an uncollected one. A third spelling exists at `routes/admin/rides.py` (terminal-state invoice guard, four values).

`webhooks.py` now imports `SETTLED_PAYMENT_STATUSES` from this module, and a test pins the two sets equal so the guard cannot silently narrow again. `admin/rides.py` is deliberately **not** changed — widening it would stop admins invoicing disputed rides, a behaviour change beyond this PR's scope; recorded as follow-up.

### 11.5 Open — `/balance` vs `/earnings` diverge when the flag is on

`get_driver_earnings` and the daily/weekly/monthly/trip endpoints do not carry the filter, so with the flag **on** a driver sees two different totals for the same period. `ACTION_ITEMS.md` A28 (decided 2026-08-12) says balance should match earnings' composition. Not a problem while the flag is off; it is a precondition for turning it on, recorded in the flag's own docstring rather than left to drift.

### 11.6 Fixed (in the C2 commit's file) — a falsy CAS result is not conclusively "we lost"

Covered in `docs/change-log/2026-09-20-search-timeout-cas-before-stripe.md` §11.
