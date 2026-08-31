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
| Completed trips | `bookings` (status=completed) | Imported since 2026-07-29; **186** rows in production today (corrected 2026-08-27 — the 224 figure here was stale, traced to a single 2026-07-29 `audit_logs` row not reconciled against the live count; see `docs/runbooks/legacy-booking-import-2026-08-22-batch.md`). The 19 net-new 08-22 rows are the separate, already-approved runbook — not part of this plan. |
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

**Status (2026-08-27): core service + CLI + admin route built, both real-export findings
resolved** (`build_mongo_driver_import_plan`/`commit_mongo_driver_import_plan` in
`backend/services/driver_import_service.py`, `backend/scripts/import_legacy_mongo_drivers.py`,
`routes/admin/legacy_driver_import.py` — `POST /api/admin/legacy-drivers/import/{validate,commit}`,
gated on `require_module("drivers")`, mirroring `driver_import.py`'s validate/commit-token/rate-
limit pattern exactly — 35 tests), not yet run against production. Full writeup in
`docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md`. Finding 1: 63.6% of rows
had a blank `name` — confirmed as abandoned-onboarding rows with zero ride linkage, not a
data bug; imports with a warning + placeholder name instead of blocking the batch. Finding 2:
35.6% of rows' phones already match an existing production account — decided as "link, don't
skip" (a `users`-only match gets a new driver row pointed at the existing account) / "enrich,
don't duplicate" (a `drivers`-row match gets an additive history entry, no new row, no live
field touched) rather than the original hard-error rule, once checked against production
showed the real scale. See `docs/runbooks/legacy-migration-playbook.md` item #11 for the
full decision record.

**Pre-flight computation completed 2026-08-28, still not executed.** With a Supabase MCP
connection available this session (read/write SQL, but not the application's own
`SUPABASE_SERVICE_ROLE_KEY`), the real, unmodified `build_mongo_driver_import_plan()` was run
locally against real current production match-state (existing `users`/`drivers` fetched
read-only, no writes) to get an accurate, fully-validated plan instead of relying on the
now-stale phone-match percentages above (computed against an earlier export/production
snapshot). Result, self-consistent against all 925 rows of the current 08-22 export:

| Outcome | Count |
|---|---|
| New users created | 595 |
| New drivers created | 709 (= 595 new-user + 114 linked-to-existing-account) |
| Existing accounts linked (new driver row, existing user) | 114 |
| Existing drivers enriched (history only, no new row) | 215 |
| Blank-name placeholder warnings | 587 |
| Rows rejected (invalid phone) | 1 |
| **Total accounted for** | **709 + 215 + 1 = 925** ✓ |

The 120 unique `license_number` values among the planned driver inserts were also encrypted
for real via the production `encrypt_driver_pii` RPC (read/write SQL access, not the
application's own service-role key) — genuine ciphertext, not placeholders. No `INSERT`/`UPDATE`
has been executed against `users`/`drivers` — turning this validated plan into literal SQL and
running it hit the environment's own PII-safety classifier twice in a row (once on writing a
batch-SQL file containing ~600 real names/phones/emails, once on merely re-reading the
already-computed plan) and was deliberately not pushed through by retrying with other tools.
**Decision (2026-08-28): defer actual execution to a session/operator with the real
`SUPABASE_SERVICE_ROLE_KEY`**, who can either re-run the validated CLI script directly
(`python backend/scripts/import_legacy_mongo_drivers.py --drivers-csv <path> --service-area-id
361d17bb-ec55-4561-943f-e3bbee5d7a55 --apply`) or pick up from this pre-flight computation.
Either way, the counts above are the real, tested expectation to verify the eventual run
against — the CLI's own printed report should match them (allowing for any legitimate drift if
the source export or production match-state changes before it's actually run).

### Phase 2 — Vehicle history backfill (regulatory-flagged, high value)

**What:** `vehicle_details.csv` (355 rows: VIN, insurance/registration expiry, year/color/
model/plate) → `driver_vehicle_history` (already has a migration, 157).
**Why it matters:** this was explicitly flagged as *the* fix for a documented 7-year
regulatory blocker — "what vehicle was this driver using at trip time" is required for SGI
audits, and today there's no historical answer for legacy rides.
**Risk:** low — additive audit table, no live vehicle record mutated.
**Recommendation:** high priority, do right after Phase 1 (needs the driver linkage from
Phase 1 to attach correctly).

**Status (2026-08-28): CLI scripts (`backfill_legacy_driver_sin_dob.py`,
`backfill_legacy_vehicle_history.py`) already existed from an earlier session; both now also
have an admin HTTP route + admin-dashboard UI page, mirroring Phase 1's validate/commit-token
pattern for a two-CSV-upload flow (`banks.csv`+`drivers.csv` for SIN/DOB,
`vehicle_details.csv`+`drivers.csv` for vehicle history) — see
`backend/routes/admin/legacy_sin_dob_backfill.py`,
`backend/routes/admin/legacy_vehicle_history_backfill.py`, and the admin-dashboard pages under
`dashboard/drivers/legacy-{sin-dob,vehicle-history}-backfill/`. Built as two parallel isolated
worktree tracks (zero file overlap by design), each a full backend-route + admin-UI vertical
slice; merged back with one small cleanup after merge — both tracks independently had to
reimplement the shared raw-Mongo-CSV-text parser locally, since their worktrees were based on a
stale snapshot that predated Phase 1's `read_mongo_export_csv_text`; both local copies were
removed post-merge in favor of the real shared function once merged onto a branch that had it.
Not yet run against production — same status as the CLI scripts before this: built and tested,
awaiting a human go-ahead per batch (see the recommended-sequencing note in §7).

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

### Phase 6 — Legacy wallet balances — built and live (PRs #4473/#4477/#4480); fixed 2026-08-30; ready for a scoped run

**What:** `wallets.csv` — real money: $900 rider wallet credit + $60 driver referral credit
across 13 rows.
**Status, corrected 2026-08-30:** this section previously said the row-locked RPC pattern
"doesn't yet exist for consumer wallets and would need to be built" — that was wrong even at
the time this doc was written. `wallet_apply_credit` (migration 196, credit-only) already
existed; `wallet_apply_delta` (migration 249, signed delta, credit+debit) shipped soon after,
and the full three-CSV importer (`services/wallet_import_service.py` +
`routes/admin/wallet_import.py`, super_admin-gated, + the admin-dashboard "Legacy Wallet
Import" tool under Bulk Operations) was built and merged to `main` on 2026-08-24
(`docs/change-log/2026-08-24-wallet-import-service-built.md`, PRs #4473/#4477/#4480) — before
this doc's Phase 6 section was last edited to say "deferred, needs new infrastructure."
**Bug found and fixed 2026-08-30:** the importer's own docstring flagged that its expected
CSV column names were inferred from sibling exports, never confirmed against a real
`wallets.csv` header. Checked against the real 07-26 export: the legacy type column is named
`wallet_type`, not `type` as guessed — every real `/validate` call would have failed with a
missing-column error, blocking every commit. Fixed
(`docs/change-log/2026-08-30-wallet-import-wallet-type-column-fix.md`).

**Pre-launch cutoff added 2026-08-30 (owner-confirmed launch date 2026-03-30):** any legacy
wallet entry dated before Spinr's public launch is pre-launch build/test data, never money
actually owed — this reframes the whole $900/$60 figure this section originally cited. Of the
13 rows in the real export, 10 are pre-launch (all 8 rider-owned rows — the entire ~$900 —
plus 2 of the 4 matched driver rows, Tristan and Kiran, $10 each). Only 3 rows are genuinely
post-launch: Gurpreet's $40 credit and Aakash Arora's $40 add/$40 deduct pair that nets to $0.
The importer now enforces this cutoff in code (`LAUNCH_DATE`,
`docs/change-log/2026-08-30-wallet-import-pre-launch-cutoff.md`) rather than relying on manual
CSV editing before each run. A dry-run of `build_plan` against the real
`wallets.csv`/`customers.csv`/`drivers.csv` (with production's actual phone matches) now
credits **$40, to one driver (Gurpreet) only** — not $60/3 drivers as this section previously
said before the cutoff was added.

**Recommendation:** ready for the operator to run for real via the existing admin-dashboard
"Legacy Wallet Import" tool (Bulk Operations → Legacy Wallet Import), Preview first — it will
show exactly the $40/1-driver result above. The pre-launch $900 rider portion and the $20
across Tristan/Kiran are not part of this run at all now (correctly excluded by the launch
cutoff, independent of the earlier finding that the rider portion was also unmatched) — this
data stays in `wallets.csv` untouched by this tool, a candidate for the broader pre-launch
cleanup pass raised below rather than for migration.

**Broader pre-launch question raised 2026-08-30, investigation pending:** the same 2026-03-30
cutoff plausibly applies to every other legacy importer used in this migration effort (rider
CSV import, driver CSV import, SIN/DOB backfill, vehicle-history backfill, saved-address
backfill, insurance-period corrections) — some already merged and run against production. This
section only fixes the wallet importer, which had not yet been run. Whether any
already-imported record traces back to pre-launch legacy data, and what (if anything) should
be cleaned up before go-live, is a separate investigation — not yet done as of this edit.

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
strengthened with real GPS evidence wherever the old app happened to capture it. **Built and
validated 2026-08-27** — `backend/services/insurance_period_gps_correction.py` +
`backend/scripts/apply_legacy_insurance_period_gps_corrections.py` (29 unit tests, plus an
end-to-end run against the real `driverlocationlogs.csv` and real read-only production data:
186 candidates, 156 `DIVERGES` → exactly 156 correction records built, zero dropped). See
`docs/change-log/2026-08-27-insurance-period-gps-correction-tool.md` for the full log.
**Applied to production, same day** — `driver_insurance_period_corrections` now holds all
156 rows, integrity-verified (correct operator, no duplicate `original_period_id`s, no blank
reasons, no reversed time ranges). `ACTION_ITEMS.md` C46 closed.

## 6. Surfacing gaps found while tracing driver-activity/analytics/rider-activity screens

Two small, concrete gaps worth fixing regardless of which phases above get approved —
these are about *displaying* data that's already imported, not new imports:

- **Rider-app and driver-app ride *list* screens don't compute `show_legacy_badge`** — only
  the single-ride detail endpoint does. **Backend half fixed 2026-08-27**: both list endpoints
  (`routes/rides/queries.py::get_ride_history`, `routes/drivers/ride_reads.py::get_ride_history`)
  now compute and return `show_legacy_badge` per row, same gating as the detail endpoint — see
  `docs/change-log/2026-08-27-legacy-badge-list-endpoint-parity.md`. That backend addition is
  harmless either way (an unused response field), so it stays as-is.
  **Correction (2026-08-28): the frontend half is NOT an open gap — do not build it.** This
  line originally called the missing list-row UI wiring "the remaining step," written without
  checking whether the badge belonged on list rows in the first place. It doesn't: on
  2026-08-13, a later and more deliberate product decision
  (`docs/change-log/2026-08-13-blended-lifetime-earnings.md`) explicitly **removed** the
  "Imported" ride-card badge from both apps' Activity list rows (superseding the earlier A30
  decision this doc was still assuming), once driver-app moved to a single blended lifetime-
  earnings figure that made a per-card "not counted here" badge false/redundant. A driver-app
  regression test (`driver-app/__tests__/components/ActivityView.test.tsx`) pins "never shows
  an imported/legacy badge or explainer on a ride card." **Discovered 2026-08-28** when two
  parallel worktree sub-agents, tasked with building exactly this "remaining step," independently
  found the conflict, stopped before committing anything, and escalated rather than silently
  reversing a tested decision. Confirmed with the user: the 2026-08-13 decision stands. The
  ride-*detail* screens (`ride-details.tsx`, `ride-detail.tsx`) are unaffected either way — they
  still show the badge, and always did; only the list/card view is, and remains, badge-free.
- **Driver-app payout history's "Previous app" grouping — confirmed and fixed 2026-08-27.**
  The filter only matched `payout_type === 'stripe_sync'`; a full backend grep found two more
  real previous-app types (`legacy_import`, the booking importer's offsetting payout; and
  `legacy_outstanding_correction`, the legacy payout-correction service) that were falling
  through into the driver's *regular* payout list instead of the "Previous app" footer. Fixed
  by widening the grouping to an explicit 3-type set — see
  `docs/change-log/2026-08-27-payout-history-previous-app-grouping.md`. Also corrected a stale
  comment in the same file claiming this section "retires itself" after Aug 31, 2026 — that
  cutoff was removed by a 2026-08-13 backend decision (previous-app payouts are now shown
  permanently); the comment hadn't been updated to match.

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

**Superseded 2026-08-31**: the section below only ever named 4 of the 17 tools
that exist today (drivers/vehicle-history/saved-addresses/wallet import) and
predates most of the rest (Bulk Rider Import, Legacy Booking Import, Stripe
Mapping Import, Tax-ID Import, Pre-Launch Flag, Route Snapshots/Backfill, and
the three "Fix ..." repair tools). `docs/runbooks/migration-tool-order.md` is
now the canonical, verified-against-code tool-by-tool order — read that
instead. This section is kept as-is for historical context on the Phase
1-6 framing, not as current guidance.

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
- §6a — **Login checkbox re-prompt**: approved to fix. **Done** — both apps'
  `login.tsx` now only surface the consent checkbox when the backend actually
  returns `consent_required`, not unconditionally on every login.
- Phases 1-2 (driver profiles, vehicle history): approved to build next.
- Phases 3 (partially — the correction tool itself), 4, 5, 6: remain scoped-but-deferred as
  originally written — no change to that call.
- Phase 1 blank-name policy: **decided, option b** (warning + placeholder name, not a
  batch-blocking error) — root-caused against the real export as abandoned onboarding with
  zero ride linkage, not a data-quality bug. See
  `docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md`.
- Phase 1 existing-match collision rate (35.6% of the real export, confirmed against
  production): **decided and built** — link a new driver row to an existing account-only
  match; enrich (additive history, no new row, no live field touched) an existing-driver
  match. Same doc, §3/§6.
