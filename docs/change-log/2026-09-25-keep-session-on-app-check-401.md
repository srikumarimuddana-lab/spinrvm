# Change Impact & Risk Log: keep sessions when App Check rejects refresh (#5777)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | PR author; follow-up commits by Claude Code |
| Surface(s) | rider-app, driver-app, shared |
| Domain (Sentry tag) | auth |
| PR / commit link | #5777 (`6e956c4`, `26228e9`, `a934b45`, `07956e7`); server-side companion #5780 |
| Related issue or gap ID | Riders/drivers logged out after the app sat in the background |

## 1. Issue / gap identified

A `401 {"detail":"App Check token required" | "Invalid App Check token"}` from `/auth/refresh` was treated as a revoked login. The apps deleted the 30-day refresh token and sent the user back to the phone-number screen.

## 2. Root cause

`FirebaseAppCheckMiddleware` rejects before the refresh token is examined. `shared/store/authStore.ts` `refreshTokens()` and `driver-app/utils/backgroundAuth.ts` treated every refresh 401 as a credential rejection. Rider-app `app/index.tsx` also routed a recoverable session (`sessionRecoverable`) straight to `/login`.

## 3. Fix / remediation

- `shared/auth/appCheckRejection.ts` recognises the two App Check 401 bodies.
- `authStore.refreshTokens()` keeps the session and returns `false` on an App Check 401. The driver background task backs off 30 s instead of permanently blacklisting the token.
- Rider `app/index.tsx`:
  - retries `initialize()` every 5 s while `sessionRecoverable`, instead of routing to `/login`;
  - shows "Sign in instead" after 3 attempts, ported from driver-app `app/index.tsx`;
  - catches rejected attempts.
- `authStore.initialize()` clears `sessionRecoverable` when the cached-profile fallback succeeds (Codex finding). Before this, both apps stayed on "Reconnecting" and rotated the refresh token every retry.

## 4. Risk & impact on existing functionality

- `refreshTokens()` is called by `shared/api/client.ts` (401 interceptor, `ensureFreshToken`) and by `initialize()` in both apps. Only the App Check 401 branch changes; the dead-token path is unchanged.
- `sessionRecoverable` is read only by `rider-app/app/index.tsx` and `driver-app/app/index.tsx`.
- `isAppCheckRejection` matches on the response `detail` text. If the backend strings in `backend/core/middleware.py` change, App Check 401s fall back to being treated as a logout (the old behaviour).
- An App Check 401 now leaves the in-memory token in place and returns `false`. The client's 401 handler then fails that request without signing out.
- Blast radius: multi-surface (both apps via `shared/`).

## 5. User-experience effect

- Riders see a "Reconnecting" spinner instead of the login screen when a refresh fails transiently, and "Sign in instead" after about 15 s.
- Drivers keep their session through App Check failures.
- Visible mid-session: only as fewer logouts. The new copy is "Still having trouble connecting." / "Sign in instead", matching the driver app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/auth/appCheckRejection.ts` | New helper | Tell an App Check 401 from a dead login |
| `shared/store/authStore.ts` | App Check branch in `refreshTokens`; clear `sessionRecoverable` on cached fallback | Keep sessions; stop the retry loop |
| `driver-app/utils/backgroundAuth.ts` | App Check 401 backs off instead of blacklisting | Keep background location auth alive |
| `rider-app/app/index.tsx` | Recovery retries, escape button, catch | Don't bounce to `/login`; never trap the rider |
| Tests: `driver-app/__tests__/store/authStore.{refreshRace,initialize}.test.ts`, `driver-app/__tests__/utils/backgroundAuth.test.ts`, `rider-app/__tests__/indexScreen.test.tsx` | New cases | Pin each behaviour |

## 7. Before / after

```ts
// Before (authStore.refreshTokens): any 401 after the rotation-race check
await clearLocalSessionUnlocked();   // 30-day login deleted
// After
if (isAppCheckRejection(await rejectionBody(e))) return false;   // session kept
```

## 8. Rollback plan

This is a client change, so there's no runtime flag. Rollback means reverting and shipping an OTA/EAS update, which is acceptable because no server or live data is written. The server-side companion #5780 makes this path unreachable once deployed, which is also the fastest mitigation for installed versions.

## 9. Verification performed

- The follow-up commits could not run jest or `tsc` in the authoring container (npm registry blocked). They were only syntax-checked with TypeScript `transpileModule`. CI runs the real suites.
- Codex review findings: two fixed (`a934b45`, `07956e7`). The forced App Check refresh was declined with reasons on the thread.

## 10. What was NOT verified

- No production build, and no device testing on iOS or Android.
- rider-app has no visual regression tooling. The new button was reasoned about, not screenshotted.
- The Codex finding on forced App Check refresh is not addressed in the client; see its thread.
