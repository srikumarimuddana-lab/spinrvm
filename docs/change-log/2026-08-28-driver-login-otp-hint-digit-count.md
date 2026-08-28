# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (mkkreddy52@gmail.com) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/driver-notification-area-boost-usaor4` |
| Related issue or gap ID | Spotted while reviewing the driver login screen |

## 1. Issue / gap identified

The driver login screen's "Send Verification Code" button announced
`"Sends a 6-digit code to your phone number"` to screen readers. Spinr's login
OTP is 4 digits, so VoiceOver/TalkBack users were told to expect two digits
that never arrive.

## 2. Root cause

Hard-coded string in `driver-app/app/login.tsx`, written independently of the
backend's `OTP_LENGTH = 4` (`backend/dependencies/__init__.py:51`) and of
`otp.tsx`'s own `codeLength = 4`. Nothing ties the three together, and the
hint is invisible to sighted QA, so a wrong literal had no way of being
noticed. "6-digit" is the common industry default — most likely copied in from
habit rather than from this codebase.

## 3. Fix / remediation

Changed the literal to `"Sends a 4-digit code to your phone number"`.

Deliberately did **not** introduce a shared OTP-length constant across
backend + both apps. That is a real gap (three independent 4s), but it is a
wider refactor than this one-word copy fix and CLAUDE.md's surgical-changes
rule says not to bundle it. Noted here instead.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** One string literal, one JSX prop, one file. No
  other file references it; no test asserts on it (grepped `driver-app/__tests__`
  for "6-digit" — no hits). It is not a selector, key, or test ID.
- No auth logic, no OTP generation/validation, no network call, no state
  change. The actual OTP length is unchanged and still owned by the backend.
- No interaction with rate limiting, OTP lockout, or the consent flow on the
  same screen.
- Left alone deliberately: the two `6-digit` strings in `admin-dashboard`
  (`mfa-enroll-dialog.tsx:147`, `login/page.tsx:173`). Those are TOTP codes
  from an authenticator app, which genuinely are 6 digits — correct as-is.

## 5. User-experience effect

- **Driver-facing, screen-reader users only.** Anyone using VoiceOver/TalkBack
  on the driver login screen now hears the right digit count. Nothing changes
  visually for sighted users — the hint is never rendered on screen.
- Not visible mid-session: this screen is pre-authentication, so no driver
  already online or mid-ride can be affected by it.
- Accessibility-positive: the previous text set a wrong expectation on an
  auth step, which is where a mismatch is most disorienting.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/login.tsx` | `accessibilityHint` on the send-code button: "6-digit" → "4-digit" | Match the actual OTP length (`OTP_LENGTH = 4`) |

## 7. Before / after

```tsx
// Before
accessibilityHint="Sends a 6-digit code to your phone number"
```

```tsx
// After
accessibilityHint="Sends a 4-digit code to your phone number"
```

## 8. Rollback plan

Revert the one-line change (`git revert`). No migration, no flag, no persisted
state, no live data touched — the string is evaluated at render time, so the
next app build carries whichever literal is current.

## 9. Verification performed

- Confirmed the real OTP length at its source: `OTP_LENGTH = 4` in
  `backend/dependencies/__init__.py:51`, consumed by `generate_otp()`; and
  `codeLength = 4` in `driver-app/app/otp.tsx:35`, which drives both the input
  boxes and its own "Please enter the 4-digit code" toast. Both agree on 4.
- Swept all three surfaces for other digit-count copy; the only other hits are
  the admin TOTP strings, correctly 6.
- Confirmed no test asserts the old string.
- Re-read the edited line in place.

## 10. What was NOT verified

- **No build, typecheck, lint, or test run.** `driver-app/node_modules` is not
  installed in this environment and npm/PyPI are blocked by the network policy
  (403 through the agent proxy), so no `tsc --noEmit`, no `npm run build`, no
  Jest. The change is a string literal inside an existing prop, so the type
  risk is as close to nil as a diff gets — but per CLAUDE.md that is reasoning,
  not a passing build, and CI is the first real check.
- Not heard on a real device: the hint was not verified through actual
  VoiceOver or TalkBack playback.
- The underlying triplication of the "4" (backend constant, `otp.tsx`,
  and this hint) is untouched — a future OTP-length change still has to be
  made in three places by hand.
