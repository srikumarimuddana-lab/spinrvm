# Dual-Run Cutover Audit — Phase 2: Migration Completeness

**Date:** 2026-08-15 · **Posture:** AUDIT-ONLY — mapping plan is a **draft to review**, nothing executed. Access boundary unchanged: the raw `Mongo.zip` export is not on this machine, so 2.1's inventory is doc-derived, not re-verified against the files.

> **Update (2026-08-19):** the access boundary above no longer holds — the user supplied the raw
> `Mongo.zip` directly. See `docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md` for
> the full collection-by-collection re-analysis against the actual file; it re-scores most of §2.2's
> table below against real data (short version: the 6 "never opened" collections are now opened, and
> most of them are either empty or too small to be the blocker they looked like on paper). That doc
> also notes what's still unresolved by this same-vintage (2026-07-26) file and needs the fresh
> Oct-30 pull instead — treat this file's §2.1/§2.2 as superseded where the two disagree, not as a
> duplicate source of truth.

---

## Plain-English summary

The plan for moving the old app's full history into the new database mostly reuses the import machinery that already exists — but about a third of the old data **doesn't fit** that machinery and needs its own design: money the old app still owes drivers must land as reviewable pending items (not "already settled" records), cancelled rides need GPS fields the current importer never touches, any real prepaid wallet balance needs the locked money-safety database function (not a plain insert), and the two incompatible old-ID systems need a proper crosswalk table before driver↔trip links can be trusted. Six collections — subscriptions, driver subscriptions, user passes, referrals, extra orders, and restaurants/vendors/fleets — have **never been opened by anyone**, so the plan deliberately refuses to guess their contents; they may not even be Spinr's data (the old platform was multi-tenant). Nothing in this phase is executable until three things exist: a fresh final export, the ID-mapping source files, and someone opening those unexamined collections.

## 2.1 Collection inventory — status: INCOMPLETE BY ACCESS, stated plainly

- Prior audits opened **11 of ~34** collections: bookings, customers, drivers, taxes (empty), driverpayouthistories (empty), surchargehistories (empty), serviceareapricings, payments, banks, coupons, wallets.
- The remaining ~23 (incl. `referrals`, `userpasses`, `subscriptions`, `driversubscriptions`, `extraorders`, `restaurants`, `vendors`, `fleets`) are **not enumerable from this machine** — the export isn't here. Per the no-silent-scope-narrowing rule: 2.1's exit criterion ("complete inventory with nothing unaccounted for") is **not met and cannot be met this session**. It becomes step one of the fresh-export work.
- Risk framing: any of the unopened collections could carry pending money (prepaid passes/subscriptions) or PII. "Probably other-tenant" is an inference from the multi-tenant finding, not a verification — confirm via a tenant/company-id field before excluding anything.

## 2.2 Migration mapping plan (draft — reviewed by spinr-migration-reviewer)

Pattern reused: `legacy_import_metadata` JSONB stamp (`{source, old_*_id, imported_at, batch}`), phone-first matching, idempotency by metadata re-query. Next free migration number at time of writing: **315** (re-check before filing).

| # | Old-app category | Target | Fits existing pattern? | Flags |
|---|---|---|---|---|
| 1 | Bookings (completed) | `rides` + offsetting `payouts` | Yes — shipped (186 rows landed) | Baseline; must also add `payout_gst_amount` this time (see 2.3) |
| 2 | Bookings (cancelled/failed) | `rides`, new status branch | Partially | Importer hard-filters completed-only today; skip payout-offset logic, keep GPS+timestamps |
| 3 | Customers | `users` | Code exists but **0/1,134 live rows carry the stamp** — verify actual run behavior, don't trust the code path | Must not stamp `created_at = now()` (corrupts promo eligibility + future B23 logic) |
| 4 | Drivers | `drivers`+`users` | Yes (187/211 stamped) | Resolve the 22 unmarked rows first |
| 5 | Payments (settled) | `payouts` type `legacy_import` | Yes | — |
| 6 | Payments (**158 'due'**, incl. 108 unresolvable) | New reviewable shape (`legacy_pending_payout_review` or sub-status) | **No** — existing pattern assumes settled money | Never auto-payable; requires Stripe cross-check (P0 §0.3) first; extends PR #3946, doesn't duplicate it |
| 7 | Banks | Stripe mirror or encrypted staging table | Unconfirmed — collection never opened | If raw bank numbers: PIPEDA minimization, import only what payout requires |
| 8 | Coupons | `promotions` + redemption tables | Partially | Must mark "redeemed in old app" to avoid double-count against Spinr velocity caps |
| 9 | Wallets | `wallets`/`wallet_transactions` | **No** for balances — money mutation needs a `SECURITY DEFINER` RPC with row lock (à la `corporate_wallet_apply_delta`), not inserts | — |
| 10–11 | subscriptions / driversubscriptions / userpasses | Unknown | **Unclassified — open the collections first** | Could be prepaid money |
| 12 | referrals | `referral_*` | **No** as plain backfill | Import with "already-paid, no-payout" flag or old referral chains could re-trigger payouts |
| 13 | extraorders | Likely none | Unknown | Explicit include/exclude decision, not silent drop |
| 14 | restaurants/vendors/fleets | Likely none (other-tenant) | — | Confirm via tenant field before excluding — a false "other tenant" call would discard a real customer's data |

Crosswalk requirement: a `legacy_id_crosswalk` table (old Mongo ObjectId ↔ old numeric driver ID ↔ Spinr UUID, per entity) — IDs only, no PII, RLS service-role-only, shipped in its own append-only migration, never renamed post-merge. Reversibility = "delete rows matching this batch's `legacy_import_metadata->>'batch'`", stated in-file (git revert is not a data rollback).

## 2.3 Retention cross-check (per `regulatory-sk.md`)

| Requirement | Draft coverage | Silent-drop risk |
|---|---|---|
| Trip record 7 yr | Completed OK (legacy `created_at` carried). Cancelled/failed: **not imported at all today** — must be added | High |
| Driver/vehicle linkage 7 yr | **Gap (P0 §0.4)** — no vehicle identity imported; `driver_vehicle_history` not backfilled; "current vehicle" is not a substitute | Explicit |
| GPS pickup/dropoff 3 yr | Completed OK. Cancelled bookings with a pickup point: easiest field to drop in a "cheap" cancelled-import path — carry lat/lng + legacy timestamp even though money logic is skipped | High |
| Insurance periods 7 yr | **Structural absence, no source data.** Do NOT fabricate synthetic period rows (misrepresents an audit trail). Decision needed: (a) SGI/legal-approved reconstructed rows clearly flagged as reconstructed, or (b) documented accepted compliance exception. Legal call, not engineering | Blocker |
| Tax line items 7 yr | `payout_gst_amount` was dropped once already ($105.17); the final import must map it explicitly (second tax component or `area_fees_breakdown` entry) | High — same mapping reuse would drop it again |

## Prerequisites before any of this is executable

1. Fresh final old-app export, all ~34 collections, taken under a write freeze (see P3 runbook step 4).
2. ID-crosswalk source data (old `drivers`/`customers` collections mapping ObjectId ↔ numeric ID ↔ phone).
3. The six unopened collections inspected and classified (tenant field, row counts, money fields).
4. Decisions: the 158 'due' rows (Stripe cross-check first), insurance-period gap (legal), 224-vs-186 discrepancy (must be explained before layering more data on).

## Phase 2 exit criterion — status

Not met, by access: the inventory cannot be completed without the export. The mapping plan is drafted and retention-checked; the gap list above is the honest boundary.

## What was NOT verified
- Contents of any old-app collection (no export on this machine) — the mapping table's "unknown/unconfirmed" rows are exactly that.
- Whether the rider importer's stamp omission was code-path or run-order (needs the original run's context or a re-run against the export).
