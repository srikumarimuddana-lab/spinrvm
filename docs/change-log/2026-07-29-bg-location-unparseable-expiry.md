# Change Impact & Risk Log — headless location uploads failed silently on an unparseable expiry

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | `drivers` |
| PR / commit link | _(pending — subtask 4 of 6)_ |
| Related issue or gap ID | Driver-app frequent sign-out, collateral damage from root cause 1 |

## 1. Issue / gap identified

Not a sign-out issue — a data-loss issue discovered while tracing root cause 1.
While `authStore` was persisting the string `"NaN"` into SecureStore
`token_expires_at`, the driver app's headless location task stopped uploading
trip breadcrumbs roughly 15 minutes after every login, **with nothing in the logs
and no crash report.** Billed distance and the SGI insurance-period audit are
settled from those breadcrumbs.

## 2. Root cause

`getBackgroundAuthToken()` gated token freshness on:

```ts
const expiresAt = parseInt(fgExpiry, 10);
if (Date.now() < expiresAt - 30_000) { /* use the token */ }
```

`parseInt("NaN")` is `NaN`, and **every** comparison against `NaN` is `false`. So
the guard failed *closed*: no token returned, upload deferred, no log line, no
`recordNonFatal`. The function's three exit paths were indistinguishable from the
outside — "driver is signed out" (normal), "token genuinely expired" (normal), and
"persisted state is corrupt" (an anomaly that disables uploads indefinitely) all
returned a bare `null`.

Subtasks 1 and 2 mean `token_expires_at` can no longer be `"NaN"`, so the trigger
is gone. This closes the **silence**, which is the part that let it run for a
release.

## 3. Fix / remediation

1. `_parseExpiryMs(raw, key)` returns `number | null`, rejecting non-finite values
   explicitly instead of letting `NaN` poison a comparison.
2. When a persisted expiry is present but unparseable, report it —
   `console.warn` plus `recordNonFatal` tagged
   `{ domain: 'drivers', surface: 'driver-app', bg_auth: 'unparseable_expiry' }`.
   **Latched to once per process:** this function runs on every background fire
   (~4 s at `TRIP_CADENCE`), so reporting every occurrence would emit ~15
   events/min/driver — the same flood the existing deferred-upload `catch`
   deliberately avoids.
3. The cached expiry is written **normalised** (`String(fgExpiry)`) rather than
   the raw string, so a parseable-but-messy value (`"123abc"`) cannot propagate
   into `bg_access_token_expires`.

**Behaviour on an unparseable expiry is unchanged — still defer.** That is
deliberate and documented in the function's JSDoc: the durable outbox treats a
deferral and a 401 identically, so deferring costs nothing extra, and the state
self-heals the moment the foreground next calls `setTokens()`. Using the token
anyway would add a new request path on a live surface for a case that subtasks
1–2 already made unreachable.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to the driver app's headless upload path.**

`getBackgroundAuthToken` has exactly two callers, both verified by grep:

| Caller | Purpose |
|---|---|
| `driver-app/utils/backgroundLocation.ts::handleBackgroundLocationTask` | authorises `POST /api/v1/drivers/location-batch` |
| `driver-app/services/backgroundMessaging.ts:253` | authorises `POST /api/v1/drivers/rides/{id}/decline` from a killed-state FCM ride offer |

The second matters: a headless **decline** also depends on this token. Before the
fix, a corrupt expiry silently broke killed-state ride declines too — a driver
would appear to ignore an offer rather than decline it, and the offer would time
out (~15 s) instead of releasing immediately. The fix does not change the
decline path's behaviour, but it makes that failure visible.

Both callers already treat `null` as "defer / skip", so the unchanged return
contract cannot break them.

Not touched: ride state machine, money arithmetic, wallet deltas, the 16 backend
background loops, RLS, migrations, WebSocket events. No token material and no
coordinates in the new log line — the logged value is an epoch-ms string or
garbage, never PII, which satisfies the PIPEDA logging rules.

Both new module-level items (`_reportedUnparseableExpiry`,
`_resetUnparseableExpiryReport`) are process-scoped. A headless wake starts a
fresh JS context, so the latch resets per wake — the flood guard bounds
per-process volume, not lifetime volume, which is the correct trade.

## 5. User-experience effect

- **Driver:** no visible change. No UI, copy, or notification difference. What
  changes is that a previously-invisible failure now produces a Crashlytics
  non-fatal we can act on.
- **Rider:** none directly. Indirectly, a killed-state decline that silently
  failed now surfaces, which shortens offer-timeout waits once the underlying
  cause is fixed.
- **Internal:** a new Crashlytics non-fatal signature,
  `BgLocation unparseable <key>`, tagged `bg_auth=unparseable_expiry`. Expected
  volume after subtasks 1–2 deploy: **zero.** Any occurrence means persisted auth
  state is corrupt and is worth investigating.
- **Visible mid-session:** no.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/utils/backgroundLocation.ts` | Added `_parseExpiryMs`, `_reportUnparseableExpiry`, the once-per-process latch and its test-only reset; both read paths use the parser; cached expiry written normalised; `getBackgroundAuthToken` JSDoc moved back above the function and extended to document the defer-on-unparseable decision | Stop a corrupt persisted expiry from silently disabling headless uploads and killed-state declines |
| `driver-app/utils/__tests__/backgroundLocation.test.ts` | New `getBackgroundAuthToken — unparseable persisted expiry` describe block, 7 cases | Pin the reporting, the flood latch, the normalised cache write, and — equally important — that the quiet paths stay quiet |

## 7. Before / after

```ts
// Before
const fgExpiry = await SecureStore.getItemAsync('token_expires_at');
if (fgToken && fgExpiry) {
  const expiresAt = parseInt(fgExpiry, 10);          // "NaN" → NaN
  if (Date.now() < expiresAt - 30_000) {             // NaN comparison → false
    await SecureStore.setItemAsync('bg_access_token_expires', fgExpiry);  // raw
    return fgToken;
  }
}
// → falls through to `return null` with no log, no crash report
```

```ts
// After
const fgExpiry = _parseExpiryMs(
  await SecureStore.getItemAsync('token_expires_at'),
  'token_expires_at',
);                                                   // null + reported once
if (fgToken && fgExpiry !== null) {
  if (Date.now() < fgExpiry - 30_000) {
    await SecureStore.setItemAsync('bg_access_token_expires', String(fgExpiry));
    return fgToken;
  }
}
```

## 8. Rollback plan

`git revert` of this single-file diff (plus its test), then an app build. Client
change, so **not instant** — needs an OTA update or EAS build.

No feature flag, no `app_settings` value, no migration, nothing written to the
database. The only persisted-state change is that `bg_access_token_expires` is
written normalised; a rollback simply resumes writing the raw string, and both
forms parse identically for any value that reached the cache before.

**Not feature-flagged** (gate #3): the change adds observability and rejects
invalid input. There is no new user-visible behaviour for a flag to select
between, and the code runs in a headless JS context with no app shell — an
`app_settings` flag read there would need its own authenticated network call, in
the very function whose job is to decide whether a token is usable.

## 9. Verification performed

- [x] **Failing test first, verified by targeted revert.** The reporting call and
      the normalised write were stripped (keeping exports intact so failures were
      informative) and the suite re-run: **4 of 7 failed** — exactly the four
      asserting new behaviour. The other three, which pin behaviour that must
      *not* change (fresh cached token, silence when signed out, silence on a
      genuinely expired token), stayed green in both states. Restored from a
      byte-for-byte backup and verified by grep (`TEMP-REVERT` ×0,
      `_reportUnparseableExpiry(key, raw)` ×1).
- [x] **`npx jest utils/__tests__/backgroundLocation.test.ts`** → **40/40 passed**
      (33 pre-existing + 7 new).
- [x] **`npx tsc --noEmit` (driver-app)** → exit 0, zero diagnostics.
- [x] **`npx eslint`** on both changed files → clean, no output.
- [x] **Real production bundle run** — `npx expo export --platform web`
      (driver-app) → **exit 0**, bundle emitted.
- [x] **Blast-radius grep performed** — `getBackgroundAuthToken` (2 callers, both
      tabulated in §4); `bg_access_token|bg_access_token_expires|fg_access_token|token_expires_at`
      across all four surfaces; `stopBackgroundLocation|startBackgroundLocation`
      to establish the lifecycle of the cached keys.
- [x] **Reviewed against `CLAUDE.md` conventions** — this change exists *because*
      of the "do not silently swallow errors" rule; the logged value is an
      epoch-ms string, never a token, coordinate, phone number, or name, so the
      PIPEDA logging restrictions hold. Sentry/Crashlytics tags follow the
      `domain`/`surface` convention.
- [x] **Feature-flag decision justified** — see §8.

### What was NOT verified

- **No real-device confirmation.** Nothing here has been exercised on a physical
  driver phone: not the headless wake, not the Crashlytics non-fatal actually
  arriving in the console, not the recovery of location-batch uploads. The
  once-per-process latch in particular is asserted in a single JS context, whereas
  on-device each headless wake is a fresh context — the test proves the latch
  works, not that fleet-wide volume is acceptable.
- **The killed-state decline path was reasoned about, not tested.**
  `backgroundMessaging.ts:253` is the second caller and was read, but this diff
  adds no test for it; its behaviour is unchanged because the `null` contract is
  unchanged.
- **A related defect was found and deliberately NOT fixed here:** after logout,
  `bg_access_token` / `bg_access_token_expires` are never cleared —
  `authStore.logout()` does not delete them and `driver-app` registers no
  `registerLogoutCallback`, so `stopBackgroundLocation()` (the only thing that
  clears them) runs solely from `useDriverDashboard.toggleOnline`'s go-offline
  branch. A driver who signs out without first going offline keeps uploading
  location for up to ~15 minutes on a valid cached token. That is a PIPEDA and
  auth concern with its own blast radius, filed as its own task rather than
  bundled into this commit.
- **No native build.** `expo export --platform web` exercises the production
  Metro/babel pipeline, not Hermes or native modules. A real EAS build needs
  `[build]` in the commit message.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and flagged as requiring an app
      build rather than a deploy
- [x] Blast radius is stated, not assumed — both callers of the changed function
      named, including the non-obvious killed-state decline path
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 records the new Crashlytics signature and its expected
      volume of zero, so an occurrence is read as a signal rather than noise
