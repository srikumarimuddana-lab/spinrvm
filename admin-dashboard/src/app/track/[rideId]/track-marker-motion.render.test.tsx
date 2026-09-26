/**
 * /track/[rideId] — the driver's car glides between 5 s location polls (UX
 * program W4.2) through src/lib/map/marker-interpolation (W4.1) instead of
 * jumping to each new fix.
 *
 * The util's maths is unit-tested on its own; this covers the page WIRING:
 * first placement is exact, a later move (position AND heading) is spread
 * over animation frames, and the big-jump / Reduce Motion / driver-released
 * / unmount paths leave no frame loop running.
 */
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import {
    activeRide,
    car,
    carRotationDeg,
    flush,
    harness,
    pendingFrames,
    poll,
    runFrame,
    setReducedMotion,
    setupTrackHarness,
    teardownTrackHarness,
} from "./track-test-harness";

vi.hoisted(() => {
    process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY = "test-key";
    process.env.NEXT_PUBLIC_API_URL = "https://api.test";
});

vi.mock("next/navigation", () => ({ useParams: () => ({ rideId: "share-token" }) }));

// next/script → fire onLoad once mounted, as if the Maps loader finished.
vi.mock("next/script", async () => {
    const R = await import("react");
    return {
        default: function FakeScript({ onLoad }: { onLoad?: () => void }) {
            R.useEffect(() => onLoad?.(), []);
            return null;
        },
    };
});

import TrackRide from "./page";

const START = { lat: 52.13, lng: -106.67 };
const NORTH_100M = { lat: 52.1309, lng: -106.67 };
const NORTH_THEN_EAST_100M = { lat: 52.1309, lng: -106.6685 };

async function mountAt(pos: { lat: number; lng: number }, status = "driver_accepted") {
    harness.ride = activeRide(status, pos);
    const utils = render(<TrackRide />);
    await flush();
    return utils;
}

beforeEach(() => {
    setupTrackHarness();
});

afterEach(() => {
    teardownTrackHarness();
});

describe("/track car marker motion", () => {
    it("places the car exactly on first load, with no animation frame", async () => {
        const { unmount } = await mountAt(START);
        expect(car()?.position).toEqual(START);
        expect(pendingFrames()).toBe(0);
        unmount();
    });

    it("glides a short move over animation frames instead of jumping", async () => {
        const { unmount } = await mountAt(START);
        const m = car()!;

        harness.ride = activeRide("driver_accepted", NORTH_100M);
        await poll();
        const t0 = performance.now();

        // Not moved on the poll itself — the glide starts on the next frame.
        expect(m.position).toEqual(START);
        expect(pendingFrames()).toBe(1);

        runFrame(t0 + 500);
        expect(m.position.lat).toBeGreaterThan(START.lat);
        expect(m.position.lat).toBeLessThan(NORTH_100M.lat);
        expect(m.position.lat).toBeCloseTo((START.lat + NORTH_100M.lat) / 2, 4);
        expect(pendingFrames()).toBe(1);

        runFrame(t0 + 1500);
        expect(m.position).toEqual(NORTH_100M);
        expect(pendingFrames()).toBe(0); // loop stops once the car is there
        unmount();
    });

    it("turns the car through the glide instead of snapping its heading", async () => {
        const { unmount } = await mountAt(START);
        const m = car()!;

        // First move sets a course (due north) from the travel direction.
        harness.ride = activeRide("in_progress", NORTH_100M);
        await poll();
        runFrame(performance.now() + 2000);
        expect(carRotationDeg(m)).toBeCloseTo(0, 0);

        // Then a turn east: heading goes 0° → ~90° over the same frames.
        harness.ride = activeRide("in_progress", NORTH_THEN_EAST_100M);
        await poll();
        const t0 = performance.now();
        expect(carRotationDeg(m)).toBeCloseTo(0, 0);

        runFrame(t0 + 500);
        const mid = carRotationDeg(m)!;
        expect(mid).toBeGreaterThan(30);
        expect(mid).toBeLessThan(60);

        runFrame(t0 + 1500);
        expect(Math.abs(carRotationDeg(m)! - 90)).toBeLessThan(1);
        unmount();
    });

    it("re-points the car against the map's heading when the map is rotated", async () => {
        const { unmount } = await mountAt(START);
        const m = car()!;
        harness.ride = activeRide("in_progress", NORTH_100M);
        await poll();
        runFrame(performance.now() + 2000);
        expect(carRotationDeg(m)).toBeCloseTo(0, 0);

        harness.mapHeading = 30;
        harness.mapListeners.get("heading_changed")!();
        expect(carRotationDeg(m)).toBeCloseTo(-30, 0);
        unmount();
    });

    it("snaps a jump over 500 m straight to the new fix", async () => {
        const { unmount } = await mountAt(START);
        const far = { lat: 52.14, lng: -106.67 }; // ~1.1 km
        harness.ride = activeRide("driver_accepted", far);
        await poll();
        expect(car()?.position).toEqual(far);
        expect(pendingFrames()).toBe(0);
        unmount();
    });

    it("moves the car directly under prefers-reduced-motion", async () => {
        setReducedMotion(true);
        const { unmount } = await mountAt(START);
        harness.ride = activeRide("driver_accepted", NORTH_100M);
        await poll();
        expect(car()?.position).toEqual(NORTH_100M);
        expect(pendingFrames()).toBe(0);
        unmount();
    });

    it("drops the glide when the driver is released mid-move", async () => {
        const { unmount } = await mountAt(START);
        const m = car()!;
        harness.ride = activeRide("driver_accepted", NORTH_100M);
        await poll();
        expect(pendingFrames()).toBe(1);

        // Offer timeout: the payload comes back with driver: null.
        harness.ride = activeRide("searching", null);
        await poll();
        expect(m.map).toBeNull();
        expect(car()).toBeUndefined();
        expect(pendingFrames()).toBe(0);
        unmount();
    });

    it("cancels a running glide on unmount", async () => {
        const { unmount } = await mountAt(START);
        harness.ride = activeRide("driver_accepted", NORTH_100M);
        await poll();
        expect(pendingFrames()).toBe(1);
        unmount();
        expect(pendingFrames()).toBe(0);
    });
});
