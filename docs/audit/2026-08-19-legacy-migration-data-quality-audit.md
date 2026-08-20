# Legacy-Migration Data-Quality Audit — Backend, Admin Portal, Driver-App, Regulatory

**Date:** 2026-08-19 · **Posture:** Mixed — most of this is AUDIT-ONLY (findings, no code changed),
but 3 findings were fixed same-session (each has its own Change Impact Log, linked below).
**Trigger:** user question — is the 2026-07-29 legacy import "clean and relevant," could it
reproduce the same class of confusion as the same-day driver-earnings-tip-underpayment incident,
and does the codebase need a real strategy for the Oct 30 final cutover.

## Plain-English summary

1. **This morning's specific incident (driver Activity panel showing "Fare $0.00" / mismatched
   "Total Earned") was confirmed unrelated to the migration.** It was a general delta-math bug in
   tip settlement (`docs/change-log/2026-08-19-driver-earnings-tip-underpayment.md`), already fixed
   and corrected in production before this audit started.
2. **But the underlying worry — could the migration itself produce this class of bug — was
   justified, and this audit found three real instances**, one of them live-exploitable (a legacy
   ride could be tipped for real money) and one of them live-active (four admin financial-dashboard
   numbers were silently inflated for roughly the first week post-cutover, and would inflate again
   on any report window overlapping the imported rides' historical dates). All three are fixed as
   of this audit — see §Fixed below.
3. **A fourth, more serious category was found and is NOT fixed**: there is no consent record on
   file for any imported rider or driver — not even the honest "predates consent tracking" null
   state organic pre-tracking users correctly have. This is a legal decision, not an engineering
   fix, and it's sharpened by today's SIN/DOB backfill collecting the single most sensitive PIPEDA
   field for exactly this un-consented population.
4. **A long tail of presentation-layer gaps exists on both the admin dashboard and the driver app** —
   blank name fields with no fallback, "every document Missing" for drivers whose paperwork
   predates the import, an admin table column that renders completely empty for a legacy driver's
   ride history. None of these lose or corrupt data; all of them look like the app is broken to
   whoever's looking at them. None fixed this session — see the prioritized list below.
5. **The method matters as much as the findings**: five specialized review agents ran in parallel
   (migration data-integrity, money/fare risk, regulatory/PIPEDA, admin-dashboard display,
   driver-app display), each scoped to avoid re-deriving what prior audits (A25-A34,
   `docs/audit/2026-08-19-full-mongodb-export-collection-inventory.md`) already found. This is the
   template for the Oct 30 final-cutover data-quality gate — see the companion playbook.

## Fixed this session

| # | Finding | Severity | Fix | Change Impact Log |
|---|---|---|---|---|
| 1 | `add_tip` (payments.py) had no legacy-ride guard — a legacy ride's matched rider could trigger a real Stripe charge for a pre-Spinr trip, and `driver_earnings_with_tip()` would silently inflate the driver's payout by the old app's admin commission | Blocker (live-exploitable) | Guard added, mirroring `rating.py`'s existing one; `driver_earnings_with_tip()` hardened to refuse legacy rides outright | `docs/change-log/2026-08-19-legacy-ride-tip-guard-and-earnings-hardening.md` |
| 2 | 4 admin money aggregates (`admin_earnings_overview_agg`, `admin_earnings_daily_series`, `admin_dashboard_money`, `get_dashboard_overview`'s ride counts) never got the A25/A26/302/303 legacy-ride exclusion | High (live-active for ~1 week post-cutover, recurs on any overlapping window) | Migration 341 + `analytics.py` fix | `docs/change-log/2026-08-19-admin-money-aggregates-legacy-exclusion.md` |
| 3 | `apply_legacy_sin_dob_import`'s never-clobber guarantee was plan-time-snapshot-only, not write-guarded | High (latent — caught before any `--apply` run) | Write-time `.is_(col, "null")` guard added, mirroring `stripe_mapping_import_service.py` | Amendment to `docs/change-log/2026-08-19-legacy-sin-dob-import.md` |

## Not fixed — regulatory (source: `spinr-regulatory-compliance-checker`)

**BLOCKER — no consent basis for imported users.** Imported riders/drivers never went through any
Spinr consent flow. This is different from — and more serious than — the already-understood
"`consent_version IS NULL` because this user predates consent-version tracking" state
(`docs/change-log/2026-08-19-consent-version-signup-fix.md`), which is honest for organic users but
inaccurate framing for imported ones. Sharpened by today's SIN/DOB backfill: the single most
sensitive PIPEDA-class field, collected for a population with zero consent record for *any* use.
Whether the old app's own `pages.csv` consent (found in the Mongo export, "Version 2.0,
PIPEDA-Compliant") transfers to this new use is a legal question, not an engineering one — needs a
decision before the SIN/DOB importer's `--apply` step runs.

**BLOCKER (for the Oct 30 cutover specifically) — `is_reconstructed` insurance periods are
invisible to the regulator-facing export.** Migration 332 correctly flags 182 legacy rides'
reconstructed `driver_insurance_periods` rows with `is_reconstructed = true`, but
`backend/scripts/compliance_export.py` — the tool `regulatory-sk.md` documents as the actual SGI
subpoena-response mechanism — never references that column. A regulator request today would
receive reconstructed data with no marker distinguishing it from a contemporaneous log.

**Cross-cutting**: 941/1,210 (78%) of the old app's bookings — cancelled and failed rides — have no
import path today and no scheduled one. They still carry full pickup/dropoff GPS and `created_at`.
If the old app is decommissioned before a cancelled-booking importer exists, this data is gone
permanently — a real deadline, not a someday item. (Previously flagged in the P0/P2 dual-run-cutover
docs; restated here because it's load-bearing for the Oct 30 checklist below.)

**The full 10-item ordered data-quality checklist this agent produced for the Oct 30 cutover is
reproduced verbatim in the companion playbook** (`docs/runbooks/legacy-migration-playbook.md`) —
not duplicated here.

## Not fixed — migration data-integrity (source: `spinr-migration-reviewer`)

- **`sin_collected_at` misrepresents provenance.** A SIN pulled from `banks.csv` gets the same
  `sin_collected_at` timestamp self-entry produces — the column's own documented meaning
  ("when the driver supplied it," migration 289) is false for a backfilled row, and two real
  consumers (admin driver-detail view, the T4A-filer compliance export) display it at face value.
  No `sin_source`/equivalent flag exists to distinguish the two. **Recommended fix direction**: add
  a distinguishing flag (or simply omit `sin_collected_at` for backfilled rows, relying on
  `legacy_import_metadata` alone) — small, but a schema/design call, not made this session.
- **Legacy rides' estimated `duration_minutes` carries no per-row marker.** When a legacy booking
  has no `start_ride_at`, the importer estimates duration from distance (`distance_km / 30kmh * 60
  + 5`) and logs a warning at import time — but the resulting value is indistinguishable from a
  real measured duration once committed, and it feeds the driver-facing Activity screen's "Total
  Duration" stat. **Recommended fix direction**: stamp `legacy_import_metadata.duration_estimated:
  true` (cheap, additive) so any consumer can choose to exclude/flag it.
- **No admin screen marks a driver or rider *profile* record as legacy-imported** — only ride rows
  have the A30 badge. `legacy_import_metadata` already reaches the frontend on every driver row
  (confirmed: `admin_get_drivers` uses no restrictive `columns=`); it's simply unused. This is the
  single biggest lever for the transparency goal in the user's original request — an admin looking
  at a legacy driver's blank fields currently has no way to recognize "known migration gap" versus
  "broken profile."
- Two lower-severity, non-urgent items: `rider_import_service.py` omits blank `first_name`/`email`
  keys entirely (NULL) where `driver_import_service.py` writes empty strings for the same
  logical state (inconsistent representation, low practical risk today); the old app's raw
  `payout_gst_amount` is preserved unmerged pending the already-tracked D1 business decision
  (not a new finding, confirmed still correctly deferred).

## Not fixed — driver-app display (source: general-purpose review agent)

- **`ActivityView.tsx` silently swallows an earnings-fetch failure** (`catch {}`) and renders a
  fully-formed "$0.00" earnings screen indistinguishable from a genuine zero balance. **This is
  general, not migration-specific** — reachable by any driver on a transient backend error — and is
  the same failure class as this morning's incident, just entirely client-side. Flagged as the
  highest-severity finding in this agent's report; not fixed this session (frontend work, out of
  this session's backend-focused fix budget).
- Client-derived "Fare" line (`totalEarnings − tips − incentives − bonuses − tax`) omits
  `total_cancel_fees` (which the backend total includes) and clamps to exactly `$0.00` on any
  drift — same "components disagree with the headline number" failure shape as this morning's bug.
- Profile screen's Vehicle card has no blank-field fallback, unlike every other field on the same
  screen — a legacy driver with unpopulated vehicle data sees a visibly broken-looking row.
- Documents screen shows every requirement "Missing"/"UPLOAD REQUIRED" for a legacy driver whose
  old-app document *images* were never part of the export (filenames only, no bytes) — no copy
  distinguishes this from "you never uploaded this," for an already-approved, already-driving
  legacy driver.
- Lower severity: per-ride card fare defaults to an unlabeled `$0.00` for a ride missing
  `total_earned` (low actual risk — the field is reliably populated for legacy rides today); email
  fallback ("N/A") gives no nudge that it matters for tax-document delivery, disproportionately
  affecting the ~65% of legacy drivers whose source record had no email.

## Not fixed — admin-dashboard display (source: general-purpose review agent)

- Driver list/detail renders blank name with **no fallback** — `users/page.tsx` (the rider-facing
  equivalent) already has `|| email || phone`; `drivers/page.tsx` doesn't. Reachable today: the
  driver importer writes `""` (not skipped) for a blank `full_name`.
- `DriverRidesTab`'s `driverName` prop drops the fallback its sibling `DriverPayoutsTab` has —
  produces a literally empty "Driver" table column on every row of a legacy driver's ride history,
  and a subject-less zero-state sentence (`" has not completed any trips."`).
- "No payout method" messaging is identical for "new driver hasn't finished onboarding" and
  "migrated driver whose old-app banking is permanently unrecoverable" (banks.csv has no import
  destination today — see the earlier collection-inventory audit) — an admin can't tell which
  remediation applies without a separate check.
- Lower severity: "Total Rides" vs "Earnings" stat cards on the same row apply inconsistent legacy
  policy (one includes legacy rides, one excludes them) with no note — this is the concrete UI
  location for the still-open ACTION_ITEMS A28 "unreconciled totals" item, previously undated.

## What this changes about "is the migration clean"

Read plainly: the migration's **data** (row counts, field population, ID crosswalks) checked out
well in the earlier collection-inventory audit — this session's finding is that the **code paths
consuming that data** had four real gaps (three now fixed) where legacy-shaped rows could trigger
behavior nobody designed for them: a money-formula assumption that didn't hold, an exclusion filter
that was copy-pasted to two functions but not four more, a race condition in code that hadn't
shipped yet, and a long tail of "the UI assumes every field is populated" bugs. None of this is
data corruption — it's downstream code that was written for organic rows and never re-verified
against the shape of an imported one. That is exactly the risk profile the companion playbook is
built to catch before it happens again at the Oct 30 cutover, rather than being found live,
one incident at a time, the way today's tip-underpayment bug was.

## What was NOT verified

- No live Supabase access this session — every "live-active" claim above (the admin money
  aggregates, the tip-guard exploitability) is derived from code reading + the already-known import
  batch dates, not a live query confirming it actually happened in production.
- The driver-app and admin-dashboard findings are static-code-reading audits, not screenshotted or
  run against a real device/browser — this repo has no automated visual-regression tooling (a
  standing gap, tracked separately in `ACTION_ITEMS.md`).
- Coverage is bounded by what 5 agents scoped in one pass found — not an exhaustive guarantee no
  other legacy-shaped-row bug exists elsewhere in the codebase.
