# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-25 |
| Author | Claude Code (interactive session) |
| Surface(s) | driver-app |
| Domain (Sentry tag) | rides |
| PR / commit link | (opened alongside this entry) |
| Related issue or gap ID | `docs/legacy-ride-history-presentation-plan.md` Item 4 |

## 1. Issue / gap identified

Same gap as #4557 (rider-app, Item 3), on the driver side: a driver viewing a legacy-imported
ride's detail screen sees a planned route line with no indication it came from the previous
app rather than being GPS-tracked. Admin-dashboard already shows this correctly; rider-app was
fixed in #4557; driver-app was not.

## 2. Root cause

Same as #4557's Item 3 — `legacy_import_metadata` was added for backend/admin traceability
(migration 268) and admin-dashboard reads it directly, but driver-app's `ride-detail.tsx` was
never updated to check for it. Not a regression, a pre-existing gap.

## 3. Fix / remediation

No backend change needed — `show_legacy_badge` already exists on `GET /rides/{ride_id}`'s
response (added in #4557, Item 2). This PR only wires driver-app's `ride-detail.tsx` to read
it: shows an "Imported" badge in the status row, and swaps the map's route-status pill text to
"Imported from the previous app — no GPS was recorded for this ride" (matching rider-app's and
admin-dashboard's wording exactly) instead of the normal route-quality label.

## 4. Risk & impact on existing functionality

- Single file touched for behavior (`driver-app/app/driver/ride-detail.tsx`) — no backend
  change, so `GET /rides/{ride_id}`'s other consumers (rider-app, admin-dashboard) are
  unaffected. Blast radius: single-surface, single screen.
- The `routeStatusPill` text and icon are the only existing elements modified; both are
  conditionally overridden only when `isImported` is true, so a non-legacy ride's pill renders
  byte-identical to before this change.
- No change to `mapCoordinates`/`RouteLine`/route-drawing logic — `planned_route_polyline`
  already rendered correctly for legacy rides before this change.
- Flag (`app_settings.legacy_ride_badge_enabled`) already defaults `false` from #4557 — this
  PR is a no-op in every environment until that flag is explicitly turned on, same as rider-app.

## 5. User-experience effect

- Who sees a difference: drivers, but only once the flag is on (already default off from
  #4557) — no difference at merge time.
- Mid-session visible: N/A while the flag is off. Once on, a driver opening a *past* legacy
  ride's detail screen sees the badge/disclaimer — this screen is not shown during an active
  ride.
- Copy: identical to rider-app's (#4557) and admin-dashboard's existing text — not newly
  authored, not separately re-reviewed against the customer-tone standard for this second
  surface (same residual gap already noted in #4557's log).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/app/driver/ride-detail.tsx` | Badge + route-status-pill disclaimer | Honesty layer for legacy-imported rides, driver side |
| `driver-app/__tests__/screens/driverRideDetailScreen.test.tsx` | +2 test cases | Shown-when-flagged / hidden-without-flag coverage |

## 7. Before / after

```
// Before
<View style={styles.routeStatusPill}>
    <Ionicons name={hasActualRoute ? 'navigate-circle-outline' : 'map-outline'} size={14} color="#2563EB" />
    <Text style={styles.routeStatusText} numberOfLines={1}>{routeLabel} · {routeStatus}</Text>
</View>
```

```
// After
<View style={styles.routeStatusPill}>
    <Ionicons name={isImported ? 'archive-outline' : hasActualRoute ? 'navigate-circle-outline' : 'map-outline'} size={14} color="#2563EB" />
    <Text style={styles.routeStatusText} numberOfLines={1}>
        {isImported
            ? 'Imported from the previous app — no GPS was recorded for this ride'
            : `${routeLabel} · ${routeStatus}`}
    </Text>
</View>
```

## 8. Rollback plan

- **Immediate**: flip `app_settings.legacy_ride_badge_enabled` back to `false` — already the
  shared kill switch for both rider-app and driver-app, no redeploy.
- **Full revert**: `git revert` is safe — pure read/display change, no data written, no
  migration in this PR (the schema change already shipped in #4557).

## 9. Verification performed

- [x] Automated tests run — `jest __tests__/screens/driverRideDetailScreen.test.tsx` (27
      passed, including 2 new cases), `__tests__/screens/ride-detail-route.test.tsx` +
      `rideDetailBackButton.test.tsx` re-verified passing (no regression). `eslint` clean (0
      errors, 3 pre-existing warnings on untouched lines), `tsc --noEmit` clean.
- [ ] Manual repro steps followed in staging — not run (no live Supabase/staging or
      device/simulator access this session)
- [x] Blast-radius grep performed — confirmed no other file reads `routeStatusPill`'s copy or
      the modified icon logic; confirmed no backend files touched, so `GET /rides/{ride_id}`'s
      other consumers are unaffected
- [x] Reviewed against relevant CLAUDE.md convention(s) — reuses the exact flag/copy pattern
      already established in #4557, no new pattern introduced
- [x] Feature-flagged — reuses the existing `legacy_ride_badge_enabled` flag, no new flag
      needed

## 10. Sign-off

- [x] Rollback plan is concrete and testable (shared flag flip, already exercised by #4557)
- [x] Blast radius is stated (one screen, no backend change), not assumed
- [x] No silent behavior change — flag already defaults `false`, UX field filled in above

## What was NOT verified

- Not tested against a live Supabase instance, a real device, or a simulator — only Jest
  component tests (`@testing-library/react-native`) this session.
- Badge/disclaimer copy is identical to rider-app's, itself not independently reviewed against
  the customer-tone standard (carried-forward gap from #4557, not newly introduced here).
