# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend (new dry-run service + test only — **no write path, no live writes performed**) |
| Domain (Sentry tag) | payments |
| Related | Extends PR #3946 (parked); follow-up to `docs/change-log/2026-08-15-legacy-payout-correction-plan.md` and `2026-08-15-legacy-import-gst-preservation.md` |

## 1. Two separate findings this entry covers

### 1a. Stripe cross-check (§0.0 of the dual-run audit) — **the $276.59 correction premise does not hold as originally stated**

Ran the full 15-real-driver Stripe cross-check that PR #3946 was parked pending. Verified directly against `driver_stripe_ledger`/`driver_stripe_payouts`/`drivers.stripe_account_id`:

- **10 of 15 real-UUID driver buckets have no Stripe Connect account at all** (`stripe_account_id IS NULL`) — genuinely owed, zero Stripe evidence possible either way.
- **1 bucket ($22.43, driver `a569909e…`) has a clean payment→payout pair for the exact amount**, single candidate in a 26-row history — "likely already paid," best available evidence given no direct ride/booking ID link exists anywhere in the Stripe-mirror schema.
- **2 buckets are genuinely ambiguous**: `350b5267…` ($33.32) has a `payment` row but **no matching payout anywhere** — evidence trends toward *still owed*, the opposite of what would justify skipping it. `93a899d5…` ($9.45) has **two** equally clean same-amount payment→payout pairs 3 weeks apart — can't tell which (if either) is the relevant one.
- **Structural finding**: `driver_stripe_ledger` has no field that could ever link a row to a specific ride/booking — only Stripe object IDs and a generic `"STRIPE PAYOUT"` description. A "confirmed" verdict is not reachable for any bucket with this schema; "likely" (amount + tight timing, single candidate) is the ceiling.

**Revised total for buckets 1–15**: not $271.24 as originally stated. Genuinely-owed ($185.31) + unresolved ($42.77, treat as owed until resolved) + likely-already-paid, excluded ($22.43) = **$228.08 still owed, range $185.31–$228.08** depending on how the 2 unresolved buckets resolve. Buckets 16–20 ($26.08, no Spinr driver account to check) are unaffected — still blocked on driver re-linking regardless.

**This is why the check exists.** The original $276.59 plan would have re-paid at least one driver (bucket #4, $22.43) who most likely already received that exact amount via Stripe.

### 1b. `old_payout_gst_amount` backfill tool — built, dry-run only, not executed

Confirmed live (2026-08-16): **0 of the 186 already-migrated legacy rides** carry `old_payout_gst_amount` in `legacy_import_metadata` — the PR #3963 fix only helps rows imported after it shipped. Confirmed the raw source is 100% populated: all 271 completed bookings in `bookings.csv` have the field (0 blank, 265 nonzero, summing to $146.07 across that full set) — it was dropped entirely by the pre-fix import code, never missing from source.

Built `backend/services/legacy_gst_backfill_service.py`: reads `bookings.csv`, finds every already-migrated ride missing the field via Supabase (`legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'`, matching the exact filter pattern already proven in `booking_import_service.py`'s own `_fetch_already_imported`), and produces a plan to add it. **Deliberately additive-only and decision-independent**: writes exactly one new JSONB key, never touches `tax_amount`/`tax_breakdown`/any dollar figure. Whichever way the open D1 tax-treatment decision goes, having the raw number preserved in Supabase now is strictly useful.

**No commit path exists in this module** — same posture as `legacy_payout_correction_service.py`. Building the actual UPDATE step is a separate, later action pending explicit go-ahead.

## 2. Risk & impact on existing functionality

- **Blast radius of 1a**: read-only queries against `driver_stripe_ledger`/`driver_stripe_payouts`/`drivers`. No writes. Changes nothing about production — only the confidence level in a still-parked plan.
- **Blast radius of 1b's code**: one new, self-contained module with zero callers anywhere in the codebase (not wired into any route/CLI/loop). Grepped for other `payout_gst_amount`/`old_payout_gst_amount` consumers — only `booking_import_service.py` writes the key (future imports) and this new module reads/plans against it; no overlap risk.
- **No risk to the already-parked PR #3946 payout-correction plan** — this doesn't touch that plan's code, only supersedes its dollar figure with a verified one.

## 3. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/legacy_gst_backfill_service.py` | New file — dry-run backfill plan builder for `old_payout_gst_amount` on already-migrated rides | Close the "0 of 186 have the field" gap without deciding the open tax-treatment question |
| `backend/tests/test_legacy_gst_backfill_service.py` | New file — 4 tests: resolvable row, already-has-field is never a candidate, no-source-match flagged not dropped, report explicitly states tax_amount is untouched | Pin the idempotency and additive-only guarantees |
| `docs/change-log/2026-08-16-gst-backfill-and-stripe-crosscheck.md` | This file | Mandatory Change Impact Log for money-adjacent findings |

## 4. Rollback plan

`git-revert-safe` for the code — no data written, no migration, nothing wired in. The Stripe cross-check (1a) is a read-only investigation with nothing to roll back.

## 5. Verification performed

- [x] Ran the full Stripe cross-check via direct Supabase queries against `driver_stripe_ledger`, `driver_stripe_payouts`, `drivers.stripe_account_id` for all 15 real-UUID buckets
- [x] Verified `legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'` returns exactly 186 rows (matches the earlier `!= '{}'` count) before relying on it in the backfill query
- [x] Ran `pytest backend/tests/test_legacy_gst_backfill_service.py` — 4/4 pass
- [ ] Did not execute the backfill against production — dry-run/plan only, pending explicit go-ahead

## What was NOT verified

- Whether the 2 unresolved Stripe buckets ($42.77 combined) are actually owed or actually paid — genuinely ambiguous from available data, needs either better source linkage (not present in this schema) or a manual decision.
- Whether `driver_stripe_ledger`/`driver_stripe_payouts` mirror the **old app's** Stripe account, the **new app's**, or both — all observed transaction dates are May–August 2026 (post-migration), so this mirror structurally cannot contain old-app-era evidence either way; not independently confirmed against Stripe directly (still blocked on live Stripe MCP access).
- The D1 tax-treatment decision (what `tax_amount` should be for the 186 rows) — still open, this entry doesn't resolve it.
