# Change Impact & Risk Log — saved addresses (Home / Work / Saved) fix

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code agent session (owner-approved scope, owner decisions 2026-09-25) |
| Surface(s) | backend / rider-app / driver-app |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/fix-saved-addresses` (local commits, not pushed) |
| Related issue or gap ID | Saved-addresses audit 2026-09-25 (defects 1–7); builds on ACTION_ITEMS.md B9 (place_id capture) |

## 1. Issue / gap identified

The rider saved/favourite address feature did not work as intended: the home-screen Home/Work/Saved buttons ignored which one was tapped, Home/Work were only recognised by an exact label, there was no edit and no one-Home rule, `place_id` never travelled end to end, logout left the previous rider's saved places in memory, and several failures were silent. Production: 264 `saved_addresses` rows, all from the legacy import (Home 138 / Work 60 / "Saved Address" 66), all `place_id` NULL, 0 duplicate labels per rider.

## 2. Root cause

1. `rider-app/app/(tabs)/index.tsx` `handleQuickAction = (_type) => openNewSearch()` — a stub, locked in by `homeScreen.test.tsx`.
2. `search-destination.tsx` matched Home/Work by `name.toLowerCase() === 'home'`, while `saved-places.tsx` stores the type in `icon` separately from the free-text label.
3. `backend/routes/addresses.py` had GET/POST/DELETE only, GET unordered, no single-Home rule.
4. Saved-place taps passed only `{address, lat, lng}`; the `SavedAddress` type had no `place_id`; the add form dropped the autocomplete `place_id`; `SavedAddressCreate` did not accept one. (The backend already stores a server-side `place_id` from the B9 geocode-verify call — reused, not duplicated.)
5. The logout callback in `rideStore.ts` did not clear `savedAddresses`; `fetchSavedAddresses` kept the old list on failure and had no `Array.isArray` guard.
6. Delete failure set a store `error` nothing displays; `catch {}` on the place lookup; load failure rendered "No saved places yet".
7. `SavedAddressCreate` had no lat/lng range validation and accepted any `icon` string.

## 3. Fix / remediation

**Backend**
- `POST /addresses`: when the new place is Home or Work (type from `icon`; exact label only for an untyped `location`/missing icon — same rule as the rider app), it **replaces** the rider's existing Home/Work **in place (same id)** with one user-scoped filtered `update_one({user_id, icon})`; any leftover duplicates are removed with one filtered `delete_many({user_id, icon, id != kept})`. No read-then-write. First Home/Work, and every other type, inserts. A Home/Work row's `icon` is normalised to its type so the next save finds it.
- New `PATCH /addresses/{id}` — owner-scoped lookup (404 for anyone else's row), edits name/address/lat/lng/icon/place_id; address/lat/lng must change together and are re-verified (B9). Turning a row into Home/Work drops the rider's other Home/Work.
- `GET` ordered by `created_at`.
- `lat` −90..90, `lng` −180..180 (422); `icon` restricted to `home|work|gym|school|other|location` (case-insensitive); optional `place_id` accepted — the server's own geocode `place_id` still wins, the client's is the fallback.
- The address/location-mismatch 400 no longer echoes the address.
- `GET /settings` exposes `saved_place_shortcuts_enabled` (default `true`).

**driver-app** — the driver Addresses screen sent `icon: 'home'` for every save; it now sends the untyped `location` icon, so the new one-Home rule doesn't make each driver save overwrite the last one (see §4).

**rider-app**
- Home/Work/Saved buttons (owner decision): Home/Work with a saved place → reset the draft, pre-fill the drop-off, open search (rider still confirms pickup); not saved → toast + Saved Places; Saved → Saved Places. With a live ride in the store the draft is never overwritten (falls back to opening search). Gated on `SavedPlaceShortcutsEnabledContext` (from `/settings`, default true).
- `savedPlaceType` / `isHomePlace` / `isWorkPlace` in `utils/savedPlaceIcon.ts` (type first, exact-label fallback) used by the search screen, home screen and store.
- `place_id` on the `SavedAddress` type, sent on save and on every saved-place tap, so `handleSelectLocation` re-resolves fresh coordinates.
- Store: logout clears `savedAddresses`; non-array GET body or failure clears the list and sets `savedAddressesLoadFailed`; add/update merge by id and drop the other local Home/Work; new `updateSavedAddress`; `deleteSavedAddress` rethrows.
- Saved Places: load-failure state with Retry; toasts for delete failure, a suggestion without coordinates, and a failed lookup; label follows the type chip while empty or still the previous type's name; "Home Updated" toast when a save replaces the existing one; Edit (pencil) action using the new PATCH.
- `e2e/fixtures.ts` mocks the real `/addresses` path.

### Alternative considered (release gate 10)

Delete-then-insert for the replace was rejected in favour of update-in-place: it keeps the row id (nothing else references `saved_addresses.id` today, but the rider app's local list does between fetches) and is one statement for the common case. A DB partial unique index `(user_id, icon) WHERE icon IN ('home','work')` would make it fully race-proof; it is deliberately **not** in this change (instructions: no migration) — follow-up below.

## 4. Risk & impact on existing functionality

Blast radius: **cross-surface** (backend + rider-app + driver-app), no ride state machine, money, dispatch or insurance-period interaction.

Every reader/writer of `saved_addresses` / `/addresses` (grep of `saved_addresses`, `'/addresses'`, `SavedAddressCreate`):
- `backend/routes/addresses.py` — changed here.
- `driver-app/app/driver/addresses.tsx` — **second client of POST /addresses**. It hard-coded `icon: 'home'`, so under the new rule every driver save would have replaced the previous one. Fixed in this change (sends `location`), but **driver-app builds already in the field keep sending `home` until they get the EAS update**: on those builds a driver can only keep one saved address (each save updates the previous row in place). Low impact — that screen's "use address" action is a stub and it was only made reachable in #5317; production has no app-created rows at all — but it is a real, time-boxed regression for old driver builds. Escalation: owner should decide whether to ship the driver-app update with/before the backend.
- `frontend/store/rideStore.ts` — deprecated surface, not touched.
- `backend/ai/tools_account.py:get_saved_places` — reads name/address/lat/lng only; unaffected (now sees one Home instead of possibly several).
- `backend/routes/users.py` (account deletion `delete_many` by user_id), `backend/services/saved_address_import_service.py`, `backend/routes/admin/legacy_saved_address_backfill.py`, `backend/services/migration_status_service.py`, `backend/routes/drivers/tax_exports.py` (data export) — not changed; they write/read rows directly, not through the route. The import writes `icon` `home`/`work`/`location`, all inside the new allowed set.
- Legacy rows: all 264 have `icon` `home`/`work`/`location`; the new validation only applies to writes, so existing rows are untouched until a rider next saves/edits.
- Validation now **rejects previously-accepted input**: a POST with an out-of-range lat/lng or an unknown icon now gets 422 instead of being stored. Both first-party clients only send allowed icons and geocoded coordinates.
- Race: two concurrent *first* Home saves can both insert (no unique constraint); the next Home save collapses them (update both + delete extras). Acceptable per instructions; unique index is the follow-up.
- rider-app shared pieces: `utils/savedPlaceIcon.ts` is imported by `saved-places.tsx`, `search-destination.tsx`, now `(tabs)/index.tsx` and `store/rideStore.ts` — additive exports only. `_layout.tsx` gains one context/provider; every test that mocks `../app/_layout` and renders the home screen was updated (only `homeScreen.test.tsx` renders it).
- `fetchSavedAddresses` now **clears** the list on a failed fetch (privacy over staleness): a transient network error makes the search-screen Home/Work chips read "not set" until the next successful fetch.
- `deleteSavedAddress` now throws; its only caller (`saved-places.tsx`) catches and toasts.

## 5. User-experience effect

Rider-visible (only after the rider-app EAS update; backend changes are invisible to old app builds except as noted):
- Home / Work buttons on the home screen now take the rider to booking with that place as drop-off (pickup still confirmed on the next screens); if not saved, a short "Add your home/work" toast and Saved Places. Saved opens Saved Places. Previously all three just opened an empty search.
- A place typed Home/Work shows on the Home/Work chips regardless of its label.
- Saving a second Home/Work replaces the first (toast "Home Updated — Your home address was replaced."). **This also applies to old rider-app builds** as soon as the backend deploys: a second Home saved from an old build replaces the first instead of adding a duplicate — the old build shows both until the list refreshes (next Saved Places open / 5-minute home refresh).
- Saved places can be edited (pencil).
- Errors are now visible: delete failure, lookup failure, no-coordinates suggestion, and a load-failure state with Retry instead of "No saved places yet".
- Logging out and in as another rider no longer shows the previous rider's saved places.
- Driver (after driver-app update): no visible change; old driver builds — see §4.
- Mid-session: a rider already on the home screen when the update lands sees the new button behaviour on next app start; nothing changes mid-ride (live-ride draft is never touched).
- New copy: "Add your home/work", "Save your home address to book it in one tap.", "Home/Work Updated", "Couldn't load your saved places / Check your connection and try again. / Retry", "Location Unavailable", "Remove Failed" — plain, specific, actionable.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/addresses.py` | Home/Work replace-in-place on POST, new PATCH, GET ordering, fixed mismatch message | Defects 3, 4, 7 + no address echo |
| `backend/schemas.py` | `SavedAddressCreate` lat/lng ranges, icon allow-list, `place_id`; new `SavedAddressUpdate` | Defects 4, 7 |
| `backend/tests/test_saved_addresses_home_work.py` | New: replace-on-second-Home, PATCH owner/404, ordering, 422s, place_id, mismatch message | Tests for the above, on `mock_supabase_client` |
| `backend/routes/settings.py` | `saved_place_shortcuts_enabled` on public `/settings` (default true) | Kill switch for the home-screen change |
| `backend/tests/test_public_settings_saved_place_shortcuts.py` | New | Flag default/overrides |
| `driver-app/app/driver/addresses.tsx` | Save with `icon: 'location'` instead of `'home'` | Avoid driver saves replacing each other under the one-Home rule |
| `driver-app/__tests__/app/addressesScreen.test.tsx` | Expectation updated | — |
| `rider-app/utils/savedPlaceIcon.ts` | `savedPlaceType`, `isHomePlace`, `isWorkPlace` | Defect 2 |
| `rider-app/store/rideStore.ts` | `place_id` type, `updateSavedAddress`, merge rule, isArray guard, clear-on-failure + `savedAddressesLoadFailed`, logout clear, delete rethrow | Defects 4, 5, 6 |
| `rider-app/__tests__/rideStore-savedAddresses.test.ts` | New | Logout clear, non-array body, failure flag, replace merge, delete rethrow, helper rule |
| `rider-app/app/search-destination.tsx` | Type-based Home/Work, `place_id` on every saved-place tap | Defects 2, 4 |
| `rider-app/__tests__/searchDestinationScreen.test.tsx` | "Downtown Office"/work row is now the Work chip; "My house" Home chip; place_id re-resolve | Defects 2, 4 |
| `rider-app/app/saved-places.tsx` | Error/Retry state, toasts, place_id on save, label/chip sync, replace toast, Edit action | Defects 2, 4, 6 + edit |
| `rider-app/__tests__/savedPlacesScreen.test.tsx` | New cases for each of the above | — |
| `rider-app/app/_layout.tsx` | `SavedPlaceShortcutsEnabledContext` from `/settings` | Flag plumbing |
| `rider-app/app/(tabs)/index.tsx` | `handleQuickAction` per owner decision | Defect 1 |
| `rider-app/__tests__/homeScreen.test.tsx` | Stub-locking test replaced with routing tests (with/without saved Home/Work, live ride, flag off) | Defect 1 |
| `rider-app/e2e/fixtures.ts` | Mock real `/addresses` path (bare list) | Was falling through to `{}` |

No path in this change matches `scripts/check_change_impact.py`'s sensitive markers (`backend/routes/addresses.py` and `backend/routes/settings.py` are not listed); this log is written anyway because the change is rider-visible on a live-tested flow.

## 7. Before / after

```ts
// Before — rider-app/app/(tabs)/index.tsx
const handleQuickAction = (_type: string) => {
  openNewSearch();
};
```

```ts
// After (abridged)
const handleQuickAction = (type: string) => {
  if (!savedPlaceShortcutsEnabled) { openNewSearch(); return; }
  if (type === 'saved') { router.push('/saved-places'); return; }
  const place = (savedAddresses ?? []).find(type === 'home' ? isHomePlace : isWorkPlace);
  if (!place) { showToast(...); router.push('/saved-places'); return; }
  if (currentRide) { openNewSearch(); return; }
  resetBookingDraft();
  setDropoff({ address: place.address, lat: place.lat, lng: place.lng, ...(place.place_id ? { place_id: place.place_id } : {}) });
  router.push('/search-destination');
};
```

```python
# Before — backend/routes/addresses.py POST
await db_supabase.insert_one("saved_addresses", address.dict())   # every save inserts
raise HTTPException(400, detail=f"Address and location don't match: {mismatch_reason}")  # reason quotes the address

# After (abridged)
if place_type:  # "home" / "work"
    updated = await db_supabase.update_one("saved_addresses", {"user_id": uid, "icon": place_type}, fields)
    if updated:
        await db_supabase.delete_many("saved_addresses", {"user_id": uid, "icon": place_type, "id": {"$ne": updated["id"]}})
        return updated
await db_supabase.insert_one("saved_addresses", doc)
raise HTTPException(400, detail="Address and location don't match. Please search for the address again and re-select it.")
```

Concrete scenario: rider has Home (id h1, "1 Test St"). Before: saving Home "3 Test St" inserts h2 → two Homes, search chip picks whichever `find` hits first. After: h1 is updated to "3 Test St" (same id), no second row; the app shows "Home Updated".

## 8. Rollback plan

- **Home-screen buttons**: `saved_place_shortcuts_enabled=false` restores the old open-search behaviour on every updated rider build. **Caveat:** there is no `settings` column for it yet (no migration in this change), so today flipping it needs a one-line backend deploy (change the default in `routes/settings.py`) — no app release needed. Follow-up: migration adding `settings.saved_place_shortcuts_enabled BOOLEAN NOT NULL DEFAULT true` + admin PATCH field makes it a no-deploy toggle.
- **Backend replace/PATCH/validation**: code revert + backend redeploy. Data note: a revert does **not** restore a Home/Work row that a replace already overwrote — the previous address is gone (same id, new values). No backup of the old value is kept; that is inherent in the owner's "replace" decision. Prod has 0 duplicate Home/Work today, so the collapse-duplicates delete has nothing to remove on existing data.
- **rider-app / driver-app**: EAS Update rollback to the previous update group, or ship the flag-off default above.

## 9. Verification performed

- [x] `cd backend && python -m pytest -q -p no:cacheprovider --no-cov --ignore=tests/rls -k "address or favorite or favourite or saved"` → **228 passed, 1 skipped** (17088 deselected).
- [x] New `backend/tests/test_saved_addresses_home_work.py` runs the real route → `db_supabase` → `repositories/_base.py` filter compilation on conftest's `mock_supabase_client` (asserts the `eq(user_id)`, `eq(icon)`, `neq(id)` chain), plus existing `test_p3_addresses_favorites_safety_disputes.py` (unchanged, green).
- [x] Settings tests: `test_public_settings*.py`, `test_utils_extended.py`, `test_min_tip_policy.py`, `test_posthog_settings_flag.py`, `test_admin_settings_write_allowlist_drift.py` → green.
- [x] `ruff check` / `ruff format --check` on every changed `.py` → clean.
- [x] rider-app jest: `homeScreen` (49), `searchDestinationScreen` + `searchDestinationPinIntegrity` (50), `savedPlacesScreen` (21), `rideStore-savedAddresses` (9), `rideStore-recentSearches` + `rideStore-samePlace`, `BrandSplash`, `rideInProgressScreen`, `walletScreen` → all green.
- [x] driver-app jest: `addressesScreen` (12) → green.
- [x] rider-app `npx tsc --noEmit` → only error is pre-existing and unrelated (`shared/auth/refreshProposal.ts`: cannot find module `expo-crypto` in this environment's installed deps); no errors in touched files.
- [x] eslint on touched rider-app files: 0 new errors (one pre-existing `react/display-name` on the ConfirmSheet mock in `savedPlacesScreen.test.tsx`).
- [x] Blast-radius grep: `saved_addresses`, `'/addresses'`, `SavedAddressCreate`, `savedPlaceIcon`, `app/_layout` test mocks.
- [x] PIPEDA: no new logging of addresses/coordinates; the mismatch 400 no longer echoes the address. Test fixtures use synthetic addresses.
- [x] Feature-flagged: home-screen change behind `saved_place_shortcuts_enabled` (default on — it repairs a broken button). Backend one-Home rule is not flagged (owner decision; see rollback).

## 10. What was NOT verified

- No production build (`eas build` / `expo export`) was run for rider-app or driver-app — only jest and `tsc --noEmit`.
- **rider-app and driver-app have no visual regression tooling**; the new Retry state, pencil icon and toasts were reasoned about, not screenshotted or run on a device/simulator.
- Not tested against live Supabase/PostgREST — only `mock_supabase_client`. In particular, the `update_one` with `{user_id, icon}` returning the updated row relies on PostgREST's default `return=representation` (same as every other `update_one` caller).
- No staging check; no concurrency test of two simultaneous first-Home saves (known gap without a unique index).
- Behaviour of old rider/driver builds against the new backend was reasoned about from their code, not run.
- No `spinr-*` reviewer agent pass was run from this session (no Agent tool available to it) — recommended before merge.

## Rollout

- Backend changes deploy with the next backend deploy (Fly/Railway) and take effect immediately for all clients, including old builds (one-Home replace, validation, PATCH available).
- rider-app and driver-app changes reach users only via an **EAS Update / new build**. Recommended order: driver-app update first or together with the backend (see §4 old-driver-build caveat), then rider-app.

## Follow-ups (out of scope)

- Migration: partial unique index on `saved_addresses (user_id, icon) WHERE icon IN ('home','work')` (race-proof one-Home) — check for existing duplicates first (prod: 0).
- Migration + admin field for `settings.saved_place_shortcuts_enabled` so the kill switch needs no deploy.
- **PIPEDA, shared code:** `backend/validators.py` `sanitize_string` logs up to 80 characters of input when it contains `#` — addresses like "Unit #4 …" land in logs. Used by addresses, favorites and other routes; needs its own change.
- `backend/routes/favorites.py` returns the same address-echoing mismatch reason in its 400 — same fix as here, not touched.
- The home-screen Home/Work shortcut pre-fills the stored coordinates without the `place_id` re-lookup that `handleSelectLocation` does on the search screen (all prod rows have `place_id` NULL today, so no practical difference yet).

## Sign-off

- [x] Rollback plan is concrete and testable (with the no-column caveat stated)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
