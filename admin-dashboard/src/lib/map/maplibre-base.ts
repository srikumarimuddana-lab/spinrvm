// src/lib/map/maplibre-base.ts
// Shared MapLibre GL helpers used by every admin-dashboard map component.
// The goal is to keep tile URLs, default centres, marker styling and
// fitBounds behaviour in one place so individual map files stay small.

import type { LngLatBoundsLike, Map as MapLibreMap } from "maplibre-gl";
import maplibregl from "maplibre-gl";
import {
    ROUTE_MARKER_SIZE,
    routePinSvg,
    type RoutePinKind,
} from "@spinr/shared/constants/routeMapStyle";

// OpenFreeMap styles — free, no API key, vector tiles.
// Swap the key in the URL to change the look (liberty / positron / bright / dark-matter).
export const MAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
export const MAP_STYLE_POSITRON = "https://tiles.openfreemap.org/styles/positron";

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

/** Fit bounds given an array of [lng, lat] or {lat,lng} points. */
export function fitBoundsToPoints(
    map: MapLibreMap,
    points: Array<[number, number] | { lat: number; lng: number }>,
    padding = 40,
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
