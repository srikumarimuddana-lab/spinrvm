# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (agent session) |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | n/a (pure UI/typography, no backend domain) |
| PR / commit link | (this PR) |
| Related issue or gap ID | `ACTION_ITEMS.md` UX1 — "Remaining follow-up scope" (2 open items, not blocking that item's own closure) |

## 1. Issue / gap identified

UX1's Plus Jakarta Sans rollout fully migrated every *qualifying* rider-app
(33) and driver-app (42) file to `@shared/components/Text`, but left two
gaps open by its own acceptance bar: (1) nothing stopped a *new* screen
from importing `Text` straight from `react-native` and shipping
off-brand system-font copy — no lint enforcement existed; (2) a fresh,
independent grep (not trusting the prior migration's "qualifying files"
list, per this exact item's own repeated "don't trust the prior count"
warning) found **27 files** — 19 in rider-app, 9 in driver-app (one file,
`driver-app/app/subscription/cancel.tsx`, is counted once and wasn't in
either app's original list) — still importing `Text` directly from
`react-native`. The original migration's criterion was "has `fontWeight`
set, zero `fontFamily` anywhere in the file" — a narrower bar than "every
file that renders `Text`," so plain default-weight `Text` usages were
never counted as candidates even though they still render the OS system
font instead of the brand typeface.

## 2. Root cause

Not a regression — the original UX1 rollout's own acceptance bar was
scoped to "screens using an explicit `fontWeight`," and explicitly named
both of these as open follow-up items rather than blockers to closing
that item. This PR closes both.

## 3. Fix / remediation

1. Added a `warn`-level `no-restricted-imports` rule to both
   `rider-app/eslint.config.js` and `driver-app/eslint.config.js`,
   banning `Text` imported from `'react-native'` in `app/**` and
   `components/**`, pointing at `@shared/components/Text`. `warn` (not
   `error`) matches this file's own established posture for the
   hex-color/SPACING rules already there — advisory until pre-existing
   violations are cleaned up (neither app currently runs `eslint` in CI
   per `ci.yml`, so this is enforcement-by-visibility today, not a merge
   gate).
2. Migrated all 27 files a fresh grep found still importing `Text` from
   `react-native` (script-assisted, format-preserving — each file's own
   import-block line-wrapping style was left untouched, only the `Text`
   token removed and a new `import { Text } from '@shared/components/Text';`
   line added) to `@shared/components/Text`. Same "import swap only, no
   other Text behavior/props touched" pattern as all prior UX1 rounds.
3. Fixed one test (`rider-app/__tests__/paymentConfirmScreen.test.tsx`)
   that broke under the swap — see "Before/after" below.

## 4. Risk & impact on existing functionality

- **Blast radius: two independent, disjoint sets.** The lint-rule change
  touches only the two `eslint.config.js` files (config, not app code) —
  isolated, and `warn`-only so it cannot fail a build even if CI ran
  eslint. The 27 file migrations are each a self-contained import swap;
  grepped for every other importer before touching anything shared —
  `driver-app/components/AlertDialog.tsx` is used by ~35 other files
  (flagged by this repo's own pre-commit hook, check 11) and
  `driver-app/components/charts/EarningsLineChart.tsx` by the driver
  earnings screen — both call sites pass `style` through unchanged, so
  every consumer gets only the brand-font default, nothing else.
- `@shared/components/Text` itself is **unmodified** — it's the same
  component already live in 75 other files across both apps since
  2026-09-10/11 with no reported issues. This PR only adds more callers.
- The wrapper composes `style={[{fontFamily}, style]}` — when the
  caller's own `style` is itself an array, the result is a **nested**
  array (`[{fontFamily}, [...]]`) rather than a flat one. React Native
  flattens nested style arrays at render time (no visual difference), so
  this is a real behavior difference only for code that introspects
  `props.style` directly (e.g. a snapshot/structural test), not for
  actual rendering. Found and fixed the one such case in this repo (see
  below) — grepped for other `.style).toEqual` / `arrayContaining`
  patterns against a migrated file's `Text`; none other found in either
  app's test suite.
- No new dependency, no schema/API change, no background loop touched.

## 5. User-experience effect

- **Rider-facing and driver-facing.** All 27 migrated screens now render
  their `Text` in Plus Jakarta Sans instead of the OS system font
  (San Francisco/Roboto) — a visual-only change, matching what the
  rest of each app already looks like post-UX1. Not visible mid-session
  to someone already on that screen in an unexpected way (a font swap on
  next render, not a layout/behavior change); no copy changed.
- No functional behavior changed on any of the 27 screens — same props,
  same conditional rendering, same navigation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/eslint.config.js` | Added `no-restricted-imports` (`warn`) banning `Text` from `react-native` | Close the "not yet lint-enforced" UX1 follow-up |
| `driver-app/eslint.config.js` | Same rule | Same |
| 19 rider-app files (`app/(tabs)/index.tsx`, `app/(tabs)/activity.tsx`, `app/become-driver.tsx`, `app/driver-arriving.tsx`, `app/ride-details.tsx`, `app/ride-status.tsx`, `app/ride-tracking-webview.tsx`, `app/ride-completed.tsx`, `app/ride-options.tsx`, `app/confirm-pickup.tsx`, `app/payment-confirm.tsx`, `app/search-destination.tsx`, `app/profile-setup.tsx`, `app/work-profile.tsx`, `app/work-allowance-request.tsx`, `app/chat-driver.tsx`, `app/emergency-contacts.tsx`, `app/lost-and-found-chat.tsx`, `app/lost-and-found.tsx`) | `Text` import swapped from `react-native` to `@shared/components/Text` | Close the "27 files missed by the original criterion" gap |
| 9 driver-app files (`app/become-driver.tsx`, `app/driver/(tabs)/profile.tsx`, `app/driver/emergency-contacts.tsx`, `app/driver/lost-and-found-chat.tsx`, `app/driver/lost-and-found.tsx`, `app/driver/ride-detail.tsx`, `app/subscription/cancel.tsx`, `components/AlertDialog.tsx`, `components/charts/EarningsLineChart.tsx`) | Same swap | Same |
| `rider-app/__tests__/paymentConfirmScreen.test.tsx` | One assertion changed from `expect(style).toEqual(expect.arrayContaining([...]))` to `expect(StyleSheet.flatten(style)).toEqual(expect.objectContaining({...}))` | The wrapper's style nesting broke the old top-level-only assertion; flattening matches actual (and the wrapper's own) style-resolution semantics |

## 7. Before / after

Representative import-swap (repeated identically, format-preserving, across all 27 files):

```tsx
# Before
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';

# After
import { View, StyleSheet, ActivityIndicator } from 'react-native';
import { Text } from '@shared/components/Text';
```

The one behavior-relevant test fix:

```tsx
# Before
expect(label.props.style).toEqual(expect.arrayContaining([expect.objectContaining({ color: '#EF4444' })]));

# After
expect(StyleSheet.flatten(label.props.style)).toEqual(expect.objectContaining({ color: '#EF4444' }));
```

## 8. Rollback plan

`git revert` is fully sufficient for every commit in this PR: the lint
rule is `warn`-only (no build ever depended on its absence) and each file
migration is a pure import swap with no data/state touched. Reverting
restores the exact prior text-rendering behavior (system font instead of
brand font) with no cleanup required.

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean in both rider-app and driver-app after
      all 27 migrations.
- [x] `npx eslint "app/**/*.{ts,tsx}" "components/**/*.{ts,tsx}"` in both
      apps — confirmed 19/9 warnings before migration (matching the
      fresh grep exactly), 0/0 after.
- [x] Full `npx jest` suite in both apps: rider-app 151/151 suites,
      2080/2080 tests passing (after the one test fix above); driver-app
      149/149 suites, 1695/1695 tests passing.
- [x] Spot-checked every file whose diff was larger than the minimal
      2-line swap (7 files with compact/multi-name-per-line import
      blocks) to confirm the migration script did not reflow unrelated
      import formatting — re-ran with a format-preserving version after
      catching this on the first pass.

## 10. What was NOT verified

- Not run in a real device/simulator — no visual screenshot taken of any
  of the 27 screens before/after. rider-app and driver-app have no
  automated visual-regression tooling (per `CLAUDE.md`'s explicit
  disclosure requirement for these two apps) — the font-only change was
  reasoned about (same component, same 75-file precedent, `tsc`+`eslint`+
  `jest` all clean) rather than screenshotted.
- Did not re-audit the ~75 already-migrated files for the same
  style-array-nesting test-assertion pattern beyond a grep for
  `arrayContaining`/`.style).toEqual` near a `Text` usage in each app's
  test suite — none matched, but a differently-shaped assertion (e.g.
  snapshot-based) touching a migrated file's style elsewhere was not
  independently re-verified beyond the full jest run passing.
