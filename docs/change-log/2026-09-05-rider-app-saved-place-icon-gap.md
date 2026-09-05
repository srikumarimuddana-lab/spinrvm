# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (session, on behalf of vikas@ngitservices.com) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | Found while auditing rider-app/driver-app for the same class of gap as `docs/change-log/2026-09-05-rider-app-vehicle-icon-fallback.md` and `2026-09-05-driver-app-vehicle-icon-fallback.md` |

## 1. Issue / gap identified

Two places in rider-app render a per-saved-address icon and both mishandle
the address's own `icon` field (persisted at save time — see
`saved-places.tsx`'s `handleSave`, `icon: selectedType.toLowerCase()`):

1. **`app/search-destination.tsx`**, the "Favourites" quick-list (every
   saved address other than one literally named "Home" or "Work") always
   rendered a hardcoded generic star icon — the exact same "always the
   same glyph regardless of the actual saved type" bug as the vehicle-type
   fallback fixed in the two prior PRs, just on a different data field.
2. **`app/saved-places.tsx`**'s own list (`renderPlace`) *did* vary the
   icon, but derived it by substring-matching the display **name** against
   known type keywords ("home", "work", "gym", "school") instead of
   reading the persisted `icon` field directly. This works for the common
   case (a rider leaves the name as the type, e.g. "Gym") but silently
   breaks the moment a rider renames it to something that doesn't contain
   the keyword (types "Gym", names it "Downtown Fitness Club" → no
   substring match → falls through to the generic "Other" star, even
   though the correct `icon: "gym"` was saved).

## 2. Root cause

Two independent, partially-correct implementations grew up around the
same `SavedAddress.icon` field instead of one shared source of truth:
`saved-places.tsx` implemented a name-substring heuristic (never reading
`icon`), and `search-destination.tsx` implemented nothing at all (always
star). Neither actually trusted the field the backend already populates
and returns.

## 3. Fix / remediation

Extracted the icon/color/background lookup into one shared module,
`rider-app/utils/savedPlaceIcon.ts`:
- `SAVED_PLACE_TYPES` — the same 5 type definitions (Home/Work/Gym/School/Other)
  previously local to `saved-places.tsx`, unchanged in content, just moved.
- `savedPlaceConfig(addr)` — looks up by the address's own `icon` field
  first (matching it against a type's lowercased `key`, since the
  persisted value is the type key lowercased, e.g. `"work"`, not the
  Ionicons glyph name `"briefcase"`); falls back to the legacy
  name-substring heuristic when `icon` is missing or unrecognized
  (covering rows saved before this fix, and rows created by the backend's
  legacy CSV import path — see §4); and finally falls back to "Other" if
  nothing matches, exactly as before.

Both screens now call this one function instead of each having (or
lacking) their own logic:
- `search-destination.tsx`'s Favourites row: replaced the hardcoded
  `Ionicons name="star"` / `'#FFF7ED'` / `'#F59E0B'` with
  `savedPlaceConfig(addr)`.
- `saved-places.tsx`: removed its local `PLACE_TYPES` array and
  `getPlaceConfig` function; both the type-picker chips (add form) and the
  list rows now use the shared `SAVED_PLACE_TYPES` / `savedPlaceConfig`.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two screens + one new shared util, all
  within rider-app.** Grepped the whole rider-app tree for every other
  reader of `savedAddresses`/`SavedAddress.icon` — only `saved-places.tsx`
  and `search-destination.tsx` render it; nothing else touches this field.
  driver-app has no equivalent saved-places feature at all (grepped for
  "favorite"/"favourite"/"saved place"/"saved address" — no matches).
- **Backward-compatible with the backend's second write path**:
  `backend/services/saved_address_import_service.py`'s legacy CSV import
  (`_TYPE_ICONS = {"home": "home", "work": "work"}`, default `"location"`
  for anything else) writes an `icon` value that doesn't match any of this
  app's 5 type keys except "home"/"work" (which coincide). A row imported
  with `icon: "location"` simply falls through the icon-key check to the
  name-substring fallback — the exact same behavior these screens already
  had for such rows before this fix. No regression for that path.
- No backend, schema, or API change — this only changes which client
  logic reads an already-existing, already-returned field.
- Does not touch fare calculation, dispatch, ride state, or any money
  path — this is a decorative icon on an address-picker row.

## 5. User-experience effect

- **Rider-facing.** Visible on two screens: the "Search Destination"
  screen's Favourites quick-list, and the "Saved Places" management
  screen. A saved address whose display name doesn't literally contain
  its type keyword (a renamed Gym/School/Other entry) now shows the
  correct distinguishing icon instead of a generic fallback (star, in the
  Favourites list; potentially the wrong fallback in Saved Places).
  Addresses whose name already matches (the common case, e.g. "Home",
  "Work", "Gym") look identical to before — no visible change for them.
- **Visible mid-session?** Only the next time a rider opens either screen
  — not a live update to an already-open screen.
- No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/utils/savedPlaceIcon.ts` | New file — `SAVED_PLACE_TYPES` (moved from `saved-places.tsx`, unchanged) + new `savedPlaceConfig()` helper (icon-field-first, name-substring fallback) | Single shared source of truth for both screens, instead of two independently wrong/incomplete implementations |
| `rider-app/app/saved-places.tsx` | Removed local `PLACE_TYPES` + `getPlaceConfig`; both the type-picker and the list now import from the shared util | Read the persisted `icon` field instead of re-deriving it from the (possibly renamed) display name |
| `rider-app/app/search-destination.tsx` | Favourites row: replaced hardcoded `star`/`'#FFF7ED'`/`'#F59E0B'` with `savedPlaceConfig(addr)` | Stop showing the same generic star for every non-Home/Work saved address |
| `rider-app/__tests__/savedPlacesScreen.test.tsx` | Added `Ionicons` import; 2 new tests: icon-field-first lookup for a renamed address, and legacy name-fallback for a row with no `icon` | Regression coverage for the new shared lookup |
| `rider-app/__tests__/searchDestinationScreen.test.tsx` | Added `Ionicons` import; 1 new test: a Favourite's own icon renders instead of the hardcoded star | Regression coverage for the Favourites fix |
| `docs/change-log/2026-09-05-rider-app-saved-place-icon-gap.md` | New file (this log) | Required for a behavior change on a live-tested rider-facing surface |

## 7. Before / after

```tsx
// Before — search-destination.tsx Favourites row (always the same icon)
<View style={[styles.predictionIcon, { backgroundColor: '#FFF7ED' }]}>
  <Ionicons name="star" size={20} color="#F59E0B" />
</View>
```

```tsx
// After
const config = savedPlaceConfig(addr);
// ...
<View style={[styles.predictionIcon, { backgroundColor: config.bg }]}>
  <Ionicons name={config.icon} size={20} color={config.color} />
</View>
```

```tsx
// Before — saved-places.tsx (name-substring only, ignores the icon field)
const getPlaceConfig = (name: string) => {
  const lower = name?.toLowerCase() || '';
  return PLACE_TYPES.find(t => lower.includes(t.key.toLowerCase())) || PLACE_TYPES[PLACE_TYPES.length - 1];
};
// renderPlace: const config = getPlaceConfig(item.name);
```

```tsx
// After — reads item.icon first, name-substring only as a fallback
// renderPlace: const config = savedPlaceConfig(item);
```

## 8. Rollback plan

Pure frontend, additive-safe change — no migration, no feature flag, no
backend/API change. Revert is a plain `git revert` of this PR's commit(s);
both screens return to their prior behavior (name-substring-only in Saved
Places, always-star in Favourites) exactly as before.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean, no errors.
- [x] `npx eslint` on all 5 changed/added files — 0 new errors; pre-existing
      style warnings only (hardcoded hex colors / spacing, on lines this
      PR doesn't touch).
- [x] Added 3 new tests (2 in `savedPlacesScreen.test.tsx`, 1 in
      `searchDestinationScreen.test.tsx`) covering: icon-field-first lookup
      overriding a non-matching name, legacy name-substring fallback still
      working with no `icon` field, and the Favourites row using a real
      per-address icon instead of the hardcoded star.
- [x] Ran both directly affected test files (50/50 passed) and the
      **entire rider-app jest suite**: 143 suites / 1976 tests, all passed.
- [x] Blast-radius grep performed: confirmed only these two screens read
      `SavedAddress.icon`/render a per-address icon in rider-app; confirmed
      driver-app has no equivalent feature.
- [x] Checked the backend's second write path
      (`saved_address_import_service.py`'s legacy CSV import) to confirm
      its different icon-value scheme (`"location"` as a generic default)
      degrades gracefully through the same fallback chain, not silently
      wrong.

### What was NOT verified

- Not run on a real device/simulator — no Expo runtime available in this
  sandboxed session.
- rider-app has no automated visual-regression tooling at all (per
  CLAUDE.md) — this is a visually-invisible-to-tooling change, reasoned
  about via the jest assertions and code review, not screenshotted.
- Did not verify what fraction of real production saved addresses
  currently have a renamed (non-keyword-matching) name vs. the default —
  i.e. how often the `saved-places.tsx` half of this bug was actually
  visible in production. The `search-destination.tsx` half (always-star)
  was visible for every single non-Home/Work favourite, unconditionally.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert, no data path).
- [x] Blast radius is stated, not assumed (isolated to two screens + one
      new shared util; confirmed via grep; driver-app confirmed to have no
      equivalent feature).
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — UX effect (rider-facing, icon-only, not
      mid-session) is stated in §5.
