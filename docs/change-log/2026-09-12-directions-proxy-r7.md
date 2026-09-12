# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude Code (audit follow-through, roadmap item R7) |
| Surface(s) | backend, rider-app, driver-app, shared |
| Domain (Sentry tag) | rides |
| PR / commit link | srikumarimuddana-lab/spinrvm#5290 (commits `a3c7c54`, `5c181cf`, `b66ea22`, `afded13`, `282a56a`, `12d2165`, `c4563ba`) |
| Related issue or gap ID | `docs/audit/ride-experience/ROADMAP.md` R7, sources REC-A-04 + G-1 |

## 1. Issue / gap identified

Six `MapViewDirections` mount sites called Google's Directions API **directly from the
device** using a bundled `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`, bypassing `maps_budget.py`'s
daily-spend circuit breaker entirely: `rider-app/app/ride-options.tsx`, `driver-arriving.tsx`
(two mount points), `driver-arrived.tsx`, `ride-in-progress.tsx`, and
`driver-app/app/driver/(tabs)/index.tsx`. Every other Directions/Places/Geocoding call in the
app already routes through `backend/routes/maps_proxy.py`, which hides the key and enforces a
budget check + rate limit; these six were the only ones that didn't (found by the 2026-09-12
ride-experience industry-benchmark audit, `docs/audit/ride-experience/module-a-rider-app.md`
+ `module-d-backend-cost.md`, not a live incident).

## 2. Root cause

These six sites are fallback route lines — rendered only when the backend hasn't already
supplied a `planned_route_polyline`/live OSRM route (driver→pickup legs have no pre-computed
polyline the way pickup→dropoff does). They predate `maps_proxy.py`'s existence and were
never migrated when the proxy was built for autocomplete/details/geocode.

## 3. Fix / remediation

- **Backend (R7.1, `a3c7c54`):** added `GET /maps/directions?origin=&destination=&waypoints=`
  to `backend/routes/maps_proxy.py` — authenticated, rate-limited (`60/minute`), budget-gated
  (`_ensure_budget()`), same pattern as the file's existing endpoints. Returns pre-decoded
  `{coordinates, distance_km, duration_minutes}` (decoding via a newly-extracted
  `backend/utils/polyline.py::decode_polyline()`, pure code motion from a private helper in
  `routes/rides/_shared.py`, which now re-exports the same name unchanged for backward
  compatibility). Migration `415_directions_proxy_enabled_flag.sql` adds
  `app_settings.directions_proxy_enabled` (default `false`) as a dark-launch switch, wired
  through the full 4-part settings pattern (migration, `schemas.py`, admin write model, public
  `GET /settings` read) per `backend/migrations/CLAUDE.md`.
- **Shared client (R7.2, part of `5c181cf`):** `shared/api/directions.ts` —
  `fetchDirectionsRoute(origin, destination)` wraps the new endpoint for both apps.
- **Rider-app (R7.3–R7.6):** `ride-options.tsx`, both `driver-arriving.tsx` mount points,
  `driver-arrived.tsx`, and `ride-in-progress.tsx` each got a "try the proxy first, fall back
  to on-device `MapViewDirections` on any failure" effect, gated by a new
  `DirectionsProxyEnabledContext` (`app/_layout.tsx`) that reads the flag once at app root.
- **Driver-app (R7.7, `12d2165` + `c4563ba`):** driver-app has no app-wide settings context
  (unlike rider-app), so a local `useDirectionsProxyFlag()` hook was added instead, mirroring
  the existing `useDriverDiscreetSosFlag.ts` precedent. The dashboard's route-rendering block
  (the most complex of the six — it also handles a self-hosted OSRM live-route path and a
  saved-polyline reuse path) got the same try-proxy-then-fallback treatment.
- **Not changed:** the on-device fallback itself was never removed (per the roadmap's
  explicit "do not remove the fallback outright" instruction) — it is a legitimate resilience
  path when the proxy is unavailable, and it is also everything that still runs today, since
  the flag defaults off.
- **G-1 (also required):** added the six call sites to
  `docs/audit/ride-experience/cost-inventory-table.md` as rows 17–22, closing the "backend-only
  inventory" gap that table's own note flagged for `ACTION_ITEMS.md` E13's future GCP Billing
  Budgets work.

## 4. Risk & impact on existing functionality

- **Blast radius: cross-surface, but additive at every layer.** Every new code path (proxy
  endpoint, shared client, per-screen effects, driver-app hook) is new and has exactly one or
  two callers, each traced explicitly below. Nothing existing was rewired to depend on the new
  path — the six on-device `MapViewDirections` mounts are untouched except for the new
  conditional gate in front of them, and the gate is `false` by default (flag off), so **today,
  in every environment where the flag hasn't been explicitly enabled, these six screens behave
  identically to before this change.**
- **Who else reads/writes `app_settings.directions_proxy_enabled`:** nothing else — it's a new
  column, read only by `GET /settings` (public) and the two new client-side flag readers
  (rider-app's context, driver-app's hook). Verified via the drift-guard tests
  (`test_admin_settings_write_allowlist_drift.py`, `test_public_settings.py`).
- **Who else reads/writes `decode_polyline`:** the extraction is pure code motion —
  `routes/rides/_shared.py` re-exports the exact same function under the same name via the
  dual-import pattern, so every existing caller of `_shared.py`'s `_decode_polyline` (its
  original private name) is unaffected. Confirmed via `backend/tests/test_polyline.py` (4
  tests) plus the full existing rides test suite passing unchanged.
- **Could this regress a currently-working flow?**
  - If the flag is ever turned on and the proxy is slow/down: each of the six call sites has
    its own local failure-tracking (a `directionsFailed`/`proxyFailedKey`-style flag keyed to
    that screen's own retry/refresh cycle) that falls through to the pre-existing on-device
    path — never "no route line at all." This mirrors the rollback posture the roadmap
    required ("keep the client fallback behind a flag during rollout so a proxy outage
    degrades to today's behavior").
  - The driver-app dashboard (R7.7) is the one call site with real concurrency risk, because
    it's the only one with **two independent route-rendering mechanisms already competing**
    (a periodic `directionsKey`-remount retry loop, and an `osrmRouteActive` live-route
    takeover) before this change added a third (the proxy attempt). An adversarial
    `spinr-edge-case-reviewer` pass (see §9) caught a real race here before it shipped — see
    the driver-app-specific note below.
- **Driver-app dashboard race, found and fixed before merge:** `useDirectionsProxyFlag`
  defaults to `enabled: false` while its `GET /settings` fetch is in flight. Naively gating the
  on-device fallback on `!enabled` alone meant a dashboard mounted mid-`navigating_to_pickup`
  (e.g. app relaunch while a driver is already en route) could render the on-device
  `MapViewDirections` on that default the instant before the real flag value (possibly `true`)
  arrived — firing **both** the on-device call and the proxy call for the same
  `directionsKey` generation, with no way to cancel the on-device one
  (`react-native-maps-directions` has no unmount guard on its in-flight promise). **Fixed** by
  having the hook also report `loaded: boolean`, and gating the on-device fallback on it too —
  the fallback now waits for the flag to resolve one way or the other before ever mounting, for
  every screen using this hook (currently just the dashboard).
- **Driver-app dashboard GPS-jitter over-fetch, found and fixed before merge:** the dashboard's
  hoisted `proxyRouteParams` memo (needed because a top-level `useEffect` can't read values
  computed inside the render path's JSX-only IIFE) originally depended on raw,
  unrounded `location.coords.latitude/longitude`. The on-device path avoids refetching on GPS
  jitter via `react-native-maps-directions`'s own `lodash.isEqual` check against
  props rounded to a 3-decimal (~110 m) grid; the new memo had no equivalent, so it would have
  called the proxy on every location tick instead of roughly every 100 m/60 s. **Fixed** by
  rounding before the dependency array (hoisted to plain variables, since
  `react-hooks/use-memo` requires deps to be simple expressions, not inline `Math.round(...)`
  calls).
- **Not a money, ride-state-machine, or insurance-period change** — this only affects which
  network endpoint renders a client-side polyline; ride state transitions, fare, and insurance
  periods are untouched.

## 5. User-experience effect

- **Who sees a difference:** nobody, in any environment where
  `app_settings.directions_proxy_enabled` is `false` (today's default everywhere). Once an
  admin turns it on for an environment: riders (ride-options, driver-arriving, driver-arrived,
  ride-in-progress screens) and drivers (dashboard) — the route line they already see renders
  from a different backend call, with no visible UI change (same line, same ETA/distance
  fields, same fallback behavior on failure).
- **Visible mid-session?** Yes, in principle — if the flag were flipped on while a driver/rider
  is mid-ride, their next periodic route refresh would start using the proxy. This is the
  intended dark-launch behavior (flip in staging/canary first, verify, then enable more
  broadly) rather than a risk unique to this change.
- **Copy/notification change:** none.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/415_directions_proxy_enabled_flag.sql` | New migration adding `app_settings.directions_proxy_enabled boolean default false`. | R7.1 |
| `backend/schemas.py` | `AppSettings.directions_proxy_enabled: bool = False`. | R7.1 |
| `backend/routes/admin/settings.py` | `SettingsUpdateRequest.directions_proxy_enabled: Optional[bool]`. | R7.1 |
| `backend/routes/settings.py` | Exposed the flag on public `GET /settings`. | R7.1 |
| `backend/routes/maps_proxy.py` | Added `GET /directions` (`get_directions`) + `_parse_latlng()` helper. | R7.1 |
| `backend/utils/polyline.py` | New shared `decode_polyline()`, extracted from `_shared.py`. | R7.1 |
| `backend/routes/rides/_shared.py` | `_decode_polyline` now re-exports `utils.polyline.decode_polyline` (no behavior change). | R7.1 |
| `backend/tests/test_admin_settings_write_allowlist_drift.py`, `test_public_settings.py`, `test_polyline.py`, `test_maps_proxy.py` | New/updated tests for the flag wiring, polyline extraction, and new endpoint; `test_maps_proxy.py` later extended with 2 more cases (malformed waypoint, malformed polyline) per adversarial review. | R7.1, R7.8 review follow-up |
| `backend/routes/maps_proxy.py` (follow-up) | `GET /directions`'s rate limit re-keyed from IP-only (`get_real_client_ip` default) to per-user (`key_func=get_user_or_ip_key`) — both `spinr-security-auditor` and `spinr-performance-sla-reviewer` independently flagged the same carrier-CGNAT gap. | R7.8 adversarial review |
| `shared/api/directions.ts` (+ its test) | New `fetchDirectionsRoute()` client. | R7.2 |
| `rider-app/app/_layout.tsx` | Added `DirectionsProxyEnabledContext` (state, fetch, prop-drilling to `RootLayoutInner`). | R7.3 |
| `rider-app/app/ride-options.tsx` | Proxy-first effect before the on-device fallback. | R7.3 |
| `rider-app/app/driver-arriving.tsx` | Two independent proxy effects (driver-leg, ride-leg). | R7.4 |
| `rider-app/app/driver-arrived.tsx` | Proxy effect before the on-device fallback. | R7.5 |
| `rider-app/app/ride-in-progress.tsx` | Proxy effect; extracted `handleRouteReady()` shared handler. | R7.6 |
| `driver-app/hooks/useDirectionsProxyFlag.ts` (+ its test) | New local flag hook, returns `{enabled, loaded}`. | R7.7 |
| `driver-app/app/driver/(tabs)/index.tsx` | Hoisted `proxyRouteParams` memo, proxy effect, `needsDirections` gate updated to require the flag has loaded. | R7.7 |
| `driver-app/__tests__/screens/driver-dashboard-route.test.ts`, `__tests__/app/driverDashboardScreen.test.tsx` | New/updated pin tests for the above. | R7.7 |
| `rider-app/__tests__/rideOptionsScreen.test.tsx`, `driverArrivingScreen.test.tsx`, `driverArrivedScreen.test.tsx`, `rideInProgressScreen.test.tsx` | New tests + `../app/_layout` mocks (importing it pulls in `@stripe/stripe-react-native`, unavailable in Jest). | R7.3–R7.6 |
| `docs/audit/ride-experience/cost-inventory-table.md` | Added rows 17–22 for the six call sites (G-1). | R7.8 |

## 7. Before / after

```tsx
// Before (driver-app/app/driver/(tabs)/index.tsx render path, representative of all 6 sites)
const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive;
// ...
{needsDirections && <MapViewDirections origin={origin} destination={destination} ... />}
```

```tsx
// After
const { enabled: directionsProxyEnabled, loaded: directionsProxyFlagLoaded } = useDirectionsProxyFlag();
const [proxyFailedKey, setProxyFailedKey] = useState<number | null>(null);

useEffect(() => {
  if (!directionsProxyEnabled || !proxyRouteParams) return;
  if (proxyFailedKey === directionsKey) return;
  // ... try fetchDirectionsRoute(), setRouteCoords/setRouteEtaMinutes/etc. on success,
  //     setProxyFailedKey(directionsKey) on empty result or exception
}, [directionsProxyEnabled, proxyRouteParams, proxyFailedKey, directionsKey]);

// ... render path:
const needsDirections = GOOGLE_MAPS_API_KEY && !useSavedRoute && !osrmRouteActive &&
  directionsProxyFlagLoaded && (!directionsProxyEnabled || proxyFailedKey === directionsKey);
{needsDirections && <MapViewDirections origin={origin} destination={destination} ... />}
```

**Concrete before/after scenario:** with the flag off (today, everywhere), `directionsProxyEnabled`
is `false` and `directionsProxyFlagLoaded` becomes `true` shortly after mount, so
`needsDirections` evaluates identically to the old expression — zero behavior change. With the
flag on, the proxy effect fires first; only a proxy failure (or the flag still loading) lets
`needsDirections` become `true` and the on-device call run.

## 8. Rollback plan

**Primary: flip `app_settings.directions_proxy_enabled` back to `false`** via the admin
dashboard settings page — no redeploy needed, takes effect on each client's next `GET
/settings` poll. This immediately reverts all six call sites to on-device-only behavior, which
is exactly today's shipped behavior.

If the flag mechanism itself needs to be fully removed (e.g. a bug in the flag-reading code
itself, not just the proxy): `git revert` is sufficient for the six frontend commits — each is
additive (new effects, new hook/context) with no destructive edit to the pre-existing
on-device path. The backend endpoint and migration are also additive (new route, new nullable
column with a default) and safe to leave in place even if unused; reverting the migration is
not required for rollback (an unused `false`-default column is inert), but if desired, a
follow-up migration could `ALTER TABLE app_settings DROP COLUMN directions_proxy_enabled`
(append-only convention permitting a new migration to do so, never editing 415 itself).

No data mutation, no wallet/Stripe/ride-state change — this is a pure code-path selector for
which service renders a map line.

## 9. Verification performed

- [x] Automated tests run, per commit (all passing at time of each commit):
  - Backend (`a3c7c54`): `test_maps_proxy.py` (7 new), `test_polyline.py` (4 new),
    `test_public_settings.py` (2 new), `test_admin_settings_write_allowlist_drift.py` updated.
  - Rider-app: full `yarn jest` suite re-run after each of `5c181cf`/`b66ea22`/`afded13`/`282a56a`,
    all passing, plus `npx tsc --noEmit` clean each time.
  - Driver-app (`12d2165`, `c4563ba`): full `yarn jest` suite — **146 suites / 1655 tests
    passed**; `npx tsc --noEmit -p .` clean.
- [x] Blast-radius grep performed: confirmed `useDirectionsProxyFlag` has exactly one consumer
  (the dashboard) before changing its return shape from `boolean` to `{enabled, loaded}`;
  confirmed `decode_polyline`'s only pre-existing caller (`_shared.py`) still resolves via the
  dual-import re-export; confirmed no other file references `cost-inventory-table.md`
  mechanically (docs cross-references only, no test parses it).
- [x] Reviewed against relevant CLAUDE.md conventions: dark-launch flag via the `app_settings`
  DB pattern (4-part wiring, not `.env`); dual-import pattern in the new backend module
  boundary; surgical-changes principle (on-device fallback left untouched, not "cleaned up").
- [x] Adversarial pre-implementation review (CLAUDE.md gate #10): alternative considered for
  R7 itself — Option (b) from the roadmap, "restrict the API key by bundle ID + add
  instrumentation, without proxying" — rejected because it only adds visibility, it doesn't
  close the actual cost/security exposure (the key would still work from any Spinr-signed
  build, and Google key restrictions don't rate-limit or budget-cap). Proxying was the
  roadmap's own preferred option and the one consistent with every other Maps call in the app.
- [x] Adversarial post-implementation review (CLAUDE.md gate #10):
  - `spinr-edge-case-reviewer` run against the driver-app dashboard diff specifically (the
    highest-complexity call site, given its pre-existing
    `directionsKey`/`osrmRouteActive`/saved-route interactions) — found and both fixes above
    (flag-load race, GPS-jitter over-fetch) were applied before commit, not after.
  - `spinr-security-auditor` and `spinr-performance-sla-reviewer` run independently against
    the backend proxy endpoint (`maps_proxy.py`'s `get_directions`, `utils/polyline.py`,
    `shared/api/directions.ts`). **No blockers from either.** Both independently flagged the
    same issue: `@limiter.limit("60/minute")` keyed on IP (`get_real_client_ip` default)
    rather than per-user (`get_user_or_ip_key`) — the same carrier-CGNAT failure mode this
    codebase already fixed for `ride_request_limit`/`location_update_limit`/etc., not yet
    applied here. Two independent reviewers reaching the same finding was treated as high
    confidence and **fixed**: added `key_func=get_user_or_ip_key` to the `/directions`
    decorator. Also added 2 test cases the security reviewer flagged as missing
    (`test_directions_rejects_malformed_waypoint`, `test_directions_502s_on_malformed_polyline`
    — the latter verified against the real `decode_polyline` before writing the test, not
    assumed). Two more findings — `maps_budget.py`'s non-atomic check-then-increment budget
    breaker (pre-existing across all 4 proxy endpoints, not introduced by R7), and the new
    endpoint's lack of a result cache + its shared 5s timeout (reviewer's own verdict:
    "acceptable for dark-launch") — were filed as `ACTION_ITEMS.md` C104 and C105
    respectively rather than fixed here, since both require changes to shared code with a
    wider blast radius than R7's stated scope, and neither reviewer called either one a
    blocker for a dark-launched (flag-off-by-default) merge.
- [ ] **Real production build:** NOT run this session — no EAS/dev-server access in this
  container, for either app. `tsc --noEmit` + each app's full Jest suite were run; per
  CLAUDE.md this is explicitly *not* equivalent to a real build, stated rather than implied.
- [x] Feature-flagged: yes — `app_settings.directions_proxy_enabled`, default `false`, exactly
  the dark-launch pattern CLAUDE.md's pre-merge gate #3 requires for a shared-component change.

### What was NOT verified

- No staging/device confirmation with the flag actually turned on — every screen's proxy path
  has been exercised only via mocked Jest tests (`fetchDirectionsRoute` mocked to resolve/reject),
  never against a real backend + real Google Directions response on a device. This is the
  standard caveat for a dark-launched change: the flag stays off until that verification
  happens in staging.
- No visual-regression tooling exists for rider-app or driver-app (per CLAUDE.md) — none of
  these changes alter any visible UI (the route line renders identically regardless of which
  service supplied its coordinates), reasoned about rather than screenshotted.
- Google Directions response edge cases beyond `test_maps_proxy.py`'s fixture (e.g. a
  `ZERO_RESULTS`/`OVER_QUERY_LIMIT` status, a multi-leg response) are handled by the existing
  `data.get("status") != "OK"` branch but not each individually unit-tested against a real
  Google response shape.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (flag flip, no redeploy; `git revert` as fallback).
- [x] Blast radius is stated, not assumed (§4 — every shared piece's other callers traced).
- [x] No silent behavior change to an already-shipped flow without the UX field filled in (§5 —
      explicitly zero behavior change while the flag is off, which is every environment today).
