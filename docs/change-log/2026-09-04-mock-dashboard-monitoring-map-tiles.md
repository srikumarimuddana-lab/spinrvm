# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (follow-up to `ACTION_ITEMS.md` B38) |
| Surface(s) | admin-dashboard, CI |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/weekly-payout-audit-tsdnxg` |
| Related issue or gap ID | `ACTION_ITEMS.md` B38 — the concrete remaining step from #4936/#4937 |

## 1. Issue / gap identified

`dashboard-monitoring`'s visual-regression baseline depended on the CI
runner's live network reachability to `tiles.openfreemap.org` at
capture/compare time. Two runs against the same commit produced different
results (a normal basemap vs. a red "Failed to load map style" error),
purely from runner network variance — found and documented in #4936, which
deliberately left `visual-regression-test`'s `continue-on-error: true` on
rather than flip it with this known flakiness unaddressed.

## 2. Root cause

`src/lib/map/maplibre-base.ts`'s `MAP_STYLE_URL` points at a real, external
OpenFreeMap style endpoint with no mock in `admin-mocks.ts` — unlike every
`/api/**` call, which that file exists specifically to intercept.

## 3. Fix / remediation

Added a `page.route()` stub for `**/tiles.openfreemap.org/**` in
`visual-regression.spec.ts`, mirroring the `/api/**` interception pattern
`admin-mocks.ts` already uses. The stub serves a minimal, self-contained
MapLibre GL style (`version: 8`, empty `sources`, one `background` layer) —
no `sources`/`glyphs`/`sprite` keys means MapLibre never needs to fetch
anything beyond that one JSON response, so the map deterministically
finishes loading (fires MapLibre's `load` event) with zero live network
dependency, rather than depending on reaching an external host.

**This changes `dashboard-monitoring`'s rendered baseline.** The
already-committed screenshot shows a real basemap (fetched from the live
tile provider at capture time); with the stub in place, the map now renders
a flat background instead. The existing baseline PNG needs to be
regenerated against this change before it's a correct reference — tracked
as the immediate next step (a fresh `update-visual-baselines.yml` run,
followed by replacing just the `dashboard-monitoring` PNG).

## 4. Risk & impact on existing functionality

- **Blast radius: test-only, one file.** `visual-regression.spec.ts` is
  Playwright test code; the route stub only intercepts requests made while
  this spec's own tests run (Playwright routes are page-scoped, not global)
  and only affects that one host. No `src/` file changed, no other spec's
  behavior affected — confirmed `venues.spec.ts` already independently
  aborts requests to the same host for its own, different purpose, and this
  change doesn't touch that file.
- No production code touched. The real app still calls the real
  `tiles.openfreemap.org` in production; only this test's simulated browser
  environment is stubbed.

## 5. User-experience effect

None. Test-only infrastructure change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/e2e/visual-regression.spec.ts` | Added a `page.route()` stub for `tiles.openfreemap.org` serving a minimal self-contained MapLibre style; updated the file-header doc comment | Remove the CI-runner-network-dependent flakiness in `dashboard-monitoring`'s baseline |

## 7. Before / after

```ts
// Before: dashboard-monitoring's map made a real network call to
// tiles.openfreemap.org during the screenshot test -- succeeds or fails
// depending on the CI runner's network reachability at that moment.

// After:
await page.route('**/tiles.openfreemap.org/**', (route) =>
  route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      version: 8,
      sources: {},
      layers: [{ id: 'background', type: 'background', paint: { 'background-color': '#e2e2e2' } }],
    }),
  })
);
```

## 8. Rollback plan

`git-revert-safe`. No migration, no production code. Reverting restores the
prior network-dependent behavior exactly.

## 9. Verification performed

- [x] `npx tsc --noEmit` clean on the changed file.
- [x] Real production build (`npm run build`) — clean.
- [x] `npx playwright test --project=visual-regression --list` — all 6
      tests still collected correctly, spec parses without error.
- [ ] The stub's actual effect (map renders successfully, no "Failed to
      load map style" error) has **not** been verified against a real
      browser run in this session — this sandbox's Chromium revision
      doesn't match CI's pinned one (same constraint documented in the
      2026-07-29 and 2026-09-02 change-log entries for this same
      surface), so this can only be confirmed by a real CI run. That run
      is also what regenerates the `dashboard-monitoring` baseline PNG to
      match the new deterministic render — both happen together in the
      next `update-visual-baselines.yml` run.
- [ ] Manual repro / staging check — not applicable, test-only change.

## What was NOT verified

- Whether MapLibre GL actually accepts a `sources`-less style without any
  other runtime error was reasoned from the MapLibre style spec (`sources`
  defaults to `{}`, a `background`-type layer needs no source) and from
  this being a standard minimal-style pattern, not confirmed by an actual
  render in this session.
