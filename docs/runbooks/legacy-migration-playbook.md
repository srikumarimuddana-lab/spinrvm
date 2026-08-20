# Legacy Data Migration Playbook — repeatable strategy for the Oct 30 final cutover

**Status:** draft, written 2026-08-19 off this session's audit work. Not yet exercised end-to-end —
the 2026-07-29 production cut predates this playbook and did not follow it formally, though in
retrospect it satisfied most of the same discipline informally. Intended audience: whoever runs the
Oct 30 final old-app export and decommission.

**Status as of 2026-08-19 (re-verified):** the 10-item checklist below was re-verified item-by-item
against the actual codebase after three same-day remediation PRs landed (#4265 MongoDB export
analysis + SIN/DOB backfill, #4270 legacy/re-consent notice mechanism, #4272 the full A41 3-track
remediation across backend/driver-app/admin-dashboard). Each item now carries an inline
**[RE-VERIFIED 2026-08-20]** annotation with a status (still accurate / partially addressed / fully
addressed / N/A) and a citation to the specific change-log/file. Net picture: items 1, 5, 6, and 7
moved from fully-open to partially or fully addressed; items 2, 4, 8, 9, and 10 are untouched by
today's work and remain exactly as open as when this checklist was written. **Item 3 — the
cancelled/failed-booking cross-cutting gap (78%, 941/1,210 old-app bookings) — is still completely
unaddressed**: `backend/services/booking_import_service.py` still only imports
`booking_status == "completed"` rows by explicit design (see its own header comment, unchanged
today), with no cancelled/failed import path built or scheduled. This re-verification pass is itself
audit-only — no code was changed to produce it, only this playbook's own annotations.

## Why this exists

Three real bugs traced back to the same root cause this session
(`docs/audit/2026-08-19-legacy-migration-data-quality-audit.md`): code written for organic rows
was never re-verified against the *shape* of an imported row. A legacy ride's fare fields don't
mean what the formula assumes; an admin aggregate that correctly excludes legacy rows in one place
was never re-applied to three siblings; a batch importer's own safety guarantee held only at
plan-time, not at write-time. None of this was a data-quality problem in the traditional sense (the
data was there, and mostly accurate) — it was a **missing verification step**: nobody swept the
codebase asking "what happens when a legacy-shaped row hits this code path?"

This playbook is that sweep, made repeatable, plus the extract → normalize → validate discipline the
2026-08-19 collection-inventory audit (`docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md`)
already demonstrated works when done thoroughly.

## The five stages

Each stage has a **gate** — a concrete, checkable condition that must hold before moving to the
next stage. A stage is not "roughly done," it's gated or it isn't.

### Stage 1 — Extract & inventory

**Goal:** know exactly what the export contains, collection by collection, before any mapping
design starts.

- Full export, all collections, unfiltered, taken under a write freeze (per the P3 dual-run-cutover
  runbook, `docs/audit/2026-08-15-dual-run-cutover/P3-operational-readiness.md`).
- Row counts, column population rates, and key-field distributions for every collection — not just
  the ones that look important. The 2026-08-19 inventory found real findings (raw SIN/banking in
  `banks.csv`, a testing-only `subscriptions` population, `driverlocationlogs`'s phase-timing data)
  precisely because it opened collections nobody had looked at before.
- ID-crosswalk verification: for every pair of collections that reference each other by ID, run the
  actual join and report the match rate — don't assume it works, prove it (`bookings.driver_id` ↔
  `drivers._id` was verified at 96/96 this session specifically because someone ran the join).

**Gate:** every collection in the export has a stated row count and a one-line classification
(`MIGRATED` / `NEW-DESIGN` / `REVIEW` / `EXCLUDE` / `EMPTY`), with a named owner for every `REVIEW`
row. No collection is silently skipped — an explicit exclude decision is recorded even for
collections judged irrelevant.

### Stage 2 — Gap analysis against the target schema

**Goal:** know, for each field you intend to import, what already exists on the Spinr side, whether
it's a genuine gap or a duplicate, and what happens to any code that reads the target column today.

- For every target column an importer will write: **grep every existing reader of that column**
  before writing to it. This is what caught the `add_tip`/`driver_earnings_with_tip` bug this
  session — the column existed, had readers, and none of them had been checked against what a
  legacy-shaped value would look like.
- For every money-adjacent field: does the target column's existing formula/aggregate assume a
  structural invariant (e.g. `total_fare = base_fare + distance_fare + time_fare + booking_fee +
  airport_fee`) that the legacy data can't satisfy? If yes, either the importer must synthesize the
  invariant faithfully, or every consumer of that invariant must explicitly exclude legacy rows —
  document which choice was made and why, per field.
- For every admin-dashboard/rider-app/driver-app screen that reads the target table: does it have a
  fallback for a null/blank value in every field the import might leave unpopulated? (Per the
  driver-app and admin-dashboard audit findings this session, several don't.)

**Gate:** a field-by-field mapping table exists (old field → new column → transform → known gaps),
and every "this field feeds a formula/aggregate that assumes X" note has an explicit resolution, not
a TODO.

### Stage 3 — Normalize & validate (dry run only)

**Goal:** produce the exact rows that would be written, without writing them, and prove they're
internally consistent.

- Every importer follows the established plan/commit split (`build_plan`/`commit_plan` in
  `booking_import_service.py`/`driver_import_service.py`) — a dry-run report is the default mode,
  never optional.
- **Never-clobber is enforced at write time, not just plan time.** This session's SIN/DOB race-
  condition finding is the concrete cautionary example: a plan-time snapshot can go stale during a
  multi-minute batch run. Use `.is_(col, "null")` guards on every write that must not overwrite an
  existing value (the `stripe_mapping_import_service.py` pattern), not just a pre-flight check.
- **Provenance is stamped on every imported row**, in the existing `legacy_import_metadata` JSONB
  convention, namespaced per import batch/purpose (this session's `LEGACY_BANK_SIN_DOB_SOURCE`
  pattern) so multiple imports touching the same row don't clobber each other's metadata.
  Provenance stamping is not optional even for fields judged low-risk — cheap now, expensive to
  reconstruct later (see the `duration_estimated`/`sin_collected_at` findings this session, both
  cases where a missing marker became a real gap only discovered after the fact).
- **PII minimization is a decision, not a default.** Every field containing SIN/DOB/banking/
  government-ID-class data gets an explicit include/exclude call from whoever holds that authority,
  with the reasoning recorded (this session's banks.csv decision — SIN+DOB yes via the existing
  encrypted column, raw banking numbers no, because nothing downstream reads them — is the model).
  "We have room to store it" is not suffient reasoning on its own.

**Gate:** dry-run report reviewed by a human; row counts, warnings, and skip reasons all make sense
against Stage 1's inventory; zero unexplained discrepancies between planned and expected row counts.

### Stage 4 — Cross-cutting review sweep (the part this session demonstrated)

**Goal:** catch the class of bug that isn't in the importer at all — it's in code written for
organic data that nobody re-checked against imported data's shape.

Run these five lenses **in parallel**, each scoped narrowly enough to avoid re-deriving prior work:

1. **Migration data-integrity** (`spinr-migration-reviewer`): blank/defaulted fields and where they
   surface, provenance visibility, idempotency of every import script.
2. **Money/fare risk** (`spinr-money-auditor`): does any money-computing function's structural
   assumption (fare composition, delta-vs-fresh math, minimum-fare-uplift semantics) break on a
   legacy-shaped row? Grep broadly for the anti-pattern class, not just the specific field being
   imported this round.
3. **Regulatory/PIPEDA** (`spinr-regulatory-compliance-checker`): consent basis, retention-window
   correctness (real event dates preserved, not import-time `now()`), accuracy-disclosure for
   fields nobody can independently verify.
4. **Every consumer surface, once per surface** (admin-dashboard, driver-app, rider-app — as
   separate agents/passes, not one combined pass): for every field the import touches, does the UI
   handle a null/blank/estimated value gracefully, and can a human viewing the data tell it's
   imported when that matters?
5. **Cross-check against the prior findings register** (`ACTION_ITEMS.md`'s A25-A41 chain,
   `docs/runbooks/full-app-audit.md`'s Prior-Findings Ledger) — every new pass should explicitly
   state what it re-verified as still-fixed versus what's genuinely new, so effort doesn't repeat.

**Gate:** every BLOCKER/HIGH finding either fixed with its own Change Impact Log (small, isolated,
high-confidence fixes — same day is fine, per this session's 3 examples) or explicitly escalated to
a named decision-owner with a due date (large/design-dependent findings — per this session's
consent-basis and `is_reconstructed`-visibility findings, both correctly left open rather than
rushed).

### Stage 5 — Apply, verify, decommission

**Goal:** the actual cutover, with a provable rollback path at every step.

- Apply only after Stage 4's gate is met — a clean dry-run (Stage 3) is necessary but not
  sufficient; Stage 4 exists because Stage 3 alone missed all three bugs this session found.
- Post-import reconciliation: row-count and dollar-figure diff between the export and what actually
  landed, same discipline as the rider-provenance backfill's 918/1,137 count
  (`docs/change-log/2026-08-17-rider-provenance-backfill-executed.md`).
- Decommission only after reconciliation passes AND every Stage 4 BLOCKER is closed or explicitly
  risk-accepted — per the regulatory checklist below, a hard data-loss deadline (cancelled/failed
  bookings, still unimported as of this writing) makes "decommission first, fix data gaps later"
  irreversible, not just risky.
  > **[RE-VERIFIED 2026-08-20]** "Still unimported as of this writing" remains true as of this
  > re-verification — see checklist item 3's annotation below for the direct code confirmation.

## The Oct 30-specific checklist (from this session's `spinr-regulatory-compliance-checker` pass, reproduced verbatim)

1. **Consent-basis decision, in writing, before any new import runs.** For every population being
   imported/backfilled (riders, drivers, and specifically any SIN/DOB/government-ID-class field):
   either (a) a documented legal opinion that the old app's consent covers this new-system use, or
   (b) a re-consent prompt gated on `consent_version IS NULL AND legacy_import_metadata <> '{}'`.
   Must happen before step 6, not after.
   > **[RE-VERIFIED 2026-08-20 — PARTIALLY ADDRESSED.]** Option (a) — a documented legal opinion —
   > still has not happened; the only thing resolved is a narrower, prerequisite question (the old
   > app's legal entity name, "Spinr Mobility Inc.," is confirmed correct/unchanged — see A42), not
   > the sufficiency-of-old-consent-for-this-use judgment itself, which stays explicitly a
   > business/counsel call per `docs/change-log/2026-08-19-legacy-consent-notice.md`. Option (b) —
   > the re-consent prompt — is now built end-to-end but **not live**: backend
   > `GET/POST /consent/status|accept` gated on `app_settings.legacy_consent_notice_enabled`
   > (default `False`) per `docs/change-log/2026-08-19-legacy-consent-notice.md`; mobile UI wired
   > into both apps' fresh-login (`otp.tsx`), cold-start (`index.tsx`), and profile-setup-completion
   > paths per `docs/change-log/2026-08-19-legacy-consent-notice-mobile.md` and
   > `-mobile-completion.md`. The gate condition is a generic `consent_version != CONSENT_VERSION`
   > check rather than the literal `consent_version IS NULL AND legacy_import_metadata <> '{}'`
   > predicate this item specifies, but it covers the same population plus more (any stale
   > consent, not just legacy-imported rows). Flag stays off — no user has actually seen the
   > notice yet, and no simulator/device visual verification was performed on either screen. Since
   > the SIN/DOB backfill (step 6) also has not been `--apply`'d yet, the "before step 6" ordering
   > constraint has not been violated, but the item's core ask — an actual decision that took
   > effect — is not yet true.
2. **Retention-window correctness proof, per data class**, run as a query against the actual Oct 30
   export before import: for each of the four regulatory retention rows (trip record 7yr, driver/
   vehicle linkage 7yr, GPS pickup/dropoff 3yr, insurance-period transitions 7yr), confirm the
   importer preserves the *legacy* event timestamp, not import-time `now()`.
   > **[RE-VERIFIED 2026-08-20 — STILL ACCURATE AS WRITTEN.]** Nothing in today's three PRs touches
   > this. This is inherently a query that can only be run against the *actual Oct 30 export* — it
   > has not happened, and none of today's remediation work substitutes for it. Still fully open.
3. **Cancelled/failed-booking import path must exist and run before decommission** — closing the
   78% gap (941/1,210 old-app bookings). At minimum: GPS pickup/dropoff trace + timestamps, no
   payout/earnings reconstruction attempted. This is the one hard data-loss deadline on this list.
   > **[RE-VERIFIED 2026-08-20 — BUILT, NOT YET RUN.]** This item was still accurate as of the
   > fifth-pass re-verification above, but is now built: PR #4278 (2026-08-20) added a
   > `cancelled`/`failed` branch to `booking_import_service.py` (GPS/timestamps/cancellation
   > attribution only, no fare/earnings/payout, matching this item's own "at minimum" spec exactly),
   > and PR #4281 (same day) closed a follow-on gap where 7 of those rows structurally completed a
   > real trip (imported as `$0`-fare `completed` rows instead, per
   > `docs/change-log/2026-08-20-anomalous-rows-zero-fare-completed-import.md`, since a plain
   > cancelled-status write would have violated the ride-state-machine's
   > never-cancelled-after-trip-start invariant). Both merged into `main`. **The hard deadline this
   > item exists to prevent is no longer live-blocking decommission** — the code path now exists,
   > is tested (109 tests across both), and was reviewed by `spinr-migration-reviewer`/
   > `spinr-money-auditor` (both SAFE TO MERGE). **What's still open:** no `--apply`/`commit` has
   > run against any environment — see `docs/runbooks/legacy-backfill-scripts-rollout.md`'s "Decision
   > recorded" section: the product owner has approved running it now (against the existing
   > 2026-07-26-vintage `bookings.csv`, not waiting for Oct 30) and will execute it directly, since no
   > session in this repo has live Supabase credentials. Until that `--apply` actually runs, the data
   > is still only *recoverable*, not yet *recovered* — this item should stay open on this checklist
   > until that execution is confirmed, but the code/design risk this item was tracking is closed.
4. **Vehicle-at-trip-time linkage backfill** using `vehicle_details.csv` into `driver_vehicle_history`
   (migration 157) — closes the P0 §0.4 7-year driver/vehicle-linkage gap.
   > **[RE-VERIFIED 2026-08-20 — STILL ACCURATE AS WRITTEN.]** No importer targeting
   > `driver_vehicle_history` from `vehicle_details.csv` was built in any of today's three PRs — none
   > of the change-logs cross-referenced for this pass mention it. Still fully open.
5. **Insurance-period reconstruction, redone with the better source, and finally surfaced**: (a)
   re-run migration 332's approach using `driverlocationlogs.csv`'s real phase-boundary timestamps
   instead of the `driver_arrived_at` fallback; (b) wire `is_reconstructed` into
   `backend/scripts/compliance_export.py`'s output and an admin-dashboard read-only column.
   > **[RE-VERIFIED 2026-08-20 — PARTIALLY ADDRESSED.]** Half of (b) is done: `is_reconstructed` is
   > now in the regulator-facing export's embedded select, `redact_row()` output, and CSV/JSON
   > `FIELDNAMES` — confirmed directly in `scripts/compliance_export.py` (repo-root, not
   > `backend/scripts/` as this item names — corrected during the fix per
   > `docs/change-log/2026-08-19-legacy-migration-transparency-backend.md`, finding #1). The other
   > half of (b) — an admin-dashboard read-only column — is **not** done: confirmed by grep, no
   > `is_reconstructed` reference exists anywhere in `admin-dashboard/src/`. (a) — re-running
   > migration 332's reconstruction using `driverlocationlogs.csv`'s real phase-boundary timestamps
   > — was not attempted in any of today's PRs; still fully open.
6. **SIN/DOB and any other PII-sensitive backfill — minimization + encryption sign-off before any
   `--apply` run**, keeping the already-recorded scope narrow (SIN+DOB via existing encrypted
   columns, not raw banking numbers with no live consumer).
   > **[RE-VERIFIED 2026-08-20 — FULLY ADDRESSED for the literal ask, though a related item (#1)
   > stays open.]** The minimization sign-off was made and recorded before any code was written
   > (business-owner decision, SIN+DOB via `encrypt_driver_pii`, raw banking numbers explicitly
   > excluded because nothing in the live payout path reads them — manual cashout is hardcoded
   > `_STANDARD_CASHOUT_DISABLED = True`) per
   > `docs/change-log/2026-08-19-legacy-sin-dob-import.md`. Scope stayed narrow (confirmed:
   > `account_number`/`transit_number`/`institute_number` were never touched). A same-day write-time
   > race-condition fix (`.is_(col, "null")` guards, mirroring `stripe_mapping_import_service.py`)
   > was applied before any `--apply` run, per that same change-log's amendment. **`--apply` has
   > still never been run against production** (confirmed — the CLI wrapper remains dry-run-only in
   > every verification note read for this pass), so the ordering this item requires (sign-off
   > before apply) has not been violated. Note this is a narrower ask than item #1's broader
   > consent-basis decision, which is still only partially addressed — see #1.
7. **Accuracy-disclosure pass**: for every field imported without independent verification (SIN,
   DOB, name, email), decide and document whether a provenance/verified flag should be surfaced
   downstream, even if the decision is explicitly "no, and here's why."
   > **[RE-VERIFIED 2026-08-20 — PARTIALLY ADDRESSED.]** SIN: done — a new
   > `driver_import_service.sin_source()` derived field (`"legacy_import" | "self_entry" | None`)
   > now surfaces in the admin driver live-stats read path and the T4A filer-handoff export, per
   > `docs/change-log/2026-08-19-legacy-migration-transparency-backend.md` finding #2. Name/profile
   > as a whole: partially covered — the "Imported" badge now on driver list/detail, rider detail,
   > and (after a same-day follow-up) the rider list table
   > (`docs/change-log/2026-08-19-legacy-migration-transparency-admin-dashboard.md`,
   > `docs/change-log/2026-08-19-legacy-migration-rider-list-badge.md`) discloses that an entire
   > profile — including its name — is import-sourced, though this is a whole-profile signal, not a
   > dedicated per-field name-provenance flag. DOB: **not** addressed — no equivalent `dob_source`
   > derived field or downstream surface exists; confirmed by grep, only `sin_source()` was built.
   > Email: **not** addressed — no dedicated provenance flag; only the same whole-profile "Imported"
   > badge applies. So: SIN is a clean yes, name/email get a coarser whole-profile signal rather than
   > a per-field decision, and DOB has no equivalent treatment at all — genuinely partial, not
   > "decide and document even if no" for every field as the item asks.
8. **Explicit include/exclude sign-off, recorded per collection**, for every `REVIEW`-tagged
   collection from the inventory doc — a silent drop at decommission time is not acceptable for
   data about to become permanently unrecoverable.
   > **[RE-VERIFIED 2026-08-20 — STILL ACCURATE AS WRITTEN.]** Today's PRs made per-field decisions
   > for two specific collections already flagged in the inventory (`banks.csv` → SIN+DOB narrow
   > backfill, item #6; `driverlocationlogs.csv` and `vehicle_details.csv` referenced as future
   > source material for items #4/#5(a) but not yet acted on) — that is progress on individual
   > `REVIEW` rows, not the systematic "sign-off recorded per collection, for every `REVIEW`-tagged
   > collection" sweep this item calls for. No evidence any of today's PRs revisited the full
   > inventory doc's `REVIEW` list as a checklist. Still open as a systematic pass.
9. **Never-import list re-confirmed against the Oct 30 export specifically** (`sessions.csv`,
   `admins.csv` from the 07-26 snapshot) — verify the new pull doesn't introduce an equivalent-
   sensitivity collection the earlier snapshot didn't have.
   > **[RE-VERIFIED 2026-08-20 — STILL ACCURATE AS WRITTEN / N/A UNTIL OCT 30.]** This item is
   > inherently gated on the Oct 30 export existing, which it doesn't yet — nothing in today's three
   > PRs could have addressed it, and none tried. Still fully open, unchanged.
10. **Final reconciliation, post-import, before old-app teardown is authorized** — row-count and
    dollar-figure diff, verified match, not just "we ran the scripts."
    > **[RE-VERIFIED 2026-08-20 — STILL ACCURATE AS WRITTEN / N/A UNTIL THE FINAL IMPORT RUNS.]**
    > This item necessarily follows the Oct 30 import itself, which has not happened. Today's PRs
    > added dry-run-only backfill tooling (SIN/DOB, `duration_estimated`) that itself was never
    > `--apply`'d, so there is nothing yet to reconcile. Still fully open, unchanged.

## What this playbook is not

It is not a replacement for judgment. Every gate above produces information for a human decision,
not an automatic pass/fail — the whole point of Stage 4's parallel-agent sweep is to surface
findings a single reviewer would miss, not to make the review itself mechanical. When a finding is
ambiguous or the fix is architecturally significant, escalate per CLAUDE.md's existing
"Escalate, don't silently ship" rule — this playbook doesn't change that, it just makes sure the
sweep that surfaces those decisions actually happens, systematically, before Oct 30 rather than one
incident at a time after.
