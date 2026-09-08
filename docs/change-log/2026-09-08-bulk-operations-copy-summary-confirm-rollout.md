# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (session `session_01JwSyq7NYGkg4ReSHBZDqva`) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR — this file is linked from its description) |
| Related issue or gap ID | Live user request this session: make the Bulk Import tools on `/dashboard/bulk-operations` consistent |

## 1. Issue / gap identified

Of the 7 bulk-import/migration components on the Bulk Operations page, none had the
"Copy summary" clipboard button that `drivers/legacy-sin-dob-backfill/page.tsx` already
established as this codebase's pattern for a report with counts, and one of them
(`LegacyTaxIdImport.tsx`) had no last-chance confirmation gate at all before committing a
write to production driver records.

## 2. Root cause

The 7 components were built incrementally (Steps 11-19 of the migration tool chain,
`docs/runbooks/migration-tool-order.md`) at different times, each adopting whichever
confirm mechanism (typed phrase, `AlertDialog`, or none) its author judged the write's
risk warranted at the time, without a pass to bring the *reporting* UX (Copy summary) in
line across all of them.

## 3. Fix / remediation

- Added a `buildSummaryText(report) => string` helper + a `variant="ghost" size="sm"`
  "Copy summary" button (lucide `Copy` icon, `navigator.clipboard.writeText(...)`, toast
  `{ description: "Summary copied", duration: 1500 }`) to all 7 components. Copy-only —
  no commit-path behavior changed anywhere.
- Added an `AlertDialog` confirm-before-commit gate to exactly one component,
  `LegacyTaxIdImport.tsx` — the only one of the 7 that had neither a typed-confirm phrase
  nor a dialog, despite writing vault-encrypted SIN + GST/HST BN to driver records and
  triggering a background Stripe push. No dialog was added to the other five commit-capable
  tools (`DriverRepairPass`, `LegacyBookingImport`, `LegacyWalletImport`,
  `PreLaunchDataFlag` — all already gated by a stricter type-to-confirm text phrase) or to
  the two read/audit-only tools (`DataQualityScan`, `LegacyIdCrosswalkBackfill` — additive
  metadata/lookup writes with no comparable risk, by explicit prior design).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Each of the 7 files is a self-contained component rendered
  independently inside `bulk-operations/page.tsx` (not modified by this change). No shared
  hook, prop, or exported type was touched — `buildSummaryText` is a private, per-file
  function, not extracted to a shared module. No other file imports any of these 7
  components. Grepped for other importers of each component name and of
  `@/components/ui/alert-dialog`; the only other admin-dashboard consumer of
  `alert-dialog` is `drivers/legacy-sin-dob-backfill/page.tsx`, which this change does not
  touch.
- The only behavior-changing edit is `LegacyTaxIdImport.tsx`: its commit button now opens
  an `AlertDialog` instead of calling `handleCommit` directly. `handleCommit` itself,
  `adminCommitTaxIdBackfill`, and the backend route (`routes/admin/tax_id_import.py`) are
  unmodified — the write path, NULL-only fill policy, and Stripe push behavior are
  identical; only the number of clicks to trigger a commit changed (one click → open
  dialog, one more click → commit).
- No ride state, wallet delta, or Stripe call site was modified. No migration, no schema
  change, no new API endpoint.

## 5. User-experience effect

- Internal-admin facing only (super_admin-gated tools; not visible to riders, drivers, or
  corporate admins).
- Not visible mid-session to anyone already using the app — these are one-off migration
  tools run by an operator, not a live rider/driver surface.
- No copy/notification change beyond the new toast text ("Summary copied") already used
  verbatim by the reference implementation, and the new `AlertDialog` copy on
  `LegacyTaxIdImport`, written to match the tone/content of its sibling
  (`legacy-sin-dob-backfill`)'s existing dialog copy.
- UX change for an operator using `LegacyTaxIdImport`: committing now requires
  confirming in a dialog first, where previously a single click on "Commit backfill"
  wrote immediately. This is the intended, and only, behavior change in this PR.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DataQualityScan.tsx` | Added `buildSummaryText` + Copy-summary button | Consistency; no confirm dialog (read-and-tag audit pass, existing precedent) |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/DriverRepairPass.tsx` | Added `buildSummaryText` + Copy-summary button | Consistency; no confirm dialog (already gated by stricter type-to-confirm "REPAIR" phrase) |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyIdCrosswalkBackfill.tsx` | Added `buildSummaryText` + Copy-summary button | Consistency; no confirm dialog (additive-only insert, explicit exclusion per its own docstring) |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | Added `buildSummaryText` + Copy-summary button | Consistency; no confirm dialog (already gated by stricter type-to-confirm "IMPORT" phrase) |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyWalletImport.tsx` | Added `buildSummaryText` + Copy-summary button | Consistency; no confirm dialog (already gated by stricter type-to-confirm "APPLY" phrase); money-touching, flagged in PR |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/PreLaunchDataFlag.tsx` | Added `buildSummaryText` + Copy-summary button | Consistency; no confirm dialog (already gated by stricter type-to-confirm "FLAG" phrase) |
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyTaxIdImport.tsx` | Added `buildSummaryText` + Copy-summary button; wrapped "Commit backfill" in an `AlertDialog` | Consistency; this was the only one of the 7 with zero last-chance gut-check, despite writing SIN/GST BN + a Stripe push |

## 7. Before / after

Only `LegacyTaxIdImport.tsx` changes commit-path behavior.

```
# Before
<Button onClick={handleCommit} disabled={committing}>
    {committing ? (<>...Committing…</>) : "Commit backfill"}
</Button>
```

```
# After
<AlertDialog>
    <AlertDialogTrigger asChild>
        <Button disabled={committing}>
            {committing ? (<>...Committing…</>) : "Commit backfill"}
        </Button>
    </AlertDialogTrigger>
    <AlertDialogContent>
        <AlertDialogHeader>
            <AlertDialogTitle>Write tax ID(s) for {c.to_write} driver(s)?</AlertDialogTitle>
            <AlertDialogDescription>...</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleCommit}>Commit backfill</AlertDialogAction>
        </AlertDialogFooter>
    </AlertDialogContent>
</AlertDialog>
```

## 8. Rollback plan

No feature flag — these are super_admin-only internal tools with no rider/driver/corporate
exposure, and the change is UI-only (no data written differently, no migration, no schema
change). Rollback is a plain `git revert` of the relevant commit(s) on this branch followed
by a redeploy; nothing has been applied to live data by this change itself (only the *UI
around* existing, unmodified write paths changed). If only the extra confirm click on
`LegacyTaxIdImport` is unwanted, reverting that single commit (`095f604` in this branch's
history) restores the one-click commit without affecting the other 6 files' Copy-summary
buttons.

## 9. Verification performed

- [x] Automated tests run: `npx vitest run src/__tests__/dashboard/pages.smoke.test.tsx` —
      27/27 passing after each of the 3 commits (the `bulk-operations` page render, which
      mounts all 7 touched components, is exercised by this suite). `Copy` was already
      present in the suite's shared `lucide-react` icon mock list — no mock update needed.
- [x] `npx tsc --noEmit -p .` — clean after every commit.
- [x] `npx eslint <each touched file>` — 0 errors/warnings on all 7 files.
- [x] **Production build run**: `npm run build` completed successfully after all 3 commits,
      including a clean compile of `/dashboard/bulk-operations`. This is a real production
      build, not just `tsc --noEmit` or a dev server.
- [x] Blast-radius grep performed: no other file imports any of the 7 touched components;
      the only other `@/components/ui/alert-dialog` consumer in admin-dashboard is the
      untouched `drivers/legacy-sin-dob-backfill/page.tsx` reference file.
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (summary text carries only
      counts, never SIN/GST BN/phone — matches each report type's own no-PII guarantee);
      admin-dashboard visual-regression suite's 6 seeded pages
      (`login`/`dashboard-home`/`dashboard-drivers`/`dashboard-monitoring`/
      `dashboard-settings`/`dashboard-rides`) do not include `/dashboard/bulk-operations`,
      so this change has no seeded visual baseline to break or re-capture.
- [ ] Feature-flagged: not applicable — internal-admin-only UI change, no rider/driver/
      corporate exposure, and additive-only (Copy button) or single extra confirmation
      click (`LegacyTaxIdImport`).

## 10. What was NOT verified

- No manual click-through in a running staging/dev server was performed — verification
  relied on `tsc`, `eslint`, the existing smoke test (which renders but does not click
  through the multi-step validate → review → commit flow), and a production build. The
  actual `navigator.clipboard.writeText` call and the `AlertDialog` open/cancel/confirm
  interaction were not exercised by hand or by a new test; they follow an existing,
  already-shipped pattern verbatim (`legacy-sin-dob-backfill/page.tsx`), which is why no
  new automated test was added for them.
- Not tested against live Supabase or a live Stripe account — no backend code was
  touched, so this was considered out of scope.
- No visual regression tooling exists for `/dashboard/bulk-operations` specifically (it is
  not one of the 6 seeded pages in `e2e/visual-regression.spec.ts`), so the new buttons'
  visual placement was reasoned about (mirrors the reference file's exact JSX/placement),
  not screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no live-data remediation needed)
- [x] Blast radius is stated, not assumed (isolated — grepped for other importers)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
      (the one behavior change — `LegacyTaxIdImport`'s new confirm click — is called out
      above)
