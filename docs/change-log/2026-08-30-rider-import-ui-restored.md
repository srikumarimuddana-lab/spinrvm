# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Found while cross-referencing production against the legacy Mongo `customers.csv` export — 216 real riders (5 with completed rides) were never imported, and the tool to fix it was unreachable |

## 1. Issue / gap identified

`admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` showed a "Rider Bulk Import has moved" card pointing at `/dashboard/data-transfer`, but the Data Transfer Import tab does not accept the legacy Mongo `customers.csv` shape at all — it's a different tool (`adminValidateDataTransferImport`/`adminCommitDataTransferImport`) for re-importing a previously-exported Spinr-native data-portability bundle for one account, not bulk-creating brand-new accounts from an external CSV. The actual working tool (`RiderImportSection`, fully coded, phone-based dedup against existing users/drivers) was left defined in the same file but never mounted anywhere reachable.

## 2. Root cause

An incomplete migration: rider bulk import was pointed at Data Transfer before Data Transfer's Import tab actually supported this use case (or ever will, given it's architecturally a different feature), and the redirect card was never corrected once that didn't pan out.

## 3. Fix / remediation

Removed the incorrect redirect card and mounted the existing, already-tested `RiderImportSection` component directly on the Bulk Operations page, positioned before Legacy Booking Import with a comment explaining why the order matters: `booking_import_service.py` matches a booking's rider/driver by phone against an *existing* account, so a rider with no account yet has their completed rides silently skipped rather than created alongside them.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one page.** No backend code changed — `services/rider_import_service.py` and `routes/admin/rider_import.py` were already complete and covered by 21 passing tests (`tests/test_admin_rider_import.py`), confirmed still passing.
- **Purely a UI-reachability fix** — no existing section's behavior changed, no new backend surface.
- `npx tsc --noEmit` clean; `npm run build` succeeded.

## 5. User-experience effect

- **Internal admin only.** Before: no way to run a legacy rider CSV import at all (the pointed-to destination didn't support it). After: the working tool is visible and usable again on Bulk Operations.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/page.tsx` | Removed the incorrect "moved to Data Transfer" redirect card; mounted the existing `RiderImportSection` component with an ordering note | Restore a working, already-built, already-tested tool that had become unreachable |

## 7. Before / after

```tsx
// Before
<Card>
    <CardHeader>
        <CardTitle>Rider Bulk Import has moved</CardTitle>
        ...
    </CardHeader>
    <CardContent><Button asChild><a href="/dashboard/data-transfer">Go to Data Transfer</a></Button></CardContent>
</Card>
```

```tsx
// After
<RiderImportSection />
```

## 8. Rollback plan

`git-revert-safe` — no backend or data change; a pure UI-reachability fix.

## 9. Verification performed

- [x] Confirmed the Data Transfer Import tab's actual backend contract (`routes/admin/data_transfer_import.py`) — a bundle-based single-account reimport, not a bulk-CSV rider creator; genuinely the wrong tool for this use case, not a preference call.
- [x] `pytest tests/test_admin_rider_import.py` — 21 passed (pre-existing, confirming the backend this UI drives is solid).
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` — real production build, succeeded.

## What was NOT verified

- Not yet run against real production — running the actual 216-row rider import batch is the operator's next step.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one page, no backend change)
- [x] No silent behavior change — the tool this restores was already fully built and tested; nothing about its logic changed
