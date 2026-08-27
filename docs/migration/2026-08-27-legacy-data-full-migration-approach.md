# Legacy (previous-app) Data — Full Migration Approach

**Status:** APPROVED (2026-08-27) — decisions in §2 and §5 finalized by the product owner.
Phases 1-2 (driver profiles, vehicle history) and the two Section 6 display gaps are
approved to proceed. Phases 3, 5, 6 remain scoped-but-not-started as described below.
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

## 2. DECIDED (2026-08-27): cancelled/failed bookings ARE now in scope — reverses the earlier exclusion

**This directly contradicts something you told me earlier in this same conversation, and I
flagged it rather than resolving it silently. Decision: import them.** Documenting the
reversal explicitly, per your own request to review prior calls for anything sub-optimal.

- Of 1,210 total bookings in the export, only 271 (22%) are `completed` — the ones already
  imported. The other **941 (78%)** are `cancelled` (712) or `failed` (225), or blank-status (2).
- Every one of them still carries real pickup/dropoff GPS and a `created_at` timestamp —
  which PIPEDA and the Saskatchewan Transportation Act's retention rules require Spinr to
  keep for cancelled trips too, not just completed ones.
- **A cancelled/failed import path was already built** (2026-08-20, `booking_import_service.py`),
  reviewed, tested (23 new tests), and includes the matching admin-analytics exclusion fix
  (migration 349) so it doesn't skew cancellation-rate KPIs. **Correction to my earlier
  read of this** — I initially wrote this off as "built but never merged," going only off the
  change-log's own header (which recorded it as an unmerged worktree branch at the time it
  was written). Re-checked directly against the live file this session: the cancelled/failed
  branch of `build_plan()` **is already present in `backend/services/booking_import_service.py`
  on this branch today** — it must have landed via a later merge not reflected in that specific
  change-log's header. Practical effect: this is not a resurrection job, it's **already-shipped
  code that has simply never been run against the full booking set** (the original 224-row
  production batch, and the 08-22 delta batch, were both filtered to `completed` only at
  execution time, not because the code couldn't handle more).
- **Earlier this session, you told me: "we are not using the cancelled trips information
  for migration."**

**Decision (2026-08-27, your explicit instruction):** reverse that — cancelled/failed
bookings are now in scope. Reasoning: the regulatory retention argument (GPS + timestamp
must be kept for cancelled trips too) is real and independent of anything about this specific
migration effort, the code is already built, tested, and reviewed — not a new risk surface —
and it directly serves your stated goal of complete historical fidelity. This does **not**
change §3's already-excluded list (chats, reviews, coupons, etc. stay excluded) — only the
`bookings` collection's own status filter widens from `completed`-only to `completed` +
`cancelled` + `failed`.

**What this means operationally:** the existing runbook
(`docs/runbooks/legacy-booking-import-2026-08-22-batch.md`) needs its expected-count section
updated — instead of ~19 net-new completed rides, a full run against the current export will
also surface up to 941 never-before-imported cancelled/failed rows. See the runbook update
(§6 below) — **actually running this against production is still a human action via the
admin-dashboard import tool**, same as before; I don't have a path to execute it directly
from this session.

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

## 5. Two former legal/product blockers — both actioned 2026-08-27

These were flagged as **BLOCKER**-class in the 2026-08-19 audit. Both now have a decision
recorded, made by the product owner directly in this session (not by me — I'm documenting the
call and its reasoning, not asserting legal authority I don't have).

### 5a. No consent record for imported riders/drivers — DECIDED, and already live

**Decision:** enable `app_settings.legacy_consent_notice_enabled` — the one-time re-consent
mechanism built and dark-shipped 2026-08-19. **Checked directly against production via the
Supabase connector before acting: this flag is already `true`, set 2026-08-21 — three days
before this session started, and four before this decision was even discussed.** No write was
needed or made; I'm recording this as verified-live rather than claiming credit for flipping
it. Every imported rider/driver (and every organic pre-tracking user with no recorded consent)
has been seeing a one-time notice on login since 2026-08-21; accepting it stamps
`consent_version` permanently. Worth a support/analytics check (outside this session's reach)
for whether the mobile screens have actually rendered cleanly in the ~6 days it's been live —
nobody explicitly confirmed real-device behavior when it was turned on.
**What this decision does NOT resolve:** whether the *text* of Spinr's current Terms/Privacy
Policy is itself legally sufficient for what's being asked of a migrated user was a separate,
still-open question from the 2026-08-20 fact-finding pass (`docs/audit/2026-08-20-legacy-consent-legal-sufficiency-factsheet.md`)
— this decision turns on the *mechanism* for capturing a consent gesture, it doesn't
retroactively validate the legal content of what's being consented to. Worth a real legal
read of that fact sheet's 7 open questions at some point, separate from this action.

### 5b. Insurance-period reconstruction — DECIDED: keep the reconstruction, add GPS-based correction where the source data allows it

**What existed before (today's actual behavior):** every legacy completed ride's driver
insurance-period record — the audit trail SGI requires, proving whether a driver was "en
route to pickup" (Period 2) or "passenger aboard" (Period 3) at any given moment — is
automatically rebuilt from three snapshot timestamps already in the booking data: when the
driver *arrived*, when the trip *started*, and when it *completed*. Every one of these rebuilt
rows is clearly tagged `is_reconstructed=true`, and (since 2026-08-19) that tag is visible in
the actual tool used to answer a real SGI records request — so nobody looking at the data
would mistake it for a live, real-time recording.

**The risk with that, in plain terms:** an estimate from 3 timestamps is a coarser picture
than what was actually happening. Specifically, Period 2 ("en route to pickup") is deemed to
start only at the moment the driver *arrived* — not the moment they actually got the trip and
started driving toward the rider, which happened earlier. So the reconstructed record likely
*understates* how long the driver was under commercial coverage during that leg. If a
regulator ever formally challenged one specific ride's classification, "we estimated this
from three timestamps" is weaker evidence than "here is the vehicle's actual recorded
location and status throughout the trip."

**What's new, and why it changes the recommendation:** the old app separately recorded real
GPS-based phase data — literal `idle` → `going_to_pickup` → `on_ride` transitions with
timestamps — for 393 of the already-imported completed rides (matched 100% by ride ID). This
isn't an inference; it's what the driver's phone actually reported happening, moment to
moment, back when the trip occurred. It was sitting unused in the export until this session's
audit found it.

**The better approach (approved):** for those 393 rides, use the *real* recorded
`going_to_pickup` start time to correct Period 2's start boundary — replacing the coarser
"assume it started at arrival" estimate with what genuinely happened. Critically, this is
never done by editing the original reconstructed row (Spinr's insurance-period table is
append-only by design — even the original 2026-08-19 reconstruction never allows in-place
edits). Instead, it goes through `driver_insurance_period_corrections` (migration 355), a
table purpose-built for exactly this: a new row that references the original by ID, states
what changed and why, and never touches or deletes the original. The original,
`is_reconstructed=true` row stays visible forever as the first-pass estimate; the correction
sits alongside it as a documented improvement. For the remaining rides with no matching GPS
log, the existing 3-timestamp reconstruction stands as-is — the best information genuinely
available, already disclosed as an estimate, not silently presented as more precise than it
is.

**Sign-off:** this approach is accepted as Spinr's position for the legacy-ride insurance-audit
trail — a disclosed, best-effort reconstruction as the floor for every legacy completed ride,
strengthened with real GPS evidence wherever the old app happened to capture it. The
correction-import tool itself (reads the old app's location-log export, matches by ride ID,
writes correction rows) is scoped as Phase 3 in §4 above — not yet built. I'll build it next
if you want it prioritized now rather than later.

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

## 6a. A third gap, found while investigating §5a — the ToS/Privacy checkbox re-prompts every returning login (not migration-specific, but directly adjacent to it)

Raised by the user directly, then confirmed by reading the code: `login.tsx` (both apps) shows
a mandatory "I agree to Spinr's Terms of Service and Privacy Policy" checkbox that must be
checked before "Send Verification Code" is enabled — **every single time** a user lands on
that screen, whether they're a brand-new signup or a returning user whose session simply
expired (30-day refresh token) or who logged out and back in.

**Why this happens:** the backend (`routes/auth.py::verify_otp`) only actually *uses* the
`consent_accepted` flag when creating a brand-new account — for a returning user it's read
and silently ignored ("harmless to send on a returning-user login too," per the code comment
that added this checkbox on 2026-08-20). But the phone-entry screen can't yet know whether a
given phone number belongs to a new or returning user — that's only knowable after OTP
verification — so today's UI takes the simplest path and shows/requires the checkbox
unconditionally for everyone, every time.

**Impact:** a returning rider/driver whose session lapses is forced to re-tick a box that
does nothing for them (their consent was already recorded, correctly, the first time) —
pure friction, no legal benefit, exactly the UX cost flagged. Approved to fix (task tracked).
**Fix approach:** stop gating "Send Verification Code" on the checkbox. Instead, only surface
the consent requirement if the backend actually comes back with its existing
`errors.auth.consent_required` response (which already only fires for genuine new-account
creation) — at that point, and only then, show the checkbox/consent step before retrying.
A returning user never sees it again after their first successful login.

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

## 9. Decisions log (2026-08-27)

- §2 — **Cancelled/failed bookings**: reversed to in-scope. Runbook updated (§6 of the
  runbook doc). Actual execution against production is still a human action.
- §5a — **Consent notice**: approved to flip; verified via Supabase connector it was already
  `true` in production since 2026-08-21 — no write needed.
- §5b — **Insurance-period reconstruction**: sign-off recorded. Correction tool (GPS-based,
  393 rides) scoped, not yet built.
- §6a — **Login checkbox re-prompt**: approved to fix. In progress.
- Phases 1-2 (driver profiles, vehicle history): approved to build next.
- Phases 3 (partially — the correction tool itself), 4, 5, 6: remain scoped-but-deferred as
  originally written — no change to that call.
