# Change Impact & Risk Log — Super-admin Sentry issue viewer

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (session: sentry-logs-admin-dashboard) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/sentry-logs-admin-dashboard-7jeqr0` |
| Related issue or gap ID | n/a — new capability request |

## 1. Issue / gap identified

There is no way to see production errors from inside the admin dashboard. Sentry
receives events from all four surfaces (backend, rider-app, driver-app, admin —
each already initialises the SDK with a `surface` tag), but triaging them means
leaving Spinr and logging into sentry.io, which only the people with Sentry seats
can do. A super-admin had no in-product view of "what is breaking right now, in
which app, and how bad is it".

## 2. Root cause

Not a bug — a missing capability. Sentry has always been write-only from Spinr's
perspective: `sentry_sdk.init()` in `backend/server.py` and
`sentry.{client,server}.config.ts` in the admin dashboard push events out, and
nothing ever read them back.

## 3. Fix / remediation

Added a **stateless read/resolve proxy** over the Sentry Web API plus a
super-admin-only dashboard page.

- Backend `routes/admin/sentry.py` exposes four endpoints under
  `/api/admin/sentry`: `GET /config`, `GET /issues`, `GET /issues/{id}`,
  `POST /issues/{id}/status`.
- Nothing is persisted. There is no new table, no cache, no background loop.
  Every request proxies live to Sentry, so the dashboard's **Refresh** button
  simply drops the client-held state and re-calls the endpoints — matching the
  requested "clear all existing memory and get the new data" behaviour.
- The issue list is fetched per-project concurrently, tagged with the Spinr
  surface each project maps to, then merged and sorted newest-seen-first.
- Issue detail additionally fetches the latest event and normalises its
  exception chain into a compact stacktrace shape (frames reversed so the crash
  site is first).
- The status endpoint supports `resolved` (the dashboard's "Close" action),
  `ignored`, and `unresolved` (reopen).

Config is env-based (`SENTRY_API_TOKEN`, `SENTRY_ORG_SLUG`,
`SENTRY_API_BASE_URL`, `SENTRY_PROJECT_BACKEND|_RIDER|_DRIVER|_ADMIN`), the same
deploy-secret posture as the existing `sentry_dsn`, rather than `app_settings` —
this is a credential for an external service, not an operator-tunable setting.

## 4. Risk & impact on existing functionality

**Blast radius: isolated / additive.** No existing code path was modified.

Blast-radius greps performed and what they found:

- `backend/routes/admin/__init__.py` — one added line
  (`include_router(sentry_router, …)`). Every other `include_router` call is
  untouched; the new router has its own `/sentry` prefix, so no route-path
  collision with any of the ~40 existing admin sub-routers.
- `backend/core/config.py` — five new optional `Settings` fields. Purely
  additive; no existing field's name, type, or default changed, so no other
  reader of `settings` is affected. Confirmed no pre-existing
  `SENTRY_API_TOKEN` / `SENTRY_ORG_SLUG` / `SENTRY_PROJECT_*` attribute existed
  (only the lowercase `sentry_dsn`, which is untouched).
- `admin-dashboard/src/lib/api.ts` — new re-export block. Checked the four new
  function names and eleven type names do not collide with any existing export
  in that barrel file.
- `admin-dashboard/src/components/sidebar.tsx` — **the one shared component
  touched.** It is imported by `src/app/dashboard/layout.tsx` only (single
  consumer, verified), so every dashboard page renders it. The change is one
  added `NAV_GROUPS` entry plus one added lucide icon import; no change to the
  filter logic, the `NavItem` type, or any existing entry. The new entry uses
  the **existing** `superAdminOnly` flag, already implemented and exercised by
  the filter at both parent and child level — no new branch was added to the
  rendering path.
- No new database table, column, or migration. No RLS policy touched.
- No interaction with the ride state machine, dispatch, wallets, Stripe, or any
  of the background loops in `core/lifespan.py`. No money arithmetic — this
  change contains no `Decimal` or currency handling at all.

Residual risks, stated rather than implied:

- **Upstream dependency.** The page depends on sentry.io availability. A Sentry
  outage makes this page error (502 with a clear message); it cannot affect any
  other Spinr surface, because nothing else calls these endpoints.
- **Sentry API rate limits.** Each page load issues one request per configured
  surface (max 4), plus one pair per issue opened. There is no polling loop —
  deliberately, unlike the Redis monitoring page — so sustained load is bounded
  by how fast an operator clicks Refresh.
- **Token scope.** `POST /issues/{id}/status` writes to Sentry. If the deployed
  token lacks `event:write`, resolve fails with a 502 whose message names the
  missing scope; reads continue working.

## 5. User-experience effect

- **Riders / drivers / corporate admins: no change whatsoever.** Nothing in the
  rider-app, driver-app, or company portal was touched. Nobody mid-ride or
  online sees anything.
- **Internal admins:** a new "Sentry Issues" entry appears in the System group of
  the sidebar **for super-admins only**. Staff with an `admin` role or any module
  grant do not see it (the `superAdminOnly` flag is a strict `role ===
  "super_admin"` check, matching the backend's `require_super_admin` exactly, so
  nobody sees a nav entry that would 403 on click).
- No existing screen changed behaviour. No notification or customer-facing copy
  was added or altered.
- One action has effects outside Spinr: resolving an issue changes its status in
  Sentry for the whole org, so it is visible to anyone with a Sentry seat. This
  is the intended semantic of the requested "close" action, and the dashboard
  offers Reopen for a mistaken resolve.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/config.py` | Added 5 optional Sentry Web API settings | Credentials + per-surface project slugs, as deploy secrets |
| `backend/routes/admin/sentry.py` | **New.** Config/list/detail/status endpoints | The read+resolve proxy itself |
| `backend/routes/admin/__init__.py` | Mounted `sentry_router` under `require_super_admin` | Raw production error data is super-admin-only |
| `backend/tests/test_admin_sentry.py` | **New.** 20 unit tests | Lock the shaping, gating, error-mapping and resolve contracts |
| `admin-dashboard/src/lib/api/sentry.ts` | **New.** Typed client for the 4 endpoints | Matches the per-domain API module split |
| `admin-dashboard/src/lib/api.ts` | Added re-export block | Barrel convention for `@/lib/api` imports |
| `admin-dashboard/src/app/dashboard/sentry-logs/page.tsx` | **New.** Issue list, filters, detail dialog, actions | The operator-facing surface |
| `admin-dashboard/src/components/sidebar.tsx` | Added super-admin-only nav entry + `Bug` icon import | Make the page reachable |

## 7. Before / after

Purely additive — no existing behaviour changed, so there is no before/after
diff of an existing code path. The only edit to shared, already-shipped code is
the sidebar entry, which appends to a list:

```tsx
// Before — System group ended at Staff
{ href: "/dashboard/monitoring/redis", label: "Redis & Infra", icon: Activity, module: "settings" },
{ href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "settings" },
```

```tsx
// After — one entry added, existing entries untouched
{ href: "/dashboard/monitoring/redis", label: "Redis & Infra", icon: Activity, module: "settings" },
{ href: "/dashboard/sentry-logs", label: "Sentry Issues", icon: Bug, module: "settings", superAdminOnly: true },
{ href: "/dashboard/audit-logs", label: "Audit Logs", icon: Shield, module: "settings" },
```

## 8. Rollback plan

**Config-level, no redeploy required:** unset `SENTRY_API_TOKEN` (or
`SENTRY_ORG_SLUG`) in the backend environment. `_is_configured()` then returns
False, `GET /config` reports `configured: false`, and the page renders its setup
panel instead of querying Sentry — the feature is off while the code stays
deployed. This is the intended kill switch and needs no code change.

To also hide the nav entry, a redeploy of the admin dashboard is required
(the entry is static in `sidebar.tsx`). That is cosmetic only — with the token
unset the page is inert.

No data-level remediation is possible or needed: this change writes nothing to
Spinr's database. The one external write (issue status in Sentry) is reversible
in-product via the Reopen action, or directly in sentry.io.

## 9. Verification performed

- [x] **Backend lint:** `ruff check` clean on `routes/admin/sentry.py`,
      `routes/admin/__init__.py`, `core/config.py`, `tests/test_admin_sentry.py`.
- [x] **Static parse check** of both new Python files.
- [x] **Blast-radius greps performed** — listed explicitly in §4: consumers of
      `sidebar.tsx`, name collisions in the `lib/api.ts` barrel, pre-existing
      `SENTRY_*` settings attributes, and route-prefix collisions in the admin
      router.
- [x] **Reviewed against `CLAUDE.md` conventions:** dual-import pattern used;
      no float/`Decimal` arithmetic involved; errors surface loudly (503 for
      missing config, 502 for upstream failure) rather than being swallowed;
      PIPEDA — the module logs only issue ids, project slugs, counts and HTTP
      status codes, never issue titles, stacktraces, or event bodies.
- [x] **Auth posture verified by reading the code**, not assumed: router mounted
      with `Depends(require_super_admin)`, and the frontend nav gate uses the
      matching strict `superAdminOnly` flag.
- [ ] **Not feature-flagged** beyond the config kill switch above. Justification:
      the surface is new, super-admin-only, and read-mostly; the unset-token path
      is an equivalent and simpler off switch.

### What was NOT verified — read this before merging

- **No test was executed.** The sandbox has no network access to PyPI, so the
  backend dependencies (`fastapi`, `httpx`, `pytest`) could not be installed and
  `pytest backend/tests/test_admin_sentry.py` was **never run**. The tests were
  written to mirror the existing `test_admin_monitoring_coverage.py` patterns
  and are lint- and parse-clean, but their pass/fail status is **unknown** until
  CI runs them.
- **No production build was run.** `npm ci` fails in this sandbox (the npm
  registry is blocked by the environment's network policy), so
  `npm run build` and `tsc --noEmit` for `admin-dashboard` were **not run**.
  Per `CLAUDE.md` this is exactly the gate that must not be silently skipped:
  **someone must run `npm run build` before merging.** The frontend was instead
  reviewed statically against the existing components' real export lists.
- **Not exercised against a real Sentry org.** Every Sentry response shape here
  (issue fields, `entries[].type == "exception"`, frame `context` pairs) comes
  from the documented Sentry Web API, not from a live call. The first run against
  a real org is the true test of the shaping code — in particular the
  `priority` field, which older Sentry orgs may not return (handled: the UI falls
  back to `level`).
- **No visual regression tooling exists for the admin dashboard**, so the new
  page's rendering was reasoned about against the comparable Redis monitoring
  page, not screenshotted or snapshot-tested. This is a standing repo gap, not
  specific to this change.
- **No staging deploy or manual repro** was performed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (unset one env var).
- [x] Blast radius is stated explicitly in §4, including the single shared
      component touched and its one consumer.
- [x] No silent behavior change to an already-shipped flow — the change is
      additive, and the only shared-file edit is one appended nav entry.
- [ ] **Blocked on:** `npm run build` (admin-dashboard) and
      `pytest backend/tests/test_admin_sentry.py` must both pass in CI before
      this merges. Neither could run in the authoring environment.
