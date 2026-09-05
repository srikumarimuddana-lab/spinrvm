# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Follow-up finding from the admin-portal design-canvas review (docs/design-drafts/2026-09-04-admin-portal-canvas-inventory.md) |

## 1. Issue / gap identified

The Vehicle Types admin page's "Icon Name" field (`/dashboard/vehicle-types`,
Add/Edit dialog) was a raw free-text input — an admin had to type an exact
icon identifier from memory (e.g. `car-sport`) with zero preview, and a typo
or unknown value silently produced no visible feedback until the card list
re-rendered.

## 2. Root cause

The field predates `VEHICLE_ICON_MAP` (added in PR #4985, merged prior to
this change), which maps a fixed set of known icon-name strings to a
lucide-react icon for display on the vehicle-type cards. That PR fixed the
*rendering* side (cards now show a real icon instead of always a generic
car) but left the *input* side untouched — the text box was never replaced
with a picker built from the same known set, so there was no way to see or
choose a valid value without guessing.

## 3. Fix / remediation

Replaced the free-text "Icon Name" `<Input>` with a visual 4-button icon
grid, built directly from the existing `VEHICLE_ICON_MAP` (same map already
used for card rendering — no new/duplicate icon list introduced) and its
existing `vehicleIconLabel()` helper for button captions. Selecting a button
writes that map key to `form.icon`, exactly as the old text field did with a
typed string. Also changed the new-vehicle-type default (`EMPTY_FORM.icon`)
from `"car"` (not a `VEHICLE_ICON_MAP` key, so it always rendered via the
generic fallback anyway) to `"car-compact"` (the map's first key) purely so
the picker shows a selected/highlighted button by default for a brand-new
type — visually identical output either way, since both values fall back to
the same generic Car glyph today.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one file** (`admin-dashboard/src/app/dashboard/vehicle-types/page.tsx`).
  Grepped the whole repo for other consumers of a vehicle type's `icon`
  field: `admin-dashboard/src/lib/api/live-monitoring.ts`'s `AdminVehicleType`
  interface and every other admin-dashboard caller of `getVehicleTypes()`
  (`drivers/page.tsx`, `monitoring/page.tsx`, `service-areas/page.tsx`,
  `rides/_components/create-ride-modal.tsx`) only reads `id`/`name` off
  vehicle types, never `icon` — none of them are touched by this change.
  Backend (`backend/routes/admin/vehicle_fleet.py`) already treats `icon` as
  an unvalidated free-string field (`icon: str = ""` / `Optional[str]`); the
  picker still just sends a string in the same shape, so no backend or
  migration change is needed.
- **Cross-surface note (not part of this change):** grepped rider-app and
  driver-app — `vehicle_type.icon` is not read anywhere in either app today.
  `rider-app/app/ride-options.tsx` (the real booking screen) falls back to a
  hardcoded Ionicons `"car"` glyph whenever a vehicle type has no uploaded
  illustration, regardless of its `icon` value. This is a separate,
  pre-existing gap (the field has never actually driven what a rider sees)
  and is out of scope for this admin-only fix — flagged separately, not
  fixed here, since wiring it up touches a live-tested rider-facing screen.
- Existing DB rows with an `icon` value outside the 4 map keys (including
  any legacy free-typed string) are unaffected until an admin re-opens and
  re-saves that type: the card list already falls back to the generic Car
  glyph for an unrecognized value (unchanged, from PR #4985), and the picker
  simply shows no button highlighted for such a row until one is clicked.
- No ride state, dispatch, fare, or payment path reads this field — vehicle
  type `icon` is decorative/display-only everywhere it's currently consumed.

## 5. User-experience effect

- **Internal-admin facing only.** Riders and drivers see no change from this
  PR — see the cross-surface note above; the field isn't wired to their
  apps regardless of how it's set in admin.
- Not visible mid-session to anyone already using the rider/driver apps.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` | Replaced the free-text "Icon Name" input with a 4-button visual picker built from the existing `VEHICLE_ICON_MAP`; changed `EMPTY_FORM.icon` default from `"car"` to `"car-compact"`; updated the stale comment above `VEHICLE_ICON_MAP` to describe its new dual role (render map + picker source) | Close the admin-UX gap the icon-rendering fix (PR #4985) left open |
| `docs/change-log/2026-09-05-vehicle-types-icon-picker.md` | New file (this log) | Required for any behavior change on a live-tested admin surface |

## 7. Before / after

```tsx
// Before
<div className="grid grid-cols-2 gap-4">
    <div className="space-y-2">
        <Label htmlFor="vehicle-type-icon">Icon Name</Label>
        <Input
            id="vehicle-type-icon"
            placeholder="car"
            value={form.icon}
            onChange={(e) => setForm({ ...form, icon: e.target.value })}
        />
    </div>
    <div className="space-y-2">
        <Label htmlFor="vehicle-type-capacity">Capacity</Label>
        <Input id="vehicle-type-capacity" type="number" min={1} max={20}
            value={form.capacity}
            onChange={(e) => setForm({ ...form, capacity: parseInt(e.target.value) || 4 })} />
    </div>
</div>
```

```tsx
// After
<div className="space-y-2">
    <Label>Icon</Label>
    <p className="text-xs text-muted-foreground">...</p>
    <div className="grid grid-cols-4 gap-2">
        {Object.entries(VEHICLE_ICON_MAP).map(([value, Icon]) => (
            <button key={value} type="button"
                onClick={() => setForm({ ...form, icon: value })}
                className={form.icon === value ? "...selected..." : "...unselected..."}>
                <Icon className="h-6 w-6" />
                <span className="text-xs font-medium">{vehicleIconLabel(value)}</span>
            </button>
        ))}
    </div>
</div>

<div className="space-y-2">
    <Label htmlFor="vehicle-type-capacity">Capacity</Label>
    <Input id="vehicle-type-capacity" type="number" min={1} max={20}
        value={form.capacity}
        onChange={(e) => setForm({ ...form, capacity: parseInt(e.target.value) || 4 })} />
</div>
```

## 8. Rollback plan

Pure frontend, additive-UI change with no migration, no feature flag, and no
data-shape change (the field it writes is the same string `icon` column, in
the same value space it already accepted). Revert is a plain `git revert` of
this PR's commit(s) — no data remediation needed, since nothing this PR
writes differs in kind from what the old free-text field could already
produce.

## 9. Verification performed

- [x] Automated tests run: none exist for this page today (no
      `admin-dashboard` unit-test file covers `vehicle-types/page.tsx`); ran
      the full `admin-dashboard` **production build** (`npm run build`) —
      succeeded, `/dashboard/vehicle-types` compiled with no errors. Also
      ran `npx tsc --noEmit` (no errors in this file) and `npx eslint` on
      the changed file (0 errors; the 7 pre-existing warnings are all on
      untouched lines — `<img>` usage and an effect-setState pattern that
      predate this change).
- [ ] Manual repro steps followed in staging — not available in this
      sandboxed session (no live Supabase/admin login); reasoned through the
      code path instead (see "What was NOT verified" below).
- [x] Blast-radius grep performed: searched the whole repo for other
      consumers of a vehicle type's `icon` field (admin-dashboard callers of
      `getVehicleTypes()`, rider-app, driver-app) — see §4.
- [x] Reviewed against relevant CLAUDE.md convention(s): additive-over-
      destructive (kept the same field/column, same value space); no
      state-machine, money, or RLS path touched.
- [ ] Feature-flagged: not applied. Justification: this is a low-traffic
      internal-admin-only form control with no rider/driver-visible effect
      and no risk to money, dispatch, or ride-state paths — the project's
      own bar for flagging ("new/changed UX ... for anything touching a
      shared component used by 3+ pages") doesn't apply here (this page has
      no other consumers), and admin-dashboard has real CI-wired visual-
      regression coverage but `vehicle-types` is not one of the 6 seeded
      pages, so there's no baseline to protect either way.

### What was NOT verified

- Not clicked through in a live/staging admin session — no Supabase
  connection or admin login available in this sandbox. Verified instead via
  a real production build succeeding and a careful read of the diff against
  the existing, already-shipped `MARKER_VARIANTS` picker pattern in the same
  file (same button/grid/selected-state structure, proven working code).
- No visual-regression tooling covers this page (not one of the 6 seeded
  admin-dashboard pages) — this is a visually-invisible-to-tooling change
  that was reasoned about, not screenshotted, per CLAUDE.md's disclosure
  requirement for admin-dashboard pages outside that seeded set.
- Did not verify against real production `vehicle_types` row data — cannot
  confirm what non-map icon strings (if any) currently exist in the live
  table beyond what `backend/seed_vehicle_types.py` seeds.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated to one file; cross-
      surface rider/driver gap explicitly named and explicitly NOT fixed
      here).
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — UX effect (admin-only, no rider/driver visibility)
      is stated in §5.
