# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code session (spinr migration work) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | drivers |
| PR / commit link | #4633 (`claude/migration-batch-readiness-wicr1d`) |
| Related issue or gap ID | `docs/runbooks/legacy-migration-playbook.md` item #11 |

## 1. Issue / gap identified

`routes/admin/legacy_driver_import.py` (yesterday's work) makes Phase 1's legacy Mongo driver
importer callable via HTTP, but no admin-dashboard page called it — the only way to reach it was
a direct API client. Flagged as a remaining subtask.

## 2. Root cause

Not a bug — explicitly deferred subtask, picked up this session.

## 3. Fix / remediation

New dedicated page, `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx`, mirroring
`drivers/import/page.tsx`'s (the existing Saskatoon-CSV importer's) validate → review → commit
flow and validation-token handling exactly, adapted for this importer's different response shape
(`new_users`/`new_drivers`/`linked_accounts`/`enriched_drivers` instead of
`users`/`drivers`/`updated`) and a downloadable CSV template using the raw Mongo export's own
column names (`_id`, `name`, `phone`, …) rather than the bespoke Saskatoon sheet's. New API client
types/functions in `src/lib/api/imports.ts` (`LegacyDriverImportReport`, `adminValidateLegacyDriverImport`,
`adminCommitLegacyDriverImport`, …), re-exported from the `src/lib/api.ts` barrel like every other
import client. Cross-linked from the two existing sibling pages (`drivers/import` and
`bulk-operations`) so an operator on the wrong page finds the right one, matching those pages'
existing cross-link convention.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, purely additive.** One new page, one new `_components`-equivalent
  API module addition (new exports only, nothing existing changed in `imports.ts`), two
  one-line cross-link additions to existing pages' description text. Grepped for other
  consumers of `src/lib/api/imports.ts`'s existing exports before editing it — confirmed the
  new block was inserted between existing sections without touching any of them, and the two
  barrel re-export lists in `api.ts` had the new names appended, not interleaved into existing
  ones.
- No existing page, component, or test references anything this change touches, other than the
  two cross-link additions (both additive text/link insertions, not behavior changes).
- This page can now trigger the same production writes `legacy_driver_import.py`'s admin route
  already allowed — no new risk category, same four gates (router auth, `require_module("drivers")`,
  validate-token, rate limit) already documented in yesterday's Change Impact Log for that route.

## 5. User-experience effect

Admin-facing only: an operator with `drivers` module access can now run this import from the
dashboard instead of a raw API client. No rider/driver-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx` | New — validate/commit UI page | The admin-dashboard execution path this Change Impact Log covers |
| `admin-dashboard/src/lib/api/imports.ts` | New `LegacyDriverImport*` types + `adminValidateLegacyDriverImport`/`adminCommitLegacyDriverImport` | API client for the new page |
| `admin-dashboard/src/lib/api.ts` | New names added to the two `imports.ts` re-export lists | Barrel re-export convention every other import client already follows |
| `admin-dashboard/src/app/dashboard/drivers/import/page.tsx` | One-sentence cross-link to the new page | Discoverability — an operator on the Saskatoon-CSV page needs to find the Mongo-export one |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | One-sentence cross-link to the new page | Same reason, from the hub page that already links the Saskatoon importer |
| `admin-dashboard/src/__tests__/dashboard/pages.smoke.test.tsx` | New smoke-test block for `/dashboard/drivers/legacy-import` | Matches the existing manually-maintained page-coverage list; every other dashboard page has one |

## 7. Before / after

Pure addition — no existing behavior-changing diff. `drivers/import/page.tsx` and
`bulk-operations/page.tsx` each get one new sentence; nothing about their existing behavior
changes.

## 8. Rollback plan

No feature flag exists or is needed — this page has never been used against production (no
prior traffic to it, brand new route). Reverting the commit removes the page and its cross-links
entirely with no data-level cleanup required; the underlying backend route and its own rollback
plan (documented in `docs/change-log/2026-08-27-legacy-driver-admin-route.md` §8) are unaffected
either way.

## 9. Verification performed

- [x] Type-check: `npx tsc --noEmit -p .` — clean, zero errors.
- [x] Lint: `npx eslint` on every changed/new file — zero errors (six pre-existing warnings in
      untouched lines of files this change also touches, none introduced by this change).
- [x] **Real production build run**: `npm run build` — succeeded, exit 0, confirmed
      `/dashboard/drivers/legacy-import` in the generated route list. Not just a dev server or
      `tsc --noEmit` — the actual production build, per CLAUDE.md's explicit requirement.
- [x] Automated tests: `npx vitest run src/__tests__/dashboard/pages.smoke.test.tsx` — 25/25 pass
      (24 existing + 1 new), zero collateral breakage.
- [x] Blast-radius grep performed: confirmed no other file references the new API exports or
      the new route path before this change existed.

**Not verified:** no manual click-through in a real browser was performed (no live backend to
validate/commit against from this session — same constraint as every other Phase 1 piece today).
No visual/screenshot review — this repo has no active visual-regression tooling for
admin-dashboard (CLAUDE.md's own documented standing gap), so the page's visual correctness was
reasoned about (mirrors `drivers/import/page.tsx`'s exact JSX structure/classes) rather than
screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete: no live traffic exists yet, so revert-the-commit is complete.
- [x] Blast radius stated: grepped, not assumed — isolated/additive.
- [x] No silent behavior change to an already-shipped flow — this is a new page; the two
      existing pages it touches only gain one additional sentence each, nothing removed or
      altered.
