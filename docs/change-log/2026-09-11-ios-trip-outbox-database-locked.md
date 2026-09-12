# 2026-09-11 — driver-app: trip-location outbox serialises SQLite operations (iOS `database is locked`)

Surface: driver-app (GPS trail capture — dispatch/ride-adjacent, live-tested).
Incident: test ride `SPR-NUZCQG`, iPhone15,3, driver build `2.0.0+29`, 2026-09-11 23:13–23:27 UTC.

| Field | Entry |
|---|---|
| **Issue/gap identified** | The driver app threw away 48 GPS fixes during one 14-minute ride (`gps capture drops during ride` → `enqueue_failures: 48`, Sentry `CRIMSON-SMOKE-7445-S0`; the same failure surfaced as an exception in `CRIMSON-SMOKE-7445-P7`: `finalizeAsync … Caused by: SQLiteErrorException: Error code 5: database is locked`). |
| **Root cause** | `TripLocationOutbox` ran every operation independently. expo-sqlite 57's `withExclusiveTransactionAsync` opens a **new native connection per call** (`useNewConnection: true`) and issues a plain deferred `BEGIN`. `enqueue` reads the session row before it writes, so when the foreground watcher, the background task and the uploader's `acknowledge` overlapped, the second caller's read→write promotion failed immediately with `database is locked`. SQLite does not consult the busy handler when promoting an already-open read transaction, so a `PRAGMA busy_timeout` would **not** have fixed this (and each transaction connection is fresh anyway, so the schema-time pragmas never reach it). `closeSession` is a bare write on the main connection and failed the same way. |
| **Fix/remediation** | All ten database-touching methods on `TripLocationOutbox` now run through one in-process promise chain (`withDatabase()` / `operationTail`): one outbox operation at a time, a failed operation never wedges the chain. Transaction semantics, SQL, schema and the public API are unchanged. |
| **Risk & impact on existing functionality** | Blast radius: `driver-app/utils/tripLocationOutbox.ts` only. Consumers: `utils/tripLocationRecorder.ts` (enqueue / startSession / closeSession / peek / acknowledge / pendingCount / latestPoint / listPendingSessions / purgeAll) and `utils/sessionTeardown.ts` (`purgeAll`). No backend, rider-app or shared change. Behavioural risk: operations are now queued, so an uploader `peek` may wait for an in-flight `enqueue` (single-digit ms at ≤ 1 fix/s). Residual not covered: two *separate* JS runtimes on one device (Android headless task while the main app is also alive) share the file but not the chain — the existing "reuses the one open session when separate JavaScript contexts share the database" test pins the session semantics for that case; locking there is unchanged from today. |
| **User experience effect** | Drivers: no visible change; trail points that were silently dropped are now kept, so the route line, distance audit (ADR 016) and SGI Period-3 evidence are complete. Nothing visible mid-session. |
| **Files modified** | `driver-app/utils/tripLocationOutbox.ts` — `withDatabase()` serialiser; every public method wrapped — fix the race. `driver-app/utils/__tests__/tripLocationOutbox.test.ts` — `LockingSqliteDatabase` fake that models the real expo-sqlite contract + 4 tests — regression coverage the lenient fake could never give. |
| **Before/after snippet** | See below. |
| **Rollback plan** | JS-only, no persisted-format change: `eas update:republish` the previous update group (or `git revert` + the normal OTA publish). The SQLite file on device is unchanged in shape, so the old code reads it fine. |
| **Verification performed** | New tests fail on the old code (3 of 4 throw `Error code 5: database is locked`; the 4th guards the chain itself) and pass on the new. `npx jest` on the outbox, recorder, completeFlush and backgroundLocation suites: 99 passed. `npx tsc --noEmit -p driver-app` clean; `eslint` clean on both files. **No production build run** — JS-only change, OTA-eligible; no native module, manifest or plugin touched. |
| **What was NOT verified** | Not run on a device. The fake reproduces the contract documented by expo-sqlite ("the other async write queries will abort with `database is locked`") and observed in Sentry P7 — it is not real SQLite. Whether iOS also suspends the app while locked (the 9.5 min of zero fixes on the same ride) is a separate question this change does not address; it depends on the phone's Location permission being "Always". rider-app/driver-app have no visual-regression tooling; nothing visual changed. |

## Before / after

```ts
// before — each call races every other caller on a fresh connection
async enqueue(fix: TripLocationFix): Promise<TripLocationPoint> {
  assertFiniteLocationFix(fix);
  const database = await this.getDatabase();
  let point: TripLocationPoint | null = null;
  await database.withExclusiveTransactionAsync(async (transaction) => { /* read session, write */ });
  return point!;
}

// after — one outbox operation at a time, in this runtime
async enqueue(fix: TripLocationFix): Promise<TripLocationPoint> {
  assertFiniteLocationFix(fix);
  return this.withDatabase(async (database) => {
    let point: TripLocationPoint | null = null;
    await database.withExclusiveTransactionAsync(async (transaction) => { /* unchanged */ });
    return point!;
  });
}

private withDatabase<T>(operation: (database: TripLocationOutboxDatabase) => Promise<T>): Promise<T> {
  const run = this.operationTail.then(async () => operation(await this.getDatabase()));
  this.operationTail = run.then(() => undefined, () => undefined);
  return run;
}
```

## Same-window observations not changed here

- Backend: `payment_intent.payment_failed` webhook for this ride arrived at 23:13:12, ~1 s after booking, before the ride row was readable → deliberate 500 (`Ride lookup failed — Stripe will retry`, Sentry T4/T5/T6). Pre-existing race; Stripe retries.
- `meta_capi` Purchase batch rejected with 400 `Invalid extended device info param` (Sentry PS) — analytics only.
- Supabase 23:00–23:35 UTC: 0 × 4xx/5xx, per-minute p95 ≤ 136 ms, peak 630 req/min; 24 PostgREST `Warp server error: Thread killed by timeout manager` lines (idle keep-alive connections, not query failures); 0 Postgres errors.
