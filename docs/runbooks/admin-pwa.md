# Admin PWA / Service Worker Policy

The admin dashboard currently has **no service worker**. This document defines
the constraints that any future PR adding one must satisfy (A-PE-P3-7 / audit
finding 23-9).

## Constraints

**Scope**: A service worker MUST be registered with `scope: '/dashboard/'`.
It must not intercept requests outside `/dashboard/*`. Routes under `/api/*`,
`/login`, and `/register/*` must never be handled by a service worker.

**Cache policy — static assets only**: The service worker may cache CSS, JS
chunks, fonts, and image assets delivered by Next.js (`/_next/static/**`).

**Never cache HTML or API responses**: Admin HTML responses contain session-
sensitive navigation (role, module list). API responses include live operational
data (payouts, dispatch, driver locations). Stale versions of either are a
correctness hazard — a finance operator could act on yesterday's payout figures.

**No background sync for write operations**: `BackgroundSync` must not be used
to replay failed mutations (surge overrides, driver suspensions, payment actions).
Write operations require a live session and idempotency guarantees that offline
replay cannot provide.

**Update on activate**: Set `skipWaiting()` + `clients.claim()` so that a new
deploy is picked up immediately rather than waiting for all tabs to close. The
admin is a single-operator surface; there is no multi-tab conflict risk worth
the staleness cost of deferring.

## Review checklist for any SW PR

- [ ] `scope` is `/dashboard/` (not `/`)
- [ ] `fetch` handler has an explicit allowlist of cacheable URL patterns
- [ ] No HTML responses (`Content-Type: text/html`) enter the cache
- [ ] No `/api/*` responses enter the cache
- [ ] `skipWaiting()` + `clients.claim()` are present in `activate`
- [ ] Security team has reviewed the manifest and fetch handler
