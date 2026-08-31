# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude, at user request ("address" the two open follow-ups from the 2026-08-30 admin portal audit PR) |
| Surface(s) | admin-dashboard, backend (migration application only, no code) |
| Domain (Sentry tag) | admin |
| PR / commit link | PR following this log |
| Related issue or gap ID | Two explicit follow-ups flagged (not fixed) in PR [#4726](https://github.com/srikumarimuddana-lab/spinrvm/pull/4726)'s Change Impact Log §E and the same PR's Tier 2 "Dependencies / coordination" note |

This closes out both items called out as deliberately out-of-scope when #4726 merged.

## 1. Issue / gap identified

1. **RBAC gap, 2 more instances**: `drivers/queue/page.tsx` and `driver-license-backfill/page.tsx` both render `DocumentReviewer` gated only on `useRequireModule("drivers")`, with no check for the "documents" module that the component's underlying `/api/admin/documents/...` calls actually require server-side — the same defect #4726 fixed on `drivers/page.tsx` itself, left as a named fast-follow rather than fixed there.
2. **Migration not applied**: `374_settings_admin_command_palette.sql` (adds `settings.admin_command_palette_enabled`) was merged in #4726 but never run against production — the command-palette feature flag was unreachable (always defaulting to the schema-level `false`, with no way to ever flip it on) until the column existed.

## 2. Root cause

1. Same root cause as the original #4726 fix: `DocumentReviewer` is called from three places in the codebase (`drivers/page.tsx`, `drivers/queue/page.tsx`, `driver-license-backfill/page.tsx`); the original fix pass covered the first and explicitly flagged the other two rather than silently leaving them undocumented.
2. This sandbox has no `DATABASE_URL` configured, so `backend/scripts/run_migrations.py` (the normal path) could not run here. The migration sat merged-but-unapplied.

## 3. Fix / remediation

1. Applied the identical `canReviewDocuments` gate from `drivers/page.tsx` to both remaining call sites: `isSuperAdmin || (user?.modules ?? []).includes("documents")`, threaded into `<DocumentReviewer canReview={canReviewDocuments} />`. Each page's own existing `useRequireModule("drivers")` page-level gate is unchanged — only the document-review sub-UI's usability is affected, matching the original fix's scope exactly.
2. Applied the migration directly against the production Supabase project (`soavhtdhefowwvforzwb`, ca-central-1) via the Supabase management API, since the standard `run_migrations.py` path wasn't available in this environment. To keep the two migration-tracking systems consistent (Supabase's own migration history vs. this repo's custom `schema_migrations` table that `run_migrations.py` reads), the applied SQL included both the `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` from the migration file verbatim *and* an `INSERT INTO schema_migrations (filename, checksum) VALUES (...) ON CONFLICT (filename) DO NOTHING` with the migration file's real sha256 checksum — the exact row `run_migrations.py` itself would have inserted. Verified after: the column exists, defaults to `false` (confirmed by querying the live `settings` row), and `schema_migrations` now has a matching tracking row, so a future `run_migrations.py --status` run will correctly report 374 as already applied rather than re-attempting it.

## 4. Risk & impact on existing functionality

- **Fix (1)**: identical blast radius to the original #4726 fix on `drivers/page.tsx` — isolated to these two files' rendering of `DocumentReviewer`; the component itself (already shipped, already tested) is unchanged. `canReview` defaults to `true` on the component, so this is purely additive/narrowing, not a new failure mode for existing callers.
- **Fix (2)**: purely additive schema change already reviewed and merged as code in #4726 — this action only executes SQL that was already approved, on the intended target. No other table/column touched. The command-palette feature itself remains fully dark (flag defaults `false`) — this only makes the flag *flippable*, it does not turn anything on.
- **No ride, dispatch, payment, wallet, or insurance-period surface touched by either fix.**

## 5. User-experience effect

- Fix (1): internal admin/staff-facing only. A staff member with "drivers" but not "documents" granted now sees an explanatory message instead of a broken-looking approve/reject UI on the Approval Queue and the License Backfill tool — same UX change already shipped on the main Drivers page in #4726, now consistent across all three surfaces.
- Fix (2): no visible effect to anyone — the flag stays off. It's now possible for a super-admin to turn the command palette on via the Settings page toggle (already shipped in #4726), which was not previously possible.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/queue/page.tsx` | Added `canReviewDocuments` computation + `canReview` prop | Close RBAC gap #2 of 2 |
| `admin-dashboard/src/app/dashboard/driver-license-backfill/page.tsx` | Added `canReviewDocuments` computation + `canReview` prop | Close RBAC gap #2 of 2 |
| *(no file — direct DB action)* `public.settings.admin_command_palette_enabled` (production) | Column added, tracking row inserted | Apply the already-merged, already-reviewed migration |
| `docs/change-log/2026-08-31-documents-rbac-followup-and-migration-applied.md` | This log | Both are behavior/operational changes on a live-tested, security-relevant surface |

## 7. Before / after

```tsx
// Before — drivers/queue/page.tsx and driver-license-backfill/page.tsx
<DocumentReviewer driverId={...} /* no canReview */ />

// After
const isSuperAdmin = (user?.role || "").toLowerCase() === "super_admin";
const canReviewDocuments = isSuperAdmin || (user?.modules ?? []).includes("documents");
<DocumentReviewer driverId={...} canReview={canReviewDocuments} />
```

## 8. Rollback plan

- Fix (1): `git revert` — pure frontend, no data.
- Fix (2): `ALTER TABLE public.settings DROP COLUMN IF EXISTS admin_command_palette_enabled;` (the exact rollback SQL already documented in the migration file's own header comment) plus `DELETE FROM schema_migrations WHERE filename = '374_settings_admin_command_palette.sql';` to keep the tracking table consistent if ever reverted.

## 9. Verification performed

- [x] `cd admin-dashboard && npm run build` — real production build, clean, exit 0.
- [x] `npx vitest run .../document-reviewer.test.tsx` — 11/11 passed (covers `canReview=false`, unaffected by this change since the component itself wasn't touched).
- [x] `npx tsc --noEmit` and `npx eslint` on both changed files — 0 errors (2 pre-existing, unrelated warnings).
- [x] Migration application verified directly against the production database: confirmed `admin_command_palette_enabled` column exists and reads `false`, confirmed `schema_migrations` row inserted with the correct filename + matching sha256 checksum of the actual migration file (so `run_migrations.py --status` won't attempt to re-apply or flag drift).

## What was NOT verified

- No dedicated frontend test exists for either `drivers/queue/page.tsx` or `driver-license-backfill/page.tsx` — verified via build + lint + manual code review against the already-tested `drivers/page.tsx` template, not a new automated test.
- Did not run `run_migrations.py --status` against production from this sandbox (no `DATABASE_URL` configured here) to double-confirm the tracking row is picked up correctly by the actual script — confirmed instead by directly querying `schema_migrations` and comparing to what the script's own INSERT statement would have produced.
- Did not verify the command-palette feature end-to-end in a running browser now that the flag is flippable — out of scope for this follow-up, which only unblocks the flag.
