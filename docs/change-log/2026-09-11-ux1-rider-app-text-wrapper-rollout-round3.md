# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (claude-sonnet-5), session `session_016N2vRqybAY6LqEr8Yg7RUB` |
| Surface(s) | rider-app |
| Domain (Sentry tag) | none fits exactly — general rider-facing UI (root layout, AI assistant, safety report, loyalty/promo/referral, ride-cancel/schedule components, toast) |
| PR / commit link | (see PR description this file is attached to) |
| Related issue or gap ID | ACTION_ITEMS.md UX1 |

## 1. Issue / gap identified

The final 10 rider-app files that render body text in the OS system font instead
of the loaded Plus Jakarta Sans brand typeface, because they still import `Text`
directly from `react-native` instead of the themed `shared/components/Text.tsx`
wrapper. Round 1 (2026-09-10, PR #5214) migrated 15 files; round 2
(2026-09-11, PR #5240) migrated 8 more and left this exact 10-file list as the
follow-up. This round closes it out — rider-app UX1 is now complete.

## 2. Root cause

Round 1 and round 2 both explicitly scoped themselves to manageable batches
and documented the remainder as follow-up (ACTION_ITEMS.md UX1). This is that
follow-up, continued to completion.

## 3. Fix / remediation

Re-grepped `fontWeight` vs `fontFamily` usage across `rider-app/app` and
`rider-app/components` from scratch (per this item's own "don't trust the
prior count, it drifts" warning) rather than trusting round 2's list
verbatim — confirmed via a Python script handling multi-line `react-native`
import blocks (a plain single-line regex under-counts, since several of
these files import `Text` as part of a multi-line destructured import). The
fresh sweep found exactly the same 10 files round 2's note predicted, with
zero drift: none had been touched by any other session's work in the
interim (confirmed — no `@shared/components/Text` import, no `fontFamily`
anywhere in any of the 10).

Migrated all 10, grouped by area and split into 4 commits per this repo's
task-decomposition rule (≤3 files/subtask):

- **Batch 1** (root/misc): `app/_layout.tsx`, `app/ai-assistant.tsx`,
  `app/report-safety.tsx`
- **Batch 2** (rewards/promo): `app/loyalty.tsx`, `app/promotions.tsx`,
  `app/referral.tsx`
- **Batch 3** (ride-cancel/schedule components): `components/CancelReasonSheet.tsx`,
  `components/FreeCancelTimer.tsx`, `components/SchedulePicker.tsx`
- **Batch 4** (isolated, highest blast radius): `components/Toast.tsx`

Each file's change is the same one-line-class swap rounds 1-2 used: remove
`Text` from the `react-native` named-import list (single-line or multi-line
block, as each file had it), add `import { Text } from '@shared/components/Text';`
immediately after. No other line touched in any file.

With this round, **rider-app UX1 is complete** — a fresh sweep after these
commits found zero remaining files matching the criterion (fontWeight-only
`Text`, no `fontFamily` anywhere, not already on the wrapper). Combined with
driver-app's already-complete rollout (PR #5244, 42/42 files), **UX1 as a
whole item is now done in both apps.**

## 4. Risk & impact on existing functionality

**Blast radius — `shared/components/Text.tsx`:** not modified in this round
(unchanged since round 1, already unit-tested in
`shared/components/__tests__/Text.test.tsx`). Adding 10 more consumers to an
unchanged, already-tested component.

**Blast radius — the 10 changed files themselves:**
- 7 are `expo-router` route files (`_layout.tsx`, `ai-assistant.tsx`,
  `report-safety.tsx`, `loyalty.tsx`, `promotions.tsx`, `referral.tsx`, plus
  `_layout.tsx` is the root layout itself — mounted once, not a route reached
  by navigation, but still only renders its own JSX, not a component other
  files import).
- 3 are shared components with real fan-out, grepped directly against the
  current tree:
  - `CancelReasonSheet.tsx` — consumed by `app/driver-arriving.tsx`,
    `app/ride-status.tsx`, `app/driver-arrived.tsx` (3 files).
  - `FreeCancelTimer.tsx` — same 3 consumers.
  - `SchedulePicker.tsx` — consumed by `app/ride-options.tsx` only.
  - `Toast.tsx` — consumed by ~30 files across `app/` (the highest fan-out
    in this round, isolated into its own commit for that reason, same
    discipline round 1 applied to `ConfirmSheet.tsx`).

  In every case the change is confined to the child component's own
  `Text` import — no prop, export, or public API of any of these 4
  components changed, so no consumer's own code or behavior is affected
  beyond the font its child's `Text` now renders with.

**Ride state machine / money / insurance periods:** not touched. Pure
presentation-layer import swap; no `onPress` handler, API call, navigation
logic, or business-logic branch was altered in any of the 10 files.

**Visual effect:** same font-family-only swap as rounds 1-2 — `fontWeight`
values map to the matching loaded Plus Jakarta Sans weight (or snap to the
nearest loaded step for a non-loaded numeric weight), and `Text` with no
`fontWeight` at all now defaults to `PlusJakartaSans_400Regular` where it
previously rendered the OS system font at the same implicit normal/400
weight — family only, not a weight or layout change.

## 5. User-experience effect

Rider-facing only. Every `<Text>` in these 10 files/components (root app
shell, AI assistant chat, safety report form, loyalty/promotions/referral
screens, ride-cancellation reason sheet + free-cancel countdown timer,
schedule-ride date/time picker, and the app-wide toast notification) now
renders in Plus Jakarta Sans instead of the OS system font, at the same
size and weight as before. `Toast.tsx` and the two ride-status-adjacent
components (`CancelReasonSheet`, `FreeCancelTimer`) are the only ones in
this round plausibly visible to a rider mid-ride (a toast can fire at any
time; the cancel sheet and free-cancel timer render during the pre-trip
tracking flow) — in all three cases the change is a typeface swap only, not
a layout, copy, or timing change, so a rider already looking at one of
these when the app updates would see no behavioral difference, only the
brand font on next render.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/_layout.tsx` | `Text` import moved from `react-native` to `@shared/components/Text` | UX1 round 3 migration |
| `rider-app/app/ai-assistant.tsx` | same | UX1 round 3 migration |
| `rider-app/app/report-safety.tsx` | same | UX1 round 3 migration |
| `rider-app/app/loyalty.tsx` | same | UX1 round 3 migration |
| `rider-app/app/promotions.tsx` | same | UX1 round 3 migration |
| `rider-app/app/referral.tsx` | same | UX1 round 3 migration |
| `rider-app/components/CancelReasonSheet.tsx` | same | UX1 round 3 migration |
| `rider-app/components/FreeCancelTimer.tsx` | same | UX1 round 3 migration |
| `rider-app/components/SchedulePicker.tsx` | same | UX1 round 3 migration |
| `rider-app/components/Toast.tsx` | same | UX1 round 3 migration |
| `ACTION_ITEMS.md` | UX1 bullet updated: rider-app round 3 recorded, item closed (`[x]`) as both apps are now fully migrated | Tracking |

## 7. Before / after

Representative example (`rider-app/components/Toast.tsx` — every other file
in this round is the same class of change, single- or multi-line import
block as each file already had it):

```
# Before
import {
  AccessibilityInfo,
  Animated,
  PanResponder,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
```

```
# After
import {
  AccessibilityInfo,
  Animated,
  PanResponder,
  StyleSheet,
  View,
} from 'react-native';
import { Text } from '@shared/components/Text';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
```

## 8. Rollback plan

No feature flag — pure client-side import swap, no server dependency, no
data written, no migration. Rollback is a plain `git revert` of the
relevant commit(s) (4 commits, one per area-grouped batch, so any single
group can be reverted independently) followed by a normal app redeploy —
acceptable here specifically because nothing in this diff touches live
data, a Stripe charge, a wallet delta, ride state, or insurance-period
rows; reverting the code fully reverts the observable behavior (screens go
back to rendering the OS system font) with no data-level remediation
needed.

## 9. Verification performed

- [x] **`npx tsc --noEmit`** from `rider-app/`: clean, 0 errors, after each
  of the 4 batches.
- [x] **`npx eslint`** on all 10 changed files: 0 errors, 218 pre-existing
  warnings (all `no-restricted-syntax` — hardcoded padding/margin/fontSize/
  hex-color literals, UX2's scope, not this diff's; plus one pre-existing
  `react-hooks/exhaustive-deps` warning in `Toast.tsx`, also not touched by
  this diff) on lines this diff did not touch; no new warnings introduced.
- [x] **Blast-radius grep**: performed and documented in §4 for
  `shared/components/Text.tsx` and each of the 4 shared components touched
  in this round (`CancelReasonSheet`, `FreeCancelTimer`, `SchedulePicker`,
  `Toast`).
- [x] Reviewed against CLAUDE.md conventions: no ride state/money/
  insurance-period code path touched; "Surgical changes" applied (only the
  `Text` import line(s) changed per file, nothing else reformatted).
- [ ] Feature flag: not applicable — see §8 (pure presentational import
  swap, no flag mechanism needed).
- **Real production build**: not run. rider-app's `npm run build`/equivalent
  is an Expo/EAS native build, which this environment cannot run (no EAS
  credentials). Pure JS/TSX import swap with no native dependency touched.
- **Jest**: **could not be run in this environment** — `npx jest` fails with
  a `jest-expo`/`@react-native/jest-preset` resolution error. Reproduced the
  identical failure against the untouched, pre-this-round `main` checkout
  (verified during PR #5240's own conflict-resolution work earlier the same
  day), confirming this is a pre-existing sandbox limitation, not something
  this round's changes caused. 8 of the 10 touched files have existing test
  coverage (`aiAssistantScreen.test.tsx`, `reportSafetyScreen.test.tsx`,
  `reportSafetyBackButton.test.tsx`, `loyaltyScreen.test.tsx`,
  `promotionsScreen.test.tsx`, `referralScreen.test.tsx`,
  `Toast.a11y.test.tsx`, `Toast.theme.test.tsx`) that could not be executed
  here; `_layout.tsx`, `CancelReasonSheet.tsx`, `FreeCancelTimer.tsx`, and
  `SchedulePicker.tsx` have no existing test file.

## 10. What was NOT verified

- **No device or simulator in this environment** — every check above is
  `tsc`/`eslint`, not a rendered screenshot on iOS/Android.
- **rider-app has no automated visual/snapshot regression tooling at all**
  (CLAUDE.md release-gate #6) — the font-family change on these 10
  files/components was reasoned about (same size/weight, family only, per
  §4), not screenshotted.
- **Jest could not be executed** (see §9) — the 8 existing test files
  covering 6 of these 10 targets were not run in this environment, though
  the failure is confirmed environmental, not a regression from this diff.
- **Not tested against a live Supabase/backend** — none of these files'
  data-fetching, navigation, or business logic was touched.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behavior change to an already-shipped flow (§5) — typeface
  swap only, no behavior change
- [x] UX1's remaining-scope claim was re-verified against current `main`
  before starting, not trusted blindly (§3) — zero drift found
