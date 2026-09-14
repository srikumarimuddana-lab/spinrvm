# Change Impact & Risk Log — remove Carto as a basemap provider

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code session (requested by repo owner) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | this commit |
| Related issue or gap ID | none — owner request ("Remove carto MAP_STYLE_CARTO_LIGHT"), follows `2026-09-14-self-hosted-basemap-only.md` |

## 1. Issue / gap identified

Admin maps could still fetch tiles from `basemaps.cartocdn.com`. Two paths
survived the 2026-09-14 self-hosted-only change: `basemapChain()`/`heat-map.tsx`
kept Carto as the last hop whenever `NEXT_PUBLIC_MAP_STYLE_URL` was unset, and
`static-route-map.tsx` hard-coded a Carto raster pyramid as its default
*regardless* of that variable — a separate `NEXT_PUBLIC_RASTER_TILE_URL` had to
be set to avoid it. The owner asked that no third-party basemap provider remain.

## 2. Root cause

Not a defect. Carto was deliberately the keyless last-resort hop: OpenFreeMap and
Protomaps can both be unavailable (Protomaps returns `null` with no API key), so
Carto was the one provider a chain could always reach. `static-route-map.tsx` is
a separate renderer — no WebGL, no vector tiles, plain `<img>` rasters — and
OpenFreeMap serves no usable raster street pyramid, so Carto was the only
keyless option there too.

## 3. Fix / remediation

- `MAP_STYLE_CARTO_LIGHT`, `MAP_STYLE_CARTO_DARK` and `cartoStyleUrl()` deleted;
  the Carto hop removed from `basemapChain()` and from `heat-map.tsx`'s inline
  chain.
- `static-route-map.tsx`'s Carto `DEFAULT_RASTER_TILE_URL` replaced by
  `selfHostedRasterTemplate()`, which **derives** the raster pyramid from
  `NEXT_PUBLIC_MAP_STYLE_URL`. tileserver-gl rasterises any style it serves at
  `<style dir>/{z}/{x}/{y}.png`, so `…/styles/basemap/style.json` implies
  `…/styles/basemap/{z}/{x}/{y}.png`. Standing up `deploy/tiles` therefore needs
  one variable, not two. `NEXT_PUBLIC_RASTER_TILE_URL` still wins when set.

One Carto reference is kept **on purpose**: `rasterAttribution()`'s
`cartocdn.com` branch. It credits Carto only if an operator points
`NEXT_PUBLIC_RASTER_TILE_URL` at them. That is an attribution-licence guard, not
a provider — deleting it would under-credit them.

## 4. Risk & impact on existing functionality

- **Consumers of the changed code** (grepped repo-wide for `MAP_STYLE_CARTO*`,
  `cartoStyleUrl`, `DEFAULT_RASTER_TILE_URL`, `rasterTileUrl*`, `basemapChain`,
  `primaryMapStyle`; zero dangling references remain):
  - `basemapChain()` — `monitoring-map.tsx`, `ride-route-map.tsx`
  - inline chain — `heat-map.tsx`
  - raster template — `static-route-map.tsx` only, reached via
    `ride-route-map.tsx`'s exhaustion hand-off
  - tests — `maplibre-basemap-chain.test.ts`,
    `static-route-map.test.ts`, `static-route-map.render.test.tsx` (the only
    three test files referencing any map-style or raster symbol)
- **What regresses, deliberately**: the unconfigured chain no longer spans two
  hosts. Carto was the only keyless hop, so without
  `NEXT_PUBLIC_PROTOMAPS_API_KEY` the unconfigured chain is now **exactly one
  hop** (OpenFreeMap) with no fallback. Accepted: the answer to our tile server
  being down is to fix our tile server.
- **A bug this change introduced and fixed before commit**: with no tile source
  configured the derived template is `""`, and `rasterTileUrl()` would have
  produced `<img src="">`. An empty `src` does **not** 404 quietly like a dead
  tile host — it resolves to the *current page*, so every tile in the viewport
  (a couple of dozen) would have re-requested the dashboard route itself. The
  tile loop is now skipped entirely when the template is empty, and
  `static-route-map.render.test.tsx` pins it.
- **Not affected**: backend, ride state, dispatch, money, insurance periods.
  Nothing here reads or writes data; this only changes which host serves
  basemap imagery to an internal admin browser.

## 5. User-experience effect

- **Internal admin, tile server configured** (production today): no visible
  change. Carto was already unreachable behind the one-hop self-hosted chain.
- **Internal admin, tile server NOT configured**: monitoring/heat/ride-route
  maps fall back to OpenFreeMap only and show the failed state if it is
  unreachable, instead of degrading to Carto. The static ride-route renderer
  draws the route and pins over an **empty grid** rather than over Carto tiles.
- **Rider / driver / corporate admin**: no change — admin-dashboard only.
- **Mid-session visibility**: yes. An admin with a map open when the build ships
  gets the new behaviour on the next map mount. No data or interaction change.
- No copy changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/maplibre-base.ts` | Deleted the Carto constants and `cartoStyleUrl()`; dropped the Carto hop from `basemapChain()`; recorded why it must not come back | The requested removal |
| `admin-dashboard/src/components/heat-map.tsx` | Dropped `MAP_STYLE_CARTO_LIGHT` from its import and inline chain | It builds its own chain; the shared change would have missed it |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.tsx` | Carto `DEFAULT_RASTER_TILE_URL` → `selfHostedRasterTemplate()`; skip the tile loop on an empty template | The only path still loading Carto by default; the guard prevents `<img src="">` |
| `admin-dashboard/src/lib/__tests__/maplibre-basemap-chain.test.ts` | Removed Carto assertions; added a "never routes any hop to Carto" regression test; re-pinned the now-single-hop unconfigured chain | Four assertions encoded the old chain shape |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.test.ts` | Tests for the derived template (style.json / directory / query-string forms) and the explicit-override precedence | `DEFAULT_RASTER_TILE_URL` no longer exists |
| `admin-dashboard/src/app/dashboard/rides/_components/static-route-map.render.test.tsx` | `beforeEach` now stubs a raster URL; new zero-tile regression test | Its existing tests assert `imgs.length > 1`, which silently depended on Carto being the default |

## 7. Before / after

```ts
// Before — Carto was the guaranteed last hop
const ordered = [
    themedMapStyle(resolvedTheme),
    ...(protomaps ? [protomaps] : []),
    cartoStyleUrl(resolvedTheme),
];
```

```ts
// After — no third-party CDN of last resort
const ordered = [
    themedMapStyle(resolvedTheme),
    ...(protomaps ? [protomaps] : []),
];
```

```ts
// Before — Carto raster pyramid regardless of NEXT_PUBLIC_MAP_STYLE_URL
export const DEFAULT_RASTER_TILE_URL =
    "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png";
export function rasterTileUrlTemplate(): string {
    return process.env.NEXT_PUBLIC_RASTER_TILE_URL?.trim() || DEFAULT_RASTER_TILE_URL;
}
```

```ts
// After — derived from the self-hosted style, or nothing at all
export function rasterTileUrlTemplate(): string {
    return process.env.NEXT_PUBLIC_RASTER_TILE_URL?.trim() || selfHostedRasterTemplate();
}
```

## 8. Rollback plan

`git revert` this commit. Safe and complete: the change writes no data, touches
no schema and no live state — it only decides which hostname a browser asks for
imagery. There is no migration to unwind and no in-flight session to corrupt.

The faster partial lever, if only the static ride-route renderer misbehaves, is
to set `NEXT_PUBLIC_RASTER_TILE_URL` in Vercel and redeploy — that path is
unchanged by this commit and still takes precedence over everything else.

Note the env-var lever that rolled back the *previous* change does **not** undo
this one: unsetting `NEXT_PUBLIC_MAP_STYLE_URL` now yields OpenFreeMap only, not
the old OpenFreeMap → Protomaps → Carto chain.

## 9. Verification performed

- [x] Blast-radius grep performed — repo-wide for every removed symbol; zero
      dangling references. Separately grepped all test files for map-style and
      raster symbols: exactly three, all updated.
- [x] Reviewed against gate #6 (visual regression). Checked rather than assumed:
      the seeded `dashboard-rides` baseline visits `/dashboard/rides`, the ride
      **list**, which never mounts `static-route-map` (it is reached only via
      `ride-route-map.tsx`), and `e2e/visual-regression.spec.ts` stubs only
      `tiles.openfreemap.org`, which remains the unconfigured first hop. No
      seeded baseline should move, so no re-capture should be needed.
- [x] Adversarial self-review found the `<img src="">` defect described in §4
      before commit, and it is now both fixed and pinned by a test.
- [ ] **Automated tests NOT run.** `npm ping` returns HTTP 403 from
      `registry.npmjs.org` under this environment's egress policy (retried this
      session), so `node_modules` cannot be installed and `vitest`, `tsc`,
      `eslint` and `next build` could not be executed here. Every assertion in
      the three updated test files is reasoned against the implementation, not
      observed passing.
- [ ] **No production build run** (`npm run build`), for the same reason.
- [ ] Manual repro in staging — not done; no staging admin-dashboard.

**What was NOT verified:** no test, typecheck, lint or build ran anywhere before
this commit — CI is the first real execution, and the CLAUDE.md-mandated
reviewer-agent pass was done as a manual self-review rather than via the Agent
tool. The single highest-risk assertion is the claim in §9 that no visual
baseline moves; it is reasoned from the spec's page list and stub, not observed.
If `dashboard-rides` does diff, the correct response is to investigate, not to
re-seed — re-seeding needs `update-visual-baselines.yml`, which this agent
integration cannot dispatch.

## 10. Sign-off

- [x] Rollback plan is concrete and testable, and states why the previous
      change's env-var lever does not cover this one
- [x] Blast radius is stated, not assumed — every consumer named
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 states the admin-visible effect, including mid-session
