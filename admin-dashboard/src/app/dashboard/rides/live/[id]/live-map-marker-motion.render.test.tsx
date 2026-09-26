/**
 * LiveRideMap — the driver marker glides between location polls (UX program
 * W4.1) instead of teleporting, via src/lib/map/marker-interpolation.
 *
 * The util's maths is unit-tested in src/lib/__tests__/marker-interpolation
 * .test.ts, and the same wiring on the monitoring map in
 * monitoring-map-marker-motion.render.test.tsx. This file covers live-map.tsx:
 * the first placement is exact, a later move is spread over animation frames,
 * and the placeholder / snap / Reduce Motion / unmount paths set the position
 * directly with no frame loop left running.
 *
 * maplibre-gl is mocked (jsdom has no WebGL); the fake map records its event
 * handlers so the test can fire "load". requestAnimationFrame is replaced with
 * a manual queue so frames run only when the test says so, with a timestamp it
 * chooses.
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";

const markers: FakeMarkerShape[] = [];
const mapHandlers = new Map<string, Array<() => void>>();
const trailSetData = vi.fn();

interface FakeMarkerShape {
    setLngLat: ReturnType<typeof vi.fn>;
}

vi.mock("maplibre-gl", () => {
    class FakeMap {
        on = vi.fn((event: string, cb: () => void) => {
            const list = mapHandlers.get(event) ?? [];
            list.push(cb);
            mapHandlers.set(event, list);
        });
        off = vi.fn();
        addControl = vi.fn();
        addSource = vi.fn();
        addLayer = vi.fn();
        getSource = vi.fn(() => ({ setData: trailSetData }));
        fitBounds = vi.fn();
        remove = vi.fn();
    }
    class FakeMarker {
        setLngLat = vi.fn().mockReturnThis();
        setPopup = vi.fn().mockReturnThis();
        addTo = vi.fn().mockReturnThis();
        remove = vi.fn();
        constructor() {
            markers.push(this);
        }
    }
    class FakePopup {
        setText = vi.fn().mockReturnThis();
    }
    class FakeControl {}
    return {
        __esModule: true,
        default: { Map: FakeMap, Marker: FakeMarker, Popup: FakePopup, NavigationControl: FakeControl },
        Map: FakeMap,
        Marker: FakeMarker,
        Popup: FakePopup,
        NavigationControl: FakeControl,
        LngLatBounds: class {
            extend() {
                return this;
            }
        },
        setWorkerUrl: vi.fn(),
    };
});

vi.mock("@/lib/map/webgl-support", () => ({
    hasRenderingWebGL: vi.fn(() => true),
}));

import LiveRideMap from "./live-map";

const ROUTE = {
    pickupLat: 52.1332,
    pickupLng: -106.67,
    dropoffLat: 52.1205,
    dropoffLng: -106.6335,
};
const DRIVER = { driverLat: 52.13, driverLng: -106.67 };

// ── Manual requestAnimationFrame ────────────────────────────────────
let rafQueue = new Map<number, FrameRequestCallback>();
let rafId = 0;
function runFrame(timestamp: number) {
    const due = rafQueue;
    rafQueue = new Map();
    due.forEach((cb) => cb(timestamp));
}

function lastLngLat(m: FakeMarkerShape): [number, number] {
    return m.setLngLat.mock.calls[m.setLngLat.mock.calls.length - 1][0];
}

type Driver = { driverLat?: number; driverLng?: number; trail?: { lat: number; lng: number }[] };

/** Mount the map, fire "load", and return the driver marker (created last). */
function mountMap(driver: Driver = DRIVER) {
    const utils = render(<LiveRideMap {...ROUTE} {...driver} />);
    act(() => {
        (mapHandlers.get("load") ?? []).forEach((cb) => cb());
    });
    const driverMarker = markers[markers.length - 1];
    const move = (next: Driver) => utils.rerender(<LiveRideMap {...ROUTE} {...next} />);
    return { ...utils, driverMarker, move };
}

beforeEach(() => {
    markers.length = 0;
    mapHandlers.clear();
    trailSetData.mockClear();
    rafQueue = new Map();
    rafId = 0;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
        rafId += 1;
        rafQueue.set(rafId, cb);
        return rafId;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
        rafQueue.delete(id);
    });
});

afterEach(() => {
    vi.unstubAllGlobals();
    delete (window as { matchMedia?: unknown }).matchMedia;
});

describe("LiveRideMap driver marker motion", () => {
    it("places the driver marker exactly, with no animation frame requested", () => {
        const { driverMarker, unmount } = mountMap();
        expect(markers).toHaveLength(3); // pickup, dropoff, driver
        expect(lastLngLat(driverMarker)).toEqual([-106.67, 52.13]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("glides a short move over frames and lands exactly on the target", () => {
        const { driverMarker, move, unmount } = mountMap();
        const t0 = performance.now();
        move({ driverLat: 52.131, driverLng: -106.669 });

        // Not moved synchronously — the glide starts on the next frame.
        expect(lastLngLat(driverMarker)).toEqual([-106.67, 52.13]);
        expect(rafQueue.size).toBe(1);

        runFrame(t0 + 500);
        const [midLng, midLat] = lastLngLat(driverMarker);
        expect(midLat).toBeCloseTo(52.1305, 5);
        expect(midLng).toBeCloseTo(-106.6695, 5);
        expect(rafQueue.size).toBe(1);

        runFrame(t0 + 1500);
        expect(lastLngLat(driverMarker)).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0); // the loop stops once the marker arrives
        unmount();
    });

    it("retargets a glide in progress from where the marker is drawn now", () => {
        const { driverMarker, move, unmount } = mountMap();
        const t0 = performance.now();
        move({ driverLat: 52.131, driverLng: -106.67 });
        runFrame(t0 + 500);
        const [, drawnLat] = lastLngLat(driverMarker);

        move({ driverLat: 52.132, driverLng: -106.67 });
        const t1 = performance.now();
        runFrame(t1); // the new glide's first frame starts where the old one was
        expect(lastLngLat(driverMarker)[1]).toBeCloseTo(drawnLat, 5);
        runFrame(t1 + 2000);
        expect(lastLngLat(driverMarker)).toEqual([-106.67, 52.132]);
        unmount();
    });

    it("snaps a jump over 500 m straight to the target", () => {
        const { driverMarker, move, unmount } = mountMap();
        move({ driverLat: 52.14, driverLng: -106.67 }); // ~1.1 km
        expect(lastLngLat(driverMarker)).toEqual([-106.67, 52.14]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("sets the position directly under prefers-reduced-motion", () => {
        Object.defineProperty(window, "matchMedia", {
            value: vi.fn((query: string) => ({ matches: query.includes("reduce"), media: query })),
            configurable: true,
            writable: true,
        });
        const { driverMarker, move, unmount } = mountMap();
        move({ driverLat: 52.131, driverLng: -106.669 });
        expect(lastLngLat(driverMarker)).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("sets the first real position directly when the marker started at the placeholder", () => {
        // No driver position yet: the marker sits at the pickup/dropoff midpoint.
        const { driverMarker, move, unmount } = mountMap({});
        move({ driverLat: 52.131, driverLng: -106.669 });
        expect(lastLngLat(driverMarker)).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0);
        unmount();
    });

    it("still updates the trail as soon as a new position arrives", () => {
        const { move, unmount } = mountMap();
        trailSetData.mockClear();
        move({
            driverLat: 52.131,
            driverLng: -106.669,
            trail: [
                { lat: 52.13, lng: -106.67 },
                { lat: 52.131, lng: -106.669 },
            ],
        });
        // The trail line is redrawn right away; only the marker glides.
        expect(trailSetData).toHaveBeenCalledTimes(1);
        expect(rafQueue.size).toBe(1);
        unmount();
    });

    it("sets the position directly when the previous move is over 30 s old", () => {
        const now = vi.spyOn(performance, "now").mockReturnValue(1_000);
        const { driverMarker, move, unmount } = mountMap();
        now.mockReturnValue(1_000 + 31_000); // no position change for 31 s
        move({ driverLat: 52.131, driverLng: -106.669 });
        expect(lastLngLat(driverMarker)).toEqual([-106.669, 52.131]);
        expect(rafQueue.size).toBe(0);
        now.mockRestore();
        unmount();
    });

    it("cancels a running glide when the map re-initialises", () => {
        const { driverMarker, move, rerender, unmount } = mountMap();
        const t0 = performance.now();
        move({ driverLat: 52.131, driverLng: -106.669 });
        expect(rafQueue.size).toBe(1);
        const callsBefore = driverMarker.setLngLat.mock.calls.length;

        // A new pickup re-runs the map's init effect. Its cleanup must stop
        // the glide before the old marker and map are removed.
        rerender(<LiveRideMap {...ROUTE} pickupLat={52.2} driverLat={52.131} driverLng={-106.669} />);
        expect(rafQueue.size).toBe(0);
        runFrame(t0 + 500);
        expect(driverMarker.setLngLat.mock.calls.length).toBe(callsBefore);
        unmount();
    });

    it("cancels a running glide on unmount", () => {
        const { move, unmount } = mountMap();
        move({ driverLat: 52.131, driverLng: -106.669 });
        expect(rafQueue.size).toBe(1);
        unmount();
        expect(rafQueue.size).toBe(0);
    });
});
