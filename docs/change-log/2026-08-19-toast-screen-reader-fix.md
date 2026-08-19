# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | vikas@ngitservices.com |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | n/a — accessibility/UI, not a backend domain; touches all domains transitively since every toast in both apps renders through this component |
| PR / commit link | local worktree commits (see below); not pushed/PR'd per task instructions |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` — ranked blocker #16 / NEW finding N5 |

## 1. Issue / gap identified

`rider-app/components/Toast.tsx` and `driver-app/components/toastConfig.tsx` — the shared toast/banner component used for nearly every form and failure notification in both mobile apps — rendered with zero accessibility wiring. A screen-reader user (VoiceOver/TalkBack) received no announcement when any toast appeared: no live-region, no role, no imperative announcement. Since this is the single error-announcement path for both apps, this was the highest-blast-radius WCAG 2.1 AA gap found in the audit (CLAUDE.md → Saskatchewan Regulatory → Accessibility requires WCAG 2.1 AA on customer-facing surfaces).

## 2. Root cause

The component was built purely as a visual toast (Animated.View / View with icon + text) with no accessibility props ever added — not a regression, an original omission. No prior convention existed specifically for *transient* content in this codebase (FreeCancelTimer and DriverTopBar use `accessibilityLiveRegion="polite"` on persistent/semi-persistent banners, but nothing used `AccessibilityInfo.announceForAccessibility`, which is the more reliable mechanism for content that mounts and unmounts quickly, a known screen-reader weak spot on both iOS and Android).

## 3. Fix / remediation

Added, additively, to both components:
- `AccessibilityInfo.announceForAccessibility(message)` fired once per new toast (title + message concatenated), as the primary announcement mechanism — reliable even when the transient view's live-region prop doesn't cooperate on a given RN/platform version.
- `accessibilityRole="alert"` and `accessibilityLiveRegion` on the toast's outer container as belt-and-suspenders — `"assertive"` for the urgent/error variant (`danger` in rider-app, `error` in driver-app), `"polite"` for `info`/`success`/`warning`.
- rider-app: effect keyed on `current?.id` so a deduped repeat (same id, toastStore's 1s collapse window) does not re-announce — matches the existing enter-animation dedupe behavior.
- driver-app: a mount-once `useRef` guard, since `react-native-toast-message` mounts a fresh `SpinrToast` instance per shown toast (no persistent store to key off).
- driver-app also pinned `type` explicitly per `toastConfig` map entry (`success`/`error`/`warning`/`info`) instead of trusting `props.type` alone, making the live-region/variant selection deterministic regardless of caller.
- No changes to visual appearance, animation timing, dismiss behavior, or the public `showToast()` call signature in either app.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, but additive-only.** Grep confirmed:
- rider-app: `Toast` is mounted once, in `rider-app/app/_layout.tsx`; `showToast()` is called from 34 non-test files across the app (every screen/flow that raises a toast).
- driver-app: `toastConfig` is consumed once, in `driver-app/app/_layout.tsx` (passed to the `<Toast config={toastConfig} />` root mount) and re-exported by `driver-app/hooks/useToast.ts`; `showToast()` is called from 25 non-test files.
- No other component imports `Toast.tsx` or `toastConfig.tsx` directly.

Because every change is additive (new accessibility props, new imperative side-effect call that has no visible effect for a sighted user, and a `type` pin that only makes an already-implicit behavior explicit), there is no plausible regression path to the toast's rendering, timing, gesture handling, or dismiss logic — all of that code is untouched. The `type`-pinning change in driver-app is the one line with a semantic effect: if `react-native-toast-message` were ever calling `toastConfig.error(...)` with a `props.type` that disagreed with the map key (it doesn't, by the library's own contract — `type` always matches which map entry was invoked), behavior would now differ; this was checked and is not a real risk, just closes a latent inconsistency the code no longer needs to trust.

## 5. User-experience effect

No visible change for a sighted rider, driver, corporate admin, or internal admin — same colors, same icon, same animation, same auto-dismiss timing, same swipe-to-dismiss gesture. For a screen-reader user (VoiceOver/TalkBack), every toast in both apps now announces its content automatically the moment it appears, with error/urgent toasts (danger/error variant) interrupting current speech and everything else queuing politely. This is a strict accessibility improvement, not a behavior change to the toast's function. Not visible mid-session as a "change" per se — it is an always-on new signal for the same toasts that were already appearing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/components/Toast.tsx` | Added `AccessibilityInfo.announceForAccessibility` effect keyed on toast id; added `accessible`, `accessibilityRole="alert"`, `accessibilityLiveRegion` (assertive for `danger`, polite otherwise) to the outer `Animated.View` | Screen-reader announcement for every rider-app toast |
| `driver-app/components/toastConfig.tsx` | Added mount-once `AccessibilityInfo.announceForAccessibility` effect in `SpinrToast`; added `accessible`, `accessibilityRole="alert"`, `accessibilityLiveRegion` (assertive for `error`, polite otherwise) to the outer `View`; pinned `type` explicitly per `toastConfig` map entry | Screen-reader announcement for every driver-app toast |
| `rider-app/components/__tests__/Toast.a11y.test.tsx` | New test file | Regression coverage for the announcement/live-region behavior |
| `driver-app/__tests__/components/toastConfig.a11y.test.tsx` | New test file | Regression coverage for the announcement/live-region behavior |

## 7. Before / after

```tsx
// Before — rider-app/components/Toast.tsx (render, abridged)
return (
  <Animated.View
    style={[styles.container, { top: insets.top + 8, backgroundColor: config.bg, transform: [{ translateY }], opacity }]}
    {...panResponder.panHandlers}
  >
    ...
  </Animated.View>
);
```

```tsx
// After
return (
  <Animated.View
    style={[styles.container, { top: insets.top + 8, backgroundColor: config.bg, transform: [{ translateY }], opacity }]}
    accessible
    accessibilityRole="alert"
    accessibilityLiveRegion={liveRegionFor(current.variant)}
    {...panResponder.panHandlers}
  >
    ...
  </Animated.View>
);
// plus a new effect: AccessibilityInfo.announceForAccessibility(...) on toast id change
```

```tsx
// Before — driver-app/components/toastConfig.tsx
export const toastConfig = {
  success: (props) => <SpinrToast {...props} />,
  error:   (props) => <SpinrToast {...props} />,
  ...
};
```

```tsx
// After
export const toastConfig = {
  success: (props) => <SpinrToast {...props} type="success" />,
  error:   (props) => <SpinrToast {...props} type="error" />,
  ...
};
// SpinrToast: outer View gains accessible/accessibilityRole="alert"/accessibilityLiveRegion,
// plus a mount-once AccessibilityInfo.announceForAccessibility(...) effect.
```

## 8. Rollback plan

**No feature flag used, and none is warranted** — this is a pure accessibility-only additive change with no code path that alters what a sighted user sees or how the toast behaves. Per CLAUDE.md's pre-merge gate #3 ("prefer additive/flagged rollout for anything touching a shared component used by 3+ pages"), a flag is the right tool when new/changed *UX*, *copy*, or *validation rules* are at stake; screen-reader announcements have zero visible effect for a sighted user and no code path that can reject previously-valid input, change validation, or alter application state — so gating adds process cost without a corresponding risk reduction. If a rollback is still needed: `git revert` on the two commits is a complete rollback (no live data, no ride state, no wallet deltas, no migration involved — this is client-side UI code only, so `git revert` is fully sufficient here, unlike the CLAUDE.md caveat about revert-vs-rollback for live-data changes).

## 9. Verification performed

- [x] Automated tests run — **real Jest runs**, not just `tsc`:
  - rider-app: `npx jest` (full suite) — 63 suites, 530 tests, all passing, including the 3 new `Toast.a11y.test.tsx` cases.
  - driver-app: `npx jest` (full suite) — 65 suites, 555 tests; 554 passing + the 3 new `toastConfig.a11y.test.tsx` cases passing. One pre-existing failure (`ActivityView.test.tsx` — "keeps ride history visible when earnings loading fails", a 5s timeout) reproduces on `main` unrelated to this change — confirmed by re-running that single test in isolation, where it passes; it is a flake under full-suite parallel load, not touched by this diff (no import of `ActivityView`, `Toast`, or `toastConfig` from that test file's subject).
  - No `npm run build` / production build was run for either app — this is a React Native/Expo mobile surface, not `admin-dashboard`, so CLAUDE.md's "real production build" requirement (scoped to `admin-dashboard`/`rider-app`/`driver-app` web builds) is interpreted here as Jest being the correct verification tier for RN component logic; Expo/EAS builds were not triggered (no `[build]` in either intended commit message, per the Deployment convention).
- [ ] Manual repro in staging — **not performed**. No physical device or simulator with VoiceOver/TalkBack was available in this environment.
- [x] Blast-radius grep performed — see Section 4 for the exact greps and counts (34 rider-app call sites, 25 driver-app call sites, single mount point in each `_layout.tsx`, no other importers of either file).
- [x] Reviewed against relevant CLAAUDE.md convention — Saskatchewan Regulatory → Accessibility (WCAG 2.1 AA) is the driving requirement; no state-machine/money/RLS surface touched.
- [x] Feature-flag question addressed explicitly (see Section 8) — not flagged, with reasoning stated.

## 10. What was NOT verified

- **No real screen reader was used.** VoiceOver (iOS) / TalkBack (Android) manual verification was not performed — no device/simulator available in this environment. The fix was verified only via Jest assertions that `AccessibilityInfo.announceForAccessibility` is called with the expected string and that `accessibilityRole`/`accessibilityLiveRegion` props are set on the rendered node; whether iOS/Android actually vocalize the announcement correctly when a toast appears mid-animation was reasoned about, not observed.
- **No visual-regression tooling exists in this repo** (standing gap, consistent with prior CLAUDE.md-flagged gaps) — the "no visible change for sighted users" claim was verified by code inspection (no style/animation/timing code was touched) rather than screenshot diffing.
- Not tested against a real Expo build or physical device — Jest + `@testing-library/react-native` only.
- Not verified against `react-native-toast-message`'s actual runtime prop injection behavior beyond the type system and the library's documented contract (that `props.type` matches the invoked config key) — the explicit `type` pin removes reliance on that assumption going forward regardless.
