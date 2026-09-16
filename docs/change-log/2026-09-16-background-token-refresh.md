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
