# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Owner directive from live testing (screenshot: Ride Details, caption under the map) |

## 1. Issue / gap identified

The rider's Ride Details screen printed a route-provenance caption under the
map — e.g. `Actual route · Route reconstructed · 59% GPS observed · 41%
inferred`. Coverage percentages and reconstruction status are operator
diagnostics; riders have no action to take on them. Owner directive: remove it
from the rider app, keep it in the admin panel.

## 2. Root cause

Not a defect — a product decision. The caption was added alongside the v2 route
work (`docs/change-log/2026-08-20-route-map-plotting-hardening.md`) and shipped
to every rider/driver ride-detail surface at once, without a separate call on
whether the *numeric* provenance detail belonged on a rider screen.

## 3. Fix / remediation

Removed the caption `<Text>` from `rider-app/app/ride-details.tsx`, plus the
three locals (`routeLabel`, `routeQuality`, `routeIsProcessing`) and the
`routeQualityText` style that existed only to build it.

Nothing was added to the admin panel: it already renders
`routeQualityLabel(ride.route_quality)` in two places —
`admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx`
line 727 (the "Actual Trip" phase-card subtitle, shown for every v2 ride) and
line 806 (the map label when the Actual phase is selected). Verified before
removing, so no diagnostic capability is lost.

## 4. Risk & impact on existing functionality

- **Blast radius: one screen, presentation only.** No data contract changed —
  `route_quality` is still returned by the API, still stored, still read by
  admin and by the emailed receipt. Nothing is deleted or stopped being
  computed.
- **`routeQualityLabel` has five call sites**; exactly one is removed. Left
  untouched: `rider-app/app/ride-details.tsx` line 80 (the **emailed/PDF
  receipt** still prints `Actual route (revision N) · <quality>` and
  `Route snapshot unavailable · <quality>`), `rider-app/app/ride-completed.tsx`
  line 144 (the **post-trip** screen's in-map status pill), and
  `driver-app/app/driver/ride-detail.tsx` line 156. The shared helper in
  `shared/utils/routeSegments.ts` is unmodified.
- **The honesty-label concern, and why this is safe.** That helper's docstring
  calls it "approved quality copy" and the 2026-08-20 log calls these "Spinr's
  honesty labels" — one branch (`distance_basis === 'planned_estimated'` →
  "Distance estimated from booking · GPS incomplete") is a deliberate
  fare-transparency disclosure from a real incident. Removing the caption does
  **not** remove that disclosure, because the fare line carries it
  independently: `relabel_booked_distance_lines()` in
  `backend/routes/rides/_shared.py` relabels the served breakdown to
  `Ride fare (X km booked)` whenever the charged distance diverges from the
  GPS-measured one — visible in the reporting screenshot itself
  ("Ride fare (7.7 km booked)" beside a 4.1 km stats tile). Where the two
  conditions don't overlap, booked ≈ measured, so there is no discrepancy left
  to explain.
- **Layout:** the caption carried `marginTop: -10, marginBottom: 16` and sat
  between `mapCard` (`marginBottom: 16`) and `routeCard`. With it gone the map's
  own 16px bottom margin sets the gap — the same spacing used between every
  other card on the screen.
- Two rider-meaningful states also disappear from this screen: "Actual route
  processing" (route still being finalized) and the imported-ride
  "no GPS was recorded" note. The Imported badge itself (line ~359, driven by
  the same `isImported` flag) is **unchanged** and still explains an empty map
  for legacy rides. Called out as a deliberate consequence, not an oversight —
  see §10.

## 5. User-experience effect

- **Rider-facing.** Not visible mid-ride: the caption only rendered under
  `isCompleted`, i.e. on a finished ride's detail screen.
- After: the map sits directly above the pickup/dropoff card. No other content
  moves.
- Driver, corporate-admin and internal-admin surfaces: no change. Emailed
  receipt: no change.
- No copy added; one line of copy removed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-details.tsx` | Removed the caption `<Text>`, the `routeLabel`/`routeQuality`/`routeIsProcessing` locals, and the `routeQualityText` style | The three locals had no other consumer; the style was orphaned by the removal |
| `rider-app/__tests__/ride-details-route.test.tsx` | Dropped the assertion pinning the now-deleted `routeLabel` ternary; added a test pinning the diagnostics *out* of the rider screen while keeping `routeQualityLabel` for the receipt | The old assertion would fail; the new one stops the caption returning silently |

## 7. Before / after

```tsx
// Before
{isCompleted && (
  <Text style={styles.routeQualityText}>
    {isImported
      ? 'Imported from the previous app — no GPS was recorded for this ride'
      : hasActualRoute
        ? `${routeLabel} · ${routeQuality}`
        : isV2Route
          ? routeIsProcessing ? 'Actual route processing' : 'Actual route unavailable'
          : `${routeLabel} · Planned route preview`}
  </Text>
)}
```

```tsx
// After — the map is followed directly by the route card.
// routeQualityLabel() still imported and used by the emailed receipt above.
```

## 8. Rollback plan

`git revert` is a complete rollback. Presentation-only, client-side, writes
nothing, no migration, no live data touched. Not feature-flagged: it is a
one-line removal at an owner's direction, and shipping it dark would leave the
caption in place for the flag-off cohort. Reaching riders needs an OTA/EAS
update like any other rider-app change, so rollback uses the same mechanism as
rollout.

## 9. Verification performed

- [x] Confirmed the admin panel already shows this diagnostic **before**
      removing it from the rider app (two call sites in `ride-detail-modal.tsx`,
      cited in §3).
- [x] Blast-radius grep: `routeQualityLabel` (all 5 call sites, enumerated in
      §4), `routeLabel` / `routeQuality` / `routeIsProcessing` /
      `routeQualityText` across `rider-app/`, and
      `isCompleted`/`hasActualRoute`/`isV2Route`/`showPlannedUnderlay`/
      `isImported` to confirm the removal orphaned no other local.
- [x] Traced the fare-basis disclosure to `relabel_booked_distance_lines()` in
      the backend, establishing that it survives this removal (§4).
- [x] `tsc --noEmit --noResolve` parse pass clean on the modified screen.
- [x] Read the existing source-contract test and found the one assertion this
      removal breaks (`"? (showPlannedUnderlay ? 'Booked route' : 'Actual
      route')"`, pinning the deleted `routeLabel` ternary); updated it.
- [ ] **Jest NOT run; no typecheck, lint, or production build run.**
      `registry.npmjs.org` and `registry.yarnpkg.com` are blocked by this
      session's egress policy (403 on CONNECT), so rider-app dependencies could
      not be installed. The updated contract test is committed unexecuted and
      must pass in CI.
- [ ] Manual check on device/simulator — not performed.

## 10. What was NOT verified

- The updated contract test has never been executed. It is a source-string test,
  so it will fail loudly rather than pass vacuously if my edit missed something —
  but that is reasoning, not an observed run.
- **Scope was decided, not confirmed.** The directive named the screen in the
  screenshot (Ride Details). Two other rider-facing surfaces still print the
  same `routeQualityLabel` string and were deliberately left alone:
  `ride-completed.tsx`'s post-trip in-map pill and the emailed/PDF receipt.
  Whether those should follow is an open product call, not a bug.
- No screenshot or visual diff of the resulting spacing. Per `CLAUDE.md` release
  gate #6, rider-app has no visual-regression tooling; the 16px gap was reasoned
  from the stylesheet (`mapCard.marginBottom`), not observed.
- Removing the "Actual route processing" state means a rider viewing a
  just-finished ride whose geometry is still finalizing now sees a bare map with
  no explanation. That is a real, accepted consequence of the directive — not
  measured against how often the finalizer is still pending at view time.
