# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (design-audit follow-up) |
| Surface(s) | rider-app, shared (driver-app unaffected — new files only, no existing driver-app code touched) |
| Domain (Sentry tag) | n/a (pure UI primitives, no backend/domain surface) |
| PR / commit link | (filled in on PR open) |
| Related issue or gap ID | `/design Spinr Apps` audit finding: "The foundational trio — Button, Card, Input — doesn't exist as a reusable primitive anywhere in the repo" |

## 1. Issue / gap identified

`shared/components/` has no reusable Button, Card, or Input primitive. Every screen hand-rolls its own button styling — the audit found three different "primary CTA button" implementations in rider-app alone with three different border-radii (30px, 12px, 28px) for what is semantically the identical action.

## 2. Root cause

The trio was never extracted. Each screen that needed a primary CTA, a card surface, or a labeled text field wrote its own `StyleSheet` block from scratch instead of importing a shared one, so the same visual intent drifted into slightly different implementations over time (radius, padding, loading/disabled handling all vary screen-to-screen).

## 3. Fix / remediation

Added three new shared primitives, each a genuine extraction of an existing, real pattern (not new invention):

- **`shared/components/Button.tsx`** — `variant` (`primary`/`secondary`/`danger`) and `size` (`sm`/`md`/`lg`) props. `size="lg"` (the default) is a direct port of driver-app's `RideOfferPanel` accept/decline buttons — the audit called these "already exemplary" (54px tall, 14px radius, spinner swapped in for the label while busy, disabled without extra dimming). `variant="secondary"` extracts `RideOfferPanel`'s decline button (neutral surface + border). `variant="danger"` extracts the destructive-fill pattern used by rider-app's `ride-completed.tsx` cancel-reason modal (`modalSubmitBtn`, `#EF4444` → `colors.danger`) — grounded in real code, not migrated in this PR.
- **`shared/components/Card.tsx`** — extracted from the card shape that is byte-for-byte identical across `rider-app/components/FareQuoteCard.tsx` and `BookingProposalCard.tsx` (`padding:14, borderRadius:14, backgroundColor:colors.surface, borderWidth:1, borderColor:colors.border`).
- **`shared/components/Input.tsx`** — extracted from the consistent labeled-field pattern in `emergency-contacts.tsx` ("formInput" + label-above-field layout), `become-driver.tsx` ("input") and `report-safety.tsx` ("input") — all `borderRadius:12`, `colors.border`, `fontSize:16`, `colors.text`.
- Migrated the **three specific rider-app CTA buttons the audit cited** (30px/12px/28px) to the new `Button`:
  - `rider-app/app/become-driver.tsx` — the final-step "Submit Application" button only (30px → shared `lg`). The screen's five other buttons that shared the same `styles.primaryButton` (Next: Vehicle / Next: Documents / Review Application / Open Spinr Driver App / Get Started) were **left untouched** — deliberately, to keep this migration to the one button the audit's example points at, not a full-screen rewrite. See §5 for the resulting in-screen radius inconsistency this leaves.
  - `rider-app/app/report-safety.tsx` — "Submit Report" (12px → shared `lg`).
  - `rider-app/app/ride-options.tsx` — "Confirm/Schedule {vehicle} · $fare" (28px → shared `lg`).

Card and Input are **not** migrated anywhere in this PR — landing the primitives plus one proof-of-concept `Button` migration is the intended scope. Full opportunistic migration (of these three components, and of the other `primaryButton`/card/input call sites found while grepping) is explicit future work, per the audit's own recommendation ("migrating screens opportunistically as they're touched").

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** `Button.tsx`, `Card.tsx`, and `Input.tsx` are brand-new files with **zero existing consumers** before this PR. `Card` and `Input` have zero consumers *after* this PR too (landed but not wired up anywhere). `Button` has exactly the three call sites listed above — grepped `rg "from '@shared/components/Button'"` across the repo before and after to confirm no other file imports it.
- The three edited screens (`become-driver.tsx`, `report-safety.tsx`, `ride-options.tsx`) are each touched only at their one migrated CTA. Every other line, handler, and state variable in those files is unchanged. `handleSubmit`, `handleBookRide`, `isLoading`/`submitting`/`isBooking`, and the `disabled` conditions are wired through to `Button` unchanged — same functions, same guards.
- Grepped for any other importer of the removed local styles (`styles.submitButton`/`submitButtonDisabled`/`submitButtonText` in report-safety.tsx; `styles.confirmButton`/`confirmButtonText` in ride-options.tsx; `styles.disabledButton` in become-driver.tsx) — none found outside the edited blocks, so removing the now-dead style keys is safe (CLAUDE.md: "remove only the imports/variables your own change orphaned").
- `driver-app` imports nothing new — `shared/components/Button.tsx` etc. are additive files it never references. Confirmed no other file in `driver-app/` imports `@shared/components/Button|Card|Input` (grep, zero matches) — the new files are a no-op for that app. `driver-app/tsconfig.json` did need one addition (a `paths` entry for `@testing-library/react-native`, matching the pattern already used there for every other bare-specifier import reached from `shared/`, since that tsconfig type-checks all of `../shared/**` including the new test files) — this is a type-check-only config change, not a runtime one.
- Running the full test suite surfaced two **pre-existing tests that depended on internals my Button migration changed**, both fixed in the same commit as their screen's migration:
  - `rideOptionsScreen.test.tsx` mocked `@shared/utils/responsive` with only `useResponsive` stubbed (no `SPACING`/`FONT`) — `Button.tsx` reads `SPACING`/`FONT` at module scope, so the whole suite failed to load. Fixed by spreading `jest.requireActual` under the existing override.
  - `reportSafetyScreen.test.tsx` located the submit button by matching "Submit Report"/"Submitting" label text — no longer possible once the shared Button swaps the label for a spinner while loading. Fixed by adding a `testID` to the Button and switching the test's locator to it.
  - `becomeDriverScreen.test.tsx` needed no changes — it only asserts on `onPress`/toast outcomes for the Submit Application button, never on its loading/disabled internals mid-flight.
- No backend, database, ride-state, payment, or dispatch code is touched. Nothing in this PR reads/writes a Supabase table, a WebSocket event, or a background loop.

## 5. User-experience effect

Rider-facing only, on three specific screens (Become a Driver's final step, Report Safety Issue, and the ride-options "Confirm ride" sheet). Not visible mid-session to someone already on one of these screens before the update ships (a fresh app load picks up the change; there's no persisted state that would show a stale button mid-interaction).

What actually changes, per screen — cosmetic only, no functional/behavioral change:

| Screen | Before | After |
|---|---|---|
| become-driver.tsx (Submit Application) | 30px radius, "Submit" loading via `ActivityIndicator` | 14px radius (shared `lg`), same spinner-swap loading behavior |
| report-safety.tsx (Submit Report) | 12px radius, loading shown as a **text swap** ("Submit Report" → "Submitting...") | 14px radius, loading now shown as a **spinner** (converges on `RideOfferPanel`'s pattern instead of the text-swap) |
| ride-options.tsx (Confirm ride) | 28px radius, dims to 0.5 opacity both when unavailable *and* while booking | 14px radius, dims to 0.5 opacity only when unavailable — no longer additionally dims while the spinner is showing (the spinner alone is the loading affordance) |

Text content, tap targets, and `onPress` handlers are byte-for-byte identical before/after on all three. `become-driver.tsx`'s five other CTA buttons on other wizard steps (Next: Vehicle, Next: Documents, Review Application, Open Spinr Driver App, Get Started) are **unchanged** and still render at the old 30px radius — this PR's scoped migration of only the audit-cited button leaves that one screen with two different radii across its own steps (30px on steps 0-3, 14px on the final step) until the rest of that screen's buttons are migrated in a future PR. Flagging this explicitly rather than letting it read as fully resolved.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/components/Button.tsx` | New file | Shared CTA primitive |
| `shared/components/Card.tsx` | New file | Shared surface-container primitive (unused so far) |
| `shared/components/Input.tsx` | New file | Shared labeled-field primitive (unused so far) |
| `shared/components/__tests__/Button.test.tsx` | New file | Variant/size render, onPress, disabled, loading |
| `shared/components/__tests__/Card.test.tsx` | New file | Render, padding sizes, bordered on/off |
| `shared/components/__tests__/Input.test.tsx` | New file | Label render, onChangeText, error state, editable=false |
| `rider-app/app/become-driver.tsx` | Migrated "Submit Application" button to `Button`; removed now-orphaned `disabledButton` style | Design-audit follow-up (30px radius example) |
| `rider-app/app/report-safety.tsx` | Migrated "Submit Report" button to `Button`; removed now-orphaned `submitButton`/`submitButtonDisabled`/`submitButtonText` styles, kept `marginTop:'auto'` spacing (load-bearing for keyboard-avoidance); added `testID` for test locator | Design-audit follow-up (12px radius example) |
| `rider-app/app/ride-options.tsx` | Migrated "Confirm ride" button to `Button`; removed now-orphaned `confirmButton`/`confirmButtonText` styles, kept `marginTop:10` spacing | Design-audit follow-up (28px radius example) |
| `rider-app/__tests__/reportSafetyScreen.test.tsx` | Locate submit button by `testID` instead of label text; loading-state test now asserts on the spinner instead of removed "Submitting..." text | Existing pinned test broke — see §4 |
| `rider-app/__tests__/rideOptionsScreen.test.tsx` | `@shared/utils/responsive` mock now spreads `jest.requireActual` (keeps real `SPACING`/`FONT`) instead of stubbing the whole module | Existing pinned test broke — see §4 |
| `rider-app/jest-setup-expo.js` | Added `@react-native-async-storage/async-storage` mock | `shared/theme/ThemeContext.tsx` requires it at module scope; no rider-app test previously exercised that path |
| `driver-app/tsconfig.json` | Added a `paths` entry for `@testing-library/react-native` | driver-app type-checks all of `../shared/**` including the new component tests, which needed this bare-specifier resolved the same way every other shared-reachable package already is in this file |

## 7. Before / after

`rider-app/app/report-safety.tsx` (representative of all three — same shape of change):

```tsx
# Before
<TouchableOpacity
    style={[styles.submitButton, submitting && styles.submitButtonDisabled]}
    onPress={handleSubmit}
    disabled={submitting}
>
    <Text style={styles.submitButtonText}>
        {submitting ? 'Submitting...' : 'Submit Report'}
    </Text>
</TouchableOpacity>
```

```tsx
# After
<Button
    variant="primary"
    size="lg"
    onPress={handleSubmit}
    loading={submitting}
    style={styles.submitButtonSpacing}
>
    Submit Report
</Button>
```

## 8. Rollback plan

No feature flag — per CLAUDE.md gate #3, a flag is for changes to a shared component **3+ pages already depend on**; these are brand-new components with exactly one (Button) or zero (Card/Input) consumers, so the gate's own logic treats this as low-risk additive work, not flagged rollout.

If a problem surfaces on one of the three migrated screens: revert that single file's hunk (`git revert` is sufficient here — no Stripe charge, wallet delta, or ride-state row is touched by this change, so there's no live-data remediation needed, only a code revert). Each of the three screen edits is an independent, self-contained hunk — reverting one does not require reverting the others or the new `shared/components/*` files (which have no other consumers to break).

## 9. Verification performed

- [x] Automated tests run — new `Button.test.tsx`/`Card.test.tsx`/`Input.test.tsx` (14 tests, unit, `@testing-library/react-native`) plus rider-app's **full** `yarn test` suite, run twice: once with `--forceExit` alone (143 suites / 1963 tests, all passing) and once with `--coverage` added to confirm `jest.config.js`'s `coverageThreshold` gate (lines 73% / functions 69% / branches 63%) still passes — it does, unaffected since `collectCoverageFrom` only globs rider-app's own directories, not `shared/`. Both runs used `jest.config.js`'s existing `roots: ['<rootDir>', '<rootDir>/../shared']`, which already collects `shared/**/__tests__`.
- [x] `npx tsc --noEmit` run in rider-app — clean.
- [x] `npx tsc --noEmit` run in driver-app — clean, after the one `tsconfig.json` `paths` addition noted in §6 (confirms the new, unimported shared files are otherwise a no-op for that app).
- [x] Blast-radius grep performed: `rg "@shared/components/(Button|Card|Input)"` across the whole repo before and after the change, confirming the only consumers are the three edited call sites plus the new tests.
- [x] Reviewed against CLAUDE.md conventions: no hardcoded hex outside the pre-existing, codebase-wide convention of hardcoded `#FFFFFF` for on-color button/text (same as `RideOfferPanel`, `become-driver.tsx`, `report-safety.tsx`, `ride-options.tsx`, `OfflineBanner.tsx` already do) — all themeable colors go through `useTheme()`. All sizing goes through `shared/utils/responsive.ts`'s `SPACING`/`FONT` tokens.
- [ ] Manual repro / staging check — not performed (no staging environment available in this session); relying on the automated test suite + `tsc` + code-level comparison of before/after JSX for the three migrated screens.

## 10. What was NOT verified

- **No visual regression tooling exists for rider-app** (CLAUDE.md is explicit: rider-app and driver-app have none). The border-radius and loading-indicator changes described in §5 were reasoned about from the source and target styles, not screenshotted. A human should eyeball the three screens (Become a Driver's final step, Report Safety Issue, ride-options' Confirm button) before/after in a simulator or device.
- Not tested against a live Supabase/backend — `handleSubmit`/`handleBookRide` themselves are unchanged, so this is a low-risk gap, but no end-to-end app run (Expo Go / simulator) was done in this session.
- No accessibility audit beyond code-level: `Button` sets `accessibilityRole="button"` and `accessibilityState={{ disabled, busy }}`; this was not verified with an actual screen reader.
- `Card` and `Input` have no real-screen consumer yet, so their only verification is the unit tests plus visual inspection of the source against their extraction basis — they have not been proven correct against an actual rendered screen using them (there isn't one yet).
