/**
 * Smooth marker movement for live maps (UX program W4.1).
 *
 * Pure helpers: given where a marker is drawn now (`from`) and where the
 * latest location update says it should be (`to`), return where to draw it
 * `elapsedMs` into a `durationMs` glide. Position is a straight lerp of
 * lat/lng; bearing takes the shortest way round the compass (350° → 10°
 * turns 20° clockwise, never 340° back). The glide is skipped — the target
 * comes back immediately with `done: true` — when:
 *
 *   - Reduce Motion is on (the viewer asked for no movement animation),
 *   - the jump is larger than `snapDistanceM` (a GPS glitch, a driver
 *     reappearing elsewhere) — sliding across town would draw a path the
 *     vehicle never drove,
 *   - the previous update is older than `staleGapMs` (the feed went quiet,
 *     so the old position isn't a meaningful start point),
 *   - either pose has a non-finite coordinate, or the move crosses the
 *     antimeridian (a naive lng lerp would go the long way round the globe),
 *   - `durationMs` is zero or less, or nothing moved.
 *
 * No DOM, map-library or timer code lives here; the caller owns the
 * requestAnimationFrame loop and calls `interpolateMarker` once per frame.
 */

export interface MarkerPose {
    lat: number;
    lng: number;
    /** Compass heading in degrees (0 = north, clockwise). Optional. */
    bearing?: number | null;
}

export interface MarkerInterpolationOptions {
    /** Return the target immediately (prefers-reduced-motion). */
    reduceMotion?: boolean;
    /** Jumps longer than this (metres) snap instead of gliding. */
    snapDistanceM?: number;
    /** Time since this marker's previous update. Unknown → no stale check. */
    gapMs?: number;
    /** A `gapMs` longer than this snaps instead of gliding. */
    staleGapMs?: number;
}

export interface InterpolatedMarker {
    pose: MarkerPose;
    /** True once the marker has reached `to` (or snapped straight there). */
    done: boolean;
}

/** Glide length per location update. Driver apps ping roughly every 4 s,
 *  so a 1 s glide keeps the marker close to live while removing the
 *  teleport between pings. */
export const MARKER_ANIMATION_MS = 1000;
/** 500 m inside one ~4 s ping interval is ~450 km/h: not real driving. */
export const MARKER_SNAP_DISTANCE_M = 500;
/** Several missed pings in a row: treat the next position as a fresh fix. */
export const MARKER_STALE_GAP_MS = 30_000;

const EARTH_RADIUS_M = 6_371_000;

/** Great-circle distance between two poses, in metres. */
export function distanceMeters(a: MarkerPose, b: MarkerPose): number {
    const toRad = (d: number) => (d * Math.PI) / 180;
    const dLat = toRad(b.lat - a.lat);
    const dLng = toRad(b.lng - a.lng);
    const h =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Normalise any angle to [0, 360). */
export function normalizeBearing(deg: number): number {
    return ((deg % 360) + 360) % 360;
}

/** Bearing `t` of the way from `from` to `to`, turning the shorter way. */
export function lerpBearing(from: number, to: number, t: number): number {
    const delta = ((normalizeBearing(to) - normalizeBearing(from) + 540) % 360) - 180;
    return normalizeBearing(from + delta * t);
}

function isFiniteNumber(n: number | null | undefined): n is number {
    return typeof n === "number" && Number.isFinite(n);
}

/** True when the move from `from` to `to` should skip the glide entirely. */
export function shouldSnap(
    from: MarkerPose,
    to: MarkerPose,
    opts: MarkerInterpolationOptions = {},
): boolean {
    const {
        reduceMotion = false,
        snapDistanceM = MARKER_SNAP_DISTANCE_M,
        gapMs,
        staleGapMs = MARKER_STALE_GAP_MS,
    } = opts;
    if (reduceMotion) return true;
    if (![from.lat, from.lng, to.lat, to.lng].every(isFiniteNumber)) return true;
    if (Math.abs(to.lng - from.lng) > 180) return true;
    if (gapMs !== undefined && gapMs > staleGapMs) return true;
    return distanceMeters(from, to) > snapDistanceM;
}

/**
 * Where to draw a marker `elapsedMs` into a `durationMs` glide from `from`
 * to `to`. See the file comment for when it snaps instead.
 */
export function interpolateMarker(
    from: MarkerPose,
    to: MarkerPose,
    elapsedMs: number,
    durationMs: number,
    opts: MarkerInterpolationOptions = {},
): InterpolatedMarker {
    const bearingsBoth = isFiniteNumber(from.bearing) && isFiniteNumber(to.bearing);
    const unmoved =
        from.lat === to.lat &&
        from.lng === to.lng &&
        (!bearingsBoth || normalizeBearing(from.bearing!) === normalizeBearing(to.bearing!));

    if (
        !(durationMs > 0) ||
        unmoved ||
        shouldSnap(from, to, opts) ||
        elapsedMs >= durationMs
    ) {
        return { pose: to, done: true };
    }

    const t = Math.max(0, elapsedMs) / durationMs;
    const pose: MarkerPose = {
        lat: from.lat + (to.lat - from.lat) * t,
        lng: from.lng + (to.lng - from.lng) * t,
    };
    if (bearingsBoth) {
        pose.bearing = lerpBearing(from.bearing!, to.bearing!, t);
    } else if (to.bearing !== undefined) {
        pose.bearing = to.bearing;
    }
    return { pose, done: false };
}

/**
 * Live read of the viewer's Reduce Motion setting. Read per update rather
 * than once per mount so toggling the OS setting mid-session takes effect
 * on the next location update without a reload. False outside a browser.
 */
export function prefersReducedMotion(): boolean {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
