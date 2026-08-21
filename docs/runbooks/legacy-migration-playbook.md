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
   >
   > **[2026-08-20, same day — fact-finding done for option (a), still not decided.]** A read-only
   > investigation compared the old app's `pages.csv` legal text against Spinr's current, unreviewed
   > `docs/legal/*.md` drafts, checked whether old-app users ever actually accepted anything (no
   > evidence found — no consent/acceptance field exists anywhere in the exported schema), and listed
   > concrete material differences (undisclosed subprocessors Gemini/LogRocket, GPS-retention
   > mismatch, surge-cap mismatch, no dashcam mention gap in current draft, etc.). See
   > `docs/audit/2026-08-20-legacy-consent-legal-sufficiency-factsheet.md` for the full fact sheet and
   > 7 open questions for counsel. **This is fact-finding only — it is explicitly not the legal
   > opinion itself and does not decide sufficiency.** The core ask (an actual decision, in writing)
   > remains open; this just makes that decision easier to make well.
   >
   > **[RE-VERIFIED 2026-08-20, LATER SAME DAY — DECISION MADE; ROLLOUT NOW THREE SEPARATE PIECES,
   > ONE SHIPPED HERE.]** The product owner made the call directly in-session, in writing here: skip
   > waiting on the option-(a) legal-sufficiency opinion and re-run consent under option (b) for
   > **both** existing and new users. That decision splits into three independent pieces, tracked
   > separately (see A41 in `ACTION_ITEMS.md` for the same note in that log):
   >   1. **This PR — `CONSENT_VERSION` bump.** `backend/routes/auth.py`:
   >      `consumer-tos-2026-01-draft` -> `consumer-tos-2026-08-v1`, tied to the real
   >      `terms-of-service.md`/`privacy-policy.md` publication event (`legal_documents` version 1,
   >      2026-08-17). Makes new signups stamp the new version immediately and makes every existing
   >      user's stored version genuinely stale — but by itself changes nothing a user can see.
   >   2. **The flag flip** (`app_settings.legacy_consent_notice_enabled` -> `true`) — explicitly
   >      **not** done by this PR; a separate actor flips it in the live DB after this merges and
   >      deploys. Until that happens, `GET /consent/status` keeps reporting `needs_notice: false`
   >      unconditionally (see `routes/legacy_consent.py`), so existing users see no prompt at all
   >      regardless of the version bump above.
   >   3. **New-signup consent checkbox** on the mobile signup screens — a separate, parallel session
   >      is reported to be building this concurrently on this same branch. Not confirmed from here:
   >      as of this change's base commit, `rider-app/app/login.tsx` has no consent-checkbox markup
   >      yet. Whether it has landed by the time this is read depends on push order on the shared
   >      branch — check the branch directly, don't trust this snapshot.
   > Net effect right now: nothing user-visible has shipped from any of the three pieces yet. Full
   > write-up: `docs/change-log/2026-08-20-consent-version-bump-re-consent-rollout.md`.
   >
   > **[RE-VERIFIED 2026-08-20, LATER SAME DAY — PIECE 3 (NEW-SIGNUP CHECKBOX) NOW BUILT.]** Piece 3
   > above is done: both apps' `login.tsx` now show a real, unchecked-by-default checkbox (accessible
   > label, `accessibilityRole="checkbox"`, icon — not color alone — signals checked state) gating the
   > "Send Verification Code" button's disabled state, with tappable links to the actual in-app
   > `/legal?type=tos` / `/legal?type=privacy` screens (the same destination `legacy-consent-notice.tsx`'s
   > "View Policy" link already uses). The checked state is carried as a route param into `otp.tsx`,
   > whose `POST /auth/verify-otp` call now sends `consent_accepted`; `backend/routes/auth.py`'s
   > new-user-creation branch rejects the signup (400, no row created, no `consent_version` stamped) if
   > that isn't `true` — the auto-stamp this item originally flagged as evidence-less is now gated on a
   > real logged gesture. Existing/returning-user logins are unaffected (that branch never reads the
   > field). Full write-up: `docs/change-log/2026-08-20-explicit-signup-consent-checkbox.md`. Net effect
   > now: 2 of 3 pieces are code-complete (1 and 3); piece 2 (the flag flip) is still explicitly pending
   > a separate actor, so existing users still see no re-consent prompt yet.
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
   > **[RE-VERIFIED 2026-08-20, SIXTH PASS — BUILT, NOT YET RUN.]** Still accurate at the fifth pass,
   > now built: `backend/scripts/backfill_legacy_vehicle_history.py` +
   > `driver_import_service.plan_legacy_vehicle_history_backfill`/`apply_legacy_vehicle_history_backfill`
   > (17 new tests, real-export crosswalk verified: 308/355 `vehicle_details.csv` rows resolve a Spinr
   > driver). Writes only to `driver_vehicle_history` (never `drivers`' own current vehicle columns);
   > a driver with more than one legacy vehicle row gets a real before/after change chain, sorted by
   > the legacy row's own timestamp — not import time, matching this playbook's own Stage 3 provenance
   > principle, with a deterministic tiebreak for identical timestamps. `spinr-migration-reviewer`
   > also caught and this fix closed same day: the idempotency dedup originally compared raw
   > `created_at` strings, which never matches Postgres's trimmed-fraction serialization on a re-run
   > (would have broken the "safe to re-run" guarantee on nearly every row). See
   > `docs/change-log/2026-08-20-legacy-vehicle-history-backfill.md`.
   >
   > **Building this also surfaced and fixed a real bug in the already-merged SIN/DOB backfill
   > (item #6 below)**: its CSV reader silently mangled the raw Mongo export's `_id` column, which
   > would have made the already-approved SIN/DOB `--apply` run resolve 0/157 rows while reporting a
   > clean "0 errors" — a silent no-op, not a visible failure. Fixed same day, verified against the
   > real export (157/157 now resolve). See
   > `docs/change-log/2026-08-20-mongo-export-header-normalization-bug.md`.
   >
   > **[RE-VERIFIED 2026-08-20, SAME DAY — ROLLOUT TIMING NOW DECIDED TOO.]** Put to the product owner
   > directly via `AskUserQuestion` (the same session, right after this capability merged): run now,
   > same as the other three. See `docs/runbooks/legacy-backfill-scripts-rollout.md`'s "Decision
   > recorded — fourth capability" section. **What's still open:** no `--apply` has run against any
   > environment yet — the product owner will execute it directly, per that same section (no session
   > in this repo has live Supabase credentials).
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
   >
   > **[RE-VERIFIED 2026-08-20, SAME DAY — (b) NOW FULLY DONE.]** The remaining half of (b) is
   > closed: `admin_driver_distance_logs` (`backend/routes/admin/driver_distance.py`, `GET
   > /drivers/{id}/distance-logs`) — the existing per-span drill-down that already lists one row
   > per `driver_insurance_periods` span for a Regina day — now includes `is_reconstructed` per
   > row (additive field; no other field changed). The admin-dashboard `DayLogs` table
   > (`admin-dashboard/src/app/dashboard/drivers/_components/driver-distance.tsx`) renders a small
   > "Reconstructed" badge next to the phase badge when true, styled like the existing "Imported"
   > badge (`docs/change-log/2026-08-19-legacy-migration-transparency-admin-dashboard.md`), with an
   > `aria-label`/`title` text alternative (not color-only). No new screen was added — this route/
   > table was judged the natural home since it is already the one place that lists raw insurance-
   > period spans one row at a time rather than an aggregate; the daily-activity summary tab
   > (`admin_driver_daily_activity`) aggregates spans into per-phase totals and has no natural
   > per-row slot. See `docs/change-log/2026-08-20-insurance-period-reconstructed-admin-column.md`.
   > **(a) is unchanged and still fully open** — out of scope for this pass, not attempted.
   >
   > **[RE-VERIFIED 2026-08-20, LATER SAME DAY — (a) NOW ADDRESSED AS A VERIFICATION PASS, NOT A
   > RE-INSERT.]** `backend/services/insurance_period_reconstruction_verification.py` +
   > `backend/scripts/verify_legacy_insurance_period_reconstruction.py` (14 new tests) stream the real
   > `driverlocationlogs.csv` (148 MB, 7,948 rows — never loading `way_points` into memory beyond the row
   > it's part of) and compare its real phase-boundary timestamps against migration 332's already-inserted
   > rows for the same 186 rides. Only 3 distinct `phase` values exist in the real export (`idle`,
   > `going_to_pickup`, `on_ride` — no separate "arrived" phase), enumerated via a streaming script before
   > any mapping was assumed; `going_to_pickup` maps to the whole of Period 2 and `on_ride` to Period 3.
   > **Finding, verified against the real 186-row set (read-only, via Supabase MCP)**: Period 3's boundary
   > was accurate (median 0.6s divergence from `ride_completed_at`) but Period 2's start was systematically
   > understated by migration 332's `driver_arrived_at` proxy — median ~580s (~9.7 min) earlier, up to
   > ~10.5h in one outlier — for every one of the 156 cleanly-reconstructable rides (25 have ambiguous
   > phase-span counts, 1 has no CSV data, 4 remain migration-332-excluded). This *confirms and quantifies*
   > a limitation migration 332's own header comment already disclosed, rather than surfacing something
   > unknown.
   >
   > **No new `driver_insurance_periods` rows were written, and none will be by this tool.** Migration
   > 332's rows are all closed (`ended_at` set); its immutability trigger unconditionally blocks any
   > `UPDATE` to a closed row regardless of which column changes — re-read directly from the trigger
   > function, not assumed. Inserting a second, competing set of rows for an already-covered ride was
   > considered and rejected: nothing in the schema says which of two overlapping spans for the same
   > `ride_id`/`period` is authoritative, and `.claude/context/domain-safety.md`'s intended fix for exactly
   > this — a `driver_insurance_period_corrections` table — does not exist (confirmed by grep and a live
   > `information_schema.tables` query). Building that table is filed as `ACTION_ITEMS.md` B34, not
   > attempted here. `apply_verification_plan()` always raises; this pass is read-only by design. See
   > `docs/change-log/2026-08-20-insurance-period-reconstruction-verification.md` for the full reasoning
   > and numbers. **Item #5 is now fully addressed**: (a) is a verification pass with a documented,
   > quantified finding and an explicit non-decision on correction (flagged for a human/compliance call);
   > (b) was already closed in the same-day re-verification above.
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
   > before apply) has not been violated.
   >
   > **2026-08-20, sixth pass — CRITICAL: a bug was found and fixed that would have made this script
   > silently no-op if `--apply` had been run before this fix.** Its CSV reader routed through header
   > normalization built for a different CSV dialect, which mangled the Mongo export's `_id` column
   > and would have resolved 0/157 real rows (0 updates, 0 errors — indistinguishable from a clean
   > successful run). Caught while building item #4 above, fixed same day, verified against the real
   > export (157/157 now resolve correctly). See
   > `docs/change-log/2026-08-20-mongo-export-header-normalization-bug.md`. This does not change this
   > item's status (the minimization sign-off itself was always correct) but is essential context for
   > anyone about to run `--apply`: confirm your checkout includes this fix first.
   >
   > Note this is a narrower ask than item #1's broader
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
   >
   > **2026-08-20, seventh pass — FULLY ADDRESSED, all four fields now have an explicit decision.**
   > DOB: built `driver_import_service.dob_source()`, same `"legacy_import" | "self_entry" | None`
   > contract as `sin_source()`, wired into the admin driver live-stats read path
   > (`dob_source`/`dob_on_file` keys, mirroring `sin_source`/`sin_on_file`). **Deliberately NOT**
   > wired into the T4A filer-handoff export — checked `_t4a_filer_handoff_rows`'s drivers-column
   > projection first; DOB is not a field that export reads or displays at all (only SIN/earnings/
   > Stripe-verified legal name), so there is nothing there to attach provenance to. Unlike SIN,
   > DOB has a second legacy-import write path (the original Saskatoon CSV import writes
   > `date_of_birth` directly at driver creation, not just the later `banks.csv` backfill — `sin` is
   > never written by that CSV import at all), so `dob_source()` is not a literal copy of
   > `sin_source()`'s single-marker check; see its docstring for the full derivation and the
   > mislabeling trap a literal copy would have caused. Raw DOB is never surfaced by either new
   > field — provenance/presence only, per PIPEDA.
   >
   > Email: investigated and decided **no dedicated flag**, documented in
   > `docs/change-log/2026-08-20-legacy-dob-email-provenance-flags.md`. Reason: email is not a
   > set-once, verification-sensitive field the way SIN (locked after first entry, tax-filing use)
   > and DOB (no self-entry route at all today, so an unverified legacy value can persist
   > indefinitely) are. `routes/users.py`'s `create_profile` (`POST /profile`, the primary profile-
   > completion flow every phone-first signup goes through) unconditionally overwrites `users.email`
   > with whatever the person types, with no guard protecting a legacy-imported value — so the instant
   > a legacy-imported rider or driver completes their profile, their email is fully self-entered and
   > the import-sourced value is gone. There is also no existing timestamp/marker analogous to
   > `sin_collected_at` to derive a provenance label from without a new column, which would not be a
   > pure-additive, zero-migration change like `sin_source()`/`dob_source()` are. The existing
   > whole-profile "Imported" badge already discloses "this profile may carry unverified legacy data"
   > at the right granularity for a field that self-corrects through ordinary use. All four fields in
   > this item now have an explicit, documented decision — SIN and DOB: yes, with flags; name: whole-
   > profile signal only (existing, unchanged); email: no, with the above reasoning. Item #7 is
   > closed.
8. **Explicit include/exclude sign-off, recorded per collection**, for every `REVIEW`-tagged
   collection from the inventory doc — a silent drop at decommission time is not acceptable for
   data about to become permanently unrecoverable.
   > **[RE-VERIFIED 2026-08-20, SEVENTH PASS — FULLY ADDRESSED.]** The systematic sweep this item
   > calls for is now done: every `REVIEW`-tagged collection in
   > `docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md` has an explicit, recorded
   > include/exclude decision (see that file's new "Sign-off recorded (2026-08-20)" section at the
   > top). Put to the product owner directly via `AskUserQuestion` — not inferred: apply the audit's
   > own recommended defaults across all remaining collections (exclude coupons/old-subscriptions/
   > referral-config/doc-history/chat-history/complaints/ratings-carryover, and confirm
   > `servicelocations`' India-region rows as dev/template cruft) rather than a case-by-case review.
   > `banks.csv` was already resolved via item #6. Two residual, non-blocking, low-priority gaps
   > remain and are explicitly flagged as unverified rather than silently assumed: `pages.csv`'s and
   > `faqs.csv`'s specific "diff against live app content" sub-asks (findings #7 and the `faqs` row)
   > were never import candidates either way, so neither creates new migration risk — just an
   > unverified content-completeness question for a future session with live-app-content access.
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
