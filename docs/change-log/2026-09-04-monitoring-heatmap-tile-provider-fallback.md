# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude (session requested by vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Live Monitoring map "Failed to load map style. Check network/tile provider." — reported directly by the user |

## 1. Issue / gap identified

Live Monitoring and Heat Map both render a MapLibre GL map whose only tile source is `tiles.openfreemap.org`. That host is unreachable from the admin's own network — confirmed both from this session's sandbox (`403 connect_rejected` via the proxy) and, per the user, from the actual deployed admin dashboard. OpenFreeMap has no SLA and no bundled fallback provider, so a single host outage/block takes down both pages with no recovery path.

## 2. Root cause

`admin-dashboard/src/lib/map/maplibre-base.ts` hardcodes OpenFreeMap as the only style source for these two pages. A `MAP_STYLE_FALLBACK` constant already existed but pointed at the *same host*, different style path — useless if the whole host is down. A real alternate provider (Protomaps, via `protomapsStyleUrl()`) was already wired for the separate public ride-tracking page, but never connected to Live Monitoring or Heat Map.

## 3. Fix / remediation

Added `monitoringFallbackStyle(flavor)` in `maplibre-base.ts` (thin wrapper over the existing `protomapsStyleUrl()`, returns `null` when no `NEXT_PUBLIC_PROTOMAPS_API_KEY` is configured). Both `monitoring-map.tsx` and `heat-map.tsx` now retry once against this fallback when the primary OpenFreeMap style fails to load, tearing down and rebuilding the MapLibre instance against the fallback URL (MapLibre doesn't support swapping an already-mounted map's style at runtime without losing sources/layers, per the existing `themedMapStyle()` doc comment — recreating the instance is the same pattern this codebase already uses for a theme change). If the fallback also fails, or no key is configured, both pages fall back to their existing "Failed to load map style" error state — unchanged behavior from before this fix in that case.

This does **not** fix the underlying network/host-reachability problem — nothing in this repo can. It adds resilience so a single provider outage doesn't have to fully block the page, once the user configures a Protomaps API key.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two files' map-initialization effects** (`monitoring-map.tsx`, `heat-map.tsx`) plus one new pure helper in `maplibre-base.ts`. Grepped for other consumers of `themedMapStyle`/`MAP_STYLE_POSITRON`/`protomapsStyleUrl` — the public tracking page's own `trackBaseMapStyle()` call is untouched; this change adds a new function, it doesn't modify any existing exported one.
- **No behavior change when `NEXT_PUBLIC_PROTOMAPS_API_KEY` is unset** (the case today, in every environment): `protomapsStyleUrl()` returns `null`, the retry condition (`fallback && fallback !== styleUrl`) is false, and both pages fall through to the exact same "Failed to load map style" error state they show today. This fix is a pure addition, live only once the env var is set.
- **CI visual-regression risk: none expected.** `e2e/visual-regression.spec.ts` stubs `**/tiles.openfreemap.org/**` with a minimal always-succeeding style (added 2026-09-04 for `dashboard-monitoring`'s own baseline determinism) — the primary style never fails during that test, so the new retry branch is dead code in CI and should produce zero diff on the `dashboard-monitoring` baseline. (Heat Map is not one of the 6 baselined pages.)
- **Marker/data-sync logic unaffected**: `updateDriverMarker`/`updateRideMarkers`/etc. all read `mapRef.current` at call time rather than closing over a specific map instance, so a mid-lifecycle rebuild (the retry) transparently continues working against whichever instance is current — same pattern the existing cleanup function already relied on.
- **Guarded against retry loops**: a local `usedFallback` flag caps the retry at exactly one attempt per mount; a fallback that itself fails to load surfaces the error state rather than looping.

## 5. User-experience effect

Internal-admin-facing only (Live Monitoring, Heat Map). No change today (no key configured). Once a Protomaps key is added: an admin whose network can't reach OpenFreeMap will now see the map render (via Protomaps) instead of the error message — same page, likely a slightly different basemap style/labeling than OpenFreeMap's, since it's a different tile provider. Not mid-session-disruptive — this only affects the initial map load, not an already-rendered map.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/maplibre-base.ts` | Added `monitoringFallbackStyle(flavor)` | Expose the already-wired Protomaps path for these two pages to use |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Map-init effect refactored into a `buildMap(styleUrl)` function; `error` handler retries once against the fallback before surfacing `loadError` | Automatic recovery from a primary-provider outage |
| `admin-dashboard/src/components/heat-map.tsx` | Same `buildMap` refactor; fallback requests the `grayscale` flavor to match Positron's "muted so heat layers pop" intent | Same recovery, applied to the Heat Map page |

## 7. Before / after

```tsx
// Before (monitoring-map.tsx) — error always surfaces, no retry
map.on("error", (e) => {
    if (cancelled) return;
    const err = e?.error as Error | undefined;
    if (err && /style/i.test(err.message ?? "")) {
        setLoadError(err.message);
    }
});
```
```tsx
// After — retries once against Protomaps (if configured) before giving up
map.on("error", (e) => {
    if (cancelled) return;
    const err = e?.error as Error | undefined;
    if (!(err && /style/i.test(err.message ?? ""))) return;

    const fallback = !usedFallback ? monitoringFallbackStyle(resolvedTheme) : null;
    if (fallback && fallback !== styleUrl) {
        usedFallback = true;
        map.remove();
        buildMap(fallback);
        return;
    }
    setLoadError(err.message);
});
```

## 8. Rollback plan

`git-revert-safe` — no data, no migration, no config change shipped by this PR itself (the env var is opt-in and configured separately, outside this diff). Reverting restores the exact prior behavior (error-only, no retry).

## 9. Verification performed

- [x] `tsc --noEmit` — clean, no new type errors.
- [x] Full production build (`npm run build`, Turbopack) — succeeded, no errors.
- [x] Ran the actual `dashboard-monitoring` Playwright visual-regression test locally against this change (real browser, real build) — **the only diff found was pre-existing and unrelated to this change** (a stale sidebar-state baseline from an earlier, separately-tracked issue — see the same-day change-log/PR discussion; not caused by anything in this diff, confirmed by checking that this change touches no sidebar code and the primary tile style is stubbed-success in that test either way).
- [ ] Not tested with a real Protomaps API key / real network block — no key configured in this sandbox, and no way to simulate the admin's actual network restriction here. The no-key path (identical to current behavior) was verified; the with-key retry path was reasoned about via code review only, not exercised end-to-end.

## What was NOT verified

Not visually screenshotted with an actual Protomaps key configured — this sandbox has none, and the sandbox's own network block on `tiles.openfreemap.org` is a proxy-imposed block that doesn't necessarily mirror the admin's real corporate/office network's specific restrictions, so end-to-end behavior with a live key should be spot-checked by the user (or in staging) before relying on it operationally.
