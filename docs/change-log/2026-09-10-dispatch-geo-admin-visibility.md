# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-10 |
| Author | Claude Code (spinr session) |
| Surface(s) | backend, admin-dashboard |
| Domain (Sentry tag) | dispatch / admin |
| PR / commit link | (branch `mvapps/sleepy-galileo-hqp2xn`) |
| Related issue or gap ID | feat/49 (dispatch geo status admin visibility) |

## 1. Issue / gap identified

The dispatch geo-provider system (legacy bounding-box / PostGIS / H3, with
automatic failover between them — live in production since migration 404,
2026-09-04) had zero admin visibility. `admin_dispatch_geo_status()` — a
fully built, fully tested status function — had no route calling it, and
the existing `POST /dispatch-geo/rebuild` action had no UI. An operator
could not see which provider is configured, which is actually serving, or
whether a failover is in effect, without reading logs/metrics directly.

## 2. Root cause

The status function and rebuild endpoint were built as part of the H3
dispatch-geo-indexing work (commit `fc6f922`) but the admin-facing surface
was never finished — confirmed by grepping the whole backend and
admin-dashboard for callers/consumers before starting (`admin_dispatch_geo_status`
had exactly one caller: its own test).

## 3. Fix / remediation

- **Backend**: new `GET /api/admin/monitoring/dispatch-geo` route, a thin
  wrapper around the existing `admin_dispatch_geo_status()`.
- **Frontend**: new `/dashboard/monitoring/dispatch-geo` page — configured
  vs. effective provider, H3 readiness/blockers, last failover, recent
  events, and a "Rebuild H3 Index" action wired to the existing rebuild
  endpoint. Sidebar + command-palette entries added (module: `settings`,
  matching the sibling Redis & Infra page's gate exactly).
- **Tests**: backend unit test for the new route; new Playwright
  interaction tests; added to the a11y baseline and crawl-audit route
  lists.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated / additive-only.** No existing route, function,
  or component was modified — every change either adds a new file or adds
  a line to a list (imports, nav entries, route/baseline lists). Verified
  by grep before starting: `admin_dispatch_geo_status` and the rebuild
  route were unused/UI-less; nothing else calls the new frontend API
  functions.
- **Deliberately not touching `/dashboard/monitoring`** (the live ride-map
  page) — it is one of 6 pages with a CI-blocking visual-regression
  baseline (`e2e/visual-regression.spec.ts`). This feature lives at a new
  sibling route, `/dashboard/monitoring/dispatch-geo`, following the exact
  precedent of `/dashboard/monitoring/redis` (confirmed neither sub-route
  is in the visual-regression suite's 6-page list). Result: **no visual
  baseline needs re-capturing** for this change — verified against the
  spec file directly, not assumed.
- No new background loop, no schema change, no money/wallet/ride-state
  code touched. The rebuild action button calls an endpoint that already
  existed and was already safe to call anytime (see its own docstring:
  never deletes cell keys, bypasses the leader lock deliberately).
- Sidebar/command-palette files are shared (`sidebar.tsx` referenced by
  ~23 other files per the repo's own pre-commit hygiene check) — the
  change to each is a single new array entry, same shape as its immediate
  neighbor; no existing entry was reordered or modified.

## 5. User-experience effect

Internal-admin only. New "Dispatch Geo Status" entry in the System nav
group and command palette, visible to admins with the `settings` module
(same audience as "Redis & Infra"). No rider/driver/corporate-facing
change. Not visible mid-session to anyone already using the app — it's a
net-new page, not a change to an existing screen.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/monitoring.py` | New `GET /dispatch-geo` route | Expose the existing, tested status function |
| `backend/tests/test_admin_monitoring_coverage.py` | New test for the route | Coverage for the new endpoint |
| `admin-dashboard/src/lib/api/live-monitoring.ts` | New `getDispatchGeoStatus()`, `rebuildDispatchGeoIndex()`, types | Typed client for the new route + existing rebuild route |
| `admin-dashboard/src/lib/api.ts` | Re-export the new functions/types | Match existing barrel-export pattern |
| `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/page.tsx` | New page | The admin-visibility UI itself |
| `admin-dashboard/src/app/dashboard/monitoring/dispatch-geo/loading.tsx` | New loading skeleton | Match sibling page convention |
| `admin-dashboard/src/components/sidebar.tsx` | One new nav entry + `Compass` icon import | Make the page reachable |
| `admin-dashboard/src/lib/command-palette-routes.ts` | One new route entry | Command-palette parity |
| `admin-dashboard/e2e/a11y-baseline.json` | One new key (`: 0`) | Track a11y violation count for the new route |
| `admin-dashboard/e2e/crawl-audit.spec.ts` | One new route in the crawl list | Crawl coverage |
| `admin-dashboard/e2e/monitoring.spec.ts` | New `describe` block, 2 tests | Interaction coverage for the new page |

## 7. Before / after

Purely additive — no existing behavior changed. Skipped per template
guidance ("skip for pure additive code with no existing caller").

## 8. Rollback plan

`git revert` is complete and sufficient: no migration, no flag, no data
written. The new route and page simply cease to exist; the nav/
command-palette/test-list entries are single-line removals with no
follow-up cleanup.

## 9. Verification performed

- [x] Automated tests run: backend — `pytest tests/test_admin_monitoring_coverage.py tests/test_dispatch_candidates.py tests/test_monitoring_health.py` (47/47 passed). Frontend — `tsc --noEmit` (clean, whole project), `eslint` on every changed file (0 errors; 2 pre-existing warning patterns matched exactly, none new).
- [x] Manual repro / real browser check: started the actual Next.js dev server and ran the new Playwright interaction tests (`e2e/monitoring.spec.ts -g dispatch-geo`) against it — both new tests pass, plus the full `monitoring.spec.ts` file (10/10) to confirm no regression to the sibling redis/live-map pages.
- [x] Blast-radius grep performed: `admin_dispatch_geo_status`, `_postgis_ids`/`_rows_for_ids`-style checks for every new function's callers before wiring anything in; confirmed sidebar.tsx/command-palette-routes.ts edits are pure additions, not modifications to existing entries.
- [x] Reviewed against relevant CLAUDE.md conventions: admin-dashboard visual-regression gate (explicitly checked which of the 6 seeded pages could be affected — none), dual-import pattern (matched in the new backend route), Sentry/observability tagging conventions (no new capture paths — read-only status route, no new error class).
- [x] **Real production build (`npm run build`) — run and passed.** Exit 0, no error/fail lines in the build log, `/dashboard/monitoring/dispatch-geo` appears correctly in the compiled route list alongside every other route.
- [x] Feature-flagged if user-visible and non-trivial — not applicable/not needed: this is a new, isolated internal-admin page gated by the existing `settings` module permission, not a change to any existing flow.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, nothing else to clean up)
- [x] Blast radius is stated, not assumed: additive-only across 11 files, verified via grep for every new function's callers before wiring
- [x] No silent behavior change to an already-shipped flow: this is a net-new page; nothing existing changed behavior
