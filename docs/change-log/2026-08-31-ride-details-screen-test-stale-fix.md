# Change Impact & Risk Log — fix stale rideDetailsScreen.test.tsx assertions

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Author | Claude (this session) |
| Surface(s) | rider-app (test file only — no production code changed) |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `fix-ride-completed-actual-route-processing` |
| Related issue or gap ID | Test regression surfaced while verifying #4663 (jest 30.4.x Dependabot fix) on current `main` |

## 1. Issue / gap identified

`rider-app/__tests__/rideDetailsScreen.test.tsx` had 5 failing tests on current
`main` (1 suite red), all asserting UI text that no longer exists on-screen:
a map route-status pill ("Actual route · <quality>", "Actual route
unavailable", "Actual route processing", "Planned route · Planned route
preview") and a "no GPS was recorded" disclaimer sentence.

## 2. Root cause

Not a regression — a stale sibling test file. `327d3e845`
("refactor(rider-app): drop the route-provenance caption from Ride Details",
2026-08-30) deliberately removed the entire `<Text style={styles.routeQualityText}>`
block from `rider-app/app/ride-details.tsx` per an explicit owner directive
("coverage percentages and reconstruction status are operator diagnostics —
not something a rider can act on; keep it in the admin panel"). That commit
correctly updated `rider-app/__tests__/ride-details-route.test.tsx` (a
source-contract test on the same screen) to match, but never touched
`rideDetailsScreen.test.tsx`, which still asserted the deleted UI. Confirmed
by diffing the commit and by checking out `ride-details.tsx` at #4557 (the
commit that originally built this UI, when the full 59-test suite passed) —
the text was real and rendering then; it was deliberately deleted since, not
broken by drift.

## 3. Fix / remediation

Test-only change, no production code touched:
- Updated the "Imported" badge test to only assert the badge (still renders),
  dropping the now-nonexistent disclaimer-sentence assertion.
- Replaced the 4 tests asserting the deleted route-status pill's text
  variants with one regression-guard test asserting those strings are
  genuinely absent from the rendered screen — mirroring the intent of
  `ride-details-route.test.tsx`'s own regression test added in the same
  removal commit ("keeps route-provenance diagnostics off the rider screen").
- Left the map-rendering assertions (MapView presence, Polyline count,
  `fitToCoordinates` call) in the same test untouched — those still pass and
  still cover real, shipped behavior.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated to one test file.** No production code in
  `rider-app/app/ride-details.tsx` or any other file was changed.
- Nothing else reads/writes this test file; it has no runtime effect on the
  app, backend, or any background loop.
- The underlying UI removal this fix catches up to was already reviewed and
  logged separately: `docs/change-log/2026-08-30-rider-route-provenance-caption-removed.md`.
  This PR does not re-litigate that decision — it only fixes CI to reflect it.

## 5. User-experience effect

None. No app code changed. Riders see whatever `327d3e845` already shipped;
this PR only makes the test suite agree with it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | "Imported" badge test drops the disclaimer assertion; 4 tests for the deleted route-status pill replaced with 1 regression-guard test | Match already-shipped, already-reviewed UI removal (327d3e845) |

## 7. Before / after

```tsx
# Before
it('shows "Actual route processing" for a v2 ride with no actual route yet while geometry is still processing', async () => {
  mockApiGet.mockResolvedValue({
    data: { ...RIDE_WITH_COORDS, route_schema_version: 2, route_geometry_status: 'processing' },
  });
  const r = await renderScreen();
  expect(allText(r)).toContain('Actual route processing');
});
```

```tsx
# After
it('never re-renders the removed route-status pill text on the rider screen', async () => {
  mockApiGet.mockResolvedValue({
    data: { ...RIDE_WITH_COORDS, route_schema_version: 2, route_geometry_status: 'processing' },
  });
  const r = await renderScreen();
  const text = allText(r);
  expect(text).not.toContain('Actual route processing');
  expect(text).not.toContain('Actual route unavailable');
  expect(text).not.toContain('Planned route preview');
});
```

## 8. Rollback plan

`git revert` is sufficient and complete — test-only change, no data or
runtime state involved.

## 9. Verification performed

- [x] Automated tests: rider-app full suite, fresh install off current
  `main` (not a stale branch) — 134/134 suites passed, 1894/1894 tests
  passed (previously 133/134 suites, 1891/1896 tests, 5 failing).
- [x] `npx tsc --noEmit` clean.
- [x] **Real production build**: not applicable — no production code
  changed, so no `expo export`/build was run for this PR. (The underlying
  UI change was already verified that way in `327d3e845`'s own log.)
- [x] Blast-radius grep: confirmed no other file references the removed
  strings/locals (`routeQualityText`, `routeIsProcessing` as used in
  `ride-details.tsx`) outside the two test files already accounted for.
- [x] Reviewed against CLAUDE.md conventions: test-only, no state-machine,
  money, or RLS surface touched.
- [ ] Not verified: on-device/visual — n/a, no UI changed by this PR.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`).
- [x] Blast radius stated: isolated to one test file, zero production code.
- [x] No silent behavior change — this PR has no behavior change of its own;
  it reconciles tests with a behavior change already reviewed/logged
  separately in `327d3e845`.
