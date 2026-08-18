# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude (agent) for vikas@ngitservices.com |
| Surface(s) | admin-dashboard (company portal) |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/portal-otp-bypass-testing-60bqjz` |
| Related issue or gap ID | "CSRF token invalid when trying to invite a member under company called spinr in corporate module" |

## 1. Issue / gap identified

Inviting a member to a company in the corporate portal (Spinr for Business)
failed with a 403 "CSRF token invalid" error, with no way to recover short
of a manual page reload.

## 2. Root cause

The company portal's CSRF protection is a well-designed, correct
per-audience double-submit cookie scheme (`csrf_token_company`, distinct
from the staff admin's `csrf_token`, so the two surfaces sharing one browser
origin don't clobber each other — see `admin-dashboard/src/app/api/company-auth/_shared.ts`).
Its one gap: the `csrf_token_company` cookie is `path=/`, so it is shared
across **every browser tab/window** on the origin, but the value echoed as
the `X-CSRF-Token` header on each write lives in **per-tab in-memory**
Zustand state (`useCompanyAuthStore`), which is never persisted
(`partialize` explicitly excludes `token`/`csrfToken` — "memory + HttpOnly
cookies only").

If any other tab (or the same tab's own background 401 on an unrelated
request) triggers `silentRefresh()`, the backend's `/api/portal/auth/refresh`
call mints a brand-new CSRF value and the Next.js proxy route
(`refresh/route.ts`) rotates the shared cookie to it — but a *different* tab
whose in-memory state never saw that refresh keeps sending the *old* value
as its header. The next write in that tab (e.g. inviting a member) then
hits `core/middleware.py`'s `CSRFMiddleware`, whose double-submit check
fails (header present, but doesn't match either present cookie) → 403 "CSRF
token invalid".

`companyRequest` (the shared fetch wrapper every company-portal write goes
through) already has exactly this self-heal pattern for a 401 (silent
refresh, rebuild headers from the refreshed store, retry once) — it was
simply never extended to the CSRF-invalid 403 case, which is the *other*
observable symptom of the identical underlying condition (a stale in-memory
token vs. a rotated shared cookie).

## 3. Decision

No product decision needed — this is a resilience/correctness fix that
makes an already-correct architecture self-heal from a race it didn't
previously handle, matching the existing 401 pattern exactly.

## 4. Fix / remediation

- `companyRequest` (`admin-dashboard/src/lib/companyApi.ts`) now also
  triggers the silent-refresh-and-retry-once path on a 403 whose body's
  `detail` exactly matches the backend's literal CSRF error strings
  (`"CSRF token invalid"` / `"CSRF token missing"` — see
  `backend/core/middleware.py:571-596`), not just on a 401.
- Discrimination is by exact string match against the backend's own,
  same-origin response — a genuine authorization 403 (e.g.
  `require_company_admin` denying a non-owner) has a different `detail`
  string and falls through to the existing plain-error path unchanged, so
  this never masks or retries a real permission denial.
- A `_peekCsrfError` helper clones the `Response` before reading its body
  for the check, so the original response stream remains consumable by the
  existing `!res.ok` fallthrough when the 403 turns out not to be a CSRF
  error.
- Retry is bounded to exactly one attempt, identical to the existing 401
  path — if the retry also fails, its error is surfaced as-is (no loop).

## 5. Risk & impact on existing functionality

- **Blast radius**: `companyRequest` is the shared client for every write
  the company portal makes (member invite/update, sections, allowance,
  policy, wallet top-up, bookings, KYB submission, etc. — grepped, all
  route through this one function). The change only *adds* a new retry
  trigger condition; every existing call site's success/plain-error path is
  untouched.
- **No CSRF protection weakened**: walked through the actual attack this
  defends against — a cross-site forged request has no access to read
  either CSRF cookie (SameSite=Strict blocks the cookie from even being
  attached on a cross-site request) and so arrives with the header **absent
  or unrelated**, which the backend responds to with `"CSRF token missing"`
  before ever reaching the mismatch branch this fix targets — but that
  string is deliberately included in the retry match too, since it's the
  *identical* class of failure (a real session with a temporarily-desynced
  local CSRF value, not a forged request — an attacker cannot forge a
  request that lands the backend on this specific code path in the first
  place, because they cannot read the cookie to omit-and-still-match, nor
  can they read it to include a matching header). No new attempts are
  granted to an actual forgery attempt.
- **No infinite-retry risk**: identical one-shot bound as the existing 401
  path, verified by test.

## 6. User-experience effect

Rider/corporate-portal facing, immediate. A company-portal user (e.g.
managing the "spinr" company) who previously hit a hard, unrecoverable
"CSRF token invalid" error on any write action — most likely to happen
after having the portal open across multiple tabs/windows for a while — now
has that transparently self-heal via one silent refresh+retry, exactly as
already happens for a plain expired-token 401. Not visible to a single-tab
user in a short session (the race window this closes only opens once a
background refresh has actually happened elsewhere).

## 7. Before / after

```typescript
// Before
if (res.status === 401) {
    const refreshed = await store.silentRefresh();
    // ... rebuild headers, retry once
}
```

```typescript
// After
if (res.status === 401 || (await _peekCsrfError(res))) {
    const refreshed = await store.silentRefresh();
    // ... rebuild headers, retry once — identical logic, now also
    //     reached by a CSRF-invalid 403
}
```

## 8. Rollback plan

`git revert` — pure client-side code change, no data mutation, no schema
change, no feature flag needed. Reverting restores the prior behavior
(hard failure on a CSRF-invalid 403) with zero other side effects.

## 9. Verification performed

- [x] Independently reproduced the pre-fix failure mode: reverted the code
  change and confirmed the new regression tests fail with the exact
  predicted error (`"CSRF token invalid"` thrown instead of the retry
  succeeding), then restored the fix and confirmed they pass.
- [x] New test file `companyRequest-csrf-retry.test.ts` (4 tests): (1) a
  CSRF-invalid 403 triggers silent refresh + a successful retry with the
  *new* token/csrf pair, not the stale one that just failed; (2) a failed
  refresh after a CSRF-invalid 403 logs out and surfaces `Unauthorized`,
  matching the 401 path's existing contract; (3) a genuine non-CSRF 403
  (role-based denial) does **not** trigger the refresh/retry path and
  surfaces its own message unchanged; (4) a retry that is *itself* still
  CSRF-invalid does not loop — the retry's error surfaces directly, exactly
  one refresh/retry attempt total.
- [x] Full `admin-dashboard` vitest suite: `163 passed` (21 files) — no
  regressions in any other `companyRequest` consumer.
- [x] `npx tsc --noEmit` → clean.
- [x] `npx eslint` on both changed files → clean.
- [x] **Full production build** (`npm run build`, not just `tsc --noEmit` or
  the dev server) → succeeded, all routes compiled.
- [x] **`spinr-security-auditor` adversarial review** — verdict SAFE TO
  MERGE, no blockers. Confirmed: (1) exact-string CSRF-detail matching is
  safe — both matched strings are emitted from exactly one place in the
  backend (`CSRFMiddleware`), and the response being matched is always
  same-origin/same-app, never attacker-influenceable; (2) no CSRF
  protection weakened — walked through what an actual forged cross-site
  request receives (it can't read the cookie to include a matching header,
  so it never lands on this code path at all; the app's own JS, including
  this retry logic, never even executes on an attacker's page); (3) retry
  is correctly bounded to exactly one attempt, verified both by inspection
  and a new explicit test; (4) the `clone()`-before-read pattern in
  `_peekCsrfError` is spec-correct, no double-read hazard; (5) `companyRequest`
  has exactly one direct caller outside `companyApi.ts` itself
  (`company-portal/[id]/book/page.tsx`, already try/catch-wrapped, no
  timing assumptions broken). Two non-blocking follow-ups applied before
  merge: added the explicit "retry itself still CSRF-invalid" regression
  test, and documented why `"CSRF token missing"` is deliberately included
  alongside `"CSRF token invalid"` in the match set (both fixed above,
  see §9's test count and the code comment).

## What was NOT verified

- Not reproduced end-to-end in a real multi-tab browser session against a
  running backend — verified at the unit level with mocked `fetch`/store,
  which is the mechanism this fix targets directly.
- Whether the specific user's "spinr" company incident was actually caused
  by this exact race (vs. some other one-off condition) — not diagnosable
  without live session/log access; this fix addresses the one concrete,
  reproducible gap found in the client's CSRF-retry logic that produces the
  exact reported symptom ("CSRF token invalid" on a write, specifically).
- No visual/UI regression tooling exists for `admin-dashboard`; this change
  has no UI diff (it's purely inside the fetch wrapper), so N/A.
