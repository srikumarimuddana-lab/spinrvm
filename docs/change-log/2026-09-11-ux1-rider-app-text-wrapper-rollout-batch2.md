# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (claude-sonnet-5), session `session_016N2vRqybAY6LqEr8Yg7RUB` |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides (settings/legal/auth screens; no domain tag fits exactly — closest is general rider-facing UI, not payments/dispatch/safety) |
| PR / commit link | (see PR description this file is attached to) |
| Related issue or gap ID | ACTION_ITEMS.md UX1 |

## 1. Issue / gap identified

8 more rider-app screens render body text in the OS system font instead of
the loaded Plus Jakarta Sans brand typeface, because they still import
`Text` directly from `react-native` instead of the themed
`shared/components/Text.tsx` wrapper that UX1's round 1 (2026-09-10, PR
#5214) introduced and rolled out to 15 other files.

## 2. Root cause

Round 1 explicitly scoped itself to a first batch of 15 high-traffic files
and documented ~19 more as follow-up (ACTION_ITEMS.md UX1). This is that
follow-up, continued.

## 3. Fix / remediation

Re-grepped `fontWeight` vs `fontFamily` usage across `rider-app/app` and
`rider-app/components` from scratch (per this item's own "don't trust the
prior count, it drifts" warning) rather than trusting the ~19 estimate:

- 40 files matched `fontWeight`.
- 15 were round 1's already-migrated set — left untouched.
- 6 were excluded because they already set `fontFamily` manually somewhere
  in the file, so they don't meet this item's "no `fontFamily` anywhere in
  the file" bar for "unmigrated" — `app/(tabs)/index.tsx`,
  `app/become-driver.tsx` (only 2 of many `Text` call sites covered),
  `app/driver-arriving.tsx`, `app/ride-details.tsx`, `app/ride-status.tsx`,
  `app/ride-tracking-webview.tsx` (its one `fontFamily` is a deliberate
  monospace override for a GPS-trace text field, not brand-font coverage).
  These are a real but different gap (hand-written literals instead of the
  wrapper) — out of this item's stated scope, not silently dropped; called
  out in the ACTION_ITEMS.md update as future, separately-scoped work.
- 1 (`components/VoltraRideActivity.tsx`) was excluded because it never
  imports `Text` from `react-native` at all — it renders through
  `Voltra.Text`, a `@use-voltra/ios-client` primitive for the iOS Lock
  Screen/Dynamic Island Live Activity UI, a native SwiftUI-ish renderer
  this wrapper cannot reach (and isn't meant to — Live Activities render
  outside RN's own text pipeline).
- That left **18 real candidates**. This round migrated **8** of them,
  grouped by flow and split into 3 commits of ≤3 files each (this repo's
  task-decomposition rule): auth (`otp.tsx`, `verify-email.tsx`,
  `reactivate-account.tsx`), legal/consent (`legal.tsx`,
  `legacy-consent-notice.tsx`, `policies.tsx`), settings
  (`privacy-settings.tsx`, `accessibility.tsx`). The remaining 10 are left
  for a follow-up batch — see ACTION_ITEMS.md UX1's updated entry for the
  exact list.

Each file's change is the same one-line-class swap round 1 used: remove
`Text` from the `react-native` named-import list, add
`import { Text } from '@shared/components/Text';` immediately after that
import block. No other line touched.

## 4. Risk & impact on existing functionality

**Blast radius — `shared/components/Text.tsx`:** not modified in this
round (round 1 already unit-tested it in
`shared/components/__tests__/Text.test.tsx`). Adding 8 more consumers to an
unchanged, already-tested component. Full current consumer list after this
round: the 15 round-1 files (`app/login.tsx`, `app/wallet.tsx`,
`app/(tabs)/account.tsx`, `app/ride-in-progress.tsx`,
`app/driver-arrived.tsx`, `app/notifications.tsx`, `app/saved-places.tsx`,
`app/scheduled-rides.tsx`, `app/settings.tsx`, `app/safety-hub.tsx`,
`app/manage-cards.tsx`, `app/pick-on-map.tsx`,
`components/FareQuoteCard.tsx`, `components/BookingProposalCard.tsx`,
`components/ConfirmSheet.tsx`) plus this round's 8
(`app/otp.tsx`, `app/verify-email.tsx`, `app/reactivate-account.tsx`,
`app/legal.tsx`, `app/legacy-consent-notice.tsx`, `app/policies.tsx`,
`app/privacy-settings.tsx`, `app/accessibility.tsx`). driver-app is
untouched (separate parallel session/worktree; this PR does not touch
anything under `driver-app/`).

**Blast radius — the 8 changed files themselves:** each is an
`expo-router` route file, not imported by other component code (routed to
only by string-path navigation), so the change cannot ripple into an
unrelated screen. Within each file the diff is exactly two lines: `Text`
removed from the `react-native` import list, one new import line added.
No JSX, no `<Text>` usage, no prop, no style object was touched.

**Ride state machine / money / insurance periods:** not touched. This is a
pure presentation-layer import swap; no `onPress` handler, API call, or
business-logic branch was altered in any of the 8 files.

**Visual effect, reasoned from the wrapper's documented mapping:** grepped
`fontWeight` values actually used across these 8 files — 13× `'700'`, 9×
`'600'`, 6× `'800'`, 2× `'500'`. `'700'`/`'600'`/`'500'` map exactly to the
matching loaded weight (Bold/SemiBold/Medium); `'800'` (not a loaded
weight) snaps to the nearest loaded step, `'700'` Bold — same snapping
round 1 already exercises and unit-tests. Every `<Text>` in these 8 files
that has no `fontWeight` at all now defaults to
`PlusJakartaSans_400Regular` where it previously rendered the OS system
font at the same implicit normal/400 weight — a font-family change only,
not a weight change, matching the wrapper's own documented invariant.

## 5. User-experience effect

Rider-facing only. Every `<Text>` on these 8 screens (auth OTP/email
verification/account-reactivation, legal document viewer, legacy-consent
notice, policies index, privacy settings, accessibility/WAV toggle) now
renders in Plus Jakarta Sans instead of the OS system font (San
Francisco/Roboto) at the same size and weight as before — a typeface swap,
not a layout, copy, or behavior change. None of these 8 screens is a
screen a rider is typically "mid-session" on during an active ride (they're
auth, legal, and settings screens, not in the ride-tracking flow), so
there's no case here of the treatment changing under a rider already
mid-flow on one of them.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/otp.tsx` | `Text` import moved from `react-native` to `@shared/components/Text` | UX1 round 2 migration |
| `rider-app/app/verify-email.tsx` | same | UX1 round 2 migration |
| `rider-app/app/reactivate-account.tsx` | same | UX1 round 2 migration |
| `rider-app/app/legal.tsx` | same | UX1 round 2 migration |
| `rider-app/app/legacy-consent-notice.tsx` | same | UX1 round 2 migration |
| `rider-app/app/policies.tsx` | same | UX1 round 2 migration |
| `rider-app/app/privacy-settings.tsx` | same | UX1 round 2 migration |
| `rider-app/app/accessibility.tsx` | same | UX1 round 2 migration |
| `ACTION_ITEMS.md` | Added round-2 progress to the existing UX1 bullet (did not rewrite round 1's section) | Tracking |

## 7. Before / after

Representative example (`rider-app/app/accessibility.tsx` — every other
file in this batch is the same class of change):

```
# Before
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
```

```
# After
import { View, StyleSheet, TouchableOpacity, ScrollView } from 'react-native';
import { Text } from '@shared/components/Text';
import { SafeAreaView } from 'react-native-safe-area-context';
```

## 8. Rollback plan

No feature flag — pure client-side import swap, no server dependency, no
data written, no migration. Rollback is a plain `git revert` of the
relevant commit(s) (3 commits, one per flow-grouped batch of ≤3 files, so
any single group can be reverted independently) followed by a normal app
redeploy — acceptable here specifically because nothing in this diff
touches live data, a Stripe charge, a wallet delta, ride state, or
insurance-period rows; reverting the code fully reverts the observable
behavior (screens go back to rendering the OS system font on these 8
screens) with no data-level remediation needed.

## 9. Verification performed

- [x] **`npx tsc --noEmit`** from `rider-app/`: clean, 0 errors.
- [x] **`npx eslint <changed files>`** (the 8 files above): 0 errors, 149
  pre-existing warnings (all `no-restricted-syntax` — hardcoded
  padding/margin/fontSize/hex-color literals, UX2's scope, not this diff's)
  on lines this diff did not touch; no new warnings introduced.
- [x] **`npx jest`** for every existing test file covering a touched
  screen: `__tests__/otpScreen.test.tsx`, `__tests__/legalScreen.test.tsx`,
  `__tests__/policiesScreen.test.tsx`, `__tests__/accessibilityScreen.test.tsx`
  — **4 suites, 47/47 tests passing**. No existing test file covers
  `verify-email.tsx`, `reactivate-account.tsx`, `legacy-consent-notice.tsx`,
  or `privacy-settings.tsx`; none was added, since the change is a pure
  import swap with no new behavior to cover (consistent with round 1's own
  verification, which did not add tests for its files either — the
  wrapper's own behavior is what's unit-tested, in
  `shared/components/__tests__/Text.test.tsx`).
- [x] **Blast-radius grep**: performed and documented in §4 for both
  `shared/components/Text.tsx` (23 total consumers after this round) and
  each of the 8 changed files (all `expo-router` route files, reached only
  by string-path navigation).
- [x] Reviewed against CLAUDE.md conventions: no ride state/money/
  insurance-period code path touched; "Surgical changes" applied (only the
  `Text` import line changed per file, nothing else reformatted).
- [ ] Feature flag: not applicable — see §8 (pure presentational import
  swap, no flag mechanism used or needed).
- **Real production build**: not run. `npm run build`/equivalent for
  rider-app is an Expo/EAS native build, which this environment cannot run
  (no EAS credentials — same constraint the PR template's "Native build
  verification" field documents). `npx expo export --platform web` was
  not run either for this batch; `tsc`+`eslint`+`jest` are what this
  repo's own task guidance calls sufficient for a pure JS/TSX,
  no-native-dependency change of this kind. Flagging explicitly rather
  than implying build parity.
- **Local environment note**: this worktree's `node_modules` had to be
  repaired before `jest` would run at all — `yarn install`'s
  `patch-package` step failed to apply `patches/react-native+0.86.3.patch`
  (pre-existing patch/version drift, unrelated to this diff) and left
  `node_modules/react-native` largely empty as a side effect; repaired by
  copying the same package version's clean, unpatched extraction from
  yarn's local cache into place. This is a local install artifact only —
  nothing under `node_modules/` is committed or part of this diff.

## 10. What was NOT verified

- **No device or simulator in this environment** — every check above is
  `tsc`/`eslint`/`jest`, not a rendered screenshot on iOS/Android.
- **rider-app has no automated visual/snapshot regression tooling at all**
  (per CLAUDE.md's release-gate #6, which explicitly requires this
  disclosure for rider-app/driver-app) — unlike admin-dashboard's
  CI-wired Playwright job, there is nothing to run here. The font-family
  change on these 8 screens was reasoned about (same size/weight, family
  only, per §4's `fontWeight`-to-mapping analysis) — it was not
  screenshotted, and no tooling exists in this repo to do so for
  rider-app.
- **Not tested against a live Supabase/backend** — none of these 8 files'
  data-fetching or auth logic was touched, so this wasn't exercised beyond
  the existing mocked component tests above.
- The 6 files found with partial/manual `fontFamily` coverage (§3) were
  not evaluated for whether their existing manual literals are correct or
  complete — only that they fall outside this item's "no `fontFamily`
  anywhere" scope, which is a status observation, not a review of their
  content.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow without the UX
  field filled in (§5) — and in this case there is no behavior change,
  only a typeface swap
