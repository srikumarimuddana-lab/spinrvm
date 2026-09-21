# Change Impact & Risk Log — Distinct Failure-Toast Copy for Sign-Out-All vs. Single-Device Sign-Out

**Date:** 2026-09-21
**Author:** Claude Code (session), on behalf of ittalenthire.ca@gmail.com
**Surfaces:** driver-app
**Domain:** auth (sign-out-of-all-devices / lost-stolen-phone recovery flow)

## Issue/gap identified
`driver-app/app/driver/(tabs)/profile.tsx`'s `handleLogout` (single-device sign-out) and `handleLogoutAll` (sign-out-of-all-devices — the lost/stolen-phone recovery flow) showed the exact same generic error toast on failure: title "Sign Out Failed", body "Your session could not be closed. Please try again."

## Root cause
Flagged as a WARNING (not a blocker) by `spinr-notification-ux-reviewer` during the 2026-09-20 `/full-audit` review of PR #5526 (which fixed the stale test for `handleLogoutAll`'s failure path, making that path testable — but the copy itself predates that PR, shipped in commit `d1c106e` on 2026-09-15, and was never differentiated). A driver taps "Sign out of all devices" specifically because their phone is lost or stolen and they need every other session killed. If that call fails, the identical-to-routine "try again" copy gives no indication that the higher-stakes outcome (other sessions may still be live) didn't happen — it reads the same as a routine single-device sign-out hiccup.

## Fix/remediation
`handleLogoutAll`'s failure toast now reads: "Some devices may still be signed in. Please try again or contact support." `handleLogout`'s copy is unchanged — this is scoped to the one higher-stakes flow, not a general toast-copy pass.

## Risk & impact on existing functionality
- **Blast radius:** one string literal in one file (`driver-app/app/driver/(tabs)/profile.tsx`), plus its matching test assertion. Grepped `rider-app`, `driver-app`, `shared` for `Sign Out Failed` / `logoutAll` / `Sign out of all devices` — confirmed this flow exists only in driver-app; no other file references this copy.
- No behavior change — same stay-on-screen-plus-toast UX (chosen via this session's own earlier `AskUserQuestion`) is unchanged; only the toast's message text differs from the single-device case.
- No interaction with the `logoutAll()` re-throw fix shipped earlier today (PR #5598) — that fix made this catch branch reachable at all; this change only edits what it displays once reached.

## User experience effect
Driver-facing. A driver whose "sign out of all devices" fails now sees copy that names the actual risk (other devices may still be signed in) instead of generic "try again" — same visible failure mode (stays on screen, sees a toast), more informative message.

## Files modified
| File | What changed | Why |
|---|---|---|
| `driver-app/app/driver/(tabs)/profile.tsx` | `handleLogoutAll`'s failure-path toast body changed from "Your session could not be closed. Please try again." to "Some devices may still be signed in. Please try again or contact support." | Differentiate the higher-stakes sign-out-all failure from a routine single-device sign-out failure |
| `driver-app/__tests__/app/driverProfileScreen.test.tsx` | Updated the matching test assertion to the new copy | Keep the regression test in sync with the real behavior |

## Before/after snippet
Before:
```tsx
onPress: async () => {
  try {
    await logoutAll();
    router.replace('/login' as any);
  } catch {
    showToast('error', 'Sign Out Failed', 'Your session could not be closed. Please try again.');
  }
},
```

After:
```tsx
onPress: async () => {
  try {
    await logoutAll();
    router.replace('/login' as any);
  } catch {
    showToast(
      'error',
      'Sign Out Failed',
      'Some devices may still be signed in. Please try again or contact support.'
    );
  }
},
```

## Rollback plan
`git revert` — a single string literal change plus its test assertion, no data/schema/runtime backend state affected.

## Verification performed
- `npx jest __tests__/app/driverProfileScreen.test.tsx` in `driver-app/` — 24/24 passing, including the updated assertion.
- `npx tsc --noEmit` clean in `driver-app/`.
- Grepped all three surfaces to confirm this toast copy and the sign-out-all flow exist only in driver-app.

## What was NOT verified
- Not run against a real device/simulator — no visual/layout change, only string content; reasoned about via the existing test suite rather than screenshotted (rider-app/driver-app have no visual-regression tooling, per CLAUDE.md).
