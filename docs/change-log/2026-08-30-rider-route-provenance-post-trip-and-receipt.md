# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session: rider-textbox-visibility) |
| Surface(s) | rider-app |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/rider-textbox-visibility-d4w9lv` |
| Related issue or gap ID | Owner directive — follow-up to `2026-08-30-rider-route-provenance-caption-removed.md` |

## 1. Issue / gap identified

Commit `327d3e8` removed the route-provenance caption from the rider's **Ride
Details** screen only. The same `routeQualityLabel()` copy was still printed on
two other rider-facing surfaces: the **post-trip screen**'s in-map status pill
and the **emailed/PDF receipt**. Owner directive: remove those too.

A second, unintended finding: `327d3e8` broke four existing tests in
`rideDetailsScreen.test.tsx` that asserted on the caption it deleted. That
commit's own change log claimed the blast-radius grep covered the test surface;
it checked `ride-details-route.test.tsx` and missed the screen test. Fixed here
— see §10.

## 2. Root cause

Not a defect — the same product decision as the previous entry, applied to the
surfaces the first pass deliberately scoped out. `routeQualityLabel` had five
call sites; the first commit removed one.

## 3. Fix / remediation

**Post-trip (`ride-completed.tsx`)** — removed the `mapRouteStatus` pill (icon +
`{routeLabel} · {routeStatus}`) from the map overlay. The pickup/dropoff address
rows in the same overlay are untouched. The `routeLabel` / `routeQuality` /
`routeIsProcessing` / `routeStatus` chain and the two orphaned styles go with
it, as does the now-unused `routeQualityLabel` import.

**Emailed receipt (`ride-details.tsx`)** — removed the caption `<p>` above each
snapshot image. Structure and URL-selection logic are otherwise unchanged, so
the **never-render-a-stale-snapshot** rule still holds: a v2 ride whose
`snapshot_revision` does not match `route_revision` renders nothing, exactly as
before, it just no longer explains itself with "Route snapshot unavailable ·
`<quality>`". `routeRevision` is retained — it still gates `isActualSnapshot`.
The actual/planned distinction is kept in the image `alt` text for screen
readers, which is not visible caption copy.

`routeQualityLabel` now has **zero rider-app consumers**. It remains exported
from `shared/utils/routeSegments.ts` for the surfaces that still want the
diagnostic: `admin-dashboard/.../ride-detail-modal.tsx` (2 call sites) and
`driver-app/app/driver/ride-detail.tsx` (1).

## 4. Risk & impact on existing functionality

- **Blast radius: two rider screens, presentation only.** No API contract, no
  stored field, no backend behaviour. `route_quality` / `route_revision` /
  `snapshot_revision` are still served and still consumed elsewhere.
- **`shared/utils/routeSegments.ts` is unmodified** — the shared helper keeps
  its exact behaviour for admin and driver. This is a call-site removal, not a
  helper change, so the driver and admin surfaces cannot regress.
- **Receipt URL selection is byte-identical in effect.** The three-branch chain
  collapsed only its caption strings; the same three conditions still pick the
  same URL (matching-revision v2 snapshot → that image; other v2 → nothing;
  legacy with a URL → that image; else nothing). Pinned by the existing
  `not.toContain('stale.png')` assertion, kept.
- **Layout:** the post-trip overlay's pill had `marginBottom: 7`; removing the
  whole row leaves the overlay's own `padding: 10` setting the top inset, with
  the address rows unchanged. The receipt loses a `<p>` above an `<img>` that
  had `margin:0 0 6px`.
- The "Actual route processing" and "Actual route unavailable" states disappear
  from the post-trip screen too, same accepted trade-off as the previous entry.
  The fare-transparency disclosure is unaffected: it is carried by the fare
  line, which `relabel_booked_distance_lines()` relabels to
  `Ride fare (X km booked)` when the charged distance is the booking estimate.
- No rider PII, money, ride-state, dispatch, or insurance-period path touched.
  No migration.

## 5. User-experience effect

- **Rider-facing.** The post-trip pill is visible immediately after a trip ends
  — but only on the completed-ride screen, never mid-ride.
- Post-trip: the white map overlay now starts with the pickup address instead of
  a blue "Actual route · Route reconstructed · 59% GPS observed · 41% inferred"
  line. One fewer row; the map itself is unchanged.
- Receipt: the route map image appears without a caption above it. A v2 ride
  with a stale snapshot shows no map, as before, but no longer shows an
  explanatory line either.
- Driver app, admin dashboard, corporate: no change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `rider-app/app/ride-completed.tsx` | Removed the map-overlay provenance pill, the four locals behind it, two styles, and the `routeQualityLabel` import | Operator diagnostics off the rider screen |
| `rider-app/app/ride-details.tsx` | Removed the receipt's three route captions and the `routeQualityLabel` import; kept both images, the revision gate, and the alt text | Same, for the emailed/PDF receipt |
| `rider-app/__tests__/rideDetailsScreen.test.tsx` | Reworked 3 receipt tests; **repaired 4 screen tests broken by `327d3e8`**, replacing them with one that pins the caption stays gone; added a receipt test sweeping every quality string | See §10 |
| `rider-app/__tests__/ride-completed-route.test.tsx` | Dropped 5 assertions on deleted copy; added a provenance-absent test that keeps `styles.mapAddrRow` pinned | The addresses must survive the pill's removal |
| `rider-app/__tests__/ride-details-route.test.tsx` | Reworked the receipt-copy test to pin the revision *gate* rather than the caption; flipped `routeQualityLabel` to `not.toContain` | The screen no longer imports it at all |
| `rider-app/__tests__/rideCompletedScreen.test.tsx` | Flipped the `'Actual route'` assertion to `not.toContain` | Same |

## 7. Before / after

```tsx
// Before — post-trip map overlay
<View style={styles.mapOverlay}>
  <View style={styles.mapRouteStatus}>
    <Ionicons name={hasActualRoute ? 'navigate-circle-outline' : 'map-outline'} ... />
    <Text style={styles.mapRouteStatusText}>{routeLabel} · {routeStatus}</Text>
  </View>
  <View style={styles.mapAddrRow}>...

// After
<View style={styles.mapOverlay}>
  <View style={styles.mapAddrRow}>...
```

```js
// Before — receipt
? `...<p style="color:#666;...">Actual route (revision ${routeRevision}) · ${routeQuality}</p><img src="${routeSnapshotUrl}" .../>...`
: _num(ride?.route_schema_version) >= 2
  ? `...<p style="color:#8a3412;...">Route snapshot unavailable · ${routeQuality}</p>...`

// After — same conditions, same URLs, no caption
? `...<img src="${routeSnapshotUrl}" alt="Actual route" .../>...`
: _num(ride?.route_schema_version) >= 2
  ? ''
```

## 8. Rollback plan

`git revert` is a complete rollback. Presentation-only, client-side, writes
nothing, no migration, no live data. Not feature-flagged, for the same reason as
the previous entry: a directed copy removal shipped dark would leave the copy in
place for the flag-off cohort. Reaching riders needs an OTA/EAS update, so
rollback uses the same mechanism as rollout.

Note this lands on the same branch as `327d3e8`; reverting that commit alone
would leave `rideDetailsScreen.test.tsx` referring to a caption that this commit
also removed. Revert both together, or neither.

## 9. Verification performed

- [x] Blast-radius grep: `routeQualityLabel` across the whole repo — confirmed
      zero rider-app consumers remain and the 3 surviving call sites are all
      admin/driver (enumerated in §3). Also `routeLabel` / `routeStatus` /
      `routeQuality` / `routeIsProcessing` / `mapRouteStatus` in
      `ride-completed.tsx` (all 0 after), and `routeRevision` in
      `ride-details.tsx` (retained for the revision gate).
- [x] Grepped every rider-app test for the affected copy strings — which is how
      the four tests `327d3e8` broke were found (§10).
- [x] `Ionicons` confirmed still used on the post-trip screen (22 refs) after
      removing the pill's icon, so the import is not orphaned.
- [x] `tsc --noEmit --noResolve` parse pass clean on both changed screens and
      all four changed test files.
- [ ] **Jest NOT run; no typecheck, lint, or production build.**
      `registry.npmjs.org` and `registry.yarnpkg.com` remain blocked by this
      session's egress policy (403 on CONNECT), so rider-app dependencies could
      not be installed. All test changes here are committed unexecuted.
- [ ] Manual check on device/simulator — not performed.

## 10. What was NOT verified

- **A miss in the previous commit, found and fixed here.** `327d3e8` deleted the
  Ride Details caption but left four tests in `rideDetailsScreen.test.tsx`
  asserting on it (`'Actual route ·'`, `'Actual route unavailable'`,
  `'Planned route · Planned route preview'`, `'Actual route processing'`). Its
  change log said the blast-radius grep covered the tests; it covered
  `ride-details-route.test.tsx` and not the screen test. Those four are replaced
  here by one negative test. **Because no test can be executed in this session,
  the only reason this surfaced was a wider grep on this pass — there may be
  comparable misses that grepping did not reach.** CI is the real check.
- Nothing in this change has been executed. Every assertion added or edited is
  reasoned, not observed.
- No screenshot of either surface. Per `CLAUDE.md` release gate #6 rider-app has
  no visual-regression tooling, so the post-trip overlay's new top spacing and
  the receipt's caption-less image were reasoned from the stylesheet and the
  HTML string, not seen rendered. The receipt in particular is HTML rendered by
  an arbitrary mail client — untested here in any client.
- The receipt's `alt` text was kept deliberately as an accessibility affordance.
  Whether the owner considers alt text part of "the text" to remove was not
  confirmed; it is invisible in normal rendering.
- `driver-app/app/driver/ride-detail.tsx` still shows the same provenance copy
  to drivers. Left in place — the directive named the rider app both times.
