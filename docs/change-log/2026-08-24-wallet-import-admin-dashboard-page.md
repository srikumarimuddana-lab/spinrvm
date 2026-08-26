# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-24 |
| Author | Claude Code session (see PR for session link) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this branch) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-24-wallet-import-admin-route-wired.md`, which noted "No admin-dashboard frontend page exists for this route yet" |

## 1. Issue / gap identified

The wallet-import admin route (`POST /api/admin/wallets/import/validate`,
`POST /api/admin/wallets/import/commit`) was reachable only via direct API
call (curl/Postman). No admin-dashboard UI existed for it. The user
explicitly asked to build the frontend page.

## 2. Root cause

Not a bug — the route was wired in a prior, separate change and the UI was
deliberately deferred to a follow-up step at the time.

## 3. Fix / remediation

- Added a Wallet-Balance Import API-client section to
  `src/lib/api/imports.ts`: types (`WalletImportReportItem`,
  `WalletImportCounts`, `WalletImportReport`, `WalletImportDeltaResult`,
  `WalletImportCommitResult`, `WalletImportFiles`, `WalletImportOptions`),
  a `walletImportFormData()` builder, and `adminValidateWalletImport()` /
  `adminCommitWalletImport()` functions — same shape as the existing
  booking-import client functions in the same file.
- Re-exported the new functions and types from `src/lib/api.ts`, next to
  the existing booking-import exports.
- New component `_components/LegacyWalletImport.tsx`, structurally
  mirroring `_components/LegacyBookingImport.tsx`: 3-file upload
  (wallets/customers/drivers CSVs, no service-area/vehicle-type fields
  since this importer has none), a validate → dry-run report → type-`APPLY`
  → commit flow, an issue table for row-level validation errors, and a
  stats grid (target rows, matched rider/driver rows, skipped-unmatched,
  sum add/deduct/net, skipped-zero-amount).
- Two explicit warning banners on the new page (not present on the booking
  importer): one that this importer applies direct wallet credits/debits
  with no offsetting entry (unlike the booking importer's ride-driven
  model), and one restating the still-open CSV column-name assumption from
  the backend Change Impact Logs, so an operator reads it before running a
  real batch.
- Wired `<LegacyWalletImport />` into
  `src/app/dashboard/bulk-operations/page.tsx`, directly after
  `<LegacyBookingImport />`, with a matching header block (icon +
  one-line description), following the page's existing section pattern
  exactly.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same file:** `page.tsx` is a single large
  client component assembling many independent sections (snapshot
  regenerate, driver/rider import, booking import, now wallet import).
  This change only adds one import line and one new JSX block after the
  existing `<LegacyBookingImport />` block — no existing section's markup,
  state, or props were touched. `src/lib/api/imports.ts` and
  `src/lib/api.ts` only gained new exports; no existing export was
  modified.
- **Blast radius:** isolated to 2 edited files (additive changes only) and
  1 new file. Grepped both edited files for other importers of the touched
  export blocks — the new exports have no other consumers yet (this PR is
  the only caller), so there's nothing else to break.
- **Could this regress a flow that currently works?** No — the booking
  import section, snapshot regenerate section, and every other section on
  the page are unmodified. `npm run build` (real production build, not
  just `tsc --noEmit`) succeeded and generated `/dashboard/bulk-operations`
  cleanly alongside all other routes.
- **Money-path interaction:** none directly — this component only calls
  the already-reviewed backend route (`docs/change-log/2026-08-24-wallet-import-admin-route-wired.md`),
  which itself is gated by `require_super_admin` and rate-limited. No new
  money logic exists in this change; it's a UI wrapper around an existing
  endpoint.

## 5. User-experience effect

An internal super-admin now has a UI page (Bulk Operations →
Legacy Wallet-Balance Import) to validate and commit wallet-balance
imports, instead of needing to call the API directly. No rider, driver, or
corporate-admin surface is touched. No mid-session visibility change for
anyone already using the app — this is a net-new admin tool section.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api/imports.ts` | Added Wallet-Balance Import types + `walletImportFormData()` + `adminValidateWalletImport`/`adminCommitWalletImport` | API client for the already-wired backend route |
| `admin-dashboard/src/lib/api.ts` | Re-exported the new functions/types | Existing barrel-export pattern for this module |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx` | New file — validate/commit UI, mirroring `LegacyBookingImport.tsx` | The requested admin page |
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Import + render `<LegacyWalletImport />` after `<LegacyBookingImport />`, with matching header | Wire the new section into the Bulk Operations page |
| `docs/change-log/2026-08-24-wallet-import-admin-dashboard-page.md` | This file | Change Impact Log, mandatory for a live-tested admin surface per CLAUDE.md |

## 7. Before / after

```tsx
// Before: page.tsx ended its Bulk Operations sections with:
<LegacyBookingImport />
</div>
);
}
```

```tsx
// After:
<LegacyBookingImport />

<div className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
    <Upload className="h-4 w-4" />
    Legacy Wallet-Balance Import — apply historical rider/driver wallet
    adjustments from the previous app
</div>
<LegacyWalletImport />
</div>
);
}
```

## 8. Rollback plan

- **Code:** `git revert` — the new component, the two API-client additions,
  and the render block in `page.tsx` are all purely additive; reverting
  removes the UI section with zero effect on any other page section or on
  the backend route it calls.
- **Data:** unchanged from `docs/change-log/2026-08-24-wallet-import-admin-route-wired.md`
  — this change adds no new write path, it only exposes the existing
  route through a form. Any batch actually committed through this UI is
  reversible the same way described there (equal-and-opposite
  `wallet_apply_delta` calls, using the `metadata.old_wallet_entry_id`
  each written row carries).

## 9. Verification performed

- [x] `npx tsc --noEmit -p .` — clean, no errors.
- [x] `npx eslint` on all 4 touched/added files — 0 errors (1 pre-existing,
  unrelated warning on `page.tsx` for an already-unused import that
  predates this change).
- [x] **Real production build run**: `npm run build` — succeeded,
  `/dashboard/bulk-operations` compiled and generated along with all 73
  other routes. This is the actual build command, not just a dev server or
  `tsc --noEmit`.
- [x] Blast-radius grep performed — confirmed no other consumer of the new
  exports; confirmed no existing section of `page.tsx` was touched besides
  the new import line and the new render block.
- [ ] Manual repro in staging — **not done**, no live Supabase/backend
  access from this session; the underlying route itself was already
  tested end-to-end (13 tests, see the route's own Change Impact Log).

## What was NOT verified

- **No visual/screenshot check** — admin-dashboard has no active
  visual-regression coverage (per CLAUDE.md: the Playwright
  visual-regression job exists but has zero committed baselines, `B38`).
  The new component's Tailwind/shadcn markup was written to match
  `LegacyBookingImport.tsx`'s existing, already-shipped visual style
  exactly (same `Card`/`Stat`/`IssueTable` structure, same semantic color
  tokens), reasoned about rather than screenshotted.
- **No real backend call was made from this UI.** The form calls the
  already-tested backend route, but no live end-to-end run (real CSV
  upload → real validate → real commit) was performed through the actual
  page in a browser.
- **The underlying CSV column-name assumption is still unverified** — this
  change does not resolve that; it only makes the already-reachable route
  operable from a form instead of curl. The page itself surfaces this risk
  to the operator via an explicit warning banner rather than hiding it.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — `git revert`, purely
  additive change.
- [x] Blast radius is stated, not assumed — see section 4.
- [x] No silent behavior change to an already-shipped flow — new section
  only; every existing section of the page is untouched.
