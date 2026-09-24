# Change Impact & Risk Log — `nativeSessionLock` PRAGMA statement not covered by contention retry

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code session (daily `/sentry-triage --severity-only` scan) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | (this branch, `fix/native-session-lock-pragma-busy-timeout-retry`) |
| Related issue or gap ID | Sentry CRIMSON-SMOKE-7445-120 |

## 1. Issue / gap identified

`FunctionCallException: execAsync failed → SQLiteErrorException: database is locked` fired 10 times over ~11 minutes for one driver, propagating up through `backgroundAuth.ts`'s top-level catch (tagged `domain=auth, surface=driver-app, reason=background_token_refresh`). The driver's background token refresh failed outright instead of retrying through contention, on real device SQLite.

## 2. Root cause

`createNativeSessionLock` (`driver-app/utils/nativeSessionLock.ts`) opens a fresh SQLite connection per call and runs `PRAGMA busy_timeout = 0` once, **before** entering the hand-rolled contention-retry loop (50ms backoff, 10s deadline) that only wraps the subsequent `BEGIN IMMEDIATE` statement. On a real device, the `PRAGMA` statement itself can throw `SQLITE_BUSY`/"database is locked" if a second connection (the headless background task vs. the foreground JS runtime) opens while another connection already holds a write lock — and since it sat outside the retry loop, that throw was uncaught and propagated immediately on the very first attempt, with zero retry.

Confirmed as a structural test gap, not incidental: `nativeSessionLock.test.ts`'s existing contention test mocked `execAsync` to throw "database is locked" only when `sql === 'BEGIN IMMEDIATE'` — the `PRAGMA`-stage failure path was never exercised.

## 3. Fix / remediation

Moved `PRAGMA busy_timeout = 0` inside the same retry loop as `BEGIN IMMEDIATE`, re-issuing it on every attempt. Re-running the pragma is a true no-op once already set (per-connection SQLite pragma, no side effect beyond the value), and `BEGIN IMMEDIATE` only ever runs after the pragma succeeds in the same iteration, so no transaction-state hazard is introduced. The existing 50ms-backoff/10s-deadline hand-rolled retry mechanism is unchanged — `busy_timeout` stays pinned at 0, so SQLite's own native blocking wait is still never engaged (this was a deliberate design choice per the file's own comment, preserved by this fix).

Added `retries when PRAGMA busy_timeout itself throws database-is-locked, not just BEGIN IMMEDIATE` — mocks the PRAGMA statement failing twice then succeeding, asserting the lock is eventually acquired with the expected retry count. Verified via `git stash` on just `nativeSessionLock.ts` that this test fails without the fix and passes with it.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `createNativeSessionLock`'s internal retry sequencing.** No change to the function's external contract (`SessionLock` interface, `shared/auth/sessionLock.ts`), no change to when/how the lock is acquired-and-released, no change to callers (`backgroundAuth.ts`, `installNativeSessionCoordination`).
- Behavior is unchanged for the non-contended case (PRAGMA succeeds on the first attempt, same as before) and for non-lock PRAGMA failures (e.g. I/O error, corrupt file — still fails the same regex test and rethrows immediately, no new retry introduced for a case that shouldn't retry).
- All 5 existing tests in `nativeSessionLock.test.ts` still pass; `backgroundAuth.test.ts` (38 tests) and `authStore.refreshRace.test.ts` re-run for regression safety, both clean.

## 5. User-experience effect

Driver-facing: background token refresh should now survive a transient lock-contention window on the PRAGMA statement instead of failing outright, matching the retry behavior already present for `BEGIN IMMEDIATE`. Not visible mid-session as a UI change — this is a background-reliability fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/nativeSessionLock.ts` | `PRAGMA busy_timeout = 0` moved inside the retry loop, re-issued per attempt | Close the uncaught-throw gap on the PRAGMA statement |
| `driver-app/__tests__/utils/nativeSessionLock.test.ts` | New test covering PRAGMA-stage contention/retry | Regression guardrail for the exact class of bug the old mock couldn't catch |

## 7. Before / after

```ts
// Before
await db.execAsync('PRAGMA busy_timeout = 0');
const deadline = Date.now() + 10_000;
while (!acquired) {
  try {
    await db.execAsync('BEGIN IMMEDIATE');
    acquired = true;
  } catch (error) {
    if (!/database is locked|SQLITE_BUSY/i.test(String(error)) || Date.now() >= deadline) throw error;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}

// After
const deadline = Date.now() + 10_000;
while (!acquired) {
  try {
    await db.execAsync('PRAGMA busy_timeout = 0');
    await db.execAsync('BEGIN IMMEDIATE');
    acquired = true;
  } catch (error) {
    if (!/database is locked|SQLITE_BUSY/i.test(String(error)) || Date.now() >= deadline) throw error;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
}
```

## 8. Rollback plan

`git revert` is a complete rollback — no data migration, no config/flag, no schema change. Reverting returns to the prior (already-broken-in-this-narrow-case, not newly-broken) behavior.

## 9. Verification performed

- [x] Automated tests run — `npx jest __tests__/utils/nativeSessionLock.test.ts` (5 passed), plus `__tests__/utils/backgroundAuth.test.ts` and `__tests__/store/authStore.refreshRace.test.ts` (38 passed) for regression safety on the surrounding auth surface
- [x] Regression proof — the new test confirmed to FAIL without the fix (`git stash` on `nativeSessionLock.ts` alone) and PASS with it
- [ ] Manual repro steps followed in staging/device — not performed; no physical device or staging environment access in this sandboxed session; this is a native-module bug that can only be triggered by real cross-runtime SQLite file contention, not reproducible in this environment beyond the mocked test
- [x] Blast-radius grep performed — confirmed `createNativeSessionLock`'s only caller path is `installNativeSessionCoordination` → `shared/auth/sessionLock.ts`'s `installSessionLock`, used by `backgroundAuth.ts`
- [x] Reviewed against relevant `CLAUDE.md` convention(s) — background-loop/native-mutex reliability; dispatched `spinr-realtime-reliability-reviewer` before commit (verdict: SHIP IT AS-IS, all 5 requested checks passed)

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the non-contended and non-lock-error paths are byte-for-byte unchanged

## What was NOT verified

- Not tested against a real physical device or a real cross-runtime (foreground + headless background task) SQLite contention scenario — verified via the existing mocked unit-test harness (`node:sqlite`-backed real-file test plus the new PRAGMA-mock test) only, per this repo's existing test conventions for this file.
- CRIMSON-SMOKE-7445-121 (a correlated, same-incident issue 13 seconds later — a deadline-composition bug in `backgroundAuth.ts` where the shared 10s budget starts after `withSessionLock` is entered) is deliberately **not** addressed by this PR — confirmed with the user via `AskUserQuestion` to fix separately with independent per-step deadlines, per this repo's one-PR-per-root-cause rule.
