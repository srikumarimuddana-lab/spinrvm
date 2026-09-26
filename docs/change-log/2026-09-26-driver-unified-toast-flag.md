# Change Impact & Risk Log: driver unified-toast flag (W2.3)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code, UX program W2.3 |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x`, in two PRs: the backend half first, then the driver-app half |
| Related issue or gap ID | `.claude/plans/2026-09-25-ux-enhancement-program.md` W2.3, decision D4; `docs/audit/2026-09-25-ux-scorecard-world-class-minimal.md` gap 3 ("two toast systems") |

**Decisions (user, 2026-09-26):**

1. **Driver-only copy.** Nothing moves to `shared/` and `rider-app/` is not touched. The unified toast is a driver-app component and store, modelled on `rider-app/components/Toast.tsx` and `rider-app/store/toastStore.ts`, and used only when the flag is on.
2. **The flag comes from `/drivers/config`,** set in `useDriverDashboard.ts` the same way migration 487's `android_auto_offer_tone_enabled` is.
3. **Keep the driver app's behaviour** under the flag:
   - a fixed 60 px top offset;
   - ending `SOS_COLUMN_CLEARANCE` (76 pt) from the right, like the current toast after the SOS fix, so SOS stays visible and tappable (user chose "narrow both toasts");
   - no de-duplication;
   - 3.5 s duration;
   - `error` maps to the danger styling.

## 1. Issue / gap identified

The rider and driver apps show toasts through two different implementations:

- **driver-app:** `react-native-toast-message` with `components/toastConfig.tsx`.
- **rider-app:** its own animated banner.

The UX program (W2.3) moves driver toasts onto the rider-style banner, dark-launched behind `driver_unified_toast_enabled`.

## 2. Root cause

The apps grew their toasts independently:

- **driver-app:** `hooks/useToast.ts::showToast(type, title, message?)` calls `Toast.show` with `visibilityTime: 3500` and `topOffset: 60`. It is the only entry point: nothing else in driver-app or `shared/` calls `react-native-toast-message`.
- **rider-app:** `components/Toast.tsx` plus the zustand store `store/toastStore.ts`.

There is no toast in `shared/`; only the length-cap helper `shared/utils/toastMessage.ts` is shared.

## 3. Fix / remediation

**Backend** (follows the migration 487 precedent):

- Migration `490_driver_unified_toast_flag.sql` adds `settings.driver_unified_toast_enabled BOOLEAN NOT NULL DEFAULT FALSE`. It uses `ADD COLUMN IF NOT EXISTS` and a 5 s `lock_timeout`, and the header carries the rollback.
- `SettingsUpdateRequest.driver_unified_toast_enabled` makes it writable through `PUT /api/admin/settings`.
- `GET /drivers/config` serves `driver_unified_toast_enabled`. Only an exact `True` turns it on. A settings read failure, or a row that predates migration 490, serves `False`.
- **Admin control:** API or SQL only. The 487 precedent has no dashboard UI. `/dashboard/settings` was deliberately not touched because it has a merge-blocking visual baseline.
- **Numbering:** built as 489 and renumbered to 490 before it was ever merged or applied, because `main` gained `489_monitoring_connection_summary.sql` first.

**driver-app:**

- `store/unifiedToastStore.ts` (new): a zustand store.
  - Every `show()` is a new toast (new id). There is no de-dupe.
  - `dismiss(id)` clears only the toast it belongs to.
  - It also holds the flag, via `setUnifiedToastEnabled` and `isUnifiedToastEnabled`. The flag is default off, and it lives here rather than in `useToast.ts` because many tests mock `useToast` wholesale.
- `components/UnifiedToast.tsx` (new): the banner, modelled on the rider toast.
  - Colours come from the live theme and follow dark mode; danger uses `colors.danger`.
  - Accessibility: `accessibilityRole="alert"`, a combined `accessibilityLabel` ("Title. Message"), an assertive live region for danger (polite otherwise), and one announcement per toast.
  - Swipe up to dismiss, and auto-dismiss after the toast's duration.
  - Fixed `top: 60`.
  - Text uses `maxFontSizeMultiplier={MAX_FONT_SCALE}`.
  - It renders `null` while the store is empty.
- `hooks/useToast.ts`: when the flag is on, `showToast` sends the same clamped text to the store with `duration: 3500`, mapping `error` to `danger`. When it is off, the old `Toast.show` block runs unchanged. The signature is unchanged.
- `hooks/useDriverDashboard.ts`: the existing `driverConfigQuery.data` effect calls `setUnifiedToastEnabled(data.driver_unified_toast_enabled === true)`, next to `setCarOfferToneEnabled`.
- `app/_layout.tsx`: mounts `<UnifiedToast />` right after `<Toast config={toastConfig} />`, inside the same providers.
- `docs/known-forks.md`: two new rows, one for the component pair and one for the store pair, so a fix to one side gets the pre-commit reminder.

## 4. Risk & impact on existing functionality

**Blast radius: single surface (driver-app), plus an additive backend column and field. Flag off is identical to today.**

| Touched | Other readers/writers (grepped) | Risk |
|---|---|---|
| `settings.driver_unified_toast_enabled` (new column) | `SettingsUpdateRequest`, `get_driver_config` only | Additive, default false. |
| `/drivers/config` response | `shared/hooks/queries/driverQueries.ts` (`useDriverConfig`, read by `useDriverDashboard`), `lib/androidAuto/carSession.ts` (`loadDriverConfig`, ignores the key), `e2e/fixtures.ts` | Additive key. Older app builds ignore it. |
| `hooks/useToast.ts::showToast` | 26 importing files, 155 call sites (listed below) | Flag off: the `Toast.show` block is byte-identical, and a test pins the exact call. Flag on: every toast renders through the new banner. |
| `hooks/useDriverDashboard.ts` config effect | runs whenever `/drivers/config` data changes | One extra synchronous setter call, with no I/O and no throw path. |
| `app/_layout.tsx` | root of every screen | New always-mounted host that returns `null` until its store has a toast. With the flag off the store is never written, so nothing renders. |
| Tests that `jest.mock('…/useToast')` | e.g. `useDriverDashboard.socketLifecycle`, `.chat`, screen tests | Unaffected: the flag setter lives in the store module, not in `useToast`. |

`showToast` importers:
- `components/DestinationModeBanner.tsx`, `hooks/useDriverDashboard.ts` and `lib/notifyError.ts`.
- In `app/`: `vehicle-info`, `legacy-consent-notice`, `reactivate-account`, `otp`, `login`, `appeal`, `crc-consent`, `profile-setup`, `report-safety`, `subscription/success` and `documents`.
- In `app/driver/`: `destination-mode`, `addresses`, `subscription`, `referral`, `tax-documents`, `settings`, `payout`, `lost-and-found-chat`, `stripe-onboarding` and `emergency-contacts`.
- In `app/driver/(tabs)/`: `index` and `profile`.

- **Ride state machine, money, insurance periods, background loops:** not touched.
- **Known fork:** the unified toast is a deliberate driver-only copy of the rider toast. It is now registered in `docs/known-forks.md`, and no parity test exists.

## 5. User-experience effect

- **Flag off (default):** none. The driver sees the react-native-toast-message banner exactly as today.
- **Flag on:** drivers see the rider-style banner. It slides down from a fixed 60 px from the top, ends left of the SOS column like today's toast, and uses the same theme colours and icons as today. Title and message clamps are the same, and it stays for 3.5 s. Differences from today:
  - The slide-in animation is a spring rather than react-native-toast-message's own animation.
  - A screen reader reads the banner as one combined label.
  - An identical toast fired twice re-animates and is announced twice. This is the same as today (no de-dupe, per decision 3).
- **Mid-session:**
  - The flag is read only from `/drivers/config`, which only the dashboard fetches.
  - After a cold start, screens shown before the dashboard mounts (login, OTP, profile setup, onboarding) keep the old toast.
  - Once the dashboard has applied the config, every screen uses the new banner for the rest of that JS session.
  - Flipping the flag reaches a running app on its next `/drivers/config` fetch. `useDriverConfig` has a 10-minute `staleTime` and a persisted cache, so this can take up to about 10 minutes. It is sooner on pull-to-refresh or when the dashboard mounts with stale data.
  - A toast already on screen when the flag flips finishes on its old banner.
- **Copy:** no text changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/490_driver_unified_toast_flag.sql` | New BOOLEAN column, default false; rollback in header | The flag |
| `backend/routes/admin/settings.py` | `driver_unified_toast_enabled: Optional[bool]` | Admin save can write it |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Column in snapshot; added to explicit-False round-trip test | Drift guard + admin write |
| `backend/routes/drivers/profile.py` | Field on `/drivers/config` (`is True`) | Deliver the flag |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | Parametrized flag test, absent-column default, failure default | Coverage |
| `driver-app/store/unifiedToastStore.ts` | New store + flag holder | State for the new banner |
| `driver-app/store/__tests__/unifiedToastStore.test.ts` | New | No-dedupe, guarded dismiss, default-off flag |
| `driver-app/components/UnifiedToast.tsx` | New host component; right edge uses `SOS_COLUMN_CLEARANCE` from `toastConfig.tsx` | The unified banner, keeping SOS clear |
| `driver-app/__tests__/components/UnifiedToast.test.tsx` | New | Role, label, live region, 60 px, SOS clearance, theme, font cap, no-dedupe, auto-dismiss |
| `driver-app/hooks/useToast.ts` | Flag-on branch before the unchanged `Toast.show` block | Route to the new banner |
| `driver-app/__tests__/hooks/useToast.test.ts` | Flag off/on cases | Old call exact; new routing |
| `driver-app/hooks/useDriverDashboard.ts` | `setUnifiedToastEnabled(...)` in the config effect | Apply the flag |
| `driver-app/hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts` | Configurable `useDriverConfig` mock; 2 wiring tests | Flag on/off/absent from config |
| `driver-app/app/_layout.tsx` | Mount `<UnifiedToast />` | Host |
| `docs/known-forks.md` | 2 rows (component, store) | Fork registry |

## 7. Before / after

```ts
// Before: driver-app/hooks/useToast.ts
export function showToast(type: ToastType, title: string, message?: string) {
  Toast.show({ type, text1: clamp(title), text2: clamp(message), visibilityTime: 3500, topOffset: 60 });
}
```

```ts
// After
export function showToast(type: ToastType, title: string, message?: string) {
  if (isUnifiedToastEnabled()) {            // migration 490, default off
    useUnifiedToastStore.getState().show({
      title: clamp(title), message: clamp(message),
      variant: UNIFIED_VARIANT[type] ?? 'info',   // error → danger
      duration: 3500,
    });
    return;
  }
  Toast.show({ type, text1: clamp(title), text2: clamp(message), visibilityTime: 3500, topOffset: 60 }); // unchanged
}
```

| Scenario | Before | After |
|---|---|---|
| Flag off, any `showToast` | react-native-toast-message banner, 60 px, 3.5 s | Same, identical call |
| Flag on, `showToast('error', …)` | n/a | Unified banner, `colors.danger`, assertive live region, 60 px, 3.5 s |
| Flag on, same toast twice | n/a | Two toasts (new id each), announced twice, same as today's replace-on-show |
| Cold start, flag on, login screen toast | n/a | Old banner (flag not yet read) |

## 8. Rollback plan

- **Behaviour, no release:** `UPDATE public.settings SET driver_unified_toast_enabled = false WHERE id = 'app_settings';`
  - The backend settings cache is 60 s.
  - A running app switches back on its next `/drivers/config` fetch: within about 10 minutes (the query's `staleTime`), or sooner on pull-to-refresh.
  - After a cold start the flag is off until the dashboard applies the config. The persisted query cache can then re-apply the last `true` until that refetch lands. This is the same propagation as migration 487's flag.
- **Schema:** `ALTER TABLE public.settings DROP COLUMN driver_unified_toast_enabled;`, after the readers are retired. It is also in the migration header.
- **Code:** flag off means the new code is never reached. A JS revert via OTA is available if needed. No live data is written by this change.

## 9. Verification performed

- **Backend:**
  - `pytest tests/test_admin_settings_write_allowlist_drift.py`: 10 passed. With the model field removed, 3 fail.
  - `pytest tests/test_drivers_shared_status_profile_coverage.py`: 99 passed. Against the pre-change endpoint code, 6 fail.
  - `ruff check` and `ruff format --check` are clean on all 4 Python files.
  - `spinr-migration-reviewer`: no blockers or should-fixes; verdict "safe to apply". The migration's header and `COMMENT ON COLUMN` were then corrected before merge: they had described the unified toast as a `shared/` component, which it is not.
- **driver-app `yarn tsc --noEmit`:** 0 errors.
- **driver-app affected jest suites:**
  - `__tests__/components/UnifiedToast.test.tsx`: 8 passed.
  - `store/__tests__/unifiedToastStore.test.ts`: 3 passed.
  - `__tests__/hooks/useToast.test.ts`: 9 passed.
  - `hooks/__tests__/useDriverDashboard.socketLifecycle.test.ts`: 24 passed.
  - `__tests__/fontScalingLock.test.ts`: passed.
- **Proven to fail without the change:**
  - Against the pre-change `useToast.ts`, 5 of the 6 new flag tests fail; the 6th is the flag-off exact-call test.
  - With the dashboard setter replaced by a no-op, both wiring tests fail.
  - Mutating the host (dropping the auto-dismiss timer or the 60 px offset) or the store (removing the `dismiss(id)` guard) fails 3 tests.
- **Full driver-app jest suite:** 174 suites, 2157 tests, all passed.
- **ESLint on the new and changed driver-app files:**
  - 0 new errors.
  - The 6 new warnings (hardcoded `#FFF` / numeric spacing) match `toastConfig.tsx`'s existing warnings, kept for visual parity with the rider banner.
  - The 7 errors reported in `useDriverDashboard.ts` and its socket test are on lines this change does not touch.
- **Pre-commit hook:** passed on every commit.

### What was NOT verified

- **No device or simulator check `[H]`.** driver-app has no visual or snapshot tooling. The banner's look, spring animation, swipe-to-dismiss, stacking above modals, and VoiceOver/TalkBack reading were reasoned about and unit-tested, not screenshotted or heard.
- **No production build.** Neither `expo export` nor an EAS build was run. Only `tsc --noEmit` and jest.
- **Database:** the migration was not applied to any database.
- **Full backend suite:** not run; only the two affected files were.
- **Reviewers:** the accessibility review of the driver-app half has not run yet; it runs before that PR.
- **Reduce Motion:** the new banner's slide/spring does not respect Reduce Motion. Neither does the rider banner or react-native-toast-message, so this is parity, not a regression. It is a follow-up for the motion work.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag UPDATE)
- [x] Blast radius is stated, not assumed (§4)
- [x] No silent behaviour change: flag off is byte-identical and pinned by a test
