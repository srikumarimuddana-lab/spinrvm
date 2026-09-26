import { afterEach, describe, expect, it, vi } from "vitest";
import {
    MARKER_ANIMATION_MS,
    MARKER_SNAP_DISTANCE_M,
    MARKER_STALE_GAP_MS,
    distanceMeters,
    interpolateMarker,
    lerpBearing,
    normalizeBearing,
    prefersReducedMotion,
    shouldSnap,
} from "@/lib/map/marker-interpolation";

// Saskatoon downtown; ~0.001° lat ≈ 111 m, ~0.001° lng ≈ 68 m at this latitude.
const A = { lat: 52.13, lng: -106.67 };
const NEAR = { lat: 52.131, lng: -106.669 }; // ~130 m from A
const FAR = { lat: 52.14, lng: -106.67 }; // ~1.1 km from A

describe("distanceMeters", () => {
    it("is zero for the same point", () => {
        expect(distanceMeters(A, A)).toBe(0);
    });

    it("measures ~111 m per 0.001° of latitude", () => {
        expect(distanceMeters(A, { lat: 52.131, lng: -106.67 })).toBeCloseTo(111.2, 0);
    });
});

describe("normalizeBearing / lerpBearing", () => {
    it("normalises negatives and full turns into [0, 360)", () => {
        expect(normalizeBearing(-10)).toBe(350);
        expect(normalizeBearing(360)).toBe(0);
        expect(normalizeBearing(725)).toBe(5);
    });

    it("turns 350° → 10° clockwise through north (20°), not 340° back", () => {
        expect(lerpBearing(350, 10, 0.5)).toBeCloseTo(0, 6);
        expect(lerpBearing(350, 10, 0.25)).toBeCloseTo(355, 6);
        expect(lerpBearing(350, 10, 1)).toBeCloseTo(10, 6);
    });

    it("turns 10° → 350° anticlockwise through north", () => {
        expect(lerpBearing(10, 350, 0.5)).toBeCloseTo(0, 6);
        expect(lerpBearing(10, 350, 0.25)).toBeCloseTo(5, 6);
    });

    it("lerps plainly when no wrap is involved", () => {
        expect(lerpBearing(90, 180, 0.5)).toBeCloseTo(135, 6);
        expect(lerpBearing(180, 90, 0.5)).toBeCloseTo(135, 6);
    });

    it("accepts un-normalised inputs", () => {
        expect(lerpBearing(-10, 370, 0.5)).toBeCloseTo(0, 6);
    });
});

describe("interpolateMarker — position", () => {
    it("starts at `from`, reaches the midpoint halfway, ends at `to`", () => {
        const start = interpolateMarker(A, NEAR, 0, 1000);
        expect(start.done).toBe(false);
        expect(start.pose.lat).toBeCloseTo(A.lat, 9);
        expect(start.pose.lng).toBeCloseTo(A.lng, 9);

        const mid = interpolateMarker(A, NEAR, 500, 1000);
        expect(mid.done).toBe(false);
        expect(mid.pose.lat).toBeCloseTo((A.lat + NEAR.lat) / 2, 9);
        expect(mid.pose.lng).toBeCloseTo((A.lng + NEAR.lng) / 2, 9);

        const end = interpolateMarker(A, NEAR, 1000, 1000);
        expect(end).toEqual({ pose: NEAR, done: true });
    });

    it("returns the target (same object) once elapsed passes the duration", () => {
        const r = interpolateMarker(A, NEAR, 5000, 1000);
        expect(r.done).toBe(true);
        expect(r.pose).toBe(NEAR);
    });

    it("clamps a negative elapsed (rAF timestamp before the start stamp) to the start", () => {
        const r = interpolateMarker(A, NEAR, -16, 1000);
        expect(r.done).toBe(false);
        expect(r.pose.lat).toBeCloseTo(A.lat, 9);
    });

    it("snaps with zero or negative duration", () => {
        expect(interpolateMarker(A, NEAR, 0, 0)).toEqual({ pose: NEAR, done: true });
        expect(interpolateMarker(A, NEAR, 0, -1)).toEqual({ pose: NEAR, done: true });
        expect(interpolateMarker(A, NEAR, 0, Number.NaN)).toEqual({ pose: NEAR, done: true });
    });

    it("is done immediately when nothing moved (no pointless animation loop)", () => {
        expect(interpolateMarker(A, { ...A }, 0, 1000).done).toBe(true);
        expect(
            interpolateMarker({ ...A, bearing: 90 }, { ...A, bearing: 450 }, 0, 1000).done,
        ).toBe(true);
    });
});

describe("interpolateMarker — bearing", () => {
    it("takes the shortest way round while moving", () => {
        const r = interpolateMarker({ ...A, bearing: 350 }, { ...NEAR, bearing: 10 }, 500, 1000);
        expect(r.pose.bearing).toBeCloseTo(0, 6);
    });

    it("animates a pure rotation (same position, new heading)", () => {
        const r = interpolateMarker({ ...A, bearing: 350 }, { ...A, bearing: 10 }, 250, 1000);
        expect(r.done).toBe(false);
        expect(r.pose.bearing).toBeCloseTo(355, 6);
    });

    it("uses the target's bearing when the start has none", () => {
        const r = interpolateMarker(A, { ...NEAR, bearing: 45 }, 500, 1000);
        expect(r.pose.bearing).toBe(45);
    });

    it("leaves bearing off entirely when neither pose has one", () => {
        const r = interpolateMarker(A, NEAR, 500, 1000);
        expect(r.pose).not.toHaveProperty("bearing");
    });
});

describe("snap rules", () => {
    it("snaps a jump longer than the threshold", () => {
        expect(distanceMeters(A, FAR)).toBeGreaterThan(MARKER_SNAP_DISTANCE_M);
        expect(shouldSnap(A, FAR)).toBe(true);
        expect(interpolateMarker(A, FAR, 0, 1000)).toEqual({ pose: FAR, done: true });
    });

    it("glides a jump just under the threshold, and respects a custom threshold", () => {
        expect(shouldSnap(A, NEAR)).toBe(false);
        expect(shouldSnap(A, NEAR, { snapDistanceM: 100 })).toBe(true);
    });

    it("snaps after a stale gap, glides after a normal one, ignores an unknown one", () => {
        expect(shouldSnap(A, NEAR, { gapMs: MARKER_STALE_GAP_MS + 1 })).toBe(true);
        expect(shouldSnap(A, NEAR, { gapMs: 4000 })).toBe(false);
        expect(shouldSnap(A, NEAR, {})).toBe(false);
        expect(shouldSnap(A, NEAR, { gapMs: 5000, staleGapMs: 2000 })).toBe(true);
    });

    it("snaps across the antimeridian rather than lerping round the globe", () => {
        expect(shouldSnap({ lat: 0, lng: 179.999 }, { lat: 0, lng: -179.999 })).toBe(true);
    });

    it("snaps when a coordinate is not a finite number", () => {
        expect(shouldSnap({ lat: Number.NaN, lng: 0 }, A)).toBe(true);
        expect(shouldSnap(A, { lat: 52, lng: Number.POSITIVE_INFINITY })).toBe(true);
    });
});

describe("reduce motion", () => {
    it("returns the target immediately, whatever the elapsed time", () => {
        expect(interpolateMarker(A, NEAR, 0, 1000, { reduceMotion: true })).toEqual({
            pose: NEAR,
            done: true,
        });
        expect(
            interpolateMarker({ ...A, bearing: 350 }, { ...A, bearing: 10 }, 0, 1000, { reduceMotion: true }),
        ).toEqual({ pose: { ...A, bearing: 10 }, done: true });
    });
});

describe("prefersReducedMotion", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        // jsdom has no matchMedia by default; remove any stub a test added.
        delete (window as { matchMedia?: unknown }).matchMedia;
    });

    it("is false when matchMedia is unavailable (jsdom, old browsers)", () => {
        delete (window as { matchMedia?: unknown }).matchMedia;
        expect(prefersReducedMotion()).toBe(false);
    });

    it("reflects the reduce media query live, on every call", () => {
        let reduce = false;
        const matchMedia = vi.fn((query: string) => ({ matches: reduce, media: query }));
        Object.defineProperty(window, "matchMedia", { value: matchMedia, configurable: true, writable: true });

        expect(prefersReducedMotion()).toBe(false);
        reduce = true;
        expect(prefersReducedMotion()).toBe(true);
        expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
    });
});

it("exports a sane default glide length", () => {
    expect(MARKER_ANIMATION_MS).toBeGreaterThan(0);
    expect(MARKER_ANIMATION_MS).toBeLessThan(4000); // shorter than one driver ping interval
});
