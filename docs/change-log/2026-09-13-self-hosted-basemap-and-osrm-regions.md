# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude (session requested by mkkreddy52@gmail.com) |
| Surface(s) | admin-dashboard, deploy/ (new `deploy/tiles` service, `deploy/osrm` build config) |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | User report: "in admin panel i have issue with the maps … Basemap slow to load — trying another provider…", plus "can i add the alberta also in to the osrm". Follows `2026-09-12-admin-ride-map-basemap-fallback.md`, whose §8 named self-hosted tiles as the correct long-term answer. |

## 1. Issue / gap identified

Three distinct things, from one report:

1. **The failover banner never clears.** An admin sees "Basemap slow to load —
   trying another provider…" pinned over the ride Route Views map. The Carto
   attribution visible in the same screenshot proves the fallback provider
   *did* load — the map is fine and the banner is lying about it.
2. **Self-hosting was documented but not wired.** `.env.example` promised
   `NEXT_PUBLIC_MAP_STYLE_URL` as a no-code-change path to our own tile server.
   `basemapChain()` never read it, so setting it did nothing for the admin maps,
   and `static-route-map.tsx` hardcoded a Carto raster URL with no override at
   all. Nothing about the basemap was actually configurable.
3. **OSRM covers Saskatchewan only** and there was no supported way to widen it.

## 2. Root cause

1. **The banner is write-only.** `attachBasemapFallback()` gave callers `onRetry`
   and `onExhausted` and no success signal. `ride-route-map.tsx` set
   `basemapStatus = "retrying"` on a hop; nothing could ever set it back,
   because `onLoad` only called `settle()` internally. `setBasemapStatus("ok")`
   ran once at mount, before the first attempt. Confirmed by reading the code,
   not inferred from the screenshot: `maplibre-base.ts`'s `onLoad` had no
   outbound call, and the only `"ok"` write in `ride-route-map.tsx` was outside
   the fallback handlers.
2. **The env var was read by the wrong function.** `trackBaseMapStyle()` (public
   `/track` page) read `NEXT_PUBLIC_MAP_STYLE_URL`; `basemapChain()` (every admin
   map) did not. The `.env.example` comment describing it as a global switch was
   written against the tracking page's behaviour and silently over-claimed.
3. Not a defect — the OSRM image simply took one `REGION_URL`, and OSRM loads
   exactly one graph per process, so a second province needs the extracts
   *merged* before `osrm-extract`, which nothing did.

## 3. Fix / remediation

- **`onLoaded` added to `BasemapFallbackHandlers`** (optional, so no existing
  caller breaks) and fired from `onLoad`. `ride-route-map.tsx` uses it to return
  the banner to `"ok"` once a provider actually renders.
- **`basemapChain()` now honours `NEXT_PUBLIC_MAP_STYLE_URL`** (and optional
  `_DARK`) as hop 0, with the three existing providers kept *behind* it —
  self-hosting adds a preferred provider, it does not remove the safety net.
  The chain is deduplicated so pointing it at a provider already in the chain
  cannot produce a "fallback" to the host that just failed.
- **`static-route-map.tsx` raster URL is overridable** via
  `NEXT_PUBLIC_RASTER_TILE_URL`. Attribution is now computed rather than
  hardcoded: OpenStreetMap always, CARTO only when Carto actually served the
  tiles.
- **New `deploy/tiles/`** — planetiler → tileserver-gl on Railway, serving both
  the vector style and rasterised PNGs, so both renderers can leave third
  parties behind. Ships with a smoke test.
- **`deploy/osrm/` gains `EXTRA_REGION_URLS`** — a new `fetch` stage merges
  extra Geofabrik extracts with `osmium merge` before preprocessing. **Default
  unchanged (Saskatchewan only).**

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (admin-dashboard) for everything that ships
active today. Zero backend, zero money, zero ride-state.**

- **`maplibre-base.ts` importers grepped** — `monitoring-map.tsx`,
  `live-map.tsx`, `ride-route-map.tsx`, `driver-map.tsx`, `geofence-map.tsx`,
  `heat-map.tsx`, `venue-map.tsx`, `e2e/visual-regression.spec.ts`. Only
  `heat-map.tsx`, `monitoring-map.tsx` and `ride-route-map.tsx` call
  `basemapChain()`; the others use unmodified exports. Every existing export
  keeps its signature — `onLoaded` is optional and `basemapChain()`'s return
  type is unchanged.
- **`basemapChain()`'s behaviour is byte-identical when the new env vars are
  unset**, which is every environment today. Verified by direct assertion:
  `chain[0]` is still `MAP_STYLE_URL`, last hop still `MAP_STYLE_CARTO_LIGHT`.
  Setting them is opt-in and per-environment.
- **`static-route-map.tsx` has one consumer**, `ride-route-map.tsx`, and is only
  reachable when WebGL is unusable or the whole chain is exhausted.
- **CI visual-regression interaction preserved.** `visual-regression.spec.ts`
  stubs `tiles.openfreemap.org` with a source-less style that fires `load`
  immediately. That stub now also triggers `onLoaded` — which sets state to
  `"ok"`, the value it already holds, so React bails out and no re-render
  occurs. CI hops to no un-stubbed host, so the network-independence
  `ACTION_ITEMS.md` B38 established is intact.
- **Deliberately NOT touched:** `live-map.tsx`, `driver-map.tsx`,
  `geofence-map.tsx` still have no fallback chain at all — the same gap the
  2026-09-12 entry left open. Still out of scope; still owed.
- **No CSP change needed** — `img-src 'self' data: blob: https:` and
  `connect-src 'self' https:` (`src/middleware.ts:64,66`) already admit any
  HTTPS tile host.
- **`deploy/tiles` cannot affect anything else.** It is a new, separate service
  that no existing service calls; nothing depends on it until an env var points
  at it. It draws pixels behind the pins and nothing more.

**The one real risk is the OSRM region change, and it is a money path.**

`backend/utils/route_distance.py` bills on road-matched distance. A GPS trace
entering Alberta today gets a non-`Ok` OSRM response, so the backend falls back
to Google Roads and then to the haversine value from `complete_ride`. With
Alberta in the graph, OSRM matches that trace and returns road-following
distance, which is **longer** than a straight line — so affected fares go **up**.
That is the documented billing model finally reaching those trips rather than a
regression, but it is a real fare change on a live-tested surface.

Mitigation: **this change ships the capability, not the coverage.** The default
build arg is unchanged, so no graph changes and no fare moves until someone
deliberately rebuilds with `EXTRA_REGION_URLS`. Both the Dockerfile header and
`README.md` §1 carry the warning at the point of use.

## 5. User-experience effect

Internal-admin-facing only. No rider, driver or corporate-admin surface changes.

- **Banner fix:** an admin whose first basemap provider is slow currently sees a
  permanent "Basemap slow to load" band over a working map; now the band
  disappears the moment a provider renders. Visible only to someone with the
  ride detail modal open during a failover — not mid-ride-session disruptive.
- **Attribution:** unchanged (`© OpenStreetMap contributors © CARTO`) unless a
  self-hosted raster URL is configured, in which case CARTO is correctly
  dropped. This is a licensing correctness change, not a design one.
- **Everything else is inert** until an operator sets an env var or rebuilds an
  image.

**Not feature-flagged, deliberately.** Gate 3 asks for a flag on user-visible
non-trivial change. Here the flag already exists and is the env var itself:
every new behaviour is off unless `NEXT_PUBLIC_MAP_STYLE_URL`,
`NEXT_PUBLIC_RASTER_TILE_URL` or `EXTRA_REGION_URLS` is set. The one
unconditional change is the banner fix, which only ever removes a false
statement from the screen; flagging it would ship the fix inert.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/maplibre-base.ts` | Added `selfHostedStyleUrl()`; `basemapChain()` prepends it and dedupes; added optional `onLoaded` to `BasemapFallbackHandlers`, fired from `onLoad` | Make self-hosting a config change; give a retry affordance something that can clear it |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-route-map.tsx` | `onLoaded` handler sets `basemapStatus` back to `"ok"` | The stuck-banner fix |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.tsx` | `TILE_URL` const → exported `rasterTileUrlTemplate()`/`rasterTileUrl()`; new `rasterAttribution()`; attribution rendered from it | Let the no-WebGL renderer use self-hosted raster tiles, and stop crediting Carto for bytes Carto did not serve |
| `admin-dashboard/src/lib/__tests__/maplibre-basemap-chain.test.ts` | +10 tests (override hop, dedupe, dark fallback, whitespace, `onLoaded` fires/doesn't-fire/optional); widened the chain-length bound 3 → 4 | Regression cover for the banner bug and the new hop |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.test.ts` | +6 tests over the raster template and attribution | A leftover `{z}` placeholder is indistinguishable from a dead tile host |
| `admin-dashboard/.env.example` | Rewrote the Maps section; documented the three new vars and the build-time-inlining caveat | The old text described the tracking page's behaviour as if it were global |
| `deploy/osrm/Dockerfile` | New `fetch` stage with `EXTRA_REGION_URLS` + `osmium merge`; billing warning in the header | One OSRM process serves one graph, so provinces must be merged pre-`osrm-extract` |
| `deploy/osrm/README.md` | New "Adding a second province" section, AB coordinates, why a `code:Ok` check is not a valid test, troubleshooting rows | |
| `deploy/osrm/smoke-test.sh` | Opt-in `EXPECT_ALBERTA=1` snap-distance assertion | |
| `deploy/tiles/*` (new) | Dockerfile, config.json, rewrite-style.jq, railway.json, README.md, smoke-test.sh | The self-hosted tile server itself |

## 7. Before / after

**The stuck banner** — `maplibre-base.ts`:

```ts
// Before — success was internal-only, so a caller showing "retrying…" had no
// signal that a later provider had succeeded.
const onLoad = () => {
    if (settled) return;
    settle();
};
```
```ts
// After
const onLoad = () => {
    if (settled) return;
    settle();
    handlers.onLoaded?.(attempt);
};
```

**The chain** — `maplibre-base.ts`:

```ts
// Before — NEXT_PUBLIC_MAP_STYLE_URL was read only by trackBaseMapStyle().
return [
    themedMapStyle(resolvedTheme),
    ...(protomaps ? [protomaps] : []),
    cartoStyleUrl(resolvedTheme),
];
```
```ts
// After — self-hosted first, third parties retained behind it, deduped.
const ordered = [
    ...(selfHosted ? [selfHosted] : []),
    themedMapStyle(resolvedTheme),
    ...(protomaps ? [protomaps] : []),
    cartoStyleUrl(resolvedTheme),
];
return [...new Set(ordered)];
```

**OSRM region** — `deploy/osrm/Dockerfile`:

```dockerfile
# Before — one extract, fetched by ADD, no way to widen without replacing it.
ADD ${REGION_URL} /data/region.osm.pbf
```
```dockerfile
# After — N extracts, merged when N > 1. Single-region builds take the mv
# branch and produce the same pbf as before.
for url in ${REGION_URL} ${EXTRA_REGION_URLS}; do curl … ; done
if [ "$count" -eq 1 ]; then mv /data/part-1.osm.pbf /data/region.osm.pbf;
else osmium merge ${parts} -o /data/region.osm.pbf; fi
```

## 8. Rollback plan

Every piece is reversible **without a deploy**, because every piece is gated on
config an operator controls:

| If this goes wrong | Revert by |
|---|---|
| Self-hosted basemap is bad/slow | Unset `NEXT_PUBLIC_MAP_STYLE_URL` (+ `_DARK`) in Vercel and redeploy the dashboard — the chain falls straight back to OpenFreeMap → Protomaps → Carto. Even *without* acting, an unreachable tile server just fails the first hop and the existing chain takes over within the 8 s watchdog. |
| Self-hosted raster tiles are bad | Unset `NEXT_PUBLIC_RASTER_TILE_URL`; the default reverts to Carto. |
| Alberta in OSRM causes unexpected fares | Rebuild the OSRM service without `EXTRA_REGION_URLS`, or point `OSRM_URL` at the previous image. The `osrm_url` override in `app_settings` also lets you swing the backend to a different OSRM instance **without a redeploy** — the documented rotation path. |
| The banner fix itself | `git revert` is a complete rollback: client-side render state only, no persisted data, no migration, no live-data mutation. |

Note the fare caveat: if Alberta coverage has already produced higher billed
distances on completed rides, reverting the graph does **not** unwind those
charges — that is a data-level remediation (refund/adjustment), not a config
revert. This is precisely why the default ships unchanged.

## 9. Verification performed

**Read this section carefully — the environment could not run the project's
normal checks.** `registry.npmjs.org` tarball fetches return 403 under this
session's network policy, so `admin-dashboard/node_modules` could not be
installed and **neither `vitest` nor `npm run build` was run locally.**
`download.geofabrik.de`, Docker Hub, ghcr.io and GitHub are also blocked, and
there is no Docker daemon (client binary only) — so **no image in this change
was built.** CI is the verification path for both.

What *was* actually verified, by execution:

- **Every new pure function was executed** against faithful line-for-line
  mirrors driven through the exact assertions the new tests make — 30/30 pass
  (`selfHostedStyleUrl` incl. whitespace and dark fallback, `basemapChain`
  ordering/dedupe/host-count, `rasterTileUrl` substitution incl. a repeated
  `{z}`, `rasterAttribution` incl. a lookalike-host case
  `evilcartocdn.com.example` that must *not* match).
- **The tile server's Web-Mercator maths is cross-checked against this repo's
  own tested code.** `smoke-test.sh` computes tiles in awk rather than
  hardcoding them; its output matches `static-route-map.tsx`'s `project()`
  exactly at three coordinates, including the borderline z13 Saskatoon tile
  `1668/2701` that `static-route-map.test.ts:21` already pins.
- **The Dockerfile's riskiest step was simulated end-to-end, using the
  committed filter file itself.** The style-rewrite stage was run locally
  against a realistic positron-style sample: sources rewritten to
  `mbtiles://{v3}`, glyphs made relative, both sprite branches exercised, and
  the leftover-third-party guard correctly finding nothing. The font-extraction
  stage was run against three zip layouts (nested `_output/`, flat, and empty) —
  both valid layouts extract and pass the fontstack assertion, the empty one
  fails the build as intended.
- **Both smoke tests pass `bash -n`**, and their response-parsing was exercised
  against synthetic provider responses for both the pass and fail branch.
- **Blast-radius grep**: every importer of `maplibre-base` and of
  `ride-route-map`; every reference to `TILE_URL` (none left); every
  `basemapStatus` write site; CSP directives in `src/middleware.ts`.
- **Existing test assertions re-checked by hand.** `ride-route-map.test.ts` is
  source-text matching — all 5 assertions still hold (this change only adds an
  `onLoaded` handler). `static-route-map.test.ts`'s projection tests are
  untouched.
- **Three build-breaking bugs were caught by self-review, not by any test** —
  worth recording, because they are exactly the class of defect that an
  un-buildable Dockerfile hides until someone tries to build it:
  1. `ARG TILESERVER_TAG` was declared just above the final `FROM`, i.e. *after*
     the first `FROM`, making it stage-scoped rather than global. The tag would
     have expanded empty (`maptiler/tileserver-gl:`) and failed immediately with
     "invalid reference format". Both image tags now precede the first `FROM`,
     and a check confirms it.
  2. The style-rewrite `jq` program was written inline across several lines
     inside a `RUN`. Lines within a `RUN` that do not end in a backslash
     **terminate the instruction**, so Docker would have tried to parse
     `.sources |= with_entries(` as a Dockerfile instruction. The filter now
     lives in `deploy/tiles/rewrite-style.jq` and is applied with `jq -f` —
     which also makes it independently runnable, and it was.
  3. Explanatory `#` comments sat inside `RUN` continuation blocks. Docker does
     strip those, but if it ever did not, the joined single-line command would
     treat the first `#` as a shell comment and **silently discard every
     remaining step** — a build that succeeds having done half the work. All
     such comments were hoisted above their `RUN`, removing the dependency on
     that parser behaviour entirely. A scripted check now confirms both
     Dockerfiles have no comment inside a continuation and that every
     multi-line instruction terminates at a real instruction boundary.
- **Reviewed by `spinr-design-consistency-reviewer` (CLAUDE.md gate 10) —
  verdict ON-BRAND & COMPLETE, no blockers.** It confirmed by reading the
  guards, not by assumption, that the banner fix cannot flicker or thrash
  (per-attempt `settled` latch + `disposed` checks + React's same-value
  `useState` bailout, and `basemapStatus` is not a dependency of either
  effect), that the attribution renders unconditionally from first paint with
  both tokens having real light *and* dark values, and that the attribution
  string can only shrink or stay identical so it cannot newly overflow. Two
  warnings and one nit came back; all three are recorded — the coverage
  disclosure and the silent-tile-failure note in §10 below, and the nit fixed:
  - **Fixed from review:** `rasterAttribution`'s host match was
    case-sensitive, so a `NEXT_PUBLIC_RASTER_TILE_URL` naming Carto with
    different casing would silently *under*-credit them — the same licensing
    failure this function exists to prevent, inverted. Now `/…/i`, with tests
    for both the mixed-case host and a lookalike (`evilcartocdn.com`) that must
    still not claim Carto's credit.
- `spinr-cicd-infra-reviewer` and `spinr-money-auditor` were also dispatched
  against this diff; their findings had not returned when these commits were
  made. **Anything they raise is owed as a follow-up commit on this branch
  before a PR is opened** — this is a feature branch with no PR, so nothing has
  reached a reviewable surface yet.

## 10. What was NOT verified

- **No test in this change has ever been executed by a test runner.** They are
  written against mirrors that pass, which is not the same thing. CI's
  `admin-test` job is the first real run.
- **`npm run build` was not run**, locally or otherwise, at authoring time. CI's
  Playwright jobs run it (`ci.yml:553`, `:660`); until they are green, the
  production-build gate is unsatisfied. `static-route-map.tsx` gained new
  exports and `maplibre-base.ts` a new interface member — both plausible TS
  break sites, unchecked here.
- **`deploy/tiles` has never been built or run.** No layer executed, no image
  produced, no endpoint served. Every external reference in it —
  `ghcr.io/onthegomap/planetiler`, `maptiler/tileserver-gl`, the
  positron-gl-style tarball, the openmaptiles fonts release — is **unconfirmed
  to still exist at that URL/tag**, because every one of those hosts is blocked
  from this session. The README carries this warning prominently and every
  artifact is a `--build-arg` so a moved URL is fixable without editing the
  Dockerfile. **Treat this service as a reviewed design that needs a local
  `docker build` + `smoke-test.sh` before it goes near Railway.**
- **Specific tileserver-gl behaviours are documented-but-unconfirmed**: that
  `/usr/src/app/run.sh` is the right entrypoint, that `mbtiles://{v3}` is
  resolved from `config.json`'s `data` key, that a relative `sprite` resolves
  against the style directory, and that CORS headers are emitted by default.
  The smoke test checks the last one explicitly because it is the failure curl
  cannot see.
- **The `osmium merge` path is unexecuted.** That Geofabrik extracts are sorted
  by `(type, id)` — the precondition `osmium merge` requires — is taken from
  documentation, not observed. If wrong, the merge fails loudly at build time
  rather than producing a bad graph, and the README names the `osmium sort`
  remedy.
- **No Alberta fare scenario was dry-run** against `mock_supabase_client`
  fixtures. Gate 4 asks for that on money changes. It is not done here because
  the default graph is unchanged and no fare can move until someone rebuilds —
  but **it is owed before anyone actually deploys an AB-merged OSRM image**, and
  that is the gate to hold, not this commit.
- **Nothing was seen rendering, and the coverage gap is wider than "the visual
  baseline doesn't open the modal."** `spinr-design-consistency-reviewer`
  checked this rather than taking the caveat at face value, and found **zero
  rendered-component or browser-level coverage of either changed component,
  from any tier**:
  - `e2e/visual-regression.spec.ts:49` navigates `dashboard-rides` to the list
    and waits on `h1`; it never opens a ride row, so neither `RideRouteMap` nor
    `StaticRouteMap` ever mounts during capture.
  - `src/__tests__/dashboard/pages.smoke.test.tsx:372` **stubs the modal out
    entirely** (`pageSubComponentStub("RideDetailModal")` → a bare `<div>`).
    That is precisely the "a stubbed-out component gives zero real coverage"
    case CLAUDE.md's blast-radius gate names — recording it as such rather than
    counting it.
  - Grepping all of `e2e/` for `RideRouteMap` / `StaticRouteMap` /
    `ride-detail-modal` returns nothing.
  - The pre-existing `ride-route-map.test.ts` is `readFileSync` + `toContain`
    source-text matching; it never renders anything.
  - Even making `dashboard-rides` click into a ride would not close it: the
    spec's only network stub is `**/tiles.openfreemap.org/**`
    (`visual-regression.spec.ts:116`), i.e. hop 0 only, and it fires `load`
    immediately by design — so the retry banner and `StaticRouteMap` would
    still never be reached.

  **So a green visual-regression run is not evidence that any of this renders
  correctly, and "admin-dashboard has a merge-blocking visual suite" must not
  be read as covering this surface.** The real regression protection is the new
  unit tests alone. rider-app/driver-app are untouched, so their
  no-visual-tooling disclosure does not apply here.
- **A misconfigured self-hosted raster URL fails silently.** `static-route-map`'s
  per-tile `onError` hides a failed tile (`visibility: hidden`) with no
  "tiles unavailable" affordance — pre-existing behaviour, but
  `NEXT_PUBLIC_RASTER_TILE_URL` is a **new way to make every tile fail at
  once**, and nothing detects that the whole source (rather than one tile) is
  down. Low severity — the route and pins still draw, which is the documented
  intent of this renderer — but it is a new operational dependency inheriting
  an old silent-failure ceiling. `deploy/tiles/smoke-test.sh` exists so the
  endpoint is validated before it is wired up; a real affordance is not built
  here.
- **Noticed, deliberately not fixed (out of scope):** `onExhausted`
  (`ride-route-map.tsx`) never calls `map.remove()` on the final failed MapLibre
  instance even though React unmounts its container when `useStatic` flips true
  — a pre-existing orphaned-WebGL-context concern, untouched by this change and
  not part of its remit.
- **Sizing figures are estimates.** Alberta's extract is described relatively
  ("several times Saskatchewan's") rather than in MB, and the README tells you
  to check Geofabrik before building, because the exact sizes could not be
  fetched and they drift.
