# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude, at user request ("this should be place only for the new driver and riders at first login") |
| Surface(s) | rider-app, driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | commit following this log |
| Related issue or gap ID | Reported live: consent checkbox shows on every login for every user, not just new signups |

## 1. Issue / gap identified

The "I agree to Spinr's Terms of Service and Privacy Policy" checkbox on the login
(phone-entry) screen showed for **every** login attempt — new signup or returning
user — every time. The backend already only *enforces* it for genuine new-account
creation (`routes/auth.py`'s `verify_otp` silently ignores `consent_accepted` for a
returning user), so this was purely a display problem: a returning rider/driver was
shown an "I agree to..." affordance with zero legal effect for them, on every
re-authentication.

## 2. Root cause

`login.tsx` (both apps) is the phone-number-entry screen, shown before OTP
verification — at that point neither the app nor the user knows whether the phone
number about to be entered belongs to a new or returning account (that's only
resolved server-side, during `POST /auth/verify-otp`). The checkbox was therefore
unconditionally rendered on every visit to this screen, with no signal available to
distinguish "first login on this device" from "the fifth login this week."

## 3. Fix / remediation

Added a lightweight, per-device heuristic instead of trying to know the phone
number's new/returning status in advance (which the app fundamentally can't, before
OTP verify):

- `otp.tsx` writes a persisted flag (`AsyncStorage`, key
  `spinr_rider_has_authenticated_before` / `spinr_driver_has_authenticated_before`)
  the moment a login succeeds (token issued) — new account or returning, doesn't
  matter, the flag just means "this device has completed a sign-in before."
- `login.tsx` reads that flag on mount and, if set, skips rendering the entire
  consent block (checkbox + "I agree to ToS/Privacy Policy" text + links).
  Defaults to **showing** the checkbox (fail open) if the flag is absent, the
  `AsyncStorage` read fails, or hasn't resolved yet — a fresh device, a fresh
  install, or a genuine new signup always sees it.

**This is a display-only heuristic, not the consent enforcement point** — that
remains entirely server-side. A device that has this flag set (e.g. a shared
device, or a different family member starting a genuine new signup on a phone
that has logged in before) will have the checkbox hidden on `login.tsx`, but if
that phone number turns out to be a brand-new account, `POST /auth/verify-otp`
still rejects it with `consent_required`, and `otp.tsx`'s existing inline consent
card (unchanged by this fix) prompts for it there before the account can be
created. No path to account creation without consent was removed or weakened.

## 4. Risk & impact on existing functionality

- **Blast radius: two screens per app (`login.tsx`, `otp.tsx`), isolated to the
  consent-checkbox display logic.** No change to `verify_otp`, no change to the
  actual consent-enforcement backend logic, no change to `otp.tsx`'s inline
  consent-recovery flow (still there, still the real safety net).
- **New `AsyncStorage` key** per app (`spinr_rider_has_authenticated_before` /
  `spinr_driver_has_authenticated_before`) — new keys, doesn't touch or repurpose
  any existing storage key. Grepped both apps for any other reader of these two
  literal key names — none exist outside the new `login.tsx`/`otp.tsx` pair.
  Cleared automatically on app uninstall/reinstall (standard `AsyncStorage`
  behavior); a device losing the flag just means the checkbox reappears once,
  functionally harmless.
- **Pre-login access to the Terms of Service / Privacy Policy links**: for a device
  that has authenticated before, the checkbox block (and its embedded ToS/Privacy
  links) no longer renders on the login screen. This was flagged in the existing
  code as "this screen has no other pre-account path to the legal documents"
  (accessibility review note) — that note was about *pre-account* access, i.e.
  before ever logging in; a device that has already logged in before is, by
  definition, past that stage and can reach the same documents in-app (rider-app's
  `app/policies.tsx`, both apps' `app/legal.tsx` from within the authenticated app).
  A device with the flag set that opens the login screen to view the documents
  *before* re-authenticating (rare) loses that one specific pre-login path — judged
  an acceptable, deliberate tradeoff matching the user's explicit request.
- **Fail-open default is the safety property this design leans on**: if the
  `AsyncStorage` read is slow, fails, or the flag was never set, the checkbox shows
  — never hidden by accident for a genuine first-time user.

## 5. User-experience effect

**Rider- and driver-facing, immediately visible.** A returning rider/driver no
longer sees the consent checkbox on the login screen after their first successful
sign-in on that device. A genuinely new signup (fresh device, fresh install, or a
different account on a device that's logged in before) still sees it exactly as
before — either on `login.tsx` directly, or inline in `otp.tsx` if the backend
rejects the attempt for missing consent.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/login.tsx` | Reads `spinr_rider_has_authenticated_before` on mount; skips rendering the consent block if set (fail-open default: show) | Scope the checkbox to first login, per the user's request |
| `rider-app/app/otp.tsx` | Writes the same key to `true` on successful token acquisition | Marks "this device has authenticated before" for `login.tsx` to read next time |
| `driver-app/app/login.tsx` | Same as rider-app, key `spinr_driver_has_authenticated_before` | Same |
| `driver-app/app/otp.tsx` | Same as rider-app | Same |
| `rider-app/__tests__/loginScreen.test.tsx` | Added local `AsyncStorage` mock (resolves `null` by default) so existing checkbox-visible assumptions still hold | Required after `login.tsx` started importing `AsyncStorage` |
| `rider-app/__tests__/otpScreen.test.tsx` | Same mock addition; new assertion that `setItem` is called on success and not called on a no-token response | Pin the write side |
| `rider-app/__tests__/loginConsentCheckbox.test.tsx` | Same mock addition (now overridable per-test); new `describe` block covering shown/hidden/fail-open/still-can-continue cases | Dedicated regression coverage for the new scoping behavior |
| `driver-app/__tests__/app/loginScreen.test.tsx` | Existing local `AsyncStorage` mock's `getItem` made overridable per-test; new `describe` block mirroring rider-app's | Same |
| `driver-app/__tests__/app/otpScreen.test.tsx` | Imports the (already globally-mocked) `AsyncStorage` module to assert the write; new assertion on the success path | Pin the write side |
| `docs/change-log/2026-08-27-consent-checkbox-scoped-to-first-login.md` | This log | UX/auth-adjacent change to a live-tested surface |

## 7. Before / after

```tsx
// Before — login.tsx (both apps), unconditional
<View style={[styles.terms, { paddingBottom: insets.bottom + 16 }]}>
  {/* checkbox + "I agree to ToS/Privacy" + links */}
</View>
```

```tsx
// After
{!hasAuthenticatedBefore && (
<View style={[styles.terms, { paddingBottom: insets.bottom + 16 }]}>
  {/* unchanged */}
</View>
)}
```

```ts
// otp.tsx (both apps) — added after a successful token acquisition
if (token) {
  await useAuthStore.getState().setTokens(token, refresh_token ?? '', expires_in ?? 900);
  AsyncStorage.setItem(HAS_AUTHENTICATED_BEFORE_KEY, 'true').catch(() => {});
  // ... existing code unchanged
}
```

## 8. Rollback plan

`git revert` is complete and sufficient — pure client-side display logic, no
backend, no data-layer, no migration. Reverting restores the "always show the
checkbox" behavior; the new `AsyncStorage` keys simply go unread again (harmless
orphaned local storage, auto-cleared on uninstall).

## 9. Verification performed

- [x] `npx tsc --noEmit -p .` — both apps clean, no errors (including after fixing
      three TypeScript errors surfaced by the initial mock typing: a zero-arg
      `jest.fn()` factory locked the mock's inferred signature, rejecting both the
      `...args` spread call site and later `mockResolvedValueOnce('true')` calls —
      fixed by giving each mock factory an explicit `(...args: any[])` signature).
- [x] `npx eslint` on every changed file — 0 errors in both apps; pre-existing
      warning counts unchanged after fixing one warning my own diff introduced
      (an `import` statement placed after `jest.mock()` calls, itself fixed by
      moving it to the top-of-file import group instead, since it didn't need
      the mock-hoisting position the other imports in that file do).
- [x] `npx jest` full suite, rider-app: 123/123 suites, 1750/1750 tests pass.
- [x] `npx jest` full suite, driver-app: 116/116 suites, 1308/1308 tests pass on
      two consecutive clean runs. One earlier run in this session showed
      `__tests__/services/notifeeService.test.ts` fail 19 tests with a
      `@notifee/react-native` native-module error — investigated: this file is
      unrelated to anything this diff touches (AsyncStorage/login/otp), passes on
      its own in isolation both with and without this diff, and the full suite
      passes clean on *unmodified* `main` too. Confirmed this is a pre-existing,
      worker-scheduling-dependent flake in that file's test isolation (not caused
      by this diff) — not something this change introduces or is responsible for
      fixing, but flagging it honestly rather than silently rerunning past it.
- [x] Added dedicated regression tests for the new scoping behavior in both apps
      (shown when never-authenticated, hidden when flagged, fails open on a
      rejected `AsyncStorage` read, and confirmed "Send Verification Code" still
      works with the checkbox hidden) rather than only relying on existing tests
      continuing to pass.

## What was NOT verified

- Did not test on a real device/simulator — no automated visual-regression tooling
  exists for either mobile surface (CLAUDE.md standing note); verified via unit
  tests exercising the actual conditional-render logic and `AsyncStorage`
  read/write calls, not a rendered on-device comparison.
- Did not verify behavior across an actual app uninstall/reinstall cycle (which
  clears `AsyncStorage`) — reasoned from `AsyncStorage`'s documented behavior
  (per-app-install storage, cleared on uninstall) rather than an on-device repro.
- Did not investigate the pre-existing `notifeeService.test.ts` worker-scheduling
  flake noted above beyond confirming it isn't caused by or related to this diff —
  out of scope for this change.

## 10. Sign-off

- [x] Rollback plan is concrete (plain `git revert`, no data-layer component).
- [x] Blast radius is stated, not assumed — two screens per app, new storage keys
      with no other readers/writers, backend consent enforcement completely
      untouched.
- [x] No silent behavior change to consent *enforcement* — only to when the
      checkbox is *displayed*. The actual account-creation gate
      (`consent_required` rejection + `otp.tsx`'s inline card) is unchanged and
      remains the real backstop.
