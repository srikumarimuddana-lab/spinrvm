# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (claude-sonnet-5), session `session_01173usfHtfdzMMzYpWeWmVm` |
| Surface(s) | driver-app (+ `shared/` primitive also consumed by rider-app) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (see PR description this file is attached to) |
| Related issue or gap ID | ACTION_ITEMS.md UX3 |

## 1. Issue / gap identified

`shared/components/Button.tsx` — the shared button primitive (variants
`primary`/`secondary`/`danger`, sizes `sm`/`md`/`lg`) — had zero real consumers
in driver-app, even though it was originally extracted **from** driver-app's
own `RideOfferPanel` accept/decline buttons. Every driver-app button was an
independently hand-styled `TouchableOpacity`, with several genuinely
different treatments for functionally similar actions.

## 2. Root cause

`Button.tsx` was added (2026-09-04, per
`docs/change-log/2026-09-04-shared-button-card-input-primitives.md`) and
adopted into 6 rider-app screens, but the driver-app side of that migration
was never done — a follow-up that fell off, not a deliberate decision to
keep driver-app on its own styling.

## 3. Fix / remediation

Migrated the real driver-app button call sites this item named, one file
(one commit) at a time, onto the shared `Button`, judging each individually
rather than blanket-converting every `TouchableOpacity`:

- **`RideOfferPanel.tsx`** — Decline → `variant="secondary" size="lg"`.
  Accept stays bespoke (see §4).
- **`AlertDialog.tsx`** — default/destructive branches →
  `variant="primary"`/`"danger" size="md"`. Cancel stays bespoke.
- **`ActivityView.tsx`** — "Try Again" retry pill →
  `variant="primary" size="md" icon="refresh"`. "Load more rides" stays
  bespoke.
- **`documents.tsx`** — "Re-upload Document" →
  `variant="primary" size="sm" icon="cloud-upload-outline"`. The square
  "UPLOAD" tile stays bespoke.
- **`payout.tsx`** — SIN-form and GST-form Cancel+Save pairs →
  `variant="secondary"/"primary" size="sm"`. Settings-list rows (setup
  checklist, Email T4A/CSV) stay bespoke.
- **`shared/components/Button.tsx`** — added one small, additive prop:
  `icon?: keyof typeof Ionicons.glyphMap`, rendered before the label and
  hidden while `loading`. Justified by two real call sites in this same
  migration (`ActivityView`'s retry pill, `documents.tsx`'s re-upload
  button) that both needed a leading icon and had no other way to express
  it through Button's existing API (Button always wraps `children` in one
  `<Text>`, so an icon can't be mixed into `children` itself). No existing
  consumer (6 rider-app files, or the previous driver-app total of zero)
  passes this prop, so none render differently.

Each of the 5 named files was evaluated on its own merits per-button, not
treated as all-or-nothing — several buttons within these same files were
judged genuinely bespoke and deliberately **not** migrated (full list and
reasoning in §4 and in code comments at each site).

## 4. Risk & impact on existing functionality

**Blast radius — `shared/components/Button.tsx`** (grepped
`from '@shared/components/Button'` across the repo): 6 real rider-app
consumers — `rider-app/app/legacy-consent-notice.tsx`,
`reactivate-account.tsx`, `ride-options.tsx`, `report-safety.tsx`,
`become-driver.tsx`, `emergency-contacts.tsx` — plus the new driver-app
consumers added here (`RideOfferPanel.tsx`, `AlertDialog.tsx`,
`ActivityView.tsx`, `documents.tsx`, `payout.tsx`). The `Button.tsx` change
itself is additive-only (one new optional prop with a safe default of
`undefined` → no icon rendered, matching every pre-existing render exactly);
all 6 rider-app consumers' own test suites (185 tests across 7 suites) and
`shared/components/__tests__/Button.test.tsx` (extended with 3 new cases)
pass unchanged. Not isolated to driver-app, but changed in a way that cannot
regress an existing caller.

**Blast radius — the 5 driver-app files:**
- `RideOfferPanel.tsx`: rendered from `driver-app/app/driver/(tabs)/index.tsx`
  (the main driver dashboard tab). Only the Decline button's *rendering*
  changed (fill/border/radius/text now come from Button instead of local
  styles) — its colors were locally-hardcoded near-duplicates of the same
  theme tokens Button uses (see the exact hex comparison in §7), so this is
  a visual no-op, not a new look. `onPress`, `onLongPress`,
  `accessibilityLabel`, and `disabled` wiring are unchanged (passed through
  Button's `TouchableOpacityProps` rest spread). Accept is untouched.
- `AlertDialog.tsx`: mounted once at the app root (`app/_layout.tsx`) and
  used by every `showAlert()` call in the app — including from
  `ActiveRidePanel.tsx` and `TripCompletedPanel.tsx` (both otherwise
  untouched; a different session is working on `ActiveRidePanel.tsx` for an
  unrelated item, so its file was not opened). This is real, wide reach:
  every alert in driver-app renders through this component. The change is
  scoped to *how* 2 of 3 button styles render (fill/radius/text now come
  from Button) — the `AlertButton`/`showAlert()` public API, `handlePress`
  wiring, and the cancel-button branch are unchanged.
- `ActivityView.tsx`: rendered from `app/driver/(tabs)/activity.tsx` only.
- `documents.tsx`, `payout.tsx`: expo-router route files, not imported by
  other component code (only referenced by route-string navigation from
  `useDriverDashboard.ts` / `profile.tsx` / `activity.tsx` / `_layout.tsx`,
  which is unaffected by an in-screen button restyle).

**Ride state machine / money / insurance periods:** not touched. This is a
pure presentation-layer change to existing buttons; no `onPress` handler,
API call, or business-logic branch was altered. `payout.tsx`'s Save buttons
still call the same `handleSaveSin`/`handleSaveGst` (which call
`updateDriverMe.mutateAsync`) with the same validation gates in front of
them — only the button's spinner-vs-label rendering moved from an inline
ternary to Button's `loading` prop, which produces the same rendered output.

## 5. User-experience effect

Driver-facing only (this task never touches rider-app or admin-dashboard
screens). Two categories of visible change:

- **No visible change** (same colors/radius/text, confirmed by exact value
  comparison): `RideOfferPanel` Decline, `AlertDialog` primary/destructive,
  `documents.tsx` Re-upload.
- **Small, deliberate visual change** (documented, not accidental):
  - `ActivityView`'s "Try Again" pill: corner radius 25px → 12px (pill →
    rounded-rect), height 44 → 48 (+4px). Text stays "Try Again" in the
    same primary-red fill with the same 18px `refresh` icon in front of it
    (Button's `md` icon size happens to match the pre-existing icon size
    exactly, so that part is unchanged).
  - `payout.tsx` SIN/GST forms: Cancel gains a 1px `colors.border` border
    it didn't have before (secondary variant's outline).
- None of these are mid-session behavior changes to a flow already in
  progress — buttons render the same way whether a driver has been on the
  screen for one second or one hour; there's no state where the "before"
  and "after" treatments would ever be shown side-by-side to the same
  driver in one session.
- No copy changes, no new confirmation steps, no changed validation.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/Button.tsx` | Added optional `icon?: keyof typeof Ionicons.glyphMap` prop, rendered before the label (hidden while `loading`); updated doc comment | Two real driver-app call sites (below) need a leading icon; additive, no existing consumer affected |
| `shared/components/__tests__/Button.test.tsx` | Added 3 test cases for `icon` (renders, omitted by default, hidden while loading); mocked `@expo/vector-icons` to avoid an unrelated `act()` warning from Ionicons' async font-load | New prop needs test coverage per CLAUDE.md testing conventions |
| `driver-app/components/panels/RideOfferPanel.tsx` | Decline button → `<Button variant="secondary" size="lg" fullWidth={false}>`; `declineBtn`/`declineBtnText` styles simplified/removed | UX3 migration; Accept explicitly not migrated (see §3) |
| `driver-app/components/AlertDialog.tsx` | Primary/destructive branches of the button-rendering loop → `<Button variant="primary"/"danger" size="md">`; `buttonPrimary`/`buttonDestructive`/`buttonTextPrimary`/`buttonTextDestructive` styles removed (orphaned), `buttonFlexBase`/`buttonFullBase`/`buttonFontFamily` added | UX3 migration; Cancel branch explicitly not migrated |
| `driver-app/components/activity/ActivityView.tsx` | Retry pill → `<Button variant="primary" size="md" icon="refresh">`; `retryBtn` style reduced to `{marginTop: 24}`, `retryBtnText` removed | UX3 migration; "Load more rides" explicitly not migrated |
| `driver-app/app/documents.tsx` | Re-upload button → `<Button variant="primary" size="sm" icon="cloud-upload-outline">`; `reuploadBtn` style reduced to `{marginTop: 8}`, `reuploadBtnText` removed | UX3 migration; square "UPLOAD" tile explicitly not migrated |
| `driver-app/app/driver/payout.tsx` | SIN-form and GST-form Cancel+Save pairs → `<Button variant="secondary"/"primary" size="sm">`, `loading` prop replaces manual spinner ternary; `cancelBtn`/`saveBtn` styles reduced to flex-only, `cancelBtnText`/`saveBtnText` removed | UX3 migration |
| `ACTION_ITEMS.md` | Closed the UX3 bullet with what migrated/what didn't | Tracking |

## 7. Before / after

**RideOfferPanel Decline — colors used to be locally hardcoded but were
already near-identical to the theme tokens Button now supplies (confirmed
against `shared/theme/index.ts`):**

| Local literal (before) | Theme token (via Button, after) | Light | Dark |
|---|---|---|---|
| `surfaceBg` | `colors.surfaceLight` | `#F5F5F7` vs `#F5F5F5` (imperceptible) | `#2C2C2E` vs `#2C2C2E` (**exact**) |
| `borderClr` | `colors.border` | `#E5E5EA` vs `#E5E7EB` (imperceptible) | `#3A3A3C` vs `#38383A` (imperceptible) |

```
# Before
<TouchableOpacity style={styles.declineBtn} onPress={() => handleDecline()}
  onLongPress={handleDeclineLongPress} activeOpacity={0.7}
  accessibilityLabel="Decline ride" disabled={isLoading}>
  <Text style={styles.declineBtnText}>Decline</Text>
</TouchableOpacity>
# declineBtn: { flex:1, height:54, borderRadius:14, backgroundColor:surfaceBg,
#   borderWidth:1, borderColor:borderClr, ... }
```

```
# After
<Button variant="secondary" size="lg" fullWidth={false} style={styles.declineBtn}
  onPress={() => handleDecline()} onLongPress={handleDeclineLongPress}
  accessibilityLabel="Decline ride" disabled={isLoading}>
  Decline
</Button>
# declineBtn: { flex: 1 }  — fill/border/radius/text now come from Button
```

**payout.tsx Save button — `loading` prop replaces the manual ternary
(identical rendered output: spinner in place of label while pending, onPress
blocked):**

```
# Before
<TouchableOpacity style={styles.saveBtn} onPress={handleSaveSin} disabled={updateDriverMe.isPending}>
  {updateDriverMe.isPending ? <ActivityIndicator size="small" color="#fff" /> : <Text style={styles.saveBtnText}>Save</Text>}
</TouchableOpacity>
```

```
# After
<Button variant="primary" size="sm" fullWidth={false} style={styles.saveBtn}
  loading={updateDriverMe.isPending} onPress={handleSaveSin}>
  Save
</Button>
```

## 8. Rollback plan

No feature flag — this is a pure client-side presentational refactor with no
server dependency, no data written, and no migration. Rollback is a plain
`git revert` of the relevant commit(s) (5 code commits, one per file, plus
the `Button.tsx`/its test and the `ACTION_ITEMS.md` update — each is a
standalone commit so any single file can be reverted independently without
touching the others) followed by a normal app redeploy — acceptable here
specifically because nothing in this diff touches live data, a Stripe
charge, a wallet delta, or ride state; reverting the code fully reverts the
observable behavior with no data-level remediation needed.

## 9. Verification performed

- [x] **Automated tests**: `npx jest` (driver-app) for every test file
  covering a touched component/screen —
  `__tests__/components/RideOfferPanel.test.tsx`,
  `__tests__/components/ActivityView.test.tsx`,
  `__tests__/app/documentsScreen.test.tsx`, `__tests__/app/payoutScreen.test.tsx`,
  `__tests__/lib/alert.test.ts`, `__tests__/screens/driverOfferPanelWiring.test.ts`,
  `__tests__/app/driverDashboardScreen.test.tsx`, `__tests__/app/activityScreen.test.tsx`,
  `__tests__/app/payoutHistoryScreen.test.tsx`, `__tests__/app/taxDocumentsScreen.test.tsx`
  — **75 + 78 = 153 tests, all passing**, plus (rider-app) the extended
  `shared/components/__tests__/Button.test.tsx` (8/8) and all 6 real
  rider-app `Button` consumers' own screen tests (7 suites, 185/185
  passing) to confirm the additive `icon` prop regresses nothing there.
- [x] **`npx tsc --noEmit`**: clean (0 errors) from both `driver-app/` and
  `rider-app/` (both include `../shared/**/*.tsx` in their `tsconfig.json`).
- [x] **`npx eslint <changed files>`**: 0 errors from `driver-app/` (334
  pre-existing warnings, all on lines this diff didn't touch — no new
  warnings introduced) and from `rider-app/`. **Gap**: `Button.tsx` itself
  reports "ignored because outside of base path" from both apps' flat
  ESLint configs (their `no-restricted-syntax` design-token rules are
  scoped to each app's own `app/`/`components/`/`store/` globs, which
  don't reach `../shared/`) — this is a pre-existing config limitation, not
  something this diff caused or could route around from either app's
  config. Reviewed the `icon` addition manually against those same rules
  (no hardcoded hex, no raw padding/margin/fontSize literal added) instead.
- [x] **Real production build**: `cd driver-app && npx expo export --platform web`
  — completed clean (exit 0, `Exported: dist`, 7.5MB web bundle produced).
- [x] **Blast-radius grep**: performed and documented in §4 for both
  `Button.tsx` (6 rider-app + 5 driver-app consumers) and each of the 5
  driver-app files (who imports/renders them).
- [x] Reviewed against CLAUDE.md conventions: no ride state/money/insurance
  code path touched; "Simplicity first" applied per-button (several buttons
  deliberately left unmigrated rather than forcing a variant/prop that
  wasn't a real fit); "Surgical changes" applied (only orphaned
  styles/props from this exact diff were removed, nothing adjacent
  reformatted).
- [ ] Feature flag: not applicable — see §8 (pure presentational refactor,
  no flag mechanism used or needed).

## 10. What was NOT verified

- **No device or simulator in this environment** — every check above is
  `tsc`/`eslint`/`jest`/a static web export, not a rendered screenshot on
  iOS/Android. The two "small, deliberate visual change" items in §5 (retry
  pill radius, Cancel's new border) were reasoned about from exact style
  values, not screenshotted.
- **No visual regression tooling exists for driver-app at all** (per
  CLAUDE.md's release-gate #6) — unlike admin-dashboard's Playwright job,
  there is no baseline to diff against and none was skipped; this is the
  standing, pre-existing gap for this surface, disclosed rather than
  assumed away.
- ESLint could not directly lint `shared/components/Button.tsx` itself from
  either app's config (see §9) — covered instead by `tsc`, manual review
  against both apps' rule text, and both apps' consumer test suites passing.
- `AlertDialog.tsx` has no dedicated component-render test (only
  `lib/alert.ts`'s `showErrorAlert` — a different file — has one); the
  primary/destructive migration there is verified by `tsc` + manual
  code-path review + the production build succeeding, not by a test
  asserting on its rendered button tree.
- Not tested against a real device's font metrics — `PlusJakartaSans_600SemiBold`
  passed via `textStyle` on the migrated `AlertDialog` buttons was verified
  by reading the prop through, not by rendering and comparing glyphs.
