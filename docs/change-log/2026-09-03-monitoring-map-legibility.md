# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude, at user request — reported via two screenshots (light + dark mode) of the Live Ride Monitoring screen: map appears to show nothing, bottom-of-map text/pills don't adapt to theme, request to declutter |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | commit `fb3cf9de5`, branch `claude/admin-monitoring-map-legibility` |
| Related issue or gap ID | None filed — direct bug report in conversation |

## 1. Issue / gap identified

On the Live Ride Monitoring screen, the map area reads as effectively blank in both light and dark mode — no visible service-area boundaries, and the small UI sitting at the bottom of the map (the "jump to city" pills and MapLibre's own attribution control) doesn't adapt to the admin's light/dark theme and visually collides.

## 2. Root cause

Three independent, unrelated bugs were producing the one visual symptom:

1. **Basemap never varies by theme.** `monitoring-map.tsx` hardcoded `MAP_STYLE_URL` (OpenFreeMap's "liberty" style, a light basemap) regardless of the admin dashboard's own light/dark setting. In dark mode this put a cream-colored map inside an otherwise near-black UI — visually reading as "not rendering" even though it was.
2. **Service-area polygons render, but near-invisibly.** Verified directly against the `service_areas` table (Supabase, `soavhtdhefowwvforzwb`): all 6 configured areas (Regina, Regina Airport, Saskatoon, Saskatoon Airport, and two test areas named "riyadh"/"riyadh airport" — see note below) have valid polygon geometry, including Saskatoon's, which fully contains the map's default center point. The polygon fill was set to 8% opacity — a level chosen deliberately (comment: "pre-overlay appearance, unchanged... pixel-identical to the original map") but in practice imperceptible against a light basemap. This is a data-was-there-all-along legibility bug, not a missing-data bug.
3. **Bottom-of-map UI hardcoded to a fixed light style.** The "jump to city" pills used `bg-white/90` unconditionally (no dark-mode variant), and MapLibre's own attribution control ships fixed light-chip/dark-text CSS with no theme awareness at all — neither was ever touched when the rest of the admin UI got dark-mode support. The pills' `bottom-4` position also sits close enough to the map's bottom edge that on a wide `serviceAreas` list they can overlap the attribution control living at the very bottom-right.

## 3. Fix / remediation

- Added `MAP_STYLE_DARK` (OpenFreeMap's "dark-matter" style) and a `themedMapStyle(resolvedTheme)` helper to the shared `maplibre-base.ts`, so any admin map can request the theme-matched basemap through one call instead of a hardcoded constant.
- `monitoring-map.tsx` now calls `themedMapStyle(resolvedTheme)` (via `next-themes`' `useTheme()`) when constructing the map. MapLibre's `setStyle()` drops runtime-added sources/layers (the service-area polygons, ride-route lines), so this isn't a live in-place swap — the map is keyed by `resolvedTheme` in `page.tsx` (`<MonitoringMap key={resolvedTheme} .../>`), so toggling the app's theme cleanly remounts the map with the correct basemap instead of leaving a stale one until the next navigation.
- Service-area fill opacity raised 0.08 → 0.14 and boundary line width 2 → 2.5, with the code comment updated to document this as the new deliberate baseline (rather than silently drifting from the prior "pixel-identical" comment).
- `globals.css`: added a scoped `.maplibregl-ctrl-attrib` override using the existing `--card`/`--muted-foreground`/`--foreground` tokens, so MapLibre's attribution chip matches the surrounding UI in both themes. The attribution *text* itself (OpenFreeMap / OpenStreetMap credit) is preserved as-is — see licensing note below. The (i) toggle icon is a fixed-color background-image SVG baked into the library's CSS; inverted under `.dark` via `filter: invert(1) brightness(1.3)` rather than forking the icon asset.
- `page.tsx`: jump-to-city pills changed from `bg-white/90 ... ring-black/10` to `bg-background/90 border-border text-foreground` (theme-aware), and repositioned from `bottom-4` to `bottom-12` with a `max-w` + `flex-wrap` so the row can't extend into the attribution control's corner.

**Not changed — flagged instead of silently acted on:**
- **Attribution removal.** The user asked "if we can get rid of the open map info bar." OpenFreeMap's tiles are built from OpenStreetMap data under the ODbL license, which requires attribution; OpenFreeMap's own hosting terms carry the same requirement. It was not removed — only restyled and left in MapLibre's `compact: true` mode (already set), which shows just the small (i) toggle rather than a persistent full-width bar unless expanded. Full removal would need switching to a tile provider whose license doesn't require attribution (a paid plan on Protomaps, Mapbox, etc.), which is a real product/cost decision, not something to make unilaterally inside a bug-fix pass.
- **"riyadh" / "riyadh airport" service areas.** Found while verifying polygon data — two active, non-Saskatchewan test service areas exist in what appears to be the production database. Flagged for the user's attention, not touched (not this task's call to deactivate/delete).
- **The other 5 admin map components** (`driver-map.tsx` — confirmed dead/unused, not imported anywhere; `geofence-map.tsx`, `venue-map.tsx`, `ride-route-map.tsx`, `live-map.tsx`) still hardcode the light basemap. `heat-map.tsx` deliberately stays light/grayscale per its own existing comment ("grayscale so heat layers pop") and is correctly left alone. The other four are reasonable candidates for the same `themedMapStyle()` fix but were left out of this pass — `geofence-map.tsx` in particular is an interactive polygon-drawing tool where a theme-triggered remount needs more care (in-progress edit state) than the read-only Monitoring map, and deserves its own focused pass rather than being rushed into this one.

## 4. Risk & impact on existing functionality

- **Blast radius:** `maplibre-base.ts` is imported by 9 files, but this change only *adds* two new exports (`MAP_STYLE_DARK`, `themedMapStyle`) — nothing existing was renamed or altered, so the other 8 files are unaffected. `globals.css`'s new attribution rules are scoped to MapLibre's own `.maplibregl-ctrl-attrib*` classes, which don't exist anywhere else in the app's markup — no collision risk with other components. The `monitoring-map.tsx`/`page.tsx` changes are isolated to the Live Monitoring screen.
- **The `key={resolvedTheme}` remount**: forces a full MapLibre teardown/recreate on every theme toggle while this page is open. This is a deliberate, accepted trade-off (a brief map reload flash on a rare action) over the alternative (a live `setStyle()` call that would require re-adding every service-area/ride-line source and re-creating every driver/ride marker by hand — meaningfully more code and more surface for a marker to silently not come back after a style swap).
- **Service-area opacity/line-width bump**: purely a visual intensity change on an already-existing layer; no new data path, no new query, nothing that can fail.

## 5. User-experience effect

Admin-facing only (Live Monitoring screen). With dark mode selected, the map now loads a dark basemap instead of a light one; service-area boundaries are more visible in both themes; the "jump to city" pills and the map's attribution chip now read correctly against a dark background instead of showing as a stray light rectangle; the pills no longer risk overlapping the attribution control. No functional change — clicking a pill still fits the map to that area exactly as before.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/lib/map/maplibre-base.ts` | Added `MAP_STYLE_DARK` + `themedMapStyle()` helper | Shared, reusable theme→style mapping |
| `admin-dashboard/src/app/dashboard/monitoring/monitoring-map.tsx` | Use `themedMapStyle(resolvedTheme)`; bump area fill/line visibility | Dark-mode basemap; legible service areas |
| `admin-dashboard/src/app/dashboard/monitoring/page.tsx` | Key `<MonitoringMap>` by `resolvedTheme`; retheme + reposition jump-to-city pills | Clean remount on theme toggle; contrast + collision fix |
| `admin-dashboard/src/app/globals.css` | Scoped `.maplibregl-ctrl-attrib*` theming | Attribution control legible in both themes, app-wide |

## 7. Before / after

```tsx
// Before — monitoring-map.tsx
const map = new maplibregl.Map({
    container: containerRef.current,
    style: MAP_STYLE_URL,   // always light, regardless of app theme
    ...
});

// After
const map = new maplibregl.Map({
    container: containerRef.current,
    style: themedMapStyle(resolvedTheme),   // dark-matter under .dark
    ...
});
```

```tsx
// Before — page.tsx jump-to-city pill
className="rounded-full bg-white/90 px-3 py-1 text-xs font-medium shadow ring-1 ring-black/10 backdrop-blur hover:bg-white"

// After
className="rounded-full border border-border bg-background/90 px-3 py-1 text-xs font-medium text-foreground shadow backdrop-blur hover:bg-accent"
```

## 8. Rollback plan

Pure `git revert` — no data, no migration, no feature flag involved (shipped unconditionally per the user's explicit choice, since these are legibility bugs rather than a style opinion).

## 9. Verification performed

- [x] `tsc --noEmit` — no new errors.
- [x] `eslint` on the 3 changed files — 0 errors. (Worked around this repo's known, pre-existing eslint 10.9.1 / eslint-plugin-react incompatibility — see the standing item flagged on PR #4831 — by linting with a local, unsaved `eslint@9.39.5` install, then restoring the pinned `eslint@10.9.1` afterward so the working tree's installed dependency state is unchanged.)
- [x] **Real production build** (`npm run build`) — completed with exit code 0, confirmed via a fresh log capture (`grep -i error` against the full build log, zero matches) rather than trusting a truncated terminal tail.
- [x] Verified the "service areas not showing" root cause directly against production data (Supabase `service_areas` table) rather than guessing — confirmed all 6 areas have polygon geometry, and Saskatoon's polygon numerically contains the map's default center point, ruling out "no polygon drawn" as the cause.

## What was NOT verified

- **No live browser screenshot of the fix.** admin-dashboard has no active visual-regression tooling (`ACTION_ITEMS.md` B38, standing gap). The dark "dark-matter" OpenFreeMap style URL could not be reached from this sandbox to confirm it resolves (outbound network to `tiles.openfreemap.org` was blocked here) — it's used by name only, per the pattern the file's own pre-existing comment already documented ("liberty / positron / bright / dark-matter" as the valid style-name swaps), not independently re-verified against the live CDN. Recommend an actual look in a browser (both themes) before calling this fully closed.
- **The 4 other map components left un-migrated** (see §3) were not touched, tested, or scoped beyond the note above.
- **The "riyadh" service areas** were observed, not investigated — no judgment made on whether they're intentional test fixtures or stray data.
