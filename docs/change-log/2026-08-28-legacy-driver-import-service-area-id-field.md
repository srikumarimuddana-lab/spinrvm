# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` Phase 1 execution |

## 1. Issue / gap identified

The Legacy Driver Import admin page (`/dashboard/drivers/legacy-import`) only
exposes a "Service area (name)" text field. Production has two service areas
that both match a `%Saskatoon%` ILIKE lookup — `Saskatoon` and
`Saskatoon Airport` — so validating with the page's own default value
("Saskatoon") always fails with `"Multiple service areas matched; pass
--service-area-id"` raised by
`services/driver_import_service.get_service_area()`. No string typed into the
name field can disambiguate this, since any substring of "Saskatoon" also
matches "Saskatoon Airport". This blocked an operator from running the
already-validated Phase 1 import through the intended UI flow.

## 2. Root cause

The backend route (`routes/admin/legacy_driver_import.py`) and the API client
(`admin-dashboard/src/lib/api/imports.ts`) already fully support an optional
`service_area_id` parameter that bypasses the ambiguous name lookup — this
was simply never wired into the page component (`page.tsx`), which only
tracked `serviceAreaName` state and never sent a `service_area_id`. The gap
was in the UI layer only; no backend change was needed.

## 3. Fix / remediation

Added a `serviceAreaId` input field to the page, defaulted to the real
Saskatoon service area's id (`361d17bb-ec55-4561-943f-e3bbee5d7a55`,
confirmed against production via read-only SQL this session), and included
it in the `importOpts()` payload sent to both validate and commit calls. The
field remains editable/clearable for any other service area. This mirrors
the existing hardcoded `"Saskatoon"` default already used for the name
field — the whole page is explicitly Saskatoon-specific per its own
description.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Only `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx`
  changed. Grepped for other importers of this page/component — none exist
  (it's a route-level page, not a shared component). `imports.ts`'s
  `serviceAreaId` option and the backend route's `service_area_id` form field
  already existed and are unchanged; Phase 2's SIN/DOB and vehicle-history
  backfill pages have their own separate state and were not touched.
- No table, endpoint contract, or background loop is affected — this is a
  client-side form field addition only.
- If left blank, behavior is unchanged from today (falls back to name-only
  lookup, which still works for any unambiguous service area name).

## 5. User-experience effect

- **Internal admin only.** No rider/driver-facing change. Not visible
  mid-session to anyone outside the admin using this specific page.
- The page's only existing user of this exact flow is the operator running
  the Phase 1 legacy driver import right now — previously blocked, now
  unblocked. No other admin user's workflow changes since no other admin
  page references this field.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/legacy-import/page.tsx` | Added `serviceAreaId` state (defaulted to the real Saskatoon service area id) + input field; included in `importOpts()` | Unblock validate/commit against production, where name-only lookup is ambiguous |

## 7. Before / after

```
# Before
const [serviceAreaName, setServiceAreaName] = useState("Saskatoon");
...
const importOpts = () => ({ serviceAreaName: serviceAreaName.trim() || "Saskatoon" });
```

```
# After
const [serviceAreaName, setServiceAreaName] = useState("Saskatoon");
const [serviceAreaId, setServiceAreaId] = useState("361d17bb-ec55-4561-943f-e3bbee5d7a55");
...
const importOpts = () => ({
    serviceAreaName: serviceAreaName.trim() || "Saskatoon",
    serviceAreaId: serviceAreaId.trim() || undefined,
});
```

## 8. Rollback plan

`git revert` is sufficient and safe here — purely additive client-side form
state, no production data is written or altered by this change itself (it
only changes what parameters an operator-initiated import request carries).
No feature flag needed for an internal-admin-only, single-page UI field.

## 9. Verification performed

- [x] Automated tests run: `npx vitest run src/__tests__/dashboard/pages.smoke.test.tsx -t legacy-import` — 1 passed.
- [x] **Real production build run**: `npm run build` (Next.js production build, not just `tsc --noEmit`) — succeeded, `/dashboard/drivers/legacy-import` listed in route output.
- [x] Blast-radius grep performed: searched for other consumers of `serviceAreaName`/`legacy-import` across `admin-dashboard/src` — only this page and the already-generic `imports.ts` client match; no other page imports this component.
- [x] Reviewed against relevant CLAUDE.md conventions: additive-only (release gate #2), isolated blast radius stated (gate #1), no schema/API contract change.
- [ ] Feature-flagged: not applicable — internal-admin-only form field, not a rider/driver-facing behavior change.

## What was NOT verified

- Not tested against a live Supabase call end-to-end from the browser (no
  running dev server in this session) — verified by build + smoke test only,
  and by direct reading of the already-existing, already-tested backend
  route and API client code paths that this field now actually invokes.
- No visual-regression tooling is active for admin-dashboard (standing gap,
  `ACTION_ITEMS.md` B38) — the new field's layout was reasoned about (same
  `Input`/`label` pattern as the adjacent existing field) rather than
  screenshotted.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to one page)
- [x] No silent behavior change to an already-shipped flow — this page has
      never successfully completed a production import before (validated
      this session for the first time), so there is no working prior
      behavior being altered; the change only enables the page to fulfill
      its own already-documented capability
