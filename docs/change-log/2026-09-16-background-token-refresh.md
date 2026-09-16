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


## Final integration and review

Native coordination is installed from `driver-app/index.js` before messaging and
Router load, and from the location module before task registration. Expired
background credentials now renew before both live position and durable batch
uploads. Recording remains before authentication/network work.

Security review caught a delayed callback adopting the next login's credentials.
Explicit sign-in now writes a non-secret persistent capture epoch under the lock;
rotation preserves it. Each location callback snapshots that epoch before capture
and strictly rechecks it, including legacy null, after App Check immediately before
each fetch. Unreadable ownership defers uploads and retains samples. Two race
regressions failed before the change; two storage-failure cases retain the outbox.
The security reviewer accepted the final integration and both sign-out handlers.

User experience: live updates can continue across access-token expiry without
opening the driver app. Native coordination errors on either profile sign-out
button now show "Sign Out Failed" and do not navigate as if cleanup succeeded.
This changes error feedback only; no normal-flow layout change. Driver/rider have
no active visual regression tooling; profile feedback was reasoned about, not
screenshotted on a device.

Before: delayed callback -> next login's token -> old captured fix uploaded.
After: capture epoch != current epoch -> defer upload; no request is sent.
Before: logout-all cleanup throws -> finally navigates to login.
After: cleanup throws -> error toast; navigate only on completed cleanup.

### Changed files

| File | Change and purpose |
|---|---|
| `shared/auth/sessionLock.ts` | Driver-installed coordination, keychain policy, sign-in epoch key. |
| `shared/store/authStore.ts` | Serialize publication/rotation/logout and preserve session ownership. |
| `shared/api/client.ts` | Avoid recursive recovery during session revocation. |
| `driver-app/utils/nativeSessionLock.ts` | SQLite native mutual exclusion without storing credentials. |
| `driver-app/utils/backgroundAuth.ts` | Bounded headless renewal with rejection suppression. |
| `driver-app/utils/backgroundLocation.ts` | Use renewal and check capture ownership before upload. |
| `driver-app/index.js` | Install coordination before headless consumers load. |
| `driver-app/app/driver/(tabs)/profile.tsx` | Report failed session cleanup for both sign-out buttons. |
| `driver-app/__tests__/utils/nativeSessionLock.test.ts` | Actual SQLite contention and entry-point ordering. |
| `driver-app/__tests__/utils/backgroundAuth.test.ts` | Renewal, deadlines, rejection, storage, concurrency. |
| `driver-app/__tests__/store/authStore.refreshRace.test.ts` | Foreground/background coordination and login epochs. |
| `driver-app/__tests__/store/authStore.initialize.test.ts` | Key-specific storage failure fixtures. |
| `driver-app/utils/__tests__/backgroundLocation.test.ts` | Real callback expiry, account switching and ownership-read errors. |
| `driver-app/__tests__/utils/backgroundLocation.reassert.test.ts` | Mock added native boundaries; retain service regressions. |
| `rider-app/__tests__/api-client-401-refresh.test.ts` | Shared logout transport regression. |
| `.claude/plans/2026-09-16-background-token-refresh.md` | Subtask and verification tracking. |
| `docs/change-log/2026-09-16-background-token-refresh.md` | Impact, rollout and evidence. |

### Verification and release boundary

- Driver targeted auth/location/SQLite tests: 135 passed; three unfinished
  `logout / logoutAll` tests from the separately paused task excluded explicitly.
  Their edits remain uncommitted and were not included in this change.
- Background messaging Android/iOS: 36 passed using `--no-cache`. A cached paired
  run incorrectly skipped Android notification setup; standalone Android (26)
  and the uncached pair both passed. The location/token boundary is mocked in
  messaging tests; actual renewal is covered by provider and task suites.
- Rider shared API client: 5 passed. Total targeted passing tests: 176.
- Final production Expo exports succeeded for Android and iOS (Hermes bundles)
  using `expo export --platform <platform> --max-workers 2`. These are production
  JavaScript exports, **not** native APK/IPA compilation or an EAS release.
- Native SQLite locking is tested with Node SQLite on a real temporary file;
  SecureStore, Firebase/App Check and OS callbacks are mocked in automated tests.
- No live backend/Supabase changes, device builds, EAS updates or deployment made.
  Existing pre-upgrade Keychain entries require successful foreground credential
  rewriting while unlocked; install the new driver build and sign in for canary.
- Apply migration 427 and set `public.settings.background_location_fanout_enabled`
  true on `id = 'app_settings'` for delivery. That flag remains an independent
  backend prerequisite; its live value was not verified or modified here.
- Canary Android and iOS: pickup and active ride, screen locked >30 minutes;
  rider must receive successive fixes across token expiry. Also test foreground
  resume, network interruption, and logout/new login. OS suspension and a lost
  server rotation response remain limitations requiring device observation.
- On native lock contention the operation fails closed after ten seconds; it
  never steals a lock from a suspended owner. A failed sign-out must be retried.
  Disable the fanout flag to stop live delivery; restore the previous driver
  build to roll back client renewal. No schema/business data changes to undo.


Final combined uncached driver run: 8 suites passed, 171 passed, 3 paused-task
cases excluded. Rider suite: 5 passed. Final driver `tsc --noEmit --incremental
false` passed; `git diff --check` passed. Targeted ESLint on the three runtime
utilities and profile reported 8 pre-existing JSX quote errors in unchanged
profile text (verified against the base commit), plus 96 warnings including four
new import-style warnings in the native helper/provider. Runtime utility files
have no lint errors. Full lint is therefore **not green**; no unrelated UI cleanup
was folded into this auth repair. Both final Android/iOS exports include the
session fence and sign-out feedback. Production backend/device behavior was not
claimed as verified.
