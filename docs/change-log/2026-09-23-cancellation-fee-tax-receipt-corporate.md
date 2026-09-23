# Change Impact & Risk Log — cancellation fee: tax, receipts, corporate billing

> Filled from `docs/templates/CHANGE_IMPACT_LOG.md`. Source: 2026-09-22 proactive
> fleet audit (`spinr-regulatory-compliance-checker` + `spinr-corporate-billing-reviewer`).

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code (AI-assisted), session_01L8WBp4c4HHd7Pq8iysPZuQ |
| Surface(s) | backend (rider-app / driver-app / admin-dashboard: no code change; rider-visible API output changes, see §5) |
| Domain (Sentry tag) | payments / corporate / rides |
| PR / commit link | branch `fix/cancellation-fee-tax-receipt-corporate` — commits `c299545`, `7ca4f92`, `bb6e775`, `967c26d`, `f47c35c`, `2554f67` (+ this log). Not pushed. |
| Related issue or gap ID | 2026-09-22 fleet audit: BLOCKER stale cancelled-ride receipt; BLOCKER untaxed cancellation fee; MEDIUM corporate fee paid-to-driver-but-never-billed |

## 1. Issue / gap identified

Three linked gaps in how a cancelled ride's actual charge (the cancellation / no-show fee) is computed, taxed, billed and displayed:

1. **Stale receipt (BLOCKER).** Every receipt surface for a cancelled ride rendered the booking-time quote. That covers the JSON `/receipt`, the receipt PDF behind rider-app's "Download invoice", the email receipt, and `GET /rides/{id}` and `/rides/history` (rider-app's Activity list shows `grand_total`). A rider charged a $4.50 fee saw "Ride fare (12 km) $18.40 · GST $0.92 · total $19.32" next to a separate `cancellation_fee: 4.50`. That reads as a hidden or duplicate charge.
2. **Untaxed fee (BLOCKER).** `services/cancellation_service.py` computes no GST/PST on cancellation or no-show fees, and nothing discloses that the fee is untaxed.
3. **Corporate write-off (MEDIUM).** For `payment_method == "company_allowance"`, both fee paths (`routes/rides/cancellation.py` rider cancel and `routes/drivers/ride_cancel.py` no-show) billed nobody. `pay_driver_cancellation_fee` still paid the driver from Spinr's funds, and there was no tracking row.

## 2. Root cause

- A cancel writes only `status`, `cancelled_at`, `cancellation_fee_admin` and `cancellation_fee_driver`. The quote columns (`total_fare`, `grand_total`, `tax_amount`, `tax_breakdown`, `base_fare`/`distance_fare`/`time_fare`, `fare_breakdown_snapshot`) keep the booking-time values. Every renderer (`_shared._build_fare_breakdown`, `receipt_pdf._fare_lines`, `email_receipt._build_fare_rows`/`_receipt_total`, the `queries.py` overlays) is status-agnostic and reads those columns.
- The fee functions return an `(admin, driver)` split with no tax step. The tax logic lives only in the fare path (`features.calculate_all_fees`).
- Corporate fee billing was explicitly left "not wired up" (code comments in both cancel paths and `calculate_scheduled_cancel_notice_fee`). The driver payout had no matching guard.
- Same family, found during this work: `utils/ledger_projection.py` did not special-case `source="noshow_fee"`. Once a no-show stamped `payment_status="paid"`, the fee's ledger event decomposed against the ride row's stale quote (`tax_amount` 2.20 / `driver_earnings` 15.00 against a $4.50 fee).

## 3. Fix / remediation

**Tax — the tax-inclusive interpretation, implemented and flagged for confirmation.** This is **a policy decision encoded in code, not an obviously correct fix.** It assumes a cancellation/no-show fee is consideration for a taxable supply and attracts the same GST/PST/HST as the fare in that service area. **It needs human/legal (tax-advisor) confirmation of both taxability and rate before the flag is turned on.**
- New `compute_cancellation_fee_tax(fee, settings, area)` reuses `features.calculate_all_fees` (canonical per-area GST/PST/HST enablement and rates, each tax quantized HALF_UP). It passes `_matched_area=area, _area_fees=[]`, so only tax is computed: no airport/night surcharges, no DB reads.
- Gated by the new `app_settings` flag **`cancellation_fee_tax_enabled`, default OFF**. With it off, every payload is byte-identical to before (tests pin this).
- With it on, the **collected** amount is fee + tax in both paths and every collection method (hold partial capture, excess-capture refund threshold, wallet debit, fresh card charge, corporate debit). `cancellation_fee_admin`/`_driver` keep their pre-tax meaning, so driver payout, statements and auto-payout are unchanged.
- The tax is persisted to new nullable columns `rides.cancellation_fee_tax_amount` / `cancellation_fee_tax_breakdown` (migration 455). This is a separate best-effort write, never merged into the critical cancel update.
- Ledger metadata gains `fee_tax`. `ledger_projection` books it to `tax_payable`, and `noshow_fee` now uses the fee-split decomposition too.
- The pre-cancel disclosure (`GET /rides/{id}` → `cancellation_fee`) adds the same tax, so the rider is told the amount that will be charged.

**Receipts — approach (b), additive read-time overlay.** Nothing on the ride row is overwritten. The new pure helper `utils/cancellation_receipt.cancellation_charge(ride)` derives the actual charge from `cancellation_fee_admin + _driver`, the migration-455 tax columns, and `scheduled_notice_fee_amount` (what was actually collected). Every renderer branches on `status == "cancelled"`:
- JSON `/receipt`: `fare_breakdown`/`grand_total`/`total_charged`/`tax_amount`/`tax_breakdown` come from the charge. Trip components are zeroed, `tip_amount` is 0, `tax_note` is None, and the fare-lock snapshot is ignored. `cancellation_fee` keeps its pre-tax meaning, and a new `cancellation_fee_tax` field is added.
- Receipt PDF and email (body rows and subject total).
- `GET /rides/{id}` and `/rides/history` response overlay (`fare_breakdown`, `grand_total`).

**Why (b) and not (a) overwriting the row:** other code reads a cancelled ride's quote columns and expects the quote:
- `ledger_projection`'s fare branch
- `scripts/reconcile_cancelled_captured_refunds.py` (fee vs captured amount)
- admin ride detail (`routes/admin/rides.py`)
- analytics
- the email/PDF `grand_total` fallbacks

Overwriting would silently repurpose columns mid-session (CLAUDE.md gate 2). A read-time branch is revertible by deploy and touches no live data.

**Corporate billing — the real fix, flag-gated, with an always-on write-off record.** New `bill_corporate_cancellation_fee(...)`, called from both fee paths for `company_allowance` rides before the driver payout:
- It debits the company **master wallet** via the existing `corporate_wallet_service.apply_adjustment`, which goes through the `corporate_wallet_apply_delta` row-locking RPC. It uses `ride_id=ride_id`, so migration 297's ride-scoped dedup makes replays idempotent, and `floor=0`, the same as `settle_corporate`. No new RPC, no new wallet type, no migration.
- Gated by the new flag **`corporate_cancellation_fee_billing_enabled`, default OFF** (a new debit visible to corporate admins), and by the existing `corporate_billing_enabled` kill switch.
- Whenever it does **not** bill, it writes a queryable `audit_logs` row `action='corporate_cancellation_fee_unbilled'` with `details.reason` ∈ {`billing_flag_off`, `corporate_billing_disabled`, `no_wallet`, `debit_failed`, `no_corporate_account`}, plus amount, driver share, company and source. Reasons: flag off, kill switch, no wallet, debit failure (e.g. `wallet_below_floor`), or no company on the ride. So even with the flag off, finance can now see every per-ride gap. The driver is still paid (unchanged); the fee is owed to them either way.

## 4. Risk & impact on existing functionality

**Why three concerns ship in one PR.** They share the same root files and data:
- The tax amount must flow into the same `total_cancel_fee` that the card, wallet, hold and corporate paths collect.
- The corporate debit must bill that tax-inclusive total.
- The receipt fix must read the tax columns the tax fix writes.

Shipping tax without the receipt fix would raise the charge while receipts still show the quote. Shipping the receipt fix first would need rework once tax columns exist. Shipping corporate billing separately would bill the pre-tax amount and need a second pass. So there are six scoped commits, one per concern and path, in one branch, with shared tests.

**Blast radius: cross-surface (backend API output consumed by rider-app), money-path, corporate ledger.**

| Concern | What else reads/writes it | Regression risk |
|---|---|---|
| `total_cancel_fee` now + tax (flag on) | `capture_cancellation_fee` (hold cap), `refund_excess_capture(fee_owed=…)`, `wallet_apply_delta` (clamp), `charge_ancillary_fee`, `record_ledger_event`, cancel response `cancellation_fee` | Flag OFF ⇒ identical. Flag ON ⇒ riders are charged more (intended, pending legal). Captured-refund path refunds the excess above fee + tax, which is correct. |
| `cancellation_fee_admin/_driver` | driver statements, `auto_payout`, `drivers/earnings.py`, `ride_reads.py`, `lifecycle.py`, `ride_complete.py`, reconcile script, rider-app ride-details | **Unchanged meaning (pre-tax)**. Deliberately not repurposed. |
| New columns (migration 455) | Written only when tax > 0; read only by `utils/cancellation_receipt.py` | If the flag is flipped before the migration, the tax write fails loudly (error log) and the cancel still succeeds; the receipt shows the untaxed fee. |
| `ledger_projection` `cancellation_fee` / `noshow_fee` | double-entry projection loop (a `lifespan.py` background loop) | `fee_tax` absent ⇒ 0 (historical rows unchanged). **Behavior change:** future `noshow_fee` events decompose from metadata (driver_payable/platform) instead of the stale ride quote. Historical projected rows are not re-projected. |
| Corporate master wallet | `corporate_wallet_apply_delta` shared with `settle_corporate` master fallback (type `adjustment`), `manual_adjust` (no ride_id), winddown (no ride_id) | Dedup key is (wallet, ride_id, `adjustment`, master). A cancelled ride can never reach `settle_corporate` (both entry points require `status == completed`: `payment_service.py` guest auto-settle and `routes/rides/payments.py`), so no collision. Flag OFF ⇒ no money moves. |
| Receipt renderers | `payment_service.send_ride_receipt` (email + PDF), admin send-receipt, outbox receipts, rider-app PDF download, JSON `/receipt` (no app consumer found) | Status-gated: **completed rides are untouched** (existing receipt suites green). |
| `get_ride` / `history` overlay | rider-app Activity list (`grand_total`), ride-details (uses `cancellation_fee_admin+_driver` for cancelled, not `fare_breakdown`) | Cancelled rows now show the fee instead of the quote. This is visible and intended; see §5. |

**Not changed / known residual gaps (explicit):**
- **The receipt shows the fee *owed*, not necessarily *collected*.** A declined card, a wallet clamp to balance, a hold capture capped below the fee, a corporate fee unbilled because the flag is off, or an unresolved excess-capture refund all still show the fee. This matches the pre-existing `cancellation_fee` field and rider-app ride-details, and `payment_status` still records failure. It is a follow-up, not introduced here.
- **Capped hold capture with tax:** ledger metadata carries the full `fee_driver` + `fee_tax` on a short collection. Tax is over-booked to `tax_payable`, or the event degrades as `fee_split_inconsistent` if the hold is < driver + tax. This is pre-existing for the driver share. It is rare, since holds are usually ≫ fee.
- **The scheduled-ride notice fee is NOT taxed** even with the flag on (ledger books it "no tax, all platform"). Taxing it is part of the same legal question and was left out deliberately to bound this diff.
- **Corporate debit type is `adjustment`**, shared with settlement fallback debits. It is distinguishable only via `notes = ride:<id>:cancellation_fee|noshow_fee`. A dedicated `corporate_wallet_transactions.type` (like `late_tip_adjustment`, migration 319) would need a CHECK-constraint migration on a live money table. Recommended follow-up.
- **Corporate debit uses `floor=0`**, like `settle_corporate`, not the company's `soft_negative_floor`. A low-balance company's fee is written off (`debit_failed`) rather than pushed negative.
- **The member allowance is not consumed**: the master wallet pays, and an `allowance_only` policy is not checked. This is a product decision to confirm.
- `routes/admin/rides.py` admin ride detail still renders the quote for cancelled rides (admin context; unchanged).
- `cancellation.py` carries a whitespace-only `ruff format` hunk on pre-existing lines. The repo's PostToolUse formatter plus the pre-commit format check force it; there is no logic change there.

## 5. User-experience effect

- **Rider (flags OFF, immediate on deploy):**
  - Cancelled-ride receipts (PDF download, email, JSON) and the Activity/history list show the actual fee (e.g. "Cancellation fee $4.50 — Total $4.50", or $0.00 for a free cancel) instead of the stale ~$19 quote. Visible mid-session for anyone browsing history.
  - New line labels: "Cancellation fee", "No-show fee", "Late cancellation fee (scheduled ride)".
  - Not screenshotted: rider-app has no visual-regression tooling. This was reasoned about from `activity.tsx:293` and `ride-details.tsx:164`.
- **Rider (`cancellation_fee_tax_enabled` ON):**
  - The fee charged rises by GST/PST, e.g. $4.50 → $4.73.
  - The pre-cancel disclosure, receipts and history show the tax-inclusive amount with GST/PST as separate lines.
  - **Flip prerequisite:** rider-app `ride-details.tsx:164` computes the cancelled fee as `admin + driver` and would still show $4.50. It needs to add `cancellation_fee_tax_amount`, which is already present in `GET /rides/{id}` because the row is `select *`. That is a rider-app change not in this backend diff.
- **Corporate admin (`corporate_cancellation_fee_billing_enabled` ON):** a new master-wallet debit per fee-bearing cancellation/no-show, with notes `ride:<id>:cancellation_fee|noshow_fee`.
- **Driver:** no change (payout amount and push copy unchanged).
- **Internal admin/finance:** new queryable `audit_logs` rows (`corporate_cancellation_fee_unbilled`).
- No notification copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/455_cancellation_fee_tax.sql` | New nullable `rides.cancellation_fee_tax_amount`, `cancellation_fee_tax_breakdown` | Persist fee tax additively for receipts |
| `backend/services/cancellation_service.py` | `compute_cancellation_fee_tax`, `bill_corporate_cancellation_fee`, `_record_corporate_fee_writeoff` | Shared tax + corporate billing for both fee paths |
| `backend/routes/rides/_deps.py` | Export the two helpers (own dual-import block) | Patchable dependency surface |
| `backend/routes/rides/cancellation.py` | Fee + tax collected; `fee_tax` ledger metadata; corporate branch; separate tax write; response includes tax | Rider-cancel path |
| `backend/routes/drivers/ride_cancel.py` | Same for the no-show path (sibling) | Same gaps on the no-show path |
| `backend/utils/ledger_projection.py` | `fee_tax` → `tax_payable`; `noshow_fee` uses the fee split | Correct double-entry for fee tax; stop the stale-quote decomposition |
| `backend/routes/rides/queries.py` | Pre-cancel disclosure + tax; cancelled overlay on `get_ride` and history | Disclosure == charge; Activity list showed the quote |
| `backend/utils/cancellation_receipt.py` (new) | Pure `cancellation_charge` / `is_cancelled` | Single source of the actual charge |
| `backend/routes/rides/receipts.py` | Cancelled branch in JSON receipt | Stale receipt |
| `backend/utils/receipt_pdf.py` | Cancelled branch in `_fare_lines` | Stale PDF (rider-app download) |
| `backend/utils/email_receipt.py` | Cancelled branch in `_build_fare_rows` / `_receipt_total` | Stale email |
| `backend/tests/test_cancellation_fee_tax_corporate.py` (new) | Helper, rider-cancel, no-show and disclosure tests | Regression coverage |
| `backend/tests/test_cancelled_ride_receipt.py` (new) | Helper, JSON, PDF, email, get_ride/history tests | Regression coverage |
| `backend/tests/test_ledger_projection.py` | +2 tests (fee tax, noshow split) | Regression coverage |

## 7. Before / after

```python
# Before — routes/rides/cancellation.py
charged_admin, charged_driver = calculate_cancellation_fee(ride, settings, area)
total_cancel_fee = _round(charged_admin + charged_driver)
...
elif payment_method == "card":
    ...  # company_allowance: nothing billed; driver still paid below
```

```python
# After
charged_admin, charged_driver = calculate_cancellation_fee(ride, settings, area)
fee_tax, fee_tax_breakdown = await _deps.compute_cancellation_fee_tax(charged_admin + charged_driver, settings, area)
total_cancel_fee = _round(charged_admin + charged_driver + fee_tax)   # == old value when flag off
...
elif payment_method == "company_allowance":
    await _deps.bill_corporate_cancellation_fee(ride=ride, ride_id=ride_id, amount=total_cancel_fee, ...)
```

```python
# Before — receipt for a cancelled ride (any surface)
fare_lines = _build_fare_breakdown(ride)          # "Ride fare (12.0 km) 18.40", "GST (5%) 0.92"
receipt_grand_total = ride.get("grand_total")     # 19.32
```

```python
# After
if is_cancelled(ride):
    charge = cancellation_charge(ride)            # "Cancellation fee 4.50" [+ "GST (5.0%) 0.23"]
    fare_lines, receipt_grand_total = charge["lines"], _f(charge["grand_total"])   # 4.50 / 4.73
```

**Dry-run scenarios (exercised with `mock_supabase_client`-style patches, CLAUDE.md gate 4):**
- Card rider cancels at `driver_arrived`, SK area GST 5%, tax flag ON:
  - Before: charged $4.50; receipt $19.32 with a separate $4.50.
  - After: charged $4.73 (ledger 473¢ = driver 400 + tax 23 + platform 50); driver credited $4.00; receipt "Cancellation fee $4.50 / GST (5.0%) $0.23 / Total $4.73".
- Company-allowance rider cancels at `driver_arrived`, both flags ON:
  - Before: driver +$4.00 from Spinr funds; company −$0; no record.
  - After: company master wallet −$4.73 (`adjustment`, ride_id-deduped); driver +$4.00.
  - Billing flag OFF: company −$0, driver +$4.00, and an `audit_logs` row `corporate_cancellation_fee_unbilled` (reason `billing_flag_off`, amount 4.73).
- Driver marks a card no-show, tax ON: fresh charge of $4.73; `noshow_fee` ledger event now splits 400/23/50 instead of decomposing against the ride's $2.20 quote tax.

## 8. Rollback plan

Rollback needs no redeploy, only `app_settings`:
- Set `cancellation_fee_tax_enabled = false`. New cancellations are charged pre-tax, exactly as before. Already-charged tax is real money and is not rolled back automatically; refund per ride via the existing refund tooling if legal rules it non-taxable. Rides are identifiable by non-null `rides.cancellation_fee_tax_amount`.
- Set `corporate_cancellation_fee_billing_enabled = false`. Corporate debits stop and write-off rows resume. To reverse an applied debit, credit the master wallet with `apply_refund(wallet_id, amount, ride_id)` (distinct `refund` dedup type), located via `corporate_wallet_transactions.notes LIKE 'ride:%:cancellation_fee'` / `'ride:%:noshow_fee'`.
- Kill switch `corporate_billing_enabled = false` also stops the corporate debit (fails closed to a write-off row).

Code-level:
- The receipt, history and ledger-projection changes are read-path and status-gated. Reverting them is a plain `git revert` + deploy with no data remediation.
- Projected `noshow_fee` ledger legs written in the meantime are correct and need no reversal.
- Migration 455 is additive; its columns can stay after a revert (NULL, unread).

Deploy order: **apply migration 455 before turning on `cancellation_fee_tax_enabled`.**

## 9. Verification performed

- [x] Automated tests run:
  - `tests/test_cancellation_fee_tax_corporate.py` (25), `tests/test_cancelled_ride_receipt.py` (18), `tests/test_ledger_projection.py` (25, +2 new).
  - Existing suites: `test_cancellation_fee_card_charge`, `test_cancel_fee_from_hold`, `test_cancel_already_captured_refund`, `test_ride_cancellation_branches`, `test_e2e_cancellation`, `test_preauth_release_on_cancel`, `test_scheduled_cancel_notice_fee`, `test_cancellation_service_driver_push`, `test_c2_driver_cancel_atomic`, `test_noshow_card_fee_collection`, `test_driver_noshow_deadline`, all `test_receipt_*`, `test_ride_receipt_delivery`, `test_admin_send_receipt_email`, `test_guest_corporate_receipt`, `test_outbox_receipts`, `test_branded_receipt_flag`, `test_coverage_rides`, `test_loguru_call_conventions`. Combined: 526 passed.
  - Full `pytest -m "not slow"` run: see the final report for its result.
- [ ] Manual repro steps followed in staging — **not done** (no staging access from this session).
- [x] Blast-radius grep performed:
  - `cancellation_fee_admin|cancellation_fee_driver`, `calculate_cancellation_fee|calculate_noshow_fee|pay_driver_cancellation_fee`
  - `apply_adjustment(` callers and their ride_id usage; `settle_corporate(` / `auto_settle_guest_corporate(` status guards
  - `_build_fare_breakdown|generate_receipt_pdf|_build_fare_rows|send_ride_receipt(` consumers
  - rider-app `receipt` / `grand_total` / `cancellation_fee` readers; ledger `source` values.
- [x] Reviewed against CLAUDE.md conventions:
  - Decimal-only money (the pre-commit money check passed on every commit).
  - Dual-import pattern, kept in separate try blocks because the repo's PostToolUse formatter strips names added to an existing `except ImportError` branch. This bit twice during this work and both cases were caught by tests.
  - Loguru conventions (scanner test green).
  - Errors never swallowed silently (every failure path is `logger.error` plus an audit row where money is involved).
  - Stripe idempotency is unchanged.
- [x] Feature-flagged: both user-visible money changes (tax, corporate debit) default OFF. The receipt/history fix is not flagged. It corrects a displayed amount to the real charge, and flagging it would keep a known-misleading receipt live.
- [x] Adversarial review: `spinr-money-auditor` / `spinr-corporate-billing-reviewer` subagents could not be spawned (no Agent tool in this session). `/code-review` at **high** effort ran against the branch diff instead and returned 10 findings:
  - Fixed: sibling surfaces `get_ride`/history still showing the quote; a rider-cancel tax error zeroing the whole fee; a possible tax-only receipt; stale `tax_note`/`tip` on the cancelled JSON receipt.
  - Documented in §4: receipt shows owed vs collected; capped-capture tax booking; notice fee untaxed; shared `adjustment` type (collision disproved, since settlement requires `completed`); `floor=0`.
  - Declined: merging the new imports into the existing dual-import tuples (the formatter-strip hazard above).

**What was NOT verified**
- Not tested against live Supabase or real Stripe. All money paths are exercised with mocks. Migration 455 was **not** applied anywhere, and `corporate_wallet_apply_delta`'s behaviour was reasoned from migration 376's SQL, not executed.
- **The taxability and rate of a cancellation/no-show fee are unconfirmed.** Someone with GST/PST expertise must sign off before `cancellation_fee_tax_enabled` is turned on. SK is currently configured GST-only (see `features.py` comment and `docs/change-log/2026-08-14-sk-pst-revert.md`).
- No visual verification: rider-app has no visual-regression tooling, so the Activity-list and PDF changes were reasoned about, not screenshotted. The PDF was generated in a test (bytes start `%PDF`) but not viewed.
- No `npm run build`: no frontend code changed.
- The rider-app tax display in `ride-details.tsx` is not updated (flip prerequisite, §5).
- Migration number 455 may collide with a parallel PR. Renumber before merge if CHECK B flags it.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (two flags + kill switch; read-path code revertible; per-ride remediation identified by column/notes)
- [x] Blast radius is stated, not assumed (§4 table)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 — cancelled-ride receipts/history change immediately on deploy)
- [ ] Legal/tax confirmation of fee taxability — **pending, required before enabling `cancellation_fee_tax_enabled`**
- [ ] Product confirmation: master wallet (not allowance) pays corporate fees; `floor=0` write-off behaviour — **pending, required before enabling `corporate_cancellation_fee_billing_enabled`**
