# Change Impact & Risk Log — admin API client sends a stale CSRF token on 401 retry

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-04 |
| Author | Claude Code (session with @srikumarimuddana) |
| Surface(s) | admin-dashboard, backend (log formatting only) |
| Domain (Sentry tag) | auth / admin |
| PR / commit link | PR #3433, branch `claude/missing-regulator-removal-date-column-o5sihd` |
| Related issue or gap ID | operator report: `PUT /api/admin/drivers/<id> → 403`, `CSRF token mismatch` |

## 1. Issue / gap identified

An admin editing a driver got a silent failure:

```
18:02:28 WARNING core.middleware:dispatch:502 | CSRF token mismatch: %s %s origin=%s
18:02:28 "PUT /api/admin/drivers/373bc278-… HTTP/1.1" 403 Forbidden
```

Two distinct defects in one line:

1. **The 403** — the double-submit check rejected a write from a legitimately
   signed-in admin.
2. **The log is useless** — `core/middleware.py` uses loguru, which formats with
   `str.format` (`{}`), not `%`-style. `logger.warning("… %s %s origin=%s", method, path, origin)`
   emits the placeholders verbatim and silently drops all three arguments, so the one record
   that should identify the failing request identifies nothing.

## 2. Root cause

**The 403:** `admin-dashboard/src/lib/api/client.ts` retries once after a 401 by calling
`store.silentRefresh()`. The refresh rotates *both* the access token and the CSRF token —
`authStore.ts:230` sets `{ token, csrfToken: data.csrf_token }` and the backend sets a new
`csrf_token` cookie on the same response. The retry rebuilt only the `Authorization` header:

```ts
const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
```

`...headers` carries the **pre-refresh** `X-CSRF-Token`, while the browser now sends the
**post-refresh** cookie. `CSRFMiddleware` compares them, they differ, 403.

So the failure is conditional on timing: it fires only when the access token happens to expire
on a state-changing request (admin access tokens are 1 h). Safe methods are unaffected — CSRF
is not enforced on GET — which is why the dashboard looks healthy right up until a save fails.

`admin-dashboard/src/lib/companyApi.ts:47-58` already carries exactly this fix, with a comment
describing the same failure mode for the company portal. The admin client never received it.

**The log:** `core/middleware.py` does `from loguru import logger` (line 8). Loguru applies
`str.format`; a `%s` is not a format field, so `"… %s".format(method)` returns the string
unchanged and no error is raised. Verified directly:

```
>>> logger.warning("CSRF token mismatch: %s %s origin=%s", "PUT", "/p", "https://a.b")
CSRF token mismatch: %s %s origin=%s
```

## 3. Fix / remediation

- `client.ts`: rebuild **both** auth headers from the refreshed store on the retry, mirroring
  `companyApi.ts`. The CSRF header is attached only for non-safe methods, as on the first attempt.
- `core/middleware.py`: convert both CSRF rejection warnings to loguru `{}` fields, and add the
  fields that actually aid diagnosis — whether header/cookies were present (missing case) and
  the count of CSRF cookies presented (mismatch case).

Token **values** are deliberately not logged in either branch. They are session credentials;
a count distinguishes "no cookie at all" from "sent the wrong one of the two per-audience
cookies", which is the distinction that needed making, without putting a credential in a sink.

## 4. Risk & impact on existing functionality

**Blast radius: `client.ts` is the shared transport for the whole admin dashboard —
cross-surface within admin, but the changed code is reachable only on the 401-retry path.**

`src/lib/api/client.ts` exports `request()`, imported by all 23 modules under `src/lib/api/`
(drivers, rides, corporate, payouts, pricing, safety-disputes, settings, data-transfer, …).
That is a wide import surface, which is why the change is confined to the block already inside
`if (res.status === 401)` and `if (newToken)`:

- **First-attempt requests: byte-identical.** Untouched code path, so every call that succeeds
  today keeps the exact same headers.
- **GET/HEAD/OPTIONS retries:** now explicitly *omit* `X-CSRF-Token` rather than carrying the
  stale value. The backend never reads it for safe methods (`_CSRF_SAFE_METHODS` short-circuits
  before any validation), so this is inert either way — covered by a test to keep it that way.
- **Caller-supplied headers survive** — `...headers` is still spread first; only `Authorization`
  and `X-CSRF-Token` are overwritten. Tested (the forced-MFA-enrollment flow passes its own
  `Authorization`, but it also sets `callerProvidedAuth`, so it never reaches this branch).
- **Retries that were already succeeding keep succeeding**: if the refresh returned no new CSRF
  token, `refreshedStore.csrfToken` is null and the header is simply not set — the backend then
  answers 403 "CSRF token missing" instead of "invalid". Both are the same 403 to the caller;
  no new failure mode is introduced, since a null csrfToken means the session is already broken
  (`authStore.ts:228` comments the same reasoning).

Backend: log-string change only. No control flow, no status codes, no policy. The 403 responses
themselves are unchanged.

Not touched: ride state machine, dispatch, money/wallet deltas, background loops, the CSRF
policy itself (exempt paths, cookie names, comparison logic all unchanged).

## 5. User-experience effect

**Internal admin only.** Before: an admin's save (driver edit, and equally any other
PUT/POST/PATCH/DELETE) could fail with a generic error whenever their access token expired on
that exact request — roughly once an hour per active admin, unpredictably, and a retry by hand
would then succeed, which is what made it look flaky rather than broken. After: the retry
carries the right token and the write goes through.

**Visible mid-session: yes** — an admin with the dashboard open when this deploys stops hitting
the intermittent failure. No copy change, no new notification. Riders, drivers, and corporate
users are unaffected; this code is not shipped to the mobile apps or the company portal
(`companyApi.ts` is a separate client and already correct).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/api/client.ts` | On the 401 retry, re-read `csrfToken` from the refreshed store and set `X-CSRF-Token` for non-safe methods | The refresh rotates the CSRF cookie; the stale header failed the backend double-submit |
| `backend/core/middleware.py` | Both CSRF rejection warnings converted from `%s` to loguru `{}` fields; added header/cookie-presence and cookie-count context | loguru drops `%`-style args silently, so the 403 was logged with no identifying detail |
| `admin-dashboard/src/__tests__/lib/api-client-csrf-retry.test.ts` | New: retry sends the refreshed CSRF token; GET retry sends none; caller headers survive | Regression coverage for the 403 |
| `backend/tests/test_csrf_middleware.py` | New `TestRejectionLogsAreInterpolated`: both rejection logs contain method/path/origin and no literal `%s`; neither leaks a token value | Regression coverage for the logging defect, including the PII boundary |

## 7. Before / after

```ts
// Before — stale X-CSRF-Token rides along in ...headers
await store.silentRefresh();
const newToken = useAuthStore.getState().token;
if (newToken) {
    const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
```

```ts
// After — both auth headers rebuilt from the refreshed store
await store.silentRefresh();
const refreshedStore = useAuthStore.getState();
const newToken = refreshedStore.token;
if (newToken) {
    const retryHeaders: Record<string, string> = {
        ...headers,
        Authorization: `Bearer ${newToken}`,
    };
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && refreshedStore.csrfToken) {
        retryHeaders["X-CSRF-Token"] = refreshedStore.csrfToken;
    }
```

```python
# Before — loguru formats with {}, so this logs "CSRF token mismatch: %s %s origin=%s"
logger.warning("CSRF token mismatch: %s %s origin=%s", request.method, path, request.headers.get("origin"))
```

```python
# After
logger.warning(
    "CSRF token mismatch: {} {} origin={} cookies={}",
    request.method, path, request.headers.get("origin"), len(present_cookies),
)
```

## 8. Rollback plan

`git revert` is sufficient and complete here — no feature flag needed, and nothing has been
applied to live data. Both changes are stateless: the frontend change alters only the headers
of an in-flight retry, and the backend change alters only a log string. Reverting restores the
previous (broken) behavior with no residue — no migration, no persisted state, no external
side effect. Admins already holding the fixed JS bundle simply reload.

Not feature-flagged, deliberately: this is a bug fix restoring intended behavior on an
internal-admin surface, not new UX, and flagging it would mean shipping a knob whose "off"
position is the defect.

## 9. Verification performed

- [x] **Real production build run**: `npm run build` (Next.js 16.2.12, Turbopack) in
      `admin-dashboard` — `BUILD_EXIT=0`, "Compiled successfully in 28.8s", TypeScript pass
      included, all routes emitted. Not a dev server and not `tsc --noEmit` alone. See §10 for
      the two earlier attempts that failed on environment problems.
- [x] Frontend tests: `npx vitest run src/__tests__/lib/api-client-csrf-retry.test.ts` (3 passed);
      plus `authStore.test.ts`, `companyApi.test.ts`, `src/__tests__/lib` — **47 passed**.
- [x] New frontend test verified non-vacuous: stashing the `client.ts` fix makes 2 of the 3
      fail on the stale-token assertion.
- [x] Backend tests: `tests/test_csrf_middleware.py` — **25 passed**.
- [x] New backend tests verified non-vacuous: reverting the middleware to `%s` fails exactly
      the 2 interpolation assertions.
- [x] loguru's `%`-vs-`{}` behavior confirmed empirically, not assumed.
- [x] `ruff check` + `ruff format --check` clean on `core/middleware.py` and the test.
- [x] Blast-radius grep: all importers of `@/lib/api/client`, all `csrf` references across
      `admin-dashboard/src`, `_CSRF_*` constants and `CSRFMiddleware` in the backend,
      `companyApi.ts` for the already-fixed equivalent.
- [x] Reviewed against `CLAUDE.md` — token lifetimes / rotation model, PIPEDA (no credential or
      PII in logs), observability (warning level correct: recoverable, client-driven).

## 10. What was NOT verified

- **The root cause is inferred, not proven from the log** — because the log dropped the fields.
  The retry path is the one place in the admin client that provably sends a CSRF header that
  can disagree with the cookie, and it matches the symptom (mismatch rather than missing, on a
  PUT), but a second cause cannot be excluded from the evidence available. The logging fix is
  what makes the next occurrence diagnosable: a recurrence after this deploy will name the
  method, path, origin, and cookie count.
- **No end-to-end run against a live admin session.** The 401→refresh→retry sequence is
  exercised with a stubbed `silentRefresh` and mocked `fetch`, not a real expiring token against
  a real backend. Worth one manual confirmation in staging: sign in, wait out or force access-token
  expiry, then save a driver edit.
- **No visual regression tooling exists for admin-dashboard**, so the "no visible UI change"
  claim is reasoned from the diff (transport layer only, no component touched), not screenshotted.
  Standing gap, not introduced here.
- **Two earlier build attempts failed for environment reasons, not this diff.** First:
  `Module not found: motion/react` in `src/app/dashboard/monitoring/alert-feed.tsx` —
  `motion@^12.43.0` is declared in `package.json` but was absent from `node_modules` in this
  container. Second: `sh: 1: next: not found`, because the rebuild was started while
  `npm install` was still relinking `node_modules/.bin`. The clean run after the install
  finished is the one recorded in §9. Nothing in this change touches that module or that page.
  Note the session-start hook reported "admin deps ok" despite `motion` being absent, so that
  check does not actually guarantee a buildable tree.
- **`admin-dashboard/package-lock.json` was left unmodified.** The `npm install` above rewrote
  one line of it; that churn is an artifact of this container's out-of-sync `node_modules`, not
  a dependency decision belonging to this fix, so it was reverted rather than committed.
- **Wider finding, deliberately NOT fixed here** — see below.

## 11. Related finding: 55 loguru `%`-style log calls repo-wide (not fixed in this change)

An AST scan of every backend module that does `from loguru import logger` found **55** call
sites passing `%`-style placeholders with arguments. All of them silently drop their arguments,
exactly as the CSRF line did:

| File | Broken calls |
|---|---|
| `services/payment_service.py` | 23 |
| `services/corporate_member_offboarding_service.py` | 7 |
| `services/corporate_suspension_service.py` | 7 |
| `documents.py` | 6 |
| `core/middleware.py` | 2 *(fixed here)* |
| `dependencies/__init__.py` | 2 |
| `features.py` | 2 |
| `repositories/wallet_repo.py` | 2 |
| `repositories/ride_repo.py`, `routes/lost_and_found.py`, `routes/websocket.py`, `services/cancellation_service.py` | 1 each |

This directly undercuts the "do not silently swallow errors" convention: e.g.
`[PAYMENT] Stripe charge %s confirmed but ride %s DB update failed` and
`Stripe event %s is STUCK: claimed but never marked processed. Manual reconciliation` both
log without the ID needed to act on them.

Not fixed in this commit, for two reasons:

1. **Scope** — 53 remaining sites across 11 files, most in `payment_service.py`, exceeds the
   one-logical-change and ~200-line limits in `CLAUDE.md`.
2. **It is not a safe mechanical `%s`→`{}` sweep, and this is the more important reason.**
   These arguments have *never actually been emitted*. Repairing the format strings would start
   writing values that no one has reviewed against the PIPEDA logging rules — and at least one
   is a real hazard: `documents.py:944` would begin logging
   `user=<id> filename=<name> content_type=…` for driver-document uploads, where the filename is
   user-supplied and routinely contains a person's name or a licence number. `utils/log_guard.py`
   scrubs known PII shapes but is not a guarantee for arbitrary filenames.

   So each site needs reading before its arguments go live. That is a deliberate follow-up, not
   a sweep — recommend a separate PR per file group (payments / corporate / documents+misc),
   each re-checked against the "what can NEVER appear in logs" list.
