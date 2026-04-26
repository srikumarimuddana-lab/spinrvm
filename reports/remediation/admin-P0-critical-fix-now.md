# P0 — Admin Critical: Fix Before Any Browser Testing

These 3 admin items must land before any internal admin uses the dashboard against production data. One enables wholesale account takeover via XSS; one keeps stolen tokens valid for half a workday; one will OOM the server on the first scheduled rollup against a real fleet.

Source audit: `reports/audits/2026-04-25-admin-panel-audit-v1.txt`
Branch: `claude/audit-continuation-batch-2`

**Estimated total effort:** ~16–24 hours.

---

## A-P0-1 · Admin JWT Stored in `sessionStorage` — Accessible to Any XSS Payload

**What's wrong:** `admin-dashboard/src/store/authStore.ts:131` persists the admin access token via `createJSONStorage(() => sessionStorage)`. Any XSS payload (compromised npm package, DOM injection, dependency typo-squat) can exfiltrate it with `sessionStorage.getItem()`. The token is also dual-written to a client-readable cookie with no `HttpOnly` flag, doubling the surface.

**File to fix:** `admin-dashboard/src/store/authStore.ts:131` + backend `/admin/auth/login` and `/admin/auth/refresh` to set the cookie server-side

**How to fix:**
1. Backend: on `/admin/auth/login` success, set the access token via `Set-Cookie: admin_access=<jwt>; HttpOnly; Secure; SameSite=Strict; Path=/admin; Max-Age=3600`. Same on refresh.
2. Frontend: remove `createJSONStorage(() => sessionStorage)` from the Zustand persist config. Drop the in-memory token field — rely on the cookie being attached automatically.
3. Add `credentials: 'include'` to every admin fetch/axios call so the cookie travels.
4. Pair with A-P0-2 (1 h TTL) so the cookie's max-age matches the token's lifetime.

**Regression test:** `test_admin_login_sets_httponly_cookie` — POST `/admin/auth/login`, assert response has `Set-Cookie: admin_access=...; HttpOnly; Secure; SameSite=Strict`. UI test: `document.cookie` must NOT include `admin_access`.

**Why it matters:** Admin compromise is total — every rider/driver PII row, every wallet, every staff record. CLAUDE.md's PIPEDA section calls a single PII exposure a P0 incident; this code makes one inevitable on the first XSS.

**Effort:** 8–16 hours · **Severity:** CRITICAL · **Risk score:** 36 · **Regulations:** PIPEDA, OWASP A07 · **Audit ref:** 02-2

---

## A-P0-2 · Admin Access Token TTL Is 12 Hours — Half a Workday Window for Stolen Tokens

**What's wrong:** `backend/core/config.py:39` sets `ADMIN_ACCESS_TOKEN_TTL_HOURS = 12`. Admin accounts can read every piece of rider/driver PII, mutate wallets, and elevate other staff. A 12-hour validity means a token stolen at 9 AM is still good after lunch. OWASP recommends ≤1 hour for privileged tokens.

**File to fix:** `backend/core/config.py:39` + admin-dashboard refresh interceptor

**How to fix:**
1. Set `ADMIN_ACCESS_TOKEN_TTL_HOURS = 1` (or `ADMIN_ACCESS_TOKEN_TTL_MINUTES = 60` if a finer unit is preferred).
2. Implement silent refresh in admin-dashboard: a timer that fires 5 minutes before expiry, calls `/admin/auth/refresh`, and updates the cookie. If refresh fails, route to `/login`.
3. Refresh-token TTL stays at its current value (rotated on every use); only the access token shrinks.

**Regression test:** `test_admin_access_token_expires_in_one_hour` — issue a token, decode JWT, assert `exp - iat = 3600`. Admin-dashboard test: simulate clock past expiry, assert refresh interceptor fires within 5 minutes.

**Why it matters:** Combined with A-P0-1, the blast radius of any admin token theft drops from 12 hours to 60 minutes. Pairs naturally — fixing one without the other leaves either the storage or the lifetime as the open door.

**Effort:** 4 hours · **Severity:** CRITICAL · **Risk score:** 32 · **Regulations:** OWASP A07, SOC2 CC6.1 · **Audit ref:** 02-1

---

## A-P0-3 · GPS Points Query Uses `limit=1000000` — Will OOM in Production

**What's wrong:** `backend/routes/admin/maintenance.py:122` fetches driver GPS points with `limit=1000000` and aggregates them in Python. A fleet of 100 drivers online for 8 hours each produces ~2.88M GPS points/day at 1 Hz. Even at 50% coverage that's 1.4M rows loaded into the request handler's memory — the rollup process will exhaust RAM and time out before the platform reaches 100 active drivers.

**File to fix:** `backend/routes/admin/maintenance.py:122` + new SQL aggregation function

**How to fix:**
1. Create a Supabase RPC `compute_driver_online_minutes(start_ts, end_ts)` that aggregates server-side: `SELECT driver_id, COUNT(*) FILTER (...) AS minutes_online ... GROUP BY driver_id`.
2. Replace the in-memory loop with `await db_supabase.rpc("compute_driver_online_minutes", {...})`.
3. Add an index on `driver_gps_points (driver_id, recorded_at)` if one isn't there yet.
4. Cap any future "fetch raw GPS points" admin call at `limit=1000` with explicit cursor pagination — never paginate analytics through Python.

**Regression test:** `test_daily_rollup_no_in_memory_aggregation` — seed 1M GPS points; run rollup; peak RSS stays under 200 MB; latency under 5 s.

**Why it matters:** This is a production-OOM bug, not a slowness bug. The rollup runs daily, so the failure mode is a service-level alarm at 02:00 UTC followed by a memory-hung worker. CLAUDE.md's anti-pattern list flags exactly this case ("paginating analytics data through Python").

**Effort:** 4 hours · **Severity:** CRITICAL · **Risk score:** 32 · **Audit ref:** 08-1

---

## Checklist

- [ ] A-P0-1 Move admin JWT to HttpOnly Secure SameSite=Strict cookie; remove `sessionStorage` persistence (02-2)
- [ ] A-P0-2 Reduce admin access token TTL to 1 hour; add silent refresh (02-1)
- [ ] A-P0-3 Replace `limit=1000000` GPS query with SQL aggregation RPC (08-1)

## After this file

- Run `pytest backend/tests/test_admin_routes_auth.py` plus the 3 new regression tests above.
- Move on to `admin-P1-before-beta.md` (11 items): RBAC enforcement, token-version bumps on staff deactivation, audit-log writes for every mutation, analytics DB-side date filtering, bulk push fan-out via background tasks, FAQ management UI, etc.
