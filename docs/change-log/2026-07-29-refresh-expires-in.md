# Change Impact & Risk Log — `/auth/refresh` missing `expires_in`

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session: driver sign-out investigation) |
| Surface(s) | backend (effect lands on driver-app + rider-app; corporate portal in blast radius, unaffected) |
| Domain (Sentry tag) | `auth` |
| PR / commit link | _(pending — subtask 1 of 6)_ |
| Related issue or gap ID | Driver-app frequent sign-out, root cause 1 of 3 |

## 1. Issue / gap identified

Drivers are signed out of the driver app frequently while riders stay signed in for
weeks. Reported by the product owner during live app testing.

## 2. Root cause

`POST /auth/refresh` returned `access_expires_at` / `refresh_expires_at` but **not**
`expires_in`. The mobile clients read `expires_in`:

- `shared/store/authStore.ts:291` destructures `expires_in` from the refresh response.
- `setTokens()` (`authStore.ts:241`) computes `Date.now() + expiresIn * 1000`.

With the field absent that arithmetic produced `NaN`, so after the **first** refresh of
any session:

1. `tokenExpiresAt` became `NaN`. `ensureFreshToken()` bails on
   `if (!token || !tokenExpiresAt) return` (`shared/api/client.ts:170`) — `!NaN` is
   `true`. The 60-second timer and the AppState-resume hook
   (`driver-app/app/_layout.tsx:482-492`) therefore became no-ops, and the proactive
   2-minute-before-expiry refresh **never ran again for the life of the session**.
   Every subsequent expiry was discovered reactively, as a burst of 401s.
2. The value is persisted as SecureStore `token_expires_at`, which the driver app's
   headless location task parses to authorise batch uploads
   (`driver-app/utils/backgroundLocation.ts:154-158`). `parseInt("NaN")` → the freshness
   comparison fails → `getBackgroundAuthToken()` returned `null`, silently disabling
   headless location-batch uploads ~15 minutes after every login. Billed distance and
   the SGI insurance-period audit are settled from those breadcrumbs.

The login path was unaffected because `AuthResponse` already carries `expires_in`
(`backend/routes/auth.py:280`) and the OTP screens default it (`otp.tsx:170` →
`expires_in ?? 900`). Only the refresh path passed `undefined` straight through.

Why CI never caught it: the client tests mock the shape the *client expects*, not the
shape the *server sends* — `driver-app/__tests__/store/authStore.refreshRace.test.ts:130`
mocks `expires_in: 900`. A prior fix in this same area
(`test_access_expires_at_uses_minutes_ttl_not_legacy_days`) corrected
`access_expires_at`, which no mobile client reads.

This is root cause 1 of 3. It does not by itself sign a driver out — it removes the
quiet refresh, which turns every expiry into a 401 burst, and the burst is what root
cause 2 (`shared/api/client.ts` G2 backstop, subtask 3) converts into a hard logout.

## 3. Fix / remediation

Add `expires_in` (access-token TTL in seconds) to `RefreshResponse`, populated from
`settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60` — identical to how `AuthResponse` already
does it. Purely additive.

**This repairs every driver binary already in the field with no app release.** The
shipped client code already reads `expires_in`; the server simply never sent it. On
their next refresh, existing installs start computing a correct `tokenExpiresAt` and
proactive refresh resumes.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface (three mounts of one router), but additive-only.**

`auth_router` is mounted three times in `backend/server.py`:

| Mount | Line | Consumer |
|---|---|---|
| `/api/v1` | `server.py:352` | rider-app + driver-app via `shared/api/client.ts` |
| `/api` | `server.py:353` | legacy path, same handler |
| `/api/portal` | `server.py:358` | corporate company portal |

Every reader of this response, enumerated:

- **`shared/store/authStore.ts::refreshTokens`** — reads `expires_in`. This is the
  intended fix; behaviour changes from `NaN` to a correct number.
- **`driver-app/utils/backgroundLocation.ts::getBackgroundAuthToken`** — reads the
  persisted `token_expires_at` derived from this value. Behaviour changes from
  permanently-null to working. Hardening of its `NaN` handling is subtask 4.
- **`admin-dashboard/src/store/companyAuthStore.ts::silentRefresh`** — reaches this
  handler via the Next BFF `/api/company-auth/refresh`, which proxies to
  `/api/portal/auth/refresh`. It reads only `data.token` / `data.csrf_token`
  (`companyAuthStore.ts:121-129`). The BFF forwards unknown fields through its
  `...clientData` spread (`api/company-auth/refresh/route.ts:49`), so `expires_in`
  reaches the browser and is ignored. **No corporate behaviour change.**
- **Internal admin auth is NOT affected** — it uses a separate router
  (`admin_auth_router`, `server.py:387`) and returns plain dicts, not `RefreshResponse`.

Construction sites of `RefreshResponse`: exactly one (`auth.py:1633`), plus one
`response_model=` reference (`auth.py:1517`). Verified by grep, so adding a required
field cannot break another caller.

No interaction with the 16 background loops, the ride state machine, money/wallet
deltas, RLS policies, or any migration. No new PII in the payload (an integer TTL).

Residual risk: a client that *validates* the response against a strict schema and
rejects unknown fields would break. None of the four consumers above does — all use
destructuring or a spread.

## 5. User-experience effect

- **Driver:** the visible effect is fewer sign-outs, but this fix alone does not close
  the sign-out path — it restores quiet background refresh so the 401 bursts stop
  happening. Subtask 3 closes the logout itself.
- **Driver (background):** headless location-batch uploads resume. Trip breadcrumbs
  that were being deferred into SQLite start flowing again, so billed distance and
  insurance-period records regain their expected sample density.
- **Rider:** no perceptible change (riders rarely reached the reactive path).
- **Corporate admin:** none.
- **Internal admin:** none.
- **Visible mid-session:** yes, and deliberately so — a driver who is currently online
  gets the corrected expiry on their next refresh without restarting the app. No UI,
  copy, or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Added `expires_in: int` to `RefreshResponse` (+ comment explaining the client contract); populated it in the single construction site | The field the mobile clients actually read was missing, making their expiry arithmetic `NaN` |
| `backend/tests/test_p1_token_refresh.py` | Added `test_response_includes_expires_in_seconds` — asserts the field equals the TTL in seconds and agrees with `access_expires_at` within 5s | Pins the contract against the field the client reads, not just the one it ignores |

## 7. Before / after

```python
# Before — backend/routes/auth.py
class RefreshResponse(BaseModel):
    token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime
    csrf_token: Optional[str] = None

    return RefreshResponse(
        token=token,
        refresh_token=new_raw,
        access_expires_at=access_expires_at,
        ...
```

```python
# After
class RefreshResponse(BaseModel):
    token: str
    refresh_token: str
    expires_in: int          # access-token lifetime in seconds (what clients read)
    access_expires_at: datetime
    refresh_expires_at: datetime
    csrf_token: Optional[str] = None

    return RefreshResponse(
        token=token,
        refresh_token=new_raw,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        access_expires_at=access_expires_at,
        ...
```

Client-side consequence, unchanged code:

```ts
// shared/store/authStore.ts:241 — same line, before and after
const expiresAt = Date.now() + expiresIn * 1000;
// before: expiresIn === undefined  → expiresAt === NaN
// after:  expiresIn === 900        → expiresAt === now + 15 min
```

## 8. Rollback plan

Revert the two-line diff in `backend/routes/auth.py` and redeploy. No feature flag, no
`app_settings` value, no migration rollback SQL — nothing is written to the database and
no schema changed.

A redeploy is the only path, which is acceptable here because the change is a single
additive response field with no persisted side effects: on rollback the field simply
disappears and clients return to the pre-existing `NaN` behaviour — the status quo, not
a corrupted state. No live data (Stripe charges, wallet deltas, ride state,
insurance-period rows) is touched, so a code revert *is* a complete rollback.

**Not feature-flagged** (gate #3), deliberately: the `app_settings` flag mechanism is
read over an authenticated HTTP call, so gating the auth-token-refresh path behind it
introduces a circular dependency — a flag read that needs a valid token to decide
whether tokens refresh correctly. Adding an unread field to a response also cannot be
partially rolled out per-user in any meaningful way. Stated here rather than silently
skipped.

## 9. Verification performed

- [x] **Automated tests (unit)** — `pytest backend/tests/test_p1_token_refresh.py -v`
      → **7 passed**. New test confirmed RED before the fix
      (`AttributeError: 'RefreshResponse' object has no attribute 'expires_in'`) and
      GREEN after.
- [x] **Regression suite** — `pytest backend/tests/test_refresh_tokens_lifecycle.py`
      → **26 passed**.
- [x] **Regression suite** — `pytest backend/tests/test_refresh_token_reuse_detection.py`
      `backend/tests/test_appcheck_portal_exempt.py` → **21 passed**.
- [x] **Lint/format** — `ruff check` and `ruff format --check` on both changed files:
      clean.
- [x] **Blast-radius grep performed** — searched for: `RefreshResponse` (all `.py`),
      `expires_in` (all `.py`), `auth/refresh` (all `.ts`/`.tsx` across `shared`,
      `driver-app`, `rider-app`, `admin-dashboard`), `auth_router` in `server.py`,
      and `tokenExpiresAt|token_expires_at|fg_access_token|bg_access_token` across all
      four surfaces. Results enumerated in §4.
- [x] **Reviewed against `CLAUDE.md` conventions** — observability (no new logging;
      no PII added to the payload — an integer TTL only); no money arithmetic, no state
      machine transition, no RLS policy, no migration. Dual-import pattern untouched.
- [x] **Feature-flag decision justified** — see §8.

### What was NOT verified

- **No staging or production deploy.** Not exercised against live Supabase; the tests
  mock `lookup_refresh_token`, `db.find_one`, and `issue_refresh_token`.
- **The full backend suite was not run** — only the four auth/refresh files above.
  Per a known local issue, async tests in this repo fail in bulk runs but pass in
  isolation (pytest-asyncio/anyio conflict), so a full-suite run here would produce
  noise unrelated to this diff. CI is the authority on the whole suite.
- **No real-device confirmation** that a driver's proactive refresh actually resumes
  and that headless location uploads recover. That needs an installed build against a
  deployed backend; it is the one check that would prove the user-facing claim in §5.
- **No production build of any app surface was run** — correctly, since this diff
  contains zero client files. Subtasks 2–4 touch `shared/` and `driver-app/` and will
  need one.
- **No load/performance check.** The change adds one integer to a response body; the
  handler does no additional I/O.
- **Root causes 2 and 3 remain open.** This subtask alone is not expected to eliminate
  driver sign-outs, and should not be reported as having done so.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — all four consumers of the response
      enumerated with file:line, plus confirmation that only one construction site exists
- [x] No silent behavior change to an already-shipped flow without the UX field filled
      in — §5 states the mid-session effect explicitly, including that shipped binaries
      change behaviour with no app release
