# MongoDB Legacy Extract Audit — GST Composition & Data Completeness

**Date:** 2026-08-14
**Trigger:** Investigation into why migrated (legacy-imported) rides show a flat, non-fare-scaling `tax_amount` (e.g. $0.14 regardless of ride size) — traced back to a request for the previous app's raw MongoDB export to identify the source of truth.
**Scope:** Structural + financial audit of the raw MongoDB CSV export (`Mongo.zip`, 34 collections) against what actually landed in Supabase via `backend/services/booking_import_service.py`. This is a **read-only source-data audit** — no code or schema changes are proposed here; see the companion reconciliation doc (`2026-08-14-three-ledger-reconciliation.md`) and any follow-up implementation PR for next steps.
**Auditor:** Claude Code, reporting as senior DBA / ride-share financial auditor.
**Method:** Loaded the export with Python's stdlib `csv` module (not naive delimiter-splitting — several free-text fields contain embedded commas) and cross-referenced every dollar figure against live production Supabase (`soavhtdhefowwvforzwb`, `ca-central-1`).

---

## Executive summary

| # | Finding | Severity | Evidence |
|---|---|---|---|
| 1 | **The source export is a shared, multi-tenant database, not Spinr-only.** Of 1,210 raw `bookings` rows, only 475 have a Canadian customer *and* Canadian driver (`country_code = 1`); the remainder includes at least one India-based booking (Chandigarh pickup/dropoff) from what appears to be the SaaS vendor's other-tenant/demo traffic. | **Informational — methodology** | Row-level inspection, `country_code` join against `customers.csv`/`drivers.csv` |
| 2 | **The migrated `tax_amount` field is not "total tax" — it's exactly `commission_gst_amount`, GST on Spinr's own commission only.** It matches `commission_gst_amount` in 220/224 completed Canada bookings (mean abs diff $0.008, rounding noise). A second, real, fare-scaling tax component (`payout_gst_amount`, GST on the *driver's* payout) exists in the source data and was never referenced by the importer at all. | **P1 — quantified data gap, $ impact below** | Field-by-field diff across 224 completed Canada bookings |
| 3 | **$105.17 of `payout_gst_amount` across the 224-ride migration batch never reached Supabase.** Per-driver example (Alexander Gavu, 17 rides): migrated tax sums to $2.38 (matches Supabase exactly); the true total is $12.44 — **$10.06 missing**, unrecoverable from anything currently in Supabase, but present in this export. | **P1 — quantified, backfillable** | `commission_gst_amount + payout_gst_amount` vs `rides.tax_amount`, all 224 rows |
| 4 | **9 of Gavu's 17 rides show a *negative* `admin_comission_amount`** in `driverearnings.csv` (e.g. −$7.83) — meaning the old app paid the driver more than it collected from the rider on those specific trips. This is internally consistent (not corrupted data) and explains an earlier-flagged oddity where migrated `grand_total` reads lower than `total_fare`. Reads as a genuine historical driver-guarantee/subsidized-pricing program, not a data error. | **P2 — informational, explains a prior open question** | `driver_earnings + admin_comission_amount + gst ≈ booking_amount` holds even when commission is negative |
| 5 | `drivers.gst_registered = false` but `drivers.gst_bn = "729817031RT0001"` is populated for Gavu in current Supabase — a business number on file for a driver flagged as not GST-registered. Small, unrelated data-integrity nit found in passing. | **P3 — minor, unrelated** | Direct Supabase query |
| 6 | Three collections relevant to money are **empty in this export** (`taxes.csv`, `driverpayouthistories.csv`, `surchargehistories.csv` — header row only), and `serviceareapricings.gst_percentage` reads `0` on every sampled row. **The percentage that actually produced `commission_gst_amount`/`payout_gst_amount` is not recoverable from static config in this export** — it was computed in the old app's live code, not stored. | **P2 — informational, bounds what re-derivation is possible** | Direct file inspection |
| 7 | `payments.csv` (372 rows), `banks.csv` (157 rows, has its own per-driver `gst` field), `coupons.csv` (11 legacy promo codes), and `wallets.csv` (13 referral/wallet transactions) exist in the export with no counterpart audit yet — flagged for the reconciliation pass, not analyzed for $ impact here. | **P2 — scoped to companion doc** | Structural inventory only |

---

## Finding 1 — the export must be filtered before it's trustworthy

Method: joined `bookings.customer_id` → `customers.csv._id` and `bookings.driver_id` → `drivers.csv._id`, then filtered on `country_code = '1'` on both sides (matches the existing migration script's own `CANADA_COUNTRY_CODE = "1"` constant and its comment about "test accounts (country code 91 / yopmail addresses) whose rides are not real").

```
raw bookings:                                   1,210
customer_cc == '1':                             1,096
customer_cc == '1' AND driver_cc == '1':           475
  of those, booking_status == 'completed':          224   ← matches the live Supabase migration batch exactly
  cancelled:                                        238
  failed:                                            13
```

The exact match to Supabase's 224-row batch (confirmed independently in `docs/audit/2026-08-13-migrated-data-visibility-audit.md`, "224 legacy rides exist in production") is the load-bearing check here — it confirms this file and the live migrated data describe the identical ride set, so every dollar comparison below is apples-to-apples.

## Finding 2 & 3 — the GST split, and its dollar cost

`bookings.csv` carries three tax-related columns:

| Column | What it represents | Landed in Supabase? |
|---|---|---|
| `commission_gst_amount` | GST on **Spinr's own commission/platform fee** | ✅ — imported as `rides.tax_amount` (see `booking_import_service.py:450,539`, which reads a single `gst` field) |
| `payout_gst_amount` | GST on **the driver's payout/earnings portion** | ❌ — not referenced anywhere in the importer or anywhere in the `rides` schema |
| `gst` | Equal to `commission_gst_amount` in 220/224 rows exactly (mean abs diff $0.008 — rounding) | (same field as `commission_gst_amount`, above) |

This resolves the "why is migrated tax flat at ~$0.14 regardless of fare" question raised in prior conversation: `commission_gst_amount` tracks GST on a small **fixed** per-ride commission (`app_commission_unit: "fixed"`, `app_commission: 1` observed on sampled rows) — a fixed-dollar commission produces near-fixed GST, independent of ride size. `payout_gst_amount`, by contrast, scales with fare as a real percentage tax should:

| Booking | Base Fare | `commission_gst_amount` (migrated) | `payout_gst_amount` (missing) | True total GST |
|---|---|---|---|---|
| CB4608768 | $6.20 | $0.14 | $0.31 | $0.45 |
| CB3968128 | $10.96 | $0.14 | $0.55 | $0.69 |
| CB5806336 | $20.06 | $0.14 | $1.00 | $1.14 |
| CB3603456 | $21.04 | $0.14 | $1.05 | $1.19 |

**Batch-wide dollar impact (224 completed Canada bookings):**

```
sum(commission_gst_amount) [= migrated tax_amount]:  $484.43
sum(payout_gst_amount)     [missing from Supabase]:  $105.17
sum(true total GST):                                 $589.60
```

**Gavu-specific (17 rides), for direct before/after reference:**

```
Migrated rides.tax_amount sum:                $2.38   (confirmed exact match to Supabase)
True total GST (commission + payout):        $12.44
Missing amount:                              $10.06
```

This is not a placeholder-value or migration-parsing bug — the migration faithfully imported exactly what the source `gst` field contained. The gap is a **column selection gap**: the importer read `commission_gst_amount` under the name `gst` and never looked at `payout_gst_amount`.

## Finding 4 — negative commission rows are a real subsidy signal, not corruption

`driverearnings.csv` carries its own `admin_comission_amount` per booking. For 9 of Gavu's 17 rides, this value is negative (e.g. −$7.83, −$7.63 ×2, −$5.84). The arithmetic still closes: `driver amount + admin_comission_amount + gst ≈ booking_amount` holds whether commission is positive or negative, meaning these aren't broken rows — they're rides where **Spinr (in the old app) paid the driver more than it collected from the rider**, consistent with a driver-guarantee or launch-subsidy program rather than a data-entry error.

This directly explains a previously-unresolved oddity: several of Gavu's migrated rides show `rides.grand_total` *lower* than `rides.total_fare` in current Supabase, which had been flagged as a possible migration artifact without an explanation. It isn't one — it's inherited business history. **Recommendation: preserve this as-is** (don't "correct" `admin_earnings` retroactively to a non-negative floor) — it's a legitimate historical fact about the old app's pricing strategy, and normalizing it away would erase real information Spinr's own finance team may want (e.g. "what did the launch subsidy program actually cost, per driver, per month").

## Finding 5 — GST-registration contradiction (Gavu, current Supabase)

```sql
select gst_registered, gst_bn from drivers where id = '93a899d5-431b-4743-afac-034cdf8c3d6c';
-- gst_registered: false | gst_bn: "729817031RT0001"
```

A populated GST business number on a driver flagged as *not* GST-registered is a contradiction on its face — either the boolean is stale or the BN shouldn't be present. Unrelated to the tax-amount findings above; flagged because it surfaced during the same driver-level check. Small enough to fix independent of the GST-backfill work.

## Finding 6 — the real GST percentage isn't recoverable from static config

`taxes.csv`, `driverpayouthistories.csv`, and `surchargehistories.csv` are empty in this export (header row only, no data). `serviceareapricings.csv` has a `gst_percentage` column, but it reads `0` on every sampled row — meaning the percentage that actually produced `commission_gst_amount`/`payout_gst_amount` per booking was computed by the old app's live business logic, not read from a static, exportable config table. **This bounds what's achievable**: `payout_gst_amount` can be backfilled from this export (it's a real recorded value per booking), but the underlying *rate* cannot be independently re-derived or validated against a rate table — only trusted as recorded, the same caveat already noted for `commission_gst_amount`/`gst` in prior conversation.

## Finding 7 — inventory of collections not yet analyzed for $ impact

Flagged here for completeness; financial cross-checking happens in the companion reconciliation doc:

- **`payments.csv`** (372 rows) — independent per-booking record with its own `commision_amount`, `tax`, `payout_amount`, `payout_to_driver`, `payout_initiated` fields. A third ledger alongside `bookings.csv` and `driverearnings.csv`.
- **`banks.csv`** (157 rows) — driver banking/payout details, including a per-record `gst` field distinct from `drivers.gst_bn`/`gst_registered` in current Supabase.
- **`coupons.csv`** (11 rows) — legacy promo code definitions (discount %, min booking amount, max discount, validity window). Relevant if any migrated ride carries a `promo_code`/`discount_amount` in Supabase without the original coupon terms preserved.
- **`wallets.csv`** (13 rows) — referral/wallet credit transactions from the old app; relevant if any migrated rider/driver wallet balance should reflect this history.

---

## What this doc does NOT do

- No code or schema changes.
- No claim that `payments.csv`/`driverearnings.csv`/`bookings.csv` agree with each other beyond the specific GST-field comparison above — that 3-way cross-check is the subject of the companion reconciliation doc.
- No recommendation on *how* to surface the newly-found `payout_gst_amount` to drivers/riders/admin (separate design decision, tracked for the implementation PR).

## Recommended next steps (tracked, not actioned here)

1. Companion 3-way ledger reconciliation (`bookings` vs `driverearnings` vs `payments`) — **done**, see `2026-08-14-three-ledger-reconciliation.md`.
2. A scoped, additive backfill migration adding `payout_gst_amount` (or an equivalent) to `rides` for the 224 already-migrated rows, sourced from this export — proposed as a separate implementation PR, gated on the reconciliation doc above.
3. Decide, as a product/finance question (not a code question), how "tax" should be presented to a driver now that there are two real components — total tax collected vs. GST specific to their own payout vs. GST on Spinr's commission.
4. Fix the `gst_registered`/`gst_bn` contradiction (Finding 5) independently — small, unrelated to the GST-backfill scope.
