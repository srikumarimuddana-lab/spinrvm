# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (backlog item B38, requested by the user during a latency/architecture advisory session) |
| Surface(s) | admin-dashboard, CI |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/weekly-payout-audit-tsdnxg` |
| Related issue or gap ID | `ACTION_ITEMS.md` B38 — visual-regression CI job has existed since 2026-07-29 with zero committed baselines, a documented no-op |

## 1. Issue / gap identified

`admin-dashboard/e2e/visual-regression.spec.ts` and its CI wiring
(`visual-regression-test` in `ci.yml`) have existed since 2026-07-29, but no
baseline screenshots were ever committed — the job self-skips with a
`::notice` on every run, so it has never actually compared a single
screenshot. B38 tracked this as an open gap.

## 2. Root cause

Never seeded. Not a regression — a one-time manual setup step
(`update-visual-baselines.yml`, deliberately `workflow_dispatch`-only so
baselines come from CI's own Chromium build rather than a developer's local
machine) that nobody had triggered.

## 3. Fix / remediation

- User triggered `update-visual-baselines.yml` against `main` (this
  session's own GitHub integration lacks Actions-dispatch scope — confirmed
  via a `403 Resource not accessible by integration` — so this step needed
  a human with real dispatch access; see B38's updated status).
- Downloaded the resulting artifact (GitHub serves it from Azure Blob
  Storage, which this session's network policy doesn't allow directly — the
  user downloaded and attached it manually) and visually reviewed all 6
  captured screenshots before committing anything.
- **5 of 6 pages seeded**: `login`, `dashboard-home`, `dashboard-drivers`,
  `dashboard-monitoring`, `dashboard-settings` all rendered correctly (full
  chrome, real empty/loading states, no crashes, no secrets — the settings
  page shows only placeholder credential values like `pk_test_...`).
- **`dashboard-rides` deliberately NOT seeded.** It rendered the
  `dashboard/error.tsx` boundary ("Something went wrong") instead of the
  actual page. Root-caused rather than worked around: `admin-mocks.ts`'s
  generic `/api/**` fallback returns `{ items, data, total, page, per_page
  }`, but `getRides()` (`src/lib/api/rides.ts`) expects `{ rides,
  total_count, limit, offset }` — the same class of gap the file already
  had one prior fix for (`/api/admin/service-areas`, with its own comment
  explaining why). `res.rides` came back `undefined`, and a child component
  crashed rendering it. Fixed by adding the matching special case for
  `/api/admin/rides` (mirrors the existing `service-areas` case exactly).
- **Did not commit a baseline for the fixed page in this change** — the fix
  only corrects the mock going forward; the artifact already in hand still
  has the crash screenshot for that page. Capturing the corrected page needs
  one more `update-visual-baselines.yml` run, tracked as the remaining step
  in B38.
- Left `continue-on-error: true` on `visual-regression-test`
  (`ci.yml`) unchanged — flipping it to blocking is intentionally deferred
  until all 6 pages have real baselines, per B38's acceptance criteria.

## 4. Risk & impact on existing functionality

- **Blast radius: `admin-mocks.ts` is test-only infrastructure** — imported
  by Playwright e2e specs (`ride-management.spec.ts`,
  `visual-regression.spec.ts`, and others via `setupAdminMocks`), never
  shipped to production. Grepped for every spec importing it; the new
  `/api/admin/rides` case only changes behavior for specs that (a) hit that
  endpoint and (b) don't already override it via `opts.extra` — an override
  always wins (checked first), so no existing spec's behavior changes
  unless it was relying on the old, broken fallback shape, which would have
  been failing already.
- **No production code touched.** `admin-mocks.ts`, the spec file's doc
  comment, and the new snapshot PNGs are the only changes — zero `src/`
  files modified.
- The mock fix does not change what the real backend returns — only what
  the *test* backend simulates, to match what the real one already returns
  (`routes/admin/rides.py`'s actual response shape, mirrored by
  `RideListOpts`'s TypeScript return type already declaring `{ rides,
  total_count, limit, offset }`).

## 5. User-experience effect

None. Test-only infrastructure and CI configuration; no rider/driver/admin
facing behavior changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/e2e/visual-regression.spec.ts-snapshots/*.png` | 5 new baseline screenshots (login, dashboard-home, dashboard-drivers, dashboard-monitoring, dashboard-settings) | Seed real visual-regression coverage for the first time |
| `admin-dashboard/e2e/admin-mocks.ts` | Added a `/api/admin/rides` special case matching `getRides()`'s expected response shape | Fix a real mock-fixture bug found while capturing the `dashboard-rides` baseline — the generic fallback shape crashed the page |
| `admin-dashboard/e2e/visual-regression.spec.ts` | Updated the file-header doc comment to reflect the 5/6 seeded state and the `dashboard-rides` follow-up | Keep the comment accurate instead of stale |
| `ACTION_ITEMS.md` | Updated B38's status to partially-closed with the concrete remaining step | Track the real, current state of the backlog item |

## 7. Before / after

```ts
// Before (admin-mocks.ts): no /api/admin/rides case, falls through to:
return json(200, { items: [], data: [], total: 0, page: 1, per_page: 20 });
// getRides() reads res.rides -> undefined -> child component crashes on .map()

// After:
if (url.includes('/api/admin/rides') && method === 'GET') {
  return json(200, { rides: [], total_count: 0, limit: 25, offset: 0 });
}
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema change, no production code. If
the seeded baselines turn out wrong for any reason, either revert this
commit (returns the job to its prior self-skipping no-op state) or delete
the specific PNG(s) in question — the CI job's own "Check for committed
visual baselines" step degrades gracefully either way.

## 9. Verification performed

- [x] Visually reviewed all 6 captured screenshots before committing
  anything — 5 confirmed correct (real content, no crashes, no secrets/PII:
  settings page shows only placeholder credential values), 1
  (`dashboard-rides`) confirmed broken and excluded, root-caused instead of
  silently worked around.
- [x] Confirmed the artifact's source commit (`37675df`) is on current
  `main`'s history, and diffed it against current `main` to check for
  staleness in the shared primitives the spec targets (sidebar,
  page-header, card, badge) — unchanged since capture. `dashboard-monitoring`
  and `dashboard-settings` page code did change since capture (globals.css
  too, a small addition) — flagged here rather than silently assumed fresh;
  first real comparison run may show expected drift on those two pages
  that a maintainer re-baselines after reviewing.
- [x] `npx tsc --noEmit` clean on the `admin-mocks.ts` change (no reported
  errors for the file).
- [x] Real production build (`npm run build`, not just a dev server or
  `tsc --noEmit` alone) — clean, `/dashboard/rides` and every other route
  still build.
- [ ] `npm run lint` — **not run successfully**; a pre-existing,
  environment-specific ESLint crash (`TypeError: Error while loading rule
  'react/display-name': contextOrFilename.getFilename is not a function`)
  reproduces identically on files this change never touched (confirmed via
  `git stash` + rerun, crashing on `.storybook/main.ts` instead) — a sandbox
  tooling bug, not something introduced here.
- [x] Blast-radius grep: confirmed `admin-mocks.ts`'s only consumers are
  Playwright e2e specs via `setupAdminMocks`, and that spec-level `extra`
  overrides are checked before the new fallback case, so no existing spec
  changes behavior unless it was already relying on the broken shape.
- [ ] Manual repro / staging check — not applicable, test-only change.

## What was NOT verified

- The two pages flagged as changed-since-capture (`dashboard-monitoring`,
  `dashboard-settings`) were not re-diffed pixel-by-pixel against current
  `main` — only their source diff was reviewed. The first real CI
  comparison run will be the actual test of whether they still match.

## 10. Follow-up (2026-09-03): dashboard-rides seeded, dashboard-monitoring flakiness found and left un-gated

Triggered `update-visual-baselines.yml` a second time, this time against
the PR branch (`claude/weekly-payout-audit-tsdnxg`, which carries the
`admin-mocks.ts` rides fix) instead of `main`, to get a corrected
`dashboard-rides` screenshot before merge rather than after.

- **`dashboard-rides` now renders correctly**: full stat cards, filters,
  "No rides found" empty state, Create Ride button — no crash. Reviewed
  and added as the 6th baseline.
- **`dashboard-monitoring` flakiness discovered, not fixed.** Comparing
  this run's `dashboard-monitoring` capture against the one already
  committed (same commit's page code, different CI run): the first showed
  a normal empty basemap; this one showed a red "Failed to load map style.
  Check network / tile provider." error instead. Root cause: the map panel
  (`src/lib/map/maplibre-base.ts`, `MAP_STYLE_URL` = live
  `tiles.openfreemap.org`) has no mock in `admin-mocks.ts` — unlike every
  `/api/**` call, which the whole file exists to intercept — so its
  screenshot depends on the CI runner actually reaching an external host
  at capture time, and evidently doesn't always. **Did not overwrite the
  already-good, already-merged `dashboard-monitoring` baseline with this
  run's broken-map capture** — kept the working one, only added
  `dashboard-rides` from this run.
- **Did not flip `continue-on-error` to `false`** on `visual-regression-test`
  despite all 6 pages now having baselines, specifically because of this
  finding — doing so now would make the gate intermittently red for
  reasons unrelated to any future PR's diff, the exact "decayed gate"
  problem `CLAUDE.md`'s CI-red discipline warns against, self-inflicted
  before the gate even started blocking anything. Documented as the
  concrete remaining step in `ACTION_ITEMS.md` B38: mock/stub the tile
  fetch via `page.route()` on `tiles.openfreemap.org`, mirroring this
  file's existing `/api/**` interception pattern, before flipping the gate.
- Updated `ci.yml`'s job comment and `visual-regression.spec.ts`'s header
  comment to state this reasoning inline, not just here, so a future
  reader of either file sees why the gate still isn't blocking with all 6
  baselines present.

### Verification performed (this follow-up)

- [x] Visually reviewed the `dashboard-rides` screenshot — confirmed
  correct, no crash, no PII/secrets.
- [x] Diffed the run's source commit against current `main` at merge time:
  only 3 files changed (`compliance/page.tsx`, not screenshotted;
  `monitoring/ride-panel.tsx`, a side panel only visible on driver
  selection, not visible in the captured "no selection" state;
  `ride-detail-modal.tsx`, a closed modal) — none affect any of the 6
  baseline images' visible content.
- [x] Compared this run's `dashboard-settings` capture against the
  already-committed one — structurally identical (same fields, same
  placeholder values), confirmed no regression, left the committed one
  unchanged rather than churn the diff for no reason.
- [x] Confirmed no existing tile-mocking infrastructure was missed before
  concluding this is a real gap: grepped `admin-mocks.ts` and
  `visual-regression.spec.ts` for `tile`/`maplibre`/`openfreemap`/`route` —
  zero matches.
