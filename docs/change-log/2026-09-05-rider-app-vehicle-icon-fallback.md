# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Cross-surface gap flagged (not fixed) in `docs/change-log/2026-09-05-vehicle-types-icon-picker.md` §4 |

## 1. Issue / gap identified

The rider-app's real booking screen (`app/ride-options.tsx`, the vehicle
selection cards shown after a rider requests a ride) never read a vehicle
type's `icon` field. Whenever a vehicle type had no uploaded illustration
image, every single card — Standard, XL, Premium, whatever — showed the
exact same generic `Ionicons name="car"` glyph, hardcoded, regardless of
what an admin had configured for that type.

## 2. Root cause

The `icon` field has always been sent by the backend (`vehicle_type.icon` is
part of the full `vehicle_types` row returned by `/fares` and the ride
estimate endpoint — confirmed by reading `backend/routes/fares.py`'s
`_fares_for_location_impl`, which fetches `vehicle_types` with the default
`columns="*"`, so no column is excluded), but no client ever read it. The
admin dashboard only gained an icon-to-glyph mapping (`VEHICLE_ICON_MAP`) in
PR #4985, and rider-app's fallback rendering predates that entirely — it
was simply never wired up on the client side.

## 3. Fix / remediation

Added a small `vehicleTypeIconName()` lookup in `ride-options.tsx` and used
it in place of the hardcoded `"car"` glyph. It maps the same 4 known
`icon` values the admin dashboard's `VEHICLE_ICON_MAP` uses
(`car-compact`, `car-sport`, `bus`, `bus-outline`) to a **real, valid**
Ionicons glyph name, with anything unrecognized (including missing/`null`)
falling back to `"car"` — the exact glyph this screen always rendered
before this fix, so there's no regression path, only an improvement.

**Why a lookup map instead of passing `vehicle_type.icon` straight into
`<Ionicons name={...}>`:** `"car-compact"` — the actual seeded value in
`backend/seed_vehicle_types.py`, and also the current default for a
brand-new vehicle type created via the admin picker (`vehicle-types/page.tsx`
`EMPTY_FORM.icon`) — is **not** a real Ionicons glyph name (verified against
the installed `@expo/vector-icons` Ionicons glyph map: valid car/bus names
are `car`, `car-outline`, `car-sharp`, `car-sport`, `car-sport-outline`,
`car-sport-sharp`, `bus`, `bus-outline`, `bus-sharp` — `car-compact` is not
among them). Passing it through raw would have rendered a blank/missing
glyph for the most common seeded vehicle type. The lookup map sidesteps
this entirely by treating `icon` as an opaque key (exactly how the admin
dashboard's own `VEHICLE_ICON_MAP` already treats it), not a literal font
glyph name.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one render site in one file**
  (`rider-app/app/ride-options.tsx`). Grepped the whole rider-app tree for
  every other reader of `vehicle_type.image_url`/`vehicle_type.icon` — the
  only other place `image_url` is read is the same component, and no other
  screen reads `icon` at all (a generic decorative `car-outline` icon in
  `BookingProposalCard.tsx`'s "Confirm your ride" header is unrelated — not
  per-vehicle-type, unaffected by this change).
- No backend, schema, or API change — the field this reads was already
  being sent; this is a pure client-side render fix.
- **Fallback safety, not a stricter requirement:** an unrecognized icon
  value (including every value that existed before this map was written)
  renders the exact same `"car"` glyph the screen always showed — this
  cannot make any existing vehicle type look worse, only give the 4 known
  values (`car-compact` maps to plain `car` too, `car-sport`, `bus`,
  `bus-outline`) a distinct icon where they previously all looked identical.
- Does not touch fare calculation, dispatch, ride state, or any money path
  — `icon` is decorative/display-only.

## 5. User-experience effect

- **Rider-facing.** Visible on the ride-options (vehicle selection) screen,
  specifically only for a vehicle type that has **no** uploaded car
  illustration image (`image_url`) — most configured types likely do have
  one, in which case this change has zero visible effect (the image branch
  is unchanged). For a type with no illustration, riders now see a
  car/car-sport/bus glyph that varies by vehicle type instead of always the
  same generic car icon.
- **Visible mid-session?** Only the next time a rider opens the ride-options
  screen (fetches a fresh fare estimate) — not a live update pushed to an
  already-open screen.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-options.tsx` | Added `VEHICLE_TYPE_ICON_NAMES` map + `vehicleTypeIconName()` helper; replaced the hardcoded `Ionicons name="car"` fallback glyph with `vehicleTypeIconName(estimate.vehicle_type.icon)` | Wire up the previously-unused `icon` field so vehicle types are visually distinguishable without an uploaded illustration |
| `rider-app/__tests__/rideOptionsScreen.test.tsx` | Added `Ionicons` import (resolves to the existing `jest.mock('@expo/vector-icons', ...)` stub); added 2 tests: known-icon mapping (`car-sport`), and unrecognized/legacy-value fallback safety (`car-compact` → `car`, never passed through raw) | Regression coverage for the new mapping, including the specific `car-compact`-is-not-a-real-glyph landmine this fix exists to avoid |
| `docs/change-log/2026-09-05-rider-app-vehicle-icon-fallback.md` | New file (this log) | Required for any behavior change on a live-tested rider-facing surface |

## 7. Before / after

```tsx
// Before
{estimate.vehicle_type.image_url ? (
  <ExpoImage source={{ uri: estimate.vehicle_type.image_url }} style={styles.carImage}
    contentFit="contain" cachePolicy="disk" />
) : (
  <View style={styles.carIconFallback}>
    <Ionicons name="car" size={isSelected && isAvailable ? 60 : 42} color="#666" />
  </View>
)}
```

```tsx
// After
{estimate.vehicle_type.image_url ? (
  <ExpoImage source={{ uri: estimate.vehicle_type.image_url }} style={styles.carImage}
    contentFit="contain" cachePolicy="disk" />
) : (
  <View style={styles.carIconFallback}>
    <Ionicons
      name={vehicleTypeIconName(estimate.vehicle_type.icon)}
      size={isSelected && isAvailable ? 60 : 42}
      color="#666"
    />
  </View>
)}

// where:
const VEHICLE_TYPE_ICON_NAMES: Record<string, React.ComponentProps<typeof Ionicons>['name']> = {
  'car-compact': 'car',
  'car-sport': 'car-sport',
  bus: 'bus',
  'bus-outline': 'bus-outline',
};
function vehicleTypeIconName(icon?: string | null) {
  return (icon && VEHICLE_TYPE_ICON_NAMES[icon]) || 'car';
}
```

## 8. Rollback plan

Pure frontend, additive-only change — no migration, no feature flag, no
backend/API change. Revert is a plain `git revert` of this PR's commit(s);
the screen returns to always showing the generic car glyph, exactly as
before. No data cleanup needed since nothing is written or persisted by
this change.

## 9. Verification performed

- [x] Automated tests: extended `rider-app/__tests__/rideOptionsScreen.test.tsx`
      with 2 new tests (known-icon mapping; unrecognized-value fallback
      safety). Ran the full file (121/121 passed) and the **entire
      rider-app jest suite** (143 suites / 1973 tests, all passed).
- [x] `npx tsc --noEmit` — clean, no errors.
- [x] `npx eslint` on both changed files — 0 new errors/warnings (3
      pre-existing `react/display-name` errors on unrelated `jest.mock(...)`
      inline components in the test file, confirmed present before this
      change via `git stash` diff).
- [x] Verified the specific landmine this fix targets: checked the real
      Ionicons glyph map shipped in `node_modules/@expo/vector-icons`
      (`build/vendor/react-native-vector-icons/glyphmaps/Ionicons.json`) to
      confirm `car-compact` (the actual seeded value) is NOT a valid glyph
      name, and that `car`, `car-sport`, `bus`, `bus-outline` all are —
      this is why the fix uses a lookup map instead of passing `icon`
      straight into `<Ionicons name={...}>`.
- [x] Blast-radius grep performed: confirmed only one render site reads
      `vehicle_type.icon`/`image_url` in rider-app; no other screen affected.

### What was NOT verified

- Not run on a real device/simulator — this sandboxed session has no Expo
  runtime or device to visually confirm the glyphs render as expected;
  verified instead via the jest test asserting the exact `name` prop passed
  to the (mocked) `Ionicons` component, and by cross-checking the glyph
  names against the actual installed Ionicons glyph map file.
- rider-app has no automated visual-regression tooling at all (per
  CLAUDE.md) — this is a visually-invisible-to-tooling change, reasoned
  about via the glyph-map check above and the jest assertions, not
  screenshotted.
- Did not verify what fraction of real production `vehicle_types` rows
  currently have no `image_url`/`illustration_url` set — i.e. how often
  this fallback path is actually hit in production today. If every active
  type already has an uploaded illustration, this change has zero visible
  effect until a new type is added without one.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated to one render site;
      confirmed via grep).
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — UX effect (rider-facing, fallback-icon-only, not
      mid-session) is stated in §5.
