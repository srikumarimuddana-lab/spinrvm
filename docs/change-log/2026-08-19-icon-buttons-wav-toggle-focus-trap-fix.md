# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Eng (agent-assisted), on behalf of vikas@ngitservices.com |
| Surface(s) | rider-app, driver-app, admin-dashboard |
| Domain (Sentry tag) | n/a (accessibility, no Sentry-tagged domain applies) |
| PR / commit link | local worktree commits — see commit SHAs in task report |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` — ranked blocker #22, finding N6 |

## 1. Issue / gap identified

Four icon-only buttons on high-traffic screens (rider ride-status close button,
rider report-safety back button, driver ride-detail map back button, and —
already fixed separately for `role="dialog"`/`aria-modal`/Escape — the admin
document-reviewer modal's missing focus management) had no accessible name or
keyboard-focus handling. Separately, the driver-app WAV (wheelchair-accessible
vehicle) declaration toggle in Settings did not announce as a switch or report
its on/off state to a screen reader — a WCAG 2.1 AA and SK Transportation Act
accessibility-adjacent gap (WAV request fulfillment depends on this
declaration being set correctly, including by low-vision/blind drivers).

## 2. Root cause

- The three icon buttons were built as bare `<TouchableOpacity>` wrapping an
  `<Ionicons>` glyph with no `accessibilityLabel`/`accessibilityRole` — a
  sighted-only pattern that happened not to follow this codebase's own
  established convention (already used correctly elsewhere, e.g.
  `rider-app/app/search-destination.tsx:451` `accessibilityLabel="Go back"`,
  `driver-app/components/dashboard/MapControls.tsx` `accessibilityRole="button"`).
- The document-reviewer modal (`role="dialog"`/`aria-modal="true"`/Escape-key
  handling) was fixed in an earlier pass but never got focus-trap-in
  (moving focus to the dialog on open) or focus-restore-out (returning focus
  to the trigger on close) — so a keyboard/screen-reader user opening it kept
  focus on whatever was behind the modal.
- The WAV toggle is a hand-rolled `<TouchableOpacity>` "pill" (not RN's
  built-in `Switch`), and unlike the codebase's own `CustomToggle` component
  (`rider-app/components/CustomToggle.tsx`), the driver-app settings screen's
  local `renderToggle()` helper never set `accessibilityRole="switch"` or
  `accessibilityState={{ checked: value }}`.

## 3. Fix / remediation

- Added `accessibilityRole="button"` + a concise `accessibilityLabel`
  ("Close" / "Go back" / "Go back") to each of the 3 icon-only buttons,
  matching this codebase's existing labeling convention exactly (verified by
  grepping both apps for prior `accessibilityLabel` usage on icon-only
  buttons before choosing wording).
- Added `accessibilityRole="switch"` + `accessibilityState={{ checked: value }}`
  + `accessibilityLabel={label}` to the shared `renderToggle()` TouchableOpacity
  in `driver-app/app/driver/settings.tsx` — this is the exact pattern already
  used by `rider-app/components/CustomToggle.tsx`.
- Added focus-trap-in / focus-restore-out to
  `admin-dashboard/.../document-reviewer.tsx`:
  - A `previouslyFocusedRef` captures `document.activeElement` when the modal
    opens; a `useEffect` cleanup (fires when `open` flips false or the
    component unmounts) restores focus to it.
  - On open, focus moves to the dialog's first focusable element (falling
    back to the dialog container itself, which now has `tabIndex={-1}`).
  - The existing keydown handler gained a `Tab`/`Shift+Tab` branch that
    cycles focus within the dialog's focusable elements instead of leaking
    focus to the page behind it.
  - No new npm dependency — `admin-dashboard/package.json` has no
    focus-trap library and no reusable focus-trap hook/util exists elsewhere
    in the codebase (grepped for `focus-trap`/`trapFocus`/`useFocusTrap`, no
    hits), so this is a minimal, dependency-free `ref`/`useEffect`
    implementation reusing the dialog's own DOM.

## 4. Risk & impact on existing functionality

**Blast radius, per site — all additive, no behavior/visual/logic change:**

1. `rider-app/app/ride-status.tsx` (close button, ~line 451) — isolated to
   this one `TouchableOpacity`. No other consumer of this button.
2. `rider-app/app/report-safety.tsx` (back button, ~line 56) — isolated,
   single consumer.
3. `driver-app/app/driver/ride-detail.tsx` (map overlay back button, ~line
   211) — isolated, single consumer.
4. `admin-dashboard/.../document-reviewer.tsx` — the component is rendered
   from exactly one place in the codebase
   (`admin-dashboard/src/app/dashboard/drivers/page.tsx` and
   `.../drivers/queue/page.tsx`, both passing the same `open`/`driverId`/
   `onClose` props); no other page imports `DocumentReviewer`. The new
   `Tab` handler only activates while the dialog is open (guarded by the
   existing `if (!open) return;` at the top of the same effect) and only
   intercepts `Tab` — no change to the existing `Escape`/`j`/`k`/`a`/`r`
   shortcuts, all of which are unmodified and re-verified passing.
5. `driver-app/app/driver/settings.tsx` `renderToggle()` — this is a
   **shared local helper**, not scoped to only the WAV toggle: it backs 8
   other toggles on the same screen (push notifications, ride alerts,
   earnings summary, promotions, marketing email, marketing SMS, sound
   effects, vibration, dark mode). All 10 call sites on this screen gain the
   same `accessibilityRole="switch"` + `accessibilityState` semantics as a
   side effect of fixing the one the audit named (WAV). This is intentional
   and desirable (matches the audit's own stated intent — screen-reader users
   get the same fix for every toggle on the screen, not just WAV) and is
   purely additive: no `onToggle`/`onPress` logic, colors, or layout changed,
   only new `accessibility*` props on the existing `TouchableOpacity`. No
   other file imports or calls this local (non-exported) function — it is
   defined and used only within `settings.tsx`.

No shared component/hook/utility used by 3+ *pages* was touched (renderToggle
is local to one screen, not exported). No ride state, money, dispatch, auth,
corporate, or safety code path was touched.

## 5. User-experience effect

- Screen-reader (VoiceOver/TalkBack) users on rider-app and driver-app now
  hear a real name ("Close" / "Go back") when focusing these 3 buttons,
  instead of "button" with no label. Sighted users see zero visual change —
  no styling, sizing, or tap-target change.
- Screen-reader users on driver-app Settings now hear "switch, on/off" (or
  platform equivalent) for all 10 toggles on the screen, including WAV, and
  the announced state matches what's actually saved server-side. No visible
  change to any toggle's appearance or its save/revert-on-error behavior.
- Keyboard/screen-reader users on the admin document-reviewer modal now land
  inside the dialog on open (instead of losing their position) and return to
  the exact control they used to open it, on close — e.g. tabbing back into
  the driver row's "Review" button instead of the top of the page. Not
  visible to a mouse-only admin user; visible mid-session only in the sense
  that opening/closing the modal now moves focus differently for AT users —
  this is the intended fix, not a regression.
- None of the 5 changes is visible to a rider/driver mid-*ride* — all 5 sites
  are outside the active-ride state machine.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-status.tsx` | Added `accessibilityRole="button"` + `accessibilityLabel="Close"` to the header close `TouchableOpacity` | Unlabeled icon-only button (audit #22) |
| `rider-app/app/report-safety.tsx` | Added `accessibilityRole="button"` + `accessibilityLabel="Go back"` to the header back `TouchableOpacity` | Unlabeled icon-only button (audit #22) |
| `driver-app/app/driver/ride-detail.tsx` | Added `accessibilityRole="button"` + `accessibilityLabel="Go back"` to the map overlay back `TouchableOpacity` | Unlabeled icon-only button (audit #22) |
| `driver-app/app/driver/settings.tsx` | Added `accessibilityRole="switch"`, `accessibilityLabel={label}`, `accessibilityState={{ checked: value }}` to the shared `renderToggle()` `TouchableOpacity` | WAV toggle doesn't announce as a switch (audit N6); applies to all 10 toggles on the screen (see §4) |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.tsx` | Added `dialogRef`/`previouslyFocusedRef`, a focus-trap-in/restore-out `useEffect`, a `Tab`/`Shift+Tab` cycling branch in the existing keydown handler, and `tabIndex={-1}` on the root dialog `div` | Modal was missing focus-trap-in/restore-out (audit #22, partial re-open of prior modal fix) |
| `driver-app/__tests__/screens/settingsWavToggle.test.tsx` | New test: WAV toggle announces `accessibilityRole="switch"` with correct `accessibilityState.checked` in both on/off states | Regression coverage |
| `driver-app/__tests__/screens/rideDetailBackButton.test.tsx` | New test: ride-detail back button has a non-empty `accessibilityLabel` and `accessibilityRole="button"` | Regression coverage |
| `rider-app/__tests__/reportSafetyBackButton.test.tsx` | New test: report-safety back button accessibility | Regression coverage |
| `rider-app/__tests__/rideStatusCloseButton.test.tsx` | New test: ride-status close button accessibility | Regression coverage |
| `admin-dashboard/src/app/dashboard/drivers/_components/document-reviewer.test.tsx` | Added 2 tests: focus moves into the dialog on open; focus restores to the triggering element on close | Regression coverage |

## 7. Before / after

**Icon buttons** (same pattern applied 3×, `rider-app/app/ride-status.tsx` shown):

```tsx
# Before
<TouchableOpacity style={styles.backButton} onPress={handleBackPress}>
  <Ionicons name="close" size={24} color={colors.text} />
</TouchableOpacity>

# After
<TouchableOpacity
  style={styles.backButton}
  onPress={handleBackPress}
  accessibilityRole="button"
  accessibilityLabel="Close"
>
  <Ionicons name="close" size={24} color={colors.text} />
</TouchableOpacity>
```

**WAV / notification toggles** (`driver-app/app/driver/settings.tsx`):

```tsx
# Before
<TouchableOpacity
    activeOpacity={0.8}
    onPress={() => onToggle(!value)}
    style={[styles.toggle, { backgroundColor: value ? `${colors.primary}60` : colors.surfaceLight }]}
>

# After
<TouchableOpacity
    activeOpacity={0.8}
    onPress={() => onToggle(!value)}
    accessibilityRole="switch"
    accessibilityLabel={label}
    accessibilityState={{ checked: value }}
    style={[styles.toggle, { backgroundColor: value ? `${colors.primary}60` : colors.surfaceLight }]}
>
```

**Document-reviewer focus trap** (new behavior, additive — no prior code to diff against for the effect itself; the root `div` gained a ref/tabIndex):

```tsx
# Before
<div className="fixed inset-0 z-[100] ..." role="dialog" aria-modal="true" aria-label="Document reviewer">

# After
<div
    ref={dialogRef}
    tabIndex={-1}
    className="fixed inset-0 z-[100] ... outline-none"
    role="dialog"
    aria-modal="true"
    aria-label="Document reviewer"
>
```

## 8. Rollback plan

All 5 changes are purely additive (`accessibility*` props or a focus-management
`useEffect`), with no schema, flag, or config coupling — a plain `git revert`
of the relevant commit(s) is a complete and sufficient rollback for every file
in this change. Nothing here touches Stripe, wallet, ride state, or any other
live-data path, so no data-level remediation is needed on rollback. No
feature flag was used or needed — these are non-trivial only in the "audit
blocker" sense, not in the "could reject previously-valid input" or
"changes an existing decision" sense the flagging guidance is aimed at; they
change nothing a sighted, mouse/touch-only user can observe.

## 9. Verification performed

- [x] Automated tests run — **real test runs, not just `tsc --noEmit`**:
  - `driver-app`: `npx jest` — full suite, **67/67 suites, 558/558 tests passed** (2 new files: `settingsWavToggle.test.tsx`, `rideDetailBackButton.test.tsx`).
  - `rider-app`: `npx jest` — full suite, **65/65 suites, 532/532 tests passed** (2 new files: `reportSafetyBackButton.test.tsx`, `rideStatusCloseButton.test.tsx`).
  - `admin-dashboard`: `npx vitest run` — full suite, **33/33 files, 329/329 tests passed** (2 new tests added to the existing `document-reviewer.test.tsx`).
- [x] **Real production build run** for admin-dashboard: `npm run build` (`next build`) completed with exit code 0, compiled successfully, generated all 73 static/dynamic routes. This is the actual `npm run build` CLAUDE.md requires, not just a dev server or `tsc --noEmit`.
- [x] Blast-radius grep performed: searched both apps for existing `accessibilityLabel`/`accessibilityRole="switch"` usage to match convention before writing the fix; searched admin-dashboard for `DocumentReviewer` import sites (2 call sites, same props shape); searched for `focus-trap`/`trapFocus`/`useFocusTrap` (no existing utility) and confirmed no focus-trap library in `admin-dashboard/package.json`; confirmed `renderToggle()` in `settings.tsx` is a local, non-exported function used only by that file's 10 call sites.
- [x] Reviewed against relevant CLAUDE.md conventions: WCAG 2.1 AA (Saskatchewan Regulatory → Accessibility), no ride-state/money/RLS path touched so those conventions don't apply here.
- [x] Not feature-flagged — additive accessibility-only change with no user-visible sighted-UX difference and no risk of rejecting previously-valid input; flagging would add complexity with no corresponding risk reduction (see §8).

## 10. What was NOT verified

- No real screen-reader device pass (VoiceOver on iOS/macOS, TalkBack on
  Android) was performed — verification here is limited to asserting the
  correct `accessibility*` props are present and carry the correct values via
  RTL/`react-test-renderer` queries (`getByLabelText`, `.props.accessibilityRole`,
  `.props.accessibilityState`), the same level of verification the prior
  Toast screen-reader fix (`docs/change-log/2026-08-19-toast-screen-reader-fix.md`)
  used and explicitly flagged as its own boundary.
- No visual regression tooling exists in this repo for any of the 3 apps (a
  standing gap — see `ACTION_ITEMS.md`), so "no visible diff" for all 5
  changes was reasoned about from the diff (accessibility props only, no
  style/layout props touched), not screenshotted.
- The admin-dashboard focus-trap `Tab`-cycling logic was exercised only via
  RTL's `document.activeElement` assertions (focus lands in the dialog on
  open, returns to the trigger on close); the `Tab`/`Shift+Tab` wrap-around
  behavior itself (cycling from last back to first focusable element and
  vice versa) was written to the same pattern as common accessible-dialog
  implementations but not separately unit-tested keystroke-by-keystroke —
  a fast-follow if this needs tighter coverage.
- Not tested against a live Supabase/staging deployment — all four
  driver-app/rider-app/admin-dashboard test suites run against mocked API
  responses (`@shared/api/client` mocked, `getDriverDocuments`/`reviewDocument`
  mocked in the admin test).
- The 4-th icon button named in the task ("document-reviewer.tsx already
  gained role/aria-modal/Escape") was, per the task's own framing, already
  fixed for those three attributes in an earlier pass — this change only
  adds the remaining focus-trap-in/restore-out piece, not a re-verification
  of the earlier `role="dialog"`/`aria-modal`/Escape fix (re-confirmed
  present in the file, unchanged, but not re-tested beyond the existing
  7 tests that already covered the component's core approve/reject flow).
