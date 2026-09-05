# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-09-05-rider-app-vehicle-icon-fallback.md` — user asked to check driver-app for the same class of gap |

## 1. Issue / gap identified

`driver-app/app/vehicle-info.tsx` (the screen a driver uses to set/change
their vehicle class during onboarding or a profile update) has a
`VehicleType` interface that already declares an `icon: string` field, but
never reads it. Both places this screen shows a per-vehicle-type icon —
the "Vehicle Type" summary box for the currently selected type, and every
row in the "Select Vehicle Type" picker modal — hardcoded the exact same
`Ionicons name="car"` glyph regardless of which vehicle type it was
representing.

## 2. Root cause

Same root cause as the rider-app gap fixed just before this one: the
backend has always sent `icon` (the public `GET /vehicle-types` endpoint in
`backend/routes/fares.py` fetches the full `vehicle_types` row via
`db_supabase.get_rows(..., columns="*")`, no column filtering), and the
client-side `VehicleType` interface was already typed to expect it — but no
render path in this screen was ever wired up to actually use it.

## 3. Fix / remediation

Added the same `VEHICLE_TYPE_ICON_NAMES` lookup map + `vehicleTypeIconName()`
helper used in the rider-app fix (mirrored, not imported — the two apps
don't currently share a module for this), and used it in both hardcoded
spots:
- The selected-type summary box, via a new `selectedVehicleType` value
  **derived** with `useMemo` from `vehicleTypes` + `form.vehicle_type_id`
  (not new state) — this keeps it correct both on initial load (when
  `driver.vehicle_type_id` seeds the form before the picker is ever
  opened) and after a fresh selection, without duplicating what
  `handleVehicleTypeSelect` already tracks in `vehicleTypeName`.
- Each row in the picker's `FlatList`, via `item.icon` directly.

Same safety property as the rider-app fix: `"car-compact"` (the seeded
value, and also `admin-dashboard`'s current default for a brand-new
vehicle type) is not a real Ionicons glyph name, so it is never passed
into `<Ionicons name={...}>` raw — only through the lookup map, which
falls back to `"car"` (the pre-existing glyph) for anything unrecognized.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file.** Grepped the whole driver-app tree
  for every importer of `vehicle-info.tsx` — only its own test file. The
  `VehicleType` interface here is locally defined (not shared/exported),
  so this change cannot affect any other screen. The driver dashboard's
  own vehicle icon (`driver-app/app/driver/(tabs)/index.tsx`) uses the map
  **marker** system (`resolveMarkerVariant`/`CarMarker`, driven by
  `marker_variant`/`marker_image_url`) — a completely separate, already-
  correct code path, untouched by this change. `become-driver.tsx`'s own
  vehicle-type step renders text-only chips with no icon at all — no gap
  there, nothing to fix.
- No backend, schema, or API change — pure client-side render fix, same
  field the interface already declared.
- Fallback-safe: an unrecognized/legacy icon value renders the exact same
  `"car"` glyph the screen always showed — no existing vehicle type can
  look worse, only distinguishable where it previously always looked
  identical.
- Does not touch driver onboarding submission, verification gating, fare
  calc, dispatch, or any money path — `icon` is decorative/display-only.

## 5. User-experience effect

- **Driver-facing.** Visible on the Vehicle Info screen (`/vehicle-info`)
  — both the current-selection summary box and the "Select Vehicle Type"
  picker rows now show a glyph that varies by vehicle type (car-sport,
  bus, etc.) instead of always the same generic car icon.
- **Visible mid-session?** No — only the next time a driver opens this
  screen; not a live update to an already-open screen.
- No copy/notification change. This screen already warns that changing
  vehicle info triggers re-verification (unchanged, unrelated to this fix).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/vehicle-info.tsx` | Added `VEHICLE_TYPE_ICON_NAMES` map + `vehicleTypeIconName()` helper (mirrors rider-app's `ride-options.tsx`); added a `useMemo`-derived `selectedVehicleType`; replaced both hardcoded `Ionicons name="car"` occurrences (summary box, picker row) with `vehicleTypeIconName(...)` | Wire up the already-declared-but-unused `icon` field |
| `driver-app/__tests__/app/vehicleInfoScreen.test.tsx` | Added `Ionicons` import (resolves to the existing `jest.mock('@expo/vector-icons', ...)` stub); added 2 fixtures (`VEHICLE_TYPE_SPORT`, `VEHICLE_TYPE_LEGACY`) and 2 tests: known-icon mapping in the picker, and unrecognized/legacy-value fallback safety (`car-compact` never passed through raw, in both the picker row and the post-selection summary box) | Regression coverage for the new mapping |
| `docs/change-log/2026-09-05-driver-app-vehicle-icon-fallback.md` | New file (this log) | Required for any behavior change on a live-tested driver-facing surface |

## 7. Before / after

```tsx
// Before — selected-type summary box
<View style={styles.vehicleTypeIconBox}>
    <Ionicons name="car" size={22} color={colors.primary} />
</View>

// Before — each row in the picker
<View style={styles.vehicleTypeOptionIcon}>
    <Ionicons name="car" size={22} color={colors.primary} />
</View>
```

```tsx
// After — selected-type summary box
const selectedVehicleType = useMemo(
    () => vehicleTypes.find(t => t.id === form.vehicle_type_id),
    [vehicleTypes, form.vehicle_type_id],
);
// ...
<View style={styles.vehicleTypeIconBox}>
    <Ionicons name={vehicleTypeIconName(selectedVehicleType?.icon)} size={22} color={colors.primary} />
</View>

// After — each row in the picker
<View style={styles.vehicleTypeOptionIcon}>
    <Ionicons name={vehicleTypeIconName(item.icon)} size={22} color={colors.primary} />
</View>
```

## 8. Rollback plan

Pure frontend, additive-only change — no migration, no feature flag, no
backend/API change. Revert is a plain `git revert` of this PR's commit(s);
the screen returns to always showing the generic car glyph, exactly as
before. No data cleanup needed.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean, no errors in driver-app.
- [x] `npx eslint` on both changed files — 0 new errors; 65 pre-existing
      warnings, all on untouched `StyleSheet` lines deep in the same file
      (hardcoded color/spacing lint rules that predate this change).
- [x] Blast-radius grep: confirmed `vehicle-info.tsx` has exactly one
      importer (its own test file) and that the driver dashboard's vehicle
      icon uses a separate, unrelated marker-image system.
- [x] Verified the `car-compact`-is-not-a-real-glyph landmine the same way
      as the rider-app fix: checked the real Ionicons glyph map shipped in
      `node_modules/@expo/vector-icons` to confirm `car`, `car-sport`,
      `bus`, `bus-outline` are valid and `car-compact` is not.

### What was NOT verified

- **The jest test suite could not be run in this session.** Every
  driver-app jest test — including files this PR does not touch, e.g.
  `__tests__/app/becomeDriverScreen.test.tsx` — currently fails at
  suite-load time with `TypeError: [BABEL] .../@react-native/jest-preset/
  jest/react-native-env.js: _lruCache is not a constructor`, a pre-existing
  environment/toolchain issue (`driver-app/yarn.lock` resolves 3 different
  `lru-cache` versions — 5.1.1, 10.4.3, 11.3.6 — a hoisting/version-conflict
  smell). Confirmed this is not caused by this change: reproduced on an
  untouched file, and `yarn install --frozen-lockfile` made no changes
  (the installed tree already matches the lockfile). Filed as a separate
  suggested task (driver-app jest environment fix) rather than attempting
  a speculative dependency-resolution fix inside this PR. The 2 new tests
  added here follow the exact same pattern as the already-passing rider-app
  tests in the equivalent fix, but have not actually been executed.
- Not run on a real device/simulator — no Expo runtime available in this
  sandboxed session.
- driver-app has no automated visual-regression tooling at all (per
  CLAUDE.md) — this is a visually-invisible-to-tooling change, reasoned
  about via the glyph-map check and code review, not screenshotted.
- Did not verify what fraction of real production `vehicle_types` rows
  currently have a non-default `icon` value — this fallback path is
  visible mainly for types beyond the plain "car" default.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated to one file; confirmed
      via grep; the pre-existing jest breakage is disclosed rather than
      hidden behind an untested "tests added" claim).
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — UX effect (driver-facing, fallback-icon-only, not
      mid-session) is stated in §5.
