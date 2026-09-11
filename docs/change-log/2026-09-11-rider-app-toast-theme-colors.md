# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (feat/51) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | n/a (UI-only; no Sentry-tagged path touched) |
| PR / commit link | af41906 (this branch, `mvapps/sleepy-galileo-hqp2xn`) |
| Related issue or gap ID | ACTION_ITEMS.md UX6 (follow-up to UX5, closed 2026-09-10) |

## 1. Issue / gap identified

`rider-app/components/Toast.tsx`'s `VARIANT_CONFIG` hardcoded toast background
colors (`info:'#1a73e8'`, `success:'#0d9f6e'`, `warning:'#d97706'`,
`danger:'#dc2626'`) that matched neither `shared/theme/index.ts`'s current nor
previous tokens, and the component never called `useTheme()` — so toast colors
were both off-brand and identical in light/dark mode (no dark-mode adaptation
at all).

## 2. Root cause

Identical bug to UX5 (`driver-app/components/toastConfig.tsx`, fixed
2026-09-10, commit `1d25b15`) — confirmed byte-for-byte matching stale hex
values at the time UX5 was investigated. UX5's own write-up explicitly flagged
rider-app's copy as "not fixed here... worth a follow-up item if picked up."
This PR is that follow-up.

## 3. Fix / remediation

Ported UX5's exact pattern: a new `variantConfig(colors, variant)` helper
resolves the background color from `colors.info`/`success`/`warning`/`danger`
(the live theme, read via `useTheme()`) instead of the static hex map. Icon
glyph names stay a static `ICON_NAMES` lookup (unchanged behavior — not a
color-drift concern, same split UX5 used). Nothing else in the component
changed: `toastStore.ts`'s `ToastVariant` type, the swipe-to-dismiss gesture,
enter/exit animation, auto-dismiss timer, and accessibility announcement logic
are all untouched.

## 4. Risk & impact on existing functionality

- **Blast radius**: `Toast.tsx` is rendered once near the app root and driven
  by the `useToastStore` zustand store; **37 files** call `showToast(...)`
  across rider-app (grepped). None of those call sites change — they all pass
  `(title, message?, variant?, duration?)` exactly as before. Only the
  background-color computation inside `Toast.tsx` itself changed.
- **No API/behavior/prop contract change**: `ToastVariant` (`'info' |
  'success' | 'warning' | 'danger'`) is unchanged; every existing
  `showToast(...)` call keeps working identically.
- **Visual-only diff**: background color shifts from the old off-brand hex to
  the current theme token per variant (e.g. `danger` `#dc2626` →
  light-theme `colors.danger` `#EF4444`), and now genuinely differs between
  light and dark mode instead of being identical in both. No layout, sizing,
  animation, or gesture change.
- **Precedent**: same fix, same day-prior session, same repo, same review
  path as UX5 on driver-app — no issues found there after merge.

## 5. User-experience effect

- Rider-facing. Every toast a rider sees (payment failures, ride
  cancellations, booking confirmations, and every other `showToast(...)`
  caller) now renders with the current on-brand color per variant, and
  correctly adapts between light and dark mode instead of staying static.
- **Visible mid-session**: yes, potentially — if a rider has the app open
  when this ships, the very next toast they see (of any kind) uses the new
  color. This is a color-only change with no behavior/text/timing difference,
  so there is nothing for a rider to be confused by mid-flow.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/components/Toast.tsx` | Static hex `VARIANT_CONFIG` replaced with a `useTheme()`-driven `variantConfig()` helper | Fix off-brand, non-dark-mode-aware toast colors (UX6) |
| `rider-app/components/__tests__/Toast.theme.test.tsx` | New regression test — light-mode tokens for all 4 variants + dark-mode adaptation via a real `ThemeProvider` | Prevent this exact regression recurring, mirroring UX5's own test |
| `ACTION_ITEMS.md` | New `UX6` entry, closed | Backlog bookkeeping per this repo's convention for closed items |

## 7. Before / after

```tsx
// Before
const VARIANT_CONFIG: Record<ToastVariant, { bg: string; icon: string }> = {
  info: { bg: '#1a73e8', icon: 'information-circle' },
  success: { bg: '#0d9f6e', icon: 'checkmark-circle' },
  warning: { bg: '#d97706', icon: 'warning' },
  danger: { bg: '#dc2626', icon: 'alert-circle' },
};
// ...
const config = VARIANT_CONFIG[current.variant];
```

```tsx
// After
function variantConfig(colors: ThemeColors, variant: ToastVariant) {
  const bgByVariant: Record<ToastVariant, string> = {
    info: colors.info,
    success: colors.success,
    warning: colors.warning,
    danger: colors.danger,
  };
  return { bg: bgByVariant[variant], icon: ICON_NAMES[variant] };
}
// ...
const { colors } = useTheme();
// ...
const config = variantConfig(colors, current.variant);
```

## 8. Rollback plan

`git-revert-safe` — a plain `git revert` of this commit fully restores the
old static hex map with no other state to clean up: no schema, no
server-side config, no feature flag, no data written anywhere. This is a pure
client-side, stateless render-path change.

## 9. Verification performed

- [x] `npx jest components/__tests__/Toast` (rider-app) → 5/5 passing (2 new
  theme tests + the pre-existing 3-test `Toast.a11y.test.tsx` suite,
  confirming no accessibility regression).
- [x] `npx tsc --noEmit` (rider-app) → clean.
- [x] `npx eslint components/Toast.tsx` → 0 errors; `no-restricted-syntax`
  hardcoded-hex warnings dropped from **14 → 10** (verified via a
  before/after `git stash` comparison) — the remaining 10 are pre-existing,
  out of this fix's scope (fixed-contrast white text/icon on a colored
  surface, a documented exception, and padding/fontSize literals, UX2's
  territory) — same categories UX5 reported leaving.
- [x] Blast-radius grep performed: 37 `showToast(...)` call sites across
  rider-app confirmed to need no changes.
- [x] Reviewed against relevant CLAUDE.md conventions: not a state-machine,
  money, RLS, or PIPEDA-relevant surface; shared-component-Change-Impact-Log
  convention followed here (this document) despite the identical driver-app
  precedent (UX5) not producing one — added for this session's own
  consistency, not because UX5 was found lacking.
- [x] **Feature-flag decision**: not flagged, deliberately. CLAUDE.md's gate
  #3 asks for a flag on "anything touching a shared component used by 3+
  pages" when the change is "user-visible and non-trivial" — this change is
  user-visible but is a strict color-token correction with zero behavior,
  prop, timing, or layout change, and zero blast radius on any of the 37
  call sites. UX5 (the identical bug, same day prior, driver-app) shipped
  the same way, unflagged, with no issues since. A dark-mode toast that now
  actually looks different from its light-mode counterpart is the intended,
  corrected behavior, not a new risk surface to gate.

## 10. What was NOT verified

- Not visually screenshotted on a real device/simulator (iOS/Android) — this
  is a React Native component; verification here was automated test
  assertions on the computed `backgroundColor` style value, not a rendered
  screenshot. rider-app has no visual-regression tooling at all (per
  CLAUDE.md), so this is reasoned about via the exact same theme-token
  source (`shared/theme/index.ts`) already used and screenshotted-in-effect
  by every other themed rider-app component, not independently
  screenshotted here.
- Not tested against a live device's actual dark-mode OS setting — the dark
  theme was exercised via `ThemeProvider` + a mocked `AsyncStorage` persisted
  preference (matching UX5's own test technique), not a real device
  dark-mode toggle.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data to clean up)
- [x] Blast radius is stated, not assumed (37 call sites grepped, confirmed unaffected)
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 above)
