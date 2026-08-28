# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (mkkreddy52@gmail.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/driver-notification-area-boost-usaor4` |
| Related issue or gap ID | Reported from a driver-login screenshot: consent checkbox under a "Welcome back" greeting |

## 1. Issue / gap identified

The driver login screen greeted everyone with "Welcome back 👋", including
first-time drivers who were simultaneously being asked to accept the Terms of
Service for the first time. The screenshot that surfaced this showed both at
once, which read as a bug and made the (correct) consent checkbox look
misplaced.

## 2. Root cause

The greeting was an unconditional literal, while the consent checkbox below it
renders only when `!hasAuthenticatedBefore` (the device has never completed a
sign-in). The two are mutually exclusive by construction — whenever the
checkbox is visible, the greeting is necessarily wrong — but nothing tied them
to the same signal, so the contradiction was reachable and, in fact, the
default state for every new driver.

## 3. Fix / remediation

The greeting now reads from the same `hasAuthenticatedBefore` flag the checkbox
uses: "Welcome back 👋" for a device that has signed in before, "Welcome to
Spinr 👋" otherwise. The checkbox itself is unchanged — see §4.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** One `<Text>` in `login.tsx`. `hasAuthenticatedBefore`
  was already state in this component, read once from AsyncStorage; this adds a
  second reader of it, no new state, no new storage access, no new render pass.
- **The consent checkbox was NOT removed or changed.** It is legally
  load-bearing: the backend rejects new-account creation with
  `consent_required` unless it was actively ticked, and `otp.tsx`'s inline
  consent card is the fallback when this screen skips it. Both paths are
  untouched. This change only fixes the copy that sits above it.
- No auth logic, OTP flow, network call, or navigation change. `canContinue`,
  the send-code handler, and the consent gating are all untouched.
- Same fail-open direction as the checkbox: if the AsyncStorage read fails or
  is slow, the flag stays `false` and a new driver sees the new-driver copy.
  The worst case is a returning driver briefly reading "Welcome to Spinr" on a
  device whose flag was lost — cosmetic, and strictly better than the reverse.

## 5. User-experience effect

- **Driver-facing, visible on the login screen.** A first-time driver is now
  greeted as new rather than as returning; a returning driver sees exactly what
  they see today.
- Not visible mid-session — this screen is pre-authentication, so nobody
  already online or mid-ride can encounter it.
- Copy change, reviewed against the customer-centric tone standard: specific
  ("Spinr", not a generic "Welcome"), non-technical, and it no longer makes a
  claim about the reader that may be false.
- Not feature-flagged: the un-flagged state is the wrong greeting, and the
  change is one line of copy behind a signal that already ships.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/login.tsx` | Greeting is now conditional on `hasAuthenticatedBefore`; comment explains the tie to the consent checkbox | A first-time driver was being told "Welcome back" while first accepting the Terms |

## 7. Before / after

```tsx
// Before — unconditional, contradicts the consent checkbox below it
<Text style={styles.greeting}>Welcome back 👋</Text>
```

```tsx
// After — same signal the checkbox uses
<Text style={styles.greeting}>
  {hasAuthenticatedBefore ? 'Welcome back 👋' : 'Welcome to Spinr 👋'}
</Text>
```

## 8. Rollback plan

Revert the one-line change (`git revert`). No migration, no flag, no persisted
state, no live data: the greeting is evaluated at render time from a flag that
already exists, so the next build carries whichever copy is current. Reverting
does not affect the consent checkbox, which this change never touched.

## 9. Verification performed

- Traced `hasAuthenticatedBefore` end to end: written by `otp.tsx` on every
  successful auth under `HAS_AUTHENTICATED_BEFORE_KEY`, read in `login.tsx`'s
  mount effect, and already the sole gate on the consent block (`login.tsx`,
  `{!hasAuthenticatedBefore && (`). Confirmed the greeting and the checkbox now
  key off one signal, so they can no longer disagree.
- Grepped `driver-app/__tests__` and `e2e/` for "Welcome back" — the only hit is
  an unrelated account-reactivation toast assertion, not this screen.
- Checked the `greeting` style (16px, single line) against the 3-character
  longer string; it is far from wrapping at any supported width.

## 10. What was NOT verified

- **No build, typecheck, lint, or test run, and no screenshot.**
  `driver-app/node_modules` is not installed and npm is blocked by this
  environment's network policy (403 through the agent proxy), so no
  `tsc --noEmit`, no `npm run build`, no Jest, and no way to launch the app.
  Per CLAUDE.md a passing dev server would not count anyway — but here nothing
  at all was run, so CI is the first check.
- The "far from wrapping" claim is reasoning from the font size and string
  length, not a render. driver-app has no visual-regression tooling (standing
  gap, `ACTION_ITEMS.md`), so no screenshot diff exists for this screen.
- Not exercised on a device with a real populated/empty AsyncStorage, so the
  returning-driver branch was read in code but not observed firing.
