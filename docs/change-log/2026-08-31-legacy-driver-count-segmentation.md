# Change Impact & Risk Log — legacy driver-count segmentation

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude Code (session `01JxLGWa57rNuFXF2sgJnZnN`) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin (read-only counting surface; `drivers` data untouched) |
| PR / commit link | branch `claude/driver-count-mismatch-legacy-dgw9xw` — `c5db1a4`, `f5e537c`, `69fa835`, `e9e7a36` |
| Related issue or gap ID | Reported by owner: "~300 drivers imported but it's showing more than 900" |

## 1. Issue / gap identified

The admin dashboard reported **910 drivers** where the business expects **~300**. Reported by the
owner after the legacy import of drivers/rides/users.

Verified read-only against production Supabase (`soavhtdhefowwvforzwb`): the count is accurate and
**nothing is duplicated** — 0 duplicate phone groups, 0 duplicate `user_id` groups. 600 of the 910
rows are abandoned-onboarding shells that the admin UI counted as drivers.

## 2. Root cause

Two separate things, only the second of which is a defect.

**Why the rows exist (by design, not a bug).** `docs/migration/2026-08-27-legacy-driver-blank-name-root-cause.md`
root-caused this against the real export: of 925 rows in the Mongo `drivers.csv`, 588 (63.6%) had a
blank `name`, in perfect bijection with `set_up_profile=false` — someone OTP-verified a phone in the
old app and never completed the profile step. **Zero of them appear in any `bookings.driver_id`.**
The 2026-08-27 decision imported them for completeness with a synthetic
`"Unnamed Legacy Driver {id}"` name, forced to `needs_review`/unverified/offline. That safety
decision is holding: of the 697 Mongo-imported rows, 0 are `active`, 0 verified, 0 online.

**Why the count was wrong (the defect).** The importer stamped exactly the markers needed to tell
the shells apart — `legacy_import_metadata.incomplete_profile_in_source` and the placeholder name —
and **nothing read them**. `admin_get_driver_stats` returned `len(all_drivers)`, and unlike the list
endpoint it accepted no filter that could narrow it.

Composition of the 910:

| Import source | Rows |
|---|---|
| `legacy_mongo_driver_import` | 697 (599 placeholder-named) |
| `legacy_saskatoon_driver_import` | 187 (the real fleet — 136 `active`) |
| Organic Spinr signups | 26 |

Split by the classifier this change introduces: **310 real drivers / 600 shells**.

## 3. Fix / remediation

Segment in the UI. **No data is mutated and no rows are deleted.** One shared classifier, read by
the drivers list, the drivers stats endpoint, and the driver-acceptance analytics endpoint. The
admin sees the real fleet by default and reaches the shells through a dedicated tab.

`stats.total` deliberately keeps its existing meaning (every driver row); two additive keys —
`onboarded_total` and `legacy_incomplete` — break it down and always sum to it.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to admin read paths. Fully enumerated, not assumed.**

Greps performed:
- `stats.total` / `getDriverStats` across `admin-dashboard/src` → exactly three consumers:
  `drivers/page.tsx:653` (tab count), `drivers/page.tsx:880` (header), and
  `_components/driver-stats-cards.tsx:71` (the "Total" tile). All three updated.
- `admin_get_drivers(` across `backend/` → one internal caller, the driver-search handler at
  `routes/admin/drivers.py:709`. Unaffected: the new param is optional and defaults to `None`.
- `fetch_pre_launch_flagged_ids` → `routes/admin/drivers.py` and `routes/admin/rides.py`.
  Only the drivers route is touched; the rides route is untouched.
- `Unnamed Legacy Driver` → two write sites in `driver_import_service.py`, both now using the
  shared constant, plus two existing test assertions on the literal (both still pass — the
  constant produces a byte-identical string).

**The one genuine regression risk, and how it is contained.** `pre_launch` already assigned
`filters["id"]`. Adding a second id-set filter that assigned the same key would have silently
clobbered the first and *widened* the result set — returning rows the admin explicitly filtered
out. Both filters now accumulate into an include/exclude pair written once. All nine
`(pre_launch × onboarding_complete)` combinations are pinned by tests, including the
disjoint-include short-circuit, and both prior `pre_launch` behaviours (empty-set early return,
no-op when nothing is flagged) are preserved and still covered by their original tests.

**Not touched:** no migration, no schema change, no write path. Dispatch, the ride state machine,
money/wallet deltas, and the `lifespan.py` background loops are all untouched. The 600 shells were
already `needs_review`/unverified/offline and therefore already undispatchable — dispatch behaviour
is unchanged **by construction**, not by inspection.

**One behaviour change worth naming:** `/analytics/driver-acceptance` now excludes the shells, so
`total_drivers`, `avg_acceptance_rate` and `low_performer_count` all shift. That is the intended
correction — the shells have never taken an offer, so they were dragging the average toward zero
and inflating the low-performer count with rows that were never dispatchable — but anyone reading
that endpoint's numbers across the deploy boundary will see a step change.

## 5. User-experience effect

- **Internal admin only.** No rider, driver, or corporate-admin surface changes. No copy or
  notification changes.
- Not visible mid-session to anyone using the rider or driver app.
- An admin with the drivers page open will see the header, the "All" tab count and the first stat
  tile drop from 910 to 310 on next load, with "· 600 legacy incomplete" appended and a new
  "Legacy incomplete" tab. The stat tile's label changes from "Total" to "Drivers" so the narrower
  basis is legible on the tile itself.
- The shells remain fully reachable and are not hidden from the database.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/driver_import_service.py` | Added `LEGACY_PLACEHOLDER_NAME_PREFIX`, `INCOMPLETE_PROFILE_KEY`, `is_incomplete_onboarding_row()`, paginated `fetch_incomplete_onboarding_driver_ids()`; both placeholder write sites now use the constant | The module that writes the markers should own reading them, mirroring `pre_launch_flag_service.fetch_pre_launch_flagged_ids`, so reader and writer cannot drift |
| `backend/routes/admin/drivers.py` | New `onboarding_complete` filter on `admin_get_drivers`; refactored the id-set filters to accumulate rather than overwrite; `admin_get_driver_stats` gained `onboarded_total` / `legacy_incomplete` | Make the count segmentable without changing what `total` means |
| `backend/routes/admin/analytics.py` | Excluded shells from the driver-acceptance fetch, after the scan-cap check | So `total_drivers` agrees with the drivers page, and the rate averages stop counting never-dispatchable rows |
| `backend/tests/test_admin_drivers_coverage.py` | New `onboarding_complete` filter tests, filter-interaction matrix, stats-breakdown tests | Pin the clobber regression and the breakdown invariant |
| `backend/tests/test_legacy_mongo_driver_import_service.py` | Classifier + paginated-fetch tests; `range()` added to the local fake Supabase | Cover marker precedence and the >1-page fetch |
| `admin-dashboard/src/lib/api/drivers.ts` | `onboarding_complete` on `getDrivers`; new stat keys on `getDriverStats`'s type | Wire the filter through |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | New tab, count mapping, default filter, header figure | Show the real fleet by default |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-stats-cards.tsx` | "Total" tile → "Drivers", reads `onboarded_total` | Same count, stated basis |

## 7. Before / after

```python
# Before — routes/admin/drivers.py, admin_get_drivers
#   Two id-set filters would each claim the same key; the second silently wins.
if pre_launch is not None:
    flagged_ids = fetch_pre_launch_flagged_ids("drivers")
    if pre_launch:
        if not flagged_ids:
            return []
        filters["id"] = {"$in": list(flagged_ids)}
    elif flagged_ids:
        filters["id"] = {"$nin": list(flagged_ids)}
```

```python
# After — accumulate, then write filters["id"] exactly once.
include_ids: Optional[set] = None   # None = unconstrained, set() = match nothing
exclude_ids: set = set()

if pre_launch is not None:
    flagged_ids = fetch_pre_launch_flagged_ids("drivers")
    if pre_launch:
        include_ids = _restrict_to(flagged_ids)
    else:
        exclude_ids |= flagged_ids

if onboarding_complete is not None:
    incomplete_ids = fetch_incomplete_onboarding_driver_ids()
    if onboarding_complete:
        exclude_ids |= incomplete_ids
    else:
        include_ids = _restrict_to(incomplete_ids)

if include_ids is not None:
    remaining = include_ids - exclude_ids
    if not remaining:
        return []
    filters["id"] = {"$in": list(remaining)}
elif exclude_ids:
    filters["id"] = {"$nin": list(exclude_ids)}
```

```tsx
// Before — drivers/page.tsx: raw row count as the fleet size
description={`${data?.stats?.total ?? 0} drivers ...`}
```

```tsx
// After — real drivers, with the excluded rows kept visible
`${data?.stats?.onboarded_total ?? data?.stats?.total ?? 0} drivers ...`
+ (data?.stats?.legacy_incomplete ? ` · ${data.stats.legacy_incomplete} legacy incomplete` : "")
```

## 8. Rollback plan

**No data-level remediation is possible or needed — this change writes nothing.** No migration, no
column, no row mutation. Production `drivers` rows are byte-identical before and after.

Ordered by blast radius, smallest first:

1. **Frontend-only revert, no backend deploy** — drop the single line
   `opts.onboarding_complete = statusFilter !== "legacy_incomplete";` in `page.tsx`'s `loadDrivers`
   and point the header/tile back at `stats.total`. The list and counts return to today's exact
   behaviour while the backend keys stay in place (they are additive and inert if unread).
2. **Full revert** — `git revert 7e14144 232a79d e9e7a36 69fa835 f5e537c c5db1a4`. Safe at any time:
   `stats.total` never changed meaning, so no consumer is left reading a key that stops existing.

   **Verified by execution, not assertion** (CI auto-labelled this PR `risk:high`, which makes the
   template's "verify the rollback actually works" item apply): the sequence was applied with
   `git revert --no-commit` inside a throwaway `git worktree`. It applied with **zero conflicts**,
   `git diff` against the branch point `9953e60` came back **empty** — a byte-identical restore —
   and a grep for `onboarding_complete` / `is_incomplete_onboarding_row` / `onboarded_total` across
   `backend/routes`, `backend/services` and `admin-dashboard/src` returned nothing. The worktree was
   then discarded; the branch is unmodified.

No feature flag was added. Justification: this is an internal-admin read-only display change with
no user-facing surface and no write path, and step 1 above is already a one-line rollback that
needs no backend deploy — a flag would add a config surface without shortening recovery.

## 9. Verification performed

- [x] **Blast-radius grep performed** — listed in §4 (`stats.total`, `getDriverStats`,
      `admin_get_drivers(`, `fetch_pre_launch_flagged_ids`, `Unnamed Legacy Driver`).
- [x] **Diagnosis verified against production**, read-only `SELECT`s only: source composition,
      duplicate-phone/`user_id` check (0/0), the marker cross-tab, and the 310/600 split.
- [x] **Filter-combination truth table executed** — all nine `(pre_launch × onboarding_complete)`
      pairings plus both empty-set edge cases, run as a standalone script against a faithful copy
      of the resolution block. All pass.
- [x] **Lint/format clean** — `ruff check` and `ruff format --check` on every touched Python file.
      (`routes/admin/drivers.py` reports 4 pre-existing `B904` errors in untouched Stripe-refresh
      handlers at lines 3346–3594; identical count on a clean tree, left alone per the
      surgical-changes rule.)
- [x] **Reviewed against `CLAUDE.md` conventions** — additive over destructive; no silent behaviour
      change to `stats.total`; dual-import pattern preserved in both new import blocks; no money
      arithmetic touched; no PII added to logs (the classifier reads `name` but never logs it).
- [x] **Tests written** for the classifier, the paginated fetch, the filter matrix, and the stats
      breakdown.
- [ ] **Tests not executed locally** — see §9a; CI has since run them, see §9c.
- [x] **Rollback actually executed, not just written** — see §8.
- [ ] Manual repro in staging — not performed.
- [ ] Feature-flagged — deliberately not, justified in §8.

### 9a. What was NOT verified — and why

**No test suite was run, and no production build was run.** The session's network policy blocks
both package registries (`pip install` → "no matching distribution"; `npm ci --offline` →
`ENOTCACHED` on the first uncached tarball), so neither `pytest` nor `npm run build` could be
installed or executed here. Concretely:

- **`pytest` was not run.** The new and existing backend tests are unexecuted. They were instead
  statically checked: AST-parsed, every `svc.<attr>` reference resolved against the service module,
  and every patch target confirmed importable in the route module. That is weaker than running
  them. **CI must be green before this merges.**
- **`npm run build` was not run**, and neither was `tsc --noEmit` or `vitest`. CLAUDE.md's release
  gate requires a real production build for any `admin-dashboard` change; **that gate is
  outstanding, not satisfied.** The TSX changes were reviewed by diff only.
- **The frontend change has no real automated coverage even if the suite is run.** The only test
  touching this page, `src/__tests__/dashboard/pages.smoke.test.tsx:357`, `vi.mock`s
  `driver-stats-cards` out entirely — a stubbed component, which per CLAUDE.md is zero real
  coverage, not partial coverage. No drivers-page test exercises `statusCounts` or the header.
- **No visual-regression coverage exists for this surface.** `admin-dashboard`'s Playwright job has
  zero committed baselines (`ACTION_ITEMS.md` B38), so it skips itself every run. The header, tab
  and stat-tile changes were reasoned about, **not** screenshotted.
- **The production numbers were read, not re-read after the change.** The 310/600 split comes from
  querying the data directly; the assertion that the endpoint will *return* those figures follows
  from the classifier logic and has not been observed end-to-end against a running backend.

### 9c. What CI then found — the §9a gap, closed

Added after PR #4810's first CI run, so this record does not stay stale at "outstanding".

- **`admin-test` passed.** The `admin-dashboard` production-build/typecheck gate §9a listed as
  outstanding is now satisfied.
- **`backend-test` found a real defect**, exactly the risk §9a existed to flag: 38 failed / 13410
  passed (coverage fine at 87.82% vs a 60% floor). 37 shared one cause — the new
  `driver_import_service` imports landed in the `try:` branch of `routes/admin/drivers.py` and
  `routes/admin/analytics.py` but **not** the `except ImportError:` fallback, so in top-level import
  mode the names were unbound. This was a latent runtime `NameError`, not merely a test failure:
  the dual-import pattern exists because both modes are live. The claim in §9's convention checkbox
  that the "dual-import pattern [was] preserved in both new import blocks" was **wrong** — it was
  reasoned, not checked, and is corrected here.
  - Caught by the repo's own `tests/test_dual_import_parity.py`, whose docstring already records the
    same regression class from PR #1757 and PR #1843.
  - Cause was procedural: two `Edit` calls issued in parallel against the *same file*; both reported
    success, only one survived, and the success reports were trusted instead of re-reading the file.
    Ruff is **not** implicated — the exact multi-line import shape survives `ruff format` +
    `ruff check --fix` unchanged when reproduced in isolation.
  - Fixed in `7e14144`, verified by replicating that guard's own `_violations()` AST logic and
    running it over the same file set: exactly the three reported names before, `PASS` across all
    136 guarded files after.
- **One failure was not this change's**: `test_run_migrations_skip_list.py`, which landed on `main`
  at 19:51Z in `a95ed51` (PR #4805) after this branch's base, builds a cwd-relative
  `Path("backend/migrations")` while `ci.yml` sets `working-directory: backend`. Diagnosed with a
  proposed patch in a PR comment; deliberately not carried here.
- **Still not run locally**: `pytest`. CI remains the source of truth for test outcomes.

### 9b. Adjacent finding, deliberately not fixed

`booking_import_service.py:126` filters `CANADA_COUNTRY_CODE = "1"` on both customer and driver,
because the legacy Mongo DB is a shared multi-tenant SaaS database with other-tenant traffic
(`docs/audit/2026-08-14-mongodb-legacy-extract-audit.md` finding 1).
`build_mongo_driver_import_plan` has **no equivalent filter** — an importer asymmetry.

It is not this issue's cause and was left alone: 678 of the 697 imported rows carry a real Canadian
area code (341× `306`, 150× `639` — Saskatchewan) and only 2 fail NANP structure. Worth deciding on
before the next import batch, not worth widening this change for.

## 10. Sign-off

- [x] Rollback plan is concrete and **executed, not just testable** — a one-line frontend revert
      with no backend deploy, plus a full `git revert` of all six commits proven in an isolated
      worktree (§8).
- [x] Blast radius is stated, not assumed — every consumer enumerated by grep in §4.
- [x] No silent behaviour change to an already-shipped flow — `stats.total` keeps its meaning; the
      two visible changes (drivers page figures, driver-acceptance analytics) are described in §5.
- [x] **`admin-dashboard` production build gate: passed in CI** (`admin-test`, §9c).
- [ ] **Backend test gate: not yet green.** CI's first run found a real dual-import defect, fixed in
      `7e14144` (§9c); re-run pending. One unrelated failure is owned by `main` (§9c). Backend tests
      must be green before merge.
