# Dual-Run Cutover Audit — Phase 0: Money & Regulatory Exposure (CRITICAL)

**Date:** 2026-08-15 · **Decommission target:** Oct 31, 2026 (tentative) · **Posture:** AUDIT-ONLY — no data or code changed.
**Data access this session:** NO live old-app (MongoDB) access. Sources = (a) live production Supabase (`soavhtdhefowwvforzwb`), which contains the one-time production cut imported 2026-07-29; (b) filter chains already documented in `docs/audit/2026-08-14-*` and PR #3946. The raw `Mongo.zip` CSV export is **not** on this audit machine. Stripe dashboard/API: no access — Stripe-side checks deferred (checklist in §0.3).

---

## Plain-English summary (read this first)

1. **Money owed by the old app:** the old app's own records say it still owes drivers **$276.59** across 20 drivers (as of the export those records came from). None of that money is pending inside the new Spinr system — the new system's books show **zero** pending payouts, zero pending refunds, and **one** open Stripe dispute for **$16.63** that needs a response. But because the old app is still running and we can't see inside it, the $276.59 is a *floor*, not a current total. **We cannot state today's true pending-money number without a fresh export from the old app.**
2. **Could both apps dispatch or pay the same driver at once?** Yes, structurally. The new system has *no idea the old app exists* — nothing in its code checks whether a driver is mid-trip or being paid elsewhere. Today the practical risk is low because **no driver is currently online in the new system** (0 of 211), but 150 imported drivers are fully approved and could go online any time, and 104 of them already have Stripe payout accounts on file — possibly the *same* Stripe accounts the old app pays into. The safeguard has to be operational (roster coordination), because the code provides none.
3. **Regulatory record-keeping:** two real gaps. (a) None of the 186 imported rides has the insurance-period audit trail Saskatchewan's insurer (SGI) expects kept for 7 years — the old app never tracked it and the import didn't create it. (b) Every ride completed in the old app **after** the production cut exists only on infrastructure scheduled for teardown; there is **no plan anywhere in this repo** for a final export before Oct 31. If the old app is switched off without one, those trip records, GPS points, and tax figures are gone permanently.
4. **A bookkeeping surprise:** prior audit docs say 224 legacy rides were imported; live production holds **186** (one batch, 2026-07-29) plus one organic cancelled ride. Nothing was soft-deleted. The 38-ride difference is unexplained and needs an answer before the final migration — it may just be two different import passes, but "may" isn't good enough for a decommission gate.

**Bottom line:** nothing is on fire today, but three things block a safe Oct 31 decommission as of now: a fresh old-app export (for true pending money + post-cut rides), an operational answer to double-dispatch/double-payout during dual-run, and a decision on the missing insurance-period trail for imported rides.

---

## 0.1 — Pending-money audit (technical)

### New-system (Supabase) side — verified live this session

Filter chains (source → filters → rows → $):

| Check | Query basis | Rows | $ |
|---|---|---|---|
| Pending payouts | `payouts` where status not completed → **0 rows** (all 222 rows completed: 59 × `legacy_import` $2,123.29 offsets + 163 × `stripe_sync` $3,045.64 historical) | 0 | $0.00 |
| Pending Stripe payouts | `driver_stripe_payouts` where status ≠ 'paid' → 0 (149 paid, $2,949.12) | 0 | $0.00 |
| Open in-app disputes | `disputes` → table empty | 0 | $0.00 |
| **Open Stripe disputes** | `stripe_disputes` where status='needs_response' | **1** | **$16.63** |
| Orphan refunds | `stripe_orphan_refunds` → table empty | 0 | $0.00 |

### Old-app side — **stale by construction, no live access**

- PR #3946 chain (from `payments.csv`, 372 rows → `pending_amount_status='due'` 158 → booking-resolvable 50 → Canada-tenant filter 35 rows / 20 drivers): **$276.59** ($250.51 payable today, $20.73 blocked on driver re-link, $5.35 payable once linked, $0.00 no-op). **As-of: the CSV export date; the old app has kept taking bookings since.**
- 108 further `due` rows were unverifiable from the CSVs (ObjectIDs predate all other export files) — **not** resolved, could be real money.
- **Verdict: the P0 exit criterion "exact pending money right now" is NOT satisfiable with current access.** Requires a fresh old-app export (or read access). Everything above is a floor.

### Population/state facts (live Supabase, 2026-08-15)

- `rides`: 187 total; 186 legacy (`legacy_import_metadata <> '{}'`, batch `20260729184745` imported 2026-07-29, trip dates 2026-04-01 → 2026-07-26, $2,161.37 fares, 3 with NULL driver_id); 1 organic cancelled ride (2026-08-08) — the new app's only non-imported booking.
- `drivers`: 211 total; 189 legacy-marked; 22 unmarked created 07-26→07-31 (likely CLI import without metadata — **open question**). 150 active+verified (dispatchable on go-online), 104 with `stripe_account_id`. **0 online / 0 available right now.**
- `users`: 1,134; **0** carry legacy metadata despite the documented rider import — the rider importer evidently does not stamp `users.legacy_import_metadata`. Open question for P1 identity mapping.
- **Discrepancy:** `docs/audit/2026-08-14-*` reconciled against a 224-ride batch; live production holds 186 with zero `deleted_at`. Unexplained; must be resolved before final migration (candidates: second filtered re-import, different environment, or hard deletes).

## 0.2 — Dual-run collision risk (technical)

Full agent reports preserved in this session; key verified points:

- Dispatch eligibility (`services/dispatch_service.py:369-383`, `claim_driver` :555-566) and go-online gating (`routes/drivers/status.py:108-329`) consult only local state + Redis presence. **Zero cross-system awareness** — no field, join, webhook, or shared DB knows the old app exists. Verified by direct read + exhaustive grep.
- Normal signup (`routes/auth.py`) does not (and cannot) check the old app: a person active on the old platform can become fully active here. Import dedup (phone→email, `driver_import_service.py:519-570`) only prevents duplicate *Spinr* rows.
- Web-admin driver import forces `needs_review` (cannot go online until manually approved); the CLI import path (`scripts/import_saskatoon_drivers.py`) can land drivers `active`/`is_verified` immediately dispatchable — no onboarding gate.
- **Double-payout vector:** `stripe_mapping_import_service.py:1-18` explicitly anticipates old and new apps may share the *same* Stripe Connect accounts. `request_payout` (`routes/drivers/payouts.py:799-883`) gates only on SIN + locally-computed `payable_balance`. No exclusivity flag, no external-payer concept anywhere.
- **Reconciliation boundary: none.** `legacy_import_metadata` gates only earnings-report exclusion (`utils/legacy_rides.py`), never dispatch/login/payout. This is the finding, stated plainly: the two systems have zero awareness of each other's live state.
- Consequences if collision occurs: conflicting append-only `driver_insurance_periods` records (two systems claiming TNC coverage truth for the same instant), wrong rider ETAs, safety tooling reasoning from a false trip picture.
- Incidental protection found: imported rides carry no `stripe_charge_id`/`payment_intent_id`, so dispute-refund resolution falls to `manual_required` instead of calling Stripe (`routes/disputes.py:222`) — safety **by omission**, no guard/test enforces it.

## 0.3 — Stripe reconciliation: DB-side done, Stripe-side DEFERRED

DB-side: covered above. **Stripe-side checklist for when access is granted** (redo trigger — user will share access later):
1. Old-account transfers/payouts created after the production cut for any `acct_` also present in `drivers.stripe_account_id` (live double-payment signal).
2. Diff of Connect account sets: old app vs `select stripe_account_id from drivers` (104 IDs here).
3. In-flight (`pending`/`in_transit`) transfers/payouts on the old platform account at cutover.
4. Confirm shared-account vs migration-mapping scenario (`docs/runbooks/stripe-legacy-migration.md` Step 1) — determines whether ID joins are even valid.
5. Reconcile PR #3946's 108 unresolved rows against Stripe/old-app directly.
6. Connect account status (active/closed) for the 15 payable drivers in PR #3946.
7. Cross-check `stripe_orphan_refunds` (currently empty) against old-app charge IDs post-cut.

## 0.4 — Regulatory retention (technical)

- **GPS + trip record for the 186 imported rides: intact.** Importer maps pickup/drop coords and — critically — carries the *legacy* `created_at` (`booking_import_service.py:421,548`), so the 3-yr GPS / 7-yr trip purge clocks (`utils/retention_purge.py`, keyed on `rides.created_at`) run from true trip dates. PIPEDA-correct.
- **Vehicle-at-trip-time: gap.** Importer writes no vehicle identity (only generic `vehicle_type_id`); `driver_vehicle_history` (migration 157) not backfilled. 7-yr driver/*vehicle* linkage requirement only reconstructable as "driver's current vehicle."
- **Insurance periods: BLOCKER-level gap.** Zero `driver_insurance_periods` rows exist for imported rides (importer never touches the table; migration 65's backfill covers only currently-online drivers). SGI 7-yr per-period trail is structurally absent for every old-app ride. Old app almost certainly never recorded the concept (inference, not verified).
- **Post-cut rides: unprotected.** No incremental import, no final-export runbook anywhere in the repo. Decommission before a final export = permanent loss of trip/GPS/tax records for every old-app ride since 2026-07-29 (ride import cut) — a compliance gap in the making, not yet a violation.
- PII: importer stores only opaque legacy IDs in metadata, no over-collection found.

## Verdicts

| Check | Verdict |
|---|---|
| 0.1 New-system pending money | **Clean** ($16.63 Stripe dispute needs response — action, not migration blocker) |
| 0.1 Old-app pending money | **Needs a decision** — true current figure unknowable without fresh export; $276.59+ floor stands |
| 0.1 224-vs-186 ride discrepancy | **Needs remediation/explanation** before final migration |
| 0.2 Collision risk | **Needs remediation (operational)** — double-dispatch & double-payout structurally possible; risk latent today (0 drivers online) |
| 0.3 Stripe | **Deferred** (DB-side clean-by-omission; Stripe-side unverified) |
| 0.4 Retention | **Needs remediation** — insurance-period gap (imported rides) + no final-export plan (post-cut rides) |

## What was NOT verified

- Anything inside the live old app (bookings, payments, refunds, disputes since its export) — no access.
- Anything inside Stripe (both platforms) — no access.
- Whether an old-app final-export plan exists outside this repo (ops calendar, vendor contract).
- Whether the old vendor app records anything insurance-period-like (inferred absent).
- Runtime behavior was verified by code reading + live SQL, not by executing dispatch/payout paths against seeded data.
- Rider-side double-booking (rider books same trip in both apps) — scoped out of 0.2's driver focus; picked up in P1.4 fraud review.
