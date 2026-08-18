# Dual-Run Cutover Audit — Phase 1: Financial & Identity Reconciliation (+ P3.1 pulled forward)

**Date:** 2026-08-15 · **Posture:** AUDIT-ONLY. Same access boundaries as Phase 0 (no old-app DB, no Stripe, no raw CSV export on this machine).

---

## Plain-English summary

1. **Who maps to whom (old app ↔ new app):** the driver mapping is real but was built from **two different old-system exports with incompatible ID schemes** — imported rides reference drivers by Mongo ObjectIds while imported drivers carry 10-digit numeric IDs from a separate Saskatoon driver CSV. The two sets cannot be cross-checked against each other inside Supabase (0 of 62 ride-side driver IDs match any driver-side ID — a namespace mismatch, not necessarily missing people, since rides do link to real driver accounts via phone-matching done at import time). Riders are worse: the 1,134 imported riders carry **no legacy marker at all**, so "which old-app customer is this?" is answerable only for the 79 customers referenced by imported rides. A full old↔new identity map — the thing this phase is supposed to produce — **cannot be built without the old export or DB**. On the plus side: no duplicate phones, emails, or Stripe accounts exist in the new system today.
2. **Corporate accounts:** nobody has ever opened the old export's fleet/subscription/pass collections (only 11 of ~34 collections were audited), so whether the old app holds prepaid corporate/subscription money is an **unknown, not a cleared risk**. If such balances exist, they have no migration path — the new corporate wallet system has zero ability to seed a starting balance from the old app.
3. **Tax consistency: not consistent.** The old app split GST into two internal pieces and only the small flat piece was migrated; the new app charges one clean GST 5% line (PST currently off after the Aug 14 revert). Practical impact today is tiny (one organic ride so far), but any report summing tax across both systems adds a partial number to a complete one, and **driver year-end T4A/GST summaries will understate GST** for old-app rides by a quantified $105.17 across the imported batch.
4. **Fraud exposure at launch:** the referral/promo system has no idea a second app or a pre-existing customer base exists. Old-app customers outside the backfill look like brand-new users (and imported-but-unmatched riders get `created_at = import date`, making years-old customers "new" for day-based promos). The rider referral program pays $5+$5 after a single ride with no velocity cap and no phone/payment cross-check — the most attackable surface during launch-week marketing.
5. **Monitoring (P3.1, pulled forward):** confirmed — **nothing today would catch a dual-run collision live**. No metric, alert, log line, or audit row distinguishes an imported driver's first go-online or first payout from routine traffic; the dispatch metrics carry no labels that could surface ghost-driver offer timeouts. Three cheap, additive signals were identified (audit-log on legacy driver first go-online; labeled go-online counter; legacy-driver payout counter) — all same-file additions to code that already runs.

---

## 1.1 Identity mapping (technical — live Supabase + repo)

Verified live (2026-08-15):
- `drivers`: 211 rows; 187 carry `legacy_import_metadata.old_driver_id` (numeric, source `legacy_saskatoon_driver_import`; 111 also carry `stripe_migration`); 189 total legacy-marked; 22 unmarked rows created 07-26→07-31 (open question — likely early CLI import without metadata).
- `users`: 1,134 rows; **0** legacy-marked (rider importer stamps nothing on `users`); created 2026-02-14→2026-08-15.
- `rides` (186 legacy): metadata carries Mongo ObjectId `old_driver_id` (62 distinct) and `old_customer_id` (79 distinct). **0/62 join to any driver's numeric `old_driver_id`** — two different export namespaces (booking export = Mongo `_id`s; driver import = numeric vendor IDs). Ride→driver linkage exists only via the FK resolved by phone-match at import time (3 rides have NULL driver_id — the known $20.73 blocked bucket in PR #3946).
- Ambiguity checks: 0 duplicate phones in `users`, 0 duplicate emails, 0 duplicate `stripe_account_id` in `drivers`.

**Verdict: needs a decision.** The full-population old↔new map required by the P1 exit criterion is **not constructible from current access** — it needs the old export's `drivers.csv`/`customers.csv` (ObjectId ↔ phone) or old-DB access. What exists is consistent but partial: every imported ride resolves to a real rider; all but 3 resolve to a real driver.

## 1.2 Corporate continuity (technical)

- The 2026-08-14 extract audit opened 11 of ~34 collections; none of `fleets`/`vendors`/`restaurants`/`subscriptions`/`driversubscriptions`/`userpasses` is analyzed anywhere in the repo (grep across all audit docs: zero corporate-sense hits).
- No import service touches anything corporate/fleet/subscription/pass-shaped (grep across all 5 import services: zero hits).
- `corporate_wallet_apply_delta`/`corporate_allowance_apply_delta` (current body: migration 297) have no legacy/source-system/imported-balance concept — a mid-cycle old-app corporate customer would start from zero with no true-up mechanism.
- **Verdict: unknown-not-cleared.** Old-app corporate/prepaid money is un-inventoried; if it exists it joins the "pending money we can't see" bucket. Resolution requires opening those collections in the export.

## 1.3 Tax-field consistency (technical)

- New app today: GST 5% only (`features.py:973-976`; `gst_enabled` default-on, `pst_enabled` default-off after `2026-08-14-sk-pst-revert.md` reset all 4 SK areas). Receipts itemize each tax as its own row (`utils/receipt_pdf.py:95-118`) — line-item rule compliant; PST applicability itself remains an unresolved policy question per the revert doc.
- Old app: GST split across `commission_gst_amount` (flat ~$0.14, imported as `rides.tax_amount`) + `payout_gst_amount` (fare-scaling, **never imported**; $105.17 across the batch).
- **Verdict: NOT consistent** in composition. Old-app GST in Supabase is structurally partial; new-app GST is complete. T4A/driver statements (`utils/driver_statement.py:133-140`, `utils/t4a_pdf.py:172-174`) source from `tax_amount` → understated annual GST for dual-system drivers. Quantified, backfillable from the export (which must therefore be preserved).

## 1.4 Fraud/duplicate-account exposure (technical)

- Self-referral guard = `user_id` equality only (`routes/users.py:989`, `routes/drivers/referrals.py:240`). No phone/device/payment-method cross-check, no per-referrer velocity cap (`utils/referral_payout.py`: absent). Rider program: 1 ride → $5+$5.
- Promo eligibility (`routes/promotions.py:496,553-579`) keys on Spinr-local `total_rides` and `created_at`. No legacy/import awareness anywhere in promo/referral code (grep: zero hits).
- `rider_import_service.py:315` stamps `created_at = now()` on net-new imported riders → old customers read as brand-new for `new_user_days` promos.
- Phone-matched backfilled users are accidentally protected (OTP login lands on the existing row; imported ride history blocks first-ride promos). Old-app customers **outside** the backfill are indistinguishable from new users.
- Rider double-booking: one-active-ride check is Spinr-local only (`routes/rides/booking.py:394-404`); cross-app double-booking undetectable by construction.
- Top launch-window vectors, ranked: (1) rider-referral farming (low friction, no velocity cap, no identity overlap check); (2) new-user promo collection by un-backfilled old-app customers; (3) `new_user_days` promos hitting import-created accounts with reset `created_at`.

## P3.1 Dual-run collision monitoring (pulled forward — technical)

- **Nothing would catch a collision live.** Go-online handler (`routes/drivers/status.py:108-715`) logs but emits no metric and never reads `legacy_import_metadata`; payout paths log failures but carry no cross-platform Stripe awareness; dispatch metrics (`spinr_dispatch_offer_*`) are unlabeled aggregates — a ghost-driver timeout spike is invisible inside normal supply noise; both daily reconciliation loops reconcile Spinr-vs-Spinr/Stripe only.
- Cheapest additive signals (identified, NOT implemented — audit-only): (1) `audit_logs` entry `legacy_driver_first_go_online` at `status.py:~655`; (2) labeled counter `spinr_drivers_go_online_total{is_legacy_import}` (metrics.inc supports labels, `utils/metrics.py:60`); (3) `spinr_payments_legacy_driver_payout_total` at the transfer-success path in `routes/drivers/payouts.py`.

## Phase 1 exit criterion — status

| Required | Status |
|---|---|
| Reconciled identity map with unmatched/ambiguous list | **Blocked on old-app export/DB access** (partial map documented above; 3 unmatched ride-driver links; 22 unmarked drivers; rider mapping structurally absent) |
| Tax-consistency verdict for the dual-run period | **Delivered: NOT consistent** (composition-level; quantified $105.17 + T4A understatement) |

## What was NOT verified

- Old-app collection contents (no export on this machine) — corporate/pass/subscription money, the 108 unresolved payment rows, full rider/driver population.
- Whether `users.phone` has a DB-level uniqueness constraint (0 duplicates observed live, constraint not confirmed in migrations).
- Old-app-side fraud/dedup logic; Stripe-side everything (unchanged from P0).
- Overlap between old-app coupon/wallet redeemers and the backfilled population.
