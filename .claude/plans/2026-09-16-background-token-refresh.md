# Background location authentication repair

Goal: renew driver credentials from native GPS callbacks without racing foreground
refresh, reviving logout, or weakening token lifetime/revocation.

Design: a driver-registered SQLite write transaction serializes session operations
across JS runtimes; credentials stay in SecureStore. Lock contention defers work,
never bypasses exclusion. Sign-in, refresh publication and logout use the same lock.
Background requests have deadlines and retain recorded positions on failure.
The rider's auth behavior has no SQLite dependency. No backend/schema changes.

Alternative: let both contexts refresh independently and retry 401s. Rejected:
single-use rotation can lose the winning credentials and sign the driver out.
Extending access-token lifetime merely postpones the failure.

## Sequential subtasks (commit each before the next; at most three files)

- [ ] Shared coordination interface, native SQLite lock, actual SQLite contention
  test. Verify mutual exclusion, acquisition failure, release after exceptions.
- [ ] Integrate foreground credential publication/refresh/logout with coordination,
  add auth regressions, and change-impact log. Verify rotated-token adoption,
  login/logout races, storage errors, existing rider and driver auth tests.
- [ ] Add lightweight background renewal and tests, update impact log. Verify
  expiry, App Check, 401, network/storage failure and concurrent renewal.
- [ ] Wire native coordinator and background renewal into driver startup/task and
  extend actual-task regressions. Verify expired-token GPS upload and logout.
- [ ] Security review, relevant tests and bundle checks; record exact limitations.

Release: new driver build required; enable existing migration 427 delivery flag
separately. Test pickup and an active ride with driver screen locked >30 minutes
on Android/iOS; verify successive rider coordinates across access-token expiry.
No claim of physical-device correctness based on mocked native APIs.

TodoWrite is unavailable in this harness; this checklist tracks the subtasks.
