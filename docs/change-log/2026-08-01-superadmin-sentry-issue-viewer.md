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

## 8b. Review-round fixes (2026-08-02)

A pre-merge review of the branch found nine issues; all are fixed in the
follow-up commit. Each is listed with what it risked, because several change
behaviour rather than just adding code.

| # | Issue | Fix | Risk if left |
|---|---|---|---|
| 1 | Two unit tests called the route functions directly, so FastAPI's `Query(...)` sentinel objects flowed in as real values and the surface guard rejected them | Route bodies extracted into plain-argument impls (`_sentry_config`, `_list_issues`, `_get_issue`, `_update_issue_status`); tests drive those | Test suite red; the two behaviours they cover (cross-project merge/sort, `truncated`) were effectively unverified |
| 2 | `asyncio.gather` without `return_exceptions` inside `async with httpx.AsyncClient` | `return_exceptions=True` on both fan-outs; per-surface `errors[]` + `partial` in the response, 502 only when **every** surface fails | One typo'd `SENTRY_PROJECT_*` slug blanked the whole triage view, and the client closed under still-in-flight sibling requests |
| 3 | Status-change wrote only an app log line | `log_admin_action(..., "sentry_issue_status_change", "sentry_issue", ...)` → `audit_logs` | Violates the observability convention (admin actions → audit table); no durable record of who closed which production issue |
| 4 | All event tags relayed verbatim to the browser | `_TAG_ALLOWLIST` — allowlist, not denylist; everything else dropped | SDK-auto-attached `url` / `user` / `server_name` tags sit outside each surface's `beforeSend` scrubbing and could surface addresses and emails on an admin screen (PIPEDA) |
| 5 | `issue_id` interpolated into the Sentry API path unvalidated | `_validate_issue_id()` — digits only, rejected before any upstream call | A value with `../` or `?` reshapes the request into a different Sentry endpoint |
| 6 | Any issue in the whole Sentry org was readable **and resolvable** by id | `_resolve_surface()` 404s anything outside `SENTRY_PROJECT_*`; the status endpoint checks before the `PUT` | An org-shared Sentry account made this an org-wide read/write console, not a Spinr viewer |
| 7 | `_ALLOWED_STATUSES`, the list guard, and its error message disagreed (`muted` accepted by one only) | Single `_ALLOWED_STATUSES` tuple used by both endpoints and both messages | `?status=muted` offered a filter the update endpoint can never set, and a rejected `is:muted` search surfaced as an opaque 502 |
| 8 | No rate limit on a fan-out proxy | `@limiter.limit` — 60/min reads, 20/min writes | A dashboard tab in a reload loop could burn the Sentry org's API quota for everyone |
| 9 | Frame context matched on `pair[0] == lineNo` even when `lineNo` is `None` | Guarded on `line_no is not None` | Minified JS frames could show an unrelated source line as the crash line |

Two smaller ones alongside: handlers now declare `Depends(require_super_admin)`
themselves (not only at the mount, matching `stripe_payout_sync`), and the
frontend fetch uses a monotonic sequence token so a Refresh racing an in-flight
filter change cannot land out of order.

**User-experience effect of the review round:** the issues list can now render a
yellow "Partial results — N surfaces failed to load" card above the list, and the
all-clear empty state is suppressed while any surface failed. The detail dialog
shows an explicit "Could not load the latest event" line instead of the
ambiguous "No stacktrace on the latest event." Tag chips show fewer keys than
before. No other visible change; the surface is not yet enabled in production
(no `SENTRY_API_TOKEN` set), so nothing here is visible mid-session to a user.

## 9. Verification performed

- [x] **Backend tests RUN and passing:** `pytest backend/tests/test_admin_sentry.py`
      → **36 passed**. (Before the review round: 20 passed, 2 failed — see §8b #1.)
      Fourteen tests were added covering the tag allowlist, issue-id validation,
      out-of-project rejection, per-surface degradation, the all-surfaces 502,
      the audit-log write, and the `lineNo=None` frame guard.
- [x] **Production build RUN and passing:** `npm run build` in `admin-dashboard`
      → exit 0, full route table emitted including `/dashboard/sentry-logs`.
      `npx tsc --noEmit` reports no errors in any Sentry file (the errors it does
      report are pre-existing, in unrelated `*.test.tsx` files). `npx eslint` on
      the two changed frontend files: 0 errors, 2 warnings (both the pre-existing
      `set-state-in-effect` pattern).
- [x] **Route registration verified** by importing the app: all four
      `/api/admin/sentry/*` routes register cleanly with the new rate-limit
      decorators and `require_super_admin` handler dependencies.
- [x] **Backend lint:** `ruff check` and `ruff format --check` clean on
      `routes/admin/sentry.py`, `routes/admin/__init__.py`, `core/config.py`,
      `tests/test_admin_sentry.py`.
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

- **The build ran against a hardlinked `node_modules`** copied from an existing
  install in the same container, not a fresh `npm ci`. The compile is real, but
  a dependency-resolution problem would not show up this way — CI's own install
  is still the authority on that.
- **Only `test_admin_sentry.py` was run**, not the full backend suite. The
  change is additive to a new module plus one appended router mount, so the
  blast radius on other tests is a mount-ordering change at most — but the
  full-suite result comes from CI, not from here.
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
- [x] **Previously blocked on** `npm run build` (admin-dashboard) and
      `pytest backend/tests/test_admin_sentry.py`. Both have now been run and
      pass — see §9. The pytest run initially failed (2 of 22), which is what
      surfaced fix #1 in §8b.
- [x] Review-round fixes in §8b are each covered by a test or a build/lint run,
      except the rate limits (#8), which are a slowapi decorator exercised only
      by route registration — the limiter itself is covered by the repo's
      existing rate-limiter tests.
