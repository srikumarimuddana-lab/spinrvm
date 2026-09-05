# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | (added at PR open) |
| Related issue or gap ID | Live-testing bug report 2026-09-05: "the vehicle disappeared from the screen cause it didn't recenter and the vehicle went on the route not visible on the map itself" |

## 1. Issue / gap identified

`MapControls.tsx`'s recenter ("locate") button always renders the same static, filled `colors.primary` icon whether or not the follow-camera is currently engaged. A driver who panned the map away (or the follow camera silently disengaged, e.g. via `onPanDrag`) had no visual cue that the map had stopped following the car — the button looks identical whether tapping it would do something or is a no-op repeat, and there's nothing telling the driver *why* the car isn't recentering on its own. This is one contributing factor to the reported "vehicle disappeared and didn't recenter."

## 2. Root cause

`followRef` (the flag the follow-camera effect checks) is a plain `useRef`, not React state, by design — it's read every GPS tick and a ref avoids re-rendering the whole screen on every fix. But that also means nothing in the render tree ever reflected its value, so the recenter button had no way to show "off-follow" even though the underlying state already existed.

## 3. Fix / remediation

Added a small `isFollowing` boolean **state** that mirrors `followRef.current`, updated at each of the ref's 4 existing write sites (`onPanDrag`, hotspot-chip press, `onRecenter`, `onToggleCourseUp`). Passed `isFollowing` down to `MapControls` as a new optional prop (defaults to `true` — matches prior always-following-icon behavior for any caller that doesn't pass it). The locate icon now swaps to an outline glyph (`locate-outline`) in `colors.textSecondary` when off-follow, vs. the existing filled `locate` in `colors.primary` while following — the same filled/accented-vs-outline-muted convention Uber/Lyft use for this control.

## 4. Risk & impact on existing functionality

- Blast radius: `MapControls` has exactly one real caller (`driver-app/app/driver/(tabs)/index.tsx`) — confirmed via grep across `driver-app/` (the only other hits are the component's own definition and a test file, `__tests__/app/driverDashboardScreen.test.tsx`, that fully mocks the component to `() => null` and only inspects props via `findByType`, so an added prop doesn't affect it).
- `followRef` itself is unchanged in meaning/usage by the follow-camera effect — this change only adds a state mirror alongside every existing assignment, it doesn't alter when or how following is engaged/disengaged.
- `isFollowing` defaults to `true` if omitted, so any future caller that doesn't pass it renders exactly the prior always-filled-icon look — no silent behavior change for an unmigrated caller.
- Ran the existing `driverDashboardScreen.test.tsx` suite (49 tests, all mock `MapControls` but still assert on its props including `onRecenter`) — all 49 pass unchanged.

## 5. User-experience effect

Driver-facing only. Purely additive visual state on an existing, always-present button — no new screen, no copy, no new user action required. Visible mid-session to any driver currently online (the control is always rendered while the map is up), and is a direct response to a live-testing pain point (no indication of why the car wasn't recentering).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/dashboard/MapControls.tsx` | Added optional `isFollowing` prop (default `true`); recenter button icon/color/accessibility label now vary on it | Give the driver a visible "following vs. off-follow" state instead of a static icon |
| `driver-app/app/driver/(tabs)/index.tsx` | Added `isFollowing` state mirroring `followRef.current`; set at all 4 existing `followRef.current` write sites; passed to `<MapControls>` | Bridge the existing ref-only follow flag into something the render tree can reflect |

## 7. Before / after

```tsx
// Before
<TouchableOpacity style={styles.btnInner} onPress={handleRecenter} activeOpacity={0.7} accessibilityRole="button" accessibilityLabel="Center map on my location">
  <Ionicons name="locate" size={24} color={colors.primary} />
</TouchableOpacity>
```

```tsx
// After
<TouchableOpacity
  style={styles.btnInner}
  onPress={handleRecenter}
  activeOpacity={0.7}
  accessibilityRole="button"
  accessibilityLabel={isFollowing ? 'Center map on my location' : 'Resume following my location'}
  accessibilityState={{ selected: isFollowing }}
>
  <Ionicons
    name={isFollowing ? 'locate' : 'locate-outline'}
    size={24}
    color={isFollowing ? colors.primary : colors.textSecondary}
  />
</TouchableOpacity>
```

## 8. Rollback plan

No feature flag — purely additive visual state with a backward-compatible default (`isFollowing = true` renders identically to the pre-change icon). Rollback is a plain `git revert`; no live data, ride state, or money path touched. Mobile changes only ship on `[build]`-tagged EAS releases per `CLAUDE.md`'s Deployment section, so this has a natural review gate before reaching drivers regardless.

## 9. Verification performed

- [x] Automated tests: `npx jest __tests__/app/driverDashboardScreen.test.tsx` — 49/49 passed (includes the `map recenter control` test asserting `onRecenter` still calls `refreshLocation(false)`).
- [x] `npx tsc --noEmit -p tsconfig.json` for the full driver-app project — clean, 0 errors.
- [ ] Manual repro / staging visual check — not performed this session (no device/simulator available in this environment); see "What was NOT verified" below.
- [x] Blast-radius grep performed: every `MapControls` usage and every `followRef.current` assignment site in `driver-app/`.
- [x] Reviewed against relevant `CLAUDE.md` conventions: surgical/additive change, default preserves prior behavior for any un-migrated caller, no PIPEDA-relevant data, no state-machine/money path touched. Also checked against `spinr-accessibility-reviewer`'s domain: added an explicit `accessibilityState.selected` and a state-describing `accessibilityLabel` rather than leaving the label static, since the icon's meaning now changes.
- [ ] Feature-flagged: not flagged. Justification: additive icon-state change to an existing always-visible control, backward-compatible default, no behavior change to what the button *does* (still calls the same `onRecenter`/`refreshLocation` path) — only its visual state.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert; default prop value keeps behavior identical if reverted mid-flight).
- [x] Blast radius is stated, not assumed (single real caller confirmed via grep; test file confirmed unaffected by re-running it).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 completed).

## What was NOT verified

Not visually screenshotted or run on a physical device/simulator in this session — driver-app has no visual-regression tooling at all (per `CLAUDE.md`), so the actual on-screen appearance of the outline-vs-filled icon swap is reasoned about (Ionicons' `locate-outline` glyph exists in the same icon set already used elsewhere in this file, and `colors.textSecondary` is an existing theme token used for muted UI elsewhere) rather than confirmed by eye. Recommend a quick visual check on the next test build — pan the map, confirm the icon dims, tap recenter, confirm it re-fills.
