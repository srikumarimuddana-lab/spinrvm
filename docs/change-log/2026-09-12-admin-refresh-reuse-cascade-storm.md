# 2026-09-12 — refresh-token reuse: logout/refresh race no longer cascades; cascade victims no longer re-cascade

Surfaces: backend (auth), admin-dashboard (auth store + API client). Live-tested.
Incident: `admin-001`, 2026-09-12 03:19:57 and 04:05:17 UTC — the admin dashboard (live map included) lost its session twice in one evening. Sentry `CRIMSON-SMOKE-7445-82` (31 occurrences since June), `-81`, `-9`.

| Field | Entry |
|---|---|
| **Issue/gap identified** | Two cascades on one account in 45 minutes, each revoking every admin session. Neither was theft. |
| **Root cause** | (1) Dashboard: `authStore.logout()` fired `POST /api/admin/auth/logout` without awaiting it, and the API client's 401 give-up path set `window.location.href = "/login"` in the same tick. The /login page's bootstrap (`initAuth → silentRefresh`) ran with the HttpOnly refresh cookie still present and replayed the token logout had revoked 1.3 s earlier (`refresh_tokens.01cb1c6e`: revoked 03:19:56.37, replayed 03:19:57.4, `replaced_by = null`). (2) Backend: `_is_benign_rotation_replay` only excused replays of *rotated* rows (`replaced_by` set), so a replay of a logout-revoked row — however fresh — ran `_handle_refresh_token_reuse`, revoking `ae5a57a2`/`5d795566` (the live browser). (3) Backend: every session that cascade killed still held its cookie and replayed it when its 1-hour access token expired; `_reuse_already_handled` matched only the *replayed* row id from a prior cascade, not the rows that cascade revoked, so `dfc2e946` (a 03:19:57 victim) cascaded again at 04:05:17 and killed `e6b01458` — the session the founder had logged into at 03:21. Each dead session was a landmine for the next live one. |
| **Fix/remediation** | Backend `utils/refresh_tokens.py`: (a) new `REFRESH_REVOKE_RACE_GRACE_SECONDS = 60` — a non-rotated revoke replayed within 60 s is a client race: generic 401, warning log, no cascade (rotated revokes keep the 600 s grace unchanged); (b) the cascade records `cascade_revoked_row_ids` in its `audit_logs.details` (new `revoke_all_for_user_ids`; `revoke_all_for_user` still returns the count) and `_reuse_already_handled` recognises those ids, so a victim's replay is logged/alerted as a repeat, not cascaded. Dashboard: `logout()` returns the BFF call's promise (never rejects); `lib/api/client.ts` awaits it before the hard redirect so the cookie is gone before `/login` bootstraps. |
| **Risk & impact on existing functionality** | Blast radius, backend: `lookup_refresh_token` serves rider/driver/admin `/auth/refresh` and `/admin/auth/refresh` — the 60 s post-revoke window applies to mobile too (their logout-then-401-interceptor overlap has the same shape). `revoke_all_for_user` callers (`/auth/logout-all`, admin staff force-logout, MFA reset) unchanged: same int. `_reuse_already_handled` consumers: only `lookup_refresh_token`. Audit rows written before this change carry no id list → a victim of an older cascade still re-cascades once (errs toward one cascade, never a suppressed one). Security cost of (a): a stolen token replayed within 60 s of the victim's logout gets a 401 (it always did) but no longer kills the victim's *other* sessions/alerts at ERROR level — the attacker gains nothing from the request itself; a replay after 60 s still cascades. Security cost of (b): a holder of a cascade-revoked credential is only ever answered "401, already handled"; the sessions minted after that cascade were never theirs. Dashboard: `logout` type is now `() => Promise<void>` — all existing callers (`topbar.tsx` ×2, `client.ts`, the store's own failure paths) ignore or await the value; no caller relied on synchronous completion of the server call. |
| **User experience effect** | Admin: a tab that logs out no longer kicks the other tab/device out of the live map; a device kicked by a genuine cascade no longer kicks the re-login an hour later. Riders/drivers: a logout that overlaps a refresh no longer signs the same user out of every other device. No mid-session visible change; no copy change. |
| **Files modified** | `backend/utils/refresh_tokens.py` — race window, victim ids on the audit row, victim-aware dedupe — the two backend gaps. `backend/tests/test_refresh_token_reuse_detection.py` — 4 new tests (both incident timelines, window edge, count contract); existing cascade tests repointed at `revoke_all_for_user_ids`. `admin-dashboard/src/store/authStore.ts` — `logout()` returns the server call. `admin-dashboard/src/lib/api/client.ts` — await it before the hard redirect. `admin-dashboard/src/__tests__/lib/api-client-logout-before-redirect.test.ts` — new (2 tests). |
| **Before/after snippet** | See below. |
| **Rollback plan** | Backend: `git revert` + Fly deploy — no schema, no data migration; the extra `cascade_revoked_row_ids` key in existing audit rows is ignored by old code. Dashboard: revert + Vercel deploy; the `logout` return value is additive. Nothing to undo in live data. |
| **Verification performed** | Backend: `pytest` on the six files touching these functions — **199 passed** (`test_refresh_token_reuse_detection.py` 29, incl. the new ones; `test_logout_all.py`, `test_refresh_tokens_lifecycle.py`, `test_admin_staff_coverage.py`, `test_admin_staff_mfa_reset.py`, `test_routes_users_coverage.py`); `ruff check` + `ruff format --check` clean. Dashboard: `vitest` on the auth store, BFF refresh route, CSRF-retry and the new file — **29 passed**; `tsc --noEmit` clean; `eslint` 0 errors (1 pre-existing warning on the `window.location.href` line); `npm run build` — see PR. `spinr-security-auditor` pass on the diff — see PR. |
| **What was NOT verified** | Not reproduced against a live backend; the 1.3 s race was reconstructed from `refresh_tokens` + `audit_logs` rows, not observed in a browser. `topbar.tsx`'s `logout(); router.push("/login")` (client-side navigation, no bootstrap) was reasoned about, not exercised. The admin-dashboard visual-regression job is unaffected (no page changed) — stated, not screenshotted. Mobile clients' own logout/refresh overlap is covered by the backend window but was not traced in `shared/store/authStore.ts`. |

## Before / after

```python
# before — any non-rotated revoke replayed = theft
def _is_benign_rotation_replay(row):
    if not row.get("replaced_by"):
        return False
    ...
    return 0 <= age <= REFRESH_REUSE_GRACE_SECONDS

# after — the window depends on how the token died
window = REFRESH_REUSE_GRACE_SECONDS if row.get("replaced_by") else REFRESH_REVOKE_RACE_GRACE_SECONDS
return 0 <= age <= window
```

```python
# before — only the replayed row counted as "already handled"
if details.get("replayed_row_id") == row_id and details.get("cascade_ok") is True:
    return True

# after — so do the rows that cascade revoked
if details.get("replayed_row_id") == row_id: return True
if row_id in details.get("cascade_revoked_row_ids", []): return True
```

```ts
// before (client.ts) — cookie still present when /login bootstraps
useAuthStore.getState().logout();
window.location.href = "/login";

// after
await useAuthStore.getState().logout();
window.location.href = "/login";
```
