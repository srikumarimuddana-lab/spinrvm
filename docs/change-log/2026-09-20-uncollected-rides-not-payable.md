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

New `backend/utils/payment_collection.py` defines the collected set and two helpers: a spreadable query filter `ONLY_COLLECTED_RIDES` and a post-fetch `drop_uncollected_rides()` for callers whose repository helper takes no filter dict (same pair of shapes as `utils/legacy_rides.py`). The three money queries spread the filter next to the existing `EXCLUDE_LEGACY_RIDES`; the T4A endpoints wrap `get_rides_for_driver` with the post-fetch drop. A ride that later settles re-enters the sums on its own once its status flips; nothing needs a backfill. Activity counts (`total_rides`) are untouched.

**Collected set:** `paid`, `waived_admin`, `refunded`, `partially_refunded`, `disputed`, `dispute_lost` — every status a ride can hold *after* the charge succeeded.

- `refunded` / `partially_refunded` stay payable: the driver keeps their pay and the platform absorbs the refund — the policy `services/ledger_service.py:190-205` and `routes/webhooks.py`'s `charge.refunded` handler already state.
- `disputed` / `dispute_lost` stay payable **on purpose** (money-auditor finding during review): a chargeback opens on a charge that *succeeded*, usually after the driver was already paid out. Because `payable_balance` is a live recompute (`total_earnings − total_payouts`), excluding these would silently pull an already-paid ride out of `total_earnings` while its payout stayed in `total_payouts` — the driver's balance would drop mid-dispute with no ledger row and no human decision. Disputes are triaged by support via `stripe_disputes`; a query filter must not auto-resolve them against the driver. **This keeps today's behaviour for disputed rides exactly**; whether Spinr should ever recover a lost chargeback from a driver is a finance/product decision, flagged here, not made here.

**Alternative considered:** an exclusion list (`NOT IN ('failed', ...)`). Rejected — an inclusion list fails in the recoverable direction (a new/unknown status temporarily under-pays, never over-pays), which is the same rule the payouts-side filter in `earnings.py` already documents for itself. `rides.payment_status` is `NOT NULL DEFAULT 'pending'`, so nothing is dropped by a NULL.

## 4. Risk & impact on existing functionality

Blast radius — every reader of driver-payable money, grepped:

| Caller | Effect |
|---|---|
| `routes/drivers/earnings.py::get_driver_balance` (`/drivers/balance`) | `payable_balance` / `total_earnings` / `total_tax` / incentives drop uncollected rides. **Changed.** |
| `utils/auto_payout.py::_compute_payable_balance` + `_compute_payable_balances_batch` (weekly batch, admin "Weekly Payouts" preflight) | same, via shared `_balance_from_rows`. **Changed.** |
| `utils/driver_statement.py::_build` (weekly/monthly/custom statements, admin statement PDFs, `backfill_statement_totals.py`) | ride earnings / trips / tax on a statement exclude uncollected rides. **Changed.** Re-generating an already-emailed period can show lower totals than the emailed copy if that period had a failed-charge ride. |
| `routes/drivers/tax_exports.py::get_t4a_years` / `get_t4a_summary` (and their callers `download_t4a_pdf`, `export_earnings`) | T4A income and the year list exclude uncollected rides. **Changed** — added during review; same bug, CRA-facing. |
| `routes/drivers/payouts.py::request_instant_payout` | reads the balance above — automatically bounded. Not edited. |
| `routes/drivers/earnings.py` display endpoints (`/earnings`, daily/weekly/monthly/trip) | **NOT changed** — they still show every completed ride, so a driver can see "earned $500 / payable $455" for a period containing a failed charge. Deliberate: those read pre-aggregated `driver_daily_stats` in places and widening the change there was out of scope. Follow-up if the divergence confuses drivers. |
| `utils/referral_payout.py` ride-count thresholds | **NOT changed** — an uncollected ride still counts toward a referral-bonus threshold. Bounded (flat bonus, not the fare); follow-up. |
| `routes/webhooks.py::_SETTLED_PAYMENT_STATUSES` | untouched duplicate of the core set; the new module names it. Follow-up: import from `payment_collection`. |
| Rides paid by wallet (`settle_wallet` RPC), corporate allowance/master wallet (`settle_corporate`), card (`settle_card` / `_settle_against_hold` / `_finalize_card_settlement`), admin waive (`routes/admin/rides.py`), invoice/PI-succeeded webhooks | all land on `paid` / `waived_admin` on success (each write verified during review), so collected rides are unaffected. |

Money/state-machine: no wallet deltas, no Stripe calls, no ride-state writes. Background loops: `auto_payout` (weekly) and `driver_earnings_statements` read the changed formula; no loop is added or re-timed.

Known interaction: a card charge that succeeded but whose `payment_status='paid'` write failed sits at `processing` (outside the set) until `utils/stripe_reconcile.py`'s stuck-`processing` detector heals it — and auto-heal (`stripe_auto_heal_processing`) is default-off, detection only. Before this change such a ride was (wrongly) payable immediately; now it is payable once healed. Fails in the recoverable direction.

Regression risk: a legitimately collected ride carrying a `payment_status` outside the set would become non-payable. Grep of every `"payment_status": "<literal>"` write to **rides** found only `pending`, `paid`, `failed`, `processing`, `held_for_review`, `waived_admin`, `retrying`, `disputed`, `dispute_lost`, `refunded`, `partially_refunded` (`past_due` is written to `driver_subscriptions` only); none of the excluded ones represent collected money.

## 5. User-experience effect

- **Driver:** `payable_balance` in the app and the weekly auto-payout amount drop by the value of any completed ride whose fare was never collected; statements and the T4A slip for such periods show fewer trips / lower income. Visible mid-session to a driver already online (next `/drivers/balance` poll). No copy change.
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
| `backend/tests/test_p2_payout_t4a.py` | `_ride_row` fixture gains `payment_status: "paid"` | real rows always carry it; the slip now filters on it |
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

Pure query-filter change with no data written: revert the commit (a deploy — there is no flag). Balances already *not* paid out because of the filter are still owed and pay out on the next cycle after revert, so the rollback direction is the safe one. Payouts made **before** this change for uncollected rides are not touched by either direction — that is what the reconciliation report is for.

## 9. Verification performed

- `ruff check` + `ruff format --check` clean on every Python file touched; `python -m py_compile` clean.
- Static review of every settlement path's terminal write (`services/payment_service.py`, `routes/webhooks.py`, `routes/admin/rides.py`) confirming collected rides land inside the set.
- `spinr-money-auditor` run against the diff before commit; its two blockers (dispute states, T4A path) are folded in above; its warnings (referral thresholds, `stripe_reconcile` interaction, `past_due` scoping) are recorded in §4.
- New tests written to the repo's existing patch conventions (`test_earnings_coverage.py`, `test_auto_payout.py`, `test_driver_statement.py`, `test_p2_payout_t4a.py`). The mock `get_rows` applies the `$in` predicate, so dropping the spread from any call site fails the dollar assertion, not just a dict-shape check.

## 10. What was NOT verified

- **The pytest suite was not run in this session**: the remote sandbox's network policy returns 403 for PyPI, so `pip install -r requirements.txt` fails and `pytest`/`fastapi` are absent. CI (`ci.yml`, real Postgres) runs on the PR — treat the new test file, `test_p2_payout_t4a.py`, and the parity tests in `test_auto_payout.py` as the gate; do not merge on a red run.
- The reconciliation SQL was not executed against any database (no production access from this session); it was written against `supabase_schema.sql` column names and should be dry-run on a read replica first.
- No load/perf check: the added predicate is an `IN` on a text column inside the same query, so no new round trip.
