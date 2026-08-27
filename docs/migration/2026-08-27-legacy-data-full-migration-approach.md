# Legacy (previous-app) Data — Full Migration Approach

**Status:** DRAFT — awaiting user approval before any execution step below is run.
**Author:** Claude Code (interactive session), 2026-08-27.
**Related:** `ACTION_ITEMS.md` A41 (prior audit), C43 (RLS finding, deferred until this
concludes), `docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md` (the
authoritative per-collection sign-off this plan builds on — not repeated here, only
summarized), `docs/runbooks/legacy-booking-import-2026-08-22-batch.md` (the narrower,
already-approved runbook for just the 19 net-new completed rides).

## 0. What "done" means for this request

You asked for: every old-app data point that still has legitimate value carried into
Spinr's current tables, normalized, and visible wherever it should be — driver activity,
rider activity, admin analytics/portal screens. Not "import everything" — several
collections were already correctly ruled out (Section 3). This doc is the roadmap for
what's left, in the order it's safe to do it, with a straight risk call on each piece.

## 1. What's already done (do not redo)

| Data | Old collection | Current status |
|---|---|---|
| Completed trips | `bookings` (status=completed) | Imported since 2026-07-29; 224 rows in production today. The 19 net-new 08-22 rows are the separate, already-approved runbook — not part of this plan. |
| Rider profiles | `customers` | Phone-matched, 918/1,137 linked to real Spinr accounts. |
| Driver SIN/DOB | `banks` | Imported into existing **encrypted** columns only. Raw banking numbers (account/transit/institution) deliberately never touched — Stripe Connect re-collects banking directly from the driver; there is no plan to change this, and it shouldn't be revisited. |
| "Imported" transparency | admin-dashboard driver/rider list+detail rows, driver-app Documents screen, rider/driver-app ride-detail screens | All shipped (2026-08-19 and 2026-08-25 sessions). Ride-detail badge is dark-shipped, off by default (`legacy_ride_badge_enabled`). |
| Driver insurance-period reconstruction | derived from `arrived_at`/`started_at`/`completed_at` on imported completed rides | Runs automatically on every completed-path import, rows marked `is_reconstructed=true`, now visible to the SGI compliance export. |

## 2. The conflict I need you to resolve first — cancelled/failed bookings

**This is the single biggest scope question, and it directly contradicts something you told
me earlier in this same conversation.**

- Of 1,210 total bookings in the export, only 271 (22%) are `completed` — the ones already
  imported. The other **941 (78%)** are `cancelled` (712) or `failed` (225), or blank-status (2).
- Every one of them still carries real pickup/dropoff GPS and a `created_at` timestamp —
  which PIPEDA and the Saskatchewan Transportation Act's retention rules require Spinr to
  keep for cancelled trips too, not just completed ones.
- **A cancelled/failed import path was already built** (2026-08-20, `booking_import_service.py`),
  reviewed, tested (23 new tests), and includes the matching admin-analytics exclusion fix
  (migration 349) so it doesn't skew cancellation-rate KPIs. It was built but **never
  committed to any branch/PR and never run against production** — it exists only as
  historical work described in a change-log.
- **Earlier this session, you told me: "we are not using the cancelled trips information
  for migration."**

I'm not resolving this silently either way. Options:
- **(a) Honor your instruction** — cancelled/failed bookings stay excluded, full stop. If so,
  I'd recommend documenting *why* explicitly (business call, not a technical limitation) so
  a future session doesn't re-discover this gap and assume it's still open work.
- **(b) Revisit it** — the regulatory retention argument for GPS+timestamp-on-cancelled-trips
  is real, and the code to do it already exists, reviewed and tested. If you want this after
  all, it's a low-effort resurrection (find the branch, re-verify against the current export,
  run it), not new engineering.

**I need your explicit answer here before I build anything else that assumes one or the
other** — driver-activity and rider-activity completeness numbers below change depending on
this.

## 3. Already correctly excluded — no action, listed so nobody re-litigates it

These were reviewed collection-by-collection with the product owner (2026-08-20 sign-off)
and excluded for stated reasons. Not part of this plan:

`sessions` (live JWTs incl. admin tokens — security risk, no legitimate destination),
`admins` (old-app password hashes), `errorlogs` (261k rows, pure operational noise),
`activities` (Mongo audit log of booking-lifecycle events — **not** driver engagement data,
despite the similar name to driver-app's "Activity" screen; analog is Spinr's own
`audit_logs`, not a migration target), `chats`/`connections` (no chat-history feature in
Spinr), `coupons` (no redemption-tracking marker, re-redemption risk), `subscriptions`/
`driversubscriptions`/`userpasses`/`passtypes` (feature-testing debris, not live revenue),
`refrals` (campaign config only, Spinr has its own referral system), `docsupdatehistories`,
`complaints`, `reviews` (ratings do not carry over — fresh start by design),
`servicelocations`, `documenttypes`, `languages`, `pages`, `banners`, `appconfigurations`,
`backups`, `declined_bookings`, `booking_notifications`, `faqs` (not verified vs. live, not
blocking).

## 4. Real gaps worth closing — phased, in priority order

### Phase 1 — Driver profiles for the un-imported driver population (foundational)

**What:** `drivers.csv` (877 rows) is a *different* driver population from the numeric-ID
Saskatoon set already in production — matched 100% against `bookings.driver_id`, but **no
importer exists** to turn them into Spinr `drivers`/`users` rows.
**Why it matters for your ask:** every downstream "driver activity history" item (earnings,
trip counts, insurance periods) is only as complete as the driver being a real, linked Spinr
account. Rides from unmatched drivers currently import with a NULL driver link — history
exists on the ride, but nothing shows up under a driver's own activity/earnings screen.
**Risk:** medium. New importer, phone-matching logic (reuse the pattern already proven in
`booking_import_service._match_rider_driver`), additive rows only (no existing driver
mutated). Docs-only (no image/document files exist in the export — filenames only), so no
new document-verification-state risk.
**Recommendation:** build this before anything else in this phase — it's the dependency
every other driver-activity item below needs.

### Phase 2 — Vehicle history backfill (regulatory-flagged, high value)

**What:** `vehicle_details.csv` (355 rows: VIN, insurance/registration expiry, year/color/
model/plate) → `driver_vehicle_history` (already has a migration, 157).
**Why it matters:** this was explicitly flagged as *the* fix for a documented 7-year
regulatory blocker — "what vehicle was this driver using at trip time" is required for SGI
audits, and today there's no historical answer for legacy rides.
**Risk:** low — additive audit table, no live vehicle record mutated.
**Recommendation:** high priority, do right after Phase 1 (needs the driver linkage from
Phase 1 to attach correctly).

### Phase 3 — `driverlocationlogs` → tighten insurance-period accuracy (optional enhancement)

**What:** 7,948 real GPS-phase segments (idle/going_to_pickup/on_ride) keyed by ride, 100%
matched to 393 already-imported completed rides. Could replace the *estimated* period
boundaries (currently derived from `arrived_at`) with the real recorded phase transitions.
**Caution:** this is the file that had CSV corruption in the fresh 08-22 export (way_points
field) — use the clean 07-26 file for this phase, not 08-22, or re-verify the fix once a
clean fresh export exists.
**Risk:** medium — touches `driver_insurance_periods`, a regulatory audit table with
append-only/no-mutate rules. Would need to go in as *new, additionally reconstructed* rows
superseding the estimated ones (via `driver_insurance_period_corrections`, migration 355 —
built for exactly this "correct a reconstructed row" case), never an in-place edit.
**Recommendation:** lower priority than Phase 1/2 — it refines existing data rather than
closing a hard gap. Optional.

### Phase 4 — Rider saved addresses (nice-to-have)

**What:** `customer_addresses.csv` (301 rows) → an equivalent of Spinr's "saved favorite
address" feature. No importer exists.
**Risk:** low, but no current Spinr table/UI concept maps 1:1 (favorites are usually
self-service, not admin-imported) — needs a small design decision on where these land, not
just an import script.
**Recommendation:** lowest priority in this plan — cosmetic completeness, not a gap anyone
is likely to notice or ask about.

### Phase 5 — Payments reconciliation ("due" balances) — deferred, needs live data

**What:** 158 of 372 `payments.csv` rows show `pending_amount_status: due` — money the old
app considered outstanding. Cannot be safely imported without a live Stripe cross-check
(is it actually still uncollected, or already resolved outside this export's snapshot).
**Risk:** high if done wrong — this is the one place a bad import could create phantom
charges or double-collect from a real customer.
**Recommendation:** explicitly deferred. Needs live Stripe access this session doesn't have,
and a fresh (not 07-26/08-22 vintage) export per the original audit's own caveat.

### Phase 6 — Legacy wallet balances — deferred, needs new infrastructure

**What:** `wallets.csv` — real money: $900 rider wallet credit + $60 driver referral credit
across 13 rows.
**Risk:** high — this is real money, and a plain `INSERT` into `wallets`/`wallet_transactions`
is exactly the kind of race-unsafe write CLAUDE.md's money-path rules exist to prevent. It
needs the same row-locked-RPC pattern `corporate_wallet_apply_delta` already established for
corporate wallets — that pattern doesn't yet exist for consumer wallets and would need to be
built (not a big build, but it's new code, not a data script).
**Recommendation:** small in row-count, but do not rush it. Build the RPC, dry-run it,
reconcile the $960 by hand before any write.

## 5. Two items that are legal/product decisions, not engineering — must clear before Phase 1-6 execute for real

These were already flagged as **BLOCKER**-class in the 2026-08-19 audit and are still open:

1. **No consent record exists for any imported rider or driver.** A stored old-app policy
   document is not evidence anyone accepted it. This is sharper now that SIN/DOB (the most
   sensitive PIPEDA field) has already been backfilled for this population. This is a legal
   call — not something I can resolve by writing code.
2. **Insurance-period reconstruction's legal sufficiency.** Reconstructed `driver_insurance_periods`
   rows (marked `is_reconstructed=true`) are an engineering best-effort from incomplete
   source data, not source-of-truth SGI records. Whether that's acceptable for a real audit
   response is a legal/regulatory call, not an engineering one.

I'm listing these here rather than treating them as solved — Phases 1-3 above *add more* rows
of exactly this reconstructed/un-consented kind, so resolving these two questions gets more
urgent, not less, the further this plan proceeds.

## 6. Surfacing gaps found while tracing driver-activity/analytics/rider-activity screens

Two small, concrete gaps worth fixing regardless of which phases above get approved —
these are about *displaying* data that's already imported, not new imports:

- **Rider-app and driver-app ride *list* screens don't compute `show_legacy_badge`** — only
  the single-ride detail endpoint does. So even with the flag on, a rider/driver scrolling
  their trip history sees no visual distinction until they tap into a specific ride. Low-risk,
  small fix (same pattern as the ride-detail flag) if you want list-level parity.
- **Driver-app payout history's "Previous app" grouping filters on `payout_type === 'stripe_sync'`**,
  but the legacy-import offset payouts this session's booking importer writes use
  `payout_type === 'legacy_import'`. This looks like a real mismatch worth verifying — if
  confirmed, the legacy offset payouts may be showing up in the driver's *regular* payout
  list instead of the intended "Previous app" footer section. Flagged, not yet confirmed as
  a live bug — needs a direct read of `payout-history.tsx` to confirm before fixing.

## 7. Recommended sequencing

1. **You answer Section 2** (cancelled/failed bookings — honor the earlier instruction, or
   revisit it).
2. Phase 1 (driver profiles) → Phase 2 (vehicle history) — foundational, regulatory-flagged,
   low/low-medium risk, do these first.
3. Fix the two display gaps in Section 6 (small, isolated, no data risk).
4. Phase 3 (GPS-refined insurance periods) — optional, do if time allows.
5. Phase 4 (saved addresses) — lowest priority, do last or skip.
6. Phases 5 and 6 (payments-due reconciliation, wallet balances) — explicitly gated on
   things this session doesn't have (live Stripe access, a new RPC) — not started without a
   separate go-ahead once those prerequisites exist.
7. The two legal blockers in Section 5 run in parallel with all of the above — they don't
   block Phase 1/2 from being *built*, but they should block treating this migration as
   "compliance-complete" until resolved.

## 8. Risk/impact summary

| Phase | Risk | Reversible? | Blocks release/go-live if skipped? |
|---|---|---|---|
| 1. Driver profiles | Medium | Yes — additive rows, delete by `legacy_import_metadata` tag | No — degrades activity completeness only |
| 2. Vehicle history | Low | Yes — additive audit table | Partially — regulatory audit gap if an SGI request comes in for a legacy ride |
| 3. GPS period refinement | Medium (touches regulatory audit table) | Yes — via corrections table, never in-place edit | No — current estimated periods already work |
| 4. Saved addresses | Low | Yes | No |
| 5. Payments-due reconciliation | High if rushed | Partially — real money, needs care | No — deferred by design |
| 6. Wallet balances | High (money) | Yes if RPC done right | No — deferred by design |
| Cancelled/failed bookings (§2) | Low technically, but contradicts your own earlier instruction | Yes | Depends entirely on your answer |
| Legal blockers (§5 of this doc, i.e. consent + insurance-period sufficiency) | N/A — not a code risk | N/A | **Yes, for a compliance-complete claim** — not for basic functionality |

## 9. What I'm asking you to approve

- Confirm the answer to Section 2 (cancelled/failed bookings).
- Approve Phases 1 and 2 to start (the two I'd actually build first).
- Tell me whether to also fix the two Section 6 display gaps now (cheap, isolated) or fold
  them into a later batch.
- Confirm Phases 3-6 stay deferred/optional as scoped, or reprioritize.
- Acknowledge the two legal blockers are tracked (they already were, in the 2026-08-19 audit)
  and are not something I can resolve — flagging again here so this doc doesn't imply the
  migration is "done" once Phases 1-4 ship.
