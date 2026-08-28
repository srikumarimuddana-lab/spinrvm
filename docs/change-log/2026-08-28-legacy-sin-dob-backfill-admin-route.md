# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers |
| PR / commit link | (local branch `worktree-agent-aec7899d919d3f3c7`, not yet pushed/opened) |
| Related issue or gap ID | Phase 2 of `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` — sibling of the vehicle-history backfill referenced in §4 |

## 1. Issue / gap identified

The legacy SIN/DOB backfill (`backend/services/driver_import_service.py`'s
`plan_legacy_sin_dob_import` / `apply_legacy_sin_dob_import`, exercised by
`backend/scripts/backfill_legacy_driver_sin_dob.py`) was CLI-only — an
operator needed shell + `SUPABASE_SERVICE_ROLE_KEY` access to run it, with no
admin-dashboard path, no dry-run review UI, and no rate limit on the write.

## 2. Root cause

The backfill was built as a one-time migration CLI tool (2026-08-20, per the
service module's own comments) before the admin dashboard had an equivalent
validate/commit pattern to plug into. `routes/admin/legacy_driver_import.py`
(Phase 1, Mongo driver import) later established that pattern for a
single-CSV upload; this backfill needs the same pattern extended to two
CSVs (`banks.csv` + `drivers.csv`), which nothing had built yet.

## 3. Fix / remediation

Added a thin FastAPI wrapper, `backend/routes/admin/legacy_sin_dob_backfill.py`,
exposing:

- `POST /api/admin/legacy-drivers/sin-dob-backfill/validate` — parses both
  CSVs, calls the existing `plan_legacy_sin_dob_import` (unchanged), returns
  a dry-run report + a signed commit token. No writes.
- `POST /api/admin/legacy-drivers/sin-dob-backfill/commit` — requires that
  token, re-validates, and if clean calls the existing
  `apply_legacy_sin_dob_import` (unchanged) to write.

No business logic was added or changed — this route calls the two existing
service functions exactly as the CLI script already does. The commit token
(reusing `utils/driver_import_token.py` unchanged) is bound to
`sha256(banks_bytes + b"|" + drivers_bytes)` so a file swap between validate
and commit invalidates it, matching the two-file variant asked for.

Added a matching admin-dashboard page
(`admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx`)
with the same validate → review → commit flow as Legacy Driver Import, two
file inputs instead of one.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Grepped for every other caller of the two
service functions and the shared token utility this route reuses:

- `plan_legacy_sin_dob_import` / `apply_legacy_sin_dob_import` — only two
  callers repo-wide: `backend/scripts/backfill_legacy_driver_sin_dob.py`
  (the pre-existing CLI, untouched) and this new route. A third file,
  `services/booking_import_service.py`, only *mentions*
  `apply_legacy_sin_dob_import` in a docstring comparison (it mirrors the
  same `.is_(col, "null")` race-guard pattern for its own, unrelated write)
  — not a call site.
- `utils/driver_import_token.py` (`sign_driver_import_token` /
  `verify_driver_import_token`) — reused unchanged. The only other caller is
  `routes/admin/driver_import.py`. The function is pure/stateless (every
  token is scoped to the caller's own `batch`/`csv_sha256`/`admin_id`
  triple), so a second caller cannot cross-contaminate the first — a token
  minted by this new route can never verify against `driver_import.py`'s
  requests or vice versa (different `csv_sha256` inputs by construction).
- Consumers of the `legacy_import_metadata.legacy_mongo_banks_sin_dob_import`
  marker that `apply_legacy_sin_dob_import` writes (`sin_source()` /
  `dob_source()` in the same service module): read by
  `routes/admin/drivers.py` (driver detail's `sin_source`/`dob_source`
  fields) and `routes/admin/compliance.py` (`sin_source` in a compliance
  report row). Both already treat this marker as "may have been written by
  a batch run of the SIN/DOB backfill" — since the write shape didn't
  change, these consumers behave identically regardless of whether the
  batch was run via CLI or this new admin route.
- No other `drivers.sin` / `drivers.date_of_birth` writer was touched.
  `routes/admin/tax_id_import.py`'s bulk SIN import and a driver's own
  self-entry (`routes/drivers/profile.py`) are separate write paths;
  `apply_legacy_sin_dob_import`'s pre-existing `.is_(<col>, "null")` guard
  (unchanged by this PR) is exactly what prevents this backfill from ever
  clobbering either of those.
- New shared-file edits are additive only: one `include_router()` line +
  import in `backend/routes/admin/__init__.py`, one new named limit
  (`legacy_sin_dob_backfill_commit_limit`) in `backend/utils/rate_limiter.py`,
  one new exported section in `admin-dashboard/src/lib/api/imports.ts` +
  matching barrel re-exports in `admin-dashboard/src/lib/api.ts`, and one new
  `describe` block in the dashboard smoke-test file. None of these touch an
  existing route, limit, export, or test.

**Known gap in my own branch state (not a risk introduced by this
change, but worth flagging):** this worktree's branch does not yet have
Phase 1 (`routes/admin/legacy_driver_import.py`, the Mongo driver import) —
it exists in the reference checkout and a sibling worktree but not here, so
`admin/__init__.py`'s `legacy_driver_import_router` line the task description
pointed at as a mount anchor does not exist in this branch. I mounted the new
router next to `driver_import_router` instead (same `require_module("drivers")`
posture) and linked the new page to `/dashboard/drivers/legacy-import`, which
will 404 until the sibling Phase-1 track merges. This is expected to resolve
itself on merge; flagging it explicitly rather than silently working around
it.

## 5. User-experience effect

Internal-admin-facing only (super_admin/admin with the `drivers` module
grant). No rider, driver, or corporate-admin-facing surface changes. Not
visible mid-session to anyone outside the admin dashboard — this is a new
page, not a change to an existing one.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/legacy_sin_dob_backfill.py` | New file — validate/commit HTTP endpoints wrapping the existing service functions | Give the CLI-only backfill an admin-dashboard path |
| `backend/routes/admin/__init__.py` | +1 import, +1 `include_router()` under `require_module("drivers")` | Mount the new router |
| `backend/utils/rate_limiter.py` | +1 named limit, `legacy_sin_dob_backfill_commit_limit = default_limiter.limit("10/hour")` | Rate-limit the write endpoint, same posture as the sibling import routes |
| `backend/tests/test_admin_legacy_sin_dob_backfill.py` | New file — 9 endpoint tests | Cover validate/commit, PII-free reports, never-clobber, token binding, auth gate, row limit |
| `admin-dashboard/src/lib/api/imports.ts` | New "Legacy SIN/DOB Backfill" section: types + `adminValidateSinDobBackfill`/`adminCommitSinDobBackfill` | API client for the new endpoints |
| `admin-dashboard/src/lib/api.ts` | Appended the two new functions + six new types to the existing `./api/imports` re-export lists | Keep the barrel import surface complete |
| `admin-dashboard/src/app/dashboard/drivers/legacy-sin-dob-backfill/page.tsx` | New file — validate → review → commit UI, two file inputs | Admin-dashboard UI for the backfill |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | +1 `describe` block for the new page | Smoke-test coverage matching every other dashboard page |
| `docs/change-log/2026-08-28-legacy-sin-dob-backfill-admin-route.md` | New file — this log | Required for a live-tested surface (`drivers`, encrypted PII columns) per CLAUDE.md |

## 7. Before / after

Pure additive — no existing endpoint, function, or exported symbol changed
behavior. No before/after snippet applies.

## 8. Rollback plan

No migration, no schema change, no feature flag needed — this is two new
HTTP endpoints plus a new admin-dashboard page, reachable only by an admin
who deliberately navigates to and submits the new page. To roll back:

- **Code-level**: revert this commit (or comment out the two
  `include_router` line additions in `admin/__init__.py`) and redeploy — the
  endpoints stop being reachable; nothing else references them.
- **Data already applied via a commit through this route**: identical
  remediation to the CLI's own rollback plan (unchanged — see
  `backend/scripts/backfill_legacy_driver_sin_dob.py`'s docstring), since the
  write path is the exact same `apply_legacy_sin_dob_import` call: for each
  updated driver id (visible in the audit log entry's `updated` count and,
  per-row, in server logs from `apply_legacy_sin_dob_import`), null
  `sin`/`sin_last4`/`sin_collected_at`/`date_of_birth` and remove the
  `legacy_mongo_banks_sin_dob_import` key from `legacy_import_metadata`.
  There is no cascading state (no payout, no Stripe call, no ride-state
  change) triggered by this write, so nothing else needs to be undone.

## 9. Verification performed

- [x] Automated tests run:
  - Backend: `cd backend && python3 -m pytest tests/test_admin_legacy_sin_dob_backfill.py --no-cov -q` → **9 passed**.
  - Backend lint: `ruff check` and `ruff format --check` on the 4 touched/new backend files → **all clean**.
  - Admin-dashboard: `node_modules/.bin/vitest run src/__tests__/dashboard/pages.smoke.test.tsx` → **25 passed** (including the new page's block); full suite `node_modules/.bin/vitest run` → **37 files / 368 tests passed**.
  - Admin-dashboard type check: `npx tsc --noEmit` → clean.
  - **Admin-dashboard real production build**: `npm run build` (Next.js/Turbopack) → **succeeded**, `Compiled successfully in 37.2s`, new route `/dashboard/drivers/legacy-sin-dob-backfill` listed in the route summary. This is the actual `next build`, not `tsc --noEmit` alone or a dev server.
  - `npx eslint` on the new/touched frontend files → 0 problems on my new files; the 5 pre-existing warnings in the smoke-test file are all on lines outside my new `describe` block (unrelated mock setup).
- [x] Manual repro steps followed in staging — N/A, no staging environment reachable from this session; see "not verified" below.
- [x] Blast-radius grep performed — see §4 above (exact patterns: `plan_legacy_sin_dob_import|apply_legacy_sin_dob_import`, `driver_import_token`, `sin_source\(|dob_source\(`).
- [x] Reviewed against relevant CLAUDE.md conventions — PIPEDA (never log/print/return raw SIN or DOB; verified by asserting `VALID_SIN not in resp.text` and the DOB substring not in `resp.text` in two tests), dual-import pattern, `require_module` gating, rate-limit-on-write pattern, Change Impact Log itself.
- [ ] Feature-flagged — not applicable/justified: this is a new, opt-in admin tool behind `require_module("drivers")` + `super_admin`/module-grant auth, not a change to an existing user-visible flow; nothing defaults it on for anyone.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow — this adds new endpoints/page only; the CLI script and every existing route/consumer named in §4 behave identically to before

## What was NOT verified

- **Not tested against live Supabase** — only the in-memory fake `_FakeSupabase` harness (mirroring the existing `test_admin_legacy_driver_import.py`/`test_admin_tax_id_import.py` pattern) and mocked API responses in the frontend test suite. No real Postgres/PostgREST round-trip, no real Vault `encrypt_pii` RPC call, no staging deploy.
- **Not manually exercised end-to-end in a running app** — no `python3 -m backend.server` + dashboard dev server session was started to click through Validate → Review → Commit against a live backend; verification is automated tests + a real production build only.
- **The `/dashboard/drivers/legacy-import` link on the new page will 404 until the sibling Phase-1 track's `legacy-import` page merges into this branch** — see the "Known gap in my own branch state" note in §4. Not a regression I introduced; flagging so it isn't rediscovered as a surprise post-merge.
- **No visual/screenshot check** — admin-dashboard has no active baseline-seeded visual-regression coverage (CLAUDE.md's standing note on `e2e/visual-regression.spec.ts`/B38), so the new page's actual rendered appearance was reasoned about from the component tree and confirmed only by "renders without throwing" (smoke test) + a clean production build, not screenshotted.
- **Rate limit (`10/hour`) was not exercised against Redis** — `utils/rate_limiter.py`'s in-process fallback applies in this dev/test environment (`REDIS_URL` unset); the limiter's mechanics themselves are pre-existing/unchanged, only the new named limit constant is new.
