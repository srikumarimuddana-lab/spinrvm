# Change Impact & Risk Log — a live access token survived sign-out

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | `shared/` (effect on driver-app; rider-app unaffected in practice) |
| Domain (Sentry tag) | `auth` |
| PR / commit link | _(pending)_ |
| Related issue or gap ID | Post-logout background upload leak — **part (a) of 2** |

## 1. Issue / gap identified

A driver who signed out **without first going offline** left a valid access token
in SecureStore, and the headless location task kept using it to
`POST /api/v1/drivers/location-batch` for the remainder of the token's TTL (up to
15 minutes). `logout()` does not bump `token_version`, so the backend accepted
those uploads.

Found while scoping the background-location expiry guard, not from a report.

## 2. Root cause

Two functions wipe session state, and they had **drifted apart** — neither knew
about the driver app's background-task keys:

| | `auth_token` | `fg_access_token` | `bg_access_token` | `bg_access_token_expires` | `refresh_token` | `token_expires_at` |
|---|---|---|---|---|---|---|
| `logout()` | ✓ | ✓ | ✗ | ✗ | ✓ | ✓ |
| `clearAuthStorage()` | ✓ | ✗ | ✗ | ✗ | ✓ | ✓ |

`bg_access_token` is written by `getBackgroundAuthToken()`
(`driver-app/utils/backgroundLocation.ts`), **not** by `setTokens()` — which is
why it was absent from a delete list written alongside `setTokens`. The only code
that ever deleted it was `stopBackgroundLocation()`, and that runs solely from the
go-offline branch of `useDriverDashboard.toggleOnline` (`useDriverDashboard.ts:1462`).
`driver-app` registers no logout callback — `grep registerLogoutCallback` across
the app returns only a jest mock — so nothing connected sign-out to that cleanup.

`clearAuthStorage()` is the worse of the two: it runs on the "no valid session"
branch of `initialize()`, and left **both** access tokens behind.

The underlying defect is not the missing keys, it is that there were two
hand-written lists to keep in sync.

## 3. Fix / remediation

One canonical `AUTH_STORAGE_KEYS` list; both `clearAuthStorage()` and `logout()`
iterate it. A key added in future cannot be covered by one path and missed by the
other.

```ts
const AUTH_STORAGE_KEYS = [
  'auth_token', 'fg_access_token', 'bg_access_token',
  'bg_access_token_expires', 'refresh_token', 'token_expires_at',
] as const;
```

## 4. Risk & impact on existing functionality

**Blast radius: `shared/store/authStore.ts`, both mobile apps.** The change only
*adds* keys to two delete lists and refactors those lists into one.

Every key, and who reads it:

| Key | Reader | Effect of clearing it |
|---|---|---|
| `auth_token` | legacy only; `setTokens` deletes it | none (already cleared by both paths) |
| `fg_access_token` | `getBackgroundAuthToken` step 2 | **newly cleared by `clearAuthStorage`** — correct; a session with no refresh token cannot be recovered anyway |
| `bg_access_token` | `getBackgroundAuthToken` step 1 | **newly cleared by both** — this is the fix |
| `bg_access_token_expires` | `getBackgroundAuthToken` step 1 | as above |
| `refresh_token` | `refreshTokens`, `initialize` | none |
| `token_expires_at` | `getBackgroundAuthToken` step 2, `ensureFreshToken` (via state) | none |

**Could this break session recovery?** No. `clearAuthStorage()` runs only on the
branch where no refresh token exists, i.e. the session is already unrecoverable —
`initialize()` deliberately does *not* call it on transient refresh failures
(`sessionRecoverable`), and that path is untouched. Verified by the pre-existing
`authStore.initialize` suite, which covers the recoverable case and still passes.

**Rider app:** the keys are driver-specific, so clearing them is a harmless no-op
there. The rider app has no headless location task
(`grep TaskManager.defineTask rider-app` → zero hits).

**Web:** `clearAuthStorage` now removes six `sessionStorage` keys instead of
three; the extra three never exist on web (native-only) so the removals are no-ops.

Not touched: ride state machine, money/wallet arithmetic, background loops, RLS,
migrations, WebSocket events. No new logging, no PII.

## 5. User-experience effect

- **Driver:** no visible change to the sign-out flow. What changes is that
  location uploads stop at sign-out instead of trailing it by up to 15 minutes.
  A driver who signs out mid-shift without going offline is the affected case.
- **Rider / corporate admin / internal admin:** none.
- **Visible mid-session:** no — this only takes effect at sign-out.
- **Compliance:** closes a PIPEDA exposure (transmitting location for a user who
  ended their session) and an auth exposure (access token used past logout).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | Added `AUTH_STORAGE_KEYS`; `clearAuthStorage()` and `logout()` now iterate it instead of hand-written lists | A live access token must not outlive its session, and two lists cannot be kept in sync by hand |
| `driver-app/__tests__/store/authStore.logoutStorage.test.ts` | New — 5 cases across both clearing paths | Pin that every session key goes, that unrelated device state stays, and that the `initialize()` path clears the same set |

## 7. Before / after

```ts
// Before — two hand-written lists, neither complete
async function clearAuthStorage() {
  await SecureStore.deleteItemAsync('auth_token');
  await SecureStore.deleteItemAsync('refresh_token');
  await SecureStore.deleteItemAsync('token_expires_at');
  // fg_access_token, bg_access_token, bg_access_token_expires survive
}

logout: async () => {
  await storage.deleteItem('auth_token');
  await storage.deleteItem('fg_access_token');
  await storage.deleteItem('refresh_token');
  await storage.deleteItem('token_expires_at');
  // bg_access_token, bg_access_token_expires survive → headless task keeps uploading
```

```ts
// After — one list, both paths
for (const key of AUTH_STORAGE_KEYS) await SecureStore.deleteItemAsync(key);
...
for (const key of AUTH_STORAGE_KEYS) await storage.deleteItem(key);
```

## 8. Rollback plan

`git revert` of this diff, then an app build. Client change, so **not instant** —
needs an OTA update or EAS build.

Nothing is written to the database; no migration, no `app_settings` value. The only
state effect is that two additional local keys are deleted at sign-out, and a
rollback simply stops deleting them — returning to the leaky status quo, not to a
corrupt state.

**Not feature-flagged** (gate #3): flagging "should sign-out clear session
tokens?" is not a meaningful choice, and the flag read is itself an authenticated
network call inside the code path that ends the session.

## 9. Verification performed

- [x] **Failing test first, verified by revert.** Both edits were reverted to the
      original hand-written lists and the suite re-run: **3 of 5 failed** — exactly
      the three asserting the leak is closed. The other two (unrelated state
      survives; safe when storage is empty) passed in both states, which is what
      makes them useful as guards rather than duplicates. Restored from a
      byte-for-byte backup and verified by grep (`TEMP-REVERT` ×0,
      `AUTH_STORAGE_KEYS` ×5).
- [x] **`npx jest __tests__/store/` (driver-app)** → **7 suites, 45 tests passed**,
      including the pre-existing `authStore.initialize` and `authStore.refreshRace`
      suites that exercise the recoverable-session path.
- [x] **Rider-app auth suites** — `auth.integration.ts`,
      `api-client-401-refresh.test.ts` → **18 passed**.
- [x] **`npx tsc --noEmit`** — driver-app exit 0, rider-app exit 0.
- [x] **Real production bundle** — `npx expo export --platform web` (driver-app)
      → **exit 0**.
- [x] **Blast-radius grep performed** — every `AUTH_STORAGE_KEYS` member searched
      for readers across all four surfaces (tabulated in §4);
      `registerLogoutCallback` across `driver-app` (only a jest mock);
      `TaskManager.defineTask` across `rider-app` (zero hits, confirming the keys
      are driver-only).
- [x] **Reviewed against `CLAUDE.md` conventions** — PIPEDA data-minimisation is
      the point of the change; no new logging, no coordinates, no token material
      anywhere. No money, state machine, RLS, or migration.

### What was NOT verified

- **This is part (a) of two, and the leak is only half closed.** Uploads stop
  because there is no token, but the **native task keeps running**: expo-location
  still delivers fixes, `tripLocationRecorder.recordNativeFix` still writes them to
  the durable SQLite outbox, the Android foreground-service notification
  ("You're online and receiving ride requests") still shows for a signed-out user,
  and the recovery geofence stays armed so a boundary exit can headlessly re-arm
  tracking. Continued *collection* after sign-out remains a PIPEDA concern even
  with transmission stopped. Filed as its own task — it needs a
  `registerLogoutCallback` in `driver-app/app/_layout.tsx` calling
  `stopBackgroundLocation()` + `stopGeofenceRecovery()`.
- **No real-device confirmation.** Not exercised on a physical driver phone: not
  the sign-out-while-online sequence, not the observation that uploads actually
  cease. Asserted at unit level against a mocked SecureStore only.
- **No staging check** that the backend stops receiving `location-batch` calls
  after a sign-out.
- **The pre-existing `onlineResync` failure and `ActivityView` flake** in the
  driver-app suite are unchanged and unrelated; see
  `2026-07-29-concurrent-401-false-logout.md` §9 for the triage. This commit's
  targeted runs (`__tests__/store/`) are fully green.
- **No native build** — `expo export --platform web` exercises the production
  Metro/babel pipeline, not Hermes or native modules.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and flagged as requiring an app build
- [x] Blast radius is stated, not assumed — all six keys tabulated with their
      readers, and the "does this break session recovery?" question answered with
      the specific branch and the test that covers it
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 names the affected case, and §9 states plainly that this
      closes the transmission half and not the collection half
