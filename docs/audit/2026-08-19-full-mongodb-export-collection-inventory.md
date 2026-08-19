# Full MongoDB Export — Collection-by-Collection Inventory & Migration Readiness

**Date:** 2026-08-19 · **Posture:** AUDIT-ONLY — no data imported, no code or migrations changed.
**Source:** `Mongo.zip` supplied directly by the user this session (55 files, 177 MB extracted,
internal file timestamps 2026-07-26 — this is the same-vintage export that fed the
2026-07-29 production cut, **not** a fresh Oct-30 pull). This is the file every prior
dual-run-cutover audit (`docs/audit/2026-08-15-dual-run-cutover/`) said it could not access.
Analyzed in full: every one of the ~34 collections referenced by the P2 mapping plan, plus
~20 more the old app's export contains that no prior audit had named.

## Plain-English summary (read this first)

1. **The "6 unopened / ~23 unclassified collections" gap from the 08-15 audit is now closed.** Of the
   collections P2 flagged unknown, `subscriptions`/`driversubscriptions`/`userpasses`/`passtypes`
   turn out to hold **4 rows total, 3 of them `isActive: false`** — this looks like feature-testing
   debris, not a live revenue line. `restaurants`, `vendors`, `fleets`, `companies` are **completely
   empty** (header row only) — the "other-tenant marketplace" risk the earlier audit couldn't rule
   out is resolved: there is no data there to be anyone's, Spinr's or otherwise. Same for
   `extraorders`, `extraorderinvoices`, `orders`, `taxes`, `cards`, `contactus`, `serviceareas`,
   `documentsdetails`, `driverpayouthistories`, `surchargehistories`, and — notably — `users` (this
   old app keeps customers and drivers in two separate collections; `users` was never used).
2. **The ID-crosswalk problem the P1 audit called "not constructible from current access" is now
   constructible.** `bookings.driver_id` matches `drivers._id` at **96/96 (100%)**;
   `bookings.customer_id` matches `customers._id` at **172/173 (99%)**. All three collections share
   one Mongo ObjectId namespace. This is a different — and previously invisible — driver population
   from the one already imported to production (that import used a separate numeric-ID Saskatoon CSV,
   not this Mongo export). The join key between the two is `phone` (100% populated in both).
3. **A real, if small, prepaid-money exposure was found and quantified**: `wallets.csv` (13 rows)
   nets to **$900 of rider wallet credit** and **$60 of driver referral-wallet credit** — genuine
   money the old app's own ledger says it owes, on top of the already-known $185–$228 driver-payout
   figure. `banks.csv` (157 rows) holds **every driver's SIN, date of birth, and full bank routing
   details in plaintext** — this must not be imported wholesale; only what Stripe Connect onboarding
   actually needs, and even that needs an encrypted-at-rest path, not a CSV-to-column copy.
4. **A genuine trip-record gap got worse, not better, on closer look**: of 1,210 total bookings in
   this export, only **271 (22%) are `completed`** — the rest (712 cancelled, 225 failed, 2 blank)
   have never been imported and have no path to import today. Every one of them still carries full
   pickup/drop GPS coordinates and a `created_at`, which the regulatory retention rules require
   Spinr to keep for cancelled trips too.
5. **A concrete fix for the insurance-period reconstruction gap was found**: `driverlocationlogs.csv`
   (7,948 rows) records real `idle` / `going_to_pickup` / `on_ride` phase segments with actual
   `start_time`/`end_time` timestamps, keyed by `ride_id` — which matches `bookings._id` 100% of the
   time (393 distinct rides have this data). Migration 332's Period-2/Period-3 backfill for the 186
   already-migrated rides used `driver_arrived_at` as a stand-in for the true Period 1→2 boundary
   because nothing better existed at the time — this file has the true boundary for a meaningful
   subset of those rides and could tighten that reconstruction.
6. **The old app is not solely Saskatchewan.** `servicelocations.csv` carries active service-area
   polygons for **Regina and Saskatoon** (matches Spinr's SK-first footprint) alongside several
   **India-region polygons and an "Aadhar Card" driver document type** — all marked `INACTIVE` except
   one Chandigarh-area polygon. Read together with a "Test Bike" vehicle type and coupon codes named
   `MOBILITYR001`/`MOBILITYR075`, this looks like template/dev cruft from the app's origin rather than
   real Spinr customer activity — but per the no-silent-scope-narrowing rule, that's a call for a
   human to confirm, not something this audit excludes on inference alone.
7. **`pages.csv` contains what reads as Spinr's actual current legal documents** — Driver/Rider
   Privacy Policy and Terms of Service, both stamped "Version 2.0, Effective Date: March 1, 2026,
   PIPEDA-Compliant," governing-law Saskatchewan. Worth a direct diff against whatever the live
   rider-app/driver-app currently serve as their ToS/Privacy screens — if this is the canonical text
   and the app is linking somewhere else (or nowhere), that's a compliance gap independent of the
   MongoDB migration itself.
8. **Two collections should never be imported, full stop, and are flagged here so nobody tries**:
   `sessions.csv` (2,074 rows of live-format JWTs, including admin-scope tokens) and `admins.csv`
   (old-app admin accounts with password hashes). Neither has a legitimate destination in the new
   system's data model.
9. **`backups.csv` incidentally answers one of the P3 runbook's "required inputs this repo cannot
   supply"**: the old app's DB backups live at `spinr.tor1.digitaloceanspaces.com` — DigitalOcean's
   Toronto region. That's a real, if partial, answer to "old-app hosting/provider" for the
   decommission runbook.

---

## Full collection inventory (55 files, all opened and classified)

Legend: **rows** = data rows (header excluded, from `wc -l - 1`). **Verdict**: `MIGRATED` (already
covered by shipped importer logic), `NEW-DESIGN` (real data, no import path exists), `REVIEW`
(small/ambiguous, needs a human decision), `EXCLUDE` (operational/security data with no destination),
`EMPTY` (header row only, zero data).

### Core entities — already partially migrated

| Collection | Rows | Verdict | Notes |
|---|---|---|---|
| `bookings` | 1,210 | MIGRATED (partial) | `booking_status`: 712 cancelled / 271 completed / 225 failed / 2 blank. Importer (`booking_import_service.py`) takes `booking_status=='completed'` **and** a non-null `complete_delivery_at` (278/1210 populated) — that combination is what narrows 271 down toward the ~224 that originally landed. **978 of 1,210 rows (81%) have no import path at all** — see finding #4 above. |
| `customers` | 1,121 | MIGRATED (partial) | Rider import already covers this population by phone-match (918/1,137 Supabase users backfilled per prior session). `customer_id` field (26% populated) is **not** a legacy numeric ID — it's a Stripe Customer ID (`cus_...`). `wallet_balance` column is **always blank** (0/1,121) — current balance must be derived from `wallets.csv`, it is not cached on the customer doc. |
| `drivers` | 877 | NEW-DESIGN | This is a **different driver population** from the one already in production (that import used a separate numeric-ID Saskatoon CSV; this collection uses Mongo ObjectIds and is the namespace `bookings.driver_id`/`rides.legacy_import_metadata->old_driver_id` actually references — see finding #2). Same `customer_id`-is-Stripe-ID pattern as `customers`. `documents` field is a JSON array of `{type_id, image filename, expire_date, is_expired}` per doc type — image bytes are not in this export, only filenames. |
| `payments` | 372 | MIGRATED (partial) | Matches the already-known PR #3946 numbers exactly: `pending_amount_status`: 214 `no_due` / 158 `due`. `payment_type`: 371 `card`, 1 `pass`. Nothing new here beyond confirming the prior audit's figures against the primary source. |
| `driverearnings` | 276 | NEW-DESIGN | `earning_type`: 272 `salary` (per-ride), 4 `refer` (referral bonus). This is the per-driver earnings ledger the `payments`/`bookings` import already derives payouts from indirectly — but this collection itself (with its own `booking_id`/`driver_id`/`amount` breakdown) has never been cross-checked against the imported `payouts` rows. Worth a reconciliation pass before the final import to confirm the two agree. |

### Money — real balances/PII found, no import path exists

| Collection | Rows | Verdict | Notes |
|---|---|---|---|
| `wallets` | 13 | NEW-DESIGN | Real money: nets to **$900 customer wallet credit + $60 driver referral-wallet credit** (all `from_bank`/`from_driver_refer`/`for_owner_refer` types, `add`/`deduct` status). Confirms P2's call that this needs the row-locked `corporate_wallet_apply_delta`-style RPC, not a plain insert — now with an actual dollar figure to reconcile against. |
| `banks` | 157 | REVIEW (PII-sensitive) | **100% of rows carry a plaintext SIN, date of birth, and full Canadian bank routing (`account_number`/`transit_number`/`institute_number`)** for a driver. This is exactly the PIPEDA-minimization case CLAUDE.md's data-minimization rule and the P2 doc's own flag describe: import only what Stripe Connect onboarding needs (likely nothing — Stripe Connect re-collects banking directly from the driver, it doesn't need Spinr to hold or re-key raw account numbers), and if any of it must move, it needs `utils/crypto.py`-grade encryption at rest, never a bulk column copy. **Do not build an importer for this collection without a specific decision on what it's used for.** |
| `coupons` | 11 | REVIEW | 2 currently `active` codes (`SPINR50`, `SPINR75`) with real per-customer `used_by` redemption arrays (multiple ObjectIds each). Matches P2's flag: importing these without a "redeemed in old app" marker would let the same customers re-redeem equivalent promos under Spinr's own promo engine. 9 more are `deactive` — likely safe to skip entirely. |
| `subscriptions` / `driversubscriptions` / `userpasses` / `passtypes` | 3 / 1 / 1 / 1 | REVIEW (downgraded) | Full contents read (all 6 rows) — see finding #1. Recommend downgrading this from P2's "could be prepaid money, blocker-class unknown" to "confirm with the business owner these are test rows, then explicitly exclude" — the data itself doesn't support a blocker-level read anymore, but the exclusion should still be an explicit, recorded decision, not a silent drop. |
| `refrals` | 4 | REVIEW | Turns out to be referral **campaign configuration** (`camp_code`, `cus_ref_amount`, `driver_ref_amount`, eligibility-window days), not per-user redemption events — much lower risk than "referrals" sounded in the P2 doc. One campaign is currently `is_active: true` (a driver-referral campaign, $20/referral, 90-day window). No individual redemption ledger exists in this collection — that's presumably inside `wallets`/`driverearnings` `refer` rows instead, already covered above. |

### Trip-adjacent history — real data, never scoped by any prior audit

| Collection | Rows | Verdict | Notes |
|---|---|---|---|
| `driverlocationlogs` | 7,948 | NEW-DESIGN (high value) | See finding #5. `phase` dist: 7,035 `idle`, 551 `on_ride`, 362 `going_to_pickup`. 913 rows carry a `ride_id`; **100% of those match a `bookings._id`** (393 distinct rides). This is the best available source for real Period-boundary timestamps and is worth using to refine migration 332's reconstruction before the Oct-30 final pass, not just for newly-imported rides. |
| `customer_addresses` | 301 | NEW-DESIGN | Saved rider addresses (`name`, `type`, lat/long) — directly analogous to Spinr's own "saved as a favorite" address feature (CLAUDE.md's data-minimization rule already scopes this correctly: only import if flagged as a saved favorite, which every row here already is by construction). No importer exists yet. |
| `vehicle_details` | 355 | NEW-DESIGN (regulatory-relevant) | **This is the fix for the P0 §0.4 "vehicle-at-trip-time" gap** flagged as a 7-year regulatory blocker: VIN (91% populated), `insurance_expiry_date` (72%), `registration_expiry_date` (73%), year/color/model/plate `number`. No importer writes to `driver_vehicle_history` (migration 157) today — this collection is exactly its source. |
| `declined_bookings` | 883 | EXCLUDE (recommended) | Driver decline/pass-on-offer history. Operational dispatch data, not a regulatory record; recommend explicit exclude rather than building an importer, but flagging instead of silently dropping since offer-timeout data could theoretically inform historical driver behavior scoring if that's ever wanted. |
| `booking_notifications` | 1,231 | EXCLUDE (recommended) | Broadcast fan-out log (`driver_ids` array notified per booking) — dispatch-engine internals, no rider/driver-facing meaning once the old app is gone. |
| `docsupdatehistories` | 357 | REVIEW | Driver document approval/rejection history tied to `vehicle_detail_id`. Not itself a regulatory record (Spinr's own `driver_insurance_periods`/document-expiry checks are the regulatory surface), but could matter if a driver's approval timeline is ever disputed. Low priority; explicit exclude is defensible if said out loud. |
| `connections` / `chats` | 162 / 338 | REVIEW | Rider↔driver in-app chat history, tied to `booking_id`. Never mentioned in any prior migration audit — a genuine gap in the P2 plan's coverage. Low volume; a straightforward `import` would be cheap if there's a product reason to preserve trip-chat history, otherwise an explicit "we're not carrying chat history forward" decision belongs in the final plan. |
| `complaints` | 9 | REVIEW | Support-ticket-shaped rows (`title`/`message`/`reply`) tied to a booking. Tiny; same treatment as chats — explicit include/exclude, not a silent drop. |
| `reviews` | 397 | REVIEW | `type`: 268 `customer` (rated the driver), 129 `driver` (rated the rider). Rating carryover is a product decision (does a driver's historical star rating transfer?), not an engineering default — flag for the business owner, don't assume either way. |

### Reference/config data — old app's own settings, not user data

| Collection | Rows | Verdict | Notes |
|---|---|---|---|
| `serviceareapricings` / `vehiclesprices` / `vehicle_types` / `surchargedates` | 11 / 6 / 6 / 8 (mostly template rows, no real surge data) | EXCLUDE | Old app's own fare-config tables. Spinr's `fare_configs`/`surge_engine.py` are independent systems with their own values — these are historical-reference-only, not a migration target. |
| `servicelocations` | 13 | EXCLUDE / REVIEW | See finding #6 — Regina/Saskatoon polygons are `ACTIVE` and match Spinr's real footprint (informational reference only, Spinr's own service-area config is authoritative); the India-region rows need the human confirmation the finding describes before anyone assumes they're safe to ignore. |
| `documenttypes` | 14 | EXCLUDE | Old app's driver-document taxonomy (includes "Social Insurance Number" and "Aadhar Card" types, both `INACTIVE`). Spinr's own document-type config is authoritative. |
| `languages` | 94 | EXCLUDE | i18n string table (English/French/Hindi/Spanish) — app-config, not user data. |
| `faqs` | 4 | REVIEW | Tiny; worth a one-time diff against the new app's FAQ content (which has had extensive dedicated work — see the multiple `faq-*` change-log entries from 2026-08-17/18) to confirm nothing here is missing from the new set. Not a migration-pipeline task, a five-minute manual check. |
| `pages` | 6 | REVIEW (compliance-relevant) | See finding #7 — worth a direct diff against what's actually live in the apps today. |
| `banners` | 2 | EXCLUDE | Empty title/description, image filenames only. No content to carry forward. |
| `appconfigurations` | 1 | EXCLUDE | Old app's own singleton config row. Contains a **live Stripe *publishable* key** (not secret) plus payment-method toggles — informational only. |
| `admins` | 4 | **EXCLUDE — do not import** | Old-app admin accounts with password hashes. No legitimate destination; the new app's admin auth is independent. |
| `sessions` | 2,074 | **EXCLUDE — do not import** | Live-format JWTs (including `scope: admin` tokens) in plaintext. Security-sensitive; flagged so nobody treats this as importable "user session history." |
| `backups` | 53 | EXCLUDE (informational) | DB-backup file inventory. Not user data, but see finding #9 — useful for the decommission runbook's "old-app hosting" question. |
| `activities` | 7,497 | EXCLUDE | App-level audit/action log (`RequestSent` 2,164, `Create` 1,523, `Cancel` 900, `Accept` 711, `Complete` 517, etc.). Operational log, not a regulatory record — Spinr's own `audit_logs` is the analog going forward, not a migration target. |
| `errorlogs` | 261,298 | EXCLUDE | Server error/stack-trace log (largest file in the export at 31 MB). Zero product-data value; confirms the old backend is a NestJS app (stack traces reference `spinrnestjs`). Pure operational noise — do not carry forward. |

### Confirmed empty (header row only, zero data rows)

`restaurants`, `vendors`, `fleets`, `companies`, `extraorders`, `extraorderinvoices`, `orders`,
`taxes`, `surchargehistories`, `driverpayouthistories`, `documentsdetails`, `cards`, `contactus`,
`serviceareas`, `users`. **15 collections, all confirmed empty** — this resolves every "other-tenant
marketplace" and "unclassified, could be prepaid money" question the 08-15 P2 audit raised about
this group. There is nothing here to migrate, and nothing here to worry about.

---

## What this changes about the P2 migration mapping plan

Re-scoring `docs/audit/2026-08-15-dual-run-cutover/P2-migration-completeness.md`'s table against
what this export actually contains:

| P2 item | P2's verdict | This session's finding |
|---|---|---|
| #2 Cancelled/failed bookings | "Partially fits, importer hard-filters completed-only" | **Confirmed and quantified**: 941/1,210 (78%) of all bookings have zero import path. Still needs its own design (P2's assessment stands — skip payout-offset logic, keep GPS+timestamps). |
| #4 Drivers | "Yes, 187/211 stamped" | That was a **different dataset** (numeric-ID Saskatoon CSV). This export's 877-row `drivers` collection is a third population that overlaps `bookings.driver_id` 100% and needs its own crosswalk row, not a re-run of the existing importer. |
| #7 Banks | "Unconfirmed — collection never opened" | **Opened. Contains raw SIN + full banking for 157 drivers.** Needs an explicit minimization decision before any import path is built (see banks entry above) — resolve this before the Oct-30 pass, not during it. |
| #9 Wallets | "No for balances — needs a locked RPC" | **Confirmed with real numbers**: $900 + $60. Small enough to be tractable in a single reviewed migration once the RPC pattern is built. |
| #10–11 subscriptions/driversubscriptions/userpasses | "Unclassified — open the collections first" | **Opened — 6 rows total, mostly `isActive: false`.** Downgrade from "could be a blocker" to "confirm test data, then exclude," pending a one-line sign-off from the business owner. |
| #12 referrals | "No as plain backfill — could re-trigger payouts" | **Opened — this is campaign config, not redemption events.** Lower risk than assumed; the real redemption ledger is in `wallets`/`driverearnings`, both now quantified above. |
| #13 extraorders | "Likely none" | **Confirmed: empty.** |
| #14 restaurants/vendors/fleets | "Likely none (other-tenant)" | **Confirmed: empty**, all four including `companies`. |
| Crosswalk requirement | "Old Mongo ObjectId ↔ old numeric driver ID ↔ Spinr UUID... IDs only, no PII" | **The Mongo-ObjectId side of the join is now fully buildable** from `drivers.csv`/`customers.csv`/`bookings.csv` in this export. The numeric-ID side still needs the original Saskatoon CSV (not in this zip) re-joined by phone. |

**New findings the P2 plan didn't anticipate at all** (not in its collection list): `driverlocationlogs`
(insurance-period timing gold mine), `customer_addresses` (saved-favorites, directly matches CLAUDE.md's
own PIPEDA rule), `vehicle_details` (closes the P0 §0.4 vehicle-linkage gap), `connections`/`chats`/
`complaints`/`reviews` (trip-adjacent history nobody had scoped), `pages` (possible live legal-doc
source), `sessions`/`admins` (must-never-import security flags).

## What is still NOT resolved by this file

- **This is not a fresh export.** Same 2026-07-26 vintage as what's already in production. It does
  **not** answer "what does the old app owe today" (P0 §0.1), "has anything changed since the cut"
  (P0 §0.4 post-cut-rides gap), or give a current view of `payments.pending_amount_status='due'`
  rows — those all still need the Oct-30 pull the user described.
- **No Stripe-side data** — the `banks`/`payments`/`customer_id` (Stripe Customer ID) fields found
  here still can't be cross-checked against live Stripe without the access P0 §0.3 already flagged
  as deferred.
- **The numeric-ID Saskatoon driver CSV is not in this zip** — the three-way crosswalk (ObjectId ↔
  numeric ID ↔ Spinr UUID) needs that file re-supplied to close the loop; this export only resolves
  the ObjectId ↔ Spinr-ride-metadata half.
- **No image/document assets** — `drivers.documents`, `banks.file`/`file_path`,
  `vehicle_details.vehicle_insurance_image`/`vehicle_registration_image`, and `complaints.image` are
  all filenames/paths only; the underlying files were not part of this export.
- Nothing here was cross-checked against a **live** Supabase query this session (no DB access from
  this pass) — all "already migrated / matches production" statements above rely on the row counts
  and findings already recorded in the 08-11 through 08-18 audit docs, not a fresh live re-verification.

## Recommended next steps (not executed — awaiting direction)

1. Get an explicit owner sign-off on the `banks.csv` minimization decision before any importer touches
   it — this is the single highest-sensitivity item in the whole export.
2. Have the business owner confirm the `subscriptions`/`driversubscriptions`/`userpasses`/`refrals`
   rows are test data (5-minute check, closes 4 of P2's open rows).
3. Diff `pages.csv`'s Rider/Driver ToS + Privacy Policy text against what the live apps currently
   serve — independent of the migration, this is either confirmation everything's in sync or a
   compliance gap worth its own ticket.
4. When the Oct-30 export lands: re-run this same collection-by-collection pass against it (row
   counts, new/changed rows since this 07-26 snapshot) rather than assuming this analysis still holds
   — specifically re-check `payments.pending_amount_status`, `wallets`, and total `bookings` count for
   drift.
5. If a cancelled/failed-booking import path is greenlit, `driverlocationlogs`'s phase data is
   available now to backfill Period timing more precisely than migration 332 could for the original
   186 rows — worth doing as one combined pass rather than two.
