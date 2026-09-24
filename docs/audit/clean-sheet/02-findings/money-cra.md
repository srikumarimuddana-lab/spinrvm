# R9 — Payments, Money & CRA (Wave W2) — findings

**Lane:** R9 · **Repo HEAD:** `332de89172a0e9bee244610944cf75024617197c` (2026-09-24) · **Mode:** report-and-recommend, static read only. No live Stripe/Supabase data; Stripe MCP down (proxy 403); no state-changing commands.
**Builds on:** `rapid-baseline-2026-09-24/A5-money-ledger-cra.md` (MONEY-001..005 + CRA table), `00-history.md` HIST-001, `W0-SUMMARY.md`. IDs here continue as **MONEY-006+**; A5's cards are re-cited, not re-filed.
**Labels:** VERIFIED (read the code path / primary source fetched) · INFERRED · ASSUMED · UNKNOWN · PROPOSED.
**Written incrementally** (coordinator rule after a usage-limit stop) — sections are appended as each closes; a section marked `(pending)` was not reached yet.

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

## 1. Steelman (pending — will be written after the Attack pass)

---

## 2. Finding cards

(being filled)

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

Conclusion: every path that can *persist* a fare is clamped at or before `booking.py:939`; estimate/display paths are clamped at their builder. `surge_engine.ratio_to_multiplier` (`utils/surge_engine.py:76-81`) tops out at `SURGE_CAP` by construction. **No unclamped path found.** Residual: site 2 is float math on a regulated number (works, but the only non-Decimal clamp — see MONEY-00x float table).

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

### 3.4 Payout ≤ collected invariant — does it exist? (pending — see §3.6 payouts read)

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

(remaining closures pending)

---

## 4. Scenario cards §3.4 (pending)

## 5. CRA / §4.1 (pending)

## 6. Invariants → property tests (pending)

## 7. Rebuild Delta (pending)

## 8. Top 5 · NOT verified · Human-only · Escalations (pending)
