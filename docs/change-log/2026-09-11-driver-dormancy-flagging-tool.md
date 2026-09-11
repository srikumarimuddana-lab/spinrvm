# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | ittalenthire.ca@gmail.com (via Claude Code) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | drivers, admin |
| PR / commit link | (branch `claude/driver-dormancy-flagging`) |
| Related issue or gap ID | User request: devise and ship a technique to identify unwanted (legacy, dormant, redundant, stale) driver data in production, working from an existing session-level research pass into the current `drivers` schema and existing legacy-data tooling |

## 1. Issue / gap identified

Production `drivers` data has no way to identify drivers who have gone idle — created an account (or once drove) and never came back. `went_online_at`/`went_offline_at`/`last_status_changed_at` columns already exist (migrations 97, 42) but nothing reads them for classification, and there is no admin filter for "show me inactive drivers" despite the admin Drivers list already having equivalent filters for two adjacent concerns (legacy-imported, pre-launch-flagged).

## 2. Root cause

This gap was never a bug — dormancy classification was simply never built. Research (an Explore-agent pass over the schema, migrations, and every existing legacy-data tool) confirmed the two *adjacent* data-quality problems the user also named are **already solved**, so this change deliberately does not touch them:
- **"Legacy/unnamed" drivers**: already fully classified by `driver_import_service.is_incomplete_onboarding_row()` and excluded from the default admin Drivers view (`onboarding_complete` filter). Production: 600 of 910 driver rows are abandoned-onboarding shells (`status='needs_review'`), 310 are real drivers.
- **"Redundant" (duplicate) drivers**: confirmed **zero** duplicate-phone and zero duplicate-`user_id` groups in production (2026-08-31 change-log), and structurally prevented going forward by `UNIQUE(phone)`/`UNIQUE(user_id)` constraints (migration 31). Nothing to build here.

"Dormant" was the one real, unbuilt gap.

## 3. Fix / remediation

Added a new, additive-only classification tool — `driver_dormancy_service.py` — following the exact pattern already established by `pre_launch_flag_service.py` (dry-run plan → confirm-phrase commit → `legacy_import_metadata` JSONB flag, never a delete/deactivate/suspend):

- **Two tiers**, both computed from the later of `went_online_at`/`went_offline_at` (falling back to `created_at` only if the driver never toggled at all — mirrors the documented `intent_online()` derivation on those columns):
  - `dormant` (≥ 90 days idle) — the soft, informational signal. Chosen as a grace period comfortably past any legitimate "still onboarding" delay (SGI approval, vehicle inspection).
  - `long_dormant` (≥ 365 days idle) — the escalation tier, deliberately lined up with Spinr's own annual document-renewal cycle (CRC/Vulnerable Sector Check renewal; license/insurance/inspection/background-check expiry re-checked on every `go_online` call) rather than an arbitrary number — a driver idle a full year needs full re-verification to return regardless of this tool.
- **Never a candidate**: a currently-online driver (query-layer exclusion), or a driver whose inactivity is already explained by tracked state (`is_suspended`, `status` in `suspended`/`banned`/`rejected`/`needs_review`). The `needs_review` exclusion specifically prevents double-counting the already-tracked abandoned-onboarding population under a second label.
- Two new endpoints (`POST /api/admin/drivers/dormancy/{preview,commit}`), `super_admin`-gated, rate-limited identically to the pre-launch tool.
- A new bulk-operations UI tool ("Driver Dormancy Flagging") in a new **Phase 7 "Ongoing data hygiene"** section — deliberately *not* added to the existing Phase 6 "Final review" migration checklist, since dormancy accrues continuously for any driver (legacy-imported or organic), unlike that phase's one-time migration-cleanup tools.
- A new `dormant`/`dormancy_tier` filter on the admin Drivers list, mirroring the existing `pre_launch` filter's `$in`/`$nin`-against-`id` compilation exactly.

This is a **classification/reporting tool only** — no purge/delete logic was built. Research confirmed actual deletion of driver rows is regulatory-constrained (7-year driver/vehicle-linkage retention under the SK Transportation Act; `driver_insurance_periods` is append-only and never deleted; the existing DSAR purge path `purge_pii_retention()` Step H already has a known, tracked gap — ACTION_ITEMS A37 — and a prior incident, ACTION_ITEMS A35, where an ad-hoc deletion script bypassed eligibility checks entirely). A proactive (non-DSAR) purge decision was explicitly scoped **out** of this change and left for a separate policy discussion, per the plan agreed with the user before implementation.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New service module, new route file, new frontend component — nothing existing was modified except:
  - `routes/admin/drivers.py`: added two optional query params and ~10 lines of filter logic to `admin_get_drivers`, in the exact style of the adjacent `pre_launch`/`onboarding_complete`/`legacy_review` filters it sits beside. Grepped and confirmed this is the only reader/writer of the `GET /drivers` filter-building logic; the new params are additive (default `None` = no filter = unchanged behavior for every existing caller, same contract the pre-launch filter already established).
  - `admin-dashboard/.../drivers/page.tsx` + `driver-list-table.tsx`: added one new `useState` and wired it into two existing `useEffect`/`useCallback` dependency arrays (matching every other filter already in those arrays) and the existing "Clear filters" handler. No existing filter's behavior changed.
  - `admin-dashboard/.../bulk-operations/page.tsx`: added one import and one new `PhaseSection` (phase 7) at the end of the file — no existing phase section touched.
  - `src/__tests__/dashboard/pages.smoke.test.tsx`: added `Moon` to the lucide-react icon allowlist (the smoke test's `vi.mock` requires every icon used anywhere on a rendered page to be explicitly listed) — the only test-infrastructure file touched, purely additive.
- **What else reads/writes `legacy_import_metadata`**: this is a JSONB column with multiple existing additive writers (`pre_launch_flag_service`, `driver_import_service`, `legacy_id_crosswalk_service`, `migration_driver_repair_service`). The new `dormant`/`dormancy_tier`/`dormancy_flag` keys are namespaced and never collide with any existing key; the read-merge-write + whole-column optimistic-concurrency guard (copied verbatim from `pre_launch_flag_service._apply_flag_to_row`) protects against a race with any of those other writers.
- **Could this regress a flow that currently works?** No ride/dispatch/payment code path reads `legacy_import_metadata.dormant`. Go-online eligibility (`routes/drivers/status.py`) is entirely unaffected — verified by reading that route in the research pass; it checks `status`/`is_suspended`/document expiry only, never this new key.
- **Background loops**: none of the 41 startup loops in `core/lifespan.py` read or write `legacy_import_metadata` on drivers; this feature has no interaction with any of them.

## 5. User-experience effect

Internal-admin-facing only (super-admin-only tool + a filter on an already admin-only list page). No rider, driver, or corporate-admin-facing change of any kind — a driver is never notified, suspended, or otherwise affected by being flagged. Not visible mid-session to anyone since it's a backend classification field, not a user-facing state.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_dormancy_service.py` | New file: read-only classifier + additive flag-writer | Core dormancy logic |
| `backend/tests/test_driver_dormancy_service.py` | New file: 18 unit tests | Idle-time computation, tier boundaries, exclusions, apply/idempotency |
| `backend/routes/admin/driver_dormancy.py` | New file: preview/commit endpoints | Admin HTTP surface |
| `backend/tests/test_admin_driver_dormancy.py` | New file: 8 endpoint tests | HTTP-layer coverage (auth boundary, dry-run, commit) |
| `backend/routes/admin/__init__.py` | Registered new router, `super_admin`-gated | Mount the new endpoints |
| `backend/utils/rate_limiter.py` | Added preview/commit rate limits | Same generous-headroom convention as every sibling tool |
| `backend/routes/admin/drivers.py` | Added `dormant`/`dormancy_tier` query params + filter logic to `GET /drivers` | Admin list filtering |
| `backend/tests/test_admin_drivers_coverage.py` | Added 6 tests for the new filter | Mirrors existing `pre_launch` filter test coverage |
| `admin-dashboard/src/lib/api/imports.ts` | Added dormancy types + `adminPreviewDriverDormancy`/`adminCommitDriverDormancy` | Frontend API client |
| `admin-dashboard/src/lib/api.ts` | Re-exported new symbols | Barrel file convention |
| `admin-dashboard/src/lib/api/drivers.ts` | Added `dormant`/`dormancy_tier` to `getDrivers` options | Frontend API client |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverDormancyFlag.tsx` | New file: preview/confirm/commit tool UI | Admin tool surface |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Added Phase 7 section + import | Mounts the new tool |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | Added `dormancyFilter` state, wired into query-building and effect deps | Drivers list filter |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-list-table.tsx` | Added dormancy filter `Select` control + clear-filters handling | Drivers list filter UI |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | Added `Moon` to the lucide-react mock allowlist | Required by the new icon usage; smoke test would otherwise fail |

## 7. Before / after

Purely additive across every file — no existing behavior changed for any caller that doesn't pass the new params. Representative snippet (backend filter, matching the established `pre_launch` pattern):

```python
# Before -- no dormancy concept existed
if pre_launch is not None:
    flagged_ids = fetch_pre_launch_flagged_ids("drivers")
    ...

# After -- added alongside, same shape
if dormant is not None:
    dormant_ids = fetch_dormancy_flagged_ids(dormancy_tier if dormant else None)
    if dormant:
        include_ids = _restrict_to(dormant_ids)
    else:
        exclude_ids |= dormant_ids
```

## 8. Rollback plan

`git-revert-safe`. Purely additive: every new query param defaults to `None`/omitted (no behavior change for any existing caller), the new JSONB keys (`dormant`, `dormancy_tier`, `dormancy_flag`) are never read by anything except this tool's own preview/filter logic, and no existing row's other fields are touched. If a driver is flagged in error, the flag can also be unset directly (`UPDATE drivers SET legacy_import_metadata = legacy_import_metadata - 'dormant' - 'dormancy_tier' - 'dormancy_flag' WHERE id = ...`) without any code change — no data-level remediation plan is needed since nothing downstream depends on the flag yet (it is purely a reporting/filter signal, not wired into KPIs, dispatch, or any automated action).

## 9. Verification performed

- [x] Automated tests run: backend — `pytest tests/test_driver_dormancy_service.py tests/test_admin_driver_dormancy.py tests/test_admin_drivers_coverage.py tests/test_pre_launch_flag_service.py tests/test_admin_pre_launch_flag.py` → all pass (18 + 8 + 173 + existing pre-launch suite, zero regressions). Frontend — `npx vitest run` → 640/640 passed (65→66 files).
- [x] **Real production build run**: `npm run build` (admin-dashboard) completed with no errors — not just `tsc --noEmit`/dev server (also ran `tsc --noEmit` separately, clean).
- [x] Blast-radius grep performed: confirmed `admin_get_drivers` is the only reader of these filter params; confirmed no ride/dispatch/payment/go-online-eligibility code path reads `legacy_import_metadata.dormant`; confirmed the JSONB key names don't collide with any of the 4 other existing writers to that column.
- [x] Reviewed against `CLAUDE.md` conventions: additive-over-destructive (rule honored explicitly — no purge logic built); dual-import pattern followed in all new backend files; no PII in any new field (idle-day counts and boolean flags only); Saskatchewan/PIPEDA retention rules were the deciding factor in scoping this to classification-only, not deletion.
- [x] Backend and frontend linters clean (`ruff check`/`ruff format`, `eslint`) on every changed/new file — pre-existing, unrelated findings elsewhere in `routes/admin/drivers.py` (4 pre-existing `B904` warnings, far from this diff) were left untouched per the surgical-changes principle.

## What was NOT verified

- Not run against real production Supabase data — only against mocked `supabase` clients in both new test files (following this repo's own convention: unit tests mock, real-DB verification happens via a manual preview call in staging/prod by an operator, which has not been done as part of this change).
- No manual click-through of the new admin UI in a running instance — reasoned from the existing `PreLaunchDataFlag` component's own shipped UX pattern (verified in production), which this component copies almost verbatim.
- No production dormancy count has been observed yet — the actual number of drivers that would be flagged 90+/365+ days idle is unknown until an admin runs Preview in the real environment. This is by design (dry-run-first), not an oversight.
- No decision was made (or asked for) on an eventual proactive-purge policy for long-dormant/legacy-incomplete rows — that remains a separate, explicitly out-of-scope business/legal question per the plan agreed before implementation.

## Sign-off

- [x] Rollback plan is concrete and testable (unset three JSONB keys; no other cleanup needed)
- [x] Blast radius is stated, not assumed (grep-verified: no other reader of the new filter params or JSONB keys)
- [x] No silent behavior change to an already-shipped flow — every new param defaults to "no filter," matching every existing caller's current behavior exactly
