# Change Impact & Risk — an unreadable keychain must not be treated as "no session"

**Date:** 2026-09-13
**Surfaces:** rider-app, driver-app (live-tested) — via `shared/api/client.ts`
**Related:** F2 in `docs/audit/2026-09-13-driver-app-mid-ride-process-death.md`; the SPR-T9NYPB guard in
`docs/change-log/2026-09-11-ride-t9nypb-hardening.md`

## Issue/gap identified

`shared/api/client.ts`'s pre-init 401 guard exists to stop a 401 arriving *before*
`authStore.initialize()` from wiping the stored refresh token — the SPR-T9NYPB failure of 2026-09-11.
The guard defeated itself: on a **locked keychain** it took the very branch it was added to prevent.

## Root cause

`hasStoredRefreshToken()` collapsed a read *exception* into `false`:

```ts
try { return !!(await SecureStore.getItemAsync('refresh_token')); }
catch { return false; }
```

`expo-secure-store` throws — rather than returning null — for any OSStatus except `errSecItemNotFound`,
notably **while the keychain is locked**, which is exactly the state a backgrounded app hits. `false` sent
control to the `else` branch at `client.ts:1175`, which calls `logout()` and deletes the refresh token
that *is* the whole session.

The file also contradicted itself. The function's docstring called clearing "the conservative (clear)
path", while the caller's own comment (`client.ts:1162-1171`) establishes that clearing is the
**destructive** path — it is the documented cause of SPR-T9NYPB.

## Fix/remediation

Report a read failure as "may exist" and defer the decision to `initialize()`. Renamed to
`mayHaveStoredRefreshToken()` so the name states the tri-state honestly rather than implying a definite
answer it cannot give.

## Before/after

```diff
- async function hasStoredRefreshToken(): Promise<boolean> {
+ async function mayHaveStoredRefreshToken(): Promise<boolean> {
    if (Platform.OS === 'web') return false;
    try {
      const SecureStore = require('expo-secure-store');
      return !!(await SecureStore.getItemAsync('refresh_token'));
    } catch {
-     return false;
+     // Unreadable !== absent. Defer the decision rather than wipe the session.
+     return true;
    }
  }
```

Safe in both directions: if a token really is on disk the session is preserved and `initialize()` decides;
if there is no token at all there is no session to lose, and `initialize()` reaches the same logged-out
state one beat later.

## Risk & impact on existing functionality

Blast radius is **one call site**. `mayHaveStoredRefreshToken()` is called exactly once
(`client.ts:1172`), inside `if (!_refreshCallback && …)` — reachable only when a 401 arrives before
`authStore.initialize()` has registered a refresh callback.

- **Web is untouched** — the `Platform.OS === 'web'` early return is unchanged, so web keeps its
  clear-and-logout behaviour (its token is an HttpOnly cookie the client cannot read).
- **The non-throwing paths are unchanged** — a present token still returns `true`, an absent token still
  returns `false`.
- Only the `catch` branch differs, and only on native.
- The other two `logout()` invocations in this file (`:977`, `:1183`) are unchanged; both are already
  inside `try`/`catch` or un-awaited, so neither is affected.
- No other module imports this function — it is file-local, not exported.

Renaming touched both references in the one file. The old name survives only in
`docs/change-log/2026-09-11-ride-t9nypb-hardening.md`, an append-only historical record that correctly
describes what was true then; it was deliberately **not** edited.

## User experience effect

Visible only in the failure case, and strictly an improvement: a rider or driver whose keychain is locked
when a pre-init 401 arrives keeps their session instead of being dropped to the OTP screen. No change on
any success path, so nothing changes for a user mid-session under normal conditions.

## Files modified

| File | What changed | Why |
|---|---|---|
| `shared/api/client.ts` | `hasStoredRefreshToken` → `mayHaveStoredRefreshToken`; read failure now returns `true`; docstring corrected | A throw is not evidence of "no token"; the old behaviour reproduced SPR-T9NYPB / F2 |
| `shared/api/__tests__/client.refresh.test.ts` | +2 tests (token-on-disk, and keychain-read-throws) | The guard had **no native coverage at all** — see below |

## Rollback plan

Revert the `catch` to `return false` (one line). Code-only, no persisted state, no schema, no server
behaviour — a revert restores prior behaviour exactly with nothing to reconcile. No feature flag: the
change only executes on a path that is currently broken, so flagging would mean choosing between "broken"
and "fixed".

## Verification performed

- `shared/api/__tests__/client.refresh.test.ts` — **16/16 pass**.
- Full related set (`client.refresh`, `client.sos`, rider `api-client-401-refresh`) — **3 suites, 30/30**.
- **Red/green proof:** with the fix reverted to `return false`, the new keychain-throws test **fails**
  (1 failed / 15 passed) and the token-on-disk test still passes — so the new test genuinely catches this
  bug rather than passing vacuously.
- `npx tsc --noEmit` — clean, exit 0, both apps.
- driver-app `__tests__/store` — 38/38 pass.

### Finding surfaced while testing

`client.refresh.test.ts` pins `Platform.OS = 'web'` at module scope (line 24), where this guard is inert
by design. **The SPR-T9NYPB guard therefore had no native test coverage** — the pre-existing
"DOES force a logout on a 401 when no refresh path exists" test passes via the web short-circuit, not via
the native logic. The two new tests flip `Platform.OS` to `'ios'` and restore it in a `finally`, and are
the first native coverage of this branch.

Separately: **driver-app's jest maps `@shared/api/client` to a mock** (`jest.config.js:86`), so no
driver-app test exercises this file at all. Native coverage here comes only from rider-app's runner.

## What was NOT verified

- **No production build was run** for either app — only `tsc --noEmit` and jest. CLAUDE.md treats those as
  not equivalent.
- **Not reproduced on a physical device with a genuinely locked keychain.** The throwing `getItemAsync` is
  a jest mock; the real OSStatus behaviour is taken from `expo-secure-store`'s iOS source
  (`SecureStoreModule.swift` throws for any status except `errSecItemNotFound`), not from an observed
  device repro.
- **The incident causality remains unproven.** This removes one documented way F2's symptom can occur; it
  is not established that this specific path is what happened on 2026-09-13.
- Android's `SecureStore` failure modes were not separately verified; the fix is platform-agnostic on the
  native side, but only iOS's throw contract was read.
- rider-app/driver-app have no visual-regression tooling; visual impact (expected nil) was reasoned about,
  not screenshotted.
