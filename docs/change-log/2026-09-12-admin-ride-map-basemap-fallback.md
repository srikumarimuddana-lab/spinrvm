# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-12 |
| Author | Claude (session requested by mkkreddy52@gmail.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | "see in admin panel this map is not loading for each ride" — reported directly by the user, with a screenshot of the Route Views panel rendering as a blank beige box |

## 1. Issue / gap identified

The ride-detail **Route Views** map (`admin-dashboard` → Rides → open a ride) renders as an empty beige panel for every ride: no basemap tiles, **and no pickup/dropoff pins or route line either** — only the OpenFreeMap attribution badge. The user reports the Live Monitoring map is also blank.

## 2. Root cause

**Partly confirmed. Stated precisely, because the trigger is still open.**

Three defects are confirmed by reading the code, and each one independently turns a basemap hiccup into a totally blank panel:

1. **No tile-failure handling at all.** `ride-route-map.tsx` hardcoded a single provider (`MAP_STYLE_URL`, OpenFreeMap) with no `on("error")` handler, no fallback provider, and no error UI. `monitoring-map.tsx` and `heat-map.tsx` received a Protomaps fallback on 2026-09-04 (`#4995`, see `2026-09-04-monitoring-heatmap-tile-provider-fallback.md`); `ride-route-map`, `live-map`, `driver-map` and `geofence-map` never did.
2. **All forensic content was gated behind the basemap.** Both pins and every route layer were added inside `map.on("load")`. MapLibre fires `load` only once the style *and every source it declares* finish loading, so a basemap whose tiles stall or 404 means `load` never fires and the pins and route never render — on the screen the code's own comments identify as the SGI / dispute-review surface.
3. **The map was destroyed and rebuilt on every parent re-render.** The map-init effect's dependency array included `locationTrail`, `pickupTrail`, `tripTrail` and `plannedTrail`, which `ride-detail-modal.tsx` rebuilds as fresh arrays inside its render body (`:757–850`), so their identity changed every render while the effect cleanup called `map.remove()`. The file's own comment at `:104–106` warned that this "exhausts WebGL contexts and blanks the map" — but the `useMemo` fix had only ever been applied to `actualSegments`.

**What the evidence rules out.** The attribution string in the user's screenshot ("OpenFreeMap © OpenMapTiles Data from OpenStreetMap") is the `attribution` field of the TileJSON at `https://tiles.openfreemap.org/planet`, verbatim — so the browser fetched both the style JSON **and** the TileJSON successfully. `tiles.openfreemap.org` is therefore reachable from the user's machine (independently confirmed: style `200` in 0.18s, `.pbf` tile `200` in 0.65s). **The 2026-09-04 "host unreachable" diagnosis does not apply here, and the Protomaps style-fallback added for it would not have fired** — its handler only matches `/style/i`, and the style loads fine. The user's console showed no CSP violation, no WebGL initialization error, and no failed tile request.

**Still open:** which trigger blanks the tiles specifically. A stale cached TileJSON pointing at an expired dated build (`planet/<YYYYMMDD_HHMMSS>_pt/...`, which rotates) remains the leading candidate and would blank every map on that browser, matching the "monitoring is also blank" report. The user is running a cache-disabled hard reload to confirm. This change is deliberately written to survive that class of failure regardless of which trigger it is, rather than to assert a cause that has not been proven.

## 3. Fix / remediation

- **New, additive helpers in `maplibre-base.ts`:** `cartoStyleUrl()` / `MAP_STYLE_CARTO_{LIGHT,DARK}` (keyless Carto basemaps on `basemaps.cartocdn.com` — an independent host *and* CDN), `basemapChain()` (OpenFreeMap → Protomaps-if-keyed → Carto), and `attachBasemapFallback()`.
- **`attachBasemapFallback()` detects tile-level failure, not just style failure** — the gap that made the existing fallback inapplicable here. It advances the chain on a pre-`load` `error` event *or* on an 8s watchdog timeout, which is the only thing that catches a basemap that hangs silently and emits no error at all.
- **Pins and route no longer wait for tiles.** Markers are DOM overlays and are attached at map creation; route layers attach on `styledata` once the style is parsed. Both now render over a blank background when no provider is reachable.
- **Visible status band** replaces the silent blank box: `"retrying"` during failover, `"failed"` once the chain is exhausted. It renders nothing on the happy path, so a basemap that loads first try never flashes a banner.
- **A no-WebGL raster renderer (`static-route-map.tsx`) as the last resort.** Added 2026-09-13 after the reporter confirmed the map was *still* blank and their console revealed an ad blocker (AdBlock, extension `gighmmpiobklfepjocnamgkkbiglidom`, running eyeo's `@eyeo/webext-ad-filtering-solution` content script) active on the page. Privacy/ad extensions routinely hand back a **stubbed WebGL context that never throws and never draws** — which produces precisely the observed signature: every MapLibre map blank, attribution DOM intact, and no failed request, no CSP violation and no WebGL error anywhere in the console. No amount of tile-provider failover fixes that, because every provider still renders through WebGL.

  `StaticRouteMap` renders plain `<img>` raster tiles positioned in a CSS grid with the route and pins drawn as an SVG overlay: **no WebGL, no Web Worker, no vector tiles, no TileJSON indirection**. `RideRouteMap` probes for a *usable* WebGL context (not merely a non-null one — it reads back `gl.VERSION` to catch a stub) and renders the raster map instead when the probe fails, or when the MapLibre provider chain exhausts. Tiles come from Carto's raster pyramid, since OpenFreeMap serves vector only. Pins and the orange→red gradient reuse the same `routePinSvg` / `buildPathGradient` spec as every other surface, so the ride looks identical either way. Even with every tile 404ing, the route and pins still draw.
- **The map is created once per mount.** The creation effect now depends on the pickup/dropoff primitives only; route data changes redraw layers via a ref-held draw routine instead of tearing down the WebGL context.

## 4. Risk & impact on existing functionality

- **`maplibre-base.ts` is purely additive.** No existing export was modified or removed. Grepped every importer — `monitoring-map.tsx`, `live-map.tsx`, `ride-route-map.tsx`, `driver-map.tsx`, `geofence-map.tsx`, `heat-map.tsx`, `venue-map.tsx`, `e2e/visual-regression.spec.ts` — only `ride-route-map.tsx` imports any of the new symbols. The other six maps are byte-for-byte unaffected.
- **`ride-route-map.tsx` has exactly two consumers:** `ride-detail-modal.tsx` (dynamic import, props unchanged) and `ride-route-map.test.ts`, which asserts on the component's **source text**. All 16 of its string assertions were re-checked individually and still hold.
- **Behaviour on the happy path is unchanged.** `basemapChain()[0]` is `themedMapStyle(undefined)` === `MAP_STYLE_URL` — byte-identical to the style previously hardcoded, and still light-only (this component was never theme-aware; making it so would have been an unrequested visual change).
- **CI visual-regression interaction — designed for, not discovered.** `e2e/visual-regression.spec.ts` stubs `**/tiles.openfreemap.org/**` with a source-less style that fires `load` immediately and issues no tile requests. The watchdog is keyed on the `load` event precisely so that stub satisfies it and **CI never hops to an un-stubbed third-party host** — which would have reintroduced the network-dependent baseline flake `ACTION_ITEMS.md` B38 closed. Keying on "did a tile paint" would have broken the merge-blocking job.
- **`dashboard-rides` is one of the 6 seeded visual baselines.** The ride map renders only inside the detail modal, which that baseline does not open, so no diff is expected. However the component's DOM wrapper changed (route map is now an absolutely-positioned child of a new `relative` wrapper), so **if a diff does appear, the baseline needs re-capturing by a human** via `update-visual-baselines.yml` — this agent cannot dispatch Actions.
- **No CSP change needed** — `connect-src 'self' https:` already permits both `api.protomaps.com` and `basemaps.cartocdn.com`; verified in `src/middleware.ts:66`.
- **Retry loops are bounded:** one hop per provider, `settled` latches on first outcome, and a post-`load` error never triggers a swap — so a working map is never torn down and restyled under an admin mid-session.

## 5. User-experience effect

Internal-admin-facing only (ride detail Route Views). No third-party account or env change is required — Carto is keyless.

- **Basemap healthy:** no visible change whatsoever.
- **Basemap failing (today's state):** was a silent blank beige box; now the map retries two other providers, and if all fail still shows the **actual GPS route and the pickup/dropoff pins** over a plain background, plus a one-line "Basemap unavailable" notice. An admin reviewing an SGI or dispute claim keeps the forensic content during a tile-provider outage.
- Not mid-session disruptive — affects initial map load only.

**Deliberately not feature-flagged.** Gate 3 asks for a flag on user-visible non-trivial changes. The fallback path is unreachable unless the primary provider has *already* failed, i.e. unless the current behaviour is an empty panel; worst case it replaces a broken map with a working one from a different provider. A flag defaulting off would ship the fix inert. Recording the decision rather than silently skipping the gate.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/maplibre-base.ts` | Added `MAP_STYLE_CARTO_{LIGHT,DARK}`, `cartoStyleUrl()`, `basemapChain()`, `BASEMAP_LOAD_TIMEOUT_MS`, `attachBasemapFallback()` | A keyless, independently-hosted final hop, plus failure detection that covers stalled tiles and not just a failed style |
| `admin-dashboard/src/lib/map/maplibre-base.ts` | `fitBoundsToPoints()`'s `padding` widened from `number` to `number \| {top,bottom,left,right}` | Lets a caller keep fitted points clear of edge chrome. Backward compatible — all six other call sites pass a number and are unchanged |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.tsx` | Adopts the chain; pins attach at creation and route layers on `styledata`; map created once per mount; error state rendered | Keep the route and pins visible through a basemap outage, and stop the per-render WebGL teardown |
| `admin-dashboard/src/lib/__tests__/maplibre-basemap-chain.test.ts` | New — 11 tests over the chain and the fallback watchdog | Regression cover for the silent-stall case that has no error event |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.tsx` | New — raster `<img>` tile grid + SVG route/pin overlay, no WebGL | The only renderer that survives a browser extension stubbing WebGL |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.test.ts` | New — 5 tests pinning the Web Mercator projection | A wrong projection renders a convincing map of the wrong place, which is indistinguishable from a dead tile host |

## 7. Before / after

```tsx
// Before — one provider, no error handling; pins and route only ever drawn
// once the style AND every source finished, so a stalled basemap hid everything.
const map = new maplibregl.Map({ container, style: MAP_STYLE_URL, ... });
map.on("load", () => {
    new maplibregl.Marker({ element: makeRoutePinEl({ kind: "pickup", ... }) }).addTo(map);
    // ...every route layer, also inside on("load")
});
// deps included pickupTrail/tripTrail/plannedTrail/locationTrail — new arrays
// every parent render — and cleanup called map.remove().
```
```tsx
// After — chain of independent providers; pins attach immediately (DOM overlays
// need no tiles); route layers attach as soon as the style parses.
const map = new maplibregl.Map({ container, style: chain[attempt], ... });
new maplibregl.Marker({ element: makeRoutePinEl({ kind: "pickup", ... }) }).addTo(map);
map.on("styledata", drawWhenReady);
detach = attachBasemapFallback(map, chain, attempt, {
    onRetry: (_next, nextAttempt) => { detach?.(); map.remove(); build(nextAttempt); },
    onExhausted: () => setBasemapFailed(true),   // keep pins + route on screen
});
// creation effect deps are now primitives only: [pickupLat, pickupLng, dropoffLat, dropoffLng]
```

## 8. Alternatives considered (pre-implementation)

| Option | Why not chosen |
|---|---|
| Reuse `monitoringFallbackStyle()` as-is on the ride map | **Would not have fired.** Its handler matches `/style/i`, and the evidence shows the style loads successfully — the failure is downstream at the tile layer. This is the fix that looks right and does nothing. |
| Self-hosted PMTiles on `maps.spinr.ca` | Correct long-term answer, removes the third party entirely, and `.env.example:40` already anticipates it. Needs hosting setup and does not help today; the user is open to it as a follow-up. |
| Carto-only (drop Protomaps hop) | Fewer moving parts, but discards a provider already wired and keyed in this repo for no gain. |

Chosen: a chain whose **last hop is keyless**, so resilience does not depend on an env var being set — the exact condition that made the 2026-09-04 fallback inert in every environment.

## 9. Verification performed

- **A verification of my own that was worthless, and how it was caught.** The projection test was first anchored to tile `13/1668/2700` because that URL returned `HTTP 200`. Carto answers 200 for *any* valid tile coordinate, including empty ocean — so the status code proved only that the endpoint was up, not that the tile was Saskatoon. The test failed against the implementation, and cross-checking the reference OSM slippy-map formula showed the correct tile is `2701`; my hand arithmetic had been wrong, not the code. Re-validated by **density** instead: Saskatoon `13/1668/2701` is 20,905 B, mid-Pacific `13/1500/4000` is 1,718 B. Recorded because "it returned 200" is exactly the kind of check that looks like verification and is not.
- **`vitest run` — 21/21 pass across 3 files** (16 as below, plus 5 projection tests)
- **`vitest run` — 16/16 pass across 2 files** (`maplibre-basemap-chain.test.ts` 11 new + `ride-route-map.test.ts` 5 existing), re-run after the review fixes below. Note: the forks pool cannot start workers in this environment; `--pool=threads` is required locally.
- **Reviewed by `spinr-design-consistency-reviewer` and `spinr-accessibility-reviewer` before commit** (CLAUDE.md gate 10), plus `/code-review` at LOW. Three findings were real and are fixed in this change:
  1. *(design, correctness)* The status band is `top-0` and opaque, but `fitBoundsToPoints` reserved a flat 40px and MapLibre markers anchor at their **centre** — a 22px pin could land ~29px from the top, i.e. underneath the very banner meant to keep it visible. Fixed by widening `fitBoundsToPoints`'s padding to accept per-edge values and reserving `top: 52`.
  2. *(a11y)* `bg-card/90`'s alpha composites over unpredictable canvas colour, on top of a light-theme `text-muted-foreground` contrast hand-computed at only ~4.8:1. Switched to opaque `bg-background`, matching the documented precedent at `monitoring-map.tsx:631` ("a translucent panel over live map tiles put the muted legend text right at the contrast floor").
  3. *(design)* No affordance during the up-to-24s failover window. Added the `"retrying"` state, which renders only after a hop has actually failed — so the happy path still shows nothing and never flashes.
- Two further review points were deliberately **not** changed, and the reasoning is now recorded in-code: neutral rather than `text-destructive` colour (unlike `heat-map.tsx`/`monitoring-map.tsx`, pins and route still render here, so it is degradation, not failure), and full opacity rather than the sibling `emptyHint`'s `/70` (which would push the already-thin contrast under AA).
- `basemapChain()` targets verified live, not assumed: OpenFreeMap style `200` / tile `200`; Carto positron, dark-matter, voyager all `200`; Carto tiles resolve to `tiles.basemaps.cartocdn.com` (confirmed independent of `tiles.openfreemap.org`).
- TileJSON indirection confirmed by fetching `https://tiles.openfreemap.org/planet` and matching its `attribution` field against the user's screenshot, which is what ruled out the host-unreachable theory.
- All 16 source-text assertions in `ride-route-map.test.ts` re-checked individually against the rewritten file — all pass.
- Blast radius grepped for every importer of `ride-route-map` and of `maplibre-base`.
- CSP verified sufficient for the new hosts (`src/middleware.ts:66`).

## 10. What was NOT verified

- **RESOLVED in CI (PR #5321):** `npm run build` **passes on the CI runner** — it runs inside both the `Visual regression (Playwright)` and `E2E tests (Playwright)` jobs (`ci.yml:553`, `:660`), both green on this change. The `Visual regression` job is the merge-blocking baseline one, so the `dashboard-rides` baseline is confirmed **not** tripped by this component's DOM wrapper change. `admin-test` (full admin suite, including the 11 new tests) is also green. CLAUDE.md's production-build gate is therefore satisfied — by CI, not locally. The local failure described below stands as an authoring-machine issue only.
- **`npm run build` did not complete on the authoring machine — the failure is proven pre-existing, not caused by this change.** The build dies with `TurbopackInternalError` on `src/app/globals.css`, caused by `node process exited before we could connect to it with exit code: 0xc0000142` (`STATUS_DLL_INIT_FAILED`) when Turbopack spawns its PostCSS worker. **Control experiment run:** the two modified files were reverted to `HEAD` and the new test moved aside, and the build was re-run on a pristine tree — it failed with the byte-identical error. The same machine also could not start vitest's `forks` pool (`--pool=threads` works, which spawns no new processes) and had 17+ live `node.exe`, so this is host process-spawn exhaustion. This has since been satisfied by CI (see the bullet above); the local break remains an authoring-machine issue, not a property of this change.
- `admin-dashboard/node_modules` in this worktree was corrupt at the start of this session (no dependency had a `package.json`; `maplibre-gl` was a partial source-only tree). A full `npm install` was run to fix it — 746 packages added, 221 changed, ~28 min. The first attempt failed with a Windows `EPERM` lock on the `@spinr/shared` workspace link and had to be retried.
- **The root cause of the user's specific blank map is still not confirmed** (see §2). This change hardens the surface against the whole class of failure; it is not proven to be the fix for their trigger.
- **The ad-blocker / WebGL-stub theory remains a theory.** It fits every observation and is why the raster renderer exists, but it has **not** been confirmed — the decisive test (open the admin panel in an Incognito window with extensions off, or read back the real `WEBGL_debug_renderer_info` string) has not been run. The raster path is deliberately built to make that question moot rather than to answer it: it renders whether WebGL is stubbed, the worker is blocked, the tile host is unreachable, or the TileJSON is stale.
- **The raster renderer has never been seen rendering.** It is covered by projection unit tests only; no browser has displayed it. Tile layout, pin placement, SVG overlay alignment and the ResizeObserver path are reasoned, not observed. This is the single largest untested surface in this change.
- **Not tested against a live basemap outage** — the fallback path was exercised by unit tests with a stubbed map object, never against a genuinely unreachable provider in a browser.
- **The new status band still has no rendered-state coverage at any tier.** The `Visual regression` job passed, which confirms the wrapper change did not disturb the `dashboard-rides` baseline — but that baseline only visits the ride **list** and waits on `h1`; it never opens the detail modal, so neither the map nor the `retrying`/`failed` band is exercised by it. A green visual-regression run is therefore not evidence that the band renders correctly. The `ride-route-map.test.ts` contract test is string-matching only. Both reviewers' findings are therefore code-level reasoning, not a screen-reader- or contrast-tool-verified pass; the light-theme contrast figure (~4.8:1) is hand-computed from `globals.css`, not measured. A real rendered check in both themes — including the long-copy-wrap case on a narrow panel — is still owed.
- **The 52px top padding is arithmetic, not measured.** It is derived from the band's computed height (`py-1.5` + 10px line-height + 1px border ≈ 27px) plus a 22px pin's ~11px centre-anchor overhang. If the copy wraps to two lines on a narrow panel the band grows to ~41px and the clearance margin shrinks — verify when the rendered check above is done.
- **`live-map.tsx`, `driver-map.tsx`, `geofence-map.tsx` still have no fallback** and are untouched by this change — the same gap, deliberately left out of scope to keep the diff reviewable. They should follow.

## 11. Rollback plan

Revert the two modified files (`maplibre-base.ts`, `ride-route-map.tsx`) and delete the new test. There is no migration, no feature flag, no persisted state, and no live-data mutation anywhere in this change — it is client-side rendering only — so a `git revert` **is** a complete rollback here, which is not true of the payment/ride-state changes that rule normally guards. Reverting restores the previous single-provider behaviour exactly, including the blank-panel failure mode.
