# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (claude-sonnet-5), session `session_016N2vRqybAY6LqEr8Yg7RUB` |
| Surface(s) | driver-app (+ `shared/components/Text.tsx`, pre-existing, consumed unchanged) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see PR description this file is attached to) |
| Related issue or gap ID | ACTION_ITEMS.md UX1 |

## 1. Issue / gap identified

driver-app loads all 4 Plus Jakarta Sans weights at boot
(`driver-app/app/_layout.tsx`'s `useFonts()` call) but most screens never
set `fontFamily` on their `Text` components, so those components silently
rendered the OS system font (San Francisco/Roboto) instead of the brand
typeface. A fresh grep at the start of this task (`grep -rl "fontWeight"
driver-app/app driver-app/components`, cross-checked against zero
`fontFamily` occurrences and a direct `react-native` import) found 42
qualifying files — 0 of them used the fix rider-app already has.

## 2. Root cause

No themed `Text` default existed anywhere in driver-app (or rider-app,
before 2026-09-10). Each screen imports React Native's `Text` directly and
either sets `fontWeight` alone (which only changes weight, not family) or
nothing at all, so the platform default font renders instead of Plus
Jakarta Sans.

## 3. Fix / remediation

Rolled out the same fix rider-app's half of this item already landed and
unit-tested (`shared/components/Text.tsx`, unmodified by this PR): a
drop-in `Text` replacement that derives `fontFamily` from `style.fontWeight`
(400/500/600/700/normal/bold map to the 4 loaded families; other weights
snap to nearest; no `fontWeight` at all defaults to Regular, matching RN's
own implicit default so plain body text's visual weight doesn't change). An
explicit `fontFamily` in `style` still overrides it.

For each of the 42 qualifying files, changed only the `Text` import — from
`import { Text, ... } from 'react-native'` (removing `Text` from that
list) to an added `import { Text } from '@shared/components/Text';`
immediately after it, matching the exact convention already used by
rider-app's merged batch (verified against `rider-app/app/login.tsx`).
Nothing else in any file was touched: no reformatting, no style changes, no
logic changes. Landed in 5 commits of 7–9 files each, grouped by screen
area (dashboard; ride/earnings; auth/onboarding; settings/support;
legal/misc), mirroring UX2/UX3 driver-app's existing batching discipline.

## 4. Risk & impact on existing functionality

**Blast radius — `shared/components/Text.tsx`:** unmodified by this PR.
Its only prior consumers were the 15 rider-app files merged 2026-09-10.
This PR adds 42 driver-app consumers to that same, already-shipped,
unit-tested component. Grepped `from '@shared/components/Text'` across the
repo before and after to confirm exactly the intended 42 files were added
and nothing else changed.

**Blast radius — the 42 files themselves:** several are reused broadly
within driver-app (the pre-commit hook's shared-component heuristic flagged
`ActiveRidePanel.tsx` (~10 referrers), `RideOfferPanel.tsx` (~11),
`ActivityView.tsx` (~8), `CancelReasonSheet.tsx` (~11), `ScreenHeader.tsx`
(~13), `toastConfig.tsx` (~7), `DriverIdlePanel.tsx`/`DriverTopBar.tsx`/
`TripCompletedPanel.tsx` (~4–5 each)). In every case the change is confined
to the file's own `Text` import statement — no prop, export, or public API
of any of these components changed, so every caller of these components is
unaffected beyond the font the component itself now renders with. No
ride-state-machine, money, dispatch, or insurance-period code path is
touched by any of the 42 files or by `shared/components/Text.tsx`.

**Not isolated to driver-app in the repo-wide sense** (the shared component
now has consumers in both apps), but the component itself is unmodified, so
rider-app's existing 15-file adoption cannot regress from this change.

## 5. User-experience effect

Driver-facing only. Every `Text` element in the 42 changed files now
renders in Plus Jakarta Sans instead of the OS system font — a typography
change only, no copy change, no new confirmation step, no changed
validation, no layout change (the wrapper does not alter size/line-height/
padding, only `fontFamily`). Not a mid-session behavior change to an
in-progress flow: a driver already viewing one of these screens when this
ships would, at most, see the same screen re-render in the brand font on
next navigation — no functional state is affected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/activity.tsx` | `Text` import moved from `react-native` to `@shared/components/Text` | UX1 |
| `driver-app/app/driver/(tabs)/index.tsx` | same | UX1 |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | same | UX1 |
| `driver-app/components/dashboard/DemandLegend.tsx` | same | UX1 |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | same | UX1 |
| `driver-app/components/dashboard/DriverTopBar.tsx` | same | UX1 |
| `driver-app/components/dashboard/ForecastStrip.tsx` | same | UX1 |
| `driver-app/components/dashboard/HotspotChips.tsx` | same | UX1 |
| `driver-app/components/dashboard/TripCompletedPanel.tsx` | same | UX1 |
| `driver-app/components/panels/RideOfferPanel.tsx` | same | UX1 |
| `driver-app/components/activity/ActivityView.tsx` | same | UX1 |
| `driver-app/components/charts/EarningsBarChart.tsx` | same (the file's separate `Text as SvgText` import from `react-native-svg` is untouched — different import, different component) | UX1 |
| `driver-app/app/driver/payout.tsx` | same | UX1 |
| `driver-app/app/driver/payout-history.tsx` | same | UX1 |
| `driver-app/app/driver/tax-documents.tsx` | same | UX1 |
| `driver-app/app/driver/subscription.tsx` | same | UX1 |
| `driver-app/app/subscription/success.tsx` | same | UX1 |
| `driver-app/app/login.tsx` | same | UX1 |
| `driver-app/app/otp.tsx` | same | UX1 |
| `driver-app/app/profile-setup.tsx` | same | UX1 |
| `driver-app/app/vehicle-info.tsx` | same | UX1 |
| `driver-app/app/reactivate-account.tsx` | same | UX1 |
| `driver-app/app/legacy-consent-notice.tsx` | same | UX1 |
| `driver-app/app/documents.tsx` | same | UX1 |
| `driver-app/app/crc-consent.tsx` | same | UX1 |
| `driver-app/app/driver/stripe-onboarding.tsx` | same | UX1 |
| `driver-app/app/driver/settings.tsx` | same | UX1 |
| `driver-app/app/driver/notifications.tsx` | same | UX1 |
| `driver-app/app/driver/addresses.tsx` | same | UX1 |
| `driver-app/app/driver/chat.tsx` | same | UX1 |
| `driver-app/app/driver/destination-mode.tsx` | same | UX1 |
| `driver-app/app/driver/faq.tsx` | same | UX1 |
| `driver-app/app/driver/referral.tsx` | same | UX1 |
| `driver-app/app/driver/quests.tsx` | same | UX1 |
| `driver-app/app/appeal.tsx` | same | UX1 |
| `driver-app/app/legal.tsx` | same | UX1 |
| `driver-app/app/policies.tsx` | same | UX1 |
| `driver-app/app/report-safety.tsx` | same | UX1 |
| `driver-app/app/index.tsx` | same | UX1 |
| `driver-app/components/CancelReasonSheet.tsx` | same | UX1 |
| `driver-app/components/ScreenHeader.tsx` | same | UX1 |
| `driver-app/components/toastConfig.tsx` | same | UX1 |
| `ACTION_ITEMS.md` | Updated UX1 entry with driver-app's completed rollout and remaining scope | Tracking |

## 7. Before / after

Representative example — `driver-app/app/login.tsx` (every other file
follows the identical pattern: remove `Text` from the `react-native`
import list, add one new import line directly after it):

```
# Before
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StatusBar,
  ScrollView,
  Image,
} from 'react-native';
```

```
# After
import {
  View,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  StatusBar,
  ScrollView,
  Image,
} from 'react-native';
import { Text } from '@shared/components/Text';
```

No JSX, no style object, and no other line in any of the 42 files changed.

## 8. Rollback plan

No feature flag — this is a pure client-side import swap with no server
dependency, no data written, and no migration. Each of the 5 commits
touches a distinct, non-overlapping set of files (dashboard; ride/earnings;
auth/onboarding; settings/support; legal/misc), so any single commit can be
`git revert`ed independently without affecting the others, restoring that
batch's files to importing `Text` from `react-native` directly. Acceptable
as the sole rollback mechanism because nothing in this diff touches live
data, a Stripe charge, a wallet delta, ride state, or an insurance-period
row — reverting the code fully reverts the observable behavior (the font
each screen renders in) with no data-level remediation needed.

## 9. Verification performed

- [x] **`npx tsc --noEmit`** from `driver-app/`: clean, 0 errors, exit 0.
- [x] **`npx eslint`** on all 42 changed files: 0 errors, 964 pre-existing
  warnings (the repo's existing `no-restricted-syntax` SPACING/FONT/hex-color
  rules — see UX2/UX3 change logs) — all on lines this diff did not touch,
  confirmed no new warning was introduced by comparing warning line numbers
  against this diff's own line ranges (the diff only touches import lines
  near the top of each file).
- [x] **`npx jest`** across the 42 test suites covering every changed
  screen/component (one file had no import match in the initial repo-list
  check but jest's pattern-args pulled in a 42nd matching suite; all
  passed): **508/508 tests passing, 42/42 suites passing**, 0 failures.
- [x] **Blast-radius grep**: performed and documented in §4 for
  `shared/components/Text.tsx` (unmodified; consumer count only) and for
  the individual driver-app files the pre-commit hook's shared-component
  heuristic flagged as widely referenced.
- [x] Reviewed against CLAUDE.md conventions: no ride state/money/insurance
  code path touched; "Surgical changes" applied (only the `Text` import
  line changed in each file, nothing reformatted or cleaned up); "Batch
  size rule" applied (5 commits, 7–9 files each, well under the ~200-line
  diff guidance per commit).
- [ ] Feature flag: not applicable — see §8 (pure import-swap, no flag
  mechanism used or needed, consistent with UX2/UX3's same judgment for
  equivalent presentational-only changes).

**Note on environment**: `driver-app/node_modules` was not present in this
worktree at task start and had to be installed (`yarn install`) before any
of the above could run; a first install attempt raced with an earlier,
manually-backgrounded install this session started and killed, which
corrupted the `react-native` package's extraction (missing ~2,500 of its
~4,600 files, including its root `index.js`) — root-caused via `patch-package`'s
failure log, fixed by copying the missing files back in from yarn's local
cache and re-running `patch-package`, then confirmed clean. This was purely
a local environment/tooling issue surfaced while setting up verification,
not a change to any dependency version, lockfile, or patch file — no
package.json, yarn.lock, or patches/ file is part of this diff.

## 10. What was NOT verified

- **No device or simulator in this environment** — verification is
  `tsc`/`eslint`/`jest`, not a rendered screenshot on iOS/Android.
- **No automated visual regression tooling exists for driver-app at all**
  (per CLAUDE.md's release-gate #6 — rider-app and driver-app have none,
  unlike admin-dashboard's CI-wired Playwright job). A font-family change
  is, by construction, invisible to `tsc`/`eslint`/`jest` — those tools
  confirm the code compiles, lints clean, and existing behavioral
  assertions still pass, but none of them render actual glyphs or compare
  pixels. This change was reasoned about (the wrapper's own existing unit
  tests in `shared/components/__tests__/Text.test.tsx` cover the
  weight-to-family mapping logic; this PR adds no new logic to test), not
  screenshotted, per CLAUDE.md's explicit disclosure requirement for this
  gap.
- **No real native build was run** (`expo export`, EAS build, or
  equivalent) — this environment cannot authenticate `eas-cli` (see the PR
  template's native-build-verification note / CR-2026-008). This is a pure
  JS/TSX import change with no native code or dependency touched, so per
  the task's own instructions a native build was treated as unnecessary
  rather than skipped-and-undisclosed.
- **Not tested against a real device's font metrics** — that Plus Jakarta
  Sans actually renders (vs. falling back silently to a system font again
  for some untested reason, e.g. a font-loading race) was verified by
  reading the wrapper's own logic and its existing test coverage, not by
  rendering and comparing glyphs on-device.
