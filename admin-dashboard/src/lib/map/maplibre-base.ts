/// <reference types="geojson" />
// src/lib/map/maplibre-base.ts
// Shared MapLibre GL helpers used by every admin-dashboard map component.
// The goal is to keep tile URLs, default centres, marker styling and
// fitBounds behaviour in one place so individual map files stay small.

import type { LngLatBoundsLike, Map as MapLibreMap } from "maplibre-gl";
import * as maplibregl from "maplibre-gl";
import {
    ROUTE_MARKER_SIZE,
    routePinSvg,
    type RoutePinKind,
} from "@spinr/shared/constants/routeMapStyle";

// MapLibre v6's default worker bootstrap bundles the tile-processing Web
// Worker via an `import.meta.url`-derived module URL -- a technique Next.js
// 16's Turbopack does not resolve the same way Webpack does, so the worker
// spawns but never actually fetches a tile (every admin map rendered a
// blank canvas with only controls/attribution -- the reason this dependency
// was downgraded to v4.7.1 on 2026-09-14, PR #5427, which pre-dates a
// critical XSS-sanitizer CVE disclosed against every version <=6.4.0,
// GHSA-jrc7-96c5-q579). Pointing MapLibre at a same-origin static copy of
// its own worker module bypasses that automatic bundling entirely -- this
// is MapLibre's own documented escape hatch for exactly this class of
// bundler incompatibility, not a workaround specific to this app.
// scripts/copy-maplibre-worker.mjs (wired as predev/prebuild) bundles the
// worker entry point -- with the shared chunk it would otherwise import via
// a relative specifier inlined via esbuild, not copied as a second file --
// into public/maplibre/ from whatever maplibre-gl version package.json
// actually resolves. The inlining matters: a worker's own top-level script
// fetch is unambiguously covered by this app's `worker-src 'self'` CSP
// directive, but a *nested* static `import` inside that script is, per the
// CSP spec's own open ambiguity (see the copy script's comment), commonly
// treated as governed by `script-src` instead -- which has no `'self'` and
// no way to attach this app's per-request nonce to an import specifier.
// Bundling to a single file removes that nested fetch entirely rather than
// gambling on which directive a given browser applies to it.
// Must run before any `new maplibregl.Map(...)` call -- safe as a
// module-level side effect here since every map component already imports
// from this shared file before constructing its own map instance.
maplibregl.setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

// OpenFreeMap styles — free, no API key, vector tiles.
// Swap the key in the URL to change the look (liberty / positron / bright / dark-matter).
export const MAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
export const MAP_STYLE_POSITRON = "https://tiles.openfreemap.org/styles/positron";
// Dark counterpart to MAP_STYLE_URL — every admin map that reaches for a
// light OpenFreeMap style directly (not via trackBaseMapStyle(), which is
// rider-tracking-specific) should pick between this pair with
// themedMapStyle() instead of hardcoding MAP_STYLE_URL, so a cream-white
// basemap never sits inside an otherwise dark admin UI.
export const MAP_STYLE_DARK = "https://tiles.openfreemap.org/styles/dark-matter";

/**
 * Pick the admin-dashboard basemap style for the given resolved theme
 * (from next-themes' `useTheme()`). Callers read this once at map-creation
 * time — MapLibre's setStyle() drops runtime-added sources/layers, so we
 * don't support live-swapping an already-mounted map's style; remounting
 * the component (e.g. keying it by resolvedTheme) picks up a theme change.
 */
export function themedMapStyle(resolvedTheme: string | undefined): string {
    return resolvedTheme === "dark" ? MAP_STYLE_DARK : MAP_STYLE_URL;
}

// Protomaps hosted basemap. The API key is a *public*, domain-restricted key
// (like a Google Maps JS key) and is meant to ship in the browser, so it lives
// in a NEXT_PUBLIC_ env var rather than the backend app_settings table.
// The hosted style endpoint returns a full MapLibre style with the vector tile
// source + glyphs/sprite already wired to the key, so we just hand MapLibre the
// URL. Flavor is configurable (light / dark / white / grayscale / black).
const PROTOMAPS_KEY = process.env.NEXT_PUBLIC_PROTOMAPS_API_KEY?.trim() || "";
const PROTOMAPS_FLAVOR = process.env.NEXT_PUBLIC_PROTOMAPS_FLAVOR?.trim() || "light";

/** Hosted Protomaps style URL for the given flavor, or null if no key is set. */
export function protomapsStyleUrl(flavor: string = PROTOMAPS_FLAVOR): string | null {
    if (!PROTOMAPS_KEY) return null;
    return `https://api.protomaps.com/styles/v5/${flavor}/en.json?key=${PROTOMAPS_KEY}`;
}

/**
 * Basemap style for the public ride-tracking page. Resolution order:
 *   1. NEXT_PUBLIC_MAP_STYLE_URL — explicit style.json (e.g. a self-hosted
 *      PMTiles server we stand up later; swapping providers is a pure env
 *      change, no code edit).
 *   2. Protomaps hosted (when NEXT_PUBLIC_PROTOMAPS_API_KEY is set).
 *   3. OpenFreeMap (free, keyless, labeled vector tiles).
 * Either way the rider gets a proper labeled basemap — never the raw OSM raster
 * tiles, which OSM's tile-usage policy forbids for app use and which load
 * blank/throttled in the field.
 */
export function trackBaseMapStyle(): string {
    const override = process.env.NEXT_PUBLIC_MAP_STYLE_URL?.trim();
    if (override) return override;
    return protomapsStyleUrl() ?? MAP_STYLE_POSITRON;
}

/** Keyless fallback style, used if the primary style fails to load at runtime.
 *  Must differ from the primary so the error-handler's `primaryStyle !== MAP_STYLE_FALLBACK`
 *  guard can actually switch styles on failure. */
export const MAP_STYLE_FALLBACK = MAP_STYLE_URL; // liberty — different look from positron

/**
 * Fallback basemap style for the admin dashboard's Live Monitoring and Heat
 * Map pages, tried when the primary keyless OpenFreeMap style fails to load.
 * Unlike MAP_STYLE_FALLBACK above (still OpenFreeMap, just a different style
 * path — useless if the whole host is unreachable), this is a genuinely
 * different provider: `tiles.openfreemap.org` being unreachable from an
 * admin's network is a real, recurring failure (not hypothetical — confirmed
 * against the actual deployed dashboard, 2026-09-04), so the fallback needs a
 * different host entirely.
 *
 * Returns null when no NEXT_PUBLIC_PROTOMAPS_API_KEY is configured, in which
 * case there is nothing to fall back to and the caller should keep its
 * existing "failed to load" error state.
 */
export function monitoringFallbackStyle(flavor: string = "light"): string | null {
    return protomapsStyleUrl(flavor);
}

// Carto (basemaps.cartocdn.com) was removed as a basemap provider on 2026-09-14
// at the product owner's request: Spinr serves its own tiles (deploy/tiles) and
// should not hand admin map traffic to a third-party CDN, not even as a last
// resort. What that costs is real and deliberate — Carto was the only keyless
// hop, so when NEXT_PUBLIC_PROTOMAPS_API_KEY is unset the unconfigured chain is
// now OpenFreeMap alone. Do not reintroduce it as a "safety net"; stand the tile
// server up instead.
//
// One Carto reference survives on purpose, in
// rides/_components/static-route-map.tsx's rasterAttribution(): it credits Carto
// only if an operator points NEXT_PUBLIC_RASTER_TILE_URL at them. That is an
// attribution-licence guard, not a provider — deleting it would under-credit
// them if anyone ever does.

/**
 * Our own tile server, when one is configured (see deploy/tiles).
 *
 * `NEXT_PUBLIC_MAP_STYLE_URL` was already the highest-priority source for the
 * public tracking page (trackBaseMapStyle above) but was NOT read by the admin
 * chain, so setting it did nothing for the dashboard's maps. It is read here
 * too now, which is what makes standing up deploy/tiles a config change rather
 * than a code change.
 *
 * `_DARK` is optional: an operator who only builds a light style still gets it
 * in dark mode, which beats silently dropping back to a third party.
 *
 * Read inside the function rather than at module scope (unlike PROTOMAPS_KEY
 * above) so tests can drive it with vi.stubEnv. Next.js inlines
 * `process.env.NEXT_PUBLIC_*` wherever the literal appears, so this is still a
 * build-time constant in a real build — the two spellings differ only in
 * testability.
 */
export function selfHostedStyleUrl(resolvedTheme?: string): string | null {
    const light = process.env.NEXT_PUBLIC_MAP_STYLE_URL?.trim() || "";
    const dark = process.env.NEXT_PUBLIC_MAP_STYLE_URL_DARK?.trim() || "";
    if (resolvedTheme === "dark") return dark || light || null;
    return light || null;
}

/**
 * Basemap providers for an admin map. Two distinct shapes:
 *
 *   Self-hosted configured  → [self-hosted]                    (nothing else)
 *   Self-hosted unconfigured → OpenFreeMap → Protomaps?
 *
 * Once we serve our own basemap it is the *only* basemap. That is a deliberate
 * trade, made by the product owner on 2026-09-14, and it costs something real:
 * our tile server going down now blanks the admin maps instead of quietly
 * degrading to somebody else's. What it buys is that the first hop is ours, so
 * admins stop paying an 8s timeout on a donation-funded CDN before every map
 * paints — the banner that timeout produces was the original reason
 * deploy/tiles exists at all.
 *
 * The third-party chain is kept for the unconfigured case rather than deleted,
 * for two reasons that are not stylistic:
 *   - An empty chain paints nothing. If NEXT_PUBLIC_MAP_STYLE_URL is missing or
 *     scoped to the wrong Vercel environment, deleting the third parties turns
 *     a misconfiguration into blank maps with no fallback at all.
 *   - CI does not set NEXT_PUBLIC_MAP_STYLE_URL, and
 *     e2e/visual-regression.spec.ts stubs tiles.openfreemap.org. Removing
 *     OpenFreeMap as the unconfigured first hop would blank the seeded
 *     dashboard-monitoring baseline and fail a merge-blocking gate.
 *
 * Passing no theme yields the light styles.
 *
 * Deduplicated because a hop that repeats an earlier URL is not a fallback: it
 * re-requests the host that just failed and burns a whole 8s watchdog window
 * doing it.
 */
/**
 * The one style to use for a map that has no fallback chain of its own.
 *
 * Several admin maps (driver, geofence, venue, live-ride) hand MapLibre a single
 * `style` and never retry, so they cannot use basemapChain(). They previously
 * hard-coded MAP_STYLE_URL, which meant they kept loading a third-party basemap
 * even with our own tile server configured — the chain-using maps switched over
 * and these four silently did not.
 *
 * Self-hosted when configured, otherwise exactly the style they used before.
 */
export function primaryMapStyle(resolvedTheme?: string): string {
    return selfHostedStyleUrl(resolvedTheme) ?? themedMapStyle(resolvedTheme);
}

export function basemapChain(resolvedTheme?: string): string[] {
    const selfHosted = selfHostedStyleUrl(resolvedTheme);
    if (selfHosted) return [selfHosted];

    const protomaps =
        resolvedTheme === "dark" ? protomapsStyleUrl("dark") : protomapsStyleUrl();
    const ordered = [
        themedMapStyle(resolvedTheme),
        ...(protomaps ? [protomaps] : []),
    ];
    return [...new Set(ordered)];
}

/** How long a basemap gets to fire `load` before it is treated as failed. */
export const BASEMAP_LOAD_TIMEOUT_MS = 8000;

export interface BasemapFallbackHandlers {
    /** Another provider is available — rebuild the map against `nextStyleUrl`. */
    onRetry: (nextStyleUrl: string, nextAttempt: number) => void;
    /** Every provider in the chain is exhausted. `reason` is safe to show an admin. */
    onExhausted: (reason: string) => void;
    /**
     * This attempt's basemap loaded. Optional, but a caller that shows a
     * "retrying…" affordance MUST implement it: onRetry is the only signal that
     * a hop happened, and without a matching success signal that banner has
     * nothing to clear it and stays on screen over a perfectly good map for the
     * life of the mount.
     */
    onLoaded?: (attempt: number) => void;
}

/**
 * Watch one map's basemap load and walk `chain` when it fails.
 *
 * Two failure modes, both of which have actually produced a blank admin map:
 *   - an explicit `error` (style 404, DNS failure, blocked host)
 *   - silence: tile requests that hang, or a stale cached TileJSON pointing at
 *     an expired dated build, leave the map blank forever and fire no error at
 *     all. Only the timeout catches that one.
 *
 * Deliberately keyed on the `load` event rather than on "did a tile actually
 * paint": e2e/visual-regression.spec.ts stubs tiles.openfreemap.org with a
 * source-less style that fires `load` immediately and issues no tile requests.
 * Keying on painted tiles would make CI hop to an un-stubbed third-party host
 * and reintroduce exactly the network-dependent baseline flake ACTION_ITEMS.md
 * B38 closed.
 *
 * Only pre-`load` failures switch providers. Once `load` has fired the map is
 * usable, and a single 404 on one tile must never tear down a working map and
 * swap its whole look out from under the admin mid-session.
 *
 * Returns a detach function; call it before removing the map.
 */
export function attachBasemapFallback(
    map: MapLibreMap,
    chain: string[],
    attempt: number,
    handlers: BasemapFallbackHandlers,
    timeoutMs: number = BASEMAP_LOAD_TIMEOUT_MS,
): () => void {
    let settled = false;

    // `timer` is declared below but only ever *called* into after it is
    // initialised, so closing over it here is TDZ-safe.
    const settle = () => {
        settled = true;
        clearTimeout(timer);
    };

    const advance = (reason: string) => {
        if (settled) return;
        settle();
        const nextAttempt = attempt + 1;
        const next = chain[nextAttempt];
        if (next) handlers.onRetry(next, nextAttempt);
        else handlers.onExhausted(reason);
    };

    const onLoad = () => {
        if (settled) return;
        settle();
        handlers.onLoaded?.(attempt);
    };

    // MapLibre's ErrorEvent carries an `ErrorLike`, not a full `Error` (no
    // `name`), so the parameter has to be this loose to satisfy the overload.
    const onError = (e: { error?: { message?: string } }) => {
        if (settled) return;
        advance(e?.error?.message || "Basemap failed to load.");
    };

    const timer = setTimeout(() => advance("Basemap timed out."), timeoutMs);

    map.on("load", onLoad);
    map.on("error", onError);

    return () => {
        settle();
        map.off("load", onLoad);
        map.off("error", onError);
    };
}

// Saskatoon by default — Spinr is a Saskatchewan-first service, so maps
// should land somewhere operational even before service areas load or
// the user's geolocation resolves. MapLibre uses [lng, lat] ordering.
export const DEFAULT_CENTER: [number, number] = [-106.67, 52.13];

/**
 * THE ride pickup / dropoff / completion marker, as a DOM element.
 *
 * Draws `routePinSvg()` from shared/constants/routeMapStyle — the same disc,
 * ring and glyph the phone apps and the Android Auto surface render. Admin maps
 * previously built bare 16/20px circles here with no glyph at all, so the same
 * ride looked like a different product on every screen it appeared on.
 *
 * Use this for pickup/dropoff/completion. `makeCircleMarkerEl` below stays for
 * everything else (drivers, venues, service areas), which is not part of the
 * route-marker language.
 */
export function makeRoutePinEl(opts: {
    kind: RoutePinKind;
    size?: number;
    title?: string;
}): HTMLDivElement {
    const size = opts.size ?? ROUTE_MARKER_SIZE;
    const el = document.createElement("div");
    el.className = "spinr-map-marker spinr-route-pin";
    el.style.width = `${size}px`;
    el.style.height = `${size}px`;
    el.style.cursor = "pointer";
    el.style.filter = "drop-shadow(0 1px 3px rgba(0,0,0,0.35))";
    el.innerHTML = routePinSvg(opts.kind, size);
    if (opts.title) el.title = opts.title;
    return el;
}

/** Build a styled DOM <div> for a circular map marker. */
export function makeCircleMarkerEl(opts: {
    color: string;
    label?: string;
    title?: string;
    size?: number;
    textColor?: string;
}): HTMLDivElement {
    const size = opts.size ?? 20;
    const el = document.createElement("div");
    el.className = "spinr-map-marker";
    el.style.width = `${size}px`;
    el.style.height = `${size}px`;
    el.style.borderRadius = "50%";
    el.style.backgroundColor = opts.color;
    el.style.border = "2px solid #ffffff";
    el.style.boxShadow = "0 1px 4px rgba(0,0,0,0.3)";
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.style.color = opts.textColor ?? "#ffffff";
    el.style.fontSize = `${Math.max(10, Math.round(size / 2))}px`;
    el.style.fontWeight = "bold";
    el.style.cursor = "pointer";
    if (opts.label) el.textContent = opts.label;
    if (opts.title) el.title = opts.title;
    return el;
}

/** Compute [lng, lat] bounds for a GeoJSON Feature / FeatureCollection / Geometry. */
function extendBoundsFromCoords(
    bounds: [[number, number], [number, number]] | null,
    coords: GeoJSON.Position,
): [[number, number], [number, number]] {
    const [lng, lat] = coords;
    if (!bounds) return [[lng, lat], [lng, lat]];
    return [
        [Math.min(bounds[0][0], lng), Math.min(bounds[0][1], lat)],
        [Math.max(bounds[1][0], lng), Math.max(bounds[1][1], lat)],
    ];
}

function walkGeometry(
    geom: GeoJSON.Geometry,
    cb: (pos: GeoJSON.Position) => void,
): void {
    switch (geom.type) {
        case "Point":
            cb(geom.coordinates);
            break;
        case "MultiPoint":
        case "LineString":
            geom.coordinates.forEach(cb);
            break;
        case "MultiLineString":
        case "Polygon":
            geom.coordinates.forEach((ring) => ring.forEach(cb));
            break;
        case "MultiPolygon":
            geom.coordinates.forEach((poly) => poly.forEach((ring) => ring.forEach(cb)));
            break;
        case "GeometryCollection":
            geom.geometries.forEach((g) => walkGeometry(g, cb));
            break;
    }
}

/** Pan/zoom the map to fit a GeoJSON Feature or FeatureCollection. */
export function fitBoundsToGeoJSON(
    map: MapLibreMap,
    geojson: GeoJSON.Feature | GeoJSON.FeatureCollection | GeoJSON.Geometry,
    padding = 40,
): void {
    let bounds: [[number, number], [number, number]] | null = null;
    const visit = (pos: GeoJSON.Position) => {
        bounds = extendBoundsFromCoords(bounds, pos);
    };
    if (geojson.type === "FeatureCollection") {
        geojson.features.forEach((f) => walkGeometry(f.geometry, visit));
    } else if (geojson.type === "Feature") {
        walkGeometry(geojson.geometry, visit);
    } else {
        walkGeometry(geojson as GeoJSON.Geometry, visit);
    }
    if (!bounds) return;
    map.fitBounds(bounds as LngLatBoundsLike, { padding, duration: 500 });
}

/** Fit bounds given an array of [lng, lat] or {lat,lng} points.
 *  `padding` accepts MapLibre's per-edge object as well as a single number, for
 *  callers that overlay chrome on one edge and must keep fitted points clear of
 *  it — note markers anchor at their *centre*, so a pin overhangs its own
 *  coordinate by half its height on top of whatever padding is set here. */
export function fitBoundsToPoints(
    map: MapLibreMap,
    points: Array<[number, number] | { lat: number; lng: number }>,
    // All four edges required when passing the object form — MapLibre's own
    // PaddingOptions does the same, and a partial object is not assignable to it.
    padding: number | { top: number; bottom: number; left: number; right: number } = 40,
): void {
    let bounds: [[number, number], [number, number]] | null = null;
    points.forEach((p) => {
        const pos: GeoJSON.Position = Array.isArray(p) ? p : [p.lng, p.lat];
        bounds = extendBoundsFromCoords(bounds, pos);
    });
    if (!bounds) return;
    map.fitBounds(bounds as LngLatBoundsLike, { padding, duration: 500 });
}

/**
 * Convert the admin-dashboard "{lat,lng}[] polygon" shape into a GeoJSON
 * Polygon geometry. The input may omit the closing ring point; we append
 * it if needed.
 */
export function polygonPointsToGeoJSON(
    points: Array<{ lat: number; lng: number }>,
): GeoJSON.Polygon | null {
    if (!points || points.length < 3) return null;
    const ring = points.map((p) => [p.lng, p.lat] as GeoJSON.Position);
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) ring.push(first);
    return { type: "Polygon", coordinates: [ring] };
}

/** Add a standard NavigationControl (zoom buttons only, no compass). */
export function addStandardControls(map: MapLibreMap): void {
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
}
