# Spinr — Comprehensive Platform Review & Remediation Plan

**Date:** 2026-07-17 · **Branch:** `claude/zealous-cori-c74nk0` · **Scope:** read-only teardown of backend, rider/driver apps, shared layer, admin dashboard, CI/CD, testing, and tech stack — benchmarked against industry leaders (Uber, Lyft).

Method: four independent deep-review passes (backend security/payments · backend performance/dispatch · mobile + shared · admin/infra/CI), every finding verified against actual code. No code was modified.

---

## 🚨 Critical Issues & Security Flaws

### CR-1 (Critical) — Redis outage silently halts ALL dispatch (fail-closed presence filter)
`backend/routes/rides/matching.py:303-319` + `utils/redis_client.py:78-94` + `utils/driver_presence.py:99-154`
The live dispatch path gates the presence filter on `_check_redis()`, but `redis.asyncio.from_url()` is lazy — it returns a client object even mid-outage, so `_redis_live` is always `True` when `REDIS_URL` is set. During an outage, `present_driver_ids()` swallows the MGET failure and returns an **empty set** (dropping the `reachable=False` flag), the filter removes every candidate, and the stuck-ride sweeper cancels every searching ride after ~5 min — a **total dispatch outage from a partial dependency failure**. The cascade branch and `DispatchService.find_candidate_drivers` handle this correctly; only the primary path is wrong. **Why it matters:** this is the single highest blast-radius defect in the platform; a 3-minute Redis blip becomes a fleet-wide ride-cancellation event. **How:** use `present_driver_ids_checked` and skip the filter when `reachable=False`, exactly as the cascade branch already does (near one-line fix + regression test).

### CR-2 (Critical) — SOS blocks on an untimed high-accuracy GPS fetch
`shared/components/SOSButton.tsx:114-121`
`triggerSOS()` awaits `Location.getCurrentPositionAsync({accuracy: High})` with no timeout and no last-known-position fallback **before** the backend call starts. Indoors/parkades/urban canyons — exactly where SOS is pressed — the fix can hang 30s+; the button sits on "Sending…" and neither the success state nor the "Call 911" failure path ever appears. The 3× retry ladder only wraps the network call and re-runs the untimed fetch each attempt. **How:** `Promise.race` the fetch against ~3s, fall back to `getLastKnownPositionAsync()`, then to no-coords (backend tolerates missing lat/lng); fire the alert immediately and attach coordinates opportunistically.

### CR-3 (High) — "Share my trip" sends a fabricated location to safety contacts
`rider-app/app/ride-in-progress.tsx:67,340-341`
`currentLocation` is initialized to the hardcoded string `'4th Avenue North'` and **never updated**; the dropoff fallback is `'1055 Canada Place'` (a Vancouver address in a Saskatchewan product). Every share-trip SMS asserts a false location; if the live-tracking token fetch failed (silently caught at :321-323), the fake text is the only location the contact gets. **How:** reverse-geocode the live position or omit the line; never fabricate an address in a safety artifact.

### CR-4 (High) — Company-email OTP has no brute-force lockout → account takeover primitive
`backend/routes/auth.py:589-702`
Phone OTP has the mandated 5-failures/hour → 24h lockout; the company-email OTP path has **none** — only a per-IP 5/min limiter. The code is 4 digits (10,000 combinations), valid 5 minutes, and `_issue_company_email_session` logs into **any existing user** matching the email. A rotating-IP attacker brute-forces into a victim's account (wallet, ride history). **How:** mirror the phone path — per-email `_check_otp_lockout`/`_record_otp_failure`, invalidate the OTP row after N failures, fail closed on Redis error.

### CR-5 (High) — Transient token-refresh failure hard-logs-out drivers mid-shift
`shared/api/client.ts:794-806,909-928` + `shared/store/authStore.ts:284-289,586-614`
`refreshTokens()` deliberately keeps the session on 5xx/timeout, but `handleApiError` has no early return on that branch — execution falls through to the G2 block, which calls `logout()`, **deleting the refresh token from SecureStore**. A driver resuming from background on flaky network is signed out mid-ride and must redo OTP — the exact outcome the transient-failure branch was written to prevent. Bonus hazard: `logout()` first fires a status PUT with the dead token, re-entering the refresh machinery. **How:** distinguish "refresh definitively rejected" (teardown) from "refresh transiently failed" (reject original request, keep credentials).

### CR-6 (High) — Rider live-ride screen can freeze silently on half-open sockets
`rider-app/hooks/useRiderSocket.ts` (no heartbeat watchdog) + `rider-app/app/ride-in-progress.tsx:157-161`
The driver app force-closes its socket after 15s of silence; the rider socket never tracks last-server-message, and ride-in-progress **suspends its fallback poll while `wsConnected` is true**. On cell handoff/NAT timeout, `onclose` may not fire for minutes — map, driver position, and status freeze with no updates from either channel. **How:** port the driver-side heartbeat watchdog, or degrade the poll to a slow verification tick instead of suspending it.

### CR-7 (High) — Three racing production deploy pipelines, contradicting the documented topology
On a `main` push touching `backend/`, **all three** fire: `ci.yml:357-388` (→ Railway, with an undocumented **Render** fallback), `deploy-backend.yml` (→ Railway again), and `deploy-fly.yml` (→ Fly). Two concurrent `railway up` runs race; `ci.yml:369` claims Railway is primary while CLAUDE.md says Fly. **How:** one deploy workflow per host; delete the `ci.yml` deploy job and Render path.

### Also confirmed (Medium)
- **Admin middleware gates on an unverified JWT** — `admin-dashboard/src/middleware.ts:114-140` decodes without signature verification (Edge Runtime excuse), and `api/auth/set-cookie/route.ts:6-29` sets `admin_token` from any unauthenticated POST. Backend still verifies every API call, so exposure is the page shell — but verify HMAC via `jose` (Web Crypto works on Edge) and add a role check (middleware currently checks only presence+`exp`; RBAC lives in a bypassable client hook).
- **Driver-document PII survives account deletion** — `routes/users.py:236` deletes `driver_documents` by `user_id` but the FK is `drivers.id` (≠ `user_id`); the delete matches zero rows. License/insurance images survive PIPEDA erasure. Resolve the driver id first.
- **Phone change without possession proof** — `routes/users.py:264-287` accepts any ≥10-char string, no OTP re-verification, no E.164 normalization; can break the caller's own login identity or squat numbers.
- **Wallet top-up ships name+email to Stripe** — `routes/wallet.py:169-177` contradicts the deliberate PII-minimizing `get_or