# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (admin portal security/RBAC audit, requested by user) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| Related issue or gap ID | `docs/audit/2026-09-10-admin-portal-security-rbac-audit.md`, finding W6 |

## 1. Issue / gap identified

`admin-dashboard/src/app/api/auth/set-cookie/route.ts`'s `POST` handler
accepted any JSON body `{token}` from any origin, with no CSRF check, no
Origin/Referer check, and no requirement that the caller already hold a
valid session, then unconditionally wrote that value into the httpOnly
`admin_token` cookie. Every other state-changing BFF route in this app
(`/api/admin/auth/{login,refresh,mfa/challenge}`) already uses the
double-submit `spinr_admin_csrf` cookie + `X-CSRF-Token` header pattern;
this one didn't.

**Concrete exploit:** a cross-site page could submit a `text/plain`-typed
POST (a CORS-simple content type; `request.json()` still parses a
JSON-shaped `text/plain` body) to overwrite a logged-in admin's
`admin_token` cookie with an attacker-chosen value. Today's actual impact
was bounded — nothing server-side trusts this cookie for a real API call,
only the edge middleware's client-side redirect gate (real API calls go
through the in-memory Zustand token, verified server-side by signature) —
but it was a live landmine: any future SSR data fetch or internal API route
that starts reading `cookies().get('admin_token')` and trusting it would
become an instant full auth bypass, with no review signal that this
endpoint was unprotected.

## 2. Root cause

This route predates (or was written independently of) the double-submit
CSRF pattern the other admin auth BFF routes use, and was never brought in
line with it.

## 3. Fix / remediation

- `set-cookie/route.ts`: added a `verifyCsrf()` function — identical
  pattern to `admin/auth/refresh/route.ts`'s own (`timingSafeEqual` on the
  `spinr_admin_csrf` cookie vs. the `X-CSRF-Token` header) — called at the
  top of `POST`; a mismatch returns 403 before any cookie is written.
  `DELETE` (session clear, no attacker-controlled value) was left
  unchanged — it has no equivalent injection risk, only the negligible
  "victim gets logged out" CSRF-logout class this repo (like most apps)
  doesn't treat as a real threat.
- The CSRF cookie is always present by the time either client caller POSTs
  to this route: `/api/admin/auth/login` and `/api/admin/auth/mfa/challenge`
  both set `spinr_admin_csrf` on every successful response, and both
  callers of `set-cookie` (`login/page.tsx`, `authStore.ts`'s
  `setAuthCookie`) fire immediately after one of those succeeds.
- `admin-dashboard/src/store/authStore.ts`: `setAuthCookie()` now reads the
  `spinr_admin_csrf` cookie (via a new `_readCookie()` helper — it's
  deliberately non-HttpOnly so JS can read it) and sends it as
  `X-CSRF-Token`.
- `admin-dashboard/src/app/login/page.tsx`: its own direct
  `fetch('/api/auth/set-cookie', ...)` call now sends
  `X-CSRF-Token: data.csrf_token` (already in scope from the login/MFA
  response — the same value the backend just set into the cookie).

## 4. Risk & impact on existing functionality

- **Blast radius: this one route + its exactly 2 callers.** Grepped the
  whole `admin-dashboard/src` tree for every reference to
  `/api/auth/set-cookie` — `middleware.ts` (reads the resulting cookie,
  doesn't call this route), `login/page.tsx` and `authStore.ts` (the 2
  POST callers, both fixed), and this route's own test file. No other
  caller exists. The company-portal's sibling route
  (`api/company-auth/set-cookie/route.ts`) is a separate file for a
  different token (rider access token) and was not touched or audited as
  part of this finding.
- **What could regress:** a POST to this route without a valid CSRF pair
  now 403s. Both real callers were updated to send it; verified via the
  full test suite, a real production build, and `tsc --noEmit` that
  nothing else calls this route without the header.

## 5. User-experience effect

None, for a legitimate login/MFA-challenge/refresh flow through this
app's own UI — both real callers send the token now. A user visiting this
app the very first time before any login (no `spinr_admin_csrf` cookie
exists yet) never hits this path, since `set-cookie` is only ever called
*after* a successful login/MFA response, at which point the cookie was
just set by that same response.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/api/auth/set-cookie/route.ts` | Added `verifyCsrf()` (mirrors `admin/auth/refresh/route.ts`); `POST` now 403s on a CSRF mismatch before writing any cookie | Close the unauthenticated cross-site cookie-injection gap |
| `admin-dashboard/src/store/authStore.ts` | Added `_readCookie()` helper; `setAuthCookie()` now sends `X-CSRF-Token` | Real caller must supply the token the new check requires |
| `admin-dashboard/src/app/login/page.tsx` | `_storeSessionAndRedirect`'s direct `fetch` call now sends `X-CSRF-Token: data.csrf_token` | Same — this is the other real caller |
| `admin-dashboard/src/__tests__/api/set-cookie.test.ts` | `makeRequest()` now simulates `cookies.get`/`headers.get`; 4 new tests (missing cookie, missing header, mismatched values, different-length values) for the CSRF gate; existing tests updated to pass a matching CSRF pair so they still test what they were written for (cookie attributes) | Cover the new gate without breaking the pre-existing attribute assertions |

## 7. Before / after

```typescript
// Before
export async function POST(request: NextRequest) {
    let token: string | undefined;
    try {
        const body = await request.json();
        token = body?.token;
    } catch (error) { ... }
    // no CSRF/Origin check at all
    ...
    response.cookies.set(COOKIE_NAME, token, { httpOnly: true, ... });
    return response;
}

// After
function verifyCsrf(req: NextRequest): boolean {
    const cookieToken = req.cookies.get(CSRF_COOKIE)?.value;
    const headerToken = req.headers.get('x-csrf-token');
    if (!cookieToken || !headerToken) return false;
    ... // timingSafeEqual comparison, same as admin/auth/refresh
}

export async function POST(request: NextRequest) {
    if (!verifyCsrf(request)) {
        return NextResponse.json({ error: 'CSRF validation failed' }, { status: 403 });
    }
    ... // unchanged from here
}
```

## 8. Rollback plan

`git revert`-safe. Pure client/server CSRF-verification addition — no
data, schema, or Stripe/wallet state involved. Reverting restores the
unprotected route (re-opens the gap); no data-level remediation needed.

## 9. Verification performed

- [x] Full test suite: `npx vitest run` — **632 passed, 0 failed**, 65 test files (includes the 16-test `set-cookie.test.ts`, `authStore.test.ts`, `login.test.tsx`).
- [x] `npx eslint` on all 4 touched files — 0 errors (1 pre-existing, unrelated `jsx-a11y/no-autofocus` warning on an untouched line in `login/page.tsx`).
- [x] `npx tsc --noEmit` — 0 errors.
- [x] **Real production build**: `npm run build` — clean, "Compiled successfully", full route manifest printed including `/login` and every `/dashboard/*` route. Per CLAUDE.md's Change Impact Log requirement, this is the actual build, not just a dev-server/tsc-only check.
- [x] Blast-radius grep performed repo-wide for every caller of `/api/auth/set-cookie` before changing anything (see §4).

## What was NOT verified

- No manual click-through of the real login flow in a running browser —
  verified by code read + full test suite + production build only,
  consistent with this repo's standing "no visual/snapshot regression
  tooling for rider-app/driver-app" gap; admin-dashboard's own visual-
  regression suite covers 6 specific dashboard pages, not the `/login`
  page or this API route, so it provides no coverage here either way.
- The company-portal's sibling `set-cookie` route (different token,
  different auth domain) was not audited or touched — out of scope for
  this finding, which named the admin portal specifically.
- Not tested against a live Vercel/edge deployment (cookie SameSite/Secure
  behavior across the real `admin-spinr.spinr.ca` domain) — only against
  the mocked `next/server` test harness and local build output.
