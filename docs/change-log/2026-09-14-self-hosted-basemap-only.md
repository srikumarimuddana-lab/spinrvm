# Change Impact & Risk Log — self-hosted basemap becomes the only basemap

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code session (requested by repo owner) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | this commit |
| Related issue or gap ID | none — owner request, following `deploy/tiles` going live |

## 1. Issue / gap identified

Admins see a "Basemap slow to load — trying another provider…" banner before
admin maps paint. It is the first hop's 8-second watchdog expiring on a
third-party CDN. Now that Spinr hosts its own tile server (`deploy/tiles`), the
owner asked that the admin maps stop falling through to third parties at all.

## 2. Root cause

Not a defect. `basemapChain()` was deliberately built to keep OpenFreeMap →
Protomaps → Carto *behind* a configured self-hosted style, so our tile server
going down degraded to somebody else's map rather than to nothing. That design
also means a third-party host is still the first hop whenever the self-hosted
style is unset — which it has been, because no production build has yet carried
`NEXT_PUBLIC_MAP_STYLE_URL`.

## 3. Fix / remediation

When `NEXT_PUBLIC_MAP_STYLE_URL` is set, the chain is now exactly
`[self-hosted]`. The third-party chain is retained **only** for the
unconfigured case.

Applied in two places, because the three admin maps do not share one chain:

- `basemapChain()` in `maplibre-base.ts` — used by `monitoring-map.tsx` and
  `ride-route-map.tsx`.
- `heat-map.tsx`, which builds its chain inline (its fallbacks are
  grayscale-preferring so heat layers pop against a muted basemap). Changing
  only the shared function would have left the heat map still hopping to Carto.

## 4. Risk & impact on existing functionality

- **Consumers of the changed code** (grepped, all three named): `basemapChain()`
  → `monitoring-map.tsx:444`, `ride-route-map.tsx:368`. `selfHostedStyleUrl()`
  → those two plus `heat-map.tsx:108`. No other importer exists.
- **What regresses**: resilience. Our tile server going down now blanks the
  admin maps rather than degrading to a third party. This is the accepted cost
  of the request, not an oversight.
  - `ride-route-map.tsx` is the exception — on exhaustion it sets
    `basemapStatus = "failed"`, which flips `useStatic` and hands over to the
    static raster renderer. That renderer reads `NEXT_PUBLIC_RASTER_TILE_URL ||
    Carto`, so it still paints as long as that variable is unset or points
    somewhere healthy.
  - `monitoring-map.tsx` and `heat-map.tsx` have no such second renderer.
- **The banner becomes unreachable** when self-hosting is configured: it renders
  only on `basemapStatus === "retrying"`, which is set only by `onRetry`, which
  `attachBasemapFallback` emits only when `chain[attempt + 1]` exists. A one-hop
  chain goes straight to `onExhausted`.
- **Why the unconfigured chain was kept rather than deleted** — two failure
  modes, both real:
  - An empty chain paints nothing. If the env var is missing, or scoped to the
    wrong Vercel environment, deleting the third parties converts a silent
    misconfiguration into blank maps with no fallback at all.
  - CI sets no style URL, and `e2e/visual-regression.spec.ts` stubs
    `tiles.openfreemap.org` (`ci.yml` line ~586 documents this). Removing it as
    the unconfigured first hop would blank the seeded `dashboard-monitoring`
    baseline and fail a merge-blocking gate.
- **Blast radius**: single-surface (admin-dashboard maps). No backend, ride
  state, dispatch, money or insurance-period code is touched.

## 5. User-experience effect

- **Internal admin**: on the monitoring map, heat map and ride route map, the
  "Basemap slow to load — trying another provider…" banner disappears and the
  map paints from our own tiles. If the tile server is unavailable, monitoring
  and heat maps show the failed state instead of a third-party basemap.
- **Rider / driver / corporate admin**: no change. This code is
  admin-dashboard only.
- **Mid-session visibility**: yes — an admin with a dashboard open when the new
  build ships gets the new basemap on the next map mount. No data or
  interaction changes, only which host draws the tiles.
- No copy changes; one existing banner simply stops being reachable.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/maplibre-base.ts` | `basemapChain()` returns `[selfHosted]` when configured | The requested behaviour, for monitoring + ride route maps |
| `admin-dashboard/src/components/heat-map.tsx` | Same conditional on its inline chain | It does not call `basemapChain()`; would otherwise still hop to Carto |
| `admin-dashboard/src/lib/__tests__/maplibre-basemap-chain.test.ts` | Two tests re-pinned to the new behaviour | They asserted the third parties stayed behind the self-hosted hop |

## 7. Before / after

```ts
// Before — self-hosted first, third parties behind it
const ordered = [
    ...(selfHosted ? [selfHosted] : []),
    themedMapStyle(resolvedTheme),
    ...(protomaps ? [protomaps] : []),
    cartoStyleUrl(resolvedTheme),
];
```

```ts
// After — self-hosted is the whole chain when configured
const selfHosted = selfHostedStyleUrl(resolvedTheme);
if (selfHosted) return [selfHosted];
// ...unconfigured case unchanged
```

## 8. Rollback plan

**Unset `NEXT_PUBLIC_MAP_STYLE_URL` in Vercel and redeploy.** That returns every
admin map to the exact third-party chain shipping today, with no code revert —
the environment variable is the flag for this change, which is why the
unconfigured path was left byte-for-byte intact.

A `git revert` also works and is safe (no data is written anywhere by this
change), but it needs a deploy either way, so the variable is the faster lever.

## 9. Verification performed

- [x] Blast-radius grep performed — `basemapChain`, `selfHostedStyleUrl`,
      `MAP_STYLE_CARTO_LIGHT`, `protomapsStyleUrl`, `themedMapStyle` across
      `admin-dashboard/src`. Three consumers found and all three handled; the
      heat map's inline chain was found this way.
- [x] Reviewed against `CLAUDE.md` gate #3 (shared component, 3+ consumers) —
      the change is gated by an environment variable that is currently unset in
      production, so it ships dark and is enabled by the same flip that enables
      self-hosting.
- [x] Reviewed against gate #6 (visual regression) — see §4; the unconfigured
      path is preserved specifically so the seeded `dashboard-monitoring`
      baseline and its `tiles.openfreemap.org` stub still hold.
- [ ] **Automated tests NOT run.** `npm install` fails with HTTP 403 from
      `registry.npmjs.org` under this environment's egress policy, so `vitest`,
      `tsc` and `eslint` could not be executed here. The two rewritten tests
      were reasoned through against the new implementation, not observed
      passing. CI is the first real execution.
- [ ] Manual repro in staging — not done; no staging admin-dashboard.
- [ ] **No production build run** (`npm run build`) for the same reason.

**What was NOT verified:** no test, typecheck, lint or build was executed
locally. The admin-dashboard has a real, merge-blocking Playwright
visual-regression job covering `dashboard-monitoring`; this change is expected
not to move that baseline *because* CI leaves the style URL unset, but that
expectation is reasoned, not observed. If the baseline does move, the fix is not
to re-seed it — it means the unconfigured path changed, which it should not have.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — unset one env var, redeploy
- [x] Blast radius is stated, not assumed — all three consumers named
- [x] No silent behavior change to an already-shipped flow without the UX field
      filled in — §5 states the admin-visible effect, including mid-session
