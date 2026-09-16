# Background location token renewal

Date: 2026-09-16. Author: Codex. Domains: auth, drivers, rides.

Issue: recorded driver routes survive backgrounding, but live uploads stop after
the foreground-provided access token expires. Root cause: headless renewal was
disabled to avoid racing single-use refresh credentials.

Design: serialize session operations across native runtimes using a dedicated
SQLite file lock; no credentials enter SQLite. Keep access tokens short-lived.
Background renewal uses the existing refresh endpoint and App Check.
Alternative independent refresh with 401 retries cannot guarantee the winning
credential survives, so it is rejected.

## Logout transport prerequisite

`shared/api/client.ts` previously attempted silent authentication recovery when
`POST /auth/logout` returned 401. Now revocation errors return to the logout
owner, which performs local cleanup. This prevents recursive refresh while ending
a session. `rider-app/__tests__/api-client-401-refresh.test.ts` exercises the real
client, with only network/auth-store boundaries mocked.

Blast radius: shared client consumers in rider and driver, explicit logout and
logout-all local cleanup. Normal API/refresh recovery is unchanged. No ride state,
money, backend token lifetime or server revocation changes. No visible copy/layout
change. No mobile visual regression tooling is active.

Before: logout 401 -> refresh -> possible recursive logout.
After: logout 401 -> caller cleanup; server revocation failure remains reported.

Rollback: restore prior app build; no schema or durable business-data changes.
Disabling `background_location_fanout_enabled` only stops live rider delivery,
not session renewal. Native production builds and physical-device behavior remain
unverified. Verification results and final changed-file list will be added below.

## Foreground coordination and background provider

`shared/store/authStore.ts` now serializes sign-in publication, refresh and local
session clearing. Refresh never clears a logout marker. Foreground adopts a fresh
background-issued token before another rotation. Logout revokes the latest stored
access/refresh pair, including a recoverable foreground with no in-memory access
token. A foreground login generation fences delayed go-offline cleanup; native
headless contexts never perform interactive sign-in. Server logout errors no
longer retry 503 or enter authentication recovery; existing local cleanup owns
the outcome. Invalid-refresh cleanup does not make authenticated HTTP requests.

`driver-app/utils/backgroundAuth.ts` renews inside the same native lock, includes
App Check, omits cookies so a stale cookie cannot override the current body token,
and bounds fetch to ten seconds. Credentials remain in SecureStore with the
driver's background-readable device-only policy. Read errors refuse upload;
network/storage errors are reported and preserve captured positions. Transient
failures back off 30 seconds. A rejected candidate is not replayed by every native
callback; its SHA-256 fingerprint is kept in SecureStore so that suppression also
survives a headless restart. A new credential resumes attempts. No independent
Firebase re-login. The deadline covers App Check preparation and response parsing,
so a stalled native preparation cannot retain the session lock indefinitely.

Blast radius: shared authStore initialize/setTokens/refreshTokens/logout/logoutAll;
driver session teardown; rider rideStore/aiChatStore cleanup callbacks; shared
401/proactive refresh callers. Native lock is registered only by driver startup,
so rider has no SQLite dependency or changed keychain policy. No callbacks were
found to reacquire the session lock. Background token consumer also serves
backgroundMessaging's offer actions, so it benefits from valid headless auth.

Before: background token expiry -> null until foreground refresh.
After: read newest credentials under native exclusion -> renew if needed -> save
successor -> resume uploads. Logout/session replacement fences discard late replies.

Verification so far: real SQLite exclusion/acquisition tests 3 passed; actual
shared-client logout regression 5 passed; foreground auth/initialization 36 passed.
Four new foreground regressions failed before implementation. Existing storage
failure tests now select the credential key rather than assuming global read order.
Background provider tests mock native SecureStore/Firebase and HTTP, not provider
logic. A lost successful refresh response remains a backend rotation limitation:
this change cannot recover a successor credential the server never delivered.

Sources checked: [Expo SQLite](https://docs.expo.dev/versions/v55.0.0/sdk/sqlite/),
[Apple Keychain accessibility](https://developer.apple.com/documentation/security/ksecattraccessibleafterfirstunlockthisdeviceonly),
and installed Expo SQLite source. Native suspension/Keychain behavior needs device
validation; JavaScript tests do not establish it.
