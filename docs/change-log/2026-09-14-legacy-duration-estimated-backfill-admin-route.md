# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session), on behalf of ittalenthire.ca@gmail.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `fix/a41-duration-backfill-admin-route` |
| Related issue or gap ID | `ACTION_ITEMS.md` A41 residual sub-item — of the 4 legacy-migration backfill tools, this was the last one still CLI-only per `docs/runbooks/migration-tool-order.md`'s "Not part of the ordered chain" section |

## 1. Issue / gap identified

The legacy ride `duration_estimated` marker backfill (`services/booking_import_service.py`'s
`plan_duration_estimated_backfill`/`apply_duration_estimated_backfill`, exercised by
`scripts/backfill_legacy_ride_duration_estimated.py`) was CLI-only — an operator needed shell +
`SUPABASE_SERVICE_ROLE_KEY` access to run it, unlike its three siblings (Legacy Driver Import,
SIN/DOB backfill, Vehicle-History backfill), which already have an admin-dashboard
validate/review/commit path.

## 2. Root cause

The backfill was built 2026-08-19 as a "ready to run, rollout decision deferred" CLI tool (see
`docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md`) before any admin-dashboard
equivalent existed for it, and no follow-up session ever gave it one — not a design decision,
just an unbuilt follow-up, same root cause as the SIN/DOB and vehicle-history admin routes had
before their own 2026-08-28 fixes.

## 3. Fix / remediation

Added a pure HTTP wrapper — no business logic changed, and the underlying
`plan_duration_estimated_backfill`/`apply_duration_estimated_backfill` pair in
`services/booking_import_service.py` was not touched at all:

- `backend/routes/admin/legacy_duration_estimated_backfill.py`: two endpoints,
  `POST /api/admin/legacy/duration-estimated-backfill/{preview,commit}`.

**Alternative considered, and why the chosen shape wins (CLAUDE.md gate #10):** the task's own
pointers named the SIN/DOB and vehicle-history backfills' two-CSV-upload + signed-commit-token
pattern (`utils/driver_import_token.py`) as the blueprint. That pattern doesn't fit here — this
backfill takes **no file upload at all**: `plan_duration_estimated_backfill()` reads directly
from Supabase (`rides.legacy_import_metadata->>source = IMPORT_SOURCE`), so there is no CSV byte
hash to bind a commit token to. The actually-analogous, already-established sibling pattern in
this same codebase is the **no-file, preview/commit, re-plan-fresh-on-commit** shape used by
`migration_data_quality.py`, `driver_dormancy.py`, and `migration_driver_repair.py` — all three
read live production data with no upload, gate on `require_super_admin`, and re-run the plan at
commit time instead of trusting a signed token from an earlier /preview call (which is safe here
specifically because a re-plan is cheap and the write itself carries its own idempotency
guard). I followed that pattern exactly rather than force-fitting the file-upload/token pattern,
since inventing a fake CSV or a token bound to an arbitrary hash would be speculative complexity
this task doesn't need (CLAUDE.md "Simplicity first"). This route's `_report()`/`_build_plan()`
shape, `require_super_admin` gate, and rate-limit posture are line-for-line the same shape as
`migration_data_quality.py`.

- `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx`:
  a preview → review → commit card, mirroring `DataQualityScan.tsx` exactly (no CSV inputs, no
  type-to-confirm gate — justified the same way `DataQualityScan.tsx` justifies skipping it:
  additive-only, never reassigns a driver/rider, never touches `rides.status`, and here never
  touches `duration_minutes` either).
- Client API types/functions in `admin-dashboard/src/lib/api/imports.ts` (new
  `DurationEstimatedBackfill*` section) re-exported from `lib/api.ts`.
- New `duration_estimated_backfill_{preview,commit}_limit` (30/hour, 10/hour) in
  `utils/rate_limiter.py`, same posture as the other no-CSV backfills.
- New router mounted in `routes/admin/__init__.py` on `require_super_admin`, next to
  `driver_dormancy_router` (most recently added sibling of the same shape) and before
  `migration_status_router`.
- Wired into the existing Bulk Operations page's Phase 5 ("Finish the ride records") section,
  after the two other Phase-5 tools that also write onto rides Phase 4 already imported
  (Snapshot/Route regeneration) — updated that phase's one-sentence overview text from "both"
  to "all three" tools to stay accurate now that a third tool lives there. No other page copy
  changed.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Grepped for every other caller of the two service functions and the
shared audit/rate-limit utilities this route reuses:

- `plan_duration_estimated_backfill` / `apply_duration_estimated_backfill` — only two callers
  repo-wide before this change (`scripts/backfill_legacy_ride_duration_estimated.py`, the
  pre-existing CLI, untouched by this diff) and now this new route as a third. Neither existing
  caller was modified; the CLI script still works exactly as before.
- `services/booking_import_service.py` itself — **not modified in any way.** No line inside it
  changed; this diff only adds a new caller in a different file.
- `log_admin_action` (`utils/audit_logger.py`) — reused unchanged; every other admin route that
  calls it (dozens) is unaffected, since this route only adds one more call site with its own
  `action`/`resource` strings (`"legacy_duration_estimated_backfill"`, `"rides"`).
- `rate_limiter.py` — purely additive: two new named limiter instances
  (`duration_estimated_backfill_preview_limit`/`_commit_limit`), no existing limiter touched.
- `routes/admin/__init__.py` — additive: one new import line, one new `include_router()` call.
  Every existing `include_router()` call above and below it is untouched, so no other router's
  mount order, dependency gate, or path prefix changes.
- Consumers of `rides.legacy_import_metadata` more broadly (the same population this backfill's
  service layer already documented in
  `docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md` §4, re-checked here since
  this diff adds a new *caller* of the write path, not a new writer): unaffected, because the
  write itself is unchanged — this route calls the exact same `apply_duration_estimated_backfill`
  the CLI already calls, with the same whole-column optimistic-concurrency guard and the same
  never-clobber guard on the `duration_estimated`/`legacy_duration_estimated_backfill` keys. No
  other reader/writer of that column had to be re-audited because nothing about *how* the column
  is written changed — only *what can trigger* that write (an admin click, in addition to a CLI
  invocation).
- `bulk-operations/page.tsx` — additive: one new import, one new overview-text edit (word-level,
  not a rewrite), one new `<DurationEstimatedBackfill />` render call in Phase 5. No existing
  section's component, state, or props changed.
- `admin-dashboard/src/lib/api.ts` / `api/imports.ts` — additive: new exported names appended to
  existing export lists; no existing exported name, type, or function signature changed.
- **Admin-dashboard visual-regression coverage (confirmed by reading the spec file directly, not
  assumed):** `e2e/visual-regression.spec.ts`'s `PAGES` array seeds exactly 6 pages — `login`,
  `dashboard-home`, `dashboard-rides`, `dashboard-drivers`, `dashboard-monitoring`,
  `dashboard-settings`. `/dashboard/bulk-operations` (the page this change touches) is **not**
  one of the 6, so no baseline exists for it and this diff cannot fail that merge-blocking job.
  This change also does not touch `sidebar.tsx`, `topbar.tsx`, or any other shared layout file
  the 6 seeded pages render — confirmed by grep, no such file appears in this diff — so there is
  no indirect risk to an existing baseline either.

## 5. User-experience effect

Internal-admin-facing only (super_admin). No rider, driver, or corporate-admin-facing surface
change. Not visible mid-session to anyone outside the admin dashboard — this is a new tool on an
existing internal ops page, not a change to an existing screen's behavior. The one page-copy
edit (Phase 5's overview sentence) is visible to any admin who already uses this page, but is a
factual update (now describing 3 tools instead of 2), not a behavior change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/legacy_duration_estimated_backfill.py` | New file — validate/commit HTTP endpoints wrapping the existing, unmodified service functions | Give the CLI-only backfill an admin-dashboard path |
| `backend/routes/admin/__init__.py` | +1 import, +1 `include_router()` under `require_super_admin` | Mount the new router |
| `backend/utils/rate_limiter.py` | +2 named limits (`duration_estimated_backfill_preview_limit` 30/hour, `duration_estimated_backfill_commit_limit` 10/hour) | Rate-limit both endpoints, same posture as every sibling no-CSV backfill |
| `backend/tests/test_admin_legacy_duration_estimated_backfill.py` | New file — 8 endpoint tests | Cover preview/commit, super_admin gate, idempotent re-run, `duration_minutes` never touched, legacy-only scan scope |
| `admin-dashboard/src/lib/api/imports.ts` | New "Duration-Estimated Marker Backfill" section: types + `adminPreviewDurationEstimatedBackfill`/`adminCommitDurationEstimatedBackfill` | API client for the new endpoints |
| `admin-dashboard/src/lib/api.ts` | Appended the two new functions + four new types to the existing `./api/imports` re-export lists | Keep the barrel import surface complete |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DurationEstimatedBackfill.tsx` | New file — preview → review → commit card, no file inputs | Admin-dashboard UI for the backfill |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | +1 import, +1 `<DurationEstimatedBackfill />` render call in the existing Phase 5 section, 1-sentence overview-text edit ("both" → "all three") | Wire the new tool into the page it belongs on |
| `docs/change-log/2026-09-14-legacy-duration-estimated-backfill-admin-route.md` | New file — this log | Required for a live-tested surface (`rides`) per CLAUDE.md |
| `ACTION_ITEMS.md` | A41 residual-gap note updated to reflect this fix | Close out the one remaining "still CLI-only" citation |

## 7. Before / after

Pure additive — no existing endpoint, function, or exported symbol changed behavior. The one
non-additive text change (Phase 5's overview sentence) is not a behavior-changing diff (page copy
only), but shown for completeness:

```
# Before (admin-dashboard/.../bulk-operations/page.tsx, Phase 5 overview)
overview="Generate map images and road-following routes for the rides Phase 4 just imported — both require rides to already carry legacy import metadata, so they run after Phase 4, not before."
```

```
# After
overview="Generate map images and road-following routes for the rides Phase 4 just imported, and mark which of them have an estimated (vs. measured) trip duration — all three require rides to already carry legacy import metadata, so they run after Phase 4, not before."
```

## 8. Rollback plan

No migration, no schema change, no feature flag needed — this is two new HTTP endpoints plus a
new admin-dashboard UI card, reachable only by a super_admin who deliberately navigates to and
submits the existing Bulk Operations page.

- **Code-level**: revert this commit (or comment out the one `include_router()` line addition in
  `routes/admin/__init__.py`) and redeploy — the endpoints stop being reachable; nothing else
  references them. Removing the `<DurationEstimatedBackfill />` line from `page.tsx` alone (without
  touching the backend) also fully hides the tool from the UI with zero backend redeploy needed.
- **Data already applied via a commit through this route**: identical remediation to the CLI's
  own rollback plan (unchanged — see `scripts/backfill_legacy_ride_duration_estimated.py`'s
  docstring and `docs/change-log/2026-08-19-legacy-duration-estimated-backfill.md` §8), since the
  write path is the exact same `apply_duration_estimated_backfill` call: for each updated ride id
  (visible in the audit log entry's `updated` count and, per-row, in server logs), remove the
  `duration_estimated` and `legacy_duration_estimated_backfill` keys from that row's
  `legacy_import_metadata`, leaving every other key untouched. There is no cascading state (no
  fare recompute, no payout, no Stripe call, no ride-state change, no WebSocket event) triggered
  by this write in either direction, and `duration_minutes` itself is never written by this
  backfill, so there is nothing to revert there.

## 9. Verification performed

- [x] Automated tests run:
  - Backend: `cd backend && python3 -m pytest tests/test_admin_legacy_duration_estimated_backfill.py -v --no-cov` → **8 passed**. Also re-ran the pre-existing, unmodified service-layer suite alongside it and the two closest sibling admin-route suites for regression: `pytest tests/test_legacy_duration_estimated_backfill_service.py tests/test_admin_migration_data_quality.py tests/test_admin_driver_dormancy.py tests/test_booking_import_service.py -q --no-cov` → **74 passed, 0 failed**.
  - Backend lint: `ruff check` and `ruff format --check` on the 4 touched/new backend files → **all clean**.
  - Backend router-assembly sanity: `routes.admin.admin_router` imports cleanly through the app's own test-client fixture (proven by every endpoint test above going through the real FastAPI app, not a bare function call) with no import-time error from the new router.
  - Admin-dashboard: `npx vitest run src/__tests__/dashboard/pages.smoke.test.tsx` → **27 passed** (the existing `/dashboard/bulk-operations` smoke block now also renders the new component, since that block imports and renders the whole page); full suite `npx vitest run` → **72 files / 696 tests passed**.
  - Admin-dashboard type check: `npx tsc --noEmit -p tsconfig.json` → **clean, exit 0**.
  - Admin-dashboard lint: `npx eslint` on the 4 new/touched frontend files → **0 problems**.
  - **Admin-dashboard real production build**: `npm run build` (Next.js/Turbopack) → **succeeded**, `✓ Compiled successfully in 22.4s`, exit code 0, `/dashboard/bulk-operations` listed in the route summary. This is the actual `next build`, not `tsc --noEmit` alone or a dev server.
- [x] Manual repro steps followed in staging — N/A, no staging environment reachable from this session; see "not verified" below.
- [x] Blast-radius grep performed — see §4 above (exact patterns: `plan_duration_estimated_backfill|apply_duration_estimated_backfill`, `duration_estimated_backfill_.*_limit`, every `include_router` call in `routes/admin/__init__.py`, every importer of `DurationEstimatedBackfill` in the admin-dashboard).
- [x] Reviewed against relevant CLAUDE.md conventions — dual-import pattern (used verbatim in the new route file), admin RBAC (`require_super_admin` at both the router mount and re-checked inside each handler, matching every no-CSV sibling), observability (Sentry `domain=rides` tag context via this being a `rides`-table admin action; `log_admin_action` audit-table write on every successful commit), do-not-silently-swallow-errors (the commit handler wraps `apply_duration_estimated_backfill` in try/except and raises a 502 with `logger.error(..., exc_info=True)`, never a bare `warning`).
- [x] Visual-regression coverage explicitly checked, not assumed — read `e2e/visual-regression.spec.ts` directly; `/dashboard/bulk-operations` is not one of the 6 seeded pages, and no shared layout file used by the seeded 6 was touched.
- [x] Ran `/code-review` (high effort) against the diff before committing, per CLAUDE.md gate #10 — a subagent fan-out (`spinr-admin-rbac-reviewer`) was not available as a separate Agent-tool invocation in this session, so this was a single-pass manual walk-through instead. Its one actionable finding (missing Change Impact Log entry) is addressed by this document; its second finding (route-boilerplate duplication across sibling admin routes) is a pre-existing, repo-wide pattern outside this diff's surgical scope, noted rather than fixed.
- [ ] Feature-flagged — not applicable/justified: this is a new, opt-in admin tool behind `require_super_admin`, not a change to an existing user-visible flow; nothing defaults it on for anyone.

## What was NOT verified

- **Not tested against live Supabase** — only the in-memory fake `_Query`/`_FakeSupabase` harness (mirroring the existing `test_admin_migration_data_quality.py`/`test_legacy_duration_estimated_backfill_service.py` patterns). No real Postgres/PostgREST round-trip, no staging deploy.
- **Not manually exercised end-to-end in a running browser** — no `python3 -m backend.server` + dashboard dev server session was started to click through Preview → Review → Commit against a live backend; verification is automated tests + a real production build only.
- **No visual/screenshot check** — `/dashboard/bulk-operations` has no seeded visual-regression baseline (confirmed by reading `e2e/visual-regression.spec.ts` directly, not assumed — see §4), so the new card's actual rendered appearance was reasoned about from the component tree and confirmed only by a passing smoke test (renders without throwing) + a clean production build, not screenshotted. admin-dashboard's other 6 pages' baselines are unaffected (this diff touches no shared layout).
- **Rate limits (`30/hour`, `10/hour`) were not exercised against Redis** — `utils/rate_limiter.py`'s in-process fallback applies in this dev/test environment (`REDIS_URL` unset); the limiter's mechanics themselves are pre-existing/unchanged, only the two new named limit constants are new.
- **This session did not check the live production `rides` population** — the backfill's own dry-run report (whatever `preview` returns against real production data) was never observed against real Supabase; per the original 2026-08-19 entry, whoever actually runs `commit` in production should trust that run's own live counts, not any number in this log.

## Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — this adds a new endpoint pair/UI card only; the CLI script and every existing route/consumer named in §4 behave identically to before
