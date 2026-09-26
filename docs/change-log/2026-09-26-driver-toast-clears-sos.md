# Change Impact & Risk Log: driver toasts no longer cover SOS

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | driver-app, every in-app toast |
| Domain (Sentry tag) | safety (SOS reachability); toast layout only |
| PR / commit link | UX program W2.3 prerequisite — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Plan W2.3 (SOS overlap found while designing the unified toast); precedent #5841 |

## 1. Issue / gap identified

During an active ride, a driver toast can cover the SOS control and swallow taps on it for up to 3.5 seconds.

## 2. Root cause

- **Toast position:** `showToast` (`hooks/useToast.ts`) shows every toast 60 pt from the top, full width (16 pt margins), for 3.5 s.
- **SOS position:** on the driver home, SOS sits top-right at `right: 16`, `top: sosTop`. That's `SOSButton` (44 pt), or `SafetyShield` (46 pt, with a 54 pt ring while held) when the discreet-SOS flag is on.
- **Result:** both occupy the same top-right corner, and the toast draws above SOS (Android `elevation: 10`).

## 3. Fix / remediation

- **Right margin:** every toast now ends 76 pt from the right edge (`SOS_COLUMN_CLEARANCE`, exported from `components/toastConfig.tsx`) instead of 16 pt. The left margin is unchanged.
- **Clearance:** SOS's widest extent is the SafetyShield ring, which overflows its 46 pt wrap by 4 pt a side. That puts its left edge 66 pt from the right, so the toast leaves a 10 pt gap.
- **Why taps reach SOS:** react-native-toast-message's full-width wrapper is `pointerEvents="box-none"`, so taps outside the visible toast pass through to SOS.
- **Alternative considered:** move toasts to the bottom of the screen. Rejected because the bottom sheet (ride card, go-online control) lives there, and every driver would learn a new toast position mid-testing. The narrower top toast changes only its width.
- **No flag:** this is unflagged as a safety fix, following #5841 (SOS moved below the navigation banner, unflagged).

## 4. Risk & impact on existing functionality

- **Blast radius:** every driver-app toast.
  - The only renderer is `<Toast config={toastConfig} />` in `app/_layout.tsx`.
  - All 25 non-test files that import `hooks/useToast` are affected. There are no other `Toast.show` callers.
  - The rider app has its own toast and is untouched.
- **Truncation:** toasts lose 60 pt of width.
  - The title is 1 line and the message is 2 lines (`numberOfLines`), and the character clamps are unchanged (60 / 140 in `shared/utils/toastMessage.ts`).
  - So a long message may now end in "…" sooner. On a 320 pt-wide phone the text column shrinks by about a quarter.
  - Example: the 91-character "safety report submitted" message (`app/report-safety.tsx:146`) already ran close to 2 lines and now truncates slightly earlier.
  - The screen-reader announcement reads the full clamped text, so it is unaffected.
- **Accessibility wiring:** the role, live region and announcement are unchanged (`toastConfig.a11y.test.tsx` still passes).
- **Unchanged:** colours, timing and position from the top.
- **Upcoming W2.3 banner:** the flagged unified banner (not yet merged) will use the same constant.

## 5. User-experience effect

- **Drivers (live-tested):** toasts are narrower and end left of the SOS column on every screen, not only during rides.
  - SOS stays visible and tappable while a toast shows.
  - Long toast messages may truncate earlier.
- **Mid-session:** takes effect on the next app update (OTA or build); nothing changes for a driver on the current build.
- **Riders and admins:** no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/components/toastConfig.tsx` | Right margin 16 → `SOS_COLUMN_CLEARANCE` (76), exported with an explanation | Toast ends left of the SOS column |
| `driver-app/__tests__/components/toastConfig.theme.test.tsx` | 2 tests: every variant uses the clearance, and the clearance covers the widest SOS control | Stops a future margin change re-covering SOS |
| `docs/change-log/2026-09-26-driver-toast-clears-sos.md` | This log | Required for a live-tested surface |

## 7. Before / after

```tsx
// Before
container: {
  marginHorizontal: SPACING.md,
```

```tsx
// After
export const SOS_COLUMN_CLEARANCE = 76;
...
container: {
  marginLeft: SPACING.md,
  marginRight: SOS_COLUMN_CLEARANCE,
```

## 8. Rollback plan

**No flag, and no data or backend change.** To roll back, revert the commit and ship the driver app (OTA update or build). The previous full-width toast returns; SOS is covered again for up to 3.5 s while a toast shows.

## 9. Verification performed

- [x] **New tests:** 2 in `toastConfig.theme.test.tsx`. The first fails on the old stylesheet (the margin was 16).
- [x] **Safety review:** `spinr-safety-sos-reviewer` found no blockers and judged it safe to ship unflagged. It confirmed:
  - 76 pt clears the SOSButton by 16 pt and the SafetyShield ring by 10 pt, at every `sosTop`, since only the vertical offset changes.
  - The library wrapper is `box-none`.
  - Paint order comes from the root mount order, not zIndex or elevation.
  - The announcement path is unchanged.
- [x] **Full driver-app suite:** `npx jest` passes, 172 suites and 2,140 tests.
- [x] **Typecheck:** `npx tsc --noEmit` passes.
- [x] **Library behaviour:** the `box-none` wrapper claim was checked against the installed react-native-toast-message 2.5.0 source.
- [ ] **Production build:** no native or EAS build was run; this is a stylesheet change covered by jest and tsc.

## 10. What was NOT verified

- **No device or simulator check:** the tap-through to SOS and the new width were reasoned about, not tried on a phone. The driver app has no visual-regression tooling.
- **Style prop only:** the new test checks the style prop, not the laid-out position. The jest renderer does no real layout.
- **Rider app, not fixed here:** the rider app has the same overlap.
  - Its own toast (`rider-app/components/Toast.tsx`) is full width at `insets.top + 8` for 4 s.
  - SOS floats in that band on `ride-in-progress.tsx` and in `driver-arriving.tsx`'s header, and possibly on `driver-arrived.tsx`.
  - The rider app gets the same 76 pt clearance in its own PR (`2026-09-26-rider-toast-clears-sos.md`).
- **Copy length:** no call site's message was measured at the new width.
