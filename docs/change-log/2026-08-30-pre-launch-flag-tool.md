# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Owner directive: "we launched on march 30 so anything before that is junk or test data please ignore and may be we should consider these for cleanup before go live" |

## 1. Issue / gap identified

The owner confirmed Spinr's public launch date (2026-03-30) and asked that
pre-launch legacy data be considered for cleanup before go-live. A survey of
what's already migrated found real exposure: 343 legacy-imported driver
profiles have a pre-launch `created_at`, and 25 already-imported rides
predate launch. Neither had any marker distinguishing pre-launch test data
from real historical activity, so admin views/KPIs (driver counts, ride
totals) can't filter it out.

The owner was asked (via `AskUserQuestion`) how to handle this and chose
**"flag only, don't delete"** for both drivers and rides — this tool
implements that decision.

## 2. Root cause

Every prior legacy importer in this migration effort predates the
2026-08-30 launch-date confirmation, so none of them classify rows by
launch date. This isn't a bug in those importers — the launch-date concept
simply didn't exist yet when they were built.

## 3. Fix / remediation

**A real correctness issue found and fixed before writing any commit
path**, not just the tool itself: the first, looser criterion tried
("driver's own `created_at` is before launch") would have flagged 343
drivers — but 33 of those have driven a real ride or hold a real
`driver_insurance_periods` row, meaning they onboarded during a pre-launch
beta window and are genuine active drivers, not test data. Checked directly
against production before building anything. The actual criterion used is
narrower and activity-based: legacy-imported, `created_at` before launch,
**and** zero rides ever driven, **and** zero `driver_insurance_periods`
rows ever. That's **310** drivers, not 343 — and 0 of those 310 are
currently online or available (checked directly), so there is zero
live-dispatch exposure today.

Rides have no comparable ambiguity — a ride either happened or it didn't,
and Spinr had no real customer base before launch by definition. All 25
pre-launch rides are flagged; all 25 are status `completed` (real pre-launch
test trips, not cancelled/failed noise), confirmed before building.

**Built:**
- `backend/services/pre_launch_flag_service.py`: `build_pre_launch_flag_plan()`
  (read-only) / `apply_pre_launch_flags()` (additive write). Sets
  `legacy_import_metadata.pre_launch_test = true` plus an audit sub-object
  (`{batch, flagged_at, reason}`) on matched rows. Never deletes,
  deactivates, or mutates any other field. Idempotent: an already-flagged
  row is never re-offered.
- Concurrent-writer safety on `rides.legacy_import_metadata` (which two
  other backfills — `legacy_gst_backfill_service.py`,
  `booking_import_service.apply_duration_estimated_backfill` — also
  read-merge-write): the exact same whole-column optimistic-concurrency
  guard those use (`.filter("legacy_import_metadata", "eq", <json of what
  was just read>)`), plus a narrow `pre_launch_test IS NULL` guard. Applied
  to `drivers` too, for the same protection, even though no other backfill
  is known to write `drivers.legacy_import_metadata` concurrently today.
- `backend/routes/admin/pre_launch_flag.py`: `/preview` (read-only) /
  `/commit` endpoints, `require_super_admin` (bulk write across two core
  tables, same posture as the wallet importer). No file upload — unlike
  every other tool in this migration effort, this operates entirely on
  already-migrated production data; commit re-plans fresh server-side
  (same idempotent re-plan pattern the CSV importers' commit endpoints use).
- Admin-dashboard `PreLaunchDataFlag` component under Bulk Operations:
  Preview → confirm-phrase-gated Apply, same UX pattern as
  `LegacyWalletImport`.
- 13 service-layer tests (the dormant-vs-active distinction is the load-
  bearing one: explicit regression tests prove a pre-launch driver with a
  ride, or with an insurance period, is excluded) + 8 HTTP-level tests.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `pre_launch_flag_service` has exactly one
  caller (`routes/admin/pre_launch_flag.py`); grepped to confirm.
- **Additive only, no existing behavior changes.** `pre_launch_test` is a
  new JSONB key nothing currently reads — flagging a row today has zero
  effect on dispatch, KPIs, or any live code path until something is later
  built to read the flag (e.g. an admin-dashboard filter). That's
  intentional: this PR only adds the marker; using it to filter admin views
  is a separate follow-up, not bundled here.
- **Two other backfills share `rides.legacy_import_metadata`** — addressed
  directly via the whole-column optimistic-concurrency guard (see §3); a
  race is reported as a conflict count in the commit response, never
  silently dropped or double-applied.
- **Zero live-dispatch exposure**: none of the 310 candidate drivers are
  currently online or available, confirmed via direct SQL before building.
- **Confirmed the correctness fix works**: 13 service tests include
  explicit cases for a pre-launch driver with a ride (excluded), with an
  insurance period (excluded), a post-launch dormant driver (out of scope
  entirely — not pre-launch data), an already-flagged row (not re-offered),
  and a driver imported without a recognized `source` (never a candidate
  regardless of date).

## 5. User-experience effect

None yet. Internal admin-only, additive-only metadata change with no reader
built yet. Riders/drivers see nothing different. A super_admin visiting
Bulk Operations will see the new "Pre-launch legacy data flagging" card and
can choose to run it; nothing happens automatically.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/pre_launch_flag_service.py` | New: plan/apply for the additive flag | Core logic |
| `backend/routes/admin/pre_launch_flag.py` | New: preview/commit routes | Sanctioned production-write path |
| `backend/routes/admin/__init__.py` | Mount the new router under `require_super_admin` | Wire the route into the app |
| `backend/utils/rate_limiter.py` | New `pre_launch_flag_preview_limit`/`pre_launch_flag_commit_limit` | Match every other backfill's rate limit |
| `backend/tests/test_pre_launch_flag_service.py` | New: 13 service-layer tests | Lock in the dormant-vs-active criterion |
| `backend/tests/test_admin_pre_launch_flag.py` | New: 8 HTTP-level tests | Lock in the route's contract |
| `admin-dashboard/src/lib/api/imports.ts` | New client functions/types | Frontend client for the two new routes |
| `admin-dashboard/src/lib/api.ts` | Re-export the new symbols | Keep the barrel export in sync |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx` | New component | Preview → Apply UI |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Mount the new component | Make the tool reachable |

## 7. Before / after

Not applicable — every file except `admin/__init__.py`, `rate_limiter.py`,
`api.ts`, and `bulk-operations/page.tsx` is new; those four are pure
additions (a new import line, a new router mount, new rate-limit constants,
new re-exports, a new component mount) with no existing line changed.

## 8. Rollback plan

`git revert` for all code. Data written by a real commit run (should an
operator run it) is identifiable via
`legacy_import_metadata->>'pre_launch_flag'` for a targeted rollback — e.g.
`UPDATE drivers SET legacy_import_metadata = legacy_import_metadata - 'pre_launch_test' - 'pre_launch_flag' WHERE legacy_import_metadata->>'pre_launch_flag' IS NOT NULL AND legacy_import_metadata->'pre_launch_flag'->>'batch' = '<batch>'`
(and the equivalent for `rides`) — no other field or table is touched by
this tool, so unflagging has no cascading side effects.

## 9. Verification performed

- [x] Investigated the real data before writing any code — confirmed the
  33-driver false-positive risk of a naive date-only criterion via direct
  SQL, refined to the activity-based 310-driver criterion, and confirmed
  all 25 pre-launch rides are `completed` (not cancelled/failed noise) —
  all via direct queries against production, not assumed.
- [x] `pytest tests/test_pre_launch_flag_service.py tests/test_admin_pre_launch_flag.py`
  — 21 passed (13 service + 8 route). Also re-ran alongside
  `test_wallet_import_service.py`/`test_admin_wallet_import.py` (the two
  other pre-launch-cutoff changes made in this same session) — 59 passed
  total, no regressions.
- [x] `ruff check` / `ruff format --check` on every touched Python file —
  clean (43 pre-existing, unrelated repo-wide lint findings confirmed not
  touched by this change, same figure as prior Change Impact Logs this
  session).
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` (admin-dashboard) — real production build, succeeded,
  new component compiled.
- [x] Blast-radius grep: `pre_launch_flag_service` has exactly one caller.

## What was NOT verified

- Not yet run against real production — previewing, then applying, is the
  operator's next step once this deploys; production will be re-checked
  directly via SQL afterward, same rigor as every other verification this
  session.
- No admin-dashboard filter/view was built to actually *use* the new
  `pre_launch_test` flag once set (e.g. hiding flagged drivers from the
  default driver-count KPI) — this PR only adds the marker. Using it is a
  deliberate follow-up, not bundled here, since the owner's request was
  specifically "flag only" as step one.
- The 819 legacy-imported drivers with zero activity but a *post*-launch
  `created_at` (i.e. imported and never engaged, but not pre-launch data)
  were found during this investigation and are explicitly out of scope —
  they're not what the owner asked about, and conflating "never active" with
  "pre-launch junk" would be a different, larger decision this PR doesn't
  make.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`; flagged rows
  are individually identifiable and unflaggable by their own provenance tag)
- [x] Blast radius is stated, not assumed (one caller; grepped confirmation)
- [x] No silent behavior change to any existing flow — purely additive
  metadata nothing currently reads differently based on it
