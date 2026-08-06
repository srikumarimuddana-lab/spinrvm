# Change Impact & Risk Log — client-side access-token expiry derivation

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | `shared/` (consumed by driver-app + rider-app) |
| Domain (Sentry tag) | `auth` |
| PR / commit link | _(pending — subtask 2 of 6)_ |
| Related issue or gap ID | Driver-app frequent sign-out, root cause 1 of 3 — client half |

## 1. Issue / gap identified

Companion to `2026-07-29-refresh-expires-in.md`. That entry fixed the server to
send `expires_in`. This one makes the client unable to break the same way again:
`setTokens()` trusted its `expiresIn` argument unconditionally, so any absent,
`NaN`, zero, or negative value silently poisoned `tokenExpiresAt` and the
persisted `token_expires_at`.

## 2. Root cause

Two separate weaknesses, both in `shared/store/authStore.ts`:

1. **A wrong type made the bug invisible.** `RefreshTokenResponse` declared
   `expires_in: number` (required). The server never sent it, so TypeScript
   believed a field that did not exist at runtime and `expires_in` was
   `undefined`. Nothing could flag it.
2. **No guard at the choke point.** `setTokens()` computed
   `Date.now() + expiresIn * 1000` directly. `undefined * 1000` is `NaN`, and
   `NaN` propagated into both Zustand state (`tokenExpiresAt`) and SecureStore
   (`token_expires_at` written as the string `"NaN"`). Downstream,
   `ensureFreshToken()`'s `!tokenExpiresAt` guard treated `NaN` as
   "not authenticated" and disabled proactive refresh, while the driver app's
   headless location task's `parseInt("NaN")` disabled batch uploads.

The response also carries `access_expires_at`, which was real, usable
information being discarded.

## 3. Fix / remediation

Three changes in one file:

1. `RefreshTokenResponse` now reflects reality — `expires_in?: number` (optional,
   because an installed app can still be talking to a backend that predates the
   server fix) plus `access_expires_at?: string`.
2. New `ttlSecondsFromRefreshResponse()` prefers `expires_in`, else derives the
   TTL from `access_expires_at`, else falls back to 900s. Rejects non-positive
   and unparseable values, so clock skew or a stale response cannot produce a
   TTL in the past.
3. `setTokens()` routes its argument through `usableTtlSeconds()` before any
   arithmetic. A substituted default emits an **ungated** `console.warn` — not
   `__DEV__`-only, because production silence is precisely how the original bug
   survived a release.

900s matches the backend's `ACCESS_TOKEN_EXPIRE_MINUTES` (15) and the
`expires_in ?? 900` default the OTP screens already applied.

## 4. Risk & impact on existing functionality

**Blast radius: `shared/store/authStore.ts` is imported by both mobile apps.**
Five `setTokens()` call sites, all enumerated:

| Call site | Value passed today | Effect of this change |
|---|---|---|
| `shared/store/authStore.ts:365` (`refreshTokens`) | was `expires_in` → `undefined` | **Fixed** — now derived |
| `driver-app/app/otp.tsx:170` | `expires_in ?? 900` | No change (already valid) |
| `rider-app/app/otp.tsx:136` | `expires_in ?? 900` | No change |
| `driver-app/app/reactivate-account.tsx:57` | `expires_in ?? 900` | No change |
| `rider-app/app/reactivate-account.tsx:57` | `expires_in ?? 900` | No change |

The public `setTokens` signature is unchanged, so no caller needs updating —
the guard is belt-and-suspenders for the four that were already correct.

Downstream readers of the values this writes:

- `shared/api/client.ts::ensureFreshToken` reads `tokenExpiresAt`. Behaviour
  change: it now receives a finite number and will actually fire the proactive
  refresh. **This means real refresh traffic resumes** — see risk note below.
- `driver-app/utils/backgroundLocation.ts::getBackgroundAuthToken` reads the
  persisted `token_expires_at`. Behaviour change: it now parses successfully and
  will authorise headless batch uploads again.

**Risk worth naming: this change increases refresh traffic.** Proactive refresh
has effectively been off for every session past its first refresh. Turning it
back on means each active client resumes rotating roughly every 13 minutes
(15-min TTL, 2-min buffer). `/auth/refresh` is rate-limited at 20/minute per
client (`backend/routes/auth.py:1510`), which is far above that, and each refresh
is one row insert plus one update. Expect a step change in `/auth/refresh` call
volume after deploy — that is the fix working, not a regression.

Not touched: ride state machine, money/wallet arithmetic, the 16 background
loops, RLS policies, migrations. No new PII — a TTL integer and an ISO timestamp
already present in the response.

## 5. User-experience effect

- **Driver:** proactive refresh resumes, so the 401 bursts on app-resume stop.
  Headless location uploads resume. Combined with subtask 3 this closes the
  sign-out loop; on its own it reduces the trigger rate, not the logout itself.
- **Rider:** same mechanism, rarely observable.
- **Corporate / internal admin:** none — neither uses this store.
- **Visible mid-session:** yes. A driver already online gets a corrected expiry
  on their next `setTokens()` call. No UI, copy, or notification change.
- **New user-visible log line:** the `console.warn` is developer-facing only; it
  is not surfaced in the UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | `RefreshTokenResponse` corrected to optional `expires_in` + `access_expires_at`; added `DEFAULT_ACCESS_TOKEN_TTL_SECONDS`, `_isUsableTtl`, `usableTtlSeconds`, `ttlSecondsFromRefreshResponse`; `setTokens` guards its argument and warns on substitution; `refreshTokens` derives the TTL from the response | Make a non-finite expiry unreachable, and stop discarding `access_expires_at` |
| `driver-app/__tests__/store/authStore.tokenExpiry.test.ts` | New — 14 cases across `setTokens` and `refreshTokens` | Pin both the guard and the derivation, including a test that reads the persisted value exactly as the headless task does |

## 7. Before / after

```ts
// Before — shared/store/authStore.ts
interface RefreshTokenResponse {
  expires_in: number;            // ← required, but the server never sent it
}

setTokens: async (token, refreshToken, expiresIn, csrfToken) => {
  const expiresAt = Date.now() + expiresIn * 1000;   // undefined → NaN
  ...
  await storage.setItem('token_expires_at', String(expiresAt));  // "NaN"

// in refreshTokens:
const { token, refresh_token: newRefresh, expires_in, csrf_token } = res.data;
await get().setTokens(token, newRefresh, expires_in, csrf_token);
```

```ts
// After
interface RefreshTokenResponse {
  expires_in?: number;           // optional — old backends omit it
  access_expires_at?: string;    // fallback source
}

setTokens: async (token, refreshToken, expiresIn, csrfToken) => {
  const ttlSeconds = usableTtlSeconds(expiresIn);   // never non-finite
  if (ttlSeconds !== expiresIn) console.warn('[Auth] Unusable access-token TTL …');
  const expiresAt = Date.now() + ttlSeconds * 1000;

// in refreshTokens:
const data = res.data as RefreshTokenResponse;
const { token, refresh_token: newRefresh, csrf_token } = data;
await get().setTokens(token, newRefresh, ttlSecondsFromRefreshResponse(data), csrf_token);
```

## 8. Rollback plan

`git revert` of this single-file diff, then ship an app build. **This is a client
change, so rollback is NOT instant** — unlike subtask 1, it requires an OTA
update or an EAS build to reach installed apps.

That asymmetry is the reason the two halves were split: the server-side fix
(subtask 1) already repairs shipped binaries on its own and can be rolled back
in one deploy. This change is defence-in-depth on top of it, so if it needs
reverting, the session behaviour falls back to subtask 1's server-provided
`expires_in` — still correct, just without protection against future drift.

Nothing is written to the database. No migration, no `app_settings` value.

**Not feature-flagged** (gate #3): the flag mechanism is an authenticated HTTP
read, so gating the token-refresh path behind it is circular — the flag lookup
would need a valid token to decide whether tokens are handled correctly. The
change is also strictly a narrowing of accepted input (reject non-finite,
substitute a known-good default), with no new branch a flag could usefully
select between. Stated here rather than silently skipped.

## 9. Verification performed

- [x] **Failing-test-first, verified by temporary revert.** The fix was written
      before the test in this instance, so both hardening sites were reverted and
      the suite re-run: **9 of 14 failed**. Restored from a byte-for-byte backup
      copy (verified by re-grepping both fix sites), then **14/14 passed**.
- [x] **`npx jest __tests__/store/` (driver-app)** → **6 suites, 40 tests passed**
      — includes the pre-existing `authStore.refreshRace` and
      `authStore.initialize` suites, which exercise the same store.
- [x] **`npx jest __tests__/auth.integration.ts __tests__/api-client-401-refresh.test.ts`
      (rider-app)** → **2 suites, 16 tests passed** (rider-side regression check).
- [x] **`npx tsc --noEmit` (driver-app)** → exit 0, clean.
- [x] **Real production bundle run** — `npx expo export --platform web`
      (driver-app) → **exit 0**, emitted a 5.2 MB production web bundle. This is
      the production Metro/babel pipeline, not a dev server and not
      `tsc --noEmit`. See the boundary note below for what it does not cover.
- [x] **Blast-radius grep performed** — `setTokens\(` across `shared`,
      `driver-app`, `rider-app` (5 call sites, tabulated in §4);
      `tokenExpiresAt|token_expires_at|fg_access_token|bg_access_token` across all
      four surfaces (readers tabulated in §4).
- [x] **Reviewed against `CLAUDE.md` conventions** — observability: the
      substitution warning is `error`-worthy information surfaced at `warn` with
      no token material and no PII; no money arithmetic; no state-machine
      transition; no RLS; no migration.
- [x] **Feature-flag decision justified** — see §8.

### What was NOT verified

- **The `shared` package's own `tsc --noEmit` exits non-zero (code 2), but not
  because of this change.** The only diagnostic is a pre-existing
  `tsconfig.json(16,9): error TS5101: Option 'baseUrl' is deprecated` — zero
  diagnostics mention `authStore`. Flagging as a standing gate-decay item per
  gate #8 rather than fixing it here (touching the shared `tsconfig` is out of
  scope for an auth fix and has its own blast radius).
- **`shared/api/__tests__/*` appears not to be run by any jest project.**
  `driver-app/jest.config.js` and `rider-app/jest.config.js` both default their
  root to their own directory, and CI runs `yarn test` per app. So
  `shared/api/__tests__/client.refresh.test.ts` may be orphaned. Not confirmed
  and not addressed here — it directly affects where subtask 3's regression test
  must live, and will be resolved there.
- **The production bundle that passed was the WEB target, not a native build.**
  `expo export --platform web` proves the production Metro/babel pipeline accepts
  the code, which is the meaningful signal for a pure-TypeScript logic change in
  `shared/`. It does **not** exercise Hermes bytecode generation or any native
  module. A real native build needs `[build]` in the commit message (EAS) and was
  not run.
- **No real-device confirmation** that proactive refresh actually fires on a
  physical driver phone, nor that headless location uploads recover. That needs
  an installed build against a deployed backend.
- **No load test of the increased refresh traffic** described in §4. The
  reasoning is arithmetic (≈1 refresh per client per 13 min vs a 20/min limit),
  not a measurement.
- **Root causes 2 and 3 remain open.** This does not by itself stop driver
  sign-outs and must not be reported as having done so.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — and explicitly notes it is NOT
      instant, unlike subtask 1
- [x] Blast radius is stated, not assumed — all 5 call sites and both downstream
      readers tabulated with file:line
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 covers the mid-session effect; §4 names the refresh-traffic
      step change as an expected consequence rather than leaving it to be
      discovered in production
