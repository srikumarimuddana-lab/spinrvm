# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code (agent), for ittalenthire.ca@gmail.com |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | n/a (pure UI, no backend/Sentry-tagged domain touched) |
| PR / commit link | branch `mvapps/focused-tesla-0o88ku` (not yet pushed/opened as a PR by this session) |
| Related issue or gap ID | ACTION_ITEMS.md UX2 (rider-app/driver-app design-system adoption gaps), the previously-undecided "(a) inline-JSX-prop-literal conversion" follow-up scope |

## 1. Issue / gap identified

UX2's `StyleSheet.create()`/`createStyles()`-block sweep (rounds 1–3, PRs #5218/#5225-5228/#5232/#5238-5243) converted every exact-match `padding`/`margin`/`fontSize`/`gap` literal inside a `StyleSheet` block to the shared `SPACING`/`FONT` constants (`shared/utils/responsive.ts`). It explicitly left inline JSX `style={{...}}`/`style={[{...}]}` prop literals out of scope, flagging that scope as "undecided whether to pursue." The user gave the go-ahead to pick this up in this session.

## 2. Root cause

Not a bug — a scoping gap in the prior mechanical sweep, not a defect. Inline JSX-prop style objects (as opposed to `StyleSheet.create()` blocks) were simply never scanned by the earlier rounds.

## 3. Fix / remediation

Ran the same mechanical sweep the earlier rounds used, extended to inline JSX-prop style objects: a script stripped every `StyleSheet.create()`/`createStyles()` block's contents (already covered, to avoid double-counting) and then scanned the remainder of each `.tsx`/`.ts` file under `rider-app/app`, `rider-app/components`, `driver-app/app`, `driver-app/components` for `padding*`/`margin*`/`gap`/`fontSize` keys whose literal numeric value is an **exact** match for a `SPACING` (4/8/16/24/32/48) or `FONT` (32/26/22/16/15/13/11) constant. Only exact matches were swapped — no literal was rounded onto the nearest constant (same discipline as every prior UX2 round), and no literal inside a dynamic expression (e.g. `insets.top + 16`, `Math.max(insets.bottom, 8)`) was touched, since those aren't plain literal assignments.

18 files needed a value swap; 4 of those (`rider-app/app/_layout.tsx`, `rider-app/app/(tabs)/_layout.tsx`, `rider-app/components/VoltraRideActivity.tsx`, `driver-app/app/driver/(tabs)/_layout.tsx`) had never imported `SPACING`/`FONT` before (they were out of scope for every prior round, having no `StyleSheet` block at all) and got a new import added. The other 14 already imported `SPACING`/`FONT` from the earlier `StyleSheet`-block rounds.

A second full re-sweep after the first pass caught 2 literals in `driver-app/app/documents.tsx` missed on the first pass (a prose comment in the same file about `borderRadius`/`fontSize` values produced a false-positive match that visually crowded out the two real hits a few lines away) — fixed in a follow-up commit. A final third re-sweep confirmed zero real hits remain; the only two "hits" the script still reports (`documents.tsx`, `components/AlertDialog.tsx`) are both prose comments referencing the already-migrated `StyleSheet`-block values from earlier UX2 rounds, not code.

## 4. Risk & impact on existing functionality

**Blast radius: multi-surface** (rider-app + driver-app; no shared/backend/admin-dashboard file touched). Every change is a value-identical constant substitution (`SPACING.sm` === `8`, `FONT.bodyMd` === `15`, etc. — see `shared/utils/responsive.ts`) inside an existing inline `style` prop. No component's props, signature, or rendering logic changed; no new component was introduced; no `StyleSheet.create()` block was touched (those were already converted in prior rounds). This cannot change any rendered pixel value.

- No money, auth, dispatch, or safety code path touched.
- `driver-app/components/activity/ActivityView.tsx` triggered the pre-commit hook's "looks shared (referenced by ~8 other files)" warning — checked: the only line changed is a literal `fontSize: 16` → `FONT.bodyLg` (also `16`) inside one `<Text style={[styles.fareAmount, {...}]}>` for the "$0.00" fallback fare display. Every one of its ~8 consumers renders through the same component/prop path; none receives or observes this inline style object directly, so no consumer's behavior is affected.
- `rider-app/components/VoltraRideActivity.tsx` renders through Voltra's native SwiftUI-ish bridge (`@use-voltra/ios-client`), not React Native's `Text`/`View` — flagged as fragile/under-documented in its own file header. Handled conservatively: only the one exact-match `fontSize: 13` → `FONT.bodySm` literal was touched; the file's other non-matching literals (`padding: 14`, `fontSize: 17`, `fontSize: 14`) were left untouched rather than rounded, and no Voltra primitive/prop name was changed.
- `rider-app/app/ride-options.tsx` and `rider-app/app/confirm-pickup.tsx`/`payment-confirm.tsx` already imported `SPACING` (some without `FONT`) from prior rounds — `ride-options.tsx`'s import was widened to add `FONT` where a `fontSize` match required it; no existing import binding was removed or renamed.

## 5. User-experience effect

None. Every swap is value-identical (`SPACING.sm` === `8`, etc.) — rider, driver, and admin see no visual difference. Not visible mid-session to anyone, since nothing renders differently.

## 6. Files modified

27 files across 14 commits (≤3 files per commit, matching CLAUDE.md's batch-size rule):

**rider-app** (17 files): `app/saved-places.tsx`, `app/ride-tracking-webview.tsx`, `app/manage-cards.tsx`, `app/privacy-settings.tsx`, `app/pick-on-map.tsx`, `app/ride-details.tsx`, `app/ride-options.tsx`, `app/verify-email.tsx`, `app/wallet.tsx`, `app/_layout.tsx`, `app/(tabs)/_layout.tsx`, `components/VoltraRideActivity.tsx`, `app/confirm-pickup.tsx`, `app/ride-status.tsx`, `app/payment-confirm.tsx`, `app/driver-arrived.tsx`, `app/(tabs)/activity.tsx`.

**driver-app** (10 files): `app/profile-setup.tsx`, `app/driver/faq.tsx`, `app/driver/payout-history.tsx`, `app/driver/settings.tsx`, `app/driver/payout.tsx`, `app/driver/chat.tsx`, `app/driver/(tabs)/_layout.tsx`, `app/driver/(tabs)/index.tsx`, `components/activity/ActivityView.tsx`, `components/dashboard/DemandLegend.tsx`, `app/documents.tsx`.

Each file: one or more `padding*`/`margin*`/`gap`/`fontSize` literal inside an inline JSX `style` prop, replaced with the matching `SPACING.*`/`FONT.*` constant from `shared/utils/responsive.ts`; a `SPACING`/`FONT` import added to the 4 files that had none.

## 7. Before / after

```tsx
// Before (rider-app/app/saved-places.tsx)
<TouchableOpacity onPress={() => handleDelete(item.id, item.name)} style={{ padding: 8 }}>

// After
<TouchableOpacity onPress={() => handleDelete(item.id, item.name)} style={{ padding: SPACING.sm }}>
```

```tsx
// Before (rider-app/app/(tabs)/_layout.tsx) — no SPACING import existed
import { useTheme } from '@shared/theme/ThemeContext';
...
tabBarStyle: { ..., paddingTop: 8, ... },
tabBarLabelStyle: { ..., marginTop: 4 },

// After
import { useTheme } from '@shared/theme/ThemeContext';
import { SPACING } from '@shared/utils/responsive';
...
tabBarStyle: { ..., paddingTop: SPACING.sm, ... },
tabBarLabelStyle: { ..., marginTop: SPACING.xs },
```

`SPACING.sm === 8` and `SPACING.xs === 4` (see `shared/utils/responsive.ts`), so both diffs are value-identical — no rendered pixel changes.

## 8. Rollback plan

`git-revert-safe` — every change is a pure constant substitution in client-side style objects, no data migration, no server-side change, no destructive write. A plain revert of any or all of these 14 commits restores the exact prior literals with zero functional difference (the constants equal the literals they replace).

## 9. Verification performed

- [x] `tsc --noEmit` clean on both `rider-app` and `driver-app` after every batch and again at the end (re-run against the full final diff).
- [x] Automated tests: full Jest suites re-run for both apps after all edits. rider-app: 157/157 suites, 2153/2153 tests pass, clean. driver-app: first run showed 42 failures in `backgroundMessaging.android.test.ts` (FCM/background-message handling — a file untouched by this change); a clean re-run immediately after came back 156/156 suites, 1834/1834 tests pass — confirms the first run was a pre-existing flake, not a regression from this diff (nothing in this change touches background messaging, FCM, or any file in that test's dependency chain).
- [x] `tsc --noEmit` clean on both apps, checked per-batch and again on the full final diff.
- [x] **Real production build run** (not just `tsc`/dev server, per CLAUDE.md's explicit requirement for any rider-app/driver-app change): `npx expo export --platform web` succeeded for both apps — rider-app exported 3 web bundles + assets to `dist/`, driver-app exported 2 web bundles + assets to `dist/`, both with zero errors.
- [x] Blast-radius grep performed: re-ran the same inline-JSX-prop-literal scanner script against the full `rider-app`/`driver-app` `app`/`components` trees three times (initial pass, a targeted re-check after 2 missed literals were found in `driver-app/app/documents.tsx`, and a final confirmation pass) — zero real remaining hits; the 2 files the scanner still flags are prose comments, verified by reading each line in context.
- [x] Reviewed against relevant CLAUDE.md convention: this is a pure additive/value-identical UI constant-substitution change, not a money/auth/dispatch/safety path — no domain-specific gate applies. `ActivityView.tsx`'s shared-component pre-commit warning addressed above (§4).
- [ ] Manual repro / real-device or staging check — **not performed**. No visual-regression tooling exists for rider-app/driver-app (per CLAUDE.md §6, this is a standing, documented gap for both apps) — this change was reasoned about (value-identical constant substitution, confirmed via the constants' own defined values) rather than screenshotted.
- [x] Feature-flagged if user-visible and non-trivial: not flagged — value-identical, not user-visible, not a UX change; matches CLAUDE.md's flag gate only applying to new/changed UX.

**What was NOT verified:** no real device or Expo Go/simulator visual check was performed (no visual-regression tooling exists for either mobile app, an already-documented gap in CLAUDE.md §6) — verification here is `tsc` + full Jest suites + a real `expo export --platform web` production build for both apps, plus the constants' own defined-equal values, not a screenshot diff.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data-layer dependency).
- [x] Blast radius is stated, not assumed (§4 — multi-surface, both apps' affected files enumerated, no shared/backend file touched).
- [x] No silent behavior change to an already-shipped flow — every swap is value-identical; §5 states explicitly there is no UX effect.
