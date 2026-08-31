# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (session on behalf of vikas@ngitservices.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (branch `claude/map-vehicle-tracking-animation-3e85y2`, commit `24e1861`) |
| Related issue or gap ID | Finding from a design-consistency audit (`spinr-design-consistency-reviewer`), 2026-08-31 |

## 1. Issue / gap identified

`driver-app/components/activity/ActivityView.tsx` (the earnings/trip-history screen) did not call `useTheme()` at all — every color in the file was a hardcoded light-mode literal, unlike every other dashboard/panel component in the app. Flagged as the single most systemic theme-parity gap found in driver-app.

## 2. Root cause

The file was written with a module-level `StyleSheet.create({...})` (a common RN pattern) before the app adopted the `createStyles(colors)` factory + `useTheme()` pattern used elsewhere (`DriverIdlePanel.tsx`, `notifications.tsx`) — it was never migrated when that pattern was introduced.

## 3. Fix / remediation

Converted the trailing `StyleSheet.create({...})` into `function createStyles(colors: ThemeColors) { return StyleSheet.create({...}) }`, called `const { colors } = useTheme(); const styles = useMemo(() => createStyles(colors), [colors]);` at the top of the component, and replaced every hardcoded hex literal with the matching theme token (primary, warning, success, textDim, textSecondary, text, border, surfaceLight, surface). Status badges in `renderRideCard` now use `successBg`/`warningBg`/`dangerBg` — the tokens `shared/theme/index.ts` documents specifically as "background behind status icons / pill badges" — replacing ad hoc `rgba(...,0.1)` literals.

Two colors have no shared/theme equivalent (a purple accent for bonus/quest amounts, a sky blue for the "Avg per Trip" stat) — kept as named module constants (`BONUS_PURPLE`, `AVG_TRIP_BLUE`) matching the values `RideOfferPanel.tsx` already uses for the same purpose, rather than inventing new shared design-system tokens for two decorative icon accents.

One deliberate non-conversion: `statusPillActive`'s dark fill stays a literal `#1F2937` rather than `colors.text`, because `colors.text` flips to near-white in dark mode and would put the pill's white text on a white background — documented inline. Shadow color and white-on-solid-fill text (`pillTextActive`, `retryBtnText`) also kept as literals with inline comments (shadow tint isn't theme-relevant; white text needs fixed max contrast against a fixed-brand-color fill regardless of theme).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one file.** `ActivityView.tsx` is a screen-level component; grepped for other importers of its internal `createStyles`/style objects — none exist (styles are private to the file, as before). `renderRideCard`'s `useCallback` deps were updated from `[router]` to `[router, styles, colors]` to keep the memoized render function in sync with the now-theme-dependent styles, the only functional (non-purely-cosmetic) change in the diff.
- No data-fetching, ride-state, or earnings-calculation logic touched — purely a style-source change.
- `useTheme()` is already globally mocked in the driver-app test setup, so the existing `ActivityView.test.tsx` suite required no changes and still exercises the same behavior.

## 5. User-experience effect

- **Driver-facing.** Previously: this screen ignored the driver's theme setting and always rendered in light-mode colors, unlike the rest of the app. Now: it respects dark mode like every other screen. Visible to any driver using dark mode who navigates to this screen — a visual-only change, no change to what data is shown or how it's calculated.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/activity/ActivityView.tsx` | `useTheme()` + `createStyles(colors)` factory; hardcoded hex → theme tokens; two documented `eslint`-clean literal exceptions | Close the theme-parity gap |

## 7. Before / after

```tsx
// Before
const styles = StyleSheet.create({
  card: { backgroundColor: '#fff', borderColor: '#E5E7EB' },
});

// After
function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    card: { backgroundColor: colors.surface, borderColor: colors.border },
  });
}
// ...
const { colors } = useTheme();
const styles = useMemo(() => createStyles(colors), [colors]);
```

## 8. Rollback plan

`git revert` of commit `24e1861` — client-side style-only change, no data, no migration, no flag needed.

## 9. Verification performed

- [x] Full driver-app suite: **127/127 suites, 1432/1432 tests passing** (one `backgroundMessaging.android.test.ts` flake seen on an initial run, reproduced as unrelated to this change — passes standalone and in two subsequent full-suite re-runs, and passes on `main` without this change too via `git stash`).
- [x] `npx tsc --noEmit` — clean.
- [x] `npx eslint` — clean (0 errors, 0 warnings) on the changed file.
- [x] Blast-radius grep: confirmed no other file imports `ActivityView.tsx`'s internal styles/constants.
- [ ] Manual/visual on-device verification (light vs dark mode) — not performed; no device/emulator in this environment, and driver-app has no visual-regression tooling (standing gap, `ACTION_ITEMS.md`).

## 10. What was NOT verified

- No on-device/visual confirmation that dark mode now renders correctly — reasoned about via the same token-mapping every other themed screen in the app already uses, not screenshotted.
- The rest of the original design-consistency audit's driver-app findings (`DriverIdlePanel.tsx` untracked green gradient, `DriverTopBar.tsx`'s half-finished dark-mode text-color parity, two silently-logged-not-surfaced failures, reduce-motion respected in 0 of ~6 animation sites) were **not** addressed in this change — scoped to the one finding requested.
