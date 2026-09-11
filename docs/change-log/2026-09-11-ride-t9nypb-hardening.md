# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (session `01Ro32rhyxmV2ws4MaPhPUSX`) |
| Surface(s) | driver-app, shared, backend |
| Domain (Sentry tag) | drivers, auth, rides |
| PR / commit link | _pending_ — branch `claude/ride-t9nypb-hardening` |
| Related issue or gap ID | Live-testing ride `SPR-T9NYPB` (`ec8d772a-…-938c1f47d486`, 2026-09-11 13:51–14:10 UTC). Sentry `CRIMSON-SMOKE-7445-PV / SX / PP / SW / NC / K4 / B / C`. Post-mortem artifact "SPR-T9NYPB Post-Mortem". Fixes 1 + 2 of that plan have their own log: `2026-09-11-driver-crash-loop-ride-t9nypb.md`. |

This log covers the **remaining plan items except the billed-distance guard** (item 6 — deliberately not started, per product). Nine commits, each independently revertable; listed in §6 in commit order.

## 1. Issue / gap identified

One 18-minute test ride produced: seven driver-app process deaths (a fatal 256 MB heap OOM, a background MapView teardown crash, five uncaptured), a mid-ride forced re-login, a car icon pointing 90° off the road, an Android Auto head unit showing Google's default world camera during the trip and "Spinr Driver isn't responding" at Complete Trip, and on the backend 3–9 s queueing on single-digit-ms polls plus a driver location-write path at p95 288 ms against a 150 ms SLA. Separately, the same driver was cascade-logged-out of every device three times on 09-09/09-10 by one stale install replaying a token revoked on 08-26.

## 2. Root cause (per item)

| # | Symptom | Root cause |
|---|---|---|
| 3a/4/E | OOM; world camera; ANR at completion | The Android Auto surface rendered a second Google Maps GL view into a VirtualDisplay and **remounted it** at +1.2 s and +4 s after every connect and on every leg change (`carSurface.tsx` key on `route.leg`; `register.ts` chrome refresh). Each remount is a new GL map + tile cache + camera reset on the main thread, in a process already running the phone map. The camera was applied only after `onMapReady`; when that did not re-fire for a remounted instance the map sat at Google's default 0,0 / zoom 2. |
| 3b | OOM | Default 256 MB heap for a two-map navigation process; no `largeHeap`. |
| 7 | Sideways car | `CarMarker` carried the previous route's snapped segment index into `snapToRoute` as `preferredFromIndex` after the live route was **re-anchored** (indices restart at 0 every 6 s poll), so the constrained search started ahead of the car and took the post-turn segment's bearing. |
| 5a | Mid-ride logout | `shared/api/client.ts` G2 backstop: any 401 while no refresh callback was registered (before `authStore.initialize()`, e.g. a background relaunch) called `logout()`, which deletes the stored refresh token. The backend never received a refresh attempt (edge logs); the previous token is still valid today. |
| 5b | All-device logouts 09-09/10 | `_handle_refresh_token_reuse` cascaded on **every** replay of the same already-revoked row, revoking sessions minted after the first response. |
| 8 | Five uncaptured deaths; noisy "anonymous" issue | `enableTombstone` / `enableNdkAppHangTracking` off (NDK and the JVM ANR watchdog were already on); the cold-start `captureMessage` grouped under an anonymous frame. |
| 9a | 5–9 s `ride_routes` polls | `select *` on a table carrying the full GPS trace and OSRM polyline per ride, every 15 s. |
| 9b | Aligned loop stampede | All 41 loops tick-then-sleep from the same instant; same-interval loops phase-locked forever. |
| 9c | `/location-batch` 232 ms | Five sequential Supabase round-trips; driver and ride reads were serialised. |
| 9d | `/live-route` p95 1.7 s | OSRM route + snapped trail recomputed for every poll from both parties. |

## 3. Fix / remediation

- **3a/4/E** — one native car map per connection: `MapView` keyed on `surfaceGeneration` only; route line and pins keyed on the leg (so the overlay cleanup the old key bought still happens, on the overlays); the self-heal remounts **only if the current instance has not attached** (`carSurfaceGeneration.mapReady`, set from `onMapReady`); camera applied on `onMapReady`, `onMapLoaded` or a non-zero layout (native `animateToCamera` is null-safe); non-finite centre degrades to "follow the driver"; `pan()` rejects non-finite input.
- **3b** — `plugins/withLargeHeap.js` sets `android:largeHeap="true"`; verified via `expo config --type introspect`. Native: EAS build, not OTA.
- **7** — on route identity change, the hint is re-derived by snapping the marker's current position onto the new polyline (unrestricted search), not cleared to null.
- **5a** — with no refresh callback registered **and** a refresh token on disk, the backstop drops the dead in-memory token, records a Sentry breadcrumb and rejects; `initialize()` decides. Clear-and-logout is unchanged when no stored token exists or on a retry-after-refresh 401. Orphaned `driver-app/utils/apiClient.ts` (logged out on any refresh failure; zero importers) deleted.
- **5b** — cascade once per revoked row: `lookup_refresh_token` checks the audit row the cascade writes (`action=refresh_token_reuse_detected`, `details.replayed_row_id`); a repeat returns the same 401 with a warning + Sentry `spinr_alert=refresh_token_replay_repeat`. Audit read failure → cascade (conservative).
- **8** — `enableTombstone`, `enableHistoricalTombstoneReporting`, `enableNdkAppHangTracking` on; `captureMessage` gains `{ fingerprint, tags }`; cold-start marker fingerprinted as one named info issue. Sentry `PV` un-archived (was `archived_forever`).
- **9a** — explicit column lists on both queue polls.
- **9b** — `_restartable` sleeps a random offset before the first tick, bounded by the loop's cadence (parsed from its registry name) and 30 s.
- **9c** — driver and ride reads issued with `asyncio.gather`; ownership checked in Python with the identical 404.
- **9d** — 6 s Redis cache keyed on ride + leg + 4-dp position; empty results never cached; cache failure → recompute.

## 4. Risk & impact on existing functionality

- **Android Auto (3a/4/E) — medium, hardware-unverified.** Blast radius: `lib/androidAuto/{carSurface.tsx, register.ts, carSurfaceGeneration.ts, carMapCamera.ts}`; no other importer of `bumpCarSurfaceGeneration` (grep: `register.ts` only). What could regress: (i) the cold-launch empty-map cure still fires when `onMapReady` has not arrived by +1.2 s / +4 s — a slow tile fetch on a good map no longer triggers a remount, which is the intent; if a map attaches but is blank for another reason (the empty-API-key case), a remount never fixed that either; (ii) leftover route overlay artifacts on a leg change are now handled by remounting the overlays, not the map — **not observed on hardware yet**; (iii) camera may now be applied slightly earlier (on layout) — native no-ops if the map is not ready. Per `docs/carplay-android-auto.md`, this ships via the `android-auto` EAS build → hardware check → Play internal track, never OTA.
- **largeHeap (3b) — low.** Heap limit roughly doubles; longer GC pauses on a fuller heap are not measurable next to a GL map re-creation. No iOS effect.
- **Marker (7) — low.** `CarMarker.tsx` only; consumers (`index.tsx`, rider-app's own marker) unchanged. Regression test reproduces the old 89.99° bearing.
- **Session guard (5a) — medium, auth.** Consumers of `handleApiError`: every `api.*` call in rider-app and driver-app. Only the `!_refreshCallback && storedRefreshToken` branch changes. A 401 in that window now leaves the session for `initialize()`; if `initialize()` never runs (it always does on app start) the dead access token is already dropped so nothing keeps sending it. The existing "no stored token → logout" and "retry-after-refresh 401 → logout" tests still pass. Both branches now breadcrumb.
- **Reuse cascade (5b) — medium, auth. Flagged for `spinr-security-auditor` before merge.** The FIRST replay of any revoked row (rotated outside grace, or logout-revoked) still cascades — the recorded decision is unchanged. Only the *repeat* on the same row is suppressed, and a replayed row that was never cascaded (no audit record) still cascades. One extra `audit_logs` read (equality filters on `action` + `entity_id`) on the rare replay path.
- **Observability (8) — low.** Shared `errorReporting` init serves both apps; the three flags are Android-only no-ops elsewhere. `captureMessage`'s new third argument is optional; 2 existing call shapes unchanged.
- **Backend 9a — none.** Same rows, fewer columns; callers read only the projected fields (test pins it).
- **Backend 9b — low.** First tick of each loop lands up to `min(interval, 30 s)` later once per process. Leader locks / idempotency untouched. `ENV=test` unchanged. A 24 h loop delayed 30 s is irrelevant; a 15 s loop is delayed < 15 s (never skips a tick).
- **Backend 9c — low.** Same 404 contract for a foreign/unassigned ride; persist-before-marker ordering pinned by the existing test.
- **Backend 9d — low.** Worst case a poller sees a ≤ 6 s-old route from the same ~11 m bucket. Rider and driver both read the same cached shape. Redis unavailable → warning + recompute (never a 5xx).

## 5. User-experience effect

Drivers: the app should stop dying on screen lock and (with 3a/3b) stop running out of memory with Android Auto connected; the car icon follows the road through turns; the head unit shows the route instead of the Atlantic and does not hang at Complete Trip; a background relaunch no longer sends the driver to OTP mid-trip; one stale install no longer logs the driver out of their live phone. Riders: `/live-route` responses are the same shape, occasionally up to 6 s cached. Nothing here changes copy, fares or ride state.

## 6. Files modified

| Commit | File path | What changed | Why |
|---|---|---|---|
| `176ddad43` | `driver-app/lib/androidAuto/carSurface.tsx` | map key on generation only; overlays keyed on leg; camera effect runs on ready/loaded/layout; finite-centre guard; `markMapReady` on `onMapReady` | 3a / 4 / E |
| `176ddad43` | `driver-app/lib/androidAuto/register.ts` | self-heal remounts only when not attached | 3a |
| `176ddad43` | `driver-app/lib/androidAuto/carSurfaceGeneration.ts` | `mapReady` flag, `markMapReady`, `isCarSurfaceMapReady` | 3a |
| `176ddad43` | `driver-app/lib/androidAuto/carMapCamera.ts` | `pan()` ignores non-finite | 4 |
| `176ddad43` | `driver-app/lib/androidAuto/__tests__/{carSurfaceGeneration,carMapCamera,carRoute}.test.ts` | 6 new tests; source-contract test updated to the new key | — |
| `197370a86` | `driver-app/components/CarMarker.tsx` | re-base snap hint on route change | 7 |
| `197370a86` | `driver-app/__tests__/components/CarMarker.test.tsx` | regression test (89.99° → ~0°) | — |
| `f9e6680d4` | `shared/api/client.ts` | pre-init 401 keeps stored refresh token; breadcrumbs; `hasStoredRefreshToken` | 5a |
| `f9e6680d4` | `shared/api/__tests__/client.sos.test.ts` | 3 tests | — |
| `f9e6680d4` | `driver-app/utils/apiClient.ts` | deleted (orphan) | 5a |
| `efa725a36` | `backend/utils/refresh_tokens.py` | `_reuse_already_handled`, `_capture_reuse_event`, `REUSE_AUDIT_ACTION` | 5b |
| `efa725a36` | `backend/tests/test_refresh_token_reuse_detection.py` | 4 tests | — |
| `d1937a4ce` | `shared/services/errorReporting.ts` | tombstone/app-hang flags; `captureMessage` options | 8 |
| `d1937a4ce` | `driver-app/app/_layout.tsx` | fingerprinted cold-start marker | 8 |
| `d1937a4ce` | `rider-app/__tests__/errorReporting.test.ts` | 2 tests | — |
| `62fff57a4` | `driver-app/plugins/withLargeHeap.js`, `driver-app/app.config.ts` | `android:largeHeap` | 3b |
| `b3593a5b7` | `backend/utils/route_finalizer.py` | projected queue polls (+ a pre-existing 3-line ruff format fix) | 9a |
| `b3593a5b7` | `backend/tests/test_route_finalizer_loop.py` | projection test | — |
| `26cb8656d` | `backend/core/lifespan.py` | `loop_start_offset_seconds`; offset in `_restartable` | 9b |
| `26cb8656d` | `backend/tests/test_lifespan_loop_start_offset.py` | 5 tests | — |
| `f4c22143c` | `backend/routes/drivers/location.py` | gather driver + ride reads; ownership in Python (+ pre-existing B904 fix) | 9c |
| `f4c22143c` | `backend/tests/test_location_batch.py` | 2 tests | — |
| `29fa084ea` | `backend/routes/rides/tracking.py` | 6 s cache | 9d |
| `29fa084ea` | `backend/tests/test_live_route.py` | 4 tests + cache-clearing fixture | — |
| — | `docs/change-log/2026-09-11-ride-t9nypb-hardening.md` | this log | — |

## 7. Before / after (behaviour-changing diffs)

```tsx
// carSurface.tsx — BEFORE: a new native map on every leg and every self-heal
<MapView key={`${route ? route.leg : 'idle'}-${surfaceGeneration}`} …>
  <RouteLine path={…} />
// AFTER: one map per connection; overlays recreated per leg
<MapView key={`car-map-${surfaceGeneration}`} …>
  <RouteLine key={`route-line-${route.leg}`} path={…} />
```
```ts
// register.ts — BEFORE               // AFTER
apply(); bumpCarSurfaceGeneration();  apply(); if (isCarSurfaceMapReady()) return; bumpCarSurfaceGeneration();
```
```ts
// client.ts G2 — BEFORE: always        // AFTER
useAuthStore.getState().logout();       if (!_refreshCallback && (await hasStoredRefreshToken())) { breadcrumb; /* keep session */ }
                                        else { breadcrumb; useAuthStore.getState().logout(); }
```
```python
# refresh_tokens.py — BEFORE                 # AFTER
await _handle_refresh_token_reuse(row)       if await _reuse_already_handled(row): _capture_reuse_event(row, repeated=True); return None
return None                                  await _handle_refresh_token_reuse(row); return None
```
```python
# lifespan.py _restartable — BEFORE: while True: await coro_factory() …
# AFTER: offset = loop_start_offset_seconds(name); if offset > 0: await asyncio.sleep(offset); while True: …
```

## 8. Rollback plan

Every commit is `git-revert-safe` independently — no migrations, no data writes, no config rows. Practical rollback per surface: **driver-app / shared** — `eas update:republish` of the prior update group for the OTA-eligible commits (7, 5a, 8); the Android Auto commit and `largeHeap` are native and roll back with the previous Play build. **backend** — revert + Fly redeploy (`deploy-fly.yml` on push). The Sentry un-archive of `PV` is a UI state, re-archivable by hand. Reverting 5b restores the repeat cascade (the mid-shift all-device logouts).

## 9. Verification performed

- driver-app: `lib/androidAuto` suites **233 passed** (14 suites); `CarMarker` + dashboard **84 passed**; `authStore.*` **14 passed**; `tsc --noEmit` 0 errors; eslint 0 errors on changed files (6 pre-existing errors in `CarMarker.tsx` confirmed identical on `main`).
- shared / rider-app: `shared/api/__tests__` **31 passed**; `errorReporting.test.ts` **11 passed**; rider-app `tsc --noEmit` 0 errors.
- backend: reuse/lifecycle **54 passed** + `test_p1_token_refresh` **6**; route finalizer **24 passed**; lifespan suites **34 passed**; location suites **63 passed, 1 xfailed**; live-route suites **15 passed**. `ruff check` clean on every changed file; `ruff format --check` clean (two pre-existing drifts fixed where the pre-commit gate required it).
- `expo config --type introspect` shows `android:largeHeap: 'true'`.
- Sentry `CRIMSON-SMOKE-7445-PV` status: ignored → unresolved (comment posted).
- The CarMarker regression test was run against the pre-fix component and **fails there** (bearing 89.99°).
- **No production build run.** JS-only commits are OTA-eligible; the Android Auto and `largeHeap` commits require an EAS build.

## 10. What was NOT verified

- **Nothing on a device.** Every driver-app root cause is inferred from Sentry + DB + source; the Android Auto change in particular is hardware-unverified and the repo's own policy makes hardware validation the gate for it.
- The five uncaptured deaths remain unexplained; item 8 is what would name them next time.
- Whether `onMapReady` fires on the virtual display after a remount is still unknown; the camera now no longer depends on it alone.
- `/location-batch` will **not** reach the 150 ms SLA from 9c alone (one round-trip saved out of five); the auth per-request `users` re-read is mandated by the JWT trust model and was left alone.
- Capacity at 10/20/30 rides remains an extrapolation; `loadtest/` has not been run.
- `test_loguru_call_conventions.py` fails on this Windows checkout for 171 files it cannot decode as cp1252 — pre-existing, environment-only (CI is UTF-8), none of them touched here.
- The Google Play Developer Reporting API is still disabled on GCP project `879808882715` (console action; not automatable from here).
- The security-auditor pass on 5b is requested, not yet done.
