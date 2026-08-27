# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session), user request: address the flagged i18n gap |
| Surface(s) | rider-app, driver-app (PIPEDA re-consent notice screen) |
| Domain (Sentry tag) | rides (customer-facing UI; adjacent to auth/onboarding) |
| PR / commit link | commit following this log |
| Related issue or gap ID | Flagged 2026-08-19/2026-08-21 alongside `legacy-consent-notice.tsx`'s original ship, previously assessed as "pre-existing convention in both files, out of scope to fix wholesale" |

## 1. Issue / gap identified

`rider-app/app/legacy-consent-notice.tsx` and `driver-app/app/legacy-consent-notice.tsx` — the
PIPEDA re-consent screen shipped this session — hardcode all their copy in English. Both apps
already have real, working i18n systems (`i18n/{en,fr,es}.json` + driver-app's `useLanguageStore`,
rider-app's `useTranslation`), and both apps already use them on the closest analogous screens:
driver-app's `login.tsx`/`otp.tsx` translate their consent-checkbox copy via `t('login.*')`, and
rider-app's `privacy-settings.tsx` translates its data-rights copy via `t('privacy.*')`. Skipping
i18n on `legacy-consent-notice.tsx` — the most legally significant copy in either app's auth flow
(a live PIPEDA consent notice) — was a genuine, scoped gap relative to each app's own established
convention, not a "no i18n exists here" situation as first assessed.

## 2. Root cause

The screen was built new this session (2026-08-19, `docs/change-log/2026-08-19-legacy-consent-
notice.md`) and its copy was never wired into either app's existing translation system — an
oversight at build time, not a deliberate decision. The earlier "pre-existing convention... out of
scope to fix wholesale" note undersold the situation: it's true that a from-scratch, whole-app i18n
rollout would be out of scope (`rider-app/app/login.tsx` and `otp.tsx` themselves don't use i18n at
all — that mismatch is real and left alone, see §4), but this screen sits in the *same domain*
(consent/privacy) as screens that already do use each app's i18n system, so a scoped fix following
that existing pattern is squarely in scope.

## 3. Fix / remediation

- Added a `legacyConsent` namespace to all locale files this screen's copy needs:
  `driver-app/i18n/{en,fr,es}.json` and `rider-app/i18n/{en,fr,es,zh}.json` — 9 keys each
  (`title`, `body`, `note`, `accept`, `acceptLabel`, `viewPolicy`, `viewPolicyLabel`, `errorTitle`,
  `errorFallback`). English values are byte-identical to the previously-hardcoded strings (rider
  vs. driver differ only in "how you use Spinr" vs. "how you drive with Spinr", matching the
  existing per-app copy). French and Spanish translations added for both apps; Chinese added for
  rider-app only (rider-app's `t()` has no automatic English fallback for `zh` on a wholly missing
  key — driver-app's `t()` does fall back to English for any missing `fr`/`es` key, so those two
  locale files were still added for parity/completeness, not because a gap would otherwise break).
- Wired both `legacy-consent-notice.tsx` files to `t('legacyConsent.*')` — driver-app via the
  existing `useLanguageStore()` hook (same as `login.tsx`), rider-app via the existing
  `useTranslation()` hook (same as `privacy-settings.tsx`). No copy content changed for English
  users; strings now resolve through the shared translation layer instead of being inlined.
- Updated both screens' existing test files
  (`{rider,driver}-app/__tests__/.../legacyConsentNoticeScreen.test.tsx`) to mock the
  language store/hook with a *real* lookup (driver-app: `translate('en', key)` from the real
  `i18n/index.ts`; rider-app: a direct `en.json` read, see note below) rather than an identity
  stub, so the existing English-text assertions keep exercising the actual translated copy instead
  of silently degrading into "does `t()` get called" checks.
- Rider-app's test reads `i18n/en.json` directly rather than through the real `i18n/index.ts`
  module: that module imports `@react-native-async-storage/async-storage`, which driver-app's
  `jest.setup.js` mocks globally but rider-app's `jest-setup-expo.js` does not — confirmed by
  reproducing the failure (`[@RNC/AsyncStorage]: NativeModule: AsyncStorage is null`) before
  working around it. Not fixed at the jest-config level: that's a pre-existing, app-wide gap
  unrelated to this screen, out of scope for a surgical fix here.

## 4. Risk & impact on existing functionality

- **Blast radius:** grepped both apps for every other importer of `legacy-consent-notice.tsx` —
  none found; it's a route-file (`app/legacy-consent-notice.tsx`), reached only via
  `otp.tsx`'s post-login redirect and its own test. The i18n JSON edits are pure additions (new
  top-level key, verified via `git diff --stat`: `+11` lines per file, `0` removed) — no existing
  key was touched, so no other screen that reads `en.json`/`fr.json`/`es.json`/`zh.json` is
  affected. Confirmed valid JSON via `python3 -c "json.load(...)"` on all 7 edited locale files
  before touching any component code.
- **What's deliberately still NOT translated (documented, not silently left):**
  `rider-app/login.tsx` and `rider-app/otp.tsx` themselves don't use i18n at all — only
  `driver-app`'s equivalent screens do. This asymmetry pre-dates this fix and is unrelated to it;
  fixing it would be the "wholesale" rollout correctly identified as out of scope. Also
  untranslated in both apps' `legacy-consent-notice.tsx`: the loading spinner has no text to
  translate, and this screen's own JSDoc/inline comments (not user-facing).
- **No behavior change for an English user** — every English string is byte-identical to what was
  previously hardcoded; this only changes *where* the string comes from. Confirmed by running each
  app's full test suite (not just the two files touched) after the change: rider-app 123/123 suites,
  1746/1746 tests; driver-app 116/116 suites, 1303/1303 tests — all green, zero new failures.

## 5. User-experience effect

Rider- and driver-facing. A rider or driver whose device language is set to French or Spanish (and
Chinese, rider-app only) now sees this PIPEDA re-consent notice in their own language instead of
English — this is a net-new capability, not a change to what English users see. The screen is
dark-shipped behind `app_settings.legacy_consent_notice_enabled` (already flipped on this session,
per `ACTION_ITEMS.md` task #7), so this fix is immediately live for anyone who reaches the screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/legacy-consent-notice.tsx` | Hardcoded copy replaced with `t('legacyConsent.*')` via `useLanguageStore()`. | Match the i18n convention already used by this screen's siblings (`login.tsx`, `otp.tsx`). |
| `rider-app/app/legacy-consent-notice.tsx` | Hardcoded copy replaced with `t('legacyConsent.*')` via `useTranslation()`. | Match the i18n convention already used by `privacy-settings.tsx` (same consent/privacy domain). |
| `driver-app/i18n/{en,fr,es}.json` | Added `legacyConsent` namespace (9 keys), inserted after `otp`. | New translated copy source for the screen above. |
| `rider-app/i18n/{en,fr,es,zh}.json` | Added `legacyConsent` namespace (9 keys), inserted after `settings`. | Same, plus `zh` since rider-app's `t()` has no English fallback for a missing `zh` key. |
| `driver-app/__tests__/app/legacyConsentNoticeScreen.test.tsx` | Mocks `useLanguageStore` with a real `translate('en', key)` lookup instead of an implicit no-op. | Component now calls a hook that didn't exist in this test before; keep existing English-text assertions meaningful. |
| `rider-app/__tests__/legacyConsentNoticeScreen.test.tsx` | Mocks `useTranslation` with a real `en.json` lookup instead of an implicit no-op. | Same, worked around this app's jest AsyncStorage gap (see §3). |

## 7. Before / after

```tsx
// Before -- both apps, hardcoded
<Text style={styles.title}>An update on how we handle your information</Text>
<Text style={styles.body}>
  Spinr Mobility Inc. is committed to protecting your privacy under Canada&apos;s federal
  privacy law (PIPEDA). ...
</Text>
```

```tsx
// After -- both apps, translated (English value is identical)
<Text style={styles.title}>{t('legacyConsent.title')}</Text>
<Text style={styles.body}>{t('legacyConsent.body')}</Text>
```

## 8. Rollback plan

`git revert` is complete and sufficient. No data, schema, migration, or flag component — this is
pure client-side copy sourcing. Reverting returns both screens to their prior hardcoded-English
state; no cleanup needed. The `legacy_consent_notice_enabled` flag itself is untouched by this fix.

## 9. Verification performed

- [x] Full test suite, both apps, after the change: rider-app 123/123 suites (1746/1746 tests),
      driver-app 116/116 suites (1303/1303 tests) — no new failures, no skipped/stubbed tests
      providing false coverage.
- [x] `npx tsc --noEmit` clean on both apps (real production build type-check, not just the
      touched files).
- [x] `npx eslint` clean on all touched files (one warning surfaced and fixed inline —
      `no-require-imports` on the driver-app test's mock factory, resolved with the same
      `eslint-disable-next-line` pattern this codebase's own `i18n/index.ts` already uses for its
      `tKey()` helper's lazy require).
- [x] All 7 edited locale JSON files validated as parseable JSON before any component change.
- [x] Reproduced the rider-app AsyncStorage test failure directly (not guessed) before working
      around it — confirmed the exact error and root cause (missing global AsyncStorage mock in
      `jest-setup-expo.js`, present in driver-app's `jest.setup.js`) rather than assuming.
- [x] Blast-radius grep performed (§4) — no other importer of either `legacy-consent-notice.tsx`.

## What was NOT verified

- Not run on a real device/simulator in any of the four now-supported languages — verified via
  the JSON's own validity and the existing unit-test harness only, consistent with this session's
  standing no-simulator-run convention for backend/app changes of this size.
- The French/Spanish/Chinese translations were produced directly by this session, not reviewed by
  a native speaker or professional translator — same caveat that already applies to every other
  translated string in both apps' `i18n/` directories (e.g. `ACTION_ITEMS.md` D3's earlier
  driver-destination-mode i18n addition), not a new gap introduced here.
- `rider-app/login.tsx`/`otp.tsx`'s own lack of i18n (§4) was identified but deliberately not
  fixed — a separate, larger gap this fix does not claim to close.
- rider-app's missing global AsyncStorage jest mock (the reason the test couldn't use the real
  `i18n/index.ts` module) was worked around locally, not fixed at the jest-config level — that's a
  pre-existing, app-wide test-infra gap, out of scope for this screen-scoped fix. Worth a follow-up
  if other tests hit the same wall.
