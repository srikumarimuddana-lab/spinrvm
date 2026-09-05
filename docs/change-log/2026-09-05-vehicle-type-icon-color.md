# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides, drivers |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Found in the same codebase-wide audit that produced the emergency-contact icon-color fixes (`docs/change-log/2026-09-05-driver-app-emergency-contact-icon-color.md`, `docs/change-log/2026-09-05-rider-app-emergency-contact-icon-color.md`) |

## 1. Issue / gap identified

Both apps map a vehicle type's `icon` key (`car-compact`/`car-sport`/`bus`/`bus-outline`, seeded in `backend/seed_vehicle_types.py`) to a distinct Ionicons glyph via `vehicleTypeIconName()`, but every rendered instance used one hardcoded color:
- `rider-app/app/ride-options.tsx` — the ride-selection screen's fallback icon (shown when a vehicle type has no admin-uploaded `image_url`), hardcoded `"#666"`.
- `driver-app/app/vehicle-info.tsx` — the vehicle-type field summary and the type picker's `FlatList` rows, both hardcoded `colors.primary`.

The ride-selection screen is the highest-visibility side-by-side comparison list in the rider app (multiple vehicle-type cards on screen together); the driver picker is a genuine `FlatList` of type options. In both, every type read as visually identical except for icon shape.

## 2. Root cause

Not applicable (enhancement, not a defect) — same shape as the two emergency-contact fixes: the existing function only returned a glyph name, with no color dimension.

## 3. Fix / remediation

Added a parallel `vehicleTypeIconColor()` map/function next to the existing `vehicleTypeIconName()` in both files, keyed on the same seeded `icon` values:

| Icon key | Seeded tier (`seed_vehicle_types.py`) | Color |
|---|---|---|
| `car-compact` | Economy | `#3B82F6` (blue) |
| `car-sport` | Premium | `#F59E0B` (amber) |
| `bus` | Van | `#10B981` (green) |
| `bus-outline` | XL | `#8B5CF6` (purple) |
| unrecognized/missing | — | `#6B7280` (neutral gray) |

- **rider-app**: the ride-selection fallback icon's background circle now tints to the type's accent color (`color + '15'`), and the icon itself renders in that color instead of `"#666"`.
- **driver-app**: both the field-summary icon box and each picker-row icon box now use the same per-type tint/color, so the color a driver sees in the collapsed field matches the row highlighted when they open the picker.

Same palette in both apps (and the same palette already used for the emergency-contact fixes' relationship colors is a disjoint set, avoiding confusion between the two features).

## 4. Risk & impact on existing functionality

- **Blast radius: confirmed isolated.** Grepped both `rider-app/` and `driver-app/` for `vehicleTypeIconName`/`VEHICLE_TYPE_ICON_NAMES` — each app's copy is local to its one file, with no shared import between them (this map is deliberately triplicated across rider-app/driver-app/admin-dashboard per the existing code comment; admin-dashboard's own `VEHICLE_ICON_MAP` in `admin-dashboard/src/app/dashboard/vehicle-types/page.tsx` was reviewed by the audit and found to be a weaker match — it renders with the default/muted foreground color rather than a hardcoded override — so it was intentionally left untouched here).
- **rider-app change is scoped to a fallback path** — `estimate.vehicle_type.image_url` is set for most/all production vehicle types (an admin-uploaded illustration), so this fallback icon is the less-common render path; the fix still matters for any type without an uploaded image and keeps the fallback visually consistent with the rest of the app's per-category coloring.
- Purely decorative in both apps — does not change fare calculation, vehicle-type selection logic, dispatch matching, or the vehicle-type data model itself.
- No backend, schema, or API change.

## 5. User-experience effect

- **Rider-facing** (`ride-options.tsx`): when a vehicle type has no admin-uploaded image, its fallback icon and background circle now show a color specific to that type instead of a flat gray, making the ride-selection list easier to visually distinguish between types at a glance.
- **Driver-facing** (`vehicle-info.tsx`): the vehicle-type field and its picker rows now show a color specific to each type instead of every row sharing the app's red brand color.
- **Visible mid-session?** Only the next time either screen is opened/re-rendered with fresh data — not a live update to an already-open screen.
- No copy or functional change to either flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-options.tsx` | Added `VEHICLE_TYPE_ICON_COLORS`/`vehicleTypeIconColor()`; fallback icon and its background circle now use the type's accent color instead of `"#666"` | Give each vehicle type a distinguishing accent color, matching the icon-shape distinction that already existed |
| `rider-app/__tests__/rideOptionsScreen.test.tsx` | Added 2 tests: two vehicle types render distinct, non-gray fallback-icon colors; an unrecognized icon value falls back to the neutral default color | Regression coverage |
| `driver-app/app/vehicle-info.tsx` | Added the same `VEHICLE_TYPE_ICON_COLORS`/`vehicleTypeIconColor()`; both the field-summary icon and the picker's `FlatList` row icons now use the type's accent color instead of `colors.primary` | Same fix, ported to the driver-side vehicle-type picker |
| `driver-app/__tests__/app/vehicleInfoScreen.test.tsx` | Added 2 tests: two vehicle types render distinct picker-row colors; the selected type's color carries into the field summary. Both deliberately use `bus`/`bus-outline` test fixtures rather than `car`/`car-sport`, documented inline, to avoid two unrelated hardcoded icons already on this screen (`car-sport` in the always-on hero-card decoration at line ~269, and `car` as the field summary's pre-selection default) that share the same glyph names and would otherwise make the color assertion pass for the wrong reason | Regression coverage; the in-file comment records why those two fixtures were chosen so a future edit doesn't reintroduce the same false-positive |
| `docs/change-log/2026-09-05-vehicle-type-icon-color.md` | New file (this log) | Required for a change to `rides`/`drivers` surfaces per CLAUDE.md's live-testing gate |

## 7. Before / after

```tsx
// Before (rider-app/app/ride-options.tsx)
<View style={styles.carIconFallback}>
  <Ionicons name={vehicleTypeIconName(estimate.vehicle_type.icon)} size={...} color="#666" />
</View>
```

```tsx
// After
<View style={[styles.carIconFallback, { backgroundColor: vehicleTypeIconColor(estimate.vehicle_type.icon) + '15' }]}>
  <Ionicons name={vehicleTypeIconName(estimate.vehicle_type.icon)} size={...} color={vehicleTypeIconColor(estimate.vehicle_type.icon)} />
</View>
```

```tsx
// Before (driver-app/app/vehicle-info.tsx, both the field summary and the picker row)
<View style={styles.vehicleTypeIconBox}>
  <Ionicons name={vehicleTypeIconName(selectedVehicleType?.icon)} size={22} color={colors.primary} />
</View>
```

```tsx
// After
<View style={[styles.vehicleTypeIconBox, { backgroundColor: vehicleTypeIconColor(selectedVehicleType?.icon) + '15' }]}>
  <Ionicons name={vehicleTypeIconName(selectedVehicleType?.icon)} size={22} color={vehicleTypeIconColor(selectedVehicleType?.icon)} />
</View>
```

## 8. Rollback plan

Pure frontend, additive-only visual change in both apps — no migration, no feature flag, no data change. Revert is a plain `git revert` of this commit; both screens return to their prior single-color icon rendering.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean in both apps, no errors on either changed file.
- [x] `npx eslint` on all 4 changed files — 0 errors in each; new warnings are the same pre-existing "no hardcoded hex colors" style rule already present elsewhere in these files.
- [x] rider-app: added 2 tests to `rideOptionsScreen.test.tsx` — full file 123/123 passed. Full rider-app suite: 143 suites / 1980 tests, all passed.
- [x] driver-app: added 2 tests to `vehicleInfoScreen.test.tsx` — full file 15/15 passed. Full driver-app suite: 130 suites / 1476 tests, all passed.
- [x] Caught and fixed a real test-design bug during this work: my first draft of both new driver-app tests picked colors off the FIRST Ionicons node matching a glyph name, which collided with two unrelated hardcoded icons already on the screen (the hero card's always-on `car-sport` decoration, and the field summary's pre-selection `car` default) — both share `colors.primary`/the default color by coincidence, so the assertions passed without exercising the actual fix. Rewrote both tests to use `bus`/`bus-outline` fixtures, which don't collide with anything else in the file, and documented why inline so a future edit doesn't reintroduce the same false positive.
- [x] Blast-radius grep: confirmed each app's `vehicleTypeIconName`/map is local to its one file, no cross-app or cross-file sharing.

### What was NOT verified

- Not run on a real device/simulator — no Expo runtime available in this sandboxed session.
- Neither app has automated visual-regression tooling (per CLAUDE.md) — reasoned about via the jest color/icon assertions and code review, not screenshotted.
- Color-contrast/accessibility of the accent colors was reasoned about (mid-saturation hues on a light `15%`-opacity tint of themselves, same approach as the existing `colors.primary` usage they replace) but not measured with a contrast-ratio tool.
- Did not touch `admin-dashboard`'s separate `VEHICLE_ICON_MAP` — reviewed and judged a weaker match to this pattern (see §4), left as a possible follow-up rather than folded into this change.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated per-app; confirmed via grep).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — UX effect (decorative-only, not mid-session, no change to fare/dispatch logic) is stated in §5.
