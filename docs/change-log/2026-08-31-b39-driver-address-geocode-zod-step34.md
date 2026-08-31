# Change Impact & Risk Log — B39 step 34: driver-app saved-address + destination-mode geocode validation (final B39 item)

- **Issue/gap identified:** `driver-app/app/driver/addresses.tsx`'s
  `handleAddAddress` and `driver-app/app/driver/destination-mode.tsx`'s
  `handleSave` both validated an address form the same way (fields
  required; the geocoded result must resolve) via inline checks with no
  dedicated test coverage — the sixth and **final** driver-app candidate
  from the 2026-08-31 broader B39 sweep (`ACTION_ITEMS.md` B39,
  "saved-address + geocode-failure gates — same shape, likely one shared
  extraction"). This closes the entire 21-candidate broader sweep.
- **Root cause:** no schema-validation library was adopted on this
  surface; each screen re-implements its own validation inline.
- **Fix/remediation:** extracted the shared *predicates* into new
  `driver-app/utils/addressGeocodeSchema.ts` —
  `isAddressNameAndAddressValid` (addresses.tsx's two-field check),
  `isAddressInputValid` (destination-mode.tsx's single-field check), and
  `isGeocodeResultValid` (the geocode-failure gate, identical shape on
  both screens). Each screen's `showToast(...)` call and its copy stay
  exactly where they were — `addresses.tsx` uses literal English
  strings, `destination-mode.tsx` uses this app's i18n `t(...)` keys;
  forcing those two copy sources into one shared error-returning
  function (the pattern used in several earlier B39 steps) would have
  either lost `destination-mode.tsx`'s i18n or fabricated i18n keys
  `addresses.tsx` never had — either is a behavior change, not a pure
  extraction. Sharing only the boolean predicates, not the copy, is the
  "one shared extraction" this item's own note anticipated without
  crossing that line. Pure extraction — byte-for-byte identical
  accept/reject behavior and identical toast copy on both screens; no
  validation-rule change. Non-null assertions (`coords!.lat`,
  `coords!.lng`) were added at the four `api.post(...)`/`setState(...)`
  call sites (two per screen) that previously relied on TypeScript's
  control-flow narrowing from the removed inline `if (!coords)` checks —
  the schema call guarantees the same non-null invariant, TS just can't
  see through the extracted function, so this is a type-only change, not
  a runtime one (same pattern as step 20's `create-ride-modal.tsx`
  extraction).
- **Risk & impact on existing functionality:** all three new functions
  are colocated in `driver-app/utils/addressGeocodeSchema.ts` and used
  only by these two screens' two handlers. Blast-radius grep for the
  two distinctive `addresses.tsx` toast strings found only the two
  (untouched) call sites still using them directly — isolated.
- **User experience effect:** none. Same fields, same order, same toast
  copy on both screens.
- **Files modified:**

  | File | What changed | Why |
  |---|---|---|
  | `driver-app/utils/addressGeocodeSchema.ts` | New file: three shared predicate functions | Extract validation logic |
  | `driver-app/utils/__tests__/addressGeocodeSchema.test.ts` | New file: 9 accept/reject tests | Close the coverage gap this item names |
  | `driver-app/app/driver/addresses.tsx` | `handleAddAddress`'s two inline checks replaced with the shared predicates; non-null assertions added at the two `lat`/`lng` payload fields; added import | Pure extraction |
  | `driver-app/app/driver/destination-mode.tsx` | `handleSave`'s two inline checks replaced with the shared predicates; non-null assertions added at the four `lat`/`lng` payload/state fields; added import | Pure extraction |

- **Before/after snippet:**

  ```tsx
  // before (addresses.tsx handleAddAddress)
  if (!newAddress.name.trim() || !newAddress.address.trim()) {
      showToast('error', 'Missing Fields', 'Please fill in both fields');
      return;
  }
  const coords = await geocodeAddress(newAddress.address.trim());
  if (!coords) {
      showToast('warning', 'Address not found', '...');
      return;
  }
  await api.post('/addresses', { ..., lat: coords.lat, lng: coords.lng, ... });

  // after
  if (!isAddressNameAndAddressValid(newAddress.name, newAddress.address)) {
      showToast('error', 'Missing Fields', 'Please fill in both fields');
      return;
  }
  const coords = await geocodeAddress(newAddress.address.trim());
  if (!isGeocodeResultValid(coords)) {
      showToast('warning', 'Address not found', '...');
      return;
  }
  await api.post('/addresses', { ..., lat: coords!.lat, lng: coords!.lng, ... });
  ```

- **Rollback plan:** revert the commit — no data migration, no feature
  flag, no live-data mutation involved; this is a client-side pure
  refactor.
- **Verification performed:** 9/9 new tests pass
  (`npx jest utils/__tests__/addressGeocodeSchema.test.ts`); full
  driver-app suite (`npx jest`) 126/126 suites, 1418/1418 tests passing,
  0 regressions; `npx tsc --noEmit` clean (repo-wide, after adding the
  four non-null assertions); `npx eslint` on touched files: 0 errors, 0
  warnings; **real production build** (`npm run build:web` → `expo
  export --platform web`) completed successfully. Blast-radius grep
  confirmed no other file uses `addresses.tsx`'s distinctive toast
  strings beyond their own untouched call sites.
- **What was NOT verified:** no manual click-through of either screen in
  a running app against a live/staging Supabase (or the real Places
  geocoding API) — verified via unit tests, `tsc`, `eslint`, and a
  production web build only; driver-app has no active visual-regression
  tooling (CLAUDE.md pre-merge gate #6), so this visually-invisible,
  logic-only change was reasoned about, not screenshotted.

**This closes the entire 21-candidate B39 broader sweep** (steps
15-34, spanning rider-app, driver-app, and admin-dashboard). No
candidates remain from the 2026-08-31 sweep. Remaining open work on
B39 itself: no ADR/migration-order doc has been written for the overall
multi-step migration (a standing gap this item's own status notes have
carried since step 1); the item's own scope was always "migrate one
form at a time," not "migrate every form in the codebase" — forms
outside the 21-candidate sweep (if any exist) were not exhaustively
enumerated beyond that sweep's own methodology.
