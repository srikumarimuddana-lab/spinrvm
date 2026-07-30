# Change Impact & Risk Log — Push token detached on logout

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-30 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend, rider-app, driver-app |
| Domain (Sentry tag) | auth |
| PR / commit link | `1e38532` (backend, dark), `15eed2d` (apps) |
| Related issue or gap ID | User report: push notifications arrive whether logged in or out |

## 1. Issue / gap identified

Signing out did not stop server-driven push notifications. A user who logged out
kept receiving onboarding reminders and any other informational push on that
device.

## 2. Root cause

`POST /auth/logout` (`backend/routes/auth.py:1638`) revoked the refresh token,
deleted the Redis session key, tombstoned the session and cleared cookies — but
never touched `users.fcm_token`, `users.fcm_token_rider` or
`users.fcm_token_driver`. `send_push_notification` (`backend/features.py:1653`)
reads those columns directly, so delivery is independent of session state. The
only place tokens were ever purged was account deletion
(`backend/features.py:1508`).

A second, latent defect made the obvious fix unsafe: both apps register their
FCM token inside a `useEffect` guarded by `fcmRegisteredRef.current`
(`driver-app/app/_layout.tsx:420`, `rider-app/app/_layout.tsx:379`). That is a
`useRef`, never reset on sign-out, so a user who signed out and back in without
killing the app never re-registered.

## 3. Fix / remediation

Two commits, deliberately sequenced:

1. **Apps (`15eed2d`, ships now).** Reset `fcmRegisteredRef` when `authToken`
   clears, so the next sign-in re-registers the token. Harmless standalone.
2. **Backend (`1e38532`, ships dark).** `_clear_push_token_on_logout` nulls the
   per-app token column on logout, gated on the `logout_clears_push_token`
   `app_settings` flag, **default off**.

The flag stays off until an EAS build containing (1) has rolled out.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (backend + both mobile apps).**

Readers of the `users.fcm_token*` columns, all grepped:

| Consumer | File | Effect when a token is nulled |
|---|---|---|
| `send_push_notification` | `backend/features.py:1653` | Returns falsy; informational pushes are dropped |
| Push retry loop | `backend/utils/push_retry.py:127` | Re-reads the user at send time; a nulled token means the queued push cannot deliver |
| Admin test push | `backend/routes/notifications.py:170` | Reports "No fcm_token_driver or fcm_token on file" |
| DSAR export column list | `backend/routes/drivers/tax_exports.py:275` | Exports `null` instead of a token — strictly better for PIPEDA |
| Token registration | `backend/routes/notifications.py:329` | Rewrites the column on next registration |

**The material risk is dispatch.** `send_push_notification` with
`priority="dispatch"` is how a driver learns about a ride offer, and an offer
expires in ~15s. If the token is cleared while an installed binary cannot
re-register, that driver silently receives no offers. This is exactly why the
backend half ships dark — the flag must not be enabled before the app build lands.

Dual-role users: the helper mirrors `register-token`'s `client_type` inference,
so a driver-app logout clears `fcm_token_driver` only. The legacy generic
`fcm_token` is nulled **only** when it still holds the same value as the column
being cleared, so a driver logout cannot kill the rider app's pushes.

`push_tokens` (the canonical multi-device table) is deliberately **not** touched
— only the `users` shortcut columns that the delivery path reads.

## 5. User-experience effect

- **Rider- and driver-facing, once the flag is enabled.** A signed-out device
  stops receiving pushes. That is the intended behaviour and what a user expects.
- Not visible mid-session while the flag is off — today the backend change is a
  no-op on every request.
- The app-side change is invisible: it only re-runs a registration call that
  already existed.
- No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Added `client_type` to `LogoutRequest`, `_push_token_columns_to_clear`, `_clear_push_token_on_logout`; called from `logout` | Detach the signed-out device's token, flag-gated |
| `backend/tests/test_logout_push_token_clear.py` | New — 11 tests covering column selection, dual-role isolation, flag gating, failure paths | Regression cover |
| `driver-app/app/_layout.tsx` | Reset `fcmRegisteredRef` when `authToken` clears | Prerequisite for enabling the flag |
| `rider-app/app/_layout.tsx` | Same | Same |

## 7. Before / after

```python
# Before — logout leaves the push token in place
if current_user:
    await redis_delete(f"session:{current_user['id']}")
    if should_tombstone(...):
        await revoke_session(str(token_session_id))
```

```python
# After — flag-gated detach (no-op while logout_clears_push_token is off)
if current_user:
    await redis_delete(f"session:{current_user['id']}")
    await _clear_push_token_on_logout(current_user, body.client_type if body else None)
    if should_tombstone(...):
        await revoke_session(str(token_session_id))
```

```tsx
// Before — ref never reset, so re-login never re-registers
useEffect(() => {
  if (!isAuthInitialized || !authToken || fcmRegisteredRef.current) return;
  ...
}, [isAuthInitialized, authToken]);
```

```tsx
// After
useEffect(() => {
  if (!authToken) fcmRegisteredRef.current = false;
}, [authToken]);
```

## 8. Rollback plan

**Backend: config-only, no redeploy.** Set `logout_clears_push_token` to `false`
in `app_settings`. The code path returns before any write. This is also the
*current* state — the change is already shipped in its rolled-back position.

Recovery if the flag is enabled prematurely: flip it off, then affected users
re-register their token on next app launch (cold start always re-runs
registration). No data-level remediation is needed — a nulled token is
self-healing on the next `POST /notifications/register-token`. **However**,
drivers who logged out and back in during the window would have missed ride
offers in the interim; that is unrecoverable, which is why the flag ships off.

**Apps:** the `_layout.tsx` change is inert without the backend flag; reverting
needs an EAS build, but there is no scenario where it needs reverting on its own.

## 9. Verification performed

- [x] Automated tests — `pytest tests/test_logout_push_token_clear.py`
      **11 passed** (unit; `AsyncMock`-patched settings + DB)
- [x] `npx tsc --noEmit` clean on **both** `driver-app` and `rider-app`
- [ ] Manual repro in staging — **not done**, see §10
- [x] Blast-radius grep — `fcm_token_driver`, `fcm_token`, `register-token`,
      `fcmRegisteredRef` across `backend/`, `driver-app/`, `rider-app/`, `shared/`
- [x] Reviewed against `CLAUDE.md` — no silent error swallowing (failures logged
      at error level; the logout itself still succeeds because the session is
      already revoked by that point), no PII in logs (user_id + column names only)
- [x] Feature-flagged, shipped dark, per the ship-dark-then-flip gate

## 10. What was NOT verified

- **No production build run** (`eas build` / `npm run build`) for either app —
  only `tsc --noEmit`. Per `CLAUDE.md` that is explicitly *not* equivalent to a
  real production build. A build is required before this ships to devices.
- **Not tested on a real device.** The re-registration behaviour after
  sign-out → sign-in was reasoned about from the `useRef` semantics and the
  effect's dependency array, not observed. This is the single most important
  thing to check on device before enabling the backend flag.
- **Not tested against live Supabase** — the `users` update is asserted against
  an `AsyncMock`, not a real PostgREST round-trip.
- **The end-to-end path was not exercised** — no test drives
  `POST /auth/logout` through the router with a real `current_user` dependency;
  coverage is at the helper level. The call site itself is one line and was
  reviewed by reading, not executed in a test.
- **No visual/snapshot regression tooling exists** for either app, so the
  `_layout.tsx` change was reasoned about rather than visually verified. This is
  a standing repo-wide gap, not specific to this change.
- **Older clients that never send `client_type`** fall back to role-flag
  inference; for a dual-role user that clears both per-app columns. Not
  separately validated against a real dual-role account.
