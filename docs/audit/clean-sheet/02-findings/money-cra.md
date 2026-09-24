# R9 — Payments, Money & CRA (Wave W2) — findings

**Lane:** R9 · **Repo HEAD:** `332de89172a0e9bee244610944cf75024617197c` (2026-09-24) · **Mode:** report-and-recommend, static read only. No live Stripe/Supabase data; Stripe MCP down (proxy 403); no state-changing commands.
**Builds on:** `rapid-baseline-2026-09-24/A5-money-ledger-cra.md` (MONEY-001..005 + CRA table), `00-history.md` HIST-001, `W0-SUMMARY.md`. IDs here continue as **MONEY-006+**; A5's cards are re-cited, not re-filed.
**Labels:** VERIFIED (read the code path / primary source fetched) · INFERRED · ASSUMED · UNKNOWN · PROPOSED.
**Written incrementally** (coordinator rule after a usage-limit stop); all sections complete. Section order: 1 Steelman · 2 Findings · 3 Closures (3.1–3.13) · 4 Scenarios · 5 CRA · 6 Invariants · 7 Rebuild Delta · 8 Wrap-up.

## 0. Contents
1. Steelman
2. Finding cards (MONEY-006+)
3. Mandatory closures (SURGE_CAP sites · corporate/scheduled exemption · `corporate_wallet_apply_delta` callers · payout ≤ collected · `tax_breakdown` siblings · FLOAT columns + dual-write plan · HIST-001 chains · dispute lifecycle · pre-auth holds · payment_retry idempotency · Stripe webhook coverage · `stripe_reconcile` invariants · `ledger_double_entry_enabled`)
4. Scenario cards — sweep-catalog §3.4 (28–38) with §4 chain
5. CRA / §4.1 table with primary-source status + what was blocked
6. §5 invariants → property-based tests
7. §7.3 Rebuild Delta — "double-entry ledger as the only money writer" (R19 hypothesis)
8. Top 5 · NOT verified · Human-only questions · Escalations for the accountant

---

## 1. Steelman — what the current design gets right (VERIFIED unless noted)

1. **One pure fare function, Decimal end to end, with an exact attribution invariant.** `calculate_fare` (`fare_service.py:184-237`) derives `driver_earnings = total − admin` so the minimum-fare uplift reconciles to the payout ledger; `driver_earnings_with_tip` (`:252-300`) is idempotent by construction after a real underpayment incident; `stripe_reconcile._attribution_mismatch` checks the invariant nightly. `platform_share` does not exist as a variable anywhere (A5) — 0 % is structural.
2. **Surge is defence-in-depth**: builder-level clamps at four sites plus a logged charge-site clamp (`booking.py:939-944`), an HMAC `estimate_token` that locks the shown multiplier to the charged one (P0-4), corporate bypass ordered *after* token resolution so mode-switching cannot bill a company at surge, and unconditional scheduled-ride exemption. Manual overrides > 2.5× are audit-rowed and never reach a fare.
3. **Three-layer Stripe idempotency is real, not prose**: SDK keys embed amount; `claim/unclaim_stripe_event` is used correctly (B42's loss was the missing unclaim, now fixed); DB CAS on `payment_status` and on `payment_retry_count`; refunds got an atomic cumulative RPC (`apply_stripe_refund_cumulative`) with a Stripe-id-derived `dedupe_key`; dispute close events are keyed on balance-transaction ids (N4).
4. **Holds are sized honestly** (`RIDE_AUTH_BUFFER_CAD = 0.00`): the rider's card is reserved for exactly the quote; tips ride on incremental authorization or a second PI; sub-$0.50 overflow is refused rather than silently dropped; three loops (`preauth_capture`, `orphaned_hold_reconciler`, `stripe_reconcile` 3g) close the hold lifecycle with strict locks + CAS + idempotent cancels.
5. **The ledger is append-only and never fails a payment**: `financial_events` UPDATE blocked, DELETE only inside the retention purge via a transaction-local GUC (migration 289); `record_event` retries, treats duplicate-key as success, escalates to Sentry, never raises; legs are a derived projection with a degraded-but-balanced fallback so the queue cannot wedge.
6. **Trust-first money policy is explicit and documented**: uncollected fares, refunds, lost disputes and uncollectable tips are platform-funded with the driver kept whole (`payment_collection.py`, 2026-09-21 change-log, 2026-08-17 tip decision). It is the right product call for a 0 %-commission brand; this lane's criticism (MONEY-010) is that it is unmeasured, not that it exists.
7. **Reconciliation exists in two loops and eleven checks** — well beyond a minimal job — each traceable to an incident (C10, C86, CRIMSON-SMOKE-7445, B27). The gaps in §3.3 are population mismatches, not absence.
8. **Tax plumbing is per-area and Decimal**: GST/PST/HST toggles, quantized independently, receipts itemise from a persisted `tax_breakdown`, and the corporate statement *alerts* when it has to fall back. The SK PST question is stuck on access, not on engineering.
9. **Driver tax posture is coherent**: BN required before any payout; SIN Vault-encrypted before Stripe onboarding; T4A sums income not gross fare and deliberately excludes collected tax; Connect ledgers are explicitly "not income records" (`stripe_connect_ledger_service.py:11-20`) so nothing double-counts.


---

## 2. Finding cards

A5's MONEY-001..005 stand as filed (re-verified: MONEY-001 lines `earnings.py:490,964` unchanged at HEAD; MONEY-004 hook still WARNING-only). New cards:

### MONEY-006 — Float money arithmetic on the canonical fare-estimate path, outside every gate
- Hierarchy: L2 Ride Completion & Payments › L3 Fare finalization › L4 S-pay-01 › L5 "grand_total shown to rider before booking"
- Severity: HIGH   Priority score: 4×4×3 = 48
- Status: VERIFIED   Existing item: new (HIST-001 class; not B28–B36; `grep "grand_total = round"` ACTION_ITEMS = 0)
- Adversary: auditor / plaintiff's lawyer (quote ≠ charge by a cent), regulator (tax line derived from a float total)
- Evidence: `backend/features.py:900` `grand_total = round(subtotal + fees_result["fees_total"] + fees_result["tax_amount"], 2)` where `subtotal = _fare_f(fb.total_fare)` (`:898`), `fees_total`/`tax_amount` are `float(...)` (`:807,841`); `taxable_amount = subtotal_d + fees_total` (`:818`) is Decimal built from that float. `compute_fare_estimate` is the "Canonical fare pipeline" (`:846-852`) used by `GET /rides/fare-estimate`, `services/company_booking_service.py:99` and `routes/corporate_company_bookings.py:348` (the corporate rides are *inserted* from this dict, `company_booking_service.py:208-210`). `.semgrep/spinr-rules.yml:97-110` does not include `features.py`; pre-commit check 6 is a warning.
- What happens: a corporate guest ride's persisted `grand_total`, and every rider's pre-booking estimate, is a binary-float sum rounded with Python `round()` (banker's rounding on exact halves) — CLAUDE.md forbids both. Divergence from the Decimal booking path is ≤ 1¢ per ride but systematic, and lands in a NUMERIC column and in corporate statements.
- Root cause: the file predates the service-layer extraction; the gate allowlist was built from `git grep float(` in the money *modules*, not from the call graph of `calculate_fare`.
- Recommendation: build `grand_total = _round(fb.total_fare + _d(fees_total) + _d(tax_total))` in Decimal and return `_fare_f()` only at the boundary; add `backend/features.py` (or split the fare functions out of it) to the SR-03 allowlist after running semgrep. Alternative considered: leave, since booking re-prices in Decimal — rejected because corporate bookings persist this value directly.
- Blast radius: `compute_fare_estimate` callers above; `calculate_all_fees` callers (`booking.py`, `_shared.py`, `estimates.py`) consume `fees_total`/`tax_amount` floats and re-`_d()` them — unchanged.
- Rollout: additive (Decimal internally, same JSON floats). Rollback: revert (no data migration; already-written corporate `grand_total`s stay as they are).
- Verification to close: unit test with fees 0.1 + 0.2 shape asserting `grand_total == Decimal` sum; semgrep run over `features.py` = 0 findings.

### MONEY-007 — Completion-time re-pricing changes the fare but keeps the booking-time GST/PST
- Hierarchy: L2 Ride Completion & Payments › L3 Fare finalization › L4 S-pay-01 › L5 "actual distance differs from planned"
- Severity: HIGH   Priority score: 4×3×4 = 48 (every non-fare-locked ride whose distance differs by > 0.1 km)
- Status: VERIFIED (code) / UNKNOWN (whether `fare_lock_enabled` is on in production — if on, the path is dormant)   Existing item: adjacent to B36 (`ACTION_ITEMS.md:7993`, float only); the tax gap is new
- Adversary: regulator (GST ≠ 5 % of consideration on the receipt), plaintiff's lawyer (rider over-charged tax on a shorter trip, driver under-collected on a longer one)
- Evidence: `routes/drivers/ride_complete.py:592-599` calls `recalculate_fare_for_distance` unless `fare_lock_enabled`; `services/fare_service.py:454-467` recomputes `total_fare`/`driver_earnings` then `tax_amount = _d(ride.get("tax_amount", 0))` and `new_grand_total = new_total_fare + area_fees_total + tax_amount − discount` — tax and `tax_breakdown` are the booking-time values; percentage/per-km area fees likewise (`area_fees_total` reused).
- What happens: a 12 km estimate that runs 16 km (the exact 2026-07-29 incident scale in CLAUDE.md) charges the new fare with the old GST: receipt shows "GST (5%)" that is not 5 % of the subtotal above it; driver is paid `tax_amount` (§3.4) that no longer matches what they must remit.
- Root cause: `recalculate_fare_for_distance` is a fare function, not a full re-run of `calculate_all_fees`; tax was never part of its contract.
- Recommendation: recompute tax and percentage/per-km fees from the ride's own persisted rates (`tax_breakdown[label].rate`, `area_fees_breakdown[].calc_mode`) inside the same function, keeping a clamp so tax never *decreases* below zero; or make `fare_lock_enabled=true` the documented production posture and delete the branch. Alternative: re-fetch area config at completion — rejected (rates may have changed since booking; the rider was quoted booking-time rates).
- Blast radius: single caller (`ride_complete.py:596`); readers of `tax_amount` (`auto_payout._ride_tax`, statements, receipts, T4A exclusion) all benefit.
- Rollout: behind `app_settings.completion_reprice_tax` default off → on after a staging ride; Rollback: flag off. Historic rides need a one-off report, not a rewrite (audited adjustment only).
- Verification to close: property test — for any ride, after `recalculate_fare_for_distance`, `tax_breakdown[GST].amount == _round(0.05 × (total_fare + fees))`.

### MONEY-008 — Referral bonuses written as `float` into `driver_bonuses.amount NUMERIC(10,2)`
- Hierarchy: L2 Promotions & Loyalty › L3 Referral codes › L4 S-promo-04 › L5 "referral reward credited to driver balance"
- Severity: MEDIUM   Priority score: 3×3×3 = 27
- Status: VERIFIED   Existing item: new (ACTION_ITEMS `referral_payout.py` hits are unrelated RPC/outage items)
- Adversary: auditor (T4A supplementary income sums `driver_bonuses.amount`, `t4a_income.py:31-34`)
- Evidence: `utils/referral_payout.py:876,941` `"amount": _f(amount)`; column is `NUMERIC(10, 2) … -- (Decimal, never float)` (`migrations/179_driver_bonuses.sql:26`); file is outside the SR-03 allowlist.
- What happens: same class as B29/B35 — a `_f()` of an already-rounded Decimal survives today, but any future summed/derived amount drifts; the file is not gated.
- Recommendation: `_money_str(amount)`; add `utils/referral_payout.py` to the semgrep allowlist. Alternative: rely on the DB cast — rejected (that is the landmine 331 describes).
- Blast radius: `driver_bonuses` readers: `auto_payout._balance_from_rows`, `earnings.py /bonuses`, `t4a_income`, driver statements — all `_d()` on read.
- Rollout/Rollback: additive string write; revert-safe.
- Verification: test asserting the insert payload `amount` is a `str` with two decimals.

### MONEY-009 — Chargeback on a wallet / corporate top-up leaves the credited balance spendable and unrecorded
- Hierarchy: L2 Ride Completion & Payments › L3 Refunds/disputes › L4 S-pay-08 › L5 "dispute on a non-ride PaymentIntent"
- Severity: HIGH   Priority score: 4×3×3 = 36
- Status: VERIFIED (code path) / INFERRED (exploitability — no live data)   Existing item: extends A6 OBS-005; B27/C23 cover ride disputes only; new for top-ups
- Adversary: fraudulent rider (top up $500 by card, ride on wallet, dispute the top-up: rides are free and the wallet is untouched); fraudulent corporate admin (same with the master wallet, at scale); auditor (Stripe debited money that appears in no ledger)
- Evidence: `routes/webhooks.py:1534-1600` (`charge.dispute.created` resolves only `rides` by `payment_intent_id`; `stripe_disputes.ride_id=NULL`; no `wallet_transactions`/`corporate_wallet_transactions` reversal, no `users` restriction); `:1727-1731` + `services/payment_service.py:565-567` skip the ledger write when no rider; top-ups are credited on `payment_intent.succeeded` (`webhooks.py:811-830`, `apply_topup` keyed on the PI). `apply_refund` exists for corporate wallets (`corporate_wallet_service.py:168`) but has **no caller** outside the service (grep).
- What happens: Stripe pulls the disputed amount + fee from Spinr's balance; the rider/company keeps the wallet credit; nothing alerts except an admin WS toast with `ride_id=null` and a warning log.
- Root cause: dispute handling was designed around rides (B27, C23); wallets were added as PI `metadata.scope` later without extending the dispute handler.
- Recommendation: on `charge.dispute.created` resolve `metadata.scope ∈ {wallet_topup, corporate_topup, driver_subscription}` → (1) ledger row with `user_id` = the payer (rider / company owner) via the existing `financial_events` header, (2) `wallet_apply_delta(−amount, type="dispute_hold")` / `apply_refund` with `dedupe` on the dispute id, allowing negative balance with a floor, (3) block further top-ups for that customer until closed, (4) metric `spinr_payments_dispute_nonride_total`. Alternative: rely on Stripe Radar rules — rejected (does not fix the ledger gap).
- Blast radius: `wallet_transactions` writers (`wallet_repo.wallet_apply_delta` 8 callers listed §3.x), `corporate_wallet_apply_delta` callers (§3.13); the `stripe_disputes` schema gains `scope`/`customer_id`.
- Rollout: additive handler branch behind `dispute_nonride_handling` flag; Rollback: flag off (rows written stay — append-only ledger; wallet debits reversed by an audited adjustment).
- Verification: webhook test with a `wallet_topup` PI dispute asserting a ledger row and a negative wallet delta; replay test asserting idempotency.

### MONEY-010 — "Payout ≤ collected" is not an invariant and its breach is unmeasured
- Hierarchy: L2 Driver Earnings & Payouts › L3 Per-driver payouts › L4 S-earn-04 › L5 "driver paid for an uncollected / refunded / disputed ride"
- Severity: MEDIUM (policy is deliberate) → HIGH if unbounded   Priority score: 3×4×3 = 36
- Status: VERIFIED   Existing item: policy in `docs/change-log/2026-09-21-uncollected-rides-stay-payable.md`; the *measurement* gap is new
- Adversary: colluding driver+rider (book on a card that will decline / dispute; driver is still paid 100 % + tax; repeat), vendor outage (a Stripe incident that fails every capture for an hour becomes a full payout liability), auditor
- Evidence: §3.4 — `utils/auto_payout.py:544-591`, `utils/payment_collection.py:20-35`, `routes/rides/payments.py:200-240`; no counter/report of `Σ payouts − Σ collected`; `stripe_reconcile._reconcile_payouts` reads no amounts.
- What happens: every failed capture, lost dispute, refund, absorbed tip and promo is platform-funded by design (a real trust feature), but nobody can say what it cost yesterday, and there is no per-driver rate limit on it.
- Recommendation: (1) nightly metric + audit row `platform_absorbed_cents{reason=failed|refund|dispute|tip|promo|incentive}` from existing columns; (2) per-driver 7-day cap on payable-but-uncollected fares (hold above cap → `requires_manual_review`, reuse `payouts.requires_manual_review`); (3) property test in §6. Alternative: switch to "paid when collected" — rejected: it is Spinr's stated differentiator and the 0 %-commission model's trust anchor; measure first.
- Blast radius: `_balance_from_rows` (one formula, parity-tested) — a cap must be applied *outside* it to keep parity.
- Rollout: metric first (no behaviour change); cap behind `payout_uncollected_cap_enabled`. Rollback: flag off.
- Verification: test that a driver with N uncollected rides over cap gets `requires_manual_review=true` and no transfer.

### MONEY-011 — Receipts and corporate statements carry no GST/HST registration number
- Hierarchy: L2 Ride Completion & Payments › L3 Receipt generation › L4 S-pay-05 › L5 "corporate customer claims ITC"
- Severity: MEDIUM   Priority score: 3×3×3 = 27
- Status: VERIFIED (absence) / ASSUMED (requirement — ITC documentation rules not fetched)   Existing item: new
- Adversary: regulator/auditor of the *corporate customer*; plaintiff's lawyer ("undisclosed tax status")
- Evidence: grep for `RT\d{4}`, `GST #`, `registration` in `utils/receipt_pdf.py`, `utils/email_receipt.py`, `utils/corporate_statement_pdf.py`, `routes/corporate_company.py` = 0; drivers' BN is captured (`drivers.gst_bn`) but never rendered.
- What happens: a company paying > $30 per ride via allowance/master wallet receives a monthly statement with "GST" lines but no supplier registration number, which (ASSUMED) is required for its ITC claim; riders' receipts likewise.
- Recommendation: after the accountant settles supplier-of-record (§8), render either the driver's `gst_bn` per ride line or Spinr's number once; store the number on the ride row at settlement (append-only) so historic receipts don't change retroactively.
- Blast radius: three renderers + `routes/rides/receipts.py` JSON; PIPEDA: a BN is business data, not personal — confirm with privacy owner before printing driver BNs on rider receipts.
- Rollout: additive column + flag; Rollback: flag off.
- Verification: snapshot test of the PDF with a BN present.

### MONEY-012 — T4A slip fills Box 020 and Box 048 with the same amount for GST registrants
- Hierarchy: L2 Driver Earnings & Payouts › L3 T4A › L4 S-earn-03 › L5 "GST-registered driver downloads slip"
- Severity: MEDIUM   Priority score: 3×3×3 = 27 (every GST-registered driver ≥ $500 — which, per the payout gate, is every paid driver)
- Status: VERIFIED (code) / ASSUMED (CRA box semantics from [snippet] only)   Existing item: new (`Box 020` grep = 0)
- Adversary: regulator (income double-reported on one slip), driver (files twice or disputes the slip)
- Evidence: `utils/t4a_pdf.py:208-212` `box_row("048", …, net_earnings)` then, when GST-registered, `box_row("020", "Self-employment commissions (GST registrant)", net_earnings)`; [snippet] CRA: Box 020 is "commissions you paid to an independent agent", Box 048 "fees for services" — mutually exclusive categories, and Spinr pays fees, not commissions. Threshold `≥ 500` vs CRA "more than $500" (`t4a_annual_job.py:127`) — harmless over-issuance.
- Recommendation: remove the Box 020 row; keep Box 048; accountant to confirm whether Spinr is the payer at all (else Part XX statement replaces the slip). Alternative: keep both with a footnote — rejected (the slip is a prescribed form).
- Blast radius: `t4a_pdf.py` only; the on-demand endpoint and the Feb job both render through it.
- Rollout: doc/code change before the next last-day-of-Feb run (2027-02-28) — nothing has been issued for tax year 2026 yet, so no re-issue needed unless slips for 2025 were downloaded (UNKNOWN — check `audit_logs` `t4a_issued`).
- Verification: PDF snapshot test asserting exactly one income box.

### MONEY-013 — Daily reconciliations compare mismatched populations, so the alert is either always red or never trusted
- Hierarchy: L2 Ride Completion & Payments › L3 Reconciliation › L4 S-pay-07 › L5 "finance reads the 02:00 summary"
- Severity: MEDIUM   Priority score: 3×3×4 = 36
- Status: INFERRED (logic read; no `audit_logs` row seen)   Existing item: `created.*window` 1 hit in ACTION_ITEMS (context only); new
- Adversary: auditor ("show me a clean day"), malicious insider (a real discrepancy hides in habitual noise)
- Evidence: §3.3 — `utils/stripe_reconcile.py:213-267` (PI `created` yesterday vs `ride_completed_at` yesterday), `utils/reconciliation.py:274-349` (`pi.amount` of PIs created that day, all scopes, vs `financial_events.stripe_charge` created that day; select without `.limit()`).
- What happens: holds are created at booking and captured at completion; rides across 00:00 UTC (18:00 CST), scheduled rides, and every top-up/subscription PI produce `DB_PAID_STRIPE_MISSING` / `STRIPE_ORPHAN` / ledger-sum alerts that are false; a genuine mismatch is indistinguishable.
- Recommendation: key both sides on the *capture* (`charge.created` / `balance_transaction.created`) date, compare `amount_received` not `amount`, filter by `metadata.scope`, and page `financial_events` with `.range()`; record the false-positive count for one week before changing thresholds. Alternative: widen the window to ±1 day — rejected (still double-counts).
- Blast radius: two loops, one `audit_logs` action; `docs/runbooks/stripe-reconciliation.md` readers.
- Rollout: flag `reconcile_v2` with both versions writing their own `audit_logs` rows for a week; Rollback: flag off.
- Verification: fixture with a booking at 23:50Z captured 00:10Z asserting zero discrepancies.


---

## 3. Mandatory closures

### 3.1 SURGE_CAP clamp — every fare-calc call site (VERIFIED)

`calculate_fare()` (`backend/services/fare_service.py:184-237`) is a pure function with **no internal cap** — it multiplies distance/time by whatever `surge` the caller passes. Enforcement is caller discipline; the sites found by `grep -rn SURGE_CAP backend --include=*.py`:

| # | Site | Clamp | Notes |
|---|---|---|---|
| 1 | `services/fare_service.py:533` `FareService.fares_for_location` | `min(_d(area.surge_multiplier), _d(SURGE_CAP))` gated on `surge_enabled AND surge_active` | Decimal |
| 2 | `routes/fares.py:241` `build_fares_for_area` | `min(area.get("surge_multiplier") or 1.0, SURGE_CAP)` | **float** `min` on a float column value — display path, feeds `fare_info` used by booking |
| 3 | `features.py:887` `compute_fare_estimate` area surge | `min(_fare_d(...), _fare_d(SURGE_CAP))` | Decimal |
| 4 | `features.py:896` `surge_override` | clamped since #4638 | callers pass `Decimal("1")`: `services/company_booking_service.py:99`, `routes/corporate_company_bookings.py:348` |
| 5 | `routes/rides/booking.py:939-944` **charge site** | `if surge > _d(SURGE_CAP): logger.error(...); surge = _d(SURGE_CAP)` | the one persisted fare; logs a breach instead of silently clamping — good |
| 6 | `routes/rides/estimates.py:510` | takes `fare_info["surge_multiplier"]` from site 2 (already clamped) — **no clamp of its own**; signs the value into the HMAC `estimate_token` (`:577-594`) | relies on site 2 |
| 7 | `routes/admin/service_areas.py:708-740` manual override | stores >2.5 verbatim + audit `surge_override_above_cap`; comment says fare calc always clamps | admin path |
| 8 | `routes/rides/_shared.py:501-509` `_reestimate_fare_for_stops` | re-derives per-km from the **stored ride** (`distance_fare / (dist*surge)`) then re-applies the same stored `surge` | no re-read of live surge → not retroactive; clamp inherited from booking |
| 9 | `services/fare_service.py:412-420` `recalculate_fare_for_distance` | uses stored `ride.surge_multiplier` only when planned distance is 0 | inherited |

Conclusion: every path that can *persist* a fare is clamped at or before `booking.py:939`; estimate/display paths are clamped at their builder. `surge_engine.ratio_to_multiplier` (`utils/surge_engine.py:76-81`) tops out at `SURGE_CAP` by construction. **No unclamped path found.** Residual: site 2 is float math on a regulated number (works, but the only non-Decimal clamp — see §3.12).

### 3.2 Corporate and scheduled rides exempt from surge (VERIFIED)

- Corporate: `routes/rides/booking.py:891-904` — after `estimate_token` resolution (so a rider cannot pin a surged token in personal mode then flip to Work), `_is_corporate_paid(...)` (`routes/rides/_shared.py:790-819`: `company_allowance` OR `work_profile+corporate_account_id`) forces `surge = Decimal("1.0")`. Estimate side: `routes/rides/estimates.py:415,510` `corporate_bypass`. Server-side corporate guest bookings: `compute_fare_estimate(surge_override=Decimal("1"))` at the two callers above.
- Scheduled: `routes/rides/booking.py:906-913` — `if (body.is_scheduled or body.scheduled_time) and surge > 1.0: surge = 1.0`, fare locked at booking; the scheduled-dispatch loop does not re-price (no surge read in `utils/scheduled_rides.py` — grep `surge` = 0 hits, INFERRED from grep only).
- Gap (INFERRED): CLAUDE.md says "not on scheduled rides booked *outside* the surge window"; the code exempts **all** scheduled rides unconditionally (more conservative than the rule → rider-favourable, no dispute risk; but a scheduled ride booked 5 min ahead during surge is under-priced vs. policy — product call, not a bug).


### 3.3 `utils/stripe_reconcile.py` + `utils/reconciliation.py` — exactly which invariants are checked (VERIFIED, both read end to end)

Two separate daily loops both run at 02:00 UTC (`stripe_reconcile (24h)` and `reconciliation (daily 02:00 UTC)` in `core/background_loop_registry.py`; traceability rows S-pay-07).

**`stripe_reconcile.py` (`_run_reconciliation_tick`, :189-505) — detection only, never moves money:**

| # | Check (type emitted) | Population | Evidence |
|---|---|---|---|
| a | `FARE_ATTRIBUTION_MISMATCH` — `total_fare == driver_earnings − tip_amount + admin_earnings` (±1¢) | yesterday's `payment_status='paid'` rides with a PI | `:59-86`, `:302-312` |
| b | `DB_PAID_STRIPE_MISSING` — ride paid, PI not in yesterday's Stripe list | same | `:315-328` |
| c | `DB_PAID_STRIPE_MISMATCH` — PI status ≠ `succeeded` | same | `:330-345` |
| d | `DB_PAID_AMOUNT_MISMATCH` — **underpayment only**: `amount_received < grand_total + tip` (capped at `authorized_amount`) | same | `:347-385` — comment: "overpayment is not a revenue risk" |
| e | `STRIPE_ORPHAN` — succeeded PI with no ride (skips scopes `driver_subscription`, `corporate_topup`, `wallet_topup`) | yesterday's PIs | `:388-409` |
| f | `PAYOUT_STRANDED` / `PAYOUT_STUCK` — `requires_manual_review=true` or `status='transfer_completed'` > 1 h | `payouts` | `:508-566` |
| g | `RIDE_PAYMENT_STUCK_PROCESSING` > 15 min | `rides` | `:568-619` |
| h | `STRIPE_EVENT_STUCK_UNPROCESSED` (C10) — `processed_at IS NULL` past grace, 30-day lookback | `stripe_events` | `:621-711` |
| i | `STRIPE_ORPHAN_HOLD` — `requires_capture` PI with no ride row (8-day lookback) | Stripe | `:714-808` |
| j | `CANCELLED_CAPTURED_UNREFUNDED` — dry run of `scripts/reconcile_cancelled_captured_refunds.py` | rides | `:849-990` |
| k | optional auto-heal of (g), flag `stripe_auto_heal_processing` default OFF, mark-paid only from ledger+Stripe proof | | `:1018-1222` |

**`reconciliation.py` (`_run_reconciliation`, :93-130):**

| # | Check | Evidence |
|---|---|---|
| l | `Σ pi.amount` of succeeded PIs **created** that UTC day vs `Σ financial_events.delta_cents WHERE event_type='stripe_charge' AND created_at` that day; alert on > 1¢ | `:274-349` |
| m | `financial_event_entries_unbalanced_between` view (debits ≠ credits per header) | `:221-272` |
| n | leg-completeness: headers > 24 h without legs while `ledger_double_entry_enabled` is on; projection queue head not moving in 24 h | `:132-219` |

**What is NOT checked (VERIFIED absent by reading both files):**
1. **Payout ≤ collected per ride/driver** — (f) only looks at payout *status*; nothing compares `Σ payouts.amount` to `Σ driver_earnings` of paid rides, or per-ride payout to per-ride capture. See §3.4.
2. **Tip pass-through** — no check that `Σ tip_amount` charged equals tip credited to drivers (`driver_earnings` includes tip by construction via `driver_earnings_with_tip`, so (a) subtracts it rather than proving it).
3. **Refunds vs disputes** — no comparison of Stripe `Refund`/`Dispute` objects to `financial_events` refund/dispute rows, `stripe_orphan_refunds`, or `disputes`; (j) covers only cancelled+captured. `apply_stripe_refund_cumulative` (`payment_service.py:468`) has no reconciler.
4. **Overcharge** — (d) deliberately ignores `amount_received > expected`.
5. **Wallet ledgers** — `wallet_transactions.balance_after` vs `users.wallet_balance`, and `corporate_wallet_transactions` vs `corporate_wallets.balance` are never re-summed.
6. **Stripe fees / net** — no `BalanceTransaction` read; "driver keeps 100% minus Stripe processing" is never measured.
7. **Connect side** — `driver_stripe_payouts` / transfers (`stripe_connect_ledger_service.py`) not reconciled against Stripe `Transfer`/`Payout` lists (only webhook-driven).

**Defects in what IS checked (INFERRED, not executed):**
- Population mismatch in (b)/(e)/(l): Stripe side is keyed by PI **`created`** (the booking-time pre-auth), DB side by **`ride_completed_at`** / ledger `created_at` (settlement). Any ride whose hold and capture straddle 00:00 UTC (= 18:00 CST Saskatchewan, peak commute) is flagged `DB_PAID_STRIPE_MISSING` one day and `STRIPE_ORPHAN` the next; scheduled rides (hold days before) always mismatch. (l) additionally sums `pi.amount` (authorized, incl. the pre-auth buffer) not `amount_received`, and includes subscription/top-up PIs that `stripe_charge` events may not. Either the alert fires most days and is ignored, or the volume is too low to show it yet — **UNKNOWN which; needs one `audit_logs` row with `action='stripe_reconciliation'` read by a human** (no live data this lane).
- `_sum_financial_events` (`:322-349`) issues a PostgREST select with no `.limit()`/`.range()` — capped by the project's `max-rows` (Supabase default 1000) → silently truncated sum once > 1000 charge events/day. ASSUMED default; confirm the project's `db-max-rows`.
- (f) reads `payouts.amount` but never uses it — the FLOAT/NUMERIC question (§3.6) is irrelevant here, but also means no amount check at all.

### 3.4 Payout ≤ collected — does the invariant exist? (VERIFIED: it does not, and it is *not* Spinr's policy)

- Payable balance is one pure formula, `utils/auto_payout.py:544-580` `_balance_from_rows` (parity-tested with `routes/drivers/earnings.py:118-158`):
  `total_earnings = Σ driver_earnings(completed) + Σ tax_amount + Σ incentive bonus + Σ cancellation_fee_driver` ; `balance = total_earnings + Σ driver_bonuses − Σ payouts(status ∉ {reversed, failed}, payout_type ≠ stripe_sync)`.
- Collected per ride is `grand_total + tip` (`routes/rides/payments.py:606-612`). `grand_total = total_fare + area_fees + tax − discount`; `driver_earnings = total_fare − booking − airport (+ tip)`. So **collected − paid-out = booking + airport + area_fees − discount − incentives − absorbed tips**, which goes negative by design whenever a promo, incentive or trust-first tip exceeds the platform's fee lines.
- Explicit policy that payout can exceed collection: `utils/auto_payout.py:590-591` "Uncollected (failed-charge) fares ARE payable and do reach a Stripe Transfer — deliberate policy" (`docs/change-log/2026-09-21-uncollected-rides-stay-payable.md`, cited at `routes/drivers/earnings.py:76`); `utils/payment_collection.py:20-35` keeps `refunded`, `partially_refunded`, `disputed`, `dispute_lost` in `COLLECTED_PAYMENT_STATUSES` ("driver keeps their pay and the platform absorbs it"); `routes/rides/payments.py:200-240` absorbs uncollectable wallet/corporate late tips.
- Nothing measures the resulting exposure: no metric/report of `Σ(paid-out) − Σ(collected)` per day or per ride, no cap on it, and `stripe_reconcile._reconcile_payouts` never reads amounts (§3.3). → **MONEY-010**.

### 3.5 Stripe webhook types handled vs. what Stripe can send (VERIFIED from `routes/webhooks.py:150-216`)

Handled (`_STRIPE_HANDLED_EVENTS`, 15): `payment_intent.succeeded`, `payment_intent.payment_failed`, `checkout.session.completed`, `charge.refunded`, `refund.updated`, `refund.failed`, `charge.dispute.created/updated/closed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated/deleted`, `account.updated`, `payout.paid`, `payout.failed`.
Deliberately ignored, stamped processed (`_STRIPE_IGNORED_EVENTS`, 21): incl. `payment_intent.canceled`, `payment_intent.requires_action`, `charge.succeeded/captured`, `setup_intent.*`, `payout.created`.
Unknown types: `logger.warning`, `processed_at` left NULL, 2xx returned (`:2226-2240`) → surfaced only by reconcile check (h) 30 days later at most.

**Money-relevant event types Stripe emits that are neither handled nor in the ignore list** (from the Stripe events reference, general knowledge — Stripe MCP down, not fetched this session → INFERRED list):
- `charge.dispute.funds_withdrawn` / `charge.dispute.funds_reinstated` — the two events that actually move money on a dispute; today the ledger relies on `created`/`closed` only (see §3.8).
- `radar.early_fraud_warning.created` — the pre-chargeback signal Uber/Lyft-class ops refund proactively on; nothing listens.
- `review.opened` / `review.closed` — Radar manual review; a PI can be `succeeded` but under review.
- `charge.failed`, `charge.expired`, `payment_intent.partially_funded`.
- `payment_intent.canceled` is *ignored* — but a **7-day auto-expiry** of an uncaptured hold arrives as exactly this event (`cancellation_reason=automatic`). A ride still `pending` settlement after 7 days loses its hold silently; only `orphaned_hold_reconciler` / check (i) could notice, and (i) filters `requires_capture` so an already-expired hold is invisible to it.
- `payout.canceled`, `payout.updated`, `transfer.created/reversed`, `account.application.deauthorized`, `capability.updated`, `account.external_account.created/updated/deleted` (driver changes bank inside Express — payouts continue to the new account with no Spinr record).
- `checkout.session.expired`, `checkout.session.async_payment_failed/succeeded`, `customer.subscription.trial_will_end`, `invoice.upcoming`, `invoice.marked_uncollectible`, `invoice.voided`, `setup_intent.setup_failed`, `mandate.updated`, `customer.deleted`.

Idempotency: `claim_stripe_event` at `:746` before dispatch; `unclaim_stripe_event` on handler failure (`:916-921`) so Stripe's retry is not deduped away — matches CLAUDE.md. Out-of-order: no per-object version/`created` comparison — a late `charge.dispute.updated` after `closed` is applied as-is (see §3.8).

### 3.6 FLOAT money columns — every reader/writer, and a dual-write plan (VERIFIED schema evidence; applied-status UNKNOWN)

`payouts.amount` (RECURRENCE-001 / B28) already has migration `331_payouts_amount_numeric.sql` (`ALTER … TYPE NUMERIC(10,2)`); **whether 331 is applied to production is UNKNOWN** (CARTO-005; G2 says 116 files untracked, C125 8 pending). But 331's own comment ("matching every other money column in this schema (rides.total_fare…)") is **wrong**: `rides.distance_fare`/`time_fare` are `DOUBLE PRECISION` (`migrations/08_complete_schema.sql:133,136`), `rides.surge_multiplier` too (`:130`), and `fare_service.py:480-483` states "distance_fare/total_fare/driver_earnings are FLOAT8 on `rides` — verified against the live schema". Every SQL aggregate casts `total_fare::text::numeric`, `tip_amount::text::numeric` (`migrations/161_ride_money_rollup_fn.sql:12-15,44`, `166`, `227`, `341`, `349`, `350`) — confirming the live FLOAT8 columns are `rides.total_fare`, `tip_amount`, `distance_fare`, `time_fare`, `driver_earnings`, `surge_multiplier` (and by the same idiom, `base_fare`, `booking_fee`, `admin_earnings` — INFERRED). `rides.grand_total`, `subtotal_fare`, `discount_amount` are NUMERIC (`82`, B36). The `rides` table itself has no tracked CREATE (created in Supabase by hand).

Writers that `float()` into those columns (VERIFIED): `services/fare_service.py:484-486` (`recalculate_fare_for_distance`, completion), `routes/rides/payments.py:249,289` (`_f(new_tip)`, `_f(driver_earnings_with_tip)`), `routes/rides/payments.py:625-628` (`ride["tip_amount"] = _f(...)`), `routes/rides/booking.py:1140` (`round(float(surge), 2)`), `routes/rides/_shared.py:522,574`, `routes/rides/rating.py:95-100`, `services/booking_import_service.py` (B29). Readers that must re-Decimal: `_ride_income/_ride_tax` (`auto_payout.py:147-156`), `stripe_reconcile._q2`, every `earnings.py` sum (MONEY-001 shows two that don't), T4A (`tax_exports.py:75`), statements, `ledger_projection._decompose` (B20). A float-typed write also exists into a NUMERIC column: `utils/referral_payout.py:876,941` writes `"amount": _f(amount)` into `driver_bonuses.amount NUMERIC(10,2)` (`migrations/179_driver_bonuses.sql:26` — "Decimal, never float") → **MONEY-008**.

**Dual-write migration plan (PROPOSED, additive, flag-gated, no big bang):**
1. `ALTER TABLE rides ADD COLUMN total_fare_d NUMERIC(12,2), driver_earnings_d, tip_amount_d, distance_fare_d, time_fare_d, base_fare_d, booking_fee_d, admin_earnings_d` (nullable, no default) + `surge_multiplier_d NUMERIC(5,3)`. Cheap on any table size (nullable add = catalog only).
2. Backfill in batches of 5k by `id` range: `SET x_d = x::text::numeric` (the idiom the SQL functions already use, so values are cent-identical to what reports show today). Verify with `SELECT count(*) WHERE abs(x::text::numeric - x_d) > 0.005`.
3. Dual-write behind `app_settings.rides_money_decimal_write` (default off): every writer above adds the `_d` twin as `_money_str(...)`; a DB trigger `rides_money_dual_write` mirrors any float write into `_d` so untouched writers (admin SQL, scripts) can't diverge. Test: property test that after any write `x_d == x::text::numeric`.
4. Dual-read behind `rides_money_decimal_read`: `_ride_income`, `_ride_tax`, `recalculate_fare_for_distance`, `driver_earnings_with_tip`, receipts, T4A, statements read `_d` when non-null. Reconcile query nightly: mismatches → alert.
5. After ≥ 30 days clean and the tax year-end statement run (T4A job, last day of Feb) has been produced from `_d`: flip read default on; then rename columns in one short migration (`x → x_float8_legacy`, `x_d → x`) with `NEVER_APPLY`-style guard against re-running; never DROP the legacy columns during live testing.
Rollback at any step: flag off → float column authoritative; backfilled `_d` columns are inert.
Alternative considered: `ALTER COLUMN TYPE` in place like 331 — rejected: `rides` is the hottest table, an in-place type change takes ACCESS EXCLUSIVE and a full rewrite, and 331's own applied-status is unknown; also every SQL function that does `::text::numeric` keeps working either way, so the additive path has no reader cost.

### 3.7 MONEY-002 `tax_breakdown` — the write-path gap incl. `email_receipt.py` / `corporate_statement_pdf.py` (VERIFIED)

All three renderers collapse to one line when `tax_breakdown` is empty: `utils/receipt_pdf.py:174-199` ("Tax" = grand_total gap), `utils/email_receipt.py:245-263` (same shape, `_line("Tax", …)`), `utils/corporate_statement_pdf.py:33-84` (combined "Tax (GST/PST)" **with a loud Sentry/`logger.error` alert**, A29). Only the corporate one alerts; the rider PDF/email fallbacks are silent.

`rides` write paths (grep `_insert_ride_with_code` / `insert_one("rides"`) and whether they persist `tax_breakdown`:
| Path | Persists `tax_breakdown`? | Evidence |
|---|---|---|
| Rider booking `routes/rides/booking.py:1335` via `_shared.py:563` | yes | `"tax_breakdown": fees_result.get("tax_breakdown", {})` |
| Corporate guest/company booking `services/company_booking_service.py:209` | yes | |
| **Admin-created ride `routes/admin/rides.py:1247`** | **no** — `ride_doc` keys are `total_fare, subtotal_fare, discount_amount, promo_code, …` with no `tax_amount`/`tax_breakdown`/`grand_total` | `:1200-1247` |
| Legacy import `services/booking_import_service.py:775,985` | GST-only reconstruction or `{}` | documented |
| **Completion re-price** `routes/drivers/ride_complete.py:596` → `fare_service.recalculate_fare_for_distance` | keeps the booking-time `tax_amount`/`tax_breakdown` while `total_fare` changes | `fare_service.py:454-467` → **MONEY-007** |
| Mid-trip stop edit `routes/rides/_shared.py:510-540` | recomputes `tax_amount` from `fees_result` | `:525` |

So the fallback is reachable for admin-created rides (silent, rider receipt), legacy rows, and — worse — the re-priced completion path produces a `tax_breakdown` that no longer equals 5 % of the charged subtotal (not a fallback; a wrong itemised line).

### 3.8 Chargeback / dispute lifecycle end to end (VERIFIED `routes/webhooks.py:1534-1747`, `services/payment_service.py:529-610`)

| Step | What happens | Gap |
|---|---|---|
| `charge.dispute.created` | insert `stripe_disputes` (amount, reason, `evidence_due_by`), ride → `payment_status='disputed'`, admin WS broadcast, `logger.error` | **No ledger row** although Stripe debits the disputed amount + fee at *creation* (`charge.dispute.funds_withdrawn`, not subscribed). Only rides are looked up by PI: a dispute on a **wallet top-up / corporate top-up / Spinr Pass** PI leaves `ride_id=NULL`, no wallet reversal, no block → **MONEY-009** |
| `charge.dispute.updated` | status mirror only | no ordering guard vs a later `closed` (late-delivered `updated` overwrites `lost`) — LOW |
| `charge.dispute.closed` | status; `lost` → ride `dispute_lost`, else `paid` (`warning_closed` handled, B27); `record_dispute_close_events` writes one `financial_events` row per `balance_transactions[]` entry, `dedupe_key = stripe_dispute|{dispute}|{bt}` (N4) | **OBS-005 skip path**: the whole ledger write is skipped when `rider_id` is unresolved (`webhooks.py:1727-1731`, `payment_service.py:565-567`) — exactly the non-ride PIs above, i.e. the money leaves Stripe with **no ledger row at all**; only a warning, no metric (A6 OBS-005 stands, and is larger than A6 scored it) |
| Driver side | `dispute_lost` stays in `COLLECTED_PAYMENT_STATUSES` — driver keeps the payout, platform absorbs (policy) | no clawback, no per-driver dispute-rate signal for fraud (§4 #33) |
| Rider side | none | no auto-restriction after `lost`; a rider can dispute repeatedly (payment_retry blocks booking only after 3 *failed* attempts, not disputes) |
| Evidence | `utils/dispute_evidence_pack.py` + `routes/admin/dispute_evidence_submission.py` (C23 items 4–5), `dispute_evidence_reminder (6h)` loop | assembled on admin demand, not auto-submitted; `radar.early_fraud_warning.created` not subscribed, so no pre-dispute refund window |
| Reconciliation | none of §3.3 compares `stripe_disputes`/`financial_events(stripe_dispute)` to Stripe's dispute list | — |

Can it run twice? `created`: `stripe_disputes.stripe_dispute_id` has a unique index (per B27 comment) → a replay after `unclaim` raises on insert → handler 5xx → Stripe retries into the same error forever until `stuck event` review (INFERRED; the insert is not wrapped). `closed`: idempotent by `dedupe_key`. Network fails after success: ride status updated before the ledger write → a crash between leaves ride `dispute_lost` with no ledger row; next redelivery is deduped by `claim_stripe_event` → permanent gap (the same F1 partial-failure class as refunds, which *did* get an atomic RPC — `apply_stripe_refund_cumulative`). Out of order: no guard.

### 3.9 Pre-auth hold sizing and the orphaned-hold reconciler (VERIFIED)

- Hold = `grand_total + RIDE_AUTH_BUFFER_CAD`, and `RIDE_AUTH_BUFFER_CAD = Decimal("0.00")` (`core/config.py:249`) — i.e. **exactly the quoted fare, no tip buffer** (`utils/stripe_charge.py:895-905`: "the booking hold no longer carries a tip buffer"). Tip/overage is collected by `increment_authorization` when the card supports it (`auth_incrementable`) else a **second PI** (`payment_service._settle_against_hold`, `:2084-2260`; sub-$0.50 overflow rejected with `tip_overflow_below_minimum`, and `min_tip_amount` setting 438). Two docstrings still describe a buffer (`card_hold_release.py:4`, `orphaned_hold_reconciler.py:11`) — doc drift only.
- Capture is capped at `authorized_amount` (`stripe_charge.py:1077-1094`); a fare that grows at completion (§3.7) is charged as capture + separate overflow PI, so no silent shortfall (`:2224-2230` "never silently drop the shortfall").
- Release paths: three implementations (`routes/rides/cancellation.py` inline, `utils/stuck_ride_sweeper` and `orphaned_hold_reconciler` via `card_hold_release.py`) — a documented, deliberate fork (`card_hold_release.py:28-36`).
- `orphaned_hold_reconciler (15m)`: strict Redis lock + CAS on `updated_at` + Stripe idempotency `ride-cancelauth-{ride}-{pi}`; only `cancelled` rides. Holds with **no ride row** are covered by `stripe_reconcile` check (i) (detection only, 8-day lookback). Holds that Stripe already auto-expired (7 days) arrive as `payment_intent.canceled`, which is in `_STRIPE_IGNORED_EVENTS` → `rides.auth_status` stays "open" forever for a ride that never settled; `preauth_capture (5min)` would then try to capture an expired PI and fall back to a fresh off-session charge (`_settle_against_hold:95-100`) — correct outcome, but the ledger never learns the hold expired. LOW.

### 3.10 `payment_retry` idempotency (VERIFIED `utils/payment_retry.py`)

Per-attempt `idempotency_key=f"ride-confirm-{ride_id}-{intent.amount}-retry-{attempt}"` (`:627`) on an existing PI; DB CAS `{"payment_status": "retrying", "payment_retry_count": retry_count}` → `processing` (`:628-640`) so two replicas cannot both submit attempt N; `MAX_RETRIES = 3` (`:263`); strict Redis leader lock (`:58`); exhausted-alert claimed once (`_claim_exhausted_alert`). Capture path key `ride-capture-{ride_id}-{capture_cents}` (`:807`). A5's steelman stands: the per-attempt key cannot double-charge because a `succeeded` PI is terminal. Residual: `intent.amount` in the key means an admin-adjusted amount between attempts creates a new key — intended.

### 3.11 Is the double-entry ledger on? (VERIFIED code; production value UNKNOWN)

- Flag `ledger_double_entry_enabled`: default `False` in `schemas.py:280` (`AppSettings`), read by `ledger_service.double_entry_enabled()` (`:427-435`, **"assuming off" on any read error**), gating `write_legs()` (`:515-584`, `check_flag=True`) and documented as "no-op until on" for the projection (`core/lifespan.py:563`, `utils/reconciliation.py:141`). Readers: `services/ledger_service.py:431`, `services/payment_service.py:376` (docstring), `utils/ledger_projection.py` (via `write_legs`), `utils/reconciliation.py:132-219` (leg-completeness only "while the flag is on").
- Sibling `ledger_atomic_settle_enabled`: default `False` (`schemas.py:290`), read at `payment_service.py:1808-1845`, gates `settle_ride_card_payment` RPC (migration 288/337).
- `financial_events` (single-entry, migration 58) is written unconditionally via `record_event` (never raises, `dedupe_key` optional — callers that omit it are not replay-safe: N4 fixed disputes; `record_payment_event`/`record_refund_event` paths carry refs). Legs (`financial_event_entries`, migration 286) have exactly one writer, the 15-min projection.
- **Production value of both flags is UNKNOWN** — `app_settings` is DB-resident and no live read was allowed. If off (the default and the documented state), Spinr today runs a single-entry, append-only ledger; every "balanced" claim in `domain-payments.md` is dormant. → §7.

### 3.12 HIST-001's 13 "found while fixing the previous" chains — still open? (VERIFIED against code, not against production data)

B28 (331 written, applied-status UNKNOWN) · B29/B30/B35/B36 closed in code · the class is **not closed**: this pass found five new instances of the same class outside the semgrep allowlist — `features.py:900` (`grand_total = round(float+float+float, 2)` on the canonical estimate), `routes/fares.py:241` (float `min` on surge), `routes/rides/booking.py:1140` (`round(float(surge),2)`), `utils/referral_payout.py:876,941` (float into NUMERIC), `routes/rides/_shared.py:773-785` (float compares/reads of `discount_amount`/`tip_amount`) — plus MONEY-001's two. `.semgrep/spinr-rules.yml:97-110` covers 12 files; `features.py`, `booking.py`, `_shared.py`, `estimates.py`, `referral_payout.py`, `incentive_service.py`, `payouts.py`, `auto_payout.py`, `webhooks.py`, `ledger_service.py`, `stripe_reconcile.py`, `t4a_*.py`, `corporate_statement_pdf.py` are outside it (`grep -c "float("` over those = 47 hits, most legitimately non-money). The pre-commit check 6 is still WARNING-only (`.claude/hooks/pre-commit:113-119`). HIST-001's recommendation (registry + write guard) has not been filed as an item. → **MONEY-006**.

### 3.13 `corporate_wallet_apply_delta` — every caller and its idempotency key (VERIFIED)

The RPC (`migrations/28`, redefined `203`, `214`, `297`, `376`) dedupes in this order (`297:270-292`, `376`): (1) `p_stripe_pi` (top-ups), (2) `(wallet_id, ride_id, type, scope)` when `p_stripe_pi IS NULL AND p_ride_id IS NOT NULL`, (3) `p_client_idempotency_key` (376). Only `services/corporate_wallet_service.py:62` calls it; the wrappers and *their* callers:

| Caller | Wrapper | `type` | Key that dedupes | Replay-safe? |
|---|---|---|---|---|
| `routes/webhooks.py:816-830` `payment_intent.succeeded` (scope `corporate_topup`) | `apply_topup` | `topup` | `stripe_payment_intent_id` | yes |
| `services/payment_service.py:1424-1440` `settle_corporate` master-wallet fallback | `apply_adjustment` | `adjustment` | `ride_id` | yes |
| `services/payment_service.py:908` `charge_late_corporate_tip` | `apply_late_tip_master_debit` | `late_tip_adjustment` (migration 319) | `ride_id` (distinct type from settlement) | yes |
| `services/cancellation_service.py:301` corporate cancellation fee | `apply_adjustment` | `adjustment` | `ride_id` | yes — but **shares the key with the settlement debit**; safe only because a ride cannot be both cancelled and settled (INFERRED) |
| `routes/corporate_wallet.py:285-291` admin ad-hoc adjustment | `apply_adjustment` | `adjustment` | `client_idempotency_key` (body or derived, #4602) | yes |
| `services/corporate_wallet_winddown_service.py:164` account-close refund debit | `apply_adjustment` | `adjustment` | **none** (no `ride_id`, no client key) | **no** — a second `refund_wallet_balance_on_close` run debits the ledger twice while Stripe refunds (keyed `corp-close-refund-{wallet}-{topup}`) do not → ledger < Stripe. Caller `routes/corporate_accounts.py:911` is admin-triggered; LOW likelihood, real if a timeout retries. Recommend `client_idempotency_key=f"corp-close-{wallet_id}"` |
| `services/corporate_allowance_service.py:68` | `corporate_allowance_apply_delta` (sibling RPC) | — | `(wallet, ride, type, member)` since 297 | yes |
| `apply_refund` (`corporate_wallet_service.py:168`) | — | `refund` | `ride_id` | **no caller in `backend/`** — the corporate refund path exists but nothing invokes it (MONEY-009 would be its first user) |

Rider wallet: `wallet_repo.wallet_apply_delta` (8 callers: `cancellation_service:357`, `payment_service:754`, `admin/wallet.py:168,272`, `drivers/ride_cancel.py:639`, `rides/cancellation.py:399,852`) — keys not re-audited here (R8/R11 overlap); noted that `admin/wallet.py:70` `AdminDebitRequest.amount: float` while `AdminCreditRequest.amount: Decimal` (`:55`) — coerced via `_q()` at `:259`, so LOW.

## 4. Scenario cards — sweep-catalog §3.4 (28–38), each with the §4 chain

Chain key: T=trigger · D=detection · S=system state · U=user experience · B=business rule · R=recovery · E=escalation · A=audit record · P=proving test. Distributed-step questions: **2×** (can it run twice), **net** (network fails after success), **ooo** (duplicate/out-of-order + compensation).

| # | Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|---|
| 28 | Card declined at completion; 3DS required after trip | rider | capture/charge fails | retry other card in-app; off-session 3DS not possible → ask rider; driver still paid | T: `settle_card` outcome `declined`/`requires_action`. D: `payment_service.py:673-680, 2406-2417` → 402 `authentication_required`; `payment_failed` webhook (B42 fixed, migration 414). S: `payment_status` `failed`/`retrying`; retry loop 3× (`payment_retry.py:263`); rider blocked from booking after exhaustion (`:237`). U: "Change card" escape (`ProcessPaymentRequest.payment_method_id`). B: ride stays payable to driver (§3.4). R: `payment_retry`, admin `held_for_review` tools. E: admin alert on exhaustion (`:253`). A: `financial_events` only on success; failures in `rides.payment_failure_reason`. P: `test_payments_stripe_error_specificity.py`. 2×: CAS on `payment_retry_count` ✔. net: PI succeeded but DB write failed → `processing` + auto-heal flag (§3.3 k) ✔. ooo: `SETTLED_PAYMENT_STATUSES` guard on late `payment_failed` ✔ | **Handled** | Low |
| 29 | Webhook twice / out of order / never | Stripe | redelivery, reorder, drop | idempotent by event id; object-version ordering; nightly replay | T: any event. D: `claim_stripe_event` (`webhooks.py:746`), stuck-event check (h) after grace, 30-day lookback. S: `stripe_events.processed_at`. B: unknown types left NULL, 2xx'd. R: admin replay `routes/admin/stripe_events.py` (N4). A: `stripe_events` row. P: webhook idempotency tests. 2×: ✔ (claim). net: handler side effects then crash before stamp → row NULL → surfaced by (h), **never replayed automatically** (deliberate). ooo: **no per-object ordering** — late `dispute.updated` after `closed` overwrites status (§3.8); late `payment_failed` guarded ✔. "Never": (h) surfaces only rows that *arrived*; a never-delivered event is caught only by (b)/(c)/(l) with the population bugs of MONEY-013 | **Partial** | Medium |
| 30 | Refund issued twice; partial refund + chargeback on same ride | admin / Stripe | double-click refund; refund then dispute | cumulative refund ≤ captured; dispute nets against refund; one ledger truth | T: refund via Stripe API (policy). D: `charge.refunded` → `reconcile_confirmed_stripe_refund` reads *all* Refund pages and applies the **cumulative** succeeded amount atomically (`apply_stripe_refund_cumulative`, RPC serialises writers; `stale` outcome ignored) ✔ — a second refund is a new cumulative, not a double-book. S: `rides.refund_amount`, `payment_status` `refunded/partially_refunded`. B: driver keeps pay. R: `refund_booked_cents` F1 gap detection. A: `financial_events.stripe_refund` keyed `stripe_refund|pi|cumulative`. **Chargeback after partial refund**: dispute amount is Stripe's; `dispute_lost` overwrites `partially_refunded` (`webhooks.py:1690-1702`) — refund state lost from `payment_status` (ledger keeps both). P: refund cumulative tests exist (F1). 2×: ✔. net: ✔ (RPC atomic). ooo: cumulative semantics make order irrelevant ✔ | **Handled** (refund) / **Partial** (refund+dispute status overwrite) | Low |
| 31 | Tip added after payout already sent | rider | late tip via history | tip charged separately; driver paid on next cycle; never double-paid | T: `POST /rides/{id}/tip` days later. D: `payment_status == paid` branch (`rides/payments.py:199`). S: card → fresh tip-only PI (`charge_late_tip`); wallet/corporate → best-effort debit, shortfall **absorbed**, driver credited in full; `tip_amount`/`driver_earnings` rewritten via idempotent `driver_earnings_with_tip`; `ERR_TIP_DUPLICATE` one-tip rule. B: trust-first (2026-08-17). R: balance is live-recomputed so the next payout includes it (§3.4). A: `spinr_payment_late_tip_total{outcome}`, `_record_late_tip_absorption`. P: late-tip tests (change-log 08-17). 2×: `@idempotent_endpoint(scope="ride_tip")` + duplicate-tip guard ✔. net: Stripe charge succeeded, `update_ride` failed → rider charged, driver not credited until retry (which is refused by `ERR_TIP_DUPLICATE`? — no: `tip_amount` still 0, so retry re-charges → **second Stripe charge**; `charge_late_tip` key not inspected — UNKNOWN). ooo: n/a | **Partial** (net-failure case UNKNOWN) | Medium |
| 32 | Promo stacking; self-referral; multi-account device farming | fraudulent rider | codes, referral links, new accounts | one promo per ride; per-user caps; device/payment-instrument fingerprint; self-referral blocked | T: `POST /promotions/apply`. D: `max_uses_per_user` (`promotions.py:183-195`), `first_ride_only`, `new_user_days` (`:226-244`); one `promo_code` column per ride (schema) → no stacking on a ride ✔; driver self-referral blocked (`drivers/referrals.py:254`); rider referral velocity check noted closed 2026-09-13 (`ACTION_ITEMS.md:18520`). **No device / card-fingerprint linkage anywhere** (grep `device_id|fingerprint` in promo/referral routes = 0); OTP-only signup means a new phone number = a new "first ride". B: discount capped at ride fare (`receipt_pdf.py:163-171`). A: `promo_applications`. P: promo tests. 2×: `_record_promo_application` atomic increment ✔ | **Partial** (multi-account unhandled) | Medium (promo spend, not chargeback) |
| 33 | Driver–rider collusion fake trips for incentives | fraudulent pair | book, "drive", complete | GPS plausibility, min distance, pay-when-collected for suspicious pairs, pair-frequency signals | T: completion. D: `held_for_review` GPS-spoof gate (`routes/admin/rides.py:453-575`, `payment_collection.py` "never charged"); incentive `min_distance_km` condition (`incentive_service.py:191`); rider/driver pair frequency — **no signal**; failed-card fake trips are *still payable* (§3.4) → the cheapest collusion is a rider card that declines. R: admin release/void of held rides. A: `ride_incentive_claims`. P: none for pair frequency | **Partial** | High (platform-funded) |
| 34 | GPS spoofing; impossible speed | fraudulent driver | spoofed pings | speed/teleport filters; hold payment; route-snapped distance | T: location stream. D: haversine spike filter + road-snapped distance both stored (`ride_complete.py:640-650`), `held_for_review` gate; owned by R11 (safety/fraud) — not re-audited here | **Partial** (R11) | Medium |
| 35 | ATO via SIM swap / OTP / refresh token | attacker | — | — | R10 lane; money impact: wallet spend and instant payout (`/payouts/instant`) reachable with a fresh token — no step-up auth on payout bank change (`DELETE /bank-account`, `POST /bank-account` at `payouts.py:712-744`, no OTP re-check seen — INFERRED) | **Unhandled** (money step-up) | High for drivers |
| 36 | Chargeback evidence pack auto-assembled | admin | dispute opened | evidence auto-built and submitted before deadline | T: `charge.dispute.created`. D: `evidence_due_by` stored; `dispute_evidence_reminder (6h)`; pack built by `utils/dispute_evidence_pack.py` (timeline, PIPEDA-filtered), zip + Stripe submission endpoints (C23 items 4–5); `docs/runbooks/payment-dispute-evidence.md`. Not *auto-submitted*; no `radar.early_fraud_warning` pre-emption; non-ride PIs have no pack (MONEY-009) | **Partial** | Medium |
| 37 | Lost item returned — fee, masking, abuse | rider/driver | lost & found flow | return fee to driver; masked contact; abuse throttle | `routes/rides/lost_found.py` exists; grep `return_fee|lost_item_fee` = 0 → **no fee mechanism**, so no money path to audit; contact masking is R11's | **Unhandled** (fee) / n/a (money) | Low |
| 38 | Allowance exhausted mid-trip; company suspended mid-trip | corporate | policy/wallet change during `in_progress` | ride completes on original terms; settlement uses fallback; rider never stranded | B: in-progress rides are grandfathered (`corporate_suspension_service.py:5,83`); settlement: allowance first then master wallet, hard-fail 503 leaving `payment_status='pending'` if master exhausted (`settle_corporate`, domain-payments) — **no fallback to rider card**, so the ride sits unpaid and payable to the driver (§3.4) until a top-up; guest bookings pre-check `remaining < 1.5 × grand_total` (`company_booking_service.py:129`). A: `corporate_wallet_transactions` idempotent on ride. 2×/net/ooo: RPC keyed ✔ | **Partial** | Medium |



## 5. CRA / sweep-catalog §4.1 — with primary-source status

**What was blocked this session (recorded, per brief):** `WebFetch` → `www.canada.ca` (`EGRESS_BLOCKED`), `www.saskatchewan.ca` (`EGRESS_BLOCKED`), `sets.saskatchewan.ca` PST-46 PDF (`EGRESS_BLOCKED`), `ryan.com` PST-46 mirror (`EGRESS_BLOCKED`). `WebSearch` used 8 of 10; every quote below marked **[snippet]** is a search-engine result block, **not a fetched page** — it identifies the primary-source URL and its gist, but the page text was not read. Per the ground rules that leaves every row **ASSUMED** (URL known, text unread) and all go to §8 Escalations.

| §4.1 item | What the code does (VERIFIED) | Primary source located (URL, 2026-09-24) | Status | Gap / note |
|---|---|---|---|---|
| GST/HST registration; small-supplier threshold | Hard payout block without a BN (`routes/drivers/payouts.py:755-780`: "the $30k small-supplier exemption does NOT apply to ride-sharing (Excise Tax Act 'taxi business')"); BN format `^\d{9}(RT\d{4})?$` | CRA GI-196 "GST/HST and Commercial Ride-sharing Services" https://www.canada.ca/en/revenue-agency/services/forms-publications/publications/gi-196/… and "GST/HST information for taxi operators and commercial ride-sharing drivers" https://www.canada.ca/…/taxi-ride-sharing-drivers.html — [snippet]: "required to be registered for the GST/HST since July 1, 2017, regardless of whether or not they are a small supplier"; "the small supplier threshold does not apply to taxi businesses" | **ASSUMED → consistent** (code matches the snippet) | The BN regex accepts a bare 9-digit BN with **no RT account** — a BN without an RT0001 program account is not a GST registration; escalate whether the gate should require `RT\d{4}` |
| Supplier of record | Driver: contractor (`docs/legal/terms-of-service.md`), driver receives `driver_earnings + tax_amount` (`auto_payout.py:566`), Spinr keeps booking/airport fees; receipts show Spinr branding with no driver GST number; statements caption tax as "remittance reminder" (`driver_statement.py:16,133-151`) | GI-196 (same URL) — [snippet] does not settle whether the *platform* must show the driver's GST number on the receipt | **ASSUMED** | Receipt shows no supplier GST/HST number at all (`receipt_pdf.py`, `email_receipt.py` — grep `RT` = 0); for a > $30 invoice the ITC documentation rules require the supplier's registration number → corporate ITC row below |
| GST/PST separate lines; **PST on SK passenger transport** | `features.py:810-841` per-area `gst_enabled/pst_enabled/hst_enabled`; production `pst_enabled=false` on 4 SK areas (G9) | PST-46 "Service Enterprises", revised **February 2025** — https://sets.saskatchewan.ca/rptp/wcm/connect/e7535a1a-…/PST.046+Service+Enterprises.pdf — [snippet] confirms a "Transportation Services" section exists; G9 (`ACTION_ITEMS.md:17748-17790`) records an earlier snippet listing "Taxicab or limousine transportation services … and Other passenger transportation services" as PST-applicable | **ASSUMED — and the only evidence found points the *opposite* way from production config** | Re-found open item G9 (4 verbal determinations, 0 citations). Reported here as *why it is stuck*: every session's egress blocks the bulletin; nobody has been asked to read it offline. **Highest-value 15-minute human task in this lane.** |
| Digital-platform reporting (ITA Part XX.1) | SIN-free "filer handoff export" (`docs/change-log/2026-07-29-t4a-filer-handoff-export.md`), SIN now Vault-encrypted (migration 289), `payouts.py:786-800` requires SIN before Stripe onboarding | CRA "Reporting Rules for Digital Platform Operators" https://www.canada.ca/en/revenue-agency/programs/about-canada-revenue-agency-cra/compliance/reporting-rules-digital-platforms.html + "Guidance…" — [snippet]: file Part XX return by **January 31** for prior year, first due Jan 31 2025; seller statement also by Jan 31; excluded platforms are *cost-sharing* ride arrangements only | **ASSUMED — likely applies to Spinr** (drivers derive profit, so the cost-sharing exclusion does not) | No code produces a Part XX XML/return; no `lifespan` loop for Jan 31; the T4A job fires **last day of Feb** — a month after the Part XX deadline. Whether Spinr is a "reporting platform operator" (Canadian resident operator facilitating personal services) is the accountant's call — §8 |
| T4A rules/threshold; T4A job correctness | `_T4A_THRESHOLD = 500.00` (`t4a_annual_job.py:42`, `≥` at `:127`); Box 048 from `driver_earnings` + supplementary income, **tax excluded** (`t4a_income.py`); slip **also fills Box 020 with the same amount** for GST registrants (`t4a_pdf.py:208-212`) | "T4A slip – Information for payers" https://www.canada.ca/…/t4a-slip.html and "Filling out the T4A slip" — [snippet]: issue if payments in the year "were more than $500" (strictly greater); Box 048 = fees for services; Box 020 = commissions paid to an independent agent, excluding GST/HST/PST; slips to recipients by last day of February | **ASSUMED; two likely defects**: (1) `≥ 500` vs CRA "more than $500" — a driver at exactly $500.00 gets a slip CRA does not require (harmless); (2) **Box 020 duplicated with Box 048 double-reports income on one slip** → MONEY-012 | Whether Spinr is the *payer* at all (rider pays driver; Spinr facilitates) determines whether a T4A or a Part XX statement is the right instrument — accountant |
| Books & records retention | 7 years (`CLAUDE.md`, purge migrations 216/289/296) | "Keeping Records" RC188 https://www.canada.ca/…/rc188/keeping-records.html — [snippet]: six years from the end of the last tax year they relate to | **ASSUMED-safe** (7 ≥ 6) | Note the trigger is *tax year*, not ride date: a ride in Jan 2026 relates to tax year 2026 → keep to end 2032; Spinr's 7-years-from-ride is ≥ that in every case |
| Corporate invoices meet ITC documentation | `routes/corporate_company.py` `_aggregate_rows` + `utils/corporate_statement_pdf.py` show tax by type; **no supplier GST/HST registration number on statements or receipts** (grep `RT\d{4}`/`GST #` in `utils/*receipt*`, `corporate_statement_pdf.py` = 0) | Not fetched (RC4022 / Input Tax Credit Information (GST/HST) Regulations) | **UNKNOWN** | If the driver is the supplier, each ride > $30 needs *the driver's* registration number for the company's ITC claim; if Spinr is agent/billing agent, Spinr's number. Either way today's documents carry neither → MONEY-011 |
| Promotions / credits / incentives tax treatment | Discount applied **after** tax on the undiscounted subtotal (`_shared.py:553` `grand_total = total + fees + tax − discount`; `features.py:818` `taxable_amount = subtotal_d + fees_total`); driver incentives/bonuses on T4A (`t4a_income.py`), not taxed as consideration | "Section 232.1 – Promotional Allowances" (P-243) https://www.canada.ca/…/p-243/section-232-1-promotional-allowances.html — [snippet]: where a promotional allowance is a discount on a supply not yet taxed, "the consideration … is deemed to be the amount net of the discount"; reimbursable coupons: tax on full price, "GST/HST considered collected equal to the tax fraction of the value of the coupon" | **ASSUMED; possibly over-collecting** | Spinr's promo is platform-funded on a driver-supplied service — whether it is a s.181 coupon (tax on gross, Spinr remits tax fraction) or a price reduction (tax on net) changes both the rider receipt and who remits. Tips: [snippet] voluntary gratuities not taxable — code excludes tip from the taxed base (`payments.py:612`), consistent |


---

## 6. §5 invariants → property-based tests (each VERIFIED only when a real test exists)

| Invariant (greenfield §5) | Holds today? | Existing test | Property test that would prove it (PROPOSED) |
|---|---|---|---|
| Ledger balances: no money created/lost across charge → refund → payout | **Not provable** — legs are flag-gated (`ledger_double_entry_enabled`, default off); single-entry headers cannot balance by construction | `tests/rls/…`, ledger_service unit tests (header retry) | Hypothesis strategy over sequences of {charge, partial refund, dispute, tip, payout}; assert `Σ debit == Σ credit` per event *and* `Σ platform_cash == Stripe net` — requires legs on; until then assert `Σ delta_cents(stripe_charge) − Σ refund − Σ dispute == Σ captured − Σ refunded` per PI |
| Payout per ride ≤ collected; tips never double-paid | **False by policy** (§3.4) — replace with: `payout ≤ collected + platform_funded_absorption`, and `tip credited == tip charged or absorbed` | `driver_earnings_with_tip` idempotency tests; balance parity test (`auto_payout` ↔ `earnings`) | For random rides × {card ok, declined, refunded, disputed, late tip absorbed}: `driver_paid − collected == Σ absorbed[reason]` exactly, and `tip` appears once in `driver_earnings` regardless of path order (rate → tip → process-payment permutations) |
| A rider has at most one active ride | R8 | `test_ride_state_machine.py` | — (R8) |
| No `cancelled` after `in_progress`; no unknown statuses | R8 | same | — |
| `is_available ⇒ is_online` | R8 | — | — |
| Period 3 ⇒ linked `in_progress` ride; period rows append-only | R11/R12 | — | — |
| Applied surge ≤ 2.5× on every fare path; 1.0× on corporate-paid | **Holds** (§3.1, §3.2) | surge tests, #4638 tests | Strategy over `(area.surge_multiplier ∈ [0, 50], surge_enabled, surge_active, payment_method, work_profile, is_scheduled, estimate_token∈{valid,expired,tampered})` → assert persisted `rides.surge_multiplier ≤ 2.5`, `== 1.0` when corporate or scheduled, and `charged == shown` when token valid |
| A completed trip's fare is never changed silently | **Partial** — `recalculate_fare_for_distance` at completion is logged (`ride_complete.py:597`) but not audit-rowed; admin `fare_overridden_by_admin` flag exists; tax not recomputed (MONEY-007) | — | After `status=completed` and `payment_status ∈ COLLECTED`, any write to fare columns must carry an `audit_logs` row with `action=fare_adjustment`; assert via DB trigger test |
| Non-admin cannot read/mutate another user's wallet | R10 | `tests/rls/` (dormant policies, C108) | — |
| **New (this lane):** `tax_breakdown[GST].amount == round(rate × (total_fare + fees))` on every persisted ride | **False** after completion re-price (MONEY-007) and absent on admin-created rides (§3.7) | none | Strategy over `(planned, actual, fare_lock)` → invariant on the final row |
| **New:** every `float(` in a money module is inside `_f/_fd` or a `nosemgrep`-justified label | **False** (§3.12) | SR-03 (12 files) | Static test in the style of `test_loguru_call_conventions.py` over a money-module registry (HIST-001 recommendation) |
| **New:** money column registry — every NUMERIC column receives `str`/`Decimal`, never `float` | **False** (`driver_bonuses.amount`, MONEY-008) | none | Repository-layer write guard test: insert `{"amount": 1.1}` into a registered column → rejected |


## 7. §7.3 Rebuild Delta — Epic: Ride Completion & Payments / Driver Earnings & Payouts (testing R19's hypothesis "double-entry ledger as the only money writer")

- **Verdict per inherited pattern:** Decimal-only rule **KEEP**; enforcement **REPLACE** (registry + write guard, HIST-001); pure `calculate_fare` **KEEP**; `financial_events` header-first write **KEEP**; flag-gated legs via projection **MODIFY** (turn on, then make legs the source of truth); "state on the ride row + ledger as a side record" **REPLACE** over time; trust-first absorption policy **KEEP but METER** (MONEY-010); two reconciliation loops **MODIFY** (MONEY-013); per-domain float formatting in three apps **REPLACE** with one `shared/money` formatter.
- **Keep (already best-in-class):** exact attribution invariant + nightly check; hold = quote with incremental auth; idempotency keys that embed amount; refund cumulative RPC; append-only ledger with GUC-guarded purge; explicit, documented absorption policy; per-area tax config.
- **Uber/Lyft do:** a ledger service is the system of record for balances; ride rows carry *references* to ledger entries, not amounts (Uber's "Money Movement" / ledger-first designs are public in their engineering blog — source: Uber Engineering "Ledger" posts, general knowledge, not fetched this session → INFERRED). Stripe itself recommends Connect *destination charges* or *separate charges & transfers* with the platform balance as the ledger. **Spinr today:** amounts live on `rides.*` FLOAT8 columns; `financial_events` is a single-entry shadow written *after* the money moved and never raises; balances are recomputed from rides + payouts on every read (`_balance_from_rows`).
- **Clean-sheet Spinr would:** make the double-entry ledger the *only* writer of money truth — every settlement is a journal entry (rider receivable → platform cash → driver payable → tax payable → promo expense), `rides.*` money columns become a read model projected *from* the ledger, driver balance = account balance (O(1), not a 4-query recompute), payout = a journal entry that *must* balance against `driver_payable`. **Why (the edge):** payout ≤ collected + funded-absorption becomes a constraint the DB enforces; T4A, statements, corporate ITC statements and the CRA Part XX return all read one table; reconciliation becomes `Σ cash account == Stripe balance` — one query instead of eleven checks.
- **Is R19's hypothesis right?** Yes on the destination, **no on "only writer" as a near-term step**. Evidence against a switch now: (1) the legs flag has never been on in production (§3.11, UNKNOWN but default off) — the projection has not been exercised at volume; (2) `ledger_service.record_event` *never raises* by design, so a ledger-first request path would need the opposite contract (fail the settlement if the entry cannot be written) — a live-tested surface change; (3) FLOAT8 columns must be migrated first (§3.6) or the projection reads drift; (4) 44 loops and ~20 readers compute money from `rides.*` today (blast radius = every earnings/statement/analytics function in migrations 161/166/227/341/349/350). So: **ledger-first, rides-as-read-model is a Rewrite-only end state; ledger-as-constraint is the Now/Next path.**
- **How:** Now — flip `ledger_double_entry_enabled` on in staging, run the projection over history, fix `financial_event_entries_unbalanced`; add the money-column registry + write guard; meter absorption. Next — make settlement write the header *inside* the atomic RPC (`ledger_atomic_settle_enabled` already does this for card; extend to wallet/corporate/refund/dispute/top-up so the header can never be lost), add `dispute`/`topup` legs, and add the balance-account view for driver payable. Later — read driver balance from the ledger view with a dual-read flag and parity test against `_balance_from_rows`; migrate FLOAT8 columns (§3.6). Rewrite-only — drop money columns from `rides`.
- **Who:** payments owner + `spinr-money-auditor` for every step; accountant for the chart of accounts (tax payable, promo expense, dispute reserve) before Next.
- **When:** Now (flag on + guard + meter) / Next (atomic headers everywhere) / Later (balance view) / Rewrite-only (rides read model).
- **Incremental path (each shippable, flagged):** 1 `ledger_double_entry_enabled` on in staging → 2 reconciliation.py leg checks green 7 days → 3 registry + write guard behind `money_write_guard` (500 in dev, metric in prod) → 4 atomic header RPCs per settlement type → 5 `driver_payable` view + dual-read → 6 FLOAT8 dual-write/dual-read → 7 read model.
- **Cost/effort:** Now S, Next M, Later M, Rewrite L · **Risk:** Now low (dark), Next medium (settlement path), Later medium · **Reversibility:** flags at every step; legs are derived so they can be dropped and re-projected · **Build / Buy / Partner / OSS:** Build (thin; Postgres functions) — a hosted ledger (e.g. Modern Treasury / Formance) would move CRA-retained records outside `ca-central-1` → compliance event, rejected.
- **Advantage type:** operational + trust (auditable 0 %-commission proof, instant driver balance) — defensible only as execution; the ledger itself is table stakes.
- **"Why not?":** it doesn't exist because money truth grew on the ride row first (April 2026) and the ledger was bolted on (migration 58, then 286/287 in August) with "never fail a payment for bookkeeping" as the priority — correct for a launch, wrong for an audit. Simpler alternative that gets 80 %: keep single-entry, but make the header write atomic with every money move and add the absorption meter — that closes the *lost-record* and *unmeasured-liability* risks without the double-entry programme; it does not give balance-as-constraint.


## 8. Top 5 · NOT verified · Human-only questions · Escalations

### (a) Top 5 (ordered by priority score × regulatory weight)
1. **G9 / MONEY-003 — SK PST on rideshare** (ASSUMED, opposite-direction evidence): the only bulletin evidence anyone has found says taxi/"other passenger transportation" is PST-taxable; production charges GST only. One human with an unblocked browser reading PST-46 (rev. Feb 2025) §Transportation Services closes it. Under-collection at Saskatoon launch volume is a remittance liability that compounds daily.
2. **MONEY-007 — completion re-pricing keeps booking-time tax** (HIGH, VERIFIED): GST line ≠ 5 % of the charged subtotal on every re-priced ride unless `fare_lock_enabled` is on (UNKNOWN).
3. **MONEY-009 — top-up chargebacks are invisible to the ledger and leave the balance spendable** (HIGH): the cheapest fraud in the system, and OBS-005's skip path is its symptom.
4. **MONEY-006 + §3.12 — the float class is not closed** (HIGH): canonical estimate path does float `round()`; corporate rides persist it; five new instances outside SR-03. File HIST-001's registry + write guard as one item.
5. **MONEY-010 — platform-funded absorption is unmeasured** (MEDIUM→HIGH under outage): the trust-first policy is right; its cost is unknown and uncapped per driver.
Runner-up: MONEY-013 (reconciliation populations) — because a noisy 02:00 alert is what would hide items 2–5.

### (b) Sweep-catalog coverage — every §3.4 and §4.1 item
§3.4: 28 Handled · 29 Partial · 30 Handled/Partial · 31 Partial · 32 Partial · 33 Partial · 34 Partial (R11) · 35 Unhandled (money step-up) · 36 Partial · 37 Unhandled (no fee path) · 38 Partial — evidence in §4.
§4.1: GST registration **ASSUMED (URL located, consistent)** · supplier of record **ASSUMED** · PST **ASSUMED (contrary evidence)** · Part XX.1 **ASSUMED (likely applies; no return produced)** · T4A **ASSUMED (two likely defects)** · retention **ASSUMED-safe** · corporate ITC **UNKNOWN** · promo/incentive tax **ASSUMED** — all with URLs in §5; none read as a page.
§2.5: fare components ✔ (base/distance/time/booking/min/airport/area fees/promo/tax/tip, `fare_service.py`, `features.py`); surge cap/retroactive/corporate/scheduled ✔ (§3.1–3.2); **stops** ✔ (`_reestimate_fare_for_stops`); **waiting time** — no wait-time charge exists (grep `wait_fee|waiting_fee` = 0, INFERRED) → not a bug, a scope gap; **tolls** — none (SK has none); rounding ✔ `ROUND_HALF_UP`; cancellation fees — R8/R11 (fee amounts flow through `cancellation_fee_driver/admin`, audited here only for payout inclusion); payment-source fallback order ✔ (§4 #38).

### (c) NOT verified (boundaries of this pass)
- No production data: `app_settings` values (`ledger_double_entry_enabled`, `ledger_atomic_settle_enabled`, `fare_lock_enabled`, `stripe_auto_heal_processing`, `min_tip_amount`), migration applied-status (331, 297, 376, 414), any `audit_logs` reconciliation row, `stripe_disputes` contents, Supabase `max-rows`.
- Stripe MCP down: the "unhandled event" list in §3.5 is from general knowledge of Stripe's event catalogue, not fetched.
- Rider-wallet `wallet_apply_delta` callers' keys, `routes/wallet.py` line by line, `routes/loyalty.py`, `routes/quests.py`, `services/incentive_service.py` beyond fraud grep, `routes/admin/wallet_import.py`, `corporate_subscription_service.py`, Spinr Pass invoicing — not read.
- `charge_late_tip` idempotency key on the Stripe call (scenario 31 net-failure case) — not read.
- Frontend: grep-level only (102/75/149 float-money hits in rider/driver/admin); no formatting bug proven beyond `auto-payouts-panel.tsx:176` float `reduce` and `ride-detail.tsx:406-410` float sum; no shared money formatter exists in `shared/`.
- Semgrep not executed; the "47 `float(` outside the allowlist" figure is grep, most are non-money.
- No test was run; every "test exists" claim is from file names/comments.

### (d) Human-only questions
1. Is `fare_lock_enabled` on in production? (decides whether MONEY-007 is live)
2. Is `ledger_double_entry_enabled` on anywhere (staging/prod)? Has the projection ever run over history?
3. Are migrations 331/297/376/414 applied in production (`schema_migrations`)?
4. Has any T4A slip for tax year 2025 been downloaded by a driver (`audit_logs` / `t4a_issued`)? If so MONEY-012 needs re-issue, not just a fix.
5. How many `audit_logs` `stripe_reconciliation` rows in the last 30 days report `discrepancies > 0`, and does anyone read them? (MONEY-013)
6. Has Spinr filed, or been advised it need not file, a Part XX information return for 2024/2025?
7. Who is the merchant of record on the Stripe account (Spinr Inc. presumably) and are drivers on Connect Express with *separate charges & transfers*? (affects supplier-of-record answer)

### (e) Escalations for the accountant / tax counsel (all ASSUMED — primary text unread this session)
1. **SK PST applicability to app-facilitated passenger transport** — read PST-46 §Transportation Services (rev. Feb 2025) and give a dated written determination; if taxable, quantify under-collection since 2026-08-14 and whether to self-assess/remit for past rides (G9).
2. **Supplier of record / who remits GST**: drivers receive `driver_earnings + tax_amount` and are required to hold a BN; Spinr keeps booking/airport fees and (presumably) remits GST on *those*? — confirm the split, whether Spinr acts as billing agent (s.177 ETA election) and therefore whose registration number belongs on receipts and corporate statements (MONEY-011).
3. **BN gate**: should the payout precondition require an RT program account (`RT0001`) rather than a bare 9-digit BN?
4. **Part XX.1 digital-platform reporting**: is Spinr a reporting platform operator; if yes, the Jan 31 return + seller statements have no code path and the export is SIN-free by design.
5. **T4A**: is Spinr the payer (T4A Box 048) or not (Part XX statement only)? Confirm Box 020 must not be filled (MONEY-012) and "more than $500" vs `≥ 500`.
6. **Promo discounts**: coupon (tax on gross, platform remits tax fraction) vs price reduction (tax on net) — code taxes the gross and deducts after (§5 last row).
7. **Driver incentives/bonuses/referral rewards**: taxable supply by the driver to Spinr (GST-inclusive?) or non-consideration payment — affects whether `driver_bonuses` amounts need a GST component and how they sit on the slip.
8. **Absorbed tips and platform-funded refunds/disputes** (MONEY-010): accounting treatment (marketing expense vs bad debt) and whether GST collected on an uncollected fare must still be remitted by the driver who was paid it.
9. **Retention trigger**: confirm "six years from end of tax year" is satisfied by Spinr's ride-date-based 7-year purge for all record types incl. `financial_events` and dispute evidence.
