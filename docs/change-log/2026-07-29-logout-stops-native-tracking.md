# Change Impact & Risk Log — native location tracking kept running after sign-out

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | `drivers` |
| PR / commit link | _(pending)_ |
| Related issue or gap ID | Post-logout background leak — **part (b) of 2** (part (a): `2026-07-29-logout-clears-bg-token.md`) |

## 1. Issue / gap identified

Part (a) stopped post-logout *uploads* by clearing the cached access token. The
native task itself kept running. After a driver signed out without first going
offline:

- `expo-location` kept delivering fixes, and `tripLocationRecorder.recordNativeFix`
  kept writing them to the durable SQLite outbox — **location collected for a user
  who had ended their session**, even with transmission stopped.
- The Android foreground-service notification kept showing
  *"Spinr Driver — You're online and receiving ride requests"* to a signed-out user.
- The recovery geofence stayed armed, so a boundary exit could headlessly **re-arm**
  tracking via the `GEOFENCE_TASK` handler.
- Battery kept draining at `IDLE_CADENCE` or `TRIP_CADENCE`.

## 2. Root cause

`stopBackgroundLocation()` and `stopGeofenceRecovery()` were only ever called from
the go-offline branch of `useDriverDashboard.toggleOnline`
(`useDriverDashboard.ts:1462-1463`). Nothing connected them to sign-out:
`driver-app` registered **no** logout callback — `grep registerLogoutCallback`
across the app returned only a jest mock in one test file. Going offline and
signing out were treated as the same act by the UI but not by the code, and a
driver can do the second without the first.

## 3. Fix / remediation

`driver-app/app/_layout.tsx` registers a logout callback that clears the trip flag,
stops background location, and disarms the recovery geofence.

Two deliberate choices:

**`registerLogoutCallback`, not an `authToken` transition effect.** The file
already has the latter idiom immediately above (resetting driver ride state), and
reusing it here would have been a bug: `authToken` also goes null on a **transient**
refresh failure, where `authStore` sets `sessionRecoverable` and clears the token
*without* calling `logout()`. Tearing down native tracking on a flaky connection
would leave a driver who reconnects with no background tracking until they
toggled offline and back on — silently losing the breadcrumbs that settle billed
distance. `registerLogoutCallback` fires only on a deliberate sign-out.

**Trip flag cleared first.** `setBackgroundTripActive(false)` precedes the stops so
a geofence wake landing between calls cannot re-arm at `TRIP_CADENCE` off a stale
`spinr_bg_trip_active`.

Each step is individually `.catch()`ed so one native failure cannot skip the
others, and `_runLogoutCallbacks` wraps the whole callback, so a throw can never
block sign-out.

## 4. Risk & impact on existing functionality

**Blast radius: driver-app only.** `_layout.tsx` is the root layout, and the change
adds one `useEffect` plus named imports to a module it already imported for side
effects.

- **The bare `import '../utils/backgroundLocation'` became a named import.** That
  import is first in the file because loading the module registers the
  `TaskManager` tasks. ES imports hoist, so a named import from the same specifier
  registers them at exactly the same point — no behaviour change. Verified by a
  clean `tsc` and a successful production bundle.
- **Could this stop tracking for a driver who is still working?** Only via a
  deliberate `logout()`. The `sessionRecoverable` path is explicitly excluded —
  see §3. This is the main regression risk and the reason for the mechanism choice.
- **Interaction with `toggleOnline`:** both now call the same stop functions.
  `stopBackgroundLocation` early-returns when the task is not running
  (`hasStartedLocationUpdatesAsync`), and `stopGeofenceRecovery` bumps its
  generation fence synchronously and tolerates "nothing is monitored". Signing out
  while already offline is therefore a safe no-op, not a double-stop.
- **Durable outbox:** untouched. Points already queued in SQLite stay queued;
  this only stops *new* fixes being captured. Whether an ended session's queued
  points should still be uploaded on next sign-in is a separate question, unchanged
  by this diff.
- **Registration lifetime:** the effect returns the unregister function, so the
  callback is removed if the root layout ever unmounts. `[]` deps, so it registers
  once per mount.

Not touched: ride state machine, money/wallet arithmetic, backend background
loops, RLS, migrations, WebSocket events. No new logging, no PII.

**Defence in depth is intentional:** part (a)'s storage clear still stands. This
callback only runs if the root layout mounted, so it must not be the sole
protection — if it never registers, uploads still stop for want of a token.

## 5. User-experience effect

- **Driver:** signing out now also removes the persistent "You're online" foreground
  notification and stops location tracking. Both are what a signed-out user expects;
  today the notification lingering is arguably a bug report waiting to happen.
- **Rider / corporate admin / internal admin:** none.
- **Visible mid-session:** no — takes effect only at sign-out. But the notification
  disappearing **is** a visible change to an already-shipped flow, hence this field.
- **Compliance:** closes the collection half of the PIPEDA exposure; part (a) closed
  the transmission half.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/_layout.tsx` | Bare side-effect import → named imports of `setBackgroundTripActive` / `stopBackgroundLocation` / `stopGeofenceRecovery`; added `registerLogoutCallback` to the shared-store import; new `useEffect` registering the teardown | Nothing connected sign-out to the native task |
| `driver-app/__tests__/store/authStore.logoutStorage.test.ts` | Added 4 cases pinning the `registerLogoutCallback` contract this fix depends on | That mechanism had zero direct coverage anywhere |

## 7. Before / after

```tsx
// Before — the only teardown path, in useDriverDashboard.toggleOnline:
} else {
  await stopBackgroundLocation();
  await stopGeofenceRecovery().catch(() => {});
}
// Sign-out reached neither. driver-app registered no logout callback at all.
```

```tsx
// After — driver-app/app/_layout.tsx
useEffect(() => {
  return registerLogoutCallback(async () => {
    await setBackgroundTripActive(false).catch(() => {});
    await stopBackgroundLocation().catch(() => {});
    await stopGeofenceRecovery().catch(() => {});
  });
}, []);
```

## 8. Rollback plan

`git revert` of this diff, then an app build (OTA or EAS). **Not instant** — client
change.

Nothing is written to the database; no migration, no `app_settings` value. On
rollback the native task returns to outliving sign-out, but part (a) still prevents
uploads, so the auth exposure stays closed independently. That separation is why
the two halves were shipped as separate commits.

**Not feature-flagged** (gate #3): the flag read is an authenticated HTTP call, and
this code runs while the session is being torn down — the flag lookup would 401
into the path being gated. There is also no meaningful "on/off" choice here:
tracking a signed-out user is not a variant worth selecting.

## 9. Verification performed

- [x] **`npx jest __tests__/store/authStore.logoutStorage.test.ts`** → **9/9 passed**
      (5 from part (a) + 4 new). The new cases pin: callbacks run on logout; async
      callbacks are awaited before `logout()` resolves (otherwise native teardown
      could race the next sign-in); a **throwing** callback cannot block sign-out and
      cannot leave session material behind; an unregistered callback stops running.
- [x] **`npx tsc --noEmit` (driver-app)** → exit 0, zero diagnostics — this is also
      what confirms the import restructure is sound.
- [x] **`npx eslint app/_layout.tsx`** → **0 errors**, 33 warnings, all pre-existing
      patterns in that file (`require()`-style imports at lines 145/517,
      `exhaustive-deps` at 442). The new effect adds neither.
- [x] **Full driver-app suite** — 45/46 suites, **345/346 tests passed**. The single
      failure is `onlineResync`, the pre-existing one already reproduced on HEAD with
      all session changes reverted. `ActivityView` passed this run, confirming it is
      a parallel-load flake rather than a regression.
- [x] **Real production bundle** — `npx expo export --platform web` (driver-app) →
      **exit 0**, bundle emitted. Relevant here because this diff restructured the
      import that registers the `TaskManager` tasks.
- [x] **Blast-radius grep performed** — `stopBackgroundLocation`,
      `startBackgroundLocation`, `stopGeofenceRecovery` call sites across
      `driver-app`; `registerLogoutCallback` across all surfaces; confirmed
      `_runLogoutCallbacks` wraps each callback in try/catch before relying on it.
- [x] **Reviewed against `CLAUDE.md` conventions** — PIPEDA data-minimisation is the
      point; no coordinates or token material logged. No money, state machine, RLS,
      or migration. The `is_online` flip is untouched, so the
      `is_available ⇒ is_online` invariant is unaffected (`logout()` already PUTs
      `is_online: false` before this callback runs).

### What was NOT verified

- **The registration itself is not covered by a test.** There is no `_layout.tsx`
  test anywhere in driver-app (`find driver-app/__tests__ -name "*layout*"` → empty),
  and rendering the root layout in jest would pull in Firebase, LogRocket, Stripe,
  TanStack persistence, and the native task registrations. So what is tested is the
  *contract* (`registerLogoutCallback` runs, awaits, survives throws) and not the
  *wiring* (that `_layout` actually calls it). A regression that deleted the
  `useEffect` would pass CI. Closing that needs a root-layout test harness, which is
  its own piece of work.
- **No real-device confirmation** — not verified on a physical driver phone that the
  foreground-service notification disappears, that fixes stop being recorded, or
  that the geofence is genuinely disarmed. All three are native behaviours that jest
  cannot exercise.
- **No native build.** `expo export --platform web` exercises the production
  Metro/babel pipeline only; a real EAS build needs `[build]` in the commit message.
  This change touches native-module call sites, so a native build is more relevant
  here than for the earlier pure-logic commits.
- **Queued-but-unsent points from an ended session are left in the outbox.** Whether
  they should be uploaded on next sign-in, discarded, or retained for the SGI audit
  is a policy question this diff does not answer and does not change.
- **The pre-existing `onlineResync` failure and `ActivityView` flake** remain; see
  `2026-07-29-concurrent-401-false-logout.md` §9 for that triage.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and notes that part (a) keeps the auth
      exposure closed independently
- [x] Blast radius is stated, not assumed — the import restructure, the
      double-stop case, and the `sessionRecoverable` trap are each addressed with the
      specific reason they are safe
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 calls out that the foreground notification disappearing is a
      user-visible change, and §9 states plainly that the wiring itself is untested
