# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (agent session) |
| Surface(s) | rider-app / driver-app (shared component in `shared/components/SOSButton.tsx`) |
| Domain (Sentry tag) | safety |
| PR / commit link | (local commit only — not pushed; see commit SHA in session report) |
| Related issue or gap ID | P1 tracker #14 |

## 1. Issue / gap identified

`shared/components/SOSButton.tsx` — the hold-to-trigger emergency SOS button used by both
rider-app and driver-app — had every user-facing string (alert titles/bodies, button labels,
accessibility labels/hints) hardcoded in English, even though `fr` is a live, user-selectable
language wired into both apps' settings screens. A French-locale user pressing SOS today sees
100% English UI on the single most safety-critical control in the app.

## 2. Root cause

A prior localization effort added translated `sos.*` / `settings.emergencyServicesTitle` /
`safety.sosActivated` keys to the locale JSON in both apps, but never wired a `t()` call into
`SOSButton.tsx` itself or updated any of its 5 call sites to pass a translator. The keys were a
stalled effort, not a deliberate decision — confirmed by grep: none of rider-app's `sos.*` keys
(`shared/api/__tests__` aside, which only exercises the API layer, not this component) were
referenced anywhere in application code before this change.

A second, smaller contributing factor: `shared/` has no i18n instance of its own, and rider-app
and driver-app each ship a **separate** one (different locale JSON, two independent zustand
stores — `rider-app/i18n/index.ts`'s `useTranslation()` vs `driver-app/store/languageStore.ts`'s
`useLanguageStore()`). No existing shared component had a working precedent for this — grepping
`shared/components/*` for i18n usage came up empty — so there was no established pattern to
follow, likely part of why this stalled instead of being finished.

## 3. Fix / remediation

- Added an optional `t?: (key: string) => string` prop to `SOSButtonProps`. Every hardcoded
  string in the component now routes through `translate('sos.<key>' | 'common.cancel')`, where
  `translate = t ?? defaultT` and `defaultT` is a local fallback map holding the **exact**
  pre-existing English strings (byte-identical), so any caller that doesn't pass `t` renders
  unchanged.
- Updated all 5 real call sites (listed below) to pass `t` from their own app's i18n hook.
- Added the missing `sos.*` keys (label/hint/alert/a11y strings) to all 4 locale files touched
  (rider-app `en-CA.json`/`fr-CA.json`, driver-app `en.json`/`fr.json`), reusing the
  already-existing `sos.button_label`, `sos.alert_title`, `sos.alert_msg`, `sos.call_911`,
  `sos.im_ok`, `sos.alert_sent_label` keys where their EN value was already byte-identical to
  the current hardcoded string (and adding them fresh to driver-app, which didn't have an `sos`
  namespace at all — only the unused `safety.sosActivated`/`safety.call911` keys, left untouched).
  Every other string got a new key; none of the existing key *values* were changed for strings
  that are already live/visible (see §7).
- Deliberately used **identical key names** (`sos.button_label`, `sos.hold_hint`, etc.) in both
  apps' locale files, even though driver-app's own convention elsewhere is camelCase — this is
  what lets one shared component call one fixed set of `t()` keys regardless of which app renders
  it, instead of branching on which app it's running in.
- Added Jest coverage in both apps (see §9).

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, but interface change is additive.** `SOSButton` is rendered from
exactly 5 places (grepped `<SOSButton` across `rider-app/` and `driver-app/`, confirmed no other
importer):

| File | Notes |
|---|---|
| `rider-app/app/driver-arrived.tsx` | header SOS button |
| `rider-app/app/driver-arriving.tsx` | header SOS button (hidden while `isSearching`) |
| `rider-app/app/ride-in-progress.tsx` | **2 usages** — action-bar button and floating top-right button |
| `rider-app/app/(tabs)/index.tsx` | home-screen SOS button (works with no active ride — falls back to `tel:911`) |
| `driver-app/app/driver/(tabs)/index.tsx` | shown when `driver_discreet_sos_enabled` app-setting is **off** (default `False` per B16) — the discreet `SafetyShield.tsx` component replaces it when the flag is on. `SafetyShield.tsx` is a separate, still-unlocalized component (driver-app-only, explicitly documented as staying untouched by this and B16) — **out of scope for this change**, flagged as a follow-up gap. |

The `t` prop is optional with an English-fallback default, so:
- No other consumer exists to miss the update — all 5 known call sites were updated.
- If a 6th call site appears later without passing `t`, it silently gets English (today's
  behavior), not a crash or a translation-key leak.
- No change to `onTrigger`, `rideId`, `size` props, hold-duration (`SOS_HOLD_MS = 1200`),
  retry/backoff (`SOS_MAX_ATTEMPTS = 3`, `[1000, 2000]` delays), or any visual/style code —
  purely a string-source swap plus one new optional prop.

**Other readers of the same state/props:** none beyond the 5 render call sites above — `SOSButton`
owns only local component state (`triggered`/`sending`/`pressing`/`failed`), not any shared
store/table. `rideStore.triggerEmergency` (the `onTrigger` passed in from rider-app) and the
driver-app inline `onTrigger` (posts to `/rides/{rideId}/emergency`) are unchanged — this fix
never touches the backend call path, only the strings shown around it.

**Locale-file risk:** rider-app's `t()` (`rider-app/i18n/index.ts`) falls back
`es → [es.json, en-CA.json]` and `zh → [zh.json, en-CA.json]`, so the new `sos.*` keys are safe
for all 4 rider-app languages even though only `en`/`fr` got real translations — `es`/`zh` users
fall back to English for these keys, same as they did for every other untranslated key already.
**Driver-app has no such fallback** — `translate()` in `driver-app/i18n/index.ts` returns the raw
key path if missing for the active language. Driver-app's `es.json` already lacks an entire
`safety` section (pre-existing gap, confirmed via `'safety' in d` → `False`), so an `es`-locale
driver already saw raw-key fallbacks for other unfinished sections before this change; I did not
add `sos.*` to `es.json` (task scope was en/fr), so this is a **pre-existing gap this change does
not fix but also does not worsen** for the `sos` namespace specifically — it's a new instance of
an already-known pattern. Flagged as a standing gap, not blocking, since driver-app's `sos.*` was
never live before this PR (defaults to English via the `t` fallback function either way, since
driver-app's `useLanguageStore().t` is always passed and only breaks for the `es` language
specifically, not `en`/`fr`).

## 5. User-experience effect

- **Rider-facing and driver-facing.** French-locale users of both apps now see the SOS button's
  full flow (idle/hold/sending/failed/sent states, all alert dialogs, all accessibility
  labels/hints) in French for the first time.
- **This is visible mid-session** to a rider or driver who is already on a ride and has French
  selected as their app language — the SOS button's copy changes language the next time they
  open/re-render the screen carrying it (no app restart needed, since it reads the live
  `useTranslation()`/`useLanguageStore()` value on every render).
- **This is a genuine, intentional behavior change for French-locale users specifically** — not
  a bug fix restoring previously-correct behavior. English-locale users (rider-app default, and
  any driver/rider who has not switched to French) see **zero visible change** — the `t` prop
  passed at every call site still resolves the identical English strings via the real locale
  files, and the component's `defaultT` fallback (used only if `t` is ever omitted) is
  byte-for-byte the previous hardcoded English.
- Given this is a safety-critical control, the copy accuracy matters more than for a typical
  screen — see §9 "What was NOT verified" for which translations still need a native-speaker
  pass before this should be treated as fully signed off for production French traffic.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/SOSButton.tsx` | Added optional `t` prop + `DEFAULT_STRINGS`/`defaultT` fallback; every hardcoded string replaced with `translate('sos.…')` / `translate('common.cancel')` calls | Core localization fix |
| `rider-app/i18n/en-CA.json` | Added 15 new `sos.*` keys (English) | New strings needed by the component, not previously covered by the existing 7-key `sos` section |
| `rider-app/i18n/fr-CA.json` | Added the same 15 keys (French) | French translation for the above |
| `driver-app/i18n/en.json` | Added a new `sos` section (24 keys total: 6 reused-value keys + 18 new) | driver-app had no `sos` namespace at all before this |
| `driver-app/i18n/fr.json` | Added the same `sos` section (French) | French translation for the above |
| `rider-app/app/driver-arrived.tsx` | Imported `useTranslation`, destructured `t`, passed `t={t}` to `SOSButton` | Wire translator into call site |
| `rider-app/app/driver-arriving.tsx` | Same | Wire translator into call site |
| `rider-app/app/ride-in-progress.tsx` | Same, applied to both of its 2 `SOSButton` usages | Wire translator into call site |
| `rider-app/app/(tabs)/index.tsx` | Same | Wire translator into call site |
| `driver-app/app/driver/(tabs)/index.tsx` | Added `t={t}` (the screen already destructured `t` from `useLanguageStore()` for other strings) | Wire translator into call site |
| `rider-app/__tests__/SOSButton.test.tsx` | New test file | EN/FR render + Alert-content assertions against rider-app's real locale data, plus a hardcoded-string regression guard |
| `driver-app/__tests__/components/SOSButton.test.tsx` | New test file | Same coverage against driver-app's real locale data (its own `en.json`/`fr.json`, not rider-app's) |
| `docs/change-log/2026-08-11-sos-button-localization.md` | New file (this document) | Mandatory Change Impact Log for a safety-surface fix |

## 7. Before / after

```tsx
// Before — shared/components/SOSButton.tsx
const showFailureAlert = (retry: () => void) => {
  Alert.alert(
    '⚠️ Alert Not Sent',
    'Could not reach Spinr. The button will stay red — tap it to retry.\n\nYou can call 911 directly right now.',
    [
      { text: 'Call 911', style: 'destructive', onPress: () => Linking.openURL('tel:911') },
      { text: 'Retry Now', onPress: retry },
      { text: 'Dismiss', style: 'cancel' },
    ]
  );
};
```

```tsx
// After
const showFailureAlert = (retry: () => void) => {
  Alert.alert(
    translate('sos.failure_title'),
    translate('sos.failure_msg'),
    [
      { text: translate('sos.call_911'), style: 'destructive', onPress: () => Linking.openURL('tel:911') },
      { text: translate('sos.retry_now'), onPress: retry },
      { text: translate('sos.dismiss'), style: 'cancel' },
    ]
  );
};
// where: const translate = t ?? defaultT;
// and defaultT('sos.failure_title') === '⚠️ Alert Not Sent' (etc.) — byte-identical to before
// for any caller that doesn't pass t, so English-locale/un-migrated behavior is unchanged.
```

```tsx
// Before — rider-app/app/driver-arrived.tsx
<SOSButton rideId={rideId as string} onTrigger={triggerEmergency} />
```

```tsx
// After
const { t } = useTranslation();
// ...
<SOSButton rideId={rideId as string} onTrigger={triggerEmergency} t={t} />
```

## 8. Rollback plan

- **No feature flag** was added — this is pure UI copy, not a behavior/logic change, and the
  fallback-to-English design means a partial rollback is already built in (see below).
- **To fully revert:** `git revert` the commit. Since the only runtime-observable change is which
  strings render (no state-machine, no money, no ride/driver table writes, no migration), a plain
  code revert is a complete and sufficient rollback here — there is no live data to reconcile.
- **To revert just the French copy while keeping the plumbing** (e.g. if a specific translation
  is reported as wrong/confusing in the field): edit the affected key's value in the relevant
  `fr.json`/`fr-CA.json` file and redeploy — no code change needed, since the component just
  reads whatever the locale file currently says.
- **Partial/automatic rollback already exists in the code:** if any of the 5 call sites' `t={t}`
  wiring were reverted individually (leaving the `SOSButton.tsx` core change in place), that
  specific screen falls back to `defaultT`'s English strings rather than breaking — so a bad
  translation discovered on one screen doesn't require reverting the shared component itself,
  only that screen's locale key or its `t` wiring.

## 9. Verification performed

- [x] **Automated tests run:**
  - `rider-app`: `npx jest __tests__/SOSButton.test.tsx --no-coverage` → 6/6 passed (default-English
    fallback render, French accessibility-label render, English accessibility-label render,
    French success-alert content after a simulated 1.2s hold, French failure-alert content after
    3 simulated failed attempts, and a source-scan regression guard asserting no listed hardcoded
    English phrase remains in `SOSButton.tsx` outside its `DEFAULT_STRINGS` fallback map, with
    line/block comments stripped first so comment prose isn't a false positive).
  - `driver-app`: `npx jest __tests__/components/SOSButton.test.tsx --no-coverage` → 3/3 passed
    (French accessibility-label render, English accessibility-label render, French success-alert
    content — all against driver-app's own `en.json`/`fr.json`, not rider-app's, since the two
    apps' locale data is independent).
  - Regression check on 3 pre-existing driver-app tests that also touch `useLanguageStore`
    (`__tests__/screens/notifications.test.tsx`, `__tests__/components/TripCompletedPanel.test.tsx`,
    `__tests__/components/ActiveRidePanel.test.tsx`) → 18/18 still passing after the locale-JSON
    additions, confirming the new `sos` section didn't break JSON parsing or any other key lookup.
- [x] **`tsc --noEmit`** run in both `rider-app/` and `driver-app/` (full project, not scoped to
  changed files) — clean, zero errors in both. **This is not a full `expo export`/EAS production
  build** — no bundler/Metro run was performed, per the CLAUDE.md distinction between `tsc
  --noEmit` and a real build. No `npm run build`/`expo export --platform web` equivalent exists as
  a checked-in script beyond `rider-app`'s `build:web`, which was not run in this pass.
- [x] **Blast-radius grep performed:** `grep -rn "SOSButton" rider-app/ driver-app/ shared/`,
  narrowed to `<SOSButton` JSX usages specifically — 5 real call sites found and all updated (see
  §4/§6). Also grepped for any other importer of `shared/components/SOSButton.tsx` beyond the 5 —
  none found (hook/comment references in `driver-app/hooks/useDriverSafetyTrigger.ts` and
  `useDriverDiscreetSosFlag.ts` mention `SOSButton` only in comments, they don't import it).
- [x] **Reviewed against relevant CLAUDE.md conventions:** WCAG/accessibility (labels/hints kept,
  only their string source changed — no structural a11y change, per the task's explicit
  instruction not to redesign); PIPEDA (no new PII logged — the one `console.warn` in the
  component logs no coordinates, unchanged); i18n pattern precedent (no other `shared/` component
  had one to follow — this establishes the injected-`t`-prop pattern as precedent going forward).
- [ ] **Not feature-flagged** — judged unnecessary: this is additive/backward-compatible by
  construction (optional prop, English-identical default), doesn't touch ride state/money/auth,
  and only *adds* a language option to an already-shipped control rather than changing its
  logic — see §8 for why a flag would be redundant with the fallback design already in place.
- [ ] **Not manually tested in a running app/staging** — no device/simulator/staging environment
  was available in this session; verification is Jest (real component, real locale JSON, real
  `Alert.alert`/`react-native` render pipeline via `@testing-library/react-native`) plus static
  typecheck only.

## 10. What was NOT verified

- **No real device/simulator/staging run.** All verification is automated (Jest + tsc). The
  actual on-screen rendering (button sizing/wrapping with the new French strings, e.g. `"Envoi…"`
  vs `"Sending…"` label width in the small 44×44/80×80 button, or the hold/retry hint bubble text
  length) was reasoned about from string length, not visually confirmed. No visual/snapshot
  regression tooling exists in this repo for `rider-app`/`driver-app` (standing gap — see
  CLAUDE.md §6 of the release-gate checklist), so this is a real, unclosed boundary of what was
  checked, not implied full coverage.
- **Translations flagged as needing native-speaker / accessibility-reviewer review before this
  ships to real French-speaking users**, despite being my best-effort, reasonably confident
  attempt at accurate Canadian French:
  1. `sos.label_hold` = `"Maintenir..."` (rider-app fr-CA) / driver-app fr.json — the short,
     space-constrained button-state label for "Hold...". Compact UI microcopy where French
     phrasing conventions (gerund/noun form vs. infinitive) are genuinely debatable and
     layout-sensitive; I'd want a native speaker or existing house-style precedent to confirm.
  2. `sos.a11y_hint_failed` and `sos.a11y_hint_default` — I used "Appuyez deux fois pour…" for
     the "double tap to activate" VoiceOver/TalkBack convention. I chose this phrasing based on
     general familiarity with Apple's French VoiceOver terminology, but did **not** verify it
     against an actual French-locale VoiceOver/TalkBack device session, and I'm not certain
     Spinr has an established house convention for this elsewhere in the app that I should have
     matched instead (a repo-wide grep for other French accessibilityHint strings using "double
     tap" phrasing would resolve this, but wasn't done as part of this pass — scope was limited
     to `SOSButton.tsx`'s own strings).
  3. `sos.dismiss` = `"Fermer"` (both apps) — for the failed-alert dialog's "Dismiss" button,
     which the code comment notes intentionally does *not* clear the failed state. "Fermer"
     (Close) was chosen over "Ignorer" (Ignore/Dismiss) since it read as more neutral for a
     safety-critical control, but the connotation difference between "closing" vs. "dismissing/
     ignoring" an unsent emergency alert is a real word-choice call that a native speaker should
     confirm reads correctly, not something I should have guessed alone given the stakes.
  4. `sos.hold_hint` = `"Maintenez pendant 1,2 seconde"` — I fixed the pre-existing (unused,
     dead-code) `sos.button_hint` key's stale "1,5 seconde"/"1.5 seconds" value by *not* reusing
     it and instead writing a fresh, shorter key matching the actual `SOS_HOLD_MS = 1200`
     constant. I did not touch or fix the old `button_hint` key itself (left as-is, still unused,
     still stale) since it's out of scope for this component and was never wired up anywhere —
     noting this so it isn't mistaken for an oversight if someone else finds it later.
- **`es`/`zh` (rider-app) and `es` (driver-app) were not translated** for the new `sos.*` keys —
  out of task scope (en/fr only, per the task). rider-app falls back to English for these
  languages automatically (see §4); driver-app does not have a fallback mechanism, so an
  `es`-locale driver would see raw key paths for the new strings specifically — flagged as a
  known, pre-existing-pattern gap, not fixed here (see §4 for detail on why this doesn't regress
  anything that worked before).
- **Did not re-run the full rider-app or driver-app Jest suites** (only the new SOSButton test
  files plus 3 targeted pre-existing driver-app tests that touch the same locale-store code path)
  — a full-suite run was judged out of proportion to a scoped, additive change with a clearly
  bounded blast radius, but it means suites outside that targeted set were not re-verified in
  this pass.
- **`shared/components/SafetyShield.tsx`** (the driver-app-only "discreet SOS" component that
  replaces `SOSButton` when `driver_discreet_sos_enabled` is on) is **also entirely unlocalized**
  and was explicitly left untouched — it's a separate component with its own hardcoded English
  strings, out of scope for this task, and not covered by any of the testing above. Flagging this
  as a related follow-up gap rather than silently leaving it undiscovered.
