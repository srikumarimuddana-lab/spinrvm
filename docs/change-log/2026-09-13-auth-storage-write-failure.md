# Fail visibly when session persistence fails

Date: 2026-09-13. Domain: auth. Surfaces: shared, rider-app, driver-app. Finding: F2.

| Field | Detail |
|---|---|
| Issue | Login could succeed in memory after SecureStore rejected the refresh-token write, leaving no usable credential on relaunch. |
| Root cause | storage.setItem swallowed exceptions; setTokens published the access token before awaiting persistence. Reproduced on mocked iOS/Android storage. Incident causality remains unproven. |
| Fix | Report a fixed non-sensitive error and reject failed writes. Publish access/CSRF/store state only after the existing token persistence sequence completes. |
| Blast radius / risks | Both OTP and account-reactivation screens await setTokens and already catch errors. Foreground refresh also awaits it and treats non-401 failures as recoverable. Shared upload/client consumers and driver headless foreground-token readers retain the same keys. Logout also uses storage.setItem for its session-ended marker: failures now reject visibly rather than pretending persistence succeeded. See the read-recovery entry for other authStore consumers. |
| UX effect | Sign-in surfaces the existing error flow if credentials cannot be saved; normal sign-in is unchanged. No new screen/copy other than the actionable storage error. Mid-session refresh does not falsely publish success after a failed write. |
| Rollback | Republish the prior mobile JS bundle. No DB writes/migration and no historical data repair. No existing runtime flag controls this adapter. |
| Verification | New tests failed before implementation: two rejected-write tests unexpectedly resolved, and access was published before a deferred write finished. After implementation, targeted initialize/refresh/index tests pass; exact final totals/build results are in the audit. |
| Not verified | Physical Keychain durability, native release archive, process death between server rotation and local persistence, and real Sentry delivery. Multi-key SecureStore writes are not a transaction. If the server rotates a token and the new refresh write itself fails, the new credential cannot be recovered after process death by this patch. Mobile UI effects reasoned about, not screenshotted; neither app has active visual regression tooling. |

| File | What changed | Why |
|---|---|---|
| shared/store/authStore.ts | Reject storage writes and defer token publication | Prevent false successful sign-in |
| driver-app/__tests__/store/authStore.initialize.test.ts | Failure/durability ordering tests | Exercise actual shared setTokens |
| This document | Impact and limitations | Release review |

Before: `setInMemoryToken(token); await storage.setItem('refresh_token', refreshToken);` with a swallowed write exception.

After: `await storage.setItem('refresh_token', refreshToken); /* remaining writes */ setInMemoryToken(token);` with write rejection reaching the caller.
