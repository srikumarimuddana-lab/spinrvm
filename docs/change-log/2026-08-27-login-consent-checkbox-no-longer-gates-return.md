# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session) |
| Surface(s) | backend (read-only, no change) / rider-app / driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | (opened alongside this entry) |
| Related issue or gap ID | `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §6a |

## 1. Issue / gap identified

`login.tsx`'s "I agree to Spinr's Terms of Service and Privacy Policy" checkbox blocked "Send
Verification Code" for **every** visit to that screen — a brand-new signup and a returning
rider/driver whose session simply expired (30-day refresh token) or who logged out and back in
alike. Raised directly by the user, then confirmed by reading the code.

## 2. Root cause

`backend/routes/auth.py`'s `verify_otp` only ever reads `consent_accepted` on the **new-account
creation** branch (`else:` when `existing_user` is `None`) — the returning-user branch never
checks it at all. But `login.tsx` cannot know in advance whether a given phone number belongs to
a new or returning account (that's only knowable after OTP verification), so it took the simplest
path and gated the button unconditionally for everyone, every time.

## 3. Fix / remediation

- `login.tsx` (both apps): `canContinue` no longer includes `consentAccepted` — only phone
  validity gates "Send Verification Code". `handleSendCode`'s internal guard updated to match
  (previously duplicated the same check). The checkbox itself is unchanged — still visible,
  still toggleable, still carried to `/otp` as a route param either way.
- `otp.tsx` (both apps): `consentAccepted` is now local mutable state (seeded from the route
  param) instead of a route param read fresh each render. `handleVerify`'s catch block detects
  the backend's `errors.auth.consent_required` `messageKey` (already wired server-side,
  `backend/utils/error_keys.py` — no backend change needed) and, only then, reveals an inline
  consent card (same checkbox/links pattern as `login.tsx`) instead of the generic "Verification
  Failed" toast.
- **OTP single-use constraint handled explicitly**: `routes/auth.py` deletes the OTP record
  immediately on successful hash match (line ~996), *before* the consent check runs — so a
  `consent_required` rejection means the code just entered is already consumed. Retrying it would
  fail with `ERR_OTP_INVALID`. The inline consent card's "Agree & Send New Code" button
  (`handleAgreeAndResend`) issues a fresh `/auth/send-otp` call once checked, clears the old code,
  and re-arms the resend countdown, rather than inviting the user to retry a dead code.
- No backend changes — `message_key=ErrorKeys.AUTH_CONSENT_REQUIRED` was already set on this
  exact raise (`backend/routes/auth.py:1170`), and `shared/api/client.ts`'s error-extraction
  layer already surfaces it as `err.messageKey` on the thrown error (same pattern already used by
  `rider-app/app/verify-email.tsx`) — this fix is 100% client-side.

## 4. Risk & impact on existing functionality

- **Blast radius**: `login.tsx` + `otp.tsx` in both `rider-app` and `driver-app` (4 files), plus
  their two consent-checkbox test files and two OTP-screen test files (4 more, test-only). No
  backend file touched — confirmed the exact server-side error contract (`message_key`) already
  existed and needed no change.
- **Who else reads `consent_accepted`**: grepped `VerifyOTPRequest`/`verify_otp` callers — only
  `rider-app/app/otp.tsx` and `driver-app/app/otp.tsx` send it in production; both updated
  identically in this change. Admin-dashboard's driver-registration flow calls a different,
  already-broken (pre-existing, unrelated) `/api/auth/verify-otp` route that never reaches this
  backend function at all — confirmed unaffected, not touched.
- **Existing-user login branch**: entirely unchanged — it never read `consent_accepted` before
  this fix and still doesn't. This fix only changes when the *client* sends the field, not what
  the backend does with it.
- **New-account creation branch**: unchanged behavior for a user who **did** check the box on
  `login.tsx` — same `consent_accepted: true` reaches the backend the same way. The only new path
  is for a new user who did **not** check it: previously impossible to reach (button was
  disabled); now reachable, and handled by the new inline recovery card rather than a raw 400
  bubbling up as a confusing "invalid code" toast.
- **Rate limiting / OTP lockout**: `handleAgreeAndResend` calls the same `/auth/send-otp` endpoint
  as the existing manual resend button, which already has its own backend rate limiting
  (429/lockout handling already covers this call site) — no new abuse surface, since a user still
  needs a real phone number to receive the resent code.

## 5. User-experience effect

- **Riders & drivers (returning)**: no longer forced to re-check a box with zero effect for them
  on every re-login. This is the entire point of the fix — a visible reduction in friction on a
  screen every user hits on every fresh sign-in.
- **Riders & drivers (new signup)**: identical experience if they check the box on `login.tsx` (the
  common case). If they don't, they now see one extra, clearly-worded step ("Almost there... we'll
  send a new code once you do") instead of either being silently blocked (old behavior) or hitting
  a confusing "invalid code" failure (what would happen without this fix, if the login.tsx gate
  were simply removed without the inline recovery).
- Not mid-session — this is a pre-authentication screen; no active session is affected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/login.tsx` | `canContinue`/`handleSendCode` no longer require `consentAccepted` | Stop gating on a field the backend never reads for returning users |
| `rider-app/app/otp.tsx` | Local `consentAccepted` state, `needsConsent` inline recovery card + `handleAgreeAndResend` | Handle the one case the backend does enforce it — a genuine new signup |
| `rider-app/__tests__/loginConsentCheckbox.test.tsx` | Updated 6 tests for the new gating behavior | Pin the corrected contract |
| `rider-app/__tests__/otpScreen.test.tsx` | +5 tests for the inline consent-recovery flow | New behavior needs coverage |
| `driver-app/app/login.tsx` | Same as rider-app | Same reason |
| `driver-app/app/otp.tsx` | Same as rider-app | Same reason |
| `driver-app/__tests__/screens/loginConsentCheckbox.test.tsx` | Updated 4 tests + 1 new | Pin the corrected contract |
| `driver-app/__tests__/app/otpScreen.test.tsx` | +5 tests for the inline consent-recovery flow | New behavior needs coverage |

## 7. Before / after

```tsx
// Before (both apps' login.tsx)
const canContinue = isValid && consentAccepted;
```

```tsx
// After
const canContinue = isValid;
```

```tsx
// Before (both apps' otp.tsx handleVerify catch)
} catch (err: any) {
  triggerShake();
  setCode('');
  showToast(/* generic "Verification Failed" */);
}
```

```tsx
// After
} catch (err: any) {
  if (err?.messageKey === 'errors.auth.consent_required') {
    setNeedsConsent(true);
    setCode('');
    showToast(/* "One More Step" */);
    return;
  }
  triggerShake();
  setCode('');
  showToast(/* generic "Verification Failed", unchanged for every other error */);
}
```

## 8. Rollback plan

`git-revert-safe` — pure client-side UI/logic change, no schema, no data, no backend contract
change (the `message_key` this relies on already existed independent of this fix). Reverting
restores the old unconditional gate; no cleanup needed on either side.

## 9. Verification performed

- [x] Automated tests: rider-app full suite **1598/1598 passed** (123 suites); driver-app full
      suite **1303/1303 passed** (116 suites) — both a real, full run, not just the touched files.
- [x] `tsc --noEmit` clean on both apps.
- [x] `eslint` on all 8 touched files: 0 errors on both apps (3 pre-existing warnings on
      untouched import lines in both `otpScreen.test.tsx` files, verified pre-existing).
- [x] Blast-radius grep performed — confirmed the only two production callers of
      `consent_accepted` are the two `otp.tsx` files, both updated; confirmed the backend's
      `errors.auth.consent_required` `message_key` was already wired server-side (no backend
      change needed), traced through `shared/api/client.ts`'s existing error-extraction layer.
- [x] Reviewed the OTP single-use constraint directly in `backend/routes/auth.py` before
      designing the fix — confirmed the code is deleted before the consent check runs, which is
      why "Agree & Send New Code" issues a fresh code rather than retrying the same one.
- [x] Feature-flagged: not applicable — this is a bug fix to an existing, always-on screen, not
      new user-visible functionality; per CLAUDE.md's flagging guidance this is a correctness fix
      to already-shipped UX, not new UX requiring dark-ship rollout.

## What was NOT verified

- Not tested on a real device/simulator — Jest component tests only, this session has no
  device/simulator access.
- The inline consent card's visual layout (spacing, dark mode) was reasoned through the existing
  `colors.*` theme tokens and mirrored from `login.tsx`'s established pattern, but not
  screenshotted — no visual-regression tooling exists for either mobile surface (per CLAUDE.md's
  standing gap).
- Copy on the inline consent card ("Almost there...") is newly authored, not independently
  reviewed against a customer-tone standard.
