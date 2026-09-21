# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-17 |
| Author | agent |
| Surface(s) | backend / rider-app / driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | |
| Related issue or gap ID | Live-testing: wrong OTP showed `ERR_OTP_INVALID` / `ERROR_OTP_INVALID` |

## 1. Issue / gap identified

Entering a wrong OTP toasted a machine code (`ERR_OTP_INVALID` / `ERROR_OTP_INVALID`) instead of a sentence the rider or driver can act on.

## 2. Root cause

`SpinrException.message` for `AUTH_OTP_INVALID` / `AUTH_OTP_EXPIRED` was a sentinel meant for i18n via `message_key`. `getApiErrorMessage` (used by `otp.tsx` in both apps) prefers that `message` over the caller fallback. Email-verify already translated `messageKey`; phone OTP did not.

## 3. Fix / remediation

Backend `message` is now English ("That code didn't match. Please try again.") so live apps pick it up on the next API deploy without a store update. `getApiErrorMessage` also skips leftover `ERR_*` / `ERROR_*` sentinels. Phone OTP screens resolve `messageKey` through `tKey`, matching verify-email.

Chosen over "OTP screens only": a backend copy change is what live testers see today, and the shared toast helper stops any other caller from leaking the same sentinel. Client `tKey` still wins for translated apps.

## 4. Risk & impact on existing functionality

- Blast radius: cross-surface auth OTP (phone verify, company email OTP, rider email verify). Same `message_key` (`errors.auth.otp_invalid` / `otp_expired`); numeric `error_code` unchanged.
- Callers of `getApiErrorMessage` that previously displayed `ERR_*` now get their fallback. Grep consumers: rider/driver OTP, booking, wallet, promotions, destination-mode, lost-and-found, profile-setup, rideStore — those paths do not send OTP sentinels.
- `verify-email.tsx` already used `tKey(messageKey)` and still prefers that over `message`.
- No ride state machine, money, or background loops.

## 5. User-experience effect

- Rider / driver / company-admin: a wrong or expired OTP shows plain English (and translated copy when `messageKey` is present) instead of a code.
- Visible on the next OTP attempt; not mid-ride.
- Copy matches the existing i18n keys (`That code didn't match. Check the SMS and try again.` on the phone screens).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Friendly `message` on OTP invalid/expired | Live API clients dump `message`/`detail` |
| `backend/routes/users.py` | Same for rider email verify | Same sentinel on that path |
| `shared/api/client.ts` | Skip `ERR_*` / `ERROR_*` in `getApiErrorMessage` | Defense if a sentinel still arrives |
| `rider-app/app/otp.tsx` | `tKey(messageKey)` like verify-email | Translated toast, not the sentinel |
| `driver-app/app/otp.tsx` | Same | Forked sibling of rider OTP |
| `rider-app/__tests__/getApiErrorMessage.test.ts` | Sentinel cases | Pin the shared helper |
| `rider-app/__tests__/otpScreen.test.tsx` | Friendly toast + SpinrApiError case | Pin the screen |
| `driver-app/__tests__/app/otpScreen.test.tsx` | Same | Pin the sibling |

## 7. Before / after

```
# Before
showToast('Verification Failed', getApiErrorMessage(err, 'Invalid code. Please try again.'), 'danger');
# getApiErrorMessage returned "ERR_OTP_INVALID"
```

```
# After
showToast('Verification Failed', resolveOtpErrorCopy(err), 'danger');
# tKey('errors.auth.otp_invalid') → "That code didn't match. Check the SMS and try again."
```

## 8. Rollback plan

Redeploy the previous backend build to restore sentinel `message` strings. No data writes. Feature-flag not used: copy-only, no new validation. A `git revert` of the client commit is enough if only the JS bundle is live.

## 9. Verification performed

- [x] Automated tests: rider-app `getApiErrorMessage.test.ts` + `otpScreen.test.tsx` (68 passed); driver-app `otpScreen.test.tsx` (23 passed)
- [ ] Manual repro on a device (wrong OTP after deploy)
- [x] Blast-radius grep: `ERR_OTP_INVALID`, `getApiErrorMessage`, `otp.tsx`
- [x] Auth copy only — no state machine / money / RLS
- [x] Not feature-flagged: copy-only fallback; existing i18n keys
- [ ] `npm run build` not run (no admin-dashboard UI change)

## 10. What was NOT verified

- Not tested against live Fly/Supabase; unit tests mock the API client.
- rider-app / driver-app have no visual-regression tooling; toast copy was asserted in unit tests, not screenshotted.
- Company portal login dumps `detail` from the API; after backend deploy that string is English, but this PR did not add a portal-specific test.
- No production mobile build (`eas build`) was run — backend message change is what existing binaries will show.
