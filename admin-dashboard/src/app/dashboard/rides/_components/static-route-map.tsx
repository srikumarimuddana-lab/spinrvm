"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
    ROUTE_STROKE_WIDTH,
    buildPathGradient,
    routePinSvg,
    type LatLng,
} from "@spinr/shared/constants/routeMapStyle";

/**
 * A route map that uses no WebGL, no Web Worker and no vector tiles.
 *
 * MapLibre needs all three, and each one is a way the admin ride map has gone
 * blank in the field: a privacy/ad extension that stubs WebGL, a CSP or
 * extension that blocks the blob: tile worker, and a tile host that stops
 * answering. This renders plain <img> raster tiles in a CSS grid with the route
 * and pins drawn as an SVG overlay, so the only thing it needs to work is an
 * <img> that loads — and even when every tile 404s, the route and pins still
 * draw over the empty background.
 *
 * Used as RideRouteMap's fallback, not its default: MapLibre is still better
 * when it works (smooth zoom, crisp labels at any scale). See hasWebGL() and
 * the basemapStatus === "failed" branch in ride-route-map.tsx.
 *
 * Raster tiles come from Carto rather than OpenFreeMap because OpenFreeMap
 * serves vector tiles only — its one raster endpoint is low-zoom shaded relief,
 * not a street map. Carto's basemaps are free for OSM-derived use with the
 * attribution rendered below, and sit on a different host from every provider
 * the MapLibre path already tried.
 */

const TILE_SIZE = 256;
const MIN_ZOOM = 1;
/** Carto's light_all raster pyramid tops out here. Kept as the ceiling for
 *  every provider: tileserver-gl rasterises by overzooming its vector data so
 *  it answers past its own maxzoom anyway, and a provider that doesn't just
 *  404s — which the per-tile onError already hides, leaving the route and pins
 *  untouched. */
const MAX_ZOOM = 20;

/** Keyless Carto raster pyramid — the default when nothing is self-hosted.
 *  OpenFreeMap is not an option here: it serves vector only, and its one raster
 *  endpoint is low-zoom shaded relief rather than a street map. */
export const DEFAULT_RASTER_TILE_URL =
    "https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png";

/**
 * Raster tile URL template, overridable with NEXT_PUBLIC_RASTER_TILE_URL so a
 * self-hosted tile server (deploy/tiles) can feed this renderer too. Vector
 * self-hosting alone cannot: this component deliberately uses no WebGL and no
 * vector tiles, so it needs real rasterised PNGs.
 *
 * Read at call time rather than module scope so tests can drive it with
 * vi.stubEnv; Next.js still inlines the literal at build time.
 */
export function rasterTileUrlTemplate(): string {
    return process.env.NEXT_PUBLIC_RASTER_TILE_URL?.trim() || DEFAULT_RASTER_TILE_URL;
}

/** Substitute {z}/{x}/{y} in the configured template. Exported for the test:
 *  a template whose placeholders silently fail to substitute requests one wrong
 *  URL forever, which is indistinguishable from a dead tile host. */
export function rasterTileUrl(z: number, x: number, y: number, template?: string): string {
    return (template ?? rasterTileUrlTemplate())
        .replace(/\{z\}/g, String(z))
        .replace(/\{x\}/g, String(x))
        .replace(/\{y\}/g, String(y));
}

/**
 * Attribution line for whoever actually served the tiles.
 *
 * OpenStreetMap's is required for any OSM-derived basemap, self-hosted
 * included — that one never drops. Carto's is required only when Carto served
 * the tiles; leaving it hardcoded would credit them for bytes from our own tile
 * server, which is false rather than merely redundant.
 *
 * A third-party provider that is neither of these gets OSM attribution only,
 * which may under-credit them — add a case here if you point
 * NEXT_PUBLIC_RASTER_TILE_URL at one.
 */
export function rasterAttribution(template?: string): string {
    const t = template ?? rasterTileUrlTemplate();
    // Case-insensitive: hostnames are, and an operator who types the Carto host
    // with different casing would otherwise silently UNDER-credit them — the
    // exact failure this function exists to prevent, just in the other
    // direction. Anchored to a `.`, `//` or string start so a lookalike host
    // like evilcartocdn.com cannot claim Carto's attribution.
    return /(^|\/\/|\.)cartocdn\.com/i.test(t)
        ? "© OpenStreetMap contributors © CARTO"
        : "© OpenStreetMap contributors";
}

export interface StaticRoutePath {
    points: { lat: number; lng: number }[];
    /** Drawn at lower opacity — an inferred/approximate leg, not captured GPS. */
    approx?: boolean;
}

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    /** Already-resolved polylines to draw, in draw order. */
    paths?: StaticRoutePath[];
    /** Reserve this many px at the top so an overlaid status band cannot cover
     *  a pin — the same clearance the MapLibre path applies via fitBounds. */
    topPadding?: number;
}

interface Point {
    x: number;
    y: number;
}

/** Web Mercator lat/lng -> absolute pixel coordinates at zoom `z`.
 *  Exported for the projection test — get this wrong and every tile request
 *  is for the wrong square of the planet, which looks identical to "the tile
 *  host is down". */
export function project(lat: number, lng: number, z: number): Point {
    const scale = TILE_SIZE * 2 ** z;
    const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const sin = Math.sin((clamped * Math.PI) / 180);
    return {
        x: ((lng + 180) / 360) * scale,
        y: (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * scale,
    };
}

function isFinitePoint(p: { lat: number; lng: number }): boolean {
    return Number.isFinite(p.lat) && Number.isFinite(p.lng);
}

/** Largest zoom at which `pts` still fit inside the padded viewport. */
function fitZoom(
    pts: { lat: number; lng: number }[],
    width: number,
    height: number,
    pad: { top: number; other: number },
): number {
    const usableW = Math.max(1, width - pad.other * 2);
    const usableH = Math.max(1, height - pad.top - pad.other);
    for (let z = MAX_ZOOM; z > MIN_ZOOM; z--) {
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (const p of pts) {
            const { x, y } = project(p.lat, p.lng, z);
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        }
        if (maxX - minX <= usableW && maxY - minY <= usableH) return z;
    }
    return MIN_ZOOM;
}

export default function StaticRouteMap({
    pickupLat,
    pickupLng,
    dropoffLat,
    dropoffLng,
    paths,
    topPadding = 52,
}: Props) {
    const boxRef = useRef<HTMLDivElement>(null);
    const [size, setSize] = useState<{ w: number; h: number } | null>(null);

    useEffect(() => {
        const el = boxRef.current;
        if (!el) return;
        const apply = () => setSize({ w: el.clientWidth, h: el.clientHeight });
        apply();
        // The panel lives in a dialog that animates open, so the first measured
        // width can be mid-transition — observe rather than measure once.
        const ro = new ResizeObserver(apply);
        ro.observe(el);
        return () => ro.disconnect();
    }, []);

    const view = useMemo(() => {
        if (!size || size.w < 1 || size.h < 1) return null;

        const pins = [
            { lat: pickupLat, lng: pickupLng, kind: "pickup" as const },
            { lat: dropoffLat, lng: dropoffLng, kind: "dropoff" as const },
        ].filter(isFinitePoint);

        const drawn = (paths ?? [])
            .map((p) => ({ ...p, points: p.points.filter(isFinitePoint) }))
            .filter((p) => p.points.length > 1);

        const all = [...pins, ...drawn.flatMap((p) => p.points)];
        if (all.length === 0) return null;

        const pad = { top: topPadding, other: 24 };
        const zoom = fitZoom(all, size.w, size.h, pad);

        // Centre the content in the space left below the reserved top strip.
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        for (const p of all) {
            const { x, y } = project(p.lat, p.lng, zoom);
            if (x < minX) minX = x;
            if (x > maxX) maxX = x;
            if (y < minY) minY = y;
            if (y > maxY) maxY = y;
        }
        const originX = (minX + maxX) / 2 - size.w / 2;
        const originY = (minY + maxY) / 2 - (size.h + pad.top - pad.other) / 2;

        const toLocal = (lat: number, lng: number): Point => {
            const { x, y } = project(lat, lng, zoom);
            return { x: x - originX, y: y - originY };
        };

        const worldTiles = 2 ** zoom;
        // Resolved once per layout rather than per tile — a viewport can be a
        // couple of dozen tiles and the template cannot change mid-render.
        const tileTemplate = rasterTileUrlTemplate();
        const tiles: { key: string; url: string; left: number; top: number }[] = [];
        const x0 = Math.floor(originX / TILE_SIZE);
        const x1 = Math.floor((originX + size.w) / TILE_SIZE);
        const y0 = Math.floor(originY / TILE_SIZE);
        const y1 = Math.floor((originY + size.h) / TILE_SIZE);
        for (let ty = y0; ty <= y1; ty++) {
            // Past the poles there is no tile to ask for; x wraps instead.
            if (ty < 0 || ty >= worldTiles) continue;
            for (let tx = x0; tx <= x1; tx++) {
                const wrapped = ((tx % worldTiles) + worldTiles) % worldTiles;
                tiles.push({
                    key: `${zoom}/${tx}/${ty}`,
                    url: rasterTileUrl(zoom, wrapped, ty, tileTemplate),
                    left: tx * TILE_SIZE - originX,
                    top: ty * TILE_SIZE - originY,
                });
            }
        }

        // Same orange->red gradient spec the MapLibre path and the phone apps
        // use, so the identical ride reads the same on every surface.
        const strokes: { key: string; color: string; points: string; opacity: number }[] = [];
        drawn.forEach((path, pathIndex) => {
            const latLng: LatLng[] = path.points.map((p) => [p.lat, p.lng]);
            buildPathGradient(latLng).forEach((chunk, chunkIndex) => {
                strokes.push({
                    key: `${pathIndex}-${chunkIndex}`,
                    color: chunk.color,
                    opacity: path.approx ? 0.5 : 0.9,
                    points: chunk.coordinates
                        .map(([lat, lng]) => {
                            const { x, y } = toLocal(lat, lng);
                            return `${x.toFixed(1)},${y.toFixed(1)}`;
                        })
                        .join(" "),
                });
            });
        });

        return {
            tiles,
            strokes,
            attribution: rasterAttribution(tileTemplate),
            pins: pins.map((p) => ({ ...p, ...toLocal(p.lat, p.lng) })),
        };
    }, [size, pickupLat, pickupLng, dropoffLat, dropoffLng, paths, topPadding]);

    return (
        <div ref={boxRef} className="absolute inset-0 overflow-hidden bg-muted">
            {view?.tiles.map((t) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                    key={t.key}
                    src={t.url}
                    alt=""
                    aria-hidden="true"
                    draggable={false}
                    width={TILE_SIZE}
                    height={TILE_SIZE}
                    style={{
                        position: "absolute",
                        left: `${t.left}px`,
                        top: `${t.top}px`,
                        width: TILE_SIZE,
                        height: TILE_SIZE,
                        // A tile that fails to load leaves the container's own
                        // background rather than a broken-image glyph; the route
                        // and pins above it stay readable either way.
                        maxWidth: "none",
                    }}
                    onError={(e) => {
                        e.currentTarget.style.visibility = "hidden";
                    }}
                />
            ))}

            {view && size && (
                <svg
                    width={size.w}
                    height={size.h}
                    viewBox={`0 0 ${size.w} ${size.h}`}
                    className="pointer-events-none absolute inset-0"
                    aria-hidden="true"
                >
                    {view.strokes.map((s) => (
                        <polyline
                            key={s.key}
                            points={s.points}
                            fill="none"
                            stroke={s.color}
                            strokeOpacity={s.opacity}
                            strokeWidth={ROUTE_STROKE_WIDTH}
                            strokeLinecap="round"
                            strokeLinejoin="round"
                        />
                    ))}
                </svg>
            )}

            {view?.pins.map((p) => (
                <div
                    key={p.kind}
                    title={p.kind === "pickup" ? "Pickup" : "Dropoff"}
                    className="absolute"
                    style={{
                        left: `${p.x}px`,
                        top: `${p.y}px`,
                        width: 22,
                        height: 22,
                        transform: "translate(-50%, -50%)",
                        filter: "drop-shadow(0 1px 3px rgba(0,0,0,0.35))",
                    }}
                    dangerouslySetInnerHTML={{ __html: routePinSvg(p.kind, 22) }}
                />
            ))}

            {/* Required by OSM's terms — and by Carto's when Carto served the
                tiles — a licensing condition of the free tiles, not decoration.
                Tracks the configured provider rather than being hardcoded; see
                rasterAttribution(). Rendered even before the first layout so a
                map that never sizes still carries its attribution. */}
            <div className="absolute bottom-0 right-0 bg-background/80 px-1.5 py-0.5 text-[9px] text-muted-foreground">
                {view?.attribution ?? rasterAttribution()}
            </div>
        </div>
    );
}
