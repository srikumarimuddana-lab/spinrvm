# Change Impact & Risk Log — French complete, Spanish/Chinese hidden (W7.1)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent), for UX program item W7.1 |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides / drivers (UI copy and settings only; no backend, money or state-machine change) |
| PR / commit link | W7.1 part 1: #5893 (merged). Part 2: branch `claude/spinr-animations-admin-ux-x7nl5x` (this PR). |
| Related issue or gap ID | W7.1, decision D3: finish French to 100% of the keys the apps use; hide `es` and `zh` from the language pickers until they are complete. Extended by the user to also restore the saved language at app start in both apps and translate the rider picker title (§3, items 6–8). |

> **[H] Human action required before release:** every French string added here was
> drafted by an agent. A fluent French speaker, ideally one familiar with Canadian
> (fr-CA) usage, must review the 77 new strings listed in §6 before this ships to
> real users. The keys are all `errors.*` (driver-app: 36; rider-app `fr.json`: 37)
> plus 4 policy-row labels (rider-app `fr-CA.json`).

## 1. Issue / gap identified

French was incomplete in both apps, and the language pickers offered Spanish (both
apps) and Chinese (rider-app) even though those translations cover well under half
the keys. In some places the missing keys showed English, and in others they showed
raw dotted keys. On top of that, neither app reliably restored the saved language at
start-up, and the rider picker title was hardcoded English (§2, last bullets).

## 2. Root cause

Which files each app actually loads at runtime (from `*/i18n/index.ts`):

- **driver-app** loads `en.json`, `fr.json` and `es.json`. `translate()` falls back to
  English for a missing key, but `tKey()` (used by `lib/alert.ts`, `app/otp.tsx` and
  `hooks/useDriverDashboard.ts` for structured API errors) does **not**. It returns the
  backend's English message or the raw key. `fr.json` had none of the 36 nested
  `errors.{auth,driver,ride,payment,wallet,system}.*` keys.
- **rider-app** loads six files and resolves each language through a chain:
  `en → [en-CA, en]`, `fr → [fr-CA, fr]`, `es → [es, en-CA]`, `zh → [zh, en-CA]`.
  The English source of truth is therefore the union of `en-CA.json` (159 snake_case
  keys for the main screens) and `en.json` (106 camelCase keys plus the `errors.*` tree).
  That union is 249 distinct keys, 16 of which appear in both files with identical values.
  French was missing 41 of them:
  - 4 in `fr-CA.json`: `privacy.terms_of_service(_subtitle)` and
    `settings.more_policies(_subtitle)`. These are used by `app/privacy-settings.tsx`
    and `app/settings.tsx`. The French chain has no English fallback, so French riders
    saw the literal key (e.g. `settings.more_policies`) on those rows.
  - 37 in `fr.json`: the whole `errors.*` tree. `tKey()` fell back to the backend's
    English message.
- **es/zh** in rider-app fall back only to `en-CA.json`, never to `en.json`, so all 37
  `errors.*` keys can never resolve for them at all.
- **Device locale:** neither app auto-detects it. There is no `expo-localization` or
  `getLocales` use. Both default to `'en'`, so the only way onto `es`/`zh` was a choice
  the user made earlier in the picker and that was saved in AsyncStorage.
- **Saved language not restored at start-up (rider):** `useLanguageStore.hydrate()`, the
  only code that reads the saved choice back, had **no caller**. Every cold start was
  English, even for a rider who had picked French.
- **Saved language restored late (driver):** `loadLanguage()` was called only from
  `app/driver/settings.tsx`. A French driver saw English on login, OTP, the dashboard and
  in alerts after every cold start, until they happened to open Settings.
- **Rider picker title:** `app/settings.tsx` rendered the literal `"Select Language"`,
  even though a `settings.selectLanguage` key already existed in `en.json`/`fr.json`.

## 3. Fix / remediation

1. driver-app `fr.json`: added the 36 missing `errors.*` keys.
2. rider-app `fr.json`: added the 37 `errors.*` keys. rider-app `fr-CA.json`: added the
   4 policy-row keys.
3. Removed `es` (driver-app) and `es`/`zh` (rider-app) from the `languages` / `LANGUAGES`
   arrays that the settings pickers render. The locale files, the `Language` union
   members and the translation maps are untouched, so re-enabling a language later means
   adding one line back.
4. Stored-preference fallback: driver-app `getStoredLanguage()` and rider-app
   `hydrate()` now honour a stored code only if the picker still offers it. Otherwise
   they return or keep English. **Decision:** English is the only complete language
   besides French. A user left on `es`/`zh` would see a language the picker can no longer
   show as selected and could never re-select after switching away. In rider-app they
   would also see raw keys for errors. The AsyncStorage value is **not** overwritten, so
   if a language is re-enabled later, the user's old choice comes back without any
   further change.
5. Added a jest parity test per app (§9).
6. **Rider start-up restore:** `RootLayout` in `rider-app/app/_layout.tsx` now calls
   `useLanguageStore.getState().hydrate()` once, in a mount-only effect. The change is
   one line plus the import.
   - **English flash:** avoided in practice without a new gate. `hydrate()` is one
     AsyncStorage read, started on RootLayout's first render. No screen mounts until
     the existing `navReady` gate, which already holds the branded splash for at least
     `SPLASH_MIN_DISPLAY_MS` (1.8 s).
   - **Why not gate on it:** I deliberately did **not** add `hydrate()` to `navReady`.
     That would put a storage read on the boot-critical path, and a hung read would
     leave the rider stuck on the splash, for no practical gain over the existing hold.
     The residual risk is a brief English flash if the read takes longer than 1.8 s.
7. **Driver start-up restore:** `driver-app/i18n/index.ts` now calls the store's
   existing `loadLanguage()` once, when the module first loads.
   - **Why not `_layout.tsx`:** that file is owned by another queued PR, so I left it
     untouched. No hook or provider already mounted at the root was a fit either:
     `useSplashPhase` is a pure timing hook with its own tests, and
     `store/languageStore.ts` was off-limits.
   - **Why this runs in time:** the root layout does not import i18n, but every
     translated screen and `lib/alert.ts` do. So the restore starts before the first
     translated string renders. That first screen is still under the launch splash for
     its 250 ms settle (`SPLASH_EXIT_SETTLE_MS`) while the single AsyncStorage read
     completes.
   - **Implementation detail:** the call is deferred to a microtask, because
     `store/languageStore.ts` imports i18n.
   - **Residual risk:** in theory, the very first frame of the first translated screen
     is English. In practice it is covered by the splash, not guaranteed by a gate.
8. **Rider picker title:** `app/settings.tsx` now renders `t('settings.selectLanguage')`.
   This shows "Choisir la langue" in French. English is unchanged ("Select Language").
9. **Load-vs-pick race (edge-case review, reproduced in both apps).**
   - **The bug:** the saved-language restore is asynchronous (driver `loadLanguage()`,
     rider `hydrate()`) and used to write its result unconditionally. If the user picked
     a language while the storage read was still in flight, the late read overwrote the
     fresh choice in memory. The stored value was still correct, so the next start fixed
     it. In the driver app, the Settings-mount re-read made this reachable on every
     Settings visit, not only at start-up.
   - **The fix:** each store now has a `hasUserChosen` flag. `setLanguage()` sets it when
     it accepts a pick, and the restore drops its result when the flag is set. A pick
     made at any point in the session always wins over a restore that finishes later.
   - **Why a flag, not a sequence counter:** a counter only protects restores that
     *started* before the pick. A restore that starts after a pick but before the pick's
     own write lands would still read the old value. The flag covers both orderings.
   - **A refused pick does not set the flag** (item 10), so it cannot block the restore.
10. **Write-side guard.** `setLanguage()` in both stores now refuses any code the picker
    list does not offer (`languages` / `LANGUAGES`). It keeps the current language, does
    not write storage, and warns in dev only.
    - **Dev-only warning:** `if (__DEV__) console.warn(...)` is the existing pattern in
      stores and hooks (`rider-app/store/rideStore.ts`, `driver-app/hooks/useRideOfferSound.ts`).
    - **Compat shims:** rider-app's two `changeLanguage()` shims still map
      `es`/`zh`/`zh-CN`. They route through `setLanguage()`, so they are refused too,
      with one enforcement point.
11. **Driver Settings-mount re-read removed.** `app/driver/settings.tsx` no longer calls
    `loadLanguage()` on mount, so `loadLanguage()` now has one caller, the app-start
    restore.
    - **Why the start-up restore fully covers it:** `settings.tsx` imports the store,
      which imports i18n. Any path to Settings has therefore already started the restore.
      The only writer of the stored value is `setLanguage()`, which also sets it in
      memory. So the mount call could only re-read the same value, and it was a second
      source of the race.
    - **One behaviour lost:** if the start-up read fails (now logged), opening Settings no
      longer retries it. The driver stays on English and can re-pick.
12. **Silent catches now log.**
    - **Rider:** `hydrate()` logs a read failure with `console.error` instead of
      `catch {}`. Its fire-and-forget `setItem` catch now logs too.
    - **Driver:** `getStoredLanguage()`'s read failure also logs now. It was silent as
      well, so neither side was logging before. This matches the `console.error` that
      driver `setStoredLanguage()` already used.

Alternative considered: keep es/zh in the list with a `selectable: false` flag and
filter in each picker screen. This was rejected because it adds a field and a filter in
two screen files for no behavioural gain. Removing the rows keeps the picker screens
untouched, and the parity test gates re-adding a language.

Alternative considered for item 7: a one-line `loadLanguage()` effect in
`driver-app/app/_layout.tsx`, mirroring the rider change. This would be deterministic and
earlier. It was rejected only because another queued PR owns that file. If that PR lands
first, moving the call there is a safe follow-up.

## 4. Risk & impact on existing functionality

Blast radius: **two surfaces, i18n only**. There are no backend, state-machine, money or
insurance paths.

- `languages` (driver-app) has exactly one consumer: `app/driver/settings.tsx`, for the
  picker list and the row subtitle. `LANGUAGES` (rider-app) has exactly one consumer:
  `app/settings.tsx`, for the picker list and the subtitle. Both already fall back to
  `'English'` when a code is missing from the list.
  - **Driver screen:** its only change is dropping the mount-time `loadLanguage()`
    (item 11).
  - **Rider screen:** its only change is the picker title (item 8).
- **Store callers:** `getStoredLanguage()` is called only by `store/languageStore.ts` →
  `loadLanguage()`, and `loadLanguage()` now has exactly one caller, the start-up call in
  `i18n/index.ts`. `hydrate()` (rider-app) has one caller, `RootLayout`.
  - **`setLanguage()` callers (both stores):** only the picker rows in each app's
    settings screen, and in rider-app the two `changeLanguage()` compat shims, which
    have no callers in app code.
  - **Why the guard is safe:** the picker only offers en/fr, so the new write-side guard
    cannot refuse anything a user can actually tap.
  - **`hasUserChosen`:** a new state field that is only read inside each store. The
    tests that stub the stores replace them wholesale, so they need no change.
- **Every screen affected at start-up.** The restored language now applies from the
  first frame to every consumer of each app's language store, not only after a visit to
  Settings (driver) or never (rider). Users with no saved language, or with English
  saved, see no change.
  - **driver-app:** `app/login.tsx`, `app/otp.tsx`, `app/legacy-consent-notice.tsx`,
    `app/driver/(tabs)/index.tsx`, `app/driver/(tabs)/activity.tsx`,
    `app/driver/destination-mode.tsx`, `app/driver/emergency-contacts.tsx`,
    `app/driver/notifications.tsx` and `app/driver/settings.tsx`.
  - **driver-app components:** `components/DestinationModeBanner.tsx` and the dashboard
    panels (`ActiveRidePanel`, `DemandLegend`, `DriverIdlePanel`, `DriverTopBar`,
    `ForecastStrip`, `HotspotChips`, `TripCompletedPanel`).
  - **driver-app non-UI:** `hooks/useDriverDashboard.ts` and every `lib/alert.ts` error
    alert (`tKey`). The shared `SOSButton`, which receives driver-app's `t`, is also
    affected.
  - **rider-app:** `app/(tabs)/index.tsx`, `app/(tabs)/activity.tsx`,
    `app/driver-arriving.tsx`, `app/driver-arrived.tsx`, `app/ride-in-progress.tsx`,
    `app/otp.tsx`, `app/verify-email.tsx`, `app/legacy-consent-notice.tsx`,
    `app/privacy-settings.tsx` and `app/settings.tsx`.
  - **rider-app components and non-UI:** `components/NoDriversSheetHost.tsx` and every
    `lib/alert.ts` error alert (`tKey`).
- **Start-up side effect (driver):** `driver-app/i18n/index.ts` now does one
  AsyncStorage read when it is first imported. In jest this also runs in any test that
  imports the real module. The full suite logged 0 restore errors, and tests that stub
  the store never load the real i18n module.
- **Rider `_layout.tsx`:** the change is one import plus one mount-only effect. It does
  not touch `navReady`, the splash phases, auth or location init, or any other effect.
- `tKey()` / `translate()` / `t()` are unchanged, and so are the translation maps. The new
  keys only fill gaps, and no existing French value was edited. English files are
  untouched.
- Existing tests that mock `../i18n` (`settingsScreen.test.tsx` and others) supply their
  own `LANGUAGES`/`t`, so they are unaffected. Their stubs give no coverage of this
  change. The coverage comes from the new parity tests, which use the real module.

## 5. User-experience effect

- **French-speaking drivers:** structured API errors (OTP invalid/expired/locked,
  documents expired/pending/rejected, Spinr Pass required or quota exhausted, ride taken,
  payout method invalid, rate limited, etc.) now show in French instead of English or a
  raw key.
- **French-speaking riders:** the "Terms of Service" row on Privacy settings and the
  "More policies" row on Settings now show French labels instead of raw keys. Structured
  API errors now show in French instead of the backend's English message.
- **French users, from app start (biggest visible change):**
  - **Riders:** a rider who picked French now gets French from the first screen after
    every cold start. Previously every cold start was English.
  - **Drivers:** a driver who picked French now gets French from the first screen,
    including login, OTP, the dashboard and error alerts. Previously they had to open
    Settings in each app session first.
  - **Both apps:** users with English or no saved choice see no change.
  - **Picker title:** the rider picker title now reads "Choisir la langue" in French.
- **Spanish/Chinese users:** the picker now lists only English and Français. A user whose
  saved choice was `es`/`zh` sees English from app start. Before this change, a rider
  never got their saved choice back at all, and a driver only got it once they opened
  Settings.
- **Race fix:** a language picked right after launch (or, for drivers, on any Settings
  visit) can no longer snap back to the previously saved one a moment later. Nobody sees
  a new screen or new copy. The write-side guard is invisible to users because the
  picker only offers en/fr.
- **Mid-session:** nothing changes for anyone until they install an app update carrying
  this change (OTA/EAS). JSON and TS bundle changes are not visible in an already-running
  app.
- Copy tone: the drafts follow the existing French files' register ("vous", "Veuillez",
  "courriel", "texto", "soutien", "course", "chauffeur", space before `!`/`?` as
  driver-app `fr.json` already does). They still need human review (the [H] item above).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/i18n/fr.json` | +36 keys under `errors.{auth,driver,ride,payment,wallet,system}` | French parity with `en.json` (483 → 519 of 519) |
| `rider-app/i18n/fr.json` | +37 keys: the `errors.*` tree | French parity with `en.json` (69 → 106 of 106) |
| `rider-app/i18n/fr-CA.json` | +4 keys: `privacy.terms_of_service(_subtitle)`, `settings.more_policies(_subtitle)` | French parity with `en-CA.json` (155 → 159 of 159) |
| `driver-app/i18n/index.ts` | `es` removed from `languages`; `getStoredLanguage()` honours only picker-offered codes | Hide incomplete Spanish (D3) |
| `rider-app/i18n/index.ts` | `es`/`zh` removed from `LANGUAGES`; `hydrate()` honours only picker-offered codes | Hide incomplete Spanish/Chinese (D3) |
| `driver-app/__tests__/i18n/localeParity.test.ts` | New, 9 tests | Parity, placeholder and picker gate |
| `rider-app/__tests__/i18n/localeParity.test.ts` | New, 11 tests | Parity, placeholder and picker gate |
| `driver-app/i18n/index.ts` (again) | Calls the store's `loadLanguage()` once when the module first loads | Restore the saved language at app start (item 7) |
| `driver-app/__tests__/i18n/startupLanguage.test.tsx` | New, 3 tests | Simulated cold starts: saved `fr` shows French without Settings; `es`/none show English |
| `rider-app/app/_layout.tsx` | Imports `useLanguageStore`; one mount-only effect calls `hydrate()` | Restore the saved language at app start (item 6) |
| `rider-app/__tests__/i18n/startupLanguage.test.tsx` | New, 6 tests | Contract (RootLayout calls `hydrate()` on mount) plus a cold-start simulation for `fr`/`es`/`zh`/none |
| `rider-app/app/settings.tsx` | Picker title `"Select Language"` → `t('settings.selectLanguage')` | Translate the title (item 8) |
| `rider-app/__tests__/i18n/languagePickerTitle.test.tsx` | New, 2 tests | Renders the real screen with the real i18n module; checks the title in French and English |
| `driver-app/store/languageStore.ts` | `hasUserChosen` flag; `loadLanguage()` drops a late result after a pick; `setLanguage()` refuses non-picker codes | Race fix and write-side guard (items 9–10) |
| `driver-app/i18n/index.ts` (again) | `getStoredLanguage()` logs read failures; start-up comment updated | Stop swallowing errors silently (item 12) |
| `driver-app/app/driver/settings.tsx` | Mount-time `loadLanguage()` removed | Redundant with the start-up restore and a second race source (item 11) |
| `driver-app/__tests__/i18n/languageStoreGuards.test.ts` | New, 5 tests | Slow read resolving after a pick (via the real start-up path); read landing before the pick's write; refused `es`; refused pick not blocking the restore |
| `rider-app/i18n/index.ts` (again) | `hasUserChosen` flag; `hydrate()` drops a late result after a pick and logs errors; `setLanguage()` refuses non-picker codes and logs write failures | Race fix, write-side guard and logging (items 9, 10, 12) |
| `rider-app/__tests__/i18n/languageStoreGuards.test.ts` | New, 12 tests | Slow `getItem` resolving after `setLanguage`; refused `es`/`zh` through `setLanguage` and both `changeLanguage` shims (`es`/`zh`/`zh-CN`); `en-CA` still accepted; refused pick not blocking the restore; `hydrate()` error is logged |
| `docs/change-log/2026-09-26-french-complete-hide-es-zh.md` | New | This log |

Key counts (flattened leaf keys):

| File | Before | After |
|---|---|---|
| driver-app `en.json` | 519 | 519 |
| driver-app `fr.json` | 483 | **519** |
| driver-app `es.json` | 354 (165 missing) | 354 (hidden) |
| rider-app `en-CA.json` / `en.json` (union) | 159 / 106 (249) | unchanged |
| rider-app `fr-CA.json` | 155 | **159** |
| rider-app `fr.json` | 69 | **106** |
| rider-app French resolved (union) | 208 of 249 | **249 of 249** |
| rider-app `es.json` / `zh.json` | 69 / 69 | unchanged (hidden) |

## 7. Before / after

```ts
// Before — driver-app/i18n/index.ts
export const languages = [ en, fr, { code: 'es', name: 'Spanish', nativeName: 'Español' } ];
if (stored === 'en' || stored === 'fr' || stored === 'es') return stored;

// Before — rider-app/i18n/index.ts
export const LANGUAGES = [ en, fr, es, zh ];
if (stored === 'en' || stored === 'fr' || stored === 'es' || stored === 'zh') set({ language: stored });
```

```ts
// After — driver-app/i18n/index.ts
export const languages = [ en, fr ];            // es hidden until complete (D3)
if (languages.some((l) => l.code === stored)) return stored as Language;
return 'en';

// After — rider-app/i18n/index.ts
export const LANGUAGES = [ en, fr ];            // es/zh hidden until complete (D3)
if (LANGUAGES.some((l) => l.code === stored)) set({ language: stored as Language });
```

Start-up restore and picker title:

```tsx
// Before — rider-app/app/_layout.tsx (RootLayout): hydrate() never called
useEffect(() => { void clearLegacyScheduledReminders(); }, []);

// After
useEffect(() => { void clearLegacyScheduledReminders(); }, []);
useEffect(() => { void useLanguageStore.getState().hydrate(); }, []);

// Before — driver-app: loadLanguage() only in app/driver/settings.tsx's mount effect
// After — end of driver-app/i18n/index.ts
Promise.resolve()
    .then(() => require('../store/languageStore').useLanguageStore.getState().loadLanguage())
    .catch((error) => console.error('Failed to restore the saved language at startup:', error));

// Before — rider-app/app/settings.tsx
<Text style={styles.langTitle}>Select Language</Text>
// After
<Text style={styles.langTitle}>{t('settings.selectLanguage')}</Text>
```

Race fix and write-side guard (the driver store is shown; the rider store is the same
shape):

```ts
// Before — driver-app/store/languageStore.ts
setLanguage: async (language) => { await setStoredLanguage(language); set({ language }); },
loadLanguage: async () => {
    set({ isLoading: true });
    const language = await getStoredLanguage();
    set({ language, isLoading: false });        // overwrites a pick made during the read
},

// After
setLanguage: async (language) => {
    if (!languages.some((l) => l.code === language)) { if (__DEV__) console.warn(...); return; }
    set({ hasUserChosen: true });
    await setStoredLanguage(language);
    set({ language });
},
loadLanguage: async () => {
    set({ isLoading: true });
    const language = await getStoredLanguage();
    if (get().hasUserChosen) { set({ isLoading: false }); return; }   // a pick wins
    set({ language, isLoading: false });
},
```

## 8. Rollback plan

There is no feature flag. The mobile i18n bundles have no runtime `app_settings` hook,
so rollback means shipping a new app build or OTA update with the commits reverted. No
live data is touched: the AsyncStorage language value is never overwritten, so a revert
restores every user's previous `es`/`zh` choice exactly. The French additions are purely
additive. Reverting them only brings back the English fallbacks and raw keys described in
§2. The start-up restore only reads the stored value. Reverting its two commits brings
back the old behaviour: rider always English at cold start, driver English until
Settings opens. The picker title commit reverts independently.

The guard commits (race and write-side) are in-memory only and revert independently. The
driver Settings re-read removal (`96c9e1d`) reverts on its own. With the guard still in
place, restoring that re-read is harmless.

## 9. Verification performed

- [x] **Parity measured** with a flatten-and-diff script. The counts are in §6. There are
  0 French keys that English lacks, and 0 placeholder mismatches in either app.
- [x] **New tests fail on `origin/main` and pass after the change.** With the pre-change
  `i18n/` files checked out: driver-app 4 of 9 failed and rider-app 7 of 11 failed. After
  the change: 9 of 9 and 11 of 11 pass.
- [x] **Start-up and title tests fail without their change.** Each changed file was
  reverted on its own, with the new test kept:
  - driver `i18n/index.ts` reverted: 3 of 3 `startupLanguage` tests fail. Without the
    restore, no AsyncStorage read happens and a saved `fr` stays English.
  - rider `_layout.tsx` reverted: 2 of 6 fail (both RootLayout contract checks).
  - rider `settings.tsx` reverted: 1 of 2 `languagePickerTitle` tests fail (the French
    title).
  - All pass with the change.
- [x] **Guard tests fail without the guard.** Each store was reverted on its own, with the
  new `languageStoreGuards` test kept:
  - **Driver store reverted:** 2 of 5 fail. These are the race (a slow `getItem`
    resolving after `setLanguage('fr')` restores `en`) and the refused-`es` case.
  - **Rider `i18n/index.ts` reverted:** 9 of 12 fail. These are the race, all 7
    refused-code cases (`setLanguage` plus both `changeLanguage` shims) and the
    `hydrate()` error log.
  - **Tests that are not bug detectors:** the remaining tests pass either way by design.
    They are sanity checks that the guard does not break the normal restore, a read
    landing before the pick's write, or a restore after a refused pick.
- [x] `npx tsc --noEmit -p .`: driver-app exit 0, rider-app exit 0. Both were re-run
  after the start-up and title changes, and again after the guards.
- [x] Full `npx jest`, baseline → first round (parity and picker) → start-up and title →
  final (guards):
  - driver-app: 174 suites / 2159 tests → 175 / 2168 → 176 / 2171 → **177 / 2176**, all
    passing.
  - rider-app: 173 suites / 2340 tests → 174 / 2351 → 176 / 2359 → **177 / 2371**, all
    passing.
  - The final runs logged no "Failed to restore / read / store language" lines in either
    app.
- [x] **Re-verified on this PR's app code** (W7.1 files identical to the reviewed branch, rebuilt on `main`):
  - `tsc` exits 0 in both apps;
  - full `jest`: driver-app **179 suites / 2198 tests**, rider-app **177 suites / 2373 tests**, all passing.
- [x] Blast-radius grep: `languages`, `LANGUAGES`, `getStoredLanguage`, `loadLanguage`,
  `hydrate`, `setLanguage`, `changeLanguage`, `from '../i18n'` across both apps, plus
  `expo-localization`/`getLocales` repo-wide (no hits).
- [ ] Feature flag: not used. There is no flag mechanism for mobile i18n bundles, and
  the change only hides incomplete options and fills missing strings.

## What was NOT verified

- **No local production build** (`eas build` / `expo export`). Only `tsc --noEmit` and
  jest were run locally. CI's `expo export (android + ios)` job builds both apps on the PR.
- **No device, simulator or visual check.** rider-app and driver-app have no visual
  regression tooling. Text length in French (typically 15–30% longer) was reasoned about,
  not screenshotted. Error strings render in native `Alert` dialogs, which wrap, and the
  4 policy-row labels are about as long as neighbouring French rows that already exist.
- **Translation quality** was not checked by a fluent speaker. See the [H] item at the top.
- **Start-up restore was not observed on a device.**
  - **"No English flash" is reasoned, not measured.** The claim rests on timing: 1.8 s
    minimum splash hold (rider) and a 250 ms splash settle (driver) against one
    AsyncStorage read. There is no device or visual check (no mobile visual tooling).
  - **The rider RootLayout is not rendered in any test.** The repo has no harness for
    it (about 60 native/SDK imports). The rider start-up test is therefore a
    source-level contract (RootLayout calls `hydrate()` in a mount-only effect) plus a
    behavioural simulation of that call. It does not mount the real layout.
  - **The driver restore depends on the first translated module loading.** It runs when
    the first translated screen or `lib/alert.ts` module loads. This was confirmed by
    reading expo-router 57's route loading: only layouts load eagerly, and no layout
    imports i18n. It was not traced on a device.
- **The race was reproduced in jest only.** It was reproduced with a controllable
  `getItem` promise, not on a device with genuinely slow storage.
- **Closed here:** open questions 1–4 are now resolved.
  - **1–3:** rider start-up restore, driver start-up restore and rider picker title
    (items 6–8).
  - **4:** `setLanguage`/`changeLanguage` accepting es/zh, closed by the write-side guard
    (item 10).
- **Follow-ups (not changed here, by decision):**
  - **Two key sets (open question 5):** rider-app keeps two parallel key sets:
    snake_case in `en-CA`/`fr-CA` and camelCase in `en`/`fr`/`es`/`zh`. About 44 of the
    53 non-error camelCase keys that exist only in `en.json` (e.g. `home.whereToGo`,
    `wallet.*`, `loyalty.*`, `support.*`) have no literal `t('…')` reference in the app.
    They look dead but were not deleted, because deletion needs approval. This is logged as
    `ACTION_ITEMS.md` C139 (#5889).
  - **Driver restore location:** if the queued `driver-app/app/_layout.tsx` PR lands, the
    driver restore could move into RootLayout for a deterministic, earlier call
    (§3, alternative for item 7).
  - No locale **file** is dead: all 9 are imported by their app's `i18n/index.ts`.
    `driver-app/i18n/es.json`, `rider-app/i18n/es.json` and `rider-app/i18n/zh.json` are
    now unreachable to users but are still bundled.
- **Review:** `spinr-edge-case-reviewer` ran on the full W7.1 branch on 2026-09-26. It found no blockers.
  - It reproduced the load-vs-pick race; that is fixed here, together with refusing hidden codes in the stores.
  - No accessibility or copy review was run.
