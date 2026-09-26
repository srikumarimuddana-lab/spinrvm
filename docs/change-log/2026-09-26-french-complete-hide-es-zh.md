# Change Impact & Risk Log — French complete, Spanish/Chinese hidden (W7.1)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code (agent), for UX program item W7.1 |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | rides / drivers (UI copy and settings only; no backend, money or state-machine change) |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x` (this PR, W7.1 part 1 of 2). Part 2 (picker title, start-up language restore, load-vs-pick race fix) follows. |
| Related issue or gap ID | W7.1, decision D3: finish French to 100% of the keys the apps use; hide `es` and `zh` from the language pickers until they are complete |

> **[H] Human action required before release:** every French string added here was
> drafted by an agent. A fluent French speaker, ideally one familiar with Canadian
> (fr-CA) usage, must review the 77 new strings listed in §6 before this ships to
> real users. The keys are all `errors.*` (driver-app: 36; rider-app `fr.json`: 37)
> plus 4 policy-row labels (rider-app `fr-CA.json`).

## 1. Issue / gap identified

French was incomplete in both apps, and the language pickers offered Spanish (both
apps) and Chinese (rider-app) even though those translations cover well under half
the keys. In some places the missing keys showed English, and in others they showed
raw dotted keys.

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

Alternative considered: keep es/zh in the list with a `selectable: false` flag and
filter in each picker screen. This was rejected because it adds a field and a filter in
two screen files for no behavioural gain. Removing the rows keeps the picker screens
untouched, and the parity test gates re-adding a language.

## 4. Risk & impact on existing functionality

Blast radius: **two surfaces, i18n only**. There are no backend, state-machine, money or
insurance paths.

- `languages` (driver-app) has exactly one consumer: `app/driver/settings.tsx`, for the
  picker list and the row subtitle. `LANGUAGES` (rider-app) has exactly one consumer:
  `app/settings.tsx`, for the picker list and the subtitle. Neither screen file was
  modified. Both already fall back to `'English'` when a code is missing from the list.
- `getStoredLanguage()` is called only by `store/languageStore.ts` → `loadLanguage()`,
  which is called only from `app/driver/settings.tsx`. `hydrate()` (rider-app) has
  **no caller** in app code (see "What was NOT verified / found").
- `tKey()` / `translate()` / `t()` are unchanged, and so are the translation maps. The new
  keys only fill gaps, and no existing French value was edited. English files are
  untouched.
- The rider-app store's `setLanguage` and the `i18n.changeLanguage` compat shim still
  accept `es`/`zh`. No app code calls `changeLanguage`, and the picker is the only
  caller of `setLanguage`.
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
- **Spanish/Chinese users:** the picker now lists only English and Français. A user whose
  saved choice was `es`/`zh` sees English the next time their stored language is loaded.
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

## 8. Rollback plan

There is no feature flag. The mobile i18n bundles have no runtime `app_settings` hook,
so rollback means shipping a new app build or OTA update with the commits reverted. No
live data is touched: the AsyncStorage language value is never overwritten, so a revert
restores every user's previous `es`/`zh` choice exactly. The French additions are purely
additive. Reverting them only brings back the English fallbacks and raw keys described in
§2.

## 9. Verification performed

- [x] **Parity measured** with a flatten-and-diff script. The counts are in §6. There are
  0 French keys that English lacks, and 0 placeholder mismatches in either app.
- [x] **New tests fail on `origin/main` and pass after the change.** With the pre-change
  `i18n/` files checked out: driver-app 4 of 9 failed and rider-app 7 of 11 failed. After
  the change: 9 of 9 and 11 of 11 pass.
- [x] `npx tsc --noEmit -p .`: driver-app exit 0, rider-app exit 0.
- [x] Full `npx jest`, before → after:
  - driver-app: 174 suites / 2159 tests → 175 / 2168, all passing.
  - rider-app: 173 suites / 2340 tests → 174 / 2351, all passing.
- [x] **Re-verified on the PR branch**, rebuilt from `main` after #5892:
  - `tsc` exits 0 in both apps;
  - full `jest`: driver-app 177 suites / 2190 tests, rider-app 174 suites / 2353 tests, all passing.
- [x] Blast-radius grep: `languages`, `LANGUAGES`, `getStoredLanguage`, `loadLanguage`,
  `hydrate`, `setLanguage`, `changeLanguage`, `from '../i18n'` across both apps, plus
  `expo-localization`/`getLocales` repo-wide (no hits).
- [ ] Feature flag: not used. There is no flag mechanism for mobile i18n bundles, and
  the change only hides incomplete options and fills missing strings.

## What was NOT verified

- **No production build** (`eas build` / `expo export`) was run. Only `tsc --noEmit` and
  jest were.
- **No device, simulator or visual check.** rider-app and driver-app have no visual
  regression tooling. Text length in French (typically 15–30% longer) was reasoned about,
  not screenshotted. Error strings render in native `Alert` dialogs, which wrap, and the
  4 policy-row labels are about as long as neighbouring French rows that already exist.
- **Translation quality** was not checked by a fluent speaker. See the [H] item at the top.
- **Found, not fixed (out of scope / forbidden files):**
  - rider-app `hydrate()` has **no caller** (fixed in W7.1 part 2). `useLanguageStore.hydrate` is never invoked
    (the natural home, `rider-app/app/_layout.tsx`, was off-limits for this change). So
    a rider's language choice does not survive an app restart: every cold start is
    English. The stored-language fallback added here is correct but dormant until
    `hydrate()` is wired.
  - driver-app `loadLanguage()` is called only from `app/driver/settings.tsx` (fixed in W7.1 part 2). A driver
    who chose French sees English after a cold start until they open Settings.
  - rider-app's picker title `"Select Language"` (`app/settings.tsx`) is hardcoded
    English (fixed in W7.1 part 2). An unused `settings.selectLanguage` key exists in `en.json`/`fr.json`.
  - rider-app keeps two parallel key sets: snake_case in `en-CA`/`fr-CA` and camelCase in
    `en`/`fr`/`es`/`zh`. About 44 of the 53 non-error camelCase keys that exist only in
    `en.json` (e.g. `home.whereToGo`, `wallet.*`, `loyalty.*`, `support.*`) have no
    literal `t('…')` reference in the app. They look dead but were not deleted, because
    deletion needs approval. This is logged as `ACTION_ITEMS.md` C139.
  - No locale **file** is dead: all 9 are imported by their app's `i18n/index.ts`.
    `driver-app/i18n/es.json`, `rider-app/i18n/es.json` and `rider-app/i18n/zh.json` are
    now unreachable to users but are still bundled.
- **Review:** `spinr-edge-case-reviewer` ran on the full W7.1 branch (both parts) on 2026-09-26 and found no blockers in this part. It reproduced a race where a slow language load overwrites a fresh pick; that is fixed in part 2, alongside the start-up restore. No accessibility or copy review was run on this part.
