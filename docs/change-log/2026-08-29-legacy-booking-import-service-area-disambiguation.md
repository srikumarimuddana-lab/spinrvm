# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Found live while the operator ran the "Legacy booking import" tool for the 08-22 export — `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` |

## 1. Issue / gap identified

Clicking "Validate (no writes)" on the Legacy booking import card
(`/dashboard/bulk-operations`) with the default "Saskatoon" service area
returned a real `400 Bad Request` and no report: `"Multiple service areas
matched; select a specific service area. Matches: Saskatoon Airport
(23509b35-222f-4d76-a189-964b3dc7f41b), Saskatoon
(361d17bb-ec55-4561-943f-e3bbee5d7a55)"`. Production now has a second
service area, "Saskatoon Airport", that was created after this UI was
originally built.

## 2. Root cause

`get_service_area()` (`backend/services/booking_import_service.py`)
resolves a service area by name via `ILIKE '%<name>%'` when no
`service_area_id` is given, and correctly refuses to guess when more than
one row matches — this is intentional, existing, correct behavior (it
already worked this way for Phase 1/2's driver imports too). The gap is
that `LegacyBookingImport.tsx` only ever exposed a free-text **name**
field, with no way to pass the `service_area_id` /
`vehicle_type_id` the backend's `BookingImportOptions` type already
supports. Since "Saskatoon" is a substring of "Saskatoon Airport", no
name typed into that field could ever disambiguate the two once a second
matching service area existed — the API's rephrased error message
("select a specific service area") described an action the UI gave no way
to take.

## 3. Fix / remediation

Added two new optional fields to the same card: "Service area ID" and
"Vehicle type ID", wired to `opts.serviceAreaId` / `opts.vehicleTypeId`
(already-existing, already-wired-through `BookingImportOptions` fields —
no API client or backend change needed). Left blank, behavior is
unchanged (falls through to the existing name match). Filled in, the ID
takes priority on the backend exactly as it already did for the CLI's
`--service-area-id` flag.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** to `LegacyBookingImport.tsx`. `serviceAreaId`/
  `vehicleTypeId` were already plumbed through `adminValidateBookingImport`/
  `adminCommitBookingImport` → `bookingImportFormData()` →
  `get_service_area`/`get_vehicle_type` on the backend; this only adds the
  two form inputs that were missing. Grepped for other importers of
  `LegacyBookingImport.tsx` — none; it's mounted once, on this one card.
- **Purely additive**: two new optional inputs, default empty, existing
  name-based flow untouched when left blank.
- No other admin-dashboard component reads or writes these two new pieces
  of local component state.

## 5. User-experience effect

- **Internal admin only** (super_admin-gated tool). Before: an operator
  hitting an ambiguous-name match had no way to proceed from this screen at
  all. After: pasting the ID named in the error message unblocks the same
  validate/commit flow. Not visible mid-session to any rider/driver/
  corporate-admin — this page has no other audience.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | Added `serviceAreaId`/`vehicleTypeId` state + two optional ID input fields, passed through `opts()` | Give the operator a way to pass the ID the backend's own error message already names, when the service-area/vehicle-type name is ambiguous |

## 7. Before / after

```tsx
// Before
const opts = () => ({
    serviceAreaName: serviceAreaName.trim() || "Saskatoon",
    vehicleTypeName: vehicleTypeName.trim() || "Economy",
    ...(report?.batch ? { batch: report.batch } : {}),
});
// No serviceAreaId/vehicleTypeId field existed in the form at all.
```

```tsx
// After
const opts = () => ({
    serviceAreaName: serviceAreaName.trim() || "Saskatoon",
    vehicleTypeName: vehicleTypeName.trim() || "Economy",
    ...(serviceAreaId.trim() ? { serviceAreaId: serviceAreaId.trim() } : {}),
    ...(vehicleTypeId.trim() ? { vehicleTypeId: vehicleTypeId.trim() } : {}),
    ...(report?.batch ? { batch: report.batch } : {}),
});
// Plus two new <Input> fields in the JSX for the two IDs above.
```

## 8. Rollback plan

`git-revert-safe` — purely additive UI fields; reverting drops them and
restores the exact prior (blocked-when-ambiguous) behavior.

## 9. Verification performed

- [x] `npx tsc --noEmit -p .` — no new errors in the changed file.
- [x] **Real production build**: `npm run build` (admin-dashboard) — exit 0, no errors, `/dashboard/records` (the redirect target for `/dashboard/bulk-operations`) builds cleanly. This is a real `npm run build`, not just a dev server or `tsc --noEmit` alone, per CLAUDE.md's requirement.
- [x] Confirmed via the operator's live browser Network tab that the exact 400 + error text this fix targets was actually reproduced against production before writing the fix (not a hypothetical).
- [ ] Not yet re-verified live with the ID fields filled in — pending the operator's next attempt.

## What was NOT verified

- No visual regression tooling exists for admin-dashboard (per CLAUDE.md
  §6 — zero committed baselines) — the two new input fields were reasoned
  about (same `Input`/`Label` components, same grid layout pattern already
  used by the two fields beside them) rather than screenshotted.
- Not tested against a live Supabase session end-to-end by this session —
  the operator will exercise the actual validate/commit call next.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one component, no other
      consumers)
- [x] No silent behavior change to an already-shipped flow — the existing
      name-only path is unchanged when the new fields are left blank; this
      only adds a way to unblock a state that was previously a dead end
