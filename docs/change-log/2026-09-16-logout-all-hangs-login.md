# Change Impact & Risk Log — sign out of all devices never reached login

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-16 |
| Author | Cursor Grok 4.6 |
| Surface(s) | backend, driver-app, rider-app (shared authStore) |
| Domain (Sentry tag) | auth, drivers |
| PR / commit link | |
| Related issue or gap ID | Live testing: driver-app "Sign out of all devices" stayed on the signed-in screen until the app was force-closed; sign-out also felt slow |

## 1. Issue / gap identified

Tapping **Sign out of all devices** on the driver profile revoked the session on the server but did not navigate to login. The driver had to kill and reopen the app. The same flow was also slow because it waited on two sequential authenticated calls, the second of which could no longer authenticate.

## 2. Root cause

`logoutAll()` POSTs `/auth/logout-all` (bumps `users.token_version`, revokes every refresh token), then always calls `logout({ revokeServerSession: false })`. That flag skipped `POST /auth/logout` but **not** `PUT /drivers/{id}/status` `{ is_online: false }`.

The go-offline PUT used a token the previous call had just killed. The API interceptor treated the 401 as "refresh then retry", the refresh token was already revoked, and the request sat in the refresh queue. `handleLogoutAll`'s `finally { router.replace('/login') }` never ran until that hang ended — often not until process death. On the next cold start, tokens were already gone, so login appeared.

Regular **Sign Out** was slow for a related reason: it awaited go-offline, then `POST /auth/logout`, sequentially.

**Alternative considered:** navigate to `/login` immediately and leave the extra PUT as-is (symptom). Rejected — the hang would still leave the driver "online" in DB/dispatch until presence TTL, and a 401 refresh storm is still a live session bug. Folding go-offline into `/auth/logout-all` and skipping the dead-token PUT is the smaller, correct merge.

## 3. Fix / remediation

1. **Client (`authStore.logout`)** — skip go-offline *and* `POST /auth/logout` when `revokeServerSession: false` (credential already dead). For a live credential, start go-offline and `POST /auth/logout` in parallel instead of one after the other.
2. **Server (`POST /auth/logout-all`)** — after the token kill, best-effort take an idle driver row offline (`is_online`/`is_available` false, Period 0, presence clear) in the same request. Skip the flip when the driver is on an obligated ride (`driver_assigned` / `driver_accepted` / `driver_arrived` / `in_progress`) so insurance stays Period 2/3. A driver-row failure must not 500 the logout.

## 4. Risk & impact on existing functionality

Blast radius: **cross-surface** (shared `authStore` + `/auth/logout-all`).

**Callers of `logout({ revokeServerSession: false })`** — these already skip `POST /auth/logout`. They now also skip go-offline (which could not succeed with a dead token anyway):

- `authStore.logoutAll` (driver profile "Sign out of all devices")
- `authStore.refreshTokens` interceptor/backstop paths
- `shared/api/client.ts` 401 refresh-rejected fallback

**Callers of `logout()` with a live token** (unchanged contract, faster): driver profile Sign Out, driver `index.tsx` / `become-driver.tsx`, rider account/privacy/profile-setup. Go-offline and `POST /auth/logout` still both fire; they overlap instead of serializing.

**`POST /auth/logout-all` readers/writers:**

- Token kill: `users.token_version`, `users.sessions_invalid_before`, `revoke_all_for_user`, WS `kick_user(reason=logout_all)` — unchanged and still happen first.
- New: `drivers` row (`is_online`, `is_available`, `went_offline_at`, `last_status_changed_at`), `driver_insurance_periods` append (Period 0), Redis presence + H3 index via `clear_presence`.
- Same tables/fields as `PUT /drivers/{id}/status` go-offline and `force_offline_if_exhausted` / account-deletion tombstone.

**Ride state machine:** we do **not** flip offline during an obligated ride (same 409 list as the status endpoint). Sessions still die; stuck-ride sweeper / existing cancel paths remain the ride-resolution path. No money/wallet deltas. No background-loop registry change.

**Rider-only `/auth/logout-all`:** helper no-ops when there is no driver row.

**Admin `/admin/auth/logout-all`:** untouched.

**Insurance:** Period 0 is recorded only when we actually flip an idle/online driver offline. An in-progress driver is left Period 3 (or 2) even though their app session is dead — matching the status-endpoint rule that going offline mid-obligation would misstate SGI coverage.

**Presence:** idle drivers drop from dispatch/admin map immediately instead of waiting for the ~30s presence TTL. Obligated drivers keep presence until WS kick / heartbeat (same as today's 409 path).

Could this regress regular Sign Out? Only if overlapping the two calls caused `/auth/logout` to run without a live token. Both start while the token is still in memory; go-offline no longer blocks the logout POST. The session lock still serializes the local wipe against token publish.

## 5. User-experience effect

- **Driver (this device):** Sign out of all devices should return to the login screen without killing the app. Both Sign Out and Sign out of all devices should feel like one round trip, not two.
- **Driver (other devices):** already kicked via WS `session_revoked` + token_version. New: if they were idle-online, they now go offline in DB immediately (admin map / dispatch), which is what "sign out everywhere" implies.
- **Rider using the same `logoutAll` store method:** no driver row → no extra write. Local session still ends.
- **Mid-ride driver who taps sign-out-all:** still signed out of the app; insurance period stays on the ride; they cannot complete the trip in-app (pre-existing — sessions are revoked). Visible mid-session: yes, they asked to end every session.
- No copy/notification change.
- rider-app / driver-app have no visual regression tooling; this is navigation + latency, reasoned about against the existing `router.replace('/login')` finally, not screenshotted.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `shared/store/authStore.ts` | Skip go-offline on dead credentials; parallelize live go-offline + `/auth/logout` | Stop the 401 hang; one wait instead of two |
| `backend/routes/auth.py` | `_offline_driver_for_logout_all` after token kill | Merge the second client call into the first |
| `driver-app/__tests__/store/authStore.initialize.test.ts` | logoutAll / dead-token / parallel-logout cases | Pin the hang and the merge |
| `backend/tests/test_logout_all.py` | Idle offline, obligated skip, write-failure still 200 | Pin insurance + fail-open |
| `docs/change-log/2026-09-16-logout-all-hangs-login.md` | This log | Live-tested auth/driver surface |

## 7. Before / after

```
# Before (client)
await POST /auth/logout-all          # kills token_version + refresh tokens
await PUT /drivers/{id}/status       # 401 → refresh hang → login never shows
await local wipe
router.replace('/login')             # only if the hang ever resolved
```

```
# After (client)
await POST /auth/logout-all          # also takes idle driver offline server-side
await local wipe                     # no go-offline PUT, no /auth/logout
router.replace('/login')
```

Regular Sign Out: `PUT go-offline` and `POST /auth/logout` start together, then local wipe.

## 8. Rollback plan

No flag, no migration. Redeploy previous backend + client.

- **Backend-only rollback:** client still skips the dead-token PUT, so login navigation stays fixed; idle drivers stay "online" in DB until presence TTL (~30s) after logout-all. Acceptable degraded state.
- **Client-only rollback:** reintroduces the hang against a backend that already does go-offline (redundant PUT 401s again). Do not ship client revert without backend revert.
- No live money/ride-row mutation to reverse. Insurance Period 0 rows already appended are append-only — do not delete; a later go-online opens Period 1 as usual.

## 9. Verification performed

- [x] Automated tests: `driver-app` `authStore.initialize.test.ts` (logout/logoutAll block); `backend/tests/test_logout_all.py` (`TestLogoutAllRiderDriver`)
- [ ] Manual repro on a device (force-close no longer required) — not run in this session
- [x] Blast-radius grep: `logoutAll`, `revokeServerSession`, `logout(` in shared/driver-app/rider-app; `/auth/logout-all`; `record_period_transition` / `clear_presence` go-offline callers
- [x] Conventions: insurance Period 0 only when not obligated; `is_available ⇒ is_online` held (both false); logout-all still fails 500 if token_version bump fails; driver-offline errors logged, not swallowed silently (`logger.error` + `exc_info`)
- [x] Not feature-flagged: this is a hang/bugfix on an existing button, not new UX. A flag-off would re-leave drivers stuck on the signed-in screen.

## 10. Sign-off

- [x] Rollback plan is concrete (redeploy; backend-only revert is safe-degraded)
- [x] Blast radius is cross-surface (shared store + rider/driver logout-all)
- [x] UX field filled: login navigation + faster sign-out; mid-session only if they tapped the button
