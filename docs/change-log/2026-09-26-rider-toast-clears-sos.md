# Change Impact & Risk Log: rider toasts no longer cover SOS

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | rider-app, every in-app toast |
| Domain (Sentry tag) | safety (SOS reachability); toast layout only |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Found by `spinr-safety-sos-reviewer` while reviewing the companion driver-app fix (#5885, merged); precedent #5841 |

## 1. Issue / gap identified

On three ride screens, a rider toast can cover the SOS button and block taps on it for up to 4 seconds.

## 2. Root cause

- **Toast position:** the rider toast (`rider-app/components/Toast.tsx`) is `left: 16, right: 16` at `insets.top + 8`, `zIndex: 9999`, for 4 s.
- **SOS position:** the 44 pt SOS button (`RiderSOS`, small) sits top-right at 16 pt from the edge, in the same band:
  - `ride-in-progress.tsx`: floating overlay at `top: insets.top`, `paddingTop: 8`, `right: 16`.
  - `driver-arriving.tsx`: floating header at `insets.top + 8`.
  - `driver-arrived.tsx`: header row at about `insets.top + 10`.
- **Result:** the toast is a visible, touchable box over SOS, and it is mounted last at the app root (`app/_layout.tsx`), so it draws above it.

## 3. Fix / remediation

- **Right edge:** every rider toast now ends 76 pt from the right edge (`SOS_COLUMN_CLEARANCE`, exported from `components/Toast.tsx`) instead of 16 pt. The left edge is unchanged.
- **Clearance:** SOS's left edge is 60 pt from the right, so the toast leaves a 16 pt gap.
- **Consistency:** this is the same value as the companion driver-app fix (#5885). The two apps keep separate toast components, as decided for W2.3 ("driver-only copy"), so the constant is defined in each.
- **Alternative considered:** shrink the toast only while an SOS button is on screen. Rejected because it needs an "SOS visible" signal from three screens to the root toast. That's more code and a new way to get it wrong, on a safety control.
- **No flag:** unflagged as a safety fix, following #5841. The user chose this on 2026-09-26.

## 4. Risk & impact on existing functionality

- **Blast radius:** every rider toast.
  - The only renderer is `<Toast />` in `rider-app/app/_layout.tsx`.
  - All 35 non-test files that import `store/toastStore` are affected.
  - The driver app is untouched here; its fix is a separate commit.
- **Truncation:** toasts lose 60 pt of width, while the title (1 line) and message (2 lines) limits are unchanged. So long messages end in "…" sooner. On a 320 pt-wide phone, after padding and the icon, the text column shrinks from about 224 pt to about 164 pt, roughly 27% narrower.
- **Unchanged:** the swipe-to-dismiss gesture (`panResponder`), accessibility role, live region and announcement, colours and timing.
- **Tablets, ride in progress (not fixed here):** on screens 768 pt or wider, this screen splits into a map column and a 380 pt side panel.
  - The floating SOS is placed at the map column's top-right, not the screen's, so the full-width toast still covers it for up to 4 s. This is pre-existing and unchanged by this fix.
  - A second SOS stays reachable while a toast shows. The side panel is always visible, and its action row, below the ETA, driver and route cards, has SOS well under the toast band.
  - Recorded as a follow-up.
- **Other header buttons:** the back / cancel button on `driver-arriving` and `driver-arrived` sits at the left and is still covered while a toast shows. That's pre-existing and not changed here.

## 5. User-experience effect

- **Riders (live-tested):** toasts are narrower and end left of the SOS column on every screen.
  - On phones, the floating SOS stays visible and tappable while a toast shows, on all three ride screens.
  - On driver-arriving and driver-arrived, this holds at every width.
  - On tablets during a ride in progress, see §4: the floating SOS can still be covered, but the side panel's SOS stays reachable.
- **Mid-session:** takes effect on the next app update (OTA or build).
- **Drivers and admins:** no change from this commit.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/components/Toast.tsx` | `right: 16` → `SOS_COLUMN_CLEARANCE` (76), exported with an explanation | Toast ends left of the SOS column |
| `rider-app/components/__tests__/Toast.theme.test.tsx` | 2 tests: every variant uses the clearance, and the clearance covers the 44 pt SOS with a gap | Stops a future margin change re-covering SOS |
| `docs/change-log/2026-09-26-rider-toast-clears-sos.md` | This log | Required for a live-tested surface |

## 7. Before / after

```tsx
// Before
container: {
  position: 'absolute',
  left: 16,
  right: 16,
```

```tsx
// After
export const SOS_COLUMN_CLEARANCE = 76;
...
container: {
  position: 'absolute',
  left: 16,
  right: SOS_COLUMN_CLEARANCE,
```

## 8. Rollback plan

**No flag, and no data or backend change.** To roll back, revert the commit and ship the rider app (OTA update or build). Full-width toasts return, and SOS is covered again for up to 4 s while a toast shows.

## 9. Verification performed

- [x] **New tests:** 2 in `Toast.theme.test.tsx`. The first fails with the old `right: 16` (checked) and passes with the fix.
- [x] **Full suite:** `npx jest` passes, 173 suites and 2,342 tests.
- [x] **Typecheck:** `npx tsc --noEmit` passes.
- [x] **Safety review:** `spinr-safety-sos-reviewer` found no blockers and no objection to shipping unflagged. It confirmed:
  - the 16 pt clearance on all three screens at phone width
  - no other touch interceptor
  - swipe-to-dismiss and the announcement are unchanged
  - the 35-file blast radius

  Its should-fixes are the tablet gap (disclosed in §4, follow-up) and this log's cross-reference to the driver fix (corrected).
- [ ] **Production build:** no native or EAS build was run; this is a stylesheet change.

## 10. What was NOT verified

- **No device or simulator check:** the toast's new width and the taps on SOS were reasoned about, not tried on a phone. The rider app has no visual-regression tooling.
- **Style prop only:** the tests check the style prop, not the laid-out position. The jest renderer does no real layout.
- **Tablet layout:** the side-panel SOS position was read from the code, not seen on a tablet.
- **Copy length:** no call site's message was measured at the new width.
